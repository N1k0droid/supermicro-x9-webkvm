import os
import socket
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webkvm.kvm import aten, vncauth                          # noqa: E402
from webkvm.kvm.framebuffer import SharedFramebuffer          # noqa: E402
from webkvm.kvm.rfb_server import RfbServer, PixelConverter   # noqa: E402
from webkvm.kvm.session import KvmSession                     # noqa: E402

from fake_bmc import FakeBmc, W, H                            # noqa: E402


def slow_bgrx(body, depth):
    """Original per-pixel implementation, kept as a reference."""
    out = bytearray()
    if depth == 0:
        for i in range(0, len(body) - 1, 2):
            v = body[i] | (body[i + 1] << 8)
            r5, g5, b5 = (v >> 10) & 31, (v >> 5) & 31, v & 31
            out += bytes(((b5 << 3) | (b5 >> 2), (g5 << 3) | (g5 >> 2), (r5 << 3) | (r5 >> 2), 0))
    else:
        for byte in body:
            r2, g2, b2 = (byte >> 4) & 3, (byte >> 2) & 3, byte & 3
            out += bytes((b2 * 85, g2 * 85, r2 * 85, 0))
    return bytes(out)


class TestPixels(unittest.TestCase):
    def test_rgb555_matches_original(self):
        body = os.urandom(4096)
        self.assertEqual(aten.pixels_to_bgrx(body, 0), slow_bgrx(body, 0))

    def test_rgb222_matches_original(self):
        body = os.urandom(1024)
        self.assertEqual(aten.pixels_to_bgrx(body, 1), slow_bgrx(body, 1))

    def test_odd_length(self):
        self.assertEqual(aten.pixels_to_bgrx(b'\x00\x7c\x01', 0), bytes((0, 0, 255, 0)))

    def test_full_frame_speed(self):
        body = os.urandom(1280 * 1024 * 2)
        t0 = time.perf_counter()
        out = aten.pixels_to_bgrx(body, 0)
        dt = time.perf_counter() - t0
        self.assertEqual(len(out), 1280 * 1024 * 4)
        self.assertLess(dt, 1.0, f'conversione frame intero troppo lenta: {dt:.2f}s')

    def test_decode_real_resolution(self):
        body = b'\x00\x00' * (1280 * 1024)
        payload = bytes([1, 0]) + b'\0' * 8 + body
        w, h, rects = aten.decode_hermon(payload, 1920, 1080)   # unreliable header
        self.assertEqual((w, h), (1280, 1024))
        self.assertEqual(len(rects), 1)


class TestXtMap(unittest.TestCase):
    def test_letters_and_extended(self):
        self.assertEqual(aten.XT_HID[0x10], 0x14)   # Q
        self.assertEqual(aten.XT_HID[0x1E], 0x04)   # A
        self.assertEqual(aten.XT_HID[0x32], 0x10)   # M
        self.assertEqual(aten.XT_HID[0xD3], 0x4C)   # Delete (E0 53)
        self.assertEqual(aten.XT_HID[0xC8], 0x52)   # up arrow
        self.assertEqual(aten.XT_HID[0x56], 0x64)   # ISO <> key (e.g. Italian keyboards)


class TestVncAuth(unittest.TestCase):
    def test_des_vector(self):
        got = vncauth.des_encrypt_block(bytes.fromhex('133457799BBCDFF1'), bytes.fromhex('0123456789ABCDEF'))
        self.assertEqual(got.hex(), '85e813540f0ab405')

    def test_roundtrip(self):
        ch = vncauth.new_challenge()
        self.assertTrue(vncauth.check_response('secret', ch, vncauth.vnc_response('secret', ch)))
        self.assertFalse(vncauth.check_response('secret', ch, vncauth.vnc_response('other', ch)))


class TestFramebuffer(unittest.TestCase):
    def test_two_viewers_both_get_updates(self):
        fb = SharedFramebuffer()
        fb.resize(32, 32)
        fb.ready.set()
        q1, q2 = fb.register(), fb.register()
        fb.post_request(q1, True)
        fb.post_request(q2, True)
        fb.apply_rect(16, 16, 16, 16, b'\x01' * 1024)
        for q in (q1, q2):
            kind, w, h, rects = fb.take_update(q, (32, 32), timeout=1)
            self.assertEqual(kind, 'inc')
            self.assertIn((16, 16, 16, 16), [r[:4] for r in rects])

    def test_clip_tiles_at_border(self):
        fb = SharedFramebuffer()
        fb.resize(20, 20)
        fb.ready.set()
        q = fb.register()
        fb.post_request(q, True)
        fb.apply_rect(16, 16, 16, 16, b'\x02' * 1024)
        _k, _w, _h, rects = fb.take_update(q, (20, 20), timeout=1)
        self.assertEqual(rects[-1][:4], (16, 16, 4, 4))
        self.assertEqual(len(rects[-1][4]), 4 * 4 * 4)

    def test_collapse(self):
        fb = SharedFramebuffer()
        fb.resize(16, 16)
        fb.ready.set()
        q = fb.register()
        q.dirty = []
        for _ in range(SharedFramebuffer.MAX_DIRTY + 5):
            fb.apply_rect(0, 0, 16, 16, b'\x00' * 1024)
        self.assertLessEqual(len(q.dirty), SharedFramebuffer.MAX_DIRTY)

    def test_pixel_converter_rgbx(self):
        pc = PixelConverter()
        pc.set_format(struct.pack('>BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 0, 8, 16))
        self.assertEqual(pc.convert(bytes((1, 2, 3, 0))), bytes((3, 2, 1, 0)))


# ------------------------------------------------------------- end-to-end
def recv_exact(s, n):
    buf = b''
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError(f'chiusa a {len(buf)}/{n}')
        buf += c
    return buf


class VncClient:
    def __init__(self, port, password=None):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=20)
        s = self.s
        assert recv_exact(s, 12) == b'RFB 003.008\n'
        s.sendall(b'RFB 003.008\n')
        n = recv_exact(s, 1)[0]
        types = recv_exact(s, n)
        sec = types[0]
        s.sendall(bytes([sec]))
        if sec == 2:
            ch = recv_exact(s, 16)
            s.sendall(vncauth.vnc_response(password or '', ch))
        res = struct.unpack('>I', recv_exact(s, 4))[0]
        if res != 0:
            ln = struct.unpack('>I', recv_exact(s, 4))[0]
            raise PermissionError(recv_exact(s, ln).decode())
        s.sendall(b'\x01')
        self.w, self.h = struct.unpack('>HH', recv_exact(s, 4))
        recv_exact(s, 16)
        nl = struct.unpack('>I', recv_exact(s, 4))[0]
        recv_exact(s, nl)

    def set_rgbx(self):
        self.s.sendall(b'\x00\x00\x00\x00' +
                       struct.pack('>BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 0, 8, 16))

    def set_encodings(self, *encs):
        self.s.sendall(struct.pack('>BxH', 2, len(encs)) + struct.pack(f'>{len(encs)}i', *encs))

    def request(self, incremental):
        self.s.sendall(struct.pack('>BBHHHH', 3, incremental, 0, 0, self.w, self.h))

    def read_update(self):
        t = recv_exact(self.s, 1)[0]
        assert t == 0, t
        recv_exact(self.s, 1)
        n = struct.unpack('>H', recv_exact(self.s, 2))[0]
        rects = []
        for _ in range(n):
            x, y, w, h, enc = struct.unpack('>HHHHi', recv_exact(self.s, 12))
            data = recv_exact(self.s, w * h * 4) if enc == 0 else b''
            rects.append((x, y, w, h, enc, data))
        return rects

    def close(self):
        self.s.close()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.bmc = FakeBmc()
        self.cleanups = 0

        def connect():
            def cleanup():
                self.cleanups += 1
            return '127.0.0.1', self.bmc.port, 'U' * 16, 'P' * 16, cleanup
        self.session = KvmSession(connect, idle_timeout=1)
        self.port = free_port()
        self.server = RfbServer(self.session, '127.0.0.1', self.port, password='pw').start()

    def tearDown(self):
        self.server.stop()
        self.session.disconnect()
        self.bmc.close()

    def test_wrong_password(self):
        with self.assertRaises(PermissionError):
            VncClient(self.port, 'sbagliata')
        self.assertEqual(self.bmc.logins, 0)    # no BMC login without valid auth

    def test_video_input_and_idle(self):
        c = VncClient(self.port, 'pw')
        self.assertEqual((c.w, c.h), (W, H))     # resolution from the payload, not from the 1920x1080 header
        c.set_rgbx()
        c.set_encodings(0, -223, -258)
        c.request(0)
        rects = c.read_update()
        encs = [r[4] for r in rects]
        self.assertIn(-258, encs)                # QEMU Extended Key Event acknowledged
        raw = [r for r in rects if r[4] == 0][0]
        self.assertEqual(raw[:4], (0, 0, W, H))
        self.assertEqual(raw[5][:4], bytes((255, 0, 0, 0)))   # red in RGBX

        # incremental update: one 16x16 tile at row 2, column 3
        c.request(1)
        self.bmc.send_tile.set()
        rects = c.read_update()
        tile = [r for r in rects if r[4] == 0][0]
        self.assertEqual(tile[:4], (48, 32, 16, 16))
        self.assertEqual(tile[5][:4], bytes((0, 255, 0, 0)))  # green

        # keyboard via XT scancode (Q = 0x10 -> HID 0x14), then keysym (Enter)
        c.s.sendall(struct.pack('>BBHII', 255, 0, 1, ord('q'), 0x10))
        c.s.sendall(struct.pack('>BBxxI', 4, 1, 0xFF0D))      # classic RFB KeyEvent
        c.s.sendall(struct.pack('>BBHH', 5, 1, 10, 500))      # y beyond the edge -> clamped
        deadline = time.time() + 5
        while time.time() < deadline and (len(self.bmc.keys) < 2 or not self.bmc.mouse):
            time.sleep(0.05)
        self.assertEqual(self.bmc.keys[:2], [(1, 0x14), (1, 0x28)])
        self.assertEqual(self.bmc.mouse[0], (1, 10, H - 1))

        # second viewer at the same time: same BMC session
        c2 = VncClient(self.port, 'pw')
        c2.request(0)
        self.assertEqual(c2.read_update()[0][:4], (0, 0, W, H))
        self.assertEqual(self.bmc.logins, 1)
        c.close()
        c2.close()

        # idle close (idle_timeout=1s) and web login cleanup
        deadline = time.time() + 10
        while time.time() < deadline and self.session.state != 'idle':
            time.sleep(0.1)
        self.assertEqual(self.session.state, 'idle')
        self.assertEqual(self.cleanups, 1)

    def test_reconnect_after_drop(self):
        c = VncClient(self.port, 'pw')
        c.request(0)
        c.read_update()
        self.bmc.drop_connections()
        deadline = time.time() + 15
        while time.time() < deadline and self.bmc.logins < 2:
            time.sleep(0.1)
        self.assertGreaterEqual(self.bmc.logins, 2)   # automatic reconnection with a viewer connected
        c.close()

    def test_bmc_unreachable_reports_reason(self):
        self.bmc.close()
        with self.assertRaises(PermissionError) as cm:
            VncClient(self.port, 'pw')
        self.assertIn('KVM console unavailable', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
