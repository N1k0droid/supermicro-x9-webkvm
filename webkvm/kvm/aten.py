"""BMC side of the KVM: ATEN protocol (an RFB variant) on port 5900 and the Hermon video decoder.

See docs/PROTOCOL.md for the protocol details. Pixel conversion uses precomputed tables
(much faster than per-pixel Python code), and reads time out cleanly when the screen is idle.
"""
import logging
import re
import socket
import struct
import sys
import threading
import time

log = logging.getLogger('webkvm.aten')

RFB_VERSION = b'RFB 003.008\n'
TILE = 16
HERMON_MAX_W = 1280   # the Hermon decoder caps the width at 0x500


# ----------------------------------------------------- X11 keysym -> USB HID
# The BMC does not use X11 keysyms on the wire but USB HID "keyboard usage" codes.
# Modifiers arrive as separate keys, so shifted symbols map to the base key
# (e.g. '!' -> same HID as '1').
def _build_keysym_hid():
    m = {}
    for c in range(ord('a'), ord('z') + 1):      # letters
        hid = 0x04 + (c - ord('a'))
        m[c] = hid                                # lower case
        m[c - 32] = hid                           # UPPER CASE
    base_digit = {'1': 0x1e, '2': 0x1f, '3': 0x20, '4': 0x21, '5': 0x22,
                  '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27}
    for ch, hid in base_digit.items():
        m[ord(ch)] = hid
    for ch, hid in {'!': 0x1e, '@': 0x1f, '#': 0x20, '$': 0x21, '%': 0x22,
                    '^': 0x23, '&': 0x24, '*': 0x25, '(': 0x26, ')': 0x27}.items():
        m[ord(ch)] = hid                          # number row symbols
    for ch, hid in {'-': 0x2d, '_': 0x2d, '=': 0x2e, '+': 0x2e, '[': 0x2f,
                    '{': 0x2f, ']': 0x30, '}': 0x30, '\\': 0x31, '|': 0x31,
                    ';': 0x33, ':': 0x33, "'": 0x34, '"': 0x34, '`': 0x35,
                    '~': 0x35, ',': 0x36, '<': 0x36, '.': 0x37, '>': 0x37,
                    '/': 0x38, '?': 0x38}.items():
        m[ord(ch)] = hid                          # punctuation
    m[0x20] = 0x2c                                # Space
    m.update({                                    # special keys (keysym 0xFFxx)
        0xFF0D: 0x28, 0xFF1B: 0x29, 0xFF08: 0x2a, 0xFF09: 0x2b, 0xFFE5: 0x39,
        0xFF14: 0x47, 0xFF13: 0x48, 0xFF61: 0x46,
        0xFF63: 0x49, 0xFF50: 0x4a, 0xFF55: 0x4b, 0xFFFF: 0x4c, 0xFF57: 0x4d,
        0xFF56: 0x4e, 0xFF53: 0x4f, 0xFF51: 0x50, 0xFF54: 0x51, 0xFF52: 0x52,
        0xFF7F: 0x53,
        0xFF8D: 0x58, 0xFFAF: 0x54, 0xFFAA: 0x55, 0xFFAD: 0x56, 0xFFAB: 0x57,
        0xFFAE: 0x63,
        0xFFE1: 0xE1, 0xFFE2: 0xE5, 0xFFE3: 0xE0, 0xFFE4: 0xE4,   # modifiers
        0xFFE9: 0xE2, 0xFFEA: 0xE6, 0xFFE7: 0xE3, 0xFFEB: 0xE3, 0xFFEC: 0xE7,
        0xFE03: 0xE6,                             # AltGr (ISO_Level3_Shift) -> right Alt
    })
    for i in range(12):                           # F1..F12
        m[0xFFBE + i] = 0x3a + i
    kp = [0x62, 0x59, 0x5a, 0x5b, 0x5c, 0x5d, 0x5e, 0x5f, 0x60, 0x61]  # KP0..KP9
    for i in range(10):
        m[0xFFB0 + i] = kp[i]
    return m


KEYSYM_HID = _build_keysym_hid()


# ------------------------------------------- XT scancode (set 1) -> USB HID
# With the "QEMU Extended Key Event" extension (noVNC, TigerVNC) the viewer sends the PHYSICAL
# key as an XT scancode: we translate it to HID without going through the character, so any
# keyboard layout works, as long as the server's OS uses the same layout as the client.
# Extended keys (0xE0 prefix) arrive as 0x80 | code.
def _build_xt_hid():
    m = {0x01: 0x29, 0x0E: 0x2A, 0x0F: 0x2B, 0x1C: 0x28, 0x1D: 0xE0, 0x2A: 0xE1, 0x36: 0xE5,
         0x38: 0xE2, 0x39: 0x2C, 0x3A: 0x39, 0x45: 0x53, 0x46: 0x47, 0x56: 0x64,
         0x57: 0x44, 0x58: 0x45,
         0x0C: 0x2D, 0x0D: 0x2E, 0x1A: 0x2F, 0x1B: 0x30, 0x27: 0x33, 0x28: 0x34, 0x29: 0x35,
         0x2B: 0x31, 0x33: 0x36, 0x34: 0x37, 0x35: 0x38,
         # numeric keypad
         0x37: 0x55, 0x47: 0x5F, 0x48: 0x60, 0x49: 0x61, 0x4A: 0x56, 0x4B: 0x5C, 0x4C: 0x5D,
         0x4D: 0x5E, 0x4E: 0x57, 0x4F: 0x59, 0x50: 0x5A, 0x51: 0x5B, 0x52: 0x62, 0x53: 0x63,
         # extended (0xE0 xx -> 0x80 | xx)
         0x9C: 0x58, 0x9D: 0xE4, 0xB5: 0x54, 0xB7: 0x46, 0xB8: 0xE6, 0xC6: 0x48,
         0xC7: 0x4A, 0xC8: 0x52, 0xC9: 0x4B, 0xCB: 0x50, 0xCD: 0x4F, 0xCF: 0x4D, 0xD0: 0x51,
         0xD1: 0x4E, 0xD2: 0x49, 0xD3: 0x4C, 0xDB: 0xE3, 0xDC: 0xE7, 0xDD: 0x65}
    for i, hid in enumerate([0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27]):
        m[0x02 + i] = hid                                  # 1..0
    for xt, ch in zip(range(0x10, 0x1A), 'qwertyuiop'):
        m[xt] = 0x04 + ord(ch) - ord('a')
    for xt, ch in zip(range(0x1E, 0x27), 'asdfghjkl'):
        m[xt] = 0x04 + ord(ch) - ord('a')
    for xt, ch in zip(range(0x2C, 0x33), 'zxcvbnm'):
        m[xt] = 0x04 + ord(ch) - ord('a')
    for i in range(10):                                    # F1..F10
        m[0x3B + i] = 0x3A + i
    return m


XT_HID = _build_xt_hid()


# -------------------------------------------------------------------- helpers
def read_jnlp_text(text):
    """<argument> values of a JNLP descriptor: host, user, password, name, KVM port, ..."""
    return [a.strip() for a in re.findall(r'<argument>([^<]*)</argument>', text)]


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f'connection closed ({len(buf)}/{n} bytes)')
        buf += chunk
    return bytes(buf)


def field24(text):
    raw = text.encode('ascii')[:24]
    return raw + b'\x00' * (24 - len(raw))


# ----------------------------------------------------------- pixel conversion
def _expand5(v):
    return (v << 3) | (v >> 2)


# u16 RGB555 -> 4 bytes BGRX; u8 RGB222 -> 4 bytes BGRX
_T555 = [bytes((_expand5(v & 31), _expand5((v >> 5) & 31), _expand5((v >> 10) & 31), 0))
         for v in range(65536)]
_T222 = [bytes(((v & 3) * 85, ((v >> 2) & 3) * 85, ((v >> 4) & 3) * 85, 0)) for v in range(256)]
_LITTLE = sys.byteorder == 'little'


def pixels_to_bgrx(body, depth):
    """RGB555 little-endian (depth=0) or RGB222 (depth!=0) -> BGRX 32 bpp
    (shift R=16 G=8 B=0, little-endian)."""
    if depth == 0:
        n = len(body) // 2 * 2
        mv = memoryview(body)[:n]
        if _LITTLE:
            vals = mv.cast('H')
        else:                                      # big-endian host: rare, but correct
            import array
            arr = array.array('H', bytes(mv))
            arr.byteswap()
            vals = arr
        return b''.join(map(_T555.__getitem__, vals))
    return b''.join(map(_T222.__getitem__, body))


def decode_hermon(payload, screen_w, screen_h):
    """Decode a Hermon payload -> (width, height, rects).

    width/height are the real resolution for a full frame (None for tile updates);
    rects is a list of (x, y, w, h, bgrx_bytes). The size in the header is NOT
    reliable: it is derived from the pixel count, with the width capped at 1280 as
    the firmware does."""
    if len(payload) < 10:
        return None, None, []
    frame_mode, depth = payload[0], payload[1]
    bpp_bytes = 2 if depth == 0 else 1
    if frame_mode == 1:
        body = payload[10:]
        total_px = len(body) // bpp_bytes
        w = min(screen_w, HERMON_MAX_W) or HERMON_MAX_W
        h = total_px // w if w else 0
        body = body[:w * h * bpp_bytes]
        return w, h, [(0, 0, w, h, pixels_to_bgrx(body, depth))]
    count = struct.unpack('>I', payload[2:6])[0]
    off = 10
    tile_bytes = TILE * TILE * bpp_bytes
    rects = []
    for _ in range(count):
        if off + 6 + tile_bytes > len(payload):
            break
        row, col = payload[off + 4], payload[off + 5]
        tile = payload[off + 6: off + 6 + tile_bytes]
        off += 6 + tile_bytes
        rects.append((col * TILE, row * TILE, TILE, TILE, pixels_to_bgrx(tile, depth)))
    return None, None, rects


# ------------------------------------------------------------------ BMC side
class BmcLink:
    """Connection to the BMC: login, then reading and translating screen messages."""

    READ_TIMEOUT = 30   # idle screen: the BMC can stay silent for a long time

    def __init__(self, host, port, user, pwd, connect_timeout=10):
        self.sock = socket.create_connection((host, port), timeout=connect_timeout)
        self.width = 0
        self.height = 0
        self.send_lock = threading.Lock()   # the socket is shared between threads
        self._login(user, pwd)
        self.sock.settimeout(self.READ_TIMEOUT)

    def _login(self, user, pwd):
        s = self.sock
        recv_exact(s, 12)                      # banner "RFB 003.008\n"
        s.sendall(RFB_VERSION)
        n = recv_exact(s, 1)[0]
        recv_exact(s, n)                       # offered security types
        s.sendall(bytes([16]))                 # we pick type 16
        recv_exact(s, 24)                      # 24 bytes from the server, ignored
        s.sendall(field24(user) + field24(pwd))
        code = struct.unpack('>I', recv_exact(s, 4))[0]
        if code != 0:
            ln = struct.unpack('>I', recv_exact(s, 4))[0]
            msg = recv_exact(s, min(ln, 512))
            raise RuntimeError(f'KVM login refused by the BMC (code {code}): {msg!r}')
        s.sendall(b'\x01')                     # ClientInit: shared = 1
        recv_exact(s, 4)                       # declared w,h (unreliable)
        recv_exact(s, 16)                      # BMC pixel format
        nlen = struct.unpack('>I', recv_exact(s, 4))[0]
        recv_exact(s, nlen)                    # desktop name
        recv_exact(s, 4)
        recv_exact(s, 4)                       # session id
        recv_exact(s, 4)                       # privilege flags

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def request_update(self, incremental):
        w = self.width or 1
        h = self.height or 1
        with self.send_lock:
            self.sock.sendall(struct.pack('>BBHHHH', 3, 1 if incremental else 0, 0, 0, w, h))

    def send_key(self, keysym, down, xt_scancode=None):
        """Key from a viewer: XT scancode when available (layout independent), else keysym."""
        hid = XT_HID.get(xt_scancode) if xt_scancode else None
        if hid is None:
            hid = KEYSYM_HID.get(keysym)
        if hid is None:
            log.debug('unmapped key (keysym %#x, scancode %s), ignored', keysym, xt_scancode)
            return
        self.send_hid(hid, down)

    def send_hid(self, hid, down):
        """tag 0x04, 0x00, state, 2 reserved, HID code u32 BE, 9 reserved."""
        msg = struct.pack('>BBBHI', 0x04, 0x00, 1 if down else 0, 0, hid) + b'\x00' * 9
        with self.send_lock:
            self.sock.sendall(msg)

    def send_mouse(self, buttons, x, y):
        """tag 0x05 (plain branch), 0x00, buttons, x u16, y u16, 11 reserved."""
        msg = struct.pack('>BBBHH', 0x05, 0x00, buttons & 0xff, x, y) + b'\x00' * 11
        with self.send_lock:
            self.sock.sendall(msg)

    def read_message(self):
        """Read one message from the BMC. Returns a dict for screen updates, None for other
        types, 'timeout' if the BMC sent nothing for READ_TIMEOUT seconds (the stream stays
        consistent: the timeout is only handled on the first byte of a message)."""
        try:
            first = self.sock.recv(1)
        except socket.timeout:
            return 'timeout'
        if not first:
            raise ConnectionError('connection closed by the BMC')
        t = first[0]
        s = self.sock
        if t == 0x16:                          # keepalive: answer right away
            v = recv_exact(s, 1)[0]
            with self.send_lock:
                s.sendall(bytes([0x16, v]))
            return None
        if t == 0x39:                          # session info (text)
            recv_exact(s, 8)
            recv_exact(s, 256)
            return None
        if t == 0x04:                          # cursor
            _x, _y, w, h = struct.unpack('>IIII', recv_exact(s, 16))
            has_bmp = struct.unpack('>I', recv_exact(s, 4))[0]
            if has_bmp == 1:
                recv_exact(s, 4)                # cursor bpp
                recv_exact(s, w * h * 2)
            return None
        if t == 0x00:                          # screen update
            recv_exact(s, 3)
            _x, _y, w, h = struct.unpack('>HHHH', recv_exact(s, 8))
            decoder = struct.unpack('>I', recv_exact(s, 4))[0]
            mode = struct.unpack('>I', recv_exact(s, 4))[0]
            ln = struct.unpack('>I', recv_exact(s, 4))[0]
            payload = recv_exact(s, ln) if ln else b''
            # "decoder" is only meaningful on the first screen message (then it is 0)
            if self.width == 0 and decoder != 0x59:
                raise RuntimeError(f'decoder 0x{decoder:x} not supported (Hermon 0x59 only)')
            # without a video signal (reboot, mode change, screen off) the BMC sends
            # negative sizes (e.g. -640x-480 = 0xFD80 x 0xFE20) and no pixels
            nosignal = w >= 0x8000 or h >= 0x8000
            if not nosignal:
                self.width, self.height = w, h
            return {'w': w, 'h': h, 'mode': mode, 'payload': payload, 'nosignal': nosignal}
        raise RuntimeError(f'unhandled BMC message type: {t:#04x}')


def bmc_reader(link, fb, stop_event):
    """Read loop from the BMC into the framebuffer. Ends with an exception when the
    connection drops (KvmSession handles it)."""
    outstanding = False
    got_first = False
    want_full = False
    last_hdr = None
    while not stop_event.is_set():
        if fb.bmc_refresh.is_set():
            fb.bmc_refresh.clear()
            want_full = True
        if not outstanding:
            link.request_update(incremental=got_first and not want_full)
            outstanding = True
            want_full = False
        msg = link.read_message()
        if msg == 'timeout':
            # no answer: repeat the request, as a full one to be safe
            outstanding = False
            want_full = True
            continue
        if msg is None:
            continue
        outstanding = False
        if msg.get('nosignal'):
            # black screen like the Java client; when the signal comes back the BMC only sends
            # the tiles that changed against ITS last image, so a full frame is needed
            if fb.signal:
                log.info('no video signal from the server')
                fb.set_signal(False)
            if not fb.ready.is_set():
                # connected without a signal: show a black screen instead of waiting
                fb.resize(640, 480)
                fb.ready.set()
            want_full = True
            last_hdr = None
            time.sleep(0.2)                    # the BMC answers in bursts: no need to hurry
            continue
        if not fb.signal:
            log.info('video signal back (%dx%d)', msg['w'], msg['h'])
            fb.set_signal(True)
        hdr = (msg['w'], msg['h'])
        if last_hdr is not None and hdr != last_hdr:
            # video mode change (e.g. graphical POST -> text screen): the following tiles
            # refer to the new grid, so a full frame is needed
            log.info('video mode change: %dx%d -> %dx%d', *last_hdr, *hdr)
            want_full = True
        last_hdr = hdr
        dw, dh, rects = decode_hermon(msg['payload'], msg['w'], msg['h'])
        if dw and (dw, dh) != (fb.width, fb.height):
            fb.resize(dw, dh)
        if not got_first:
            if dw is None:                     # before a full frame the size is unknown
                continue
            got_first = True
            fb.ready.set()
        for x, y, w, h, bgrx in rects:
            fb.apply_rect(x, y, w, h, bgrx)
