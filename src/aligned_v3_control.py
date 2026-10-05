"""Shared transport circuit across eight independent Linux workers."""
import datetime
from pathlib import Path
import sqlite3
import time


class CircuitOpen(BaseException):
    pass


class APIWindowClosed(BaseException):
    pass


class Circuit:
    def __init__(self, path, threshold=5):
        self.path, self.threshold = Path(path), threshold
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as database:
            database.execute('CREATE TABLE IF NOT EXISTS circuit (id INTEGER PRIMARY KEY, failures INTEGER, opened INTEGER)')
            database.execute('INSERT OR IGNORE INTO circuit VALUES (1,0,0)')
            database.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, utc TEXT, success INTEGER, classification TEXT)')

    def connection(self):
        return sqlite3.connect(self.path, timeout=30)

    def check(self):
        with self.connection() as database:
            if database.execute('SELECT opened FROM circuit WHERE id=1').fetchone()[0]:
                raise CircuitOpen('Five consecutive transport failures; no queued task is consumed')

    def note(self, success, classification):
        with self.connection() as database:
            database.execute('BEGIN IMMEDIATE')
            failures, opened = database.execute('SELECT failures,opened FROM circuit WHERE id=1').fetchone()
            failures = 0 if success else failures + 1
            opened = opened or failures >= self.threshold
            database.execute('UPDATE circuit SET failures=?,opened=? WHERE id=1', (failures, int(opened)))
            database.execute('INSERT INTO events(utc,success,classification) VALUES (?,?,?)',
                             (datetime.datetime.now(datetime.timezone.utc).isoformat(), int(success), classification))

    def reset_after_explicit_resume(self):
        with self.connection() as database:
            database.execute('UPDATE circuit SET failures=0,opened=0 WHERE id=1')


def api_window_check(cutoff):
    if cutoff is not None and time.time() >= cutoff:
        raise APIWindowClosed('Predeclared API window closed; no new request')
