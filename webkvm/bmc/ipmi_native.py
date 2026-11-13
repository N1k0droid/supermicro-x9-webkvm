"""Network IPMI in pure Python (pyghmi library): no external program, works on Windows too.
Same interface and same data as IpmiClient (ipmitool), so the collector, dashboard,
Prometheus and Zabbix do not change.

pyghmi handles the RMCP+ session (IPMI 2.0, UDP 623), the SDR and the decoding of readings and
SEL entries. Thresholds are fetched with "Get Sensor Thresholds" and converted with the sensor
formula read from the SDR. Chassis status, controller info and power commands are standard IPMI
commands sent raw, with the same meaning as `ipmitool chassis`.
"""
import asyncio
import logging
import threading

from .ipmi import POWER_ACTIONS, STATUS_CODE, IpmiError, sensor_type

log = logging.getLogger('webkvm.ipmi')

# pyghmi units -> ipmitool spelling (used by the UI, Prometheus and the Zabbix template)
UNITS = {'°C': 'degrees C', '°F': 'degrees F', 'V': 'Volts', 'W': 'Watts', 'A': 'Amps',
         'RPM': 'RPM'}
# chassis control (IPMI 2.0, table 28-4): same meaning as `ipmitool chassis power`
CHASSIS_CONTROL = {'off': 0, 'on': 1, 'cycle': 2, 'reset': 3, 'soft': 5}
RESTORE_POLICY = {0: 'always-off', 1: 'previous', 2: 'always-on', 3: 'unknown'}
THRESHOLD_ORDER = ('lnc', 'lcr', 'lnr', 'unc', 'ucr', 'unr')   # Get Sensor Thresholds response order


def _status_from_health(health):
    from pyghmi.constants import Health
    if health & Health.Failed:
        return 'nr'
    if health & Health.Critical:
        return 'cr'
    if health & Health.Warning:
        return 'nc'
    return 'ok'


def _signed(entry, raw):
    if entry.numeric_format == 2:
        return raw - 256 if raw & 0x80 else raw
    if entry.numeric_format == 1:
        return -((~raw) & 0xff) if raw & 0x80 else raw
    return raw


def _bcd(b):
    return f'{(b >> 4) & 0xf}{b & 0xf}'


class NativeIpmiClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._sem = asyncio.Semaphore(1)
        self._lock = threading.Lock()
        self._cmd = None

    # ------------------------------------------------------------- session
    def _command(self):
        if self._cmd is None:
            try:
                from pyghmi.ipmi import command
            except ImportError as e:
                raise IpmiError('pyghmi library not installed (pip install pyghmi)') from e
            try:
                self._cmd = command.Command(bmc=self.cfg.bmc_host, userid=self.cfg.bmc_user,
                                            password=self.cfg.bmc_password)
            except Exception as e:
                raise IpmiError(f'IPMI session not opened: {e}') from e
        return self._cmd

    def _drop(self):
        cmd, self._cmd = self._cmd, None
        if cmd is not None:
            try:
                cmd.ipmi_session.logout()
            except Exception:
                pass

    def _raw(self, netfn, command, data=(), rslun=0):
        rsp = self._command().raw_command(netfn=netfn, command=command, data=data, rslun=rslun)
        if 'error' in rsp:
            raise IpmiError(f'IPMI command {netfn:#x}/{command:#x}: {rsp["error"]}')
        return bytes(rsp['data'])

    async def _call(self, fn, *args, retry=True):
        """Run fn in a thread (pyghmi is blocking). With retry=True a failure reopens the
        session and retries once; power commands are NEVER repeated."""
        def run():
            with self._lock:
                for attempt in ((1, 2) if retry else (2,)):
                    try:
                        return fn(*args)
                    except IpmiError:
                        if attempt == 2:
                            self._drop()
                            raise
                        self._drop()
                    except Exception as e:
                        self._drop()
                        if attempt == 2:
                            raise IpmiError(f'{type(e).__name__}: {e}') from e
        async with self._sem:
            try:
                return await asyncio.wait_for(asyncio.to_thread(run), self.cfg.ipmi_timeout * 3)
            except asyncio.TimeoutError:
                raise IpmiError('IPMI session timed out')

    # ------------------------------------------------------------- sensors
    def _sensors(self):
        cmd = self._command()
        sdr = cmd.init_sdr()
        out = []
        for num in list(sdr.get_sensor_numbers()):
            entry = sdr.sensors[num]
            rsp = cmd.raw_command(netfn=4, command=0x2d, rslun=entry.sensor_lun,
                                  data=(entry.sensor_number,))
            if 'error' in rsp:
                if rsp.get('code') == 203:       # optional sensor not present
                    continue
                raise IpmiError(f'reading sensor {entry.name}: {rsp["error"]}')
            reading = entry.decode_sensor_reading(cmd, rsp['data'])
            # "threshold" sensor (temperature, fan, voltage...) even when it has no reading now;
            # reading type 1 = threshold based (IPMI table 42-1), the others are state based
            analog = entry.has_thresholds or getattr(entry, 'reading_type', None) == 1
            unit = UNITS.get(entry.unit_suffix, entry.unit_suffix) if analog else 'discrete'
            if reading.unavailable:
                status, value = 'na', None
            else:
                status, value = _status_from_health(reading.health), reading.value
            thresholds = self._thresholds(cmd, entry) if value is not None and entry.has_thresholds else {}
            stype = sensor_type(unit, entry.name)
            if stype == 'other' and value is None and not unit.strip():
                # e.g. "Chassis Intru" on the X9: declared as threshold based, but no unit or value
                stype, unit = 'discrete', 'discrete'
            out.append({
                'name': entry.name,
                'value': round(value, 3) if value is not None else None,
                'unit': unit,
                'type': stype,
                'status': status,
                'status_code': STATUS_CODE.get(status, -1),
                'numeric': value is not None,
                'present': status != 'na',
                'discrete_state': None,
                'states': list(reading.states),
                'thresholds': thresholds,
            })
        return out

    def _thresholds(self, cmd, entry):
        rsp = cmd.raw_command(netfn=4, command=0x27, rslun=entry.sensor_lun,
                              data=(entry.sensor_number,))
        if 'error' in rsp:
            return {}
        data = bytes(rsp['data'])
        mask, values = data[0], data[1:7]
        out = {}
        for i, key in enumerate(THRESHOLD_ORDER):
            if mask & (1 << i) and i < len(values):
                try:
                    out[key] = round(entry.decode_value(cmd, _signed(entry, values[i])), 3)
                except Exception:
                    pass
        return out

    # ------------------------------------------------------------- chassis
    def _chassis(self):
        d = self._raw(0x00, 0x01)                # Get Chassis Status
        cur, last = d[0], d[1] if len(d) > 1 else 0
        misc = d[2] if len(d) > 2 else 0
        events = [name for bit, name in ((0, 'ac-failed'), (1, 'overload'), (2, 'interlock'),
                                         (3, 'fault'), (4, 'command')) if last & (1 << bit)]
        return {
            'power_on': bool(cur & 0x01),
            'power_overload': bool(cur & 0x02),
            'main_power_fault': bool(cur & 0x08),
            'power_control_fault': bool(cur & 0x10),
            'power_restore_policy': RESTORE_POLICY[(cur >> 5) & 0x03],
            'last_power_event': ', '.join(events) or None,
            'intrusion': bool(misc & 0x01),
            'drive_fault': bool(misc & 0x04),
            'fan_fault': bool(misc & 0x08),
        }

    def _mc_info(self):
        d = self._raw(0x06, 0x01)                # Get Device ID
        mfg = d[6] | (d[7] << 8) | (d[8] << 16) if len(d) >= 9 else None
        names = {10876: 'Supermicro', 10437: 'Peppercon (ATEN)', 343: 'Intel'}
        return {
            'Firmware Revision': f'{d[2] & 0x7f}.{_bcd(d[3])}',
            'IPMI Version': f'{d[4] & 0xf}.{(d[4] >> 4) & 0xf}',
            'Manufacturer ID': str(mfg),
            'Manufacturer Name': names.get(mfg, str(mfg)),
            'Product ID': str(d[9] | (d[10] << 8)) if len(d) >= 11 else None,
        }

    def _fru(self):
        try:
            inv = self._command().get_inventory_of_component('System') or {}
        except Exception as e:
            log.warning('FRU not available: %s: %s', type(e).__name__, e)
            return {}

        def text(key):
            v = inv.get(key)
            if isinstance(v, bytes):
                v = v.decode('ascii', 'replace')
            return str(v).strip() or None if v is not None else None
        return {
            'Board Mfg': text('Board manufacturer'),
            'Board Product': text('Board product name'),
            'Board Serial': text('Board serial number'),
            'Product Name': text('Product name'),
            'Product Serial': text('Serial Number'),
        }

    def _sel(self):
        out = []
        for ev in self._command().get_event_log():
            ts = ev.get('timestamp') or ''
            date, _, time_ = ts.partition('T')
            sensor = ' '.join(x for x in (ev.get('component_type'), ev.get('component')) if x)
            event = ev.get('event') or ''
            if ev.get('event_data'):
                event += f' ({ev["event_data"]})'
            out.append({
                'id': ev.get('record_id', 0),
                'date': date or 'Pre-Init',
                'time': time_[:8] if time_ else '',
                'sensor': sensor,
                'event': event,
                'direction': 'Deasserted' if ev.get('deassertion') else 'Asserted',
            })
        return out

    def _power(self, action):
        self._raw(0x00, 0x02, data=(CHASSIS_CONTROL[action],))   # Chassis Control
        return f'Chassis Power Control: {action}'

    # ---------------------------------------------- IpmiClient interface
    async def sensors(self):
        return await self._call(self._sensors)

    async def chassis(self):
        return await self._call(self._chassis)

    async def sel(self):
        return await self._call(self._sel)

    async def mc_info(self):
        return await self._call(self._mc_info)

    async def fru(self):
        return await self._call(self._fru)

    async def power(self, action):
        if action not in POWER_ACTIONS:
            raise ValueError(f'invalid action: {action}')
        return await self._call(self._power, action, retry=False)

    def describe(self):
        return 'native IPMI (pyghmi, RMCP+ over UDP 623)'
