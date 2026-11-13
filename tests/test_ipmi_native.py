import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from pyghmi.constants import Health
except ImportError:
    Health = None

from webkvm.bmc.ipmi import IpmiError       # noqa: E402
from webkvm.config import Config            # noqa: E402


class Reading:
    def __init__(self, value=None, health=0, states=(), unavailable=0):
        self.value, self.health, self.states, self.unavailable = value, health, list(states), unavailable


class Entry:
    def __init__(self, num, name, unit, reading, thresholds=None, signed=False):
        self.sensor_number, self.sensor_lun, self.name = num, 0, name
        self.unit_suffix = unit
        self.reading = reading
        self.has_thresholds = thresholds is not None
        self.reading_type = 1 if unit else 0x6f        # threshold based if it has a unit, state based otherwise
        self.thr = thresholds
        self.numeric_format = 2 if signed else 0

    def decode_sensor_reading(self, cmd, data):
        return self.reading

    def decode_value(self, cmd, raw):
        return raw * 1.0


class Sdr:
    def __init__(self, entries):
        self.sensors = {e.sensor_number: e for e in entries}

    def get_sensor_numbers(self):
        return iter(self.sensors)


class FakeCmd:
    def __init__(self, entries, sel=(), fail_power=False):
        self.sdr = Sdr(entries)
        self.sel = list(sel)
        self.calls = []
        self.fail_power = fail_power

    def init_sdr(self):
        return self.sdr

    def raw_command(self, netfn, command, data=(), rslun=0):
        self.calls.append((netfn, command, tuple(data)))
        if (netfn, command) == (4, 0x2d):
            return {'data': [0, 0x40, 0]}
        if (netfn, command) == (4, 0x27):
            return {'data': self.sdr.sensors[data[0]].thr}
        if (netfn, command) == (0, 1):        # chassis status: on, policy always-off, intrusion
            return {'data': [0x01, 0x10, 0x01, 0x00]}
        if (netfn, command) == (6, 1):        # X9 device id: fw 3.19, IPMI 2.0, Supermicro
            return {'data': [0x20, 0x01, 0x03, 0x19, 0x02, 0xbf, 0x7c, 0x2a, 0x00, 0x24, 0x06]}
        if (netfn, command) == (0, 2):
            if self.fail_power:
                raise RuntimeError('timeout di rete')
            return {'data': []}
        return {'error': 'not handled', 'code': 0xc1}

    def get_event_log(self):
        return iter(self.sel)

    def get_inventory_of_component(self, name):     # real pyghmi method name
        return {'Board manufacturer': 'Supermicro', 'Board product name': 'X9SRL-F',
                'Board serial number': 'ZM1', 'Product name': 'SYS', 'Serial Number': 'S1'}


@unittest.skipIf(Health is None, 'pyghmi not installed')
class TestNative(unittest.TestCase):
    def make(self, cmd):
        from webkvm.bmc.ipmi_native import NativeIpmiClient
        cfg = Config(bmc_host='x', bmc_password='x', ipmi_timeout=5)
        c = NativeIpmiClient(cfg)
        c._cmd = cmd
        c._command = lambda: cmd           # no real session
        c._drop = lambda: None
        return c

    def entries(self):
        return [
            # mask 0x3f: all thresholds; response order lnc,lcr,lnr,unc,ucr,unr
            Entry(1, 'CPU Temp', '°C', Reading(42.0), [0x3f, 2, 1, 0, 80, 85, 90]),
            Entry(2, 'FAN2', 'RPM', Reading(150.0, Health.Critical, ['lower critical threshold']),
                  [0x07, 60, 45, 30, 0, 0, 0]),
            Entry(3, 'FAN3', 'RPM', Reading(None, unavailable=1), [0x07, 60, 45, 30, 0, 0, 0]),
            Entry(4, 'Chassis Intru', '', Reading(None, Health.Warning, ['General Chassis intrusion'])),
            Entry(5, 'Sys Temp neg', '°C', Reading(-5.0), [0x02, 0, 0xF9, 0, 0, 0, 0], signed=True),
        ]

    def test_sensors(self):
        c = self.make(FakeCmd(self.entries()))
        s = {x['name']: x for x in asyncio.run(c.sensors())}
        cpu = s['CPU Temp']
        self.assertEqual((cpu['unit'], cpu['type'], cpu['status'], cpu['value']),
                         ('degrees C', 'temperature', 'ok', 42.0))
        self.assertEqual(cpu['thresholds'], {'lnc': 2, 'lcr': 1, 'lnr': 0, 'unc': 80, 'ucr': 85, 'unr': 90})
        self.assertEqual((s['FAN2']['status'], s['FAN2']['status_code'], s['FAN2']['type']), ('cr', 2, 'fan'))
        self.assertEqual(set(s['FAN2']['thresholds']), {'lnc', 'lcr', 'lnr'})
        self.assertFalse(s['FAN3']['present'])
        self.assertEqual(s['FAN3']['type'], 'fan')
        intru = s['Chassis Intru']
        self.assertEqual((intru['type'], intru['numeric'], intru['status']), ('discrete', False, 'nc'))
        self.assertEqual(intru['states'], ['General Chassis intrusion'])
        self.assertEqual(s['Sys Temp neg']['thresholds'], {'lcr': -7.0})   # 0xF9 signed

    def test_chassis_and_info(self):
        c = self.make(FakeCmd([]))
        ch = asyncio.run(c.chassis())
        self.assertTrue(ch['power_on'])
        self.assertTrue(ch['intrusion'])
        self.assertEqual(ch['power_restore_policy'], 'always-off')
        self.assertEqual(ch['last_power_event'], 'command')
        mc = asyncio.run(c.mc_info())
        self.assertEqual((mc['Firmware Revision'], mc['IPMI Version'], mc['Manufacturer Name'],
                          mc['Product ID']), ('3.19', '2.0', 'Supermicro', '1572'))
        self.assertEqual(asyncio.run(c.fru())['Board Product'], 'X9SRL-F')

    def test_sel(self):
        ev = [{'record_id': 7, 'timestamp': '2026-10-27T08:12:50', 'component_type': 'Fan',
               'component': 'FAN2', 'event': 'Lower Critical going low', 'deassertion': False},
              {'record_id': 8, 'component': 'Intru', 'event': 'General Chassis intrusion',
               'deassertion': True}]
        sel = asyncio.run(self.make(FakeCmd([], sel=ev)).sel())
        self.assertEqual(sel[0], {'id': 7, 'date': '2026-10-27', 'time': '08:12:50',
                                  'sensor': 'Fan FAN2', 'event': 'Lower Critical going low',
                                  'direction': 'Asserted'})
        self.assertEqual((sel[1]['date'], sel[1]['direction']), ('Pre-Init', 'Deasserted'))

    def test_real_pyghmi_api(self):
        """The methods and attributes used by the backend exist in the installed pyghmi."""
        from pyghmi.ipmi import command, sdr
        for name in ('raw_command', 'init_sdr', 'get_event_log', 'get_inventory_of_component'):
            self.assertTrue(hasattr(command.Command, name), name)
        for name in ('decode_sensor_reading', 'decode_value'):
            self.assertTrue(hasattr(sdr.SDREntry, name), name)
        self.assertTrue(hasattr(sdr.SDR, 'get_sensor_numbers'))

    def test_power_codes(self):
        cmd = FakeCmd([])
        c = self.make(cmd)
        for action, code in (('off', 0), ('on', 1), ('cycle', 2), ('reset', 3), ('soft', 5)):
            asyncio.run(c.power(action))
            self.assertEqual(cmd.calls[-1], (0, 2, (code,)))

    def test_power_never_retried(self):
        cmd = FakeCmd([], fail_power=True)
        c = self.make(cmd)
        with self.assertRaises(IpmiError):
            asyncio.run(c.power('reset'))
        self.assertEqual(sum(1 for call in cmd.calls if call[:2] == (0, 2)), 1)

    def test_read_retried_once(self):
        cmd = FakeCmd([])
        c = self.make(cmd)
        n = {'k': 0}
        orig = cmd.raw_command

        def flaky(**kw):
            n['k'] += 1
            if n['k'] == 1:
                raise RuntimeError('session expired')
            return orig(**kw)
        cmd.raw_command = flaky
        self.assertTrue(asyncio.run(c.chassis())['power_on'])


if __name__ == '__main__':
    unittest.main()
