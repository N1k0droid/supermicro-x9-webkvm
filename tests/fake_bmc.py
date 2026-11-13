"""Simulated BMC: speaks just enough of the ATEN KVM protocol for the end-to-end tests."""
import socket
import struct
import threading

W, H = 64, 48
RED555 = 0x7C00
GREEN555 = 0x03E0


def full_frame_payload(color=RED555):
    body = struct.pack('<H', color) * (W * H)
    head = bytes([1, 0]) + b'\x00\x00\x00\x00' + struct.pack('>I', 10 + len(body))
    return head + body


def tile_payload(row, col, color=GREEN555):
    tile = struct.pack('<H', color) * 256
    rec = b'\x00\x00\x00\x00' + bytes([row, col]) + tile
    return bytes([0, 0]) + struct.pack('>I', 1) + struct.pack('>I', 10 + len(rec)) + rec


def screen_msg(payload, first):
    # header: declares 1920x1080 to check that the resolution is derived from the payload
    return (b'\x00' + b'\x00' * 3 + struct.pack('>HHHH', 0, 0, W, H) +
            struct.pack('>III', 0x59 if first else 0, 0, len(payload)) + payload)


def _recv(s, n):
    buf = b''
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError
        buf += c
    return buf


class FakeBmc:
    def __init__(self, user='U' * 16, pwd='P' * 16):
        self.user, self.pwd = user, pwd
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.keys = []          # (down, hid)
        self.mouse = []         # (buttons, x, y)
        self.logins = 0
        self.event = threading.Event()
        self.send_tile = threading.Event()
        self.conns = []
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.sock.close()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass

    def drop_connections(self):
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            c.sendall(b'RFB 003.008\n')
            _recv(c, 12)
            c.sendall(bytes([1, 16]))
            _recv(c, 1)
            c.sendall(b'\x00' * 24)
            creds = _recv(c, 48)
            if creds[:24].rstrip(b'\x00').decode() != self.user or \
                    creds[24:].rstrip(b'\x00').decode() != self.pwd:
                msg = b'bad credentials'
                c.sendall(struct.pack('>II', 1, len(msg)) + msg)
                return
            c.sendall(struct.pack('>I', 0))
            self.logins += 1
            _recv(c, 1)
            name = b'fake'
            c.sendall(struct.pack('>HH', 1920, 1080) + b'\x00' * 16 + struct.pack('>I', len(name)) +
                      name + b'\x00' * 12)
            first = True
            c.settimeout(0.2)
            while True:
                if self.send_tile.is_set():
                    self.send_tile.clear()
                    c.sendall(screen_msg(tile_payload(2, 3), False))
                try:
                    t = c.recv(1)
                except socket.timeout:
                    continue
                if not t:
                    return
                c.settimeout(None)
                t = t[0]
                if t == 3:
                    _recv(c, 9)
                    if first:
                        c.sendall(b'\x16\x07')                 # keepalive to answer
                        c.sendall(screen_msg(full_frame_payload(), True))
                        first = False
                elif t == 0x16:
                    _recv(c, 1)
                elif t == 4:
                    m = _recv(c, 17)
                    self.keys.append((m[1], struct.unpack('>I', m[4:8])[0]))
                    self.event.set()
                elif t == 5:
                    m = _recv(c, 17)
                    self.mouse.append((m[1], *struct.unpack('>HH', m[2:6])))
                    self.event.set()
                c.settimeout(0.2)
        except (ConnectionError, OSError):
            pass
