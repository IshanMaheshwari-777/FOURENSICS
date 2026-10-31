"""SQLite storage: runs (one PanelResult JSON per run), model-call log, feedback."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schemas import PanelResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  cache_key TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_cache_key ON runs(cache_key);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  purpose TEXT,
  model TEXT,
  prompt_version TEXT,
  prompt_hash TEXT,
  prompt_text TEXT,
  response_text TEXT,
  ok INTEGER,
  ms INTEGER,
  created_at TEXT,
  wait_ms INTEGER,
  model_ms INTEGER,
  tokens_used INTEGER
);
CREATE TABLE IF NOT EXISTS llm_cache (
  prompt_hash TEXT PRIMARY KEY,
  response_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  vote TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            # Older databases predate the timing columns: add them in place, keeping all rows.
            have = {r["name"] for r in c.execute("PRAGMA table_info(llm_calls)")}
            for column in ("wait_ms", "model_ms", "tokens_used"):
                if column not in have:
                    c.execute(f"ALTER TABLE llm_calls ADD COLUMN {column} INTEGER")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    # runs
    def new_run(self, cache_key: str, result: PanelResult) -> str:
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs (id, cache_key, status, result_json, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (result.run_id, cache_key, result.status, result.model_dump_json(), now_iso(), now_iso()),
            )
        return result.run_id

    def save_run(self, result: PanelResult) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET status=?, result_json=?, updated_at=? WHERE id=?",
                (result.status, result.model_dump_json(), now_iso(), result.run_id),
            )

    def get_run(self, run_id: str) -> Optional[PanelResult]:
        row = self._conn().execute("SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()
        return PanelResult.model_validate_json(row["result_json"]) if row else None

    def latest_for_key(self, cache_key: str) -> Optional[PanelResult]:
        row = self._conn().execute(
            "SELECT result_json FROM runs WHERE cache_key=? AND status IN ('complete','partial','running','queued') "
            "ORDER BY created_at DESC LIMIT 1",
            (cache_key,),
        ).fetchone()
        return PanelResult.model_validate_json(row["result_json"]) if row else None

    def mark_stale_running_as_error(self) -> None:
        """Runs left 'running' by a previous process will never finish."""
        rows = self._conn().execute("SELECT id, result_json FROM runs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            result = PanelResult.model_validate_json(row["result_json"])
            result.status = "error"
            result.error = "Interrupted by a backend restart"
            self.save_run(result)

    # model calls
    def log_llm_call(self, **fields) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO llm_calls (run_id, purpose, model, prompt_version, prompt_hash, prompt_text, response_text, "
                "ok, ms, created_at, wait_ms, model_ms, tokens_used) "
                "VALUES (:run_id,:purpose,:model,:prompt_version,:prompt_hash,:prompt_text,:response_text,:ok,:ms,"
                ":created_at,:wait_ms,:model_ms,:tokens_used)",
                # ms = total; wait_ms = rate-limit pacing/backoff and queueing; model_ms = time inside HTTP calls.
                {"wait_ms": None, "model_ms": None, "tokens_used": None, **fields, "created_at": now_iso()},
            )

    def llm_calls_for(self, run_id: str) -> list[dict]:
        rows = self._conn().execute("SELECT * FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def cache_get(self, prompt_hash: str) -> Optional[str]:
        row = self._conn().execute("SELECT response_text FROM llm_cache WHERE prompt_hash=?", (prompt_hash,)).fetchone()
        return row["response_text"] if row else None

    def cache_put(self, prompt_hash: str, response_text: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO llm_cache (prompt_hash, response_text, created_at) VALUES (?,?,?)",
                (prompt_hash, response_text, now_iso()),
            )

    # feedback
    def add_feedback(self, run_id: str, claim_id: str, vote: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO feedback (run_id, claim_id, vote, created_at) VALUES (?,?,?,?)",
                (run_id, claim_id, vote, now_iso()),
            )

    def feedback_for(self, run_id: str) -> list[dict]:
        rows = self._conn().execute("SELECT * FROM feedback WHERE run_id=?", (run_id,)).fetchall()
        return [dict(r) for r in rows]


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
