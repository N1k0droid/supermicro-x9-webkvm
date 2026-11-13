"""RFB (VNC) server for the viewers: noVNC (through the WebSocket bridge) and native viewers.

Per viewer: one thread reads input (keyboard, mouse, update requests) and one thread writes
updates, so input is never blocked while waiting for a screen change.
"""
import logging
import socket
import struct
import threading

from .aten import RFB_VERSION, recv_exact
from . import vncauth

log = logging.getLogger('webkvm.rfb')

ENC_RAW = 0
ENC_DESKTOPSIZE = -223
ENC_QEMU_EXT_KEY = -258       # the viewer sends physical scancodes (see aten.XT_HID)

# internal format: 32 bpp, depth 24, little-endian, true colour, shift R=16 G=8 B=0 (BGRX)
SERVER_PF = struct.pack('>BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)


class UnsupportedPixelFormat(Exception):
    pass


class PixelConverter:
    """Converts BGRX to 32 bpp true-colour formats whose shifts are multiples of 8."""

    def __init__(self):
        self.order = None            # None = identity (BGRX)

    def set_format(self, raw16):
        bpp, _depth, big, true, rmax, gmax, bmax, rs, gs, bs = \
            struct.unpack('>BBBBHHHBBB3x', raw16)
        if bpp != 32 or not true or (rmax, gmax, bmax) != (255, 255, 255) or \
                any(s % 8 or s > 24 for s in (rs, gs, bs)):
            raise UnsupportedPixelFormat(
                f'unsupported pixel format: bpp={bpp} true={true} max=({rmax},{gmax},{bmax}) '
                f'shift=({rs},{gs},{bs}); set the viewer to 32 bit / full colour')

        def pos(shift):
            return 3 - shift // 8 if big else shift // 8
        r, g, b = pos(rs), pos(gs), pos(bs)
        self.order = None if (r, g, b) == (2, 1, 0) else (r, g, b)

    def convert(self, data):
        if self.order is None:
            return data
        r, g, b = self.order
        out = bytearray(len(data))
        out[r::4] = data[2::4]
        out[g::4] = data[1::4]
        out[b::4] = data[0::4]
        return bytes(out)


class RfbServer:
    """TCP listener. password=None -> no authentication (loopback only)."""

    def __init__(self, session, host, port, password=None, name='Supermicro BMC (webkvm)'):
        self.session = session
        self.host, self.port = host, port
        self.password = password
        self.name = name.encode('utf-8')
        self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(8)
        auth = 'VNC password' if self.password else 'no password'
        log.info('VNC server listening on %s:%d (%s)', self.host, self.port, auth)
        threading.Thread(target=self._accept_loop, daemon=True,
                         name=f'rfb-accept-{self.port}').start()
        return self

    def stop(self):
        if self.sock:
            self.sock.close()

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self.sock.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True,
                             name=f'rfb-viewer-{addr[0]}:{addr[1]}').start()

    # ---------------------------------------------------------- handshake
    def _fail(self, conn, minor, reason):
        """SecurityResult = failed with a reason (RFB 3.8 shows it to the user)."""
        msg = reason.encode('utf-8', 'replace')
        if minor >= 8:
            conn.sendall(struct.pack('>II', 1, len(msg)) + msg)
        else:
            conn.sendall(struct.pack('>I', 1))

    def _handshake(self, conn, addr):
        conn.settimeout(60)
        conn.sendall(RFB_VERSION)
        ver = recv_exact(conn, 12)
        try:
            minor = int(ver[8:11])
        except ValueError:
            raise ConnectionError(f'invalid RFB version: {ver!r}')
        if minor < 7:
            reason = b'An RFB 3.7 or newer viewer is required'
            conn.sendall(struct.pack('>II', 0, len(reason)) + reason)
            raise ConnectionError('RFB 3.3 viewer not supported')
        sec = 2 if self.password else 1
        conn.sendall(bytes([1, sec]))
        if recv_exact(conn, 1)[0] != sec:
            raise ConnectionError('security type refused by the viewer')
        if sec == 2:
            chal = vncauth.new_challenge()
            conn.sendall(chal)
            resp = recv_exact(conn, 16)
            if not vncauth.check_response(self.password, chal, resp):
                log.warning('wrong VNC password from %s', addr[0])
                self._fail(conn, minor, 'Wrong password')
                raise ConnectionError('wrong VNC password')
        try:
            lease = self.session.acquire(timeout=45)
        except Exception as e:
            self._fail(conn, minor, f'KVM console unavailable: {e}')
            raise ConnectionError(f'KVM session unavailable: {e}')
        if minor >= 8 or sec == 2:
            conn.sendall(struct.pack('>I', 0))        # SecurityResult: OK
        recv_exact(conn, 1)                           # ClientInit (shared, ignored)
        fb = self.session.fb
        w, h = fb.width, fb.height
        conn.sendall(struct.pack('>HH', w, h) + SERVER_PF +
                     struct.pack('>I', len(self.name)) + self.name)
        conn.settimeout(None)
        return lease, (w, h)

    # -------------------------------------------------------------- viewer
    def _handle(self, conn, addr):
        lease = None
        q = None
        try:
            lease, size = self._handshake(conn, addr)
            fb = self.session.fb
            q = fb.register()
            log.info('viewer connected from %s, screen %dx%d', addr[0], *size)
            state = {'pf': PixelConverter(), 'desktop_size': False, 'size': size}
            writer = threading.Thread(target=self._writer, args=(conn, q, state), daemon=True)
            writer.start()
            self._reader(conn, q, state)
        except (ConnectionError, OSError, UnsupportedPixelFormat, RuntimeError) as e:
            log.info('viewer %s disconnected: %s', addr[0], e)
        finally:
            if q is not None:
                self.session.fb.unregister(q)
            try:
                conn.close()
            except OSError:
                pass
            if lease is not None:
                lease.release()

    def _reader(self, conn, q, state):
        fb = self.session.fb
        while True:
            t = recv_exact(conn, 1)[0]
            if t == 0:                                # SetPixelFormat
                raw = recv_exact(conn, 3 + 16)[3:]
                conv = PixelConverter()
                conv.set_format(raw)                  # raises UnsupportedPixelFormat
                state['pf'] = conv
            elif t == 2:                              # SetEncodings
                _pad, count = struct.unpack('>BH', recv_exact(conn, 3))
                encs = struct.unpack(f'>{count}i', recv_exact(conn, count * 4))
                state['desktop_size'] = ENC_DESKTOPSIZE in encs
                if ENC_QEMU_EXT_KEY in encs and not state.get('qemu_key_acked'):
                    state['qemu_key_ack'] = True      # acknowledged in the next FramebufferUpdate
            elif t == 3:                              # FramebufferUpdateRequest
                incremental = recv_exact(conn, 1)[0]
                recv_exact(conn, 8)                   # requested area: we send all changes anyway
                fb.post_request(q, bool(incremental))
            elif t == 4:                              # KeyEvent
                down, _pad, keysym = struct.unpack('>B2sI', recv_exact(conn, 7))
                self.session.send_key(keysym, down)
            elif t == 5:                              # PointerEvent
                buttons, x, y = struct.unpack('>BHH', recv_exact(conn, 5))
                x = min(x, max(fb.width - 1, 0))
                y = min(y, max(fb.height - 1, 0))
                self.session.send_mouse(buttons, x, y)
            elif t == 6:                              # ClientCutText: discarded
                recv_exact(conn, 3)
                ln = struct.unpack('>I', recv_exact(conn, 4))[0]
                recv_exact(conn, ln)
            elif t == 255:                            # QEMU messages
                sub = recv_exact(conn, 1)[0]
                if sub != 0:
                    raise RuntimeError(f'unhandled QEMU message: {sub}')
                down, keysym, keycode = struct.unpack('>HII', recv_exact(conn, 10))
                self.session.send_key(keysym, down, keycode)
            else:
                raise RuntimeError(f'unhandled RFB client message: {t}')

    def _writer(self, conn, q, state):
        fb = self.session.fb
        try:
            while not q.closed:
                upd = fb.take_update(q, state['size'], timeout=5)
                if upd is None:
                    continue
                kind, w, h, rects = upd
                conv = state['pf']
                parts = []
                resized = (w, h) != state['size']
                ack = state.pop('qemu_key_ack', False)
                n = len(rects) + (1 if resized else 0) + (1 if ack else 0)
                parts.append(struct.pack('>BxH', 0, n))
                if ack:
                    parts.append(struct.pack('>HHHHi', 0, 0, 0, 0, ENC_QEMU_EXT_KEY))
                    state['qemu_key_acked'] = True
                if resized:
                    parts.append(struct.pack('>HHHHi', 0, 0, w, h, ENC_DESKTOPSIZE))
                    if not state['desktop_size']:
                        log.warning('the viewer does not support DesktopSize: resolution changed to %dx%d', w, h)
                for x, y, rw, rh, data in rects:
                    parts.append(struct.pack('>HHHHi', x, y, rw, rh, ENC_RAW))
                    parts.append(conv.convert(data))
                conn.sendall(b''.join(parts))
                state['size'] = (w, h)
        except OSError as e:
            log.debug('writer ended: %s', e)
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
