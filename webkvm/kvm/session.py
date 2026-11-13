"""Lifecycle of the KVM connection to the BMC.

- Opened by the first viewer (acquire) and closed after `idle_timeout` seconds without viewers,
  so the BMC console is not kept busy.
- Every connection signs in to the web interface again and gets a fresh token, so token
  expiry is never a problem.
- If the connection drops while viewers are connected, it reconnects with backoff; the viewers
  stay connected and see the last frame until a new one arrives.
"""
import logging
import threading
import time

from .aten import BmcLink, bmc_reader
from .framebuffer import SharedFramebuffer

log = logging.getLogger('webkvm.session')


class Lease:
    def __init__(self, session):
        self._session = session
        self._released = False

    def release(self):
        if not self._released:
            self._released = True
            self._session._release()


class KvmSession:
    def __init__(self, connect_fn, idle_timeout=300):
        """connect_fn() -> (host, port, user, pwd, cleanup), with cleanup() called on close."""
        self.connect_fn = connect_fn
        self.idle_timeout = idle_timeout
        self.fb = SharedFramebuffer()
        self.cond = threading.Condition()
        self.viewers = 0
        self.state = 'idle'                  # idle, connecting, connected, retrying
        self.last_error = None
        self.connected_since = None
        self._link = None
        self._thread = None
        self._stop = threading.Event()
        self._idle_deadline = None
        self._attempt = 0                    # grows at every failed attempt

    # ------------------------------------------------------------ viewer
    def acquire(self, timeout=45):
        with self.cond:
            self.viewers += 1
            self._idle_deadline = None
            closing = self._thread if (self._thread is not None and self._thread.is_alive()
                                       and self._stop.is_set()) else None
        if closing is not None:          # the previous session is closing: wait
            closing.join(20)
        with self.cond:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self.fb.ready.clear()
                self._attempt = 0
                self._thread = threading.Thread(target=self._run, daemon=True, name='kvm-session')
                self._thread.start()
            start_attempt = self._attempt
            end = time.monotonic() + timeout
            while not self.fb.ready.is_set():
                if self._attempt > start_attempt:
                    self.viewers -= 1
                    self._schedule_idle_locked()
                    raise RuntimeError(self.last_error or 'connection to the BMC failed')
                left = end - time.monotonic()
                if left <= 0:
                    self.viewers -= 1
                    self._schedule_idle_locked()
                    raise RuntimeError('timed out waiting for the first frame from the BMC')
                self.cond.wait(min(left, 1))
        return Lease(self)

    def _release(self):
        with self.cond:
            self.viewers = max(0, self.viewers - 1)
            self._schedule_idle_locked()

    def _schedule_idle_locked(self):
        if self.viewers == 0:
            self._idle_deadline = time.monotonic() + self.idle_timeout
            self.cond.notify_all()

    def send_key(self, keysym, down, xt_scancode=None):
        link = self._link
        if link is not None:
            try:
                link.send_key(keysym, down, xt_scancode)
            except OSError as e:
                log.debug('key not sent: %s', e)

    def send_mouse(self, buttons, x, y):
        link = self._link
        if link is not None:
            try:
                link.send_mouse(buttons, x, y)
            except OSError as e:
                log.debug('mouse not sent: %s', e)

    def disconnect(self):
        """Close the BMC connection now (viewers are disconnected)."""
        with self.cond:
            self._stop.set()
            self.cond.notify_all()
        link = self._link
        if link:
            link.close()
        self.fb.close_all()

    def status(self):
        fb = self.fb
        return {
            'state': self.state,
            'viewers': self.viewers,
            'width': fb.width if fb.ready.is_set() else None,
            'height': fb.height if fb.ready.is_set() else None,
            'last_error': self.last_error,
            'connected_since': self.connected_since,
            'signal': fb.signal,
        }

    # ------------------------------------------------------------ worker
    def _idle_expired_locked(self):
        return self.viewers == 0 and self._idle_deadline is not None and \
            time.monotonic() >= self._idle_deadline

    def _watchdog(self, link):
        """Close the link when the idle time expires or a stop is requested."""
        while True:
            with self.cond:
                if self._link is not link:
                    return
                if self._stop.is_set() or self._idle_expired_locked():
                    self._stop.set()
                    break
                self.cond.wait(2)
        log.info('no viewers for %ds: closing the KVM session', self.idle_timeout)
        link.close()

    def _run(self):
        backoff = 2
        while not self._stop.is_set():
            cleanup = None
            link = None
            try:
                with self.cond:
                    self.state = 'connecting'
                host, port, user, pwd, cleanup = self.connect_fn()
                log.info('KVM connection to %s:%d', host, port)
                link = BmcLink(host, port, user, pwd)
                with self.cond:
                    self._link = link
                    self.state = 'connected'
                    self.connected_since = time.time()
                    self.last_error = None
                    self.cond.notify_all()
                threading.Thread(target=self._watchdog, args=(link,), daemon=True).start()
                backoff = 2
                # wake up waiting viewers when the first frame arrives
                waiter = threading.Thread(target=self._notify_ready, daemon=True)
                waiter.start()
                bmc_reader(link, self.fb, self._stop)
            except Exception as e:
                if not self._stop.is_set():
                    log.warning('KVM session: %s', e)
                    with self.cond:
                        self.last_error = str(e)
                        self._attempt += 1
                        self.cond.notify_all()
            finally:
                with self.cond:
                    self._link = None
                    self.connected_since = None
                    self.cond.notify_all()
                if link:
                    link.close()
                if cleanup:
                    try:
                        cleanup()
                    except Exception as e:
                        log.debug('web session cleanup: %s', e)
            with self.cond:
                if self._stop.is_set() or self.viewers == 0:
                    break
                self.state = 'retrying'
                self.cond.wait(backoff)
                if self.viewers == 0:
                    break
            backoff = min(backoff * 2, 60)
        with self.cond:
            self.state = 'idle'
            self.fb.ready.clear()
            self.cond.notify_all()
        log.info('KVM session closed')

    def _notify_ready(self):
        if self.fb.ready.wait(60):
            with self.cond:
                self.cond.notify_all()
