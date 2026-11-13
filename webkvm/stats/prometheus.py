"""Renders the statistics in the Prometheus text format (exposition format 0.0.4)."""


def _esc(v):
    return str(v).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def _labels(**kw):
    return '{' + ','.join(f'{k}="{_esc(v)}"' for k, v in kw.items()) + '}'


def _fmt(v):
    if v is True:
        return '1'
    if v is False:
        return '0'
    return repr(float(v))


def render(snapshot, kvm_status=None, info=None):
    lines = []

    def metric(name, help_, typ, samples):
        lines.append(f'# HELP {name} {help_}')
        lines.append(f'# TYPE {name} {typ}')
        for labels, value in samples:
            if value is None:
                continue
            lines.append(f'{name}{labels} {_fmt(value)}')

    sensors = snapshot.get('sensors') or []
    metric('bmc_up', 'BMC raggiungibile via IPMI (1) o no (0)', 'gauge',
           [('', snapshot.get('up', False))])
    metric('bmc_scrape_duration_seconds', 'Duration of the last IPMI poll', 'gauge',
           [('', snapshot.get('scrape_duration'))])
    metric('bmc_last_success_timestamp_seconds', 'Last successful IPMI poll', 'gauge',
           [('', snapshot.get('ts'))])
    metric('bmc_sensor_value', 'Sensor reading', 'gauge',
           [(_labels(sensor=s['name'], unit=s['unit'], type=s['type']), s['value'])
            for s in sensors if s['numeric']])
    metric('bmc_sensor_status', 'Sensor status: 0 ok, 1 non-critical, 2 critical, 3 non-recoverable',
           'gauge',
           [(_labels(sensor=s['name'], type=s['type']), s['status_code'])
            for s in sensors if s['present'] and s['status_code'] >= 0])
    metric('bmc_sensor_ok', 'Sensor in ok state (1) or not (0)', 'gauge',
           [(_labels(sensor=s['name'], type=s['type']), s['status_code'] == 0)
            for s in sensors if s['present'] and s['status_code'] >= 0])
    thr = []
    for s in sensors:
        if s['numeric']:
            for kind, val in s['thresholds'].items():
                thr.append((_labels(sensor=s['name'], kind=kind), val))
    metric('bmc_sensor_threshold', 'BMC thresholds (lnr, lcr, lnc, unc, ucr, unr)', 'gauge', thr)
    ch = snapshot.get('chassis') or {}
    metric('bmc_chassis_power_on', 'Server power on (1) or off (0)', 'gauge',
           [('', ch.get('power_on'))])
    metric('bmc_chassis_intrusion', 'Chassis intrusion detected', 'gauge',
           [('', ch.get('intrusion'))])
    faults = [(_labels(fault=k), ch.get(k)) for k in
              ('power_overload', 'main_power_fault', 'power_control_fault', 'drive_fault', 'fan_fault')]
    metric('bmc_chassis_fault', 'Faults reported by the chassis', 'gauge', faults)
    metric('bmc_sel_entries', 'Entries in the BMC event log', 'gauge',
           [('', snapshot.get('sel_count'))])
    if kvm_status is not None:
        metric('webkvm_kvm_connected', 'KVM session to the BMC active', 'gauge',
               [('', kvm_status['state'] == 'connected')])
        metric('webkvm_kvm_viewers', 'Connected KVM viewers', 'gauge', [('', kvm_status['viewers'])])
    if info:
        metric('bmc_info', 'BMC information', 'gauge',
               [(_labels(**{k: v for k, v in info.items() if v}), 1)])
    return '\n'.join(lines) + '\n'
