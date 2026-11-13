"""Framebuffer shared between the BMC thread and the viewers, with a queue of changed
rectangles per viewer (several viewers connected at once all see every update)."""
import threading


class ViewerQueue:
    """State of one viewer: pending changed rectangles and pending update request."""

    def __init__(self):
        self.dirty = []
        self.request = None      # None, 'inc' or 'full'
        self.closed = False


class SharedFramebuffer:
    MAX_DIRTY = 2000             # beyond this the queue collapses into one full rectangle

    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.width = 0
        self.height = 0
        self.buf = bytearray()           # BGRX 32 bpp, w*h*4 bytes
        self.ready = threading.Event()   # set after the first full frame
        self.bmc_refresh = threading.Event()   # ask the BMC for a full frame
        self.signal = True               # False = the BMC gets no video from the server
        self.generation = 0              # grows at every resize
        self.queues = set()

    # ------------------------------------------------------------ viewer
    def register(self):
        q = ViewerQueue()
        with self.lock:
            self.queues.add(q)
        return q

    def unregister(self, q):
        with self.cond:
            q.closed = True
            self.queues.discard(q)
            self.cond.notify_all()

    def post_request(self, q, incremental):
        with self.cond:
            if not incremental:
                q.request = 'full'
                # a viewer asking for everything (connect, "Reconnect") also restarts the
                # BMC from a full frame: this clears any leftovers
                self.bmc_refresh.set()
            elif q.request is None:
                q.request = 'inc'
            self.cond.notify_all()

    def close_all(self):
        """Disconnect all viewers (their writers exit and close the socket)."""
        with self.cond:
            for q in self.queues:
                q.closed = True
            self.cond.notify_all()

    # ------------------------------------------------------------- BMC
    def resize(self, w, h):
        with self.cond:
            self.width, self.height = w, h
            self.buf = bytearray(w * h * 4)
            self.generation += 1
            for q in self.queues:
                q.dirty = [(0, 0, w, h)]
            self.cond.notify_all()

    def set_signal(self, present):
        """Without a signal the screen turns black (no leftovers from the previous screen)."""
        with self.cond:
            self.signal = present
            if not present and self.width:
                self.buf = bytearray(self.width * self.height * 4)
                for q in self.queues:
                    q.dirty = [(0, 0, self.width, self.height)]
            self.cond.notify_all()

    def apply_rect(self, x, y, w, h, bgrx):
        with self.cond:
            fw, fh = self.width, self.height
            if x >= fw or y >= fh:
                return
            n = min(w, fw - x) * 4
            for row in range(min(h, fh - y)):
                src = row * w * 4
                dst = ((y + row) * fw + x) * 4
                self.buf[dst:dst + n] = bgrx[src:src + n]
            # tiles sit on a 16x16 grid rounded up: the rectangle must be clipped
            # at the edges or viewers reject it
            cw, ch = min(w, fw - x), min(h, fh - y)
            for q in self.queues:
                if len(q.dirty) < self.MAX_DIRTY:
                    q.dirty.append((x, y, cw, ch))
                elif len(q.dirty) == self.MAX_DIRTY:
                    q.dirty = [(0, 0, fw, fh)]
            self.cond.notify_all()

    # ---------------------------------------------------------- reading
    def _rect_bytes_locked(self, x, y, w, h):
        fw = self.width
        if x == 0 and w == fw:
            return bytes(self.buf[y * fw * 4:(y + h) * fw * 4])
        out = bytearray(w * h * 4)
        rw = w * 4
        for row in range(h):
            src = ((y + row) * fw + x) * 4
            out[row * rw:(row + 1) * rw] = self.buf[src:src + rw]
        return bytes(out)

    def snapshot_full(self):
        with self.lock:
            return self.width, self.height, bytes(self.buf)

    def take_update(self, q, sent_size, timeout):
        """Wait until the viewer has a pending request and something to send.
        Returns (kind, w, h, rects) with kind 'full' or 'inc' and rects = [(x,y,w,h,bytes)],
        or None on timeout/close."""
        with self.cond:
            def ready():
                if q.closed:
                    return True
                if q.request is None or not self.ready.is_set():
                    return False
                return q.request == 'full' or bool(q.dirty) or \
                    (self.width, self.height) != sent_size
            if not self.cond.wait_for(ready, timeout):
                return None
            if q.closed:
                return None
            size = (self.width, self.height)
            if q.request == 'full' or size != sent_size:
                kind = 'full'
                rects = [(0, 0, self.width, self.height,
                          self._rect_bytes_locked(0, 0, self.width, self.height))]
            else:
                kind = 'inc'
                rects = [(x, y, w, h, self._rect_bytes_locked(x, y, w, h))
                         for x, y, w, h in q.dirty]
            q.dirty = []
            q.request = None
            return kind, self.width, self.height, rects
