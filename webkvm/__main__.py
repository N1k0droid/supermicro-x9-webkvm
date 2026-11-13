"""Usage: python -m webkvm [serve | check [ipmi|kvm|kvm-watch [seconds]] | hash-password [password] | gen-token]"""
import getpass
import logging
import os
import sys

from . import __version__
from .config import Config, new_token


def cmd_hash_password(args):
    from .web.auth import hash_password
    if args:
        pw = args[0]
    else:
        pw = getpass.getpass('Password: ')
        if pw != getpass.getpass('Repeat: '):
            sys.exit('passwords do not match')
    print(hash_password(pw))


def cmd_gen_token():
    print(new_token())


def cmd_serve():
    from aiohttp import web

    from .bmc.ipmi import make_ipmi_client
    from .bmc.web_login import make_kvm_connector
    from .kvm.rfb_server import RfbServer
    from .kvm.session import KvmSession
    from .stats.collector import Collector
    from .stats.store import Store
    from .web.app import build_app, make_context

    cfg = Config.from_env()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format='%(asctime)s %(levelname)-7s %(name)s: %(message)s')
    log = logging.getLogger('webkvm')
    errors, warnings = cfg.validate()
    for w in warnings:
        log.warning(w)
    if errors:
        for e in errors:
            log.error(e)
        sys.exit(2)
    log.info('webkvm %s - BMC %s', __version__, cfg.bmc_host)

    store = Store(os.path.join(cfg.data_dir, 'webkvm.sqlite'))
    kvm = KvmSession(make_kvm_connector(cfg), idle_timeout=cfg.kvm_idle_timeout)
    RfbServer(kvm, '127.0.0.1', cfg.kvm_internal_port).start()
    if cfg.vnc_enable:
        RfbServer(kvm, cfg.vnc_listen, cfg.vnc_port, password=cfg.vnc_password).start()

    novnc_dir = os.environ.get('NOVNC_DIR') or os.path.join(os.path.dirname(__file__), 'web',
                                                             'static', 'novnc')

    ipmi = make_ipmi_client(cfg)
    log.info('statistics via %s', ipmi.describe())
    collector = Collector(cfg, ipmi, store)
    app = build_app(make_context(cfg, collector, store, ipmi, kvm, __version__, novnc_dir))

    async def start_background(app):
        collector.start()
        log.info('web gateway on http://%s:%d', cfg.listen, cfg.port)

    async def stop_background(app):
        await collector.stop()

    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    try:
        # run_app handles Ctrl+C: it closes connections first (on_shutdown), then the tasks,
        # without cancellation tracebacks
        web.run_app(app, host=cfg.listen, port=cfg.port, access_log=None, print=None)
    finally:
        kvm.disconnect()
        store.close()
        log.info('gateway stopped')


def main(argv):
    if not argv or argv[0] == 'serve':
        cmd_serve()
    elif argv[0] == 'hash-password':
        cmd_hash_password(argv[1:])
    elif argv[0] == 'gen-token':
        cmd_gen_token()
    elif argv[0] == 'check':
        from .check import main as check_main
        sys.exit(check_main(argv[1:]))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
