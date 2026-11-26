"""Thin SQLite store: clip sources, render jobs, finished reels.

Threading: the HTTP server is threaded and job finalisers run on their own
threads, so **every** call into this class is serialised on one RLock. Sharing a
single ``sqlite3.Connection`` without that lock segfaults inside libsqlite3
(exit 139, "Python quit unexpectedly") within minutes — CPython releases the GIL
around ``sqlite3_step``, so two threads prepare/step the same handle and corrupt
its heap.
"""
import os
import sqlite3
import threading
import time


class Store(object):
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self._init()

    def _init(self):
        with self._lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER DEFAULT 0,
                status TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL,
                expected INTEGER DEFAULT 0,
                downloaded INTEGER DEFAULT 0,
                limit_n INTEGER DEFAULT 0,
                quality TEXT DEFAULT '',
                message TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT UNIQUE NOT NULL,
                clip TEXT DEFAULT '',
                line TEXT DEFAULT '',
                theme TEXT DEFAULT '',
                profile TEXT DEFAULT '',
                duration REAL DEFAULT 0,
                match_json TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            """)
            self.db.commit()

    # ---- sources ----
    def add_source(self, name, path):
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO sources (name, path, created_at) "
                            "VALUES (?,?,?)", (name, path, time.time()))
            self.db.execute("UPDATE sources SET path=? WHERE name=?", (path, name))
            self.db.commit()
        return self.source(name)

    def source(self, name):
        with self._lock:
            r = self.db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def source_by_id(self, sid):
        with self._lock:
            r = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def sources(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM sources ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def remove_source(self, sid):
        with self._lock:
            self.db.execute("DELETE FROM sources WHERE id=?", (sid,))
            self.db.commit()

    # ---- jobs ----
    def new_job(self, source_id, expected, limit_n=0, quality="render"):
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO jobs (source_id, status, started_at, expected, limit_n, quality) "
                "VALUES (?,?,?,?,?,?)",
                (source_id, "running", time.time(), expected, limit_n, quality))
            self.db.commit()
            return cur.lastrowid

    def job(self, jid):
        with self._lock:
            r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None

    def active_job(self, source_id=0):
        with self._lock:
            r = self.db.execute(
                "SELECT * FROM jobs WHERE status='running' AND (?=0 OR source_id=?) "
                "ORDER BY id DESC LIMIT 1", (source_id, source_id)).fetchone()
        return dict(r) if r else None

    def running_jobs(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        return [dict(r) for r in rows]

    def update_job(self, jid, **kw):
        if not kw:
            return
        keys = ",".join("%s=?" % k for k in kw)
        with self._lock:
            self.db.execute("UPDATE jobs SET %s WHERE id=?" % keys,
                            tuple(kw.values()) + (jid,))
            self.db.commit()

    # ---- renders ----
    def add_render(self, file, clip="", line="", theme="", profile="", duration=0.0,
                   match_json=""):
        with self._lock:
            self.db.execute(
                "INSERT INTO renders (file, clip, line, theme, profile, duration, match_json,"
                " created_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(file) DO UPDATE SET clip=excluded.clip, line=excluded.line, "
                "theme=excluded.theme, profile=excluded.profile, duration=excluded.duration, "
                "match_json=excluded.match_json, created_at=excluded.created_at",
                (file, clip, line, theme, profile, duration, match_json, time.time()))
            self.db.commit()

    def renders(self, limit=500):
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM renders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def render(self, file):
        with self._lock:
            r = self.db.execute("SELECT * FROM renders WHERE file=?", (file,)).fetchone()
        return dict(r) if r else None

    def remove_render(self, file):
        with self._lock:
            self.db.execute("DELETE FROM renders WHERE file=?", (file,))
            self.db.commit()
