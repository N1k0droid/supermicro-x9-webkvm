"""Diagnostics against the real BMC: python -m webkvm check [ipmi | kvm | kvm-watch [seconds]]

Tests, one at a time, the parts that depend on the firmware: port reachability, web login and
KVM descriptor, KVM handshake + first frame, IPMI. It never prints passwords, tokens or
cookies, and never captures screen pixels: the output can be shared as it is.
"""
import asyncio
import re
import socket
import struct
import subprocess
import time

from .bmc.ipmi import IpmiError, make_ipmi_client
from .bmc.web_login import BmcLoginError, BmcWebSession
from .config import Config
from .kvm.aten import BmcLink, decode_hermon

results = []
state = {'login_ok': False}


def report(level, msg):
    results.append(level)
    print(f'[{level}] {msg}', flush=True)


def _redact(text, secrets):
    for s in secrets:
        if s and len(s) >= 3:
            text = text.replace(s, '***')
    return text


def check_ports(cfg):
    print('\n== BMC TCP ports ==')
    web_port = cfg.bmc_web_port or (443 if cfg.bmc_web_scheme == 'https' else 80)
    for port, what in ((web_port, 'web interface'), (cfg.bmc_kvm_port or 5900, 'KVM')):
        try:
            with socket.create_connection((cfg.bmc_host, port), timeout=5):
                report('OK', f'{cfg.bmc_host}:{port} reachable ({what})')
        except OSError as e:
            report('ERROR', f'{cfg.bmc_host}:{port} unreachable ({what}): {e}')
    print('[INFO] UDP 623 (IPMI) cannot be probed like this: the IPMI test checks it')


def _scrub(text, secrets):
    """Hide known secrets, <argument> values and long token-like strings."""
    text = _redact(text, secrets)
    text = re.sub(r'(<argument>)([^<]*)(</argument>)',
                  lambda m: f'{m.group(1)}[{len(m.group(2).strip())} chars]{m.group(3)}', text)
    text = re.sub(r'(SID=)[^;\s"\']+', r'\1***', text)
    return re.sub(r'\b[A-Za-z0-9]{16,}\b', lambda m: f'[token {len(m.group(0))}]', text)


def _show(label, resp, body, secrets):
    hdr = {k: resp.headers.get(k) for k in ('Content-Type', 'Content-Length', 'Location')
           if resp.headers.get(k)}
    cookies = []
    for c in resp.headers.get_all('Set-Cookie') or []:
        name, _, rest = c.partition('=')
        cookies.append(f'{name.strip()}=<{len(rest.split(";")[0])} chars>')
    snippet = re.sub(r'\s+', ' ', body.decode('utf-8', 'replace')).strip()
    print(f'  -- {label}: HTTP {resp.status} {hdr} cookies={cookies or "none"} '
          f'body={len(body)} bytes')
    print(f'     {_scrub(snippet[:600], secrets)!r}')


def _dump_kvm_page(web, secrets):
    """Lines of the "Console Redirection" page and its scripts that mention the viewer:
    they show which request the web interface really uses to download the JNLP."""
    print('\n== How the web interface launches the console ==')
    pattern = re.compile(r'jnlp|jwsk|url_type|ikvm|launch|\.js', re.I)
    pages = [web.KVM_PAGE]
    seen = set()
    while pages and len(seen) < 6:
        path = pages.pop(0)
        if path in seen:
            continue
        seen.add(path)
        try:
            resp, body = web._request(path, cookies=web.BROWSER_COOKIES,
                                      referer='/cgi/url_redirect.cgi?url_name=mainmenu')
        except OSError as e:
            print(f'  -- {path}: error {e}')
            continue
        text = body.decode('utf-8', 'replace')
        print(f'  -- {path}: HTTP {resp.status}, {len(body)} bytes')
        shown = 0
        for line in text.splitlines():
            if pattern.search(line) and shown < 25:
                print(f'     {_scrub(line.strip()[:200], secrets)}')
                shown += 1
        for js in re.findall(r'src="\.\./(js/[^"]+\.js)"', text):
            if 'utils' not in js and 'jquery' not in js.lower():
                pages.append('/' + js)


def debug_web(cfg):
    """Show the raw answers of login.cgi and url_redirect.cgi (without secrets)."""
    import base64
    import urllib.parse
    print('\n== Web response details (for diagnosis) ==')
    secrets = [cfg.bmc_password, base64.b64encode(cfg.bmc_password.encode()).decode()]
    variants = [('base64', base64.b64encode(cfg.bmc_user.encode()).decode(),
                 base64.b64encode(cfg.bmc_password.encode()).decode()),
                ('plain', cfg.bmc_user, cfg.bmc_password)]
    for variant, name, pwd in variants:
        web = BmcWebSession(cfg.bmc_host, cfg.bmc_user, cfg.bmc_password,
                            scheme=cfg.bmc_web_scheme, port=cfg.bmc_web_port)
        data = urllib.parse.urlencode({'name': name, 'pwd': pwd, 'check': '00'}).encode()
        try:
            resp, body = web._request('/cgi/login.cgi', data)
        except OSError as e:
            print(f'  -- login {variant}: error {e}')
            continue
        _show(f'login.cgi ({variant})', resp, body, secrets)
        sid = web._extract_sid(resp)
        if not sid:
            continue
        secrets.append(sid)
        web.sid = sid
        for path in ('/cgi/url_redirect.cgi?url_name=mainmenu',
                     '/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk'):
            try:
                resp, body = web._request(path)
                _show(f'GET {path}', resp, body, secrets)
            except OSError as e:
                print(f'  -- GET {path}: error {e}')
        web.logout()


def check_web(cfg):
    print('\n== Web login and KVM descriptor ==')
    web = BmcWebSession(cfg.bmc_host, cfg.bmc_user, cfg.bmc_password,
                        scheme=cfg.bmc_web_scheme, port=cfg.bmc_web_port)
    secrets = [cfg.bmc_password]
    try:
        web.login()
    except BmcLoginError as e:
        report('ERROR', str(e))
        # response details, to understand what this firmware expects
        try:
            resp, body = web._request('/cgi/login.cgi', b'name=x&pwd=x&check=00')
            cookies = [c.split('=', 1)[0].strip() for c in resp.headers.get_all('Set-Cookie') or []]
            snippet = re.sub(r'\s+', ' ', body.decode('utf-8', 'replace'))[:300]
            print(f'[INFO] login.cgi with fake credentials: HTTP {resp.status}, '
                  f'cookies set: {cookies or "none"}')
            print(f'[INFO] start of the response: {snippet!r}')
        except OSError as e2:
            print(f'[INFO] login.cgi unreachable: {e2}')
        return None
    report('OK', f'web login succeeded ({web.variant} fields, SID of {len(web.sid)} characters)')
    state['login_ok'] = True
    secrets.append(web.sid)
    try:
        args = web.kvm_descriptor()
    except BmcLoginError as e:
        report('ERROR', str(e))
        _dump_kvm_page(web, secrets)
        web.logout()
        return None
    host, user, pwd, name, port = args[:5]
    secrets += [user, pwd]
    report('OK', f'KVM descriptor: {len(args)} arguments, host {host}, KVM port {port}, '
                 f'token of {len(user)}/{len(pwd)} characters (variant "{web.descriptor_variant}")')
    for n, status, ln, nargs in web.attempts:
        print(f'[INFO] attempt {n}: HTTP {status}, {ln} bytes, {nargs} arguments')
    extra = ', '.join(_redact(a, secrets) for a in args[5:12])
    print(f'[INFO] further arguments: {extra}')
    if host != cfg.bmc_host:
        print(f'[INFO] the BMC announces itself as {host}: using BMC_HOST={cfg.bmc_host} anyway')
    return web, user, pwd, int(port)


def _describe(msg):
    """Screen message metadata only (never pixels)."""
    p = msg['payload']
    if len(p) < 10:
        return f'header {msg["w"]}x{msg["h"]} mode {msg["mode"]}, payload {len(p)} bytes (empty)'
    w, h, rects = decode_hermon(p, msg['w'], msg['h'])
    kind = f'full frame {w}x{h}' if w else f'{len(rects)} tiles'
    return (f'header {msg["w"]}x{msg["h"]} mode {msg["mode"]}, {kind}, '
            f'{"16" if p[1] == 0 else "8"} bit, {len(p)} bytes')


def _watch_updates(link, seconds):
    """Watch updates the way the gateway does: incremental requests and, after 10 s of
    silence, a full request. Prints headers only."""
    print(f'[INFO] watching updates for {seconds} s (headers only, no images)')
    print('[INFO] printing only events (full frames, resolution changes, many tiles) '
          'and a summary every 5 s')
    link.sock.settimeout(10)
    t0 = time.monotonic()
    incremental = True
    link.request_update(incremental=True)
    last_hdr = (link.width, link.height)
    n_msg = n_tiles = 0
    next_sum = 5
    while time.monotonic() - t0 < seconds:
        msg = link.read_message()
        t = time.monotonic() - t0
        if t >= next_sum:
            print(f'[INFO] {t:5.1f} s  last 5 s: {n_msg} messages, {n_tiles} tiles')
            n_msg = n_tiles = 0
            next_sum += 5
        if msg == 'timeout':
            print(f'[INFO] {t:5.1f} s  no answer within 10 s to the '
                  f'{"incremental" if incremental else "full"} request: asking for a full frame')
            incremental = False
            link.request_update(incremental=False)
            continue
        if msg is None:
            continue
        p = msg['payload']
        full = len(p) >= 10 and p[0] == 1
        tiles = struct.unpack('>I', p[2:6])[0] if len(p) >= 10 and not full else 0
        n_msg += 1
        n_tiles += tiles
        hdr = (msg['w'], msg['h'])
        if full or hdr != last_hdr or tiles > 8 or (len(p) < 10 and hdr != last_hdr) or not incremental:
            extra = f' (was {last_hdr[0]}x{last_hdr[1]})' if hdr != last_hdr else ''
            print(f'[INFO] {t:5.1f} s  {"inc " if incremental else "FULL"} {_describe(msg)}{extra}')
        last_hdr = hdr
        incremental = True
        link.request_update(incremental=True)


def check_kvm(cfg, creds, watch=0):
    print('\n== KVM console ==')
    web, user, pwd, port = creds
    port = cfg.bmc_kvm_port or port
    link = None
    try:
        t0 = time.monotonic()
        link = BmcLink(cfg.bmc_host, port, user, pwd)
        report('OK', f'KVM handshake and login succeeded in {time.monotonic() - t0:.1f} s')
        link.request_update(incremental=False)
        seen = {}
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            msg = link.read_message()
            if msg == 'timeout':
                link.request_update(incremental=False)
                continue
            if msg is None:
                seen['other'] = seen.get('other', 0) + 1
                continue
            if msg.get('nosignal'):
                report('WARNING', 'no video signal from the server (rebooting or screen off?)')
                return
            p = msg['payload']
            w, h, rects = decode_hermon(p, msg['w'], msg['h'])
            mode = p[0] if p else None
            depth = '16 bit' if p and p[1] == 0 else '8 bit'
            print(f'[INFO] screen message: header {msg["w"]}x{msg["h"]}, payload mode {mode}, '
                  f'{depth}, {len(p)} bytes')
            if w:
                report('OK', f'first full frame: real resolution {w}x{h} '
                             f'(in {time.monotonic() - t0:.1f} s)')
                if w * h == 0:
                    report('WARNING', 'empty resolution: screen off or in standby?')
                if watch:
                    _watch_updates(link, watch)
                return
            link.request_update(incremental=False)
        report('ERROR', f'no full frame within 25 s (other messages: {seen})')
    except Exception as e:
        report('ERROR', f'KVM: {type(e).__name__}: {e}')
    finally:
        if link:
            link.close()
        web.logout()


def check_ipmi(cfg):
    print('\n== IPMI (UDP 623) ==')
    if cfg.ipmi_backend == 'ipmitool':
        try:
            ver = subprocess.run([cfg.ipmitool, '-V'], capture_output=True, text=True, timeout=10)
            print(f'[INFO] {ver.stdout.strip() or ver.stderr.strip()}')
        except (OSError, subprocess.SubprocessError) as e:
            report('ERROR', f'ipmitool cannot be run ({cfg.ipmitool}): {e}')
            return

    async def run():
        ip = make_ipmi_client(cfg)
        print(f'[INFO] backend: {ip.describe()}')
        t0 = time.monotonic()
        try:
            mc = await ip.mc_info()
            report('OK', f'IPMI session opened in {time.monotonic() - t0:.1f} s; firmware '
                         f'{mc.get("Firmware Revision")}, IPMI {mc.get("IPMI Version")}, '
                         f'{mc.get("Manufacturer Name")}')
        except IpmiError as e:
            report('ERROR', f'controller info: {e}')
            if cfg.ipmi_backend == 'ipmitool':
                print('[INFO] for an RMCP+ session error try IPMI_CIPHER=1, 2 or empty, '
                      'or IPMI_INTERFACE=lan')
            else:
                print('[INFO] check that IPMI over LAN is enabled on the BMC and that the user '
                      'has administrator privileges')
            return
        try:
            fru = await ip.fru()
            print(f'[INFO] FRU: {fru.get("Board Mfg")} {fru.get("Board Product")}, '
                  f'product {fru.get("Product Name")}')
        except IpmiError as e:
            report('WARNING', f'FRU: {e}')
        try:
            t0 = time.monotonic()
            sensors = await ip.sensors()
            numeric = [s for s in sensors if s['numeric']]
            bad = [s for s in sensors if s['status'] in ('nc', 'cr', 'nr')]
            report('OK', f'sensors: {len(sensors)} read in {time.monotonic() - t0:.1f} s, '
                         f'{len(numeric)} numeric, {len(bad)} out of thresholds')
            for s in sensors:
                if s['numeric']:
                    v = f'{s["value"]:g} {s["unit"]}'
                else:
                    v = ', '.join(s.get('states') or []) or s['status']
                t = s['thresholds']
                thr = ' '.join(f'{k}={t[k]:g}' for k in ('lcr', 'lnc', 'unc', 'ucr') if k in t)
                print(f'        {s["name"]:<18} {v[:28]:<28} {s["type"]:<12} '
                      f'status={s["status"]:<3} {thr}')
        except IpmiError as e:
            report('ERROR', f'sensors: {e}')
        try:
            ch = await ip.chassis()
            report('OK', f'chassis: power {"on" if ch["power_on"] else "off"}, '
                         f'restore policy {ch["power_restore_policy"]}, intrusion {ch["intrusion"]}')
        except IpmiError as e:
            report('ERROR', f'chassis status: {e}')
        try:
            sel = await ip.sel()
            report('OK', f'SEL: {len(sel)} entries' + (f', last: {sel[-1]["date"]} {sel[-1]["time"]} '
                                                         f'{sel[-1]["sensor"]} {sel[-1]["event"]}'
                                                         if sel else ''))
        except IpmiError as e:
            report('ERROR', f'SEL: {e}')
    asyncio.run(run())


def main(argv=()):
    """argv: none = everything; 'ipmi' = IPMI only; 'kvm' = web login + KVM console only;
    'kvm-watch [seconds]' = like 'kvm', then watch the screen updates (headers only)."""
    cfg = Config.from_env()
    if not cfg.bmc_host or not cfg.bmc_password:
        print('set at least BMC_HOST, BMC_USER and BMC_PASSWORD')
        return 2
    only = argv[0] if argv else None
    watch = 0
    if only == 'kvm-watch':
        only, watch = 'kvm', int(argv[1]) if len(argv) > 1 else 45
    print(f'webkvm diagnostics against {cfg.bmc_host} (user {cfg.bmc_user})')
    if only != 'ipmi':
        check_ports(cfg)
        creds = check_web(cfg)
        if creds:
            check_kvm(cfg, creds, watch)
        else:
            if not state['login_ok']:
                debug_web(cfg)
            print('\n== KVM console ==\n[SKIPPED] the KVM descriptor is needed first')
    if only != 'kvm':
        check_ipmi(cfg)
    n_err = results.count('ERROR')
    print(f'\nResult: {results.count("OK")} OK, {results.count("WARNING")} warnings, {n_err} errors')
    return 1 if n_err else 0
