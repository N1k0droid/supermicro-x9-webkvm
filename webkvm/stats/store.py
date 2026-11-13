"""SQLite history: sensor samples and the event log (SEL).

Used by a single process; calls come from the asyncio loop through asyncio.to_thread,
so a lock serialises access to the connection.
"""
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS sensors (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    unit TEXT,
    type TEXT
);
CREATE TABLE IF NOT EXISTS samples (
    sensor_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    value REAL
);
CREATE INDEX IF NOT EXISTS samples_sensor_ts ON samples(sensor_id, ts);
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS sel (
    key TEXT PRIMARY KEY,
    record_id INTEGER,
    date TEXT,
    time TEXT,
    sensor TEXT,
    event TEXT,
    direction TEXT,
    first_seen INTEGER
);
"""


class Store:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript(SCHEMA)
        self._ids = {}

    def close(self):
        with self.lock:
            self.db.close()

    def _sensor_id(self, name, unit, stype):
        sid = self._ids.get(name)
        if sid is not None:
            return sid
        cur = self.db.execute('SELECT id FROM sensors WHERE name=?', (name,))
        row = cur.fetchone()
        if row:
            sid = row[0]
            self.db.execute('UPDATE sensors SET unit=?, type=? WHERE id=?', (unit, stype, sid))
        else:
            sid = self.db.execute('INSERT INTO sensors(name, unit, type) VALUES (?,?,?)',
                                  (name, unit, stype)).lastrowid
        self._ids[name] = sid
        return sid

    def add_samples(self, ts, samples):
        """samples: iterable of (name, unit, type, value)."""
        with self.lock, self.db:
            rows = [(self._sensor_id(n, u, t), int(ts), v) for n, u, t, v in samples]
            self.db.executemany('INSERT INTO samples(sensor_id, ts, value) VALUES (?,?,?)', rows)

    def list_sensors(self):
        with self.lock:
            cur = self.db.execute('SELECT name, unit, type FROM sensors ORDER BY name')
            return [{'name': n, 'unit': u, 'type': t} for n, u, t in cur]

    def history(self, name, start, end, points=300):
        """Series [(ts, avg)] reduced to about `points` points."""
        bucket = max(1, int((end - start) / max(points, 1)))
        with self.lock:
            cur = self.db.execute(
                'SELECT (s.ts / ?) * ? AS b, AVG(s.value), MIN(s.value), MAX(s.value) '
                'FROM samples s JOIN sensors n ON n.id = s.sensor_id '
                'WHERE n.name = ? AND s.ts BETWEEN ? AND ? GROUP BY b ORDER BY b',
                (bucket, bucket, name, int(start), int(end)))
            return [{'ts': b, 'avg': a, 'min': mn, 'max': mx} for b, a, mn, mx in cur]

    def add_sel(self, entries, now=None):
        """Insert new entries; return the ones never seen before."""
        now = int(now or time.time())
        new = []
        with self.lock, self.db:
            for e in entries:
                key = f"{e['id']}|{e['date']}|{e['time']}|{e['sensor']}|{e['event']}"
                cur = self.db.execute(
                    'INSERT OR IGNORE INTO sel(key, record_id, date, time, sensor, event, direction, '
                    'first_seen) VALUES (?,?,?,?,?,?,?,?)',
                    (key, e['id'], e['date'], e['time'], e['sensor'], e['event'],
                     e['direction'], now))
                if cur.rowcount:
                    new.append(e)
        return new

    def sel(self, limit=500):
        with self.lock:
            cur = self.db.execute(
                'SELECT record_id, date, time, sensor, event, direction, first_seen FROM sel '
                'ORDER BY first_seen DESC, record_id DESC LIMIT ?', (limit,))
            return [{'id': r, 'date': d, 'time': t, 'sensor': s, 'event': e, 'direction': dr,
                     'first_seen': fs} for r, d, t, s, e, dr, fs in cur]

    def purge(self, older_than):
        with self.lock, self.db:
            self.db.execute('DELETE FROM samples WHERE ts < ?', (int(older_than),))
