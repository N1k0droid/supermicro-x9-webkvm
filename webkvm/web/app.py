"""aiohttp web app: gateway login, dashboard, REST API, /metrics and the noVNC WebSocket bridge."""
import asyncio
import hmac
import logging
import os
import time

from aiohttp import WSCloseCode, WSMsgType, web

from ..bmc.ipmi import POWER_ACTIONS, IpmiError
from ..stats import prometheus
from .auth import RateLimiter, Sessions, check_credentials

log = logging.getLogger('webkvm.web')
audit = logging.getLogger('webkvm.audit')

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
COOKIE = 'webkvm_session'

CTX = web.AppKey('ctx', dict)
PURGE_TASK = web.AppKey('purge_task', asyncio.Task)


# ------------------------------------------------------------------ helper
def _ctx(request):
    return request.app[CTX]


def client_ip(request):
    cfg = _ctx(request)['cfg']
    if cfg.trusted_proxy:
        fwd = request.headers.get('X-Forwarded-For')
        if fwd:
            return fwd.split(',')[0].strip()
    return request.remote or '?'


def is_https(request):
    cfg = _ctx(request)['cfg']
    if cfg.trusted_proxy and request.headers.get('X-Forwarded-Proto') == 'https':
        return True
    return request.secure


def current_session(request):
    return _ctx(request)['sessions'].get(request.cookies.get(COOKIE))


def bearer_ok(request):
    token = _ctx(request)['cfg'].metrics_token
    if not token:
        return False
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return hmac.compare_digest(auth[7:].strip().encode(), token.encode())
    return False


def require_session(handler):
    async def wrapper(request):
        if current_session(request) is None:
            if request.path.startswith('/api/'):
                raise web.HTTPUnauthorized(text='{"error":"login required","code":"login_required"}',
                                           content_type='application/json')
            raise web.HTTPFound('/login')
        return await handler(request)
    return wrapper


def require_reader(handler):
    """Web session, or bearer token (Prometheus/Zabbix), or public metrics."""
    async def wrapper(request):
        cfg = _ctx(request)['cfg']
        if current_session(request) or bearer_ok(request) or \
                (cfg.metrics_public and not cfg.metrics_token):
            return await handler(request)
        raise web.HTTPUnauthorized(text='{"error":"token or login required","code":"auth_required"}',
                                   content_type='application/json',
                                   headers={'WWW-Authenticate': 'Bearer'})
    return wrapper


def require_csrf(handler):
    async def wrapper(request):
        s = current_session(request)
        if s is None:
            raise web.HTTPUnauthorized(text='{"error":"login required","code":"login_required"}',
                                       content_type='application/json')
        token = request.headers.get('X-CSRF-Token', '')
        if not hmac.compare_digest(token.encode(), s['csrf'].encode()):
            raise web.HTTPForbidden(text='{"error":"invalid CSRF token","code":"csrf"}',
                                    content_type='application/json')
        return await handler(request)
    return wrapper


def same_origin(request):
    origin = request.headers.get('Origin')
    if not origin:
        return True
    host = request.headers.get('X-Forwarded-Host') if _ctx(request)['cfg'].trusted_proxy else None
    host = host or request.host
    return origin.split('://', 1)[-1] == host


@web.middleware
async def security_headers(request, handler):
    resp = await handler(request)
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    host = request.host
    resp.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
        f"connect-src 'self' ws://{host} wss://{host}; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'")
    if request.path.startswith('/api/') or request.path == '/metrics':
        resp.headers.setdefault('Cache-Control', 'no-store')
    elif request.path.startswith('/static/'):
        # after a container update the browser must fetch the new files
        resp.headers.setdefault('Cache-Control', 'no-cache')
    return resp


def _page(name):
    return web.FileResponse(os.path.join(STATIC_DIR, name))


# ------------------------------------------------------------------ pages
async def login_page(request):
    if current_session(request):
        raise web.HTTPFound('/')
    return _page('login.html')


async def login_post(request):
    ctx = _ctx(request)
    ip = client_ip(request)
    if ctx['ratelimit'].blocked(ip):
        audit.warning('login blocked (too many attempts) from %s', ip)
        raise web.HTTPFound('/login?err=blocked')
    data = await request.post()
    user, password = str(data.get('user', '')), str(data.get('password', ''))
    ok = await asyncio.to_thread(check_credentials, ctx['cfg'], user, password)
    if not ok:
        ctx['ratelimit'].fail(ip)
        audit.warning('failed login for "%s" from %s', user[:64], ip)
        raise web.HTTPFound('/login?err=1')
    ctx['ratelimit'].reset(ip)
    token, _s = ctx['sessions'].create(user)
    audit.info('login of "%s" from %s', user, ip)
    resp = web.HTTPFound('/')
    resp.set_cookie(COOKIE, token, httponly=True, samesite='Strict', secure=is_https(request),
                    max_age=ctx['cfg'].session_hours * 3600, path='/')
    raise resp


async def logout_post(request):
    token = request.cookies.get(COOKIE)
    s = current_session(request)
    if s:
        audit.info('logout of "%s" from %s', s['user'], client_ip(request))
    _ctx(request)['sessions'].delete(token)
    resp = web.HTTPFound('/login')
    resp.del_cookie(COOKIE, path='/')
    raise resp


@require_session
async def index_page(request):
    return _page('index.html')


@require_session
async def kvm_page(request):
    return _page('kvm.html')


async def healthz(request):
    col = _ctx(request)['collector']
    return web.json_response({'ok': True, 'bmc_up': col.up, 'version': _ctx(request)['version']})


# ------------------------------------------------------------------ API
@require_session
async def api_session(request):
    ctx = _ctx(request)
    s = current_session(request)
    cfg = ctx['cfg']
    return web.json_response({
        'user': s['user'],
        'csrf': s['csrf'],
        'power_control': cfg.power_control,
        'vnc': {'enabled': cfg.vnc_enable, 'port': cfg.vnc_port},
        'bmc_host': cfg.bmc_host,
        'version': ctx['version'],
    })


@require_reader
async def api_sensors(request):
    return web.json_response(_ctx(request)['collector'].snapshot())


@require_reader
async def api_history(request):
    ctx = _ctx(request)
    name = request.query.get('sensor')
    if not name:
        return web.json_response(await asyncio.to_thread(ctx['store'].list_sensors))
    now = time.time()
    try:
        rng = int(request.query.get('range', 3600))
        end = float(request.query.get('to', now))
        start = float(request.query.get('from', end - rng))
        points = min(int(request.query.get('points', 300)), 2000)
    except ValueError:
        raise web.HTTPBadRequest(text='invalid parameters')
    rows = await asyncio.to_thread(ctx['store'].history, name, start, end, points)
    return web.json_response({'sensor': name, 'from': start, 'to': end, 'points': rows})


@require_reader
async def api_sel(request):
    ctx = _ctx(request)
    rows = await asyncio.to_thread(ctx['store'].sel, 1000)
    return web.json_response({'count': ctx['collector'].sel_total, 'entries': rows})


@require_reader
async def api_info(request):
    ctx = _ctx(request)
    return web.json_response({'bmc': ctx['collector'].info, 'bmc_host': ctx['cfg'].bmc_host,
                              'version': ctx['version']})


@require_session
async def api_kvm(request):
    return web.json_response(_ctx(request)['kvm'].status())


@require_csrf
async def api_kvm_disconnect(request):
    ctx = _ctx(request)
    audit.info('KVM disconnect requested by "%s" (%s)', current_session(request)['user'],
               client_ip(request))
    await asyncio.to_thread(ctx['kvm'].disconnect)
    return web.json_response({'ok': True})


@require_session
async def api_power_get(request):
    col = _ctx(request)['collector']
    return web.json_response({'power_on': col.chassis.get('power_on'), 'chassis': col.chassis,
                              'up': col.up})


@require_csrf
async def api_power_post(request):
    ctx = _ctx(request)
    if not ctx['cfg'].power_control:
        raise web.HTTPForbidden(text='{"error":"power control disabled","code":"power_disabled"}',
                                content_type='application/json')
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text='{"error":"invalid JSON","code":"bad_json"}', content_type='application/json')
    action = body.get('action')
    if action not in POWER_ACTIONS:
        raise web.HTTPBadRequest(text='{"error":"invalid action","code":"bad_action"}', content_type='application/json')
    user = current_session(request)['user']
    audit.warning('POWER: "%s" requested by "%s" (%s)', action, user, client_ip(request))
    try:
        out = await ctx['ipmi'].power(action)
    except IpmiError as e:
        audit.warning('POWER: "%s" failed: %s', action, e)
        return web.json_response({'ok': False, 'error': str(e)}, status=502)
    ctx['collector'].refresh_soon()
    return web.json_response({'ok': True, 'result': out})


@require_reader
async def metrics(request):
    ctx = _ctx(request)
    text = prometheus.render(ctx['collector'].snapshot(), ctx['kvm'].status(), ctx['collector'].info)
    return web.Response(text=text, content_type='text/plain', charset='utf-8',
                        headers={'X-Prometheus-Format': '0.0.4'})


# ------------------------------------------------------------- WebSocket KVM
async def ws_kvm(request):
    s = current_session(request)
    if s is None:
        raise web.HTTPUnauthorized()
    if not same_origin(request):
        audit.warning('KVM WebSocket refused: Origin %s from %s', request.headers.get('Origin'),
                      client_ip(request))
        raise web.HTTPForbidden()
    ctx = _ctx(request)
    ws = web.WebSocketResponse(protocols=('binary',), max_msg_size=0, heartbeat=30,
                               compress=False)
    await ws.prepare(request)
    ctx['websockets'].add(ws)
    audit.info('KVM opened by "%s" (%s)', s['user'], client_ip(request))
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', ctx['cfg'].kvm_internal_port)
    except OSError as e:
        log.error('internal VNC server unreachable: %s', e)
        await ws.close(code=1011, message=b'internal VNC server unavailable')
        return ws

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(262144)
                if not data:
                    break
                await ws.send_bytes(data)
        except (ConnectionError, RuntimeError):
            pass
        finally:
            await ws.close()

    pump = asyncio.create_task(tcp_to_ws())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type == WSMsgType.ERROR:
                break
    except (ConnectionError, RuntimeError):
        pass
    finally:
        pump.cancel()
        writer.close()
        ctx['websockets'].discard(ws)
        audit.info('KVM closed by "%s" (%s)', s['user'], client_ip(request))
    return ws


# ------------------------------------------------------------------- app
def build_app(ctx):
    app = web.Application(middlewares=[security_headers], client_max_size=64 * 1024)
    app[CTX] = ctx
    r = app.router
    r.add_get('/login', login_page)
    r.add_post('/login', login_post)
    r.add_post('/logout', logout_post)
    r.add_get('/', index_page)
    r.add_get('/kvm', kvm_page)
    r.add_get('/healthz', healthz)
    r.add_get('/metrics', metrics)
    r.add_get('/ws/kvm', ws_kvm)
    r.add_get('/api/v1/session', api_session)
    r.add_get('/api/v1/sensors', api_sensors)
    r.add_get('/api/v1/history', api_history)
    r.add_get('/api/v1/sel', api_sel)
    r.add_get('/api/v1/info', api_info)
    r.add_get('/api/v1/kvm', api_kvm)
    r.add_post('/api/v1/kvm/disconnect', api_kvm_disconnect)
    r.add_get('/api/v1/power', api_power_get)
    r.add_post('/api/v1/power', api_power_post)
    r.add_static('/static/', STATIC_DIR, show_index=False)
    novnc = ctx.get('novnc_dir')
    if novnc and os.path.isdir(novnc):
        r.add_static('/novnc/', novnc, show_index=False)
    else:
        log.warning('noVNC not found (%s): the KVM page will not work', novnc)

    async def purge_sessions(app):
        async def loop():
            while True:
                await asyncio.sleep(600)
                ctx['sessions'].purge()
        app[PURGE_TASK] = asyncio.create_task(loop())
    app.on_startup.append(purge_sessions)

    async def close_websockets(app):
        for ws in list(ctx['websockets']):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b'gateway in chiusura')
    app.on_shutdown.append(close_websockets)
    return app


def make_context(cfg, collector, store, ipmi, kvm, version, novnc_dir):
    return {
        'cfg': cfg, 'collector': collector, 'store': store, 'ipmi': ipmi, 'kvm': kvm,
        'version': version, 'novnc_dir': novnc_dir,
        'sessions': Sessions(cfg.session_hours * 3600),
        'websockets': set(),
        'ratelimit': RateLimiter(),
    }
