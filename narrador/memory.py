"""Small private SQLite store for current stream context and join greetings."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from platformdirs import user_data_path


class MemoryStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or (Path(user_data_path('TikTokNarradorLive')) / 'memory.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stream_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stream_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL UNIQUE,
                    updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS joins (
                    room TEXT NOT NULL, viewer TEXT NOT NULL, name TEXT NOT NULL,
                    created TEXT NOT NULL, greeted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (room, viewer)
                );
                CREATE INDEX IF NOT EXISTS joins_pending ON joins(room, greeted, created);
            ''')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=5000')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def set_activity(self, text: str):
        text = ' '.join(text.split())[:1000]
        with self._connect() as db:
            db.execute('INSERT INTO stream_state(key,value) VALUES(\'activity\',?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (text,))

    def add_fact(self, text: str):
        text = ' '.join(text.split())[:300]
        if not text:
            return
        with self._connect() as db:
            db.execute('INSERT INTO stream_facts(fact,updated) VALUES(?,?) '
                       'ON CONFLICT(fact) DO UPDATE SET updated=excluded.updated', (text, self._now()))

    def set_facts(self, text: str):
        facts = [line.strip()[:300] for line in text.splitlines() if line.strip()][:50]
        with self._connect() as db:
            db.execute('DELETE FROM stream_facts')
            db.executemany('INSERT INTO stream_facts(fact,updated) VALUES(?,?)',
                           [(fact, self._now()) for fact in facts])

    def context(self):
        with self._connect() as db:
            values = dict(db.execute('SELECT key,value FROM stream_state'))
            facts = [row[0] for row in db.execute('SELECT fact FROM stream_facts ORDER BY updated DESC LIMIT 10')]
        return {'activity': values.get('activity', ''), 'facts': '\n'.join(facts)}

    def enqueue_join(self, room: str, viewer: str, name: str):
        with self._connect() as db:
            db.execute('INSERT OR IGNORE INTO joins(room,viewer,name,created,greeted) VALUES(?,?,?,?,0)',
                       (room, viewer, name[:60], self._now()))

    def pending_joins(self, room: str):
        with self._connect() as db:
            return db.execute('SELECT viewer,name FROM joins WHERE room=? AND greeted=0 ORDER BY created',
                              (room,)).fetchall()

    def next_pending_join(self, room: str):
        with self._connect() as db:
            count = db.execute('SELECT COUNT(*) FROM joins WHERE room=? AND greeted=0', (room,)).fetchone()[0]
            row = db.execute('SELECT viewer,name FROM joins WHERE room=? AND greeted=0 ORDER BY created LIMIT 1',
                             (room,)).fetchone()
            return count, row

    def mark_greeted(self, room: str, viewer: str):
        with self._connect() as db:
            db.execute('UPDATE joins SET greeted=1 WHERE room=? AND viewer=?', (room, viewer))

    def keep_room_only(self, room: str):
        """Discard earlier room queues once a new TikTok room ID is confirmed."""
        with self._connect() as db:
            db.execute('DELETE FROM joins WHERE room != ?', (room,))

    def prune_greeted(self, days: int = 90):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as db:
            db.execute('DELETE FROM joins WHERE greeted=1 AND created < ?', (cutoff,))

    def clear(self):
        with self._connect() as db:
            db.execute('DELETE FROM stream_state')
            db.execute('DELETE FROM stream_facts')
            db.execute('DELETE FROM joins')
        for suffix in ('-wal', '-shm'):
            try:
                Path(str(self.path) + suffix).unlink()
            except OSError:
                pass
        try:
            self.path.unlink()
        except OSError:
            pass

    def clear_context(self):
        with self._connect() as db:
            db.execute('DELETE FROM stream_state')
            db.execute('DELETE FROM stream_facts')

    def export(self, path):
        path = Path(path)
        path.write_text(json.dumps(self.context(), ensure_ascii=False, indent=2), encoding='utf-8')
