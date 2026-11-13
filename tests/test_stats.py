import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webkvm.bmc import ipmi                     # noqa: E402
from webkvm.stats import prometheus             # noqa: E402
from webkvm.stats.store import Store            # noqa: E402

import fixtures                                 # noqa: E402


class TestParsers(unittest.TestCase):
    def test_sensor(self):
        s = {x['name']: x for x in ipmi.parse_sensor(fixtures.SENSOR)}
        self.assertEqual(len(s), 10)
        cpu = s['CPU Temp']
        self.assertEqual((cpu['value'], cpu['type'], cpu['status_code']), (42.0, 'temperature', 0))
        self.assertEqual(cpu['thresholds']['ucr'], 85.0)
        self.assertEqual(s['Peripheral Temp']['status_code'], 1)
        self.assertEqual(s['FAN2']['status_code'], 2)
        self.assertEqual(s['FAN1']['type'], 'fan')
        self.assertFalse(s['FAN3']['present'])
        self.assertFalse(s['FAN3']['numeric'])
        self.assertEqual(s['12V']['type'], 'voltage')
        intru = s['Chassis Intru']
        self.assertEqual((intru['type'], intru['numeric'], intru['discrete_state']), ('discrete', False, 0))

    def test_chassis(self):
        c = ipmi.parse_chassis(fixtures.CHASSIS)
        self.assertTrue(c['power_on'])
        self.assertFalse(c['intrusion'])
        self.assertFalse(c['fan_fault'])
        self.assertEqual(c['power_restore_policy'], 'always-off')

    def test_sel(self):
        e = ipmi.parse_sel(fixtures.SEL)
        self.assertEqual([x['id'] for x in e], [1, 2, 10])
        self.assertIn('Reading 150', e[1]['event'])
        self.assertEqual(e[2]['direction'], 'Deasserted')

    def test_keyvalue(self):
        mc = ipmi.parse_keyvalue(fixtures.MC_INFO)
        self.assertEqual(mc['Firmware Revision'], '3.19')
        fru = ipmi.parse_keyvalue(fixtures.FRU)
        self.assertEqual(fru['Board Product'], 'X9SRL-F')


class TestStore(unittest.TestCase):
    def test_history_and_sel(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, 'x.sqlite'))
            now = int(time.time())
            for i in range(60):
                st.add_samples(now - 3600 + i * 60, [('CPU Temp', 'degrees C', 'temperature', 40 + i % 5)])
            pts = st.history('CPU Temp', now - 3600, now, points=10)
            self.assertTrue(5 <= len(pts) <= 11)
            self.assertTrue(all(p['min'] <= p['avg'] <= p['max'] for p in pts))
            entries = ipmi.parse_sel(fixtures.SEL)
            self.assertEqual(len(st.add_sel(entries)), 3)
            self.assertEqual(len(st.add_sel(entries)), 0)          # dedup
            self.assertEqual(len(st.sel()), 3)
            st.purge(now - 1800)
            self.assertLess(len(st.history('CPU Temp', now - 3600, now, points=100)), 40)
            st.close()


class TestPrometheus(unittest.TestCase):
    def test_render(self):
        snap = {'up': True, 'ts': 1.0, 'scrape_duration': 0.5, 'sel_count': 3,
                'chassis': ipmi.parse_chassis(fixtures.CHASSIS),
                'sensors': ipmi.parse_sensor(fixtures.SENSOR)}
        out = prometheus.render(snap, {'state': 'idle', 'viewers': 0}, {'bmc_firmware': '3.19'})
        self.assertIn('bmc_up 1\n', out)
        self.assertIn('bmc_sensor_value{sensor="CPU Temp",unit="degrees C",type="temperature"} 42.0', out)
        self.assertIn('bmc_sensor_status{sensor="FAN2",type="fan"} 2.0', out)
        self.assertIn('bmc_sensor_threshold{sensor="CPU Temp",kind="ucr"} 85.0', out)
        self.assertIn('bmc_chassis_power_on 1', out)
        self.assertNotIn('FAN3', out)                               # absent sensor
        for line in out.splitlines():                               # no malformed lines
            self.assertTrue(line.startswith('#') or ' ' in line)


if __name__ == '__main__':
    unittest.main()
