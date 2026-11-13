"""Sign in to the BMC (ATEN) web interface to obtain the KVM console token.

Flow (see docs/PROTOCOL.md):
  1. POST /cgi/login.cgi  name=<user>&pwd=<password>&check=00          -> SID cookie
  2. GET  /cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk (with SID)  -> JNLP descriptor
  3. from the JNLP <argument> values: host, session user/password (token), KVM port
  4. when the session ends: GET /cgi/logout.cgi

Firmware 3.19 on X9 boards wants name/pwd in plain text; other firmware wants them base64
encoded, so if the first attempt returns no valid SID the other form is tried.
"""
import base64
import http.cookies
import logging
import urllib.error
import urllib.parse
import urllib.request

from ..kvm.aten import read_jnlp_text

log = logging.getLogger('webkvm.weblogin')


class BmcLoginError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BmcWebSession:
    def __init__(self, host, user, password, scheme='http', port=None, timeout=15):
        self.base = f'{scheme}://{host}' + (f':{port}' if port else '')
        self.user = user
        self.password = password
        self.timeout = timeout
        self.sid = None
        self.variant = None          # 'base64' or 'plain': which login form worked
        self.descriptor_variant = None
        self.attempts = []
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, path, data=None, cookies=None, referer=None):
        req = urllib.request.Request(self.base + path, data=data)
        req.add_header('User-Agent', 'Mozilla/5.0 (webkvm)')
        jar = dict(cookies or {})
        if self.sid:
            jar['SID'] = self.sid
        if jar:
            req.add_header('Cookie', '; '.join(f'{k}={v}' for k, v in jar.items()))
        if referer:
            req.add_header('Referer', self.base + referer)
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:   # redirects that are not followed end up here
            resp = e
        body = resp.read()
        return resp, body

    @staticmethod
    def _extract_sid(resp):
        for header in resp.headers.get_all('Set-Cookie') or []:
            c = http.cookies.SimpleCookie()
            try:
                c.load(header)
            except http.cookies.CookieError:
                continue
            if 'SID' in c and c['SID'].value:
                return c['SID'].value
        return None

    def login(self):
        # X9 / ATEN 3.19: plain fields (verified); other firmware wants base64
        attempts = [
            ('plain', self.user, self.password),
            ('base64', base64.b64encode(self.user.encode()).decode(),
             base64.b64encode(self.password.encode()).decode()),
        ]
        for variant, name, pwd in attempts:
            data = urllib.parse.urlencode({'name': name, 'pwd': pwd, 'check': '00'}).encode()
            try:
                resp, _body = self._request('/cgi/login.cgi', data)
            except OSError as e:
                raise BmcLoginError(f'BMC unreachable at {self.base}: {e}') from e
            sid = self._extract_sid(resp)
            if sid:
                self.sid = sid
                self.variant = variant
                return sid
        raise BmcLoginError('BMC web login refused (wrong credentials or too many open sessions)')

    # Cookies the web interface sets from JavaScript: some ATEN CGIs fail with HTTP 500
    # without them (e.g. the language, used to pick page templates).
    BROWSER_COOKIES = {'langSetFlag': '0', 'language': 'English',
                       'mainpage': 'remote', 'subpage': 'man_ikvm'}
    KVM_PAGE = '/cgi/url_redirect.cgi?url_name=man_ikvm'
    KVM_JNLP = '/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk'
    # (name, browser cookies, Referer, visit the "Console Redirection" page first)
    DESCRIPTOR_VARIANTS = [
        ('cookie+referer', True, True, False),
        ('cookie+referer+page', True, True, True),
        ('cookie', True, False, False),
        ('referer', False, True, False),
        ('bare', False, False, False),
    ]

    def kvm_descriptor(self, variants=None):
        """Return the <argument> list of the KVM viewer JNLP. Tries the request variants
        until one returns the descriptor; self.descriptor_variant tells which one."""
        if not self.sid:
            self.login()
        self.attempts = []
        for name, use_cookies, use_referer, visit in variants or self.DESCRIPTOR_VARIANTS:
            cookies = self.BROWSER_COOKIES if use_cookies else None
            referer = self.KVM_PAGE if use_referer else None
            if visit:
                self._request(self.KVM_PAGE, cookies=cookies,
                              referer='/cgi/url_redirect.cgi?url_name=mainmenu')
            resp, body = self._request(self.KVM_JNLP, cookies=cookies, referer=referer)
            args = read_jnlp_text(body.decode('utf-8', 'replace'))
            self.attempts.append((name, resp.status, len(body), len(args)))
            if len(args) >= 5:
                self.descriptor_variant = name
                return args
        tried = ', '.join(f'{n}: HTTP {s}' for n, s, _l, _a in self.attempts)
        raise BmcLoginError(f'the BMC did not return the KVM descriptor ({tried})')

    def logout(self):
        if not self.sid:
            return
        try:
            self._request('/cgi/logout.cgi')
        except OSError as e:
            log.debug('web logout failed: %s', e)
        finally:
            self.sid = None


def make_kvm_connector(cfg):
    """connect_fn for KvmSession: web login -> token -> KVM connection parameters."""
    def connect():
        web = BmcWebSession(cfg.bmc_host, cfg.bmc_user, cfg.bmc_password,
                            scheme=cfg.bmc_web_scheme, port=cfg.bmc_web_port)
        web.login()
        try:
            args = web.kvm_descriptor()
        except Exception:
            web.logout()
            raise
        jnlp_host, user, pwd, _name, port = args[:5]
        if jnlp_host != cfg.bmc_host:
            log.debug('the JNLP names host %s, using the configured %s', jnlp_host, cfg.bmc_host)
        port = cfg.bmc_kvm_port or int(port)
        return cfg.bmc_host, port, user, pwd, web.logout
    return connect
