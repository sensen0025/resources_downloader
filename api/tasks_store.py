"""任务存储 — SQLite(纯 sqlite3,线程安全),带旧库自动迁移。

一张 tasks 表装下任务全生命周期:状态机(queued/running/done/failed/cancelled)、
进度(stage/percent)、append-only 事件流、结果与文件清单、owner 隔离、幂等键。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

__all__ = ["TaskStore", "TERMINAL_STATUS"]

TERMINAL_STATUS = ("done", "failed", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    owner           TEXT NOT NULL DEFAULT 'local',
    query           TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'queued',
    request         TEXT,                 -- 完整请求 JSON
    callback_url    TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    file_token      TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT '',
    percent         INTEGER NOT NULL DEFAULT 0,
    events          TEXT,                 -- JSON 数组(append-only 事件流)
    files           TEXT,                 -- JSON 数组[{name,size,sha256,verdict,url}]
    result          TEXT,                 -- TaskResult JSON
    error           TEXT NOT NULL DEFAULT '',
    cancelled       INTEGER NOT NULL DEFAULT 0,   -- 取消请求标志(协作式)
    created_at      REAL NOT NULL,
    started_at      REAL,
    finished_at     REAL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_created ON tasks(owner, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_idem ON tasks(owner, idempotency_key);
"""

# 旧库迁移:缺列就 ALTER TABLE 补上(新列有默认值,兼容存量数据)
_ADD_COLUMNS: dict[str, str] = {
    "owner": "owner TEXT NOT NULL DEFAULT 'local'",
    "label": "label TEXT NOT NULL DEFAULT ''",
    "request": "request TEXT",
    "callback_url": "callback_url TEXT NOT NULL DEFAULT ''",
    "idempotency_key": "idempotency_key TEXT NOT NULL DEFAULT ''",
    "file_token": "file_token TEXT NOT NULL DEFAULT ''",
    "stage": "stage TEXT NOT NULL DEFAULT ''",
    "percent": "percent INTEGER NOT NULL DEFAULT 0",
    "events": "events TEXT",
    "files": "files TEXT",
    "error": "error TEXT NOT NULL DEFAULT ''",
    "cancelled": "cancelled INTEGER NOT NULL DEFAULT 0",
    "started_at": "started_at REAL",
    "finished_at": "finished_at REAL",
}


class TaskStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path or os.environ.get("RH_DB_PATH") or "data/tasks.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._sweep_orphans()
            self._conn.commit()

    def _sweep_orphans(self) -> None:
        """孤儿任务清理:新进程启动时,任何 queued/running 的任务都属于上一个进程
        (本进程还没创建过任务)—— 进程崩溃/重启后它们永远不会再被跑,
        状态卡在 running 会让控制台一直显示"在跑"(线上"卡住"观感,曾滞留一整天)。
        统一标记为 failed,附重启中断说明。
        """
        now = time.time()
        self._conn.execute(
            "UPDATE tasks SET status='failed', finished_at=?, updated_at=?, error=? "
            "WHERE status IN ('queued','running')",
            (now, now, "API 重启,运行中任务被中断"),
        )

    def _migrate(self) -> None:
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(tasks)")}
        for col, ddl in _ADD_COLUMNS.items():
            if col not in existing:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {ddl}")

    # ------------------------------------------------------------ 创建/查询

    def create(self, owner: str, query: str, *, label: str = "",
               request: Optional[dict] = None, callback_url: str = "",
               idempotency_key: str = "", file_token: str = "") -> str:
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, owner, query, label, status, request,"
                " callback_url, idempotency_key, file_token, events, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, owner, query, label, "queued",
                 json.dumps(request, ensure_ascii=False) if request else None,
                 callback_url, idempotency_key, file_token,
                 json.dumps([], ensure_ascii=False), now, now),
            )
            self._conn.commit()
        return task_id

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row(row)

    def find_by_idempotency(self, owner: str, key: str) -> Optional[dict]:
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE owner=? AND idempotency_key=? ORDER BY created_at DESC LIMIT 1",
                (owner, key),
            ).fetchone()
        return self._row(row) if row else None

    def list(self, owner: str, limit: int = 20, offset: int = 0,
             status: str = "") -> list[dict]:
        sql = "SELECT id, owner, query, label, status, stage, percent, error," \
              " created_at, started_at, finished_at FROM tasks WHERE owner=?"
        args: list = [owner]
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count(self, owner: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE owner=?", (owner,)).fetchone()
        return int(row["n"])

    def _row(self, row) -> dict:
        d = dict(row)
        for key in ("request", "events", "files", "result"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except Exception:
                    d[key] = None if key != "events" else []
        d.setdefault("events", d.get("events") or [])
        return d

    # ------------------------------------------------------------ 生命周期

    def set_running(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='running', started_at=?, updated_at=? WHERE id=?",
                (time.time(), time.time(), task_id),
            )
            self._conn.commit()

    def set_progress(self, task_id: str, stage: str, percent: int,
                     message: str = "") -> None:
        self.append_event(task_id, "stage",
                          {"stage": stage, "percent": percent, "message": message})
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET stage=?, percent=?, updated_at=? WHERE id=?",
                (stage, percent, time.time(), task_id),
            )
            self._conn.commit()

    def append_event(self, task_id: str, event_type: str, data: dict) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT events FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return
            try:
                events = json.loads(row["events"] or "[]")
            except Exception:
                events = []
            events.append({"ts": time.time(), "type": event_type, **data})
            # 事件流上限:保留最近 500 条,防无限膨胀
            if len(events) > 500:
                events = events[-500:]
            self._conn.execute(
                "UPDATE tasks SET events=?, updated_at=? WHERE id=?",
                (json.dumps(events, ensure_ascii=False), time.time(), task_id),
            )
            self._conn.commit()

    def set_result(self, task_id: str, status: str, result: Optional[dict],
                   files: Optional[list[dict]] = None, error: str = "") -> None:
        self.append_event(task_id, status, {"message": (result or {}).get("summary", "")})
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status=?, result=?, files=?, error=?, finished_at=?,"
                " updated_at=? WHERE id=?",
                (status,
                 json.dumps(result, ensure_ascii=False) if result else None,
                 json.dumps(files, ensure_ascii=False) if files else None,
                 error, time.time(), time.time(), task_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------ 取消(协作式)

    def request_cancel(self, task_id: str) -> bool:
        """置取消标志;queued 状态直接终止。返回是否受理。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == "queued":
                self._conn.execute(
                    "UPDATE tasks SET status='cancelled', cancelled=1, finished_at=?, updated_at=? WHERE id=?",
                    (time.time(), time.time(), task_id),
                )
                self._conn.commit()
                return True
            self._conn.execute(
                "UPDATE tasks SET cancelled=1, updated_at=? WHERE id=?",
                (time.time(), task_id),
            )
            self._conn.commit()
        return True

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancelled FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row["cancelled"])
