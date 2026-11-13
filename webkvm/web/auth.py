"""Gateway authentication: password hash (scrypt), in-memory sessions, rate limit."""
import base64
import hashlib
import hmac
import secrets
import time

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()   # noqa: E731
    return f'scrypt${_N}${_R}${_P}${b64(salt)}${b64(dk)}'


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, dk = stored.split('$')
        if algo != 'scrypt':
            return False
        salt, dk = base64.b64decode(salt), base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p),
                             dklen=len(dk), maxmem=256 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, dk)


def check_credentials(cfg, user, password):
    user_ok = hmac.compare_digest(user.encode(), cfg.web_user.encode())
    if cfg.web_password_hash:
        pw_ok = verify_password(password, cfg.web_password_hash)
    else:
        pw_ok = hmac.compare_digest(password.encode(), cfg.web_password.encode())
    return user_ok and pw_ok


class Sessions:
    def __init__(self, ttl_seconds):
        self.ttl = ttl_seconds
        self._s = {}

    def create(self, user):
        token = secrets.token_urlsafe(32)
        self._s[token] = {'user': user, 'csrf': secrets.token_urlsafe(24),
                          'expires': time.time() + self.ttl}
        return token, self._s[token]

    def get(self, token):
        if not token:
            return None
        s = self._s.get(token)
        if s is None:
            return None
        if s['expires'] < time.time():
            self._s.pop(token, None)
            return None
        return s

    def delete(self, token):
        self._s.pop(token, None)

    def purge(self):
        now = time.time()
        for k in [k for k, v in self._s.items() if v['expires'] < now]:
            self._s.pop(k, None)


class RateLimiter:
    """At most `limit` failed attempts per IP within `window` seconds."""

    def __init__(self, limit=5, window=300):
        self.limit, self.window = limit, window
        self._fails = {}

    def blocked(self, ip):
        now = time.time()
        fails = [t for t in self._fails.get(ip, []) if now - t < self.window]
        self._fails[ip] = fails
        return len(fails) >= self.limit

    def fail(self, ip):
        self._fails.setdefault(ip, []).append(time.time())

    def reset(self, ip):
        self._fails.pop(ip, None)
