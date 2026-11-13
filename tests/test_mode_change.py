"""bmc_reader: on a video mode change (or after a silence) it asks for a full frame."""
import struct
import threading
import unittest
from unittest import mock

from webkvm.kvm.aten import bmc_reader
from webkvm.kvm.framebuffer import SharedFramebuffer


def full_frame(w, h):
    return b'\x01\x00' + b'\x00' * 8 + b'\x00\x00' * (w * h)


def tiles(n):
    return b'\x00\x00' + struct.pack('>I', n) + b'\x00' * 4 + \
        b''.join(b'\x00' * 4 + bytes((0, 0)) + b'\x00\x00' * 256 for _ in range(n))


class FakeLink:
    def __init__(self, script, stop):
        self.script = list(script)
        self.requests = []
        self.stop = stop

    def request_update(self, incremental):
        self.requests.append(incremental)

    def read_message(self):
        if not self.script:
            self.stop.set()
            return None
        item = self.script.pop(0)
        if item == 'timeout':
            return item
        w, h, payload = item
        nosignal = w >= 0x8000 or h >= 0x8000
        return {'w': w, 'h': h, 'mode': 0, 'payload': payload, 'nosignal': nosignal}


class ModeChangeTest(unittest.TestCase):
    def run_script(self, script):
        stop = threading.Event()
        link = FakeLink(script, stop)
        fb = SharedFramebuffer()
        bmc_reader(link, fb, stop)
        return link.requests, fb

    def test_resolution_change_requests_full_frame(self):
        reqs, fb = self.run_script([
            (64, 48, full_frame(64, 48)),
            (64, 48, tiles(1)),
            (48, 32, tiles(1)),            # mode change: different header
            (48, 32, full_frame(48, 32)),
            (48, 32, tiles(0)),
        ])
        # requests: initial full, inc, inc, FULL after the change, inc
        self.assertEqual(reqs[:5], [False, True, True, False, True])
        self.assertEqual((fb.width, fb.height), (48, 32))

    def test_timeout_requests_full_frame(self):
        reqs, _ = self.run_script([
            (64, 48, full_frame(64, 48)),
            'timeout',
            (64, 48, full_frame(64, 48)),
        ])
        self.assertEqual(reqs[:3], [False, True, False])

    def test_no_signal_blanks_screen_and_requests_full_frame(self):
        stop = threading.Event()
        white = b'\x01\x00' + b'\x00' * 8 + b'\xff\x7f' * (32 * 16)
        link = FakeLink([
            (32, 16, white),               # previous screen
            (0xFD80, 0xFE20, b''),         # reboot: no signal (-640x-480)
            (32, 16, tiles(1)),            # signal back: tiles only
            (32, 16, full_frame(32, 16)),
        ], stop)
        fb = SharedFramebuffer()
        seen = []
        orig = fb.set_signal

        def spy(present):
            seen.append((present, bytes(fb.buf).count(0xff)))
            orig(present)
        fb.set_signal = spy
        with mock.patch('webkvm.kvm.aten.time.sleep'):
            bmc_reader(link, fb, stop)
        self.assertFalse(seen[0][0])        # signal lost...
        self.assertTrue(seen[1][0])         # ...and back
        self.assertEqual(seen[1][1], 0)     # meanwhile the screen was black
        # after the signal loss the requests are full ones
        self.assertEqual(link.requests[:3], [False, True, False])
        self.assertTrue(fb.signal)


if __name__ == '__main__':
    unittest.main()
