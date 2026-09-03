from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key         TEXT PRIMARY KEY,
    url_norm    TEXT,
    source      TEXT,
    board       TEXT,
    company     TEXT,
    title       TEXT,
    url         TEXT,
    location    TEXT,
    posted_at   TEXT,
    first_seen  TEXT NOT NULL,
    matched     INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    notified_at TEXT,
    data        TEXT
);
CREATE INDEX IF NOT EXISTS jobs_url_norm ON jobs(url_norm);
CREATE INDEX IF NOT EXISTS jobs_matched ON jobs(matched, first_seen);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS boards (
    kind      TEXT NOT NULL,
    ident     TEXT NOT NULL,
    company   TEXT,
    params    TEXT,
    added_at  TEXT NOT NULL,
    origin    TEXT,
    failures  INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (kind, ident)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL")

    # -- kv -----------------------------------------------------------------
    def get(self, k: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def set(self, k: str, v: str) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, v))
            self.conn.commit()

    # -- jobs ---------------------------------------------------------------
    def is_first_run(self) -> bool:
        return self.get("first_run_done") != "1"

    def mark_first_run_done(self) -> None:
        self.set("first_run_done", "1")

    def seen_keys(self) -> set[str]:
        with self._lock:
            return {r["key"] for r in self.conn.execute("SELECT key FROM jobs")}

    def seen_urls(self) -> set[str]:
        with self._lock:
            return {r["url_norm"] for r in self.conn.execute("SELECT url_norm FROM jobs WHERE url_norm != ''")}

    def record(self, job: Job, matched: bool, reason: str, notified: bool) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO jobs(key,url_norm,source,board,company,title,url,location,posted_at,"
                "first_seen,matched,reason,notified_at,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job.key, job.url_norm, job.source, job.board, job.company, job.title, job.url, job.location,
                 job.posted_at.isoformat() if job.posted_at else None, _now(), int(matched), reason,
                 _now() if notified else None, json.dumps(job.to_dict())))
            self.conn.commit()

    def mark_notified(self, keys: Iterable[str]) -> None:
        with self._lock:
            self.conn.executemany("UPDATE jobs SET notified_at=? WHERE key=?", [(_now(), k) for k in keys])
            self.conn.commit()

    def matched_jobs(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data, first_seen, notified_at FROM jobs WHERE matched=1 ORDER BY first_seen DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d["first_seen"] = r["first_seen"]
            d["notified_at"] = r["notified_at"]
            out.append(d)
        return out

    def stats(self) -> dict:
        with self._lock:
            total = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            matched = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE matched=1").fetchone()[0]
            boards = self.conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0]
        return {"jobs_seen": total, "jobs_matched": matched, "discovered_boards": boards}

    # -- boards -------------------------------------------------------------
    def add_board(self, kind: str, ident: str, company: str, params: Optional[dict] = None,
                  origin: str = "discovered") -> bool:
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO boards(kind, ident, company, params, added_at, origin) VALUES (?,?,?,?,?,?)",
                (kind, ident, company, json.dumps(params or {}), _now(), origin))
            self.conn.commit()
            return cur.rowcount > 0

    def remove_board(self, kind: str, ident: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM boards WHERE kind=? AND ident=?", (kind, ident))
            self.conn.commit()
            return cur.rowcount > 0

    def boards(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM boards ORDER BY added_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d.get("params") or "{}")
            out.append(d)
        return out

    def board_result(self, kind: str, ident: str, error: Optional[str]) -> None:
        with self._lock:
            if error:
                self.conn.execute("UPDATE boards SET failures=failures+1, last_error=? WHERE kind=? AND ident=?",
                                  (error[:300], kind, ident))
            else:
                self.conn.execute("UPDATE boards SET failures=0, last_error=NULL WHERE kind=? AND ident=?",
                                  (kind, ident))
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()
