"""用户级运行时库 data/runtime.db：任务/用量/agent 会话/解锁/工具幂等。

这些表不可重建，reindex 永不触碰（附录 B 分库规则）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..core.text import utc_now_iso

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agent_sessions (
  plan_id       TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL,
  scope         TEXT NOT NULL,
  messages      TEXT NOT NULL,
  status        TEXT NOT NULL,
  confirmed_calls TEXT NOT NULL DEFAULT '[]',
  affected_revisions TEXT NOT NULL DEFAULT '{}',
  expires_at    TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS block_unlocks (
  plan_id       TEXT NOT NULL,
  block_id      TEXT NOT NULL,
  unlocked_at   TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  PRIMARY KEY (plan_id, block_id)
);
CREATE TABLE IF NOT EXISTS usage_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  provider_id   TEXT NOT NULL,
  model         TEXT NOT NULL,
  task          TEXT NOT NULL,
  doc_id        TEXT,
  chapter_id    TEXT,
  tokens_in     INTEGER,
  tokens_out    INTEGER,
  cost_est      REAL,
  request_id    TEXT NOT NULL UNIQUE,
  task_id       TEXT,
  success       INTEGER NOT NULL,
  estimated     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_doc ON usage_log(doc_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_log(provider_id, model, ts);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL, doc_id TEXT, status TEXT NOT NULL,
  request_json TEXT NOT NULL, progress_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT, error_json TEXT,
  idempotency_key TEXT UNIQUE,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_operations (
  operation_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
  result_json TEXT NOT NULL, created_at TEXT NOT NULL
);

-- 点查映射（规范 6.4）：block_id / anchor_id -> doc_id，随 reindex 同步
CREATE TABLE IF NOT EXISTS doc_map (
  kind TEXT NOT NULL,          -- block | anchor
  key TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  PRIMARY KEY (kind, key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_doc_map_doc ON doc_map(doc_id);
"""

_TASK_STATUSES = ("queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted")


class RuntimeDB:
    """线程安全的运行时库访问（单进程约束 10.4）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---- 任务 ----
    def create_task(self, task_id: str, kind: str, request: dict, *, doc_id: str | None = None,
                    idempotency_key: str | None = None) -> dict:
        now = utc_now_iso()
        with self._lock, self._conn:
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row:
                    return dict(row)
            self._conn.execute(
                "INSERT INTO tasks(task_id,kind,doc_id,status,request_json,progress_json,"
                "idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id, kind, doc_id, "queued", json.dumps(request, ensure_ascii=False), "{}",
                 idempotency_key, now, now))
        return self.get_task(task_id)

    def update_task(self, task_id: str, *, status: str | None = None, progress: dict | None = None,
                    result: dict | None = None, error: dict | None = None) -> dict:
        now = utc_now_iso()
        fields, values = [], []
        if status is not None:
            fields.append("status=?"); values.append(status)
        if progress is not None:
            fields.append("progress_json=?"); values.append(json.dumps(progress, ensure_ascii=False))
        if result is not None:
            fields.append("result_json=?"); values.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            fields.append("error_json=?"); values.append(json.dumps(error, ensure_ascii=False))
        fields.append("updated_at=?"); values.append(now)
        values.append(task_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE task_id=?", values)
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _task_dict(row)

    def list_tasks(self, doc_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM tasks"
        args: tuple = ()
        if doc_id:
            q += " WHERE doc_id=?"
            args = (doc_id,)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY created_at DESC LIMIT 200", args).fetchall()
        return [_task_dict(r) for r in rows]

    def interrupt_running(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status='interrupted', updated_at=? WHERE status IN ('queued','running')",
                (utc_now_iso(),))
            return cur.rowcount

    # ---- 用量 ----
    def log_usage(self, *, provider_id: str, model: str, task: str, request_id: str,
                  doc_id: str | None = None, chapter_id: str | None = None,
                  tokens_in: int | None = None, tokens_out: int | None = None,
                  cost_est: float | None = None, success: bool = True,
                  estimated: bool = False, task_id: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO usage_log(ts,provider_id,model,task,doc_id,chapter_id,"
                "tokens_in,tokens_out,cost_est,request_id,task_id,success,estimated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (utc_now_iso(), provider_id, model, task, doc_id, chapter_id, tokens_in,
                 tokens_out, cost_est, request_id, task_id, 1 if success else 0,
                 1 if estimated else 0))

    def usage_summary(self, *, doc_id: str | None = None, days: int = 30) -> list[dict]:
        q = ("SELECT provider_id, model, task, COUNT(*) calls, "
             "SUM(COALESCE(tokens_in,0)) tokens_in, SUM(COALESCE(tokens_out,0)) tokens_out, "
             "SUM(COALESCE(cost_est,0)) cost_est, SUM(estimated) estimated_rows "
             "FROM usage_log WHERE ts >= datetime('now', ?) ")
        args: list = [f"-{int(days)} days"]
        if doc_id:
            q += " AND doc_id=?"
            args.append(doc_id)
        q += " GROUP BY provider_id, model, task ORDER BY provider_id, model, task"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def usage_total(self, *, doc_id: str | None = None, chapter_id: str | None = None) -> float | None:
        """已发生成本估算；含未知价格时返回 None（不能按 0 处理）。"""
        q = "SELECT SUM(COALESCE(cost_est,0)) s, SUM(CASE WHEN cost_est IS NULL THEN 1 ELSE 0 END) unknown FROM usage_log WHERE 1=1"
        args: list = []
        if doc_id:
            q += " AND doc_id=?"; args.append(doc_id)
        if chapter_id:
            q += " AND chapter_id=?"; args.append(chapter_id)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        if row["unknown"]:
            return None
        return float(row["s"] or 0.0)

    # ---- agent 会话 / 解锁 ----
    def save_session(self, plan_id: str, doc_id: str, scope: str, messages: list, status: str,
                     *, confirmed_calls: list | None = None,
                     affected_revisions: dict | None = None, ttl_hours: int = 24) -> None:
        now = utc_now_iso()
        import datetime as _dt
        exp = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=ttl_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO agent_sessions(plan_id,doc_id,scope,messages,status,confirmed_calls,"
                "affected_revisions,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(plan_id) DO UPDATE SET messages=excluded.messages, status=excluded.status,"
                " confirmed_calls=excluded.confirmed_calls, affected_revisions=excluded.affected_revisions,"
                " expires_at=excluded.expires_at",
                (plan_id, doc_id, scope, json.dumps(messages, ensure_ascii=False), status,
                 json.dumps(confirmed_calls or [], ensure_ascii=False),
                 json.dumps(affected_revisions or {}, ensure_ascii=False), exp, now))

    def get_session(self, plan_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM agent_sessions WHERE plan_id=?", (plan_id,)).fetchone()
        return _session_dict(row) if row else None

    def unlock_block(self, plan_id: str, block_id: str, ttl_hours: int = 1) -> None:
        import datetime as _dt
        now = utc_now_iso()
        exp = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=ttl_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO block_unlocks(plan_id,block_id,unlocked_at,expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(plan_id,block_id) DO UPDATE SET expires_at=excluded.expires_at",
                (plan_id, block_id, now, exp))

    def is_unlocked(self, block_id: str, plan_id: str | None = None) -> bool:
        now = utc_now_iso()
        q = "SELECT 1 FROM block_unlocks WHERE block_id=? AND expires_at>?"
        args: list = [block_id, now]
        if plan_id:
            q += " AND plan_id=?"; args.append(plan_id)
        with self._lock:
            return self._conn.execute(q, args).fetchone() is not None

    def lock_block(self, block_id: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM block_unlocks WHERE block_id=?", (block_id,))
            return cur.rowcount

    # ---- 点查映射（block_id/anchor_id -> doc_id）----
    def sync_doc_map(self, doc_id: str, block_ids: list[str], anchor_ids: list[str]) -> None:
        """随 reindex 同步；同一 doc 的键全量替换，避免残留。"""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM doc_map WHERE doc_id=?", (doc_id,))
            self._conn.executemany(
                "INSERT OR REPLACE INTO doc_map(kind,key,doc_id) VALUES(?,?,?)",
                [("block", b, doc_id) for b in block_ids] +
                [("anchor", a, doc_id) for a in anchor_ids])

    def lookup_doc(self, kind: str, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT doc_id FROM doc_map WHERE kind=? AND key=?", (kind, key)).fetchone()
        return row[0] if row else None

    def forget_doc(self, doc_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM doc_map WHERE doc_id=?", (doc_id,))

    # ---- 工具幂等 ----
    def applied_operation(self, operation_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM applied_operations WHERE operation_id=?",
                                     (operation_id,)).fetchone()
        return dict(row) if row else None

    def record_operation(self, operation_id: str, plan_id: str, result: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO applied_operations(operation_id,plan_id,result_json,created_at)"
                " VALUES(?,?,?,?)",
                (operation_id, plan_id, json.dumps(result, ensure_ascii=False), utc_now_iso()))


_RUNTIME: dict[str, RuntimeDB] = {}


def get_runtime(path: str | Path) -> RuntimeDB:
    """进程内复用同一连接（单进程约束 10.4）。"""
    key = str(Path(path).resolve())
    if key not in _RUNTIME:
        _RUNTIME[key] = RuntimeDB(key)
    return _RUNTIME[key]


def _task_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["request"] = json.loads(d.pop("request_json") or "{}")
    d["progress"] = json.loads(d.pop("progress_json") or "{}")
    d["result"] = json.loads(d.pop("result_json") or "null") if d.get("result_json") else None
    d["error"] = json.loads(d.pop("error_json") or "null") if d.get("error_json") else None
    return d


def _session_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["messages"] = json.loads(d.pop("messages") or "[]")
    d["confirmed_calls"] = json.loads(d.pop("confirmed_calls") or "[]")
    d["affected_revisions"] = json.loads(d.pop("affected_revisions") or "{}")
    return d
