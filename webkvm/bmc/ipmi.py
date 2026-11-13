"""IPMI through ipmitool (lanplus, UDP 623): sensors, chassis status, SEL, BMC info, power.

The password goes through the IPMI_PASSWORD environment variable (option -E), never on the
command line, so it does not show up in `ps`.
"""
import asyncio
import logging
import os
import re

log = logging.getLogger('webkvm.ipmi')

THRESHOLD_KEYS = ('lnr', 'lcr', 'lnc', 'unc', 'ucr', 'unr')
POWER_ACTIONS = ('on', 'off', 'cycle', 'reset', 'soft')

# ipmitool status -> numeric code (0 ok, 1 non-critical, 2 critical, 3 non-recoverable)
STATUS_CODE = {'ok': 0, 'nc': 1, 'cr': 2, 'nr': 3}


class IpmiError(Exception):
    pass


# ------------------------------------------------------------------ parser
def _num(text):
    text = text.strip()
    if text in ('', 'na', 'n/a', 'disabled', 'No Reading'):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def sensor_type(unit, name=''):
    u = unit.lower()
    if 'degrees' in u:
        return 'temperature'
    if u == 'rpm':
        return 'fan'
    if u.startswith('volt'):
        return 'voltage'
    if u.startswith('watt'):
        return 'power'
    if u.startswith('amp'):
        return 'current'
    if u == 'discrete':
        return 'discrete'
    n = name.lower()                     # absent sensor: ipmitool reports no unit
    if n.startswith('fan'):
        return 'fan'
    if 'temp' in n:
        return 'temperature'
    return 'other'


def parse_sensor(text):
    """Output of `ipmitool sensor`:
    name | value | unit | status | lnr | lcr | lnc | unc | ucr | unr"""
    out = []
    for line in text.splitlines():
        cols = [c.strip() for c in line.split('|')]
        if len(cols) < 4 or not cols[0]:
            continue
        name, raw, unit, status = cols[0], cols[1], cols[2], cols[3].lower()
        thresholds = {}
        for key, val in zip(THRESHOLD_KEYS, cols[4:10]):
            n = _num(val)
            if n is not None:
                thresholds[key] = n
        stype = sensor_type(unit, name)
        if stype == 'discrete':
            value = None
            try:
                state = int(raw, 16) if raw.lower().startswith('0x') else None
            except ValueError:
                state = None
        else:
            value = _num(raw)
            state = None
        present = status != 'na' and (value is not None or stype == 'discrete')
        out.append({
            'name': name,
            'value': value,
            'unit': unit,
            'type': stype,
            'status': status,
            'status_code': STATUS_CODE.get(status, 0 if status.startswith('0x') else -1),
            'numeric': value is not None,
            'present': present,
            'discrete_state': state,
            'states': [],
            'thresholds': thresholds,
        })
    return out


def parse_keyvalue(text):
    """`Key : value` lines (chassis status, mc info, fru print)."""
    out = {}
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key, val = key.strip(), val.strip()
        if key and key not in out:
            out[key] = val
    return out


def parse_chassis(text):
    kv = parse_keyvalue(text)

    def flag(key, true_word):
        v = kv.get(key, '').lower()
        return None if not v else v == true_word
    return {
        'power_on': flag('System Power', 'on'),
        'power_overload': flag('Power Overload', 'true'),
        'main_power_fault': flag('Main Power Fault', 'true'),
        'power_control_fault': flag('Power Control Fault', 'true'),
        'power_restore_policy': kv.get('Power Restore Policy'),
        'last_power_event': kv.get('Last Power Event') or None,
        'intrusion': flag('Chassis Intrusion', 'active'),
        'drive_fault': flag('Drive Fault', 'true'),
        'fan_fault': flag('Cooling/Fan Fault', 'true'),
    }


_SEL_LINE = re.compile(r'^\s*([0-9a-fA-F]+)\s*\|')


def parse_sel(text):
    """Output of `ipmitool sel elist`: id | date | time | sensor | event [| direction]."""
    out = []
    for line in text.splitlines():
        if not _SEL_LINE.match(line):
            continue
        cols = [c.strip() for c in line.split('|')]
        while len(cols) < 6:
            cols.append('')
        rid, date, time_, sensor, event, direction = cols[:6]
        extra = ' | '.join(cols[6:]) if len(cols) > 6 else ''
        out.append({
            'id': int(rid, 16),
            'date': date,
            'time': time_,
            'sensor': sensor,
            'event': event + (f' ({extra})' if extra else ''),
            'direction': direction,
        })
    return out


# ------------------------------------------------------------------ client
class IpmiClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._sem = asyncio.Semaphore(1)     # the BMC copes badly with parallel sessions

    def _base_args(self):
        c = self.cfg
        args = [c.ipmitool, '-I', c.ipmi_interface, '-H', c.bmc_host, '-U', c.bmc_user, '-E',
                '-N', '3', '-R', '2']
        if c.ipmi_interface == 'lanplus' and c.ipmi_cipher:
            args += ['-C', c.ipmi_cipher]
        return args

    async def run(self, *cmd, timeout=None):
        env = dict(os.environ, IPMI_PASSWORD=self.cfg.bmc_password)
        timeout = timeout or self.cfg.ipmi_timeout
        async with self._sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._base_args(), *cmd, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            except FileNotFoundError as e:
                raise IpmiError(f'ipmitool not found ({self.cfg.ipmitool})') from e
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise IpmiError(f'timeout ipmitool {" ".join(cmd)}')
        if proc.returncode != 0:
            msg = err.decode('utf-8', 'replace').strip().splitlines()
            raise IpmiError(f'ipmitool {" ".join(cmd)}: {msg[-1] if msg else proc.returncode}')
        return out.decode('utf-8', 'replace')

    async def sensors(self):
        return parse_sensor(await self.run('sensor'))

    async def chassis(self):
        return parse_chassis(await self.run('chassis', 'status'))

    async def sel(self):
        return parse_sel(await self.run('sel', 'elist'))

    async def mc_info(self):
        return parse_keyvalue(await self.run('mc', 'info'))

    async def fru(self):
        try:
            return parse_keyvalue(await self.run('fru', 'print', '0'))
        except IpmiError as e:
            log.info('FRU not available: %s', e)
            return {}

    async def power(self, action):
        if action not in POWER_ACTIONS:
            raise ValueError(f'invalid action: {action}')
        return (await self.run('chassis', 'power', action)).strip()

    def describe(self):
        c = self.cfg
        return f'ipmitool ({c.ipmitool}, interface {c.ipmi_interface}, cipher {c.ipmi_cipher or "default"})'


def make_ipmi_client(cfg):
    """Backend chosen with IPMI_BACKEND: 'native' (pyghmi, default) or 'ipmitool'."""
    if cfg.ipmi_backend == 'ipmitool':
        return IpmiClient(cfg)
    from .ipmi_native import NativeIpmiClient
    return NativeIpmiClient(cfg)
