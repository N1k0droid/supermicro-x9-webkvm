import os
import socket
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from aiohttp import WSMsgType
    from aiohttp.test_utils import TestClient, TestServer
except ImportError:                                    # aiohttp not installed
    TestClient = None

from webkvm.bmc import ipmi                            # noqa: E402
from webkvm.config import Config                       # noqa: E402
from webkvm.kvm.rfb_server import RfbServer            # noqa: E402
from webkvm.kvm.session import KvmSession              # noqa: E402
from webkvm.stats.collector import Collector           # noqa: E402
from webkvm.stats.store import Store                   # noqa: E402
from webkvm.web.auth import hash_password              # noqa: E402

import fixtures                                        # noqa: E402
from fake_bmc import FakeBmc, W, H                     # noqa: E402

TOKEN = 'metrics-token-123'


class FakeIpmi(ipmi.IpmiClient):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.commands = []

    async def run(self, *cmd, timeout=None):
        self.commands.append(cmd)
        out = {('sensor',): fixtures.SENSOR, ('chassis', 'status'): fixtures.CHASSIS,
               ('sel', 'elist'): fixtures.SEL, ('mc', 'info'): fixtures.MC_INFO,
               ('fru', 'print', '0'): fixtures.FRU}.get(cmd)
        if out is None and cmd[:2] == ('chassis', 'power'):
            out = f'Chassis Power Control: {cmd[2]}'
        return out


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipIf(TestClient is None, 'aiohttp not installed')
class TestWeb(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from webkvm.web.app import build_app, make_context
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(bmc_host='127.0.0.1', bmc_password='x', web_user='admin',
                          web_password_hash=hash_password('segreta'), metrics_token=TOKEN,
                          data_dir=self.tmp.name, kvm_internal_port=free_port(), kvm_idle_timeout=1)
        self.bmc = FakeBmc()
        self.kvm = KvmSession(lambda: ('127.0.0.1', self.bmc.port, 'U' * 16, 'P' * 16, None),
                              idle_timeout=1)
        self.rfb = RfbServer(self.kvm, '127.0.0.1', self.cfg.kvm_internal_port).start()
        self.store = Store(os.path.join(self.tmp.name, 'db.sqlite'))
        self.ipmi = FakeIpmi(self.cfg)
        self.collector = Collector(self.cfg, self.ipmi, self.store)
        await self.collector._load_info()
        await self.collector._poll()
        await self.collector._poll_sel()
        ctx = make_context(self.cfg, self.collector, self.store, self.ipmi, self.kvm, 'test', None)
        self.client = TestClient(TestServer(build_app(ctx)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.rfb.stop()
        self.kvm.disconnect()
        self.bmc.close()
        self.store.close()
        self.tmp.cleanup()

    async def login(self, password='segreta'):
        return await self.client.post('/login', data={'user': 'admin', 'password': password},
                                      allow_redirects=False)

    async def test_auth_flow(self):
        r = await self.client.get('/', allow_redirects=False)
        self.assertEqual((r.status, r.headers['Location']), (302, '/login'))
        r = await self.client.get('/api/v1/sensors')
        self.assertEqual(r.status, 401)
        r = await self.login('sbagliata')
        self.assertEqual(r.headers['Location'], '/login?err=1')
        r = await self.login()
        self.assertEqual(r.headers['Location'], '/')
        self.assertIn('HttpOnly', r.headers['Set-Cookie'])
        r = await self.client.get('/api/v1/session')
        self.assertEqual(r.status, 200)
        self.assertTrue((await r.json())['csrf'])
        r = await self.client.get('/')
        self.assertEqual(r.status, 200)
        self.assertIn('Content-Security-Policy', r.headers)

    async def test_rate_limit(self):
        for _ in range(5):
            await self.login('no')
        r = await self.login()                              # even the right one is blocked
        self.assertEqual(r.headers['Location'], '/login?err=blocked')

    async def test_token_access(self):
        hdr = {'Authorization': f'Bearer {TOKEN}'}
        r = await self.client.get('/api/v1/sensors', headers=hdr)
        data = await r.json()
        self.assertTrue(data['up'])
        self.assertTrue(data['power_on'])
        self.assertEqual(data['sel_count'], 3)
        names = [s['name'] for s in data['sensors']]
        self.assertIn('CPU Temp', names)
        r = await self.client.get('/metrics', headers=hdr)
        self.assertEqual(r.status, 200)
        self.assertIn('bmc_sensor_value{sensor="CPU Temp"', await r.text())
        r = await self.client.get('/metrics', headers={'Authorization': 'Bearer no'})
        self.assertEqual(r.status, 401)
        r = await self.client.get('/api/v1/history?sensor=CPU%20Temp&range=3600', headers=hdr)
        self.assertEqual(len((await r.json())['points']), 1)
        r = await self.client.get('/api/v1/power', headers=hdr)  # the token does NOT give access to commands
        self.assertEqual(r.status, 401)

    async def test_power_requires_csrf(self):
        await self.login()
        csrf = (await (await self.client.get('/api/v1/session')).json())['csrf']
        r = await self.client.post('/api/v1/power', json={'action': 'soft'})
        self.assertEqual(r.status, 403)
        r = await self.client.post('/api/v1/power', json={'action': 'rm -rf'},
                                   headers={'X-CSRF-Token': csrf})
        self.assertEqual(r.status, 400)
        r = await self.client.post('/api/v1/power', json={'action': 'soft'},
                                   headers={'X-CSRF-Token': csrf})
        self.assertEqual(r.status, 200)
        self.assertIn(('chassis', 'power', 'soft'), self.ipmi.commands)

    async def test_websocket_bridge(self):
        r = await self.client.get('/ws/kvm')
        self.assertEqual(r.status, 401)
        await self.login()
        r = await self.client.get('/ws/kvm', headers={'Origin': 'http://evil.example',
                                                      'Connection': 'Upgrade', 'Upgrade': 'websocket',
                                                      'Sec-WebSocket-Version': '13',
                                                      'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ=='})
        self.assertEqual(r.status, 403)
        ws = await self.client.ws_connect('/ws/kvm', protocols=('binary',))
        buf = bytearray()

        async def need(n):
            while len(buf) < n:
                msg = await ws.receive(timeout=20)
                self.assertEqual(msg.type, WSMsgType.BINARY)
                buf.extend(msg.data)
            out = bytes(buf[:n])
            del buf[:n]
            return out
        self.assertEqual(await need(12), b'RFB 003.008\n')
        await ws.send_bytes(b'RFB 003.008\n')
        self.assertEqual(await need(2), bytes([1, 1]))
        await ws.send_bytes(b'\x01')
        self.assertEqual(await need(4), b'\x00\x00\x00\x00')
        await ws.send_bytes(b'\x01')
        w, h = struct.unpack('>HH', await need(4))
        self.assertEqual((w, h), (W, H))
        await need(16)
        nl = struct.unpack('>I', await need(4))[0]
        await need(nl)
        await ws.send_bytes(struct.pack('>BBHHHH', 3, 0, 0, 0, w, h))
        head = await need(4)
        self.assertEqual(head[0], 0)
        x, y, rw, rh, enc = struct.unpack('>HHHHi', await need(12))
        self.assertEqual((rw, rh, enc), (W, H, 0))
        self.assertEqual(len(await need(rw * rh * 4)), W * H * 4)
        await ws.close()


if __name__ == '__main__':
    unittest.main()
