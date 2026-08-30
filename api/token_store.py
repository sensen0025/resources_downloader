"""令牌存储 — 每位客户独立令牌(SQLite,纯 sqlite3 线程安全)。

令牌是 API 的**前置门槛**(个人版配置 RH_API_SECRET 后强制):
- 申请:POST /api/v1/tokens → 签发 `rh_live_xxx`,明文只返回一次;
- 认证:Authorization: Bearer <key>;库里只存 SHA-256 哈希,不存明文;
- 隔离:任务/历史/文件全部挂 owner(令牌所属身份),越权即 403/404;
- 管理:列表 / 轮换(旧钥作废发新钥) / 吊销(审计留痕)。

owner 语义:
- 个人模式(未配 RH_API_SECRET):所有请求 owner="local",不强制认证;
- 令牌模式(配了 RH_API_SECRET):owner=令牌 owner,每个令牌一套独立数据。
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

__all__ = ["TokenStore", "hash_key", "generate_key", "new_owner"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    scopes      TEXT,                 -- JSON 数组
    status      TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    created_at  REAL NOT NULL,
    expires_at  REAL,                 -- 0/NULL = 不过期
    last_used_at REAL,
    revoked_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_tokens_owner ON tokens(owner);
"""


def hash_key(key: str) -> str:
    import hashlib

    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return "rh_live_" + secrets.token_urlsafe(24)


def new_owner() -> str:
    return uuid.uuid4().hex


class TokenStore:
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
            self._conn.commit()

    # ------------------------------------------------------------ 签发

    def create(self, owner: str, name: str = "", scopes: Optional[list[str]] = None,
               expires_days: int = 0) -> tuple[str, str]:
        """签发新令牌,返回 (token_id, token_key)。token_key 明文仅此一次。"""
        token_id = uuid.uuid4().hex[:12]
        key = generate_key()
        now = time.time()
        expires = now + expires_days * 86400 if expires_days else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO tokens (id, owner, key_hash, name, scopes, status,"
                " created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (token_id, owner, hash_key(key), name,
                 json.dumps(scopes or []), "active", now, expires),
            )
            self._conn.commit()
        return token_id, key

    # ------------------------------------------------------------ 查询

    def get(self, token_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tokens WHERE id=?", (token_id,)).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_key(self, key: str) -> Optional[dict]:
        return self.get_by_hash(hash_key(key))

    def get_by_hash(self, key_hash: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tokens WHERE key_hash=?", (key_hash,)).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_by_owner(self, owner: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, owner, name, scopes, status, created_at, expires_at,"
                " last_used_at, revoked_at FROM tokens WHERE owner=? ORDER BY created_at DESC",
                (owner,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _row(self, row) -> dict:
        d = dict(row)
        try:
            d["scopes"] = json.loads(d["scopes"] or "[]")
        except Exception:
            d["scopes"] = []
        return d

    # ------------------------------------------------------------ 管理

    def touch(self, token_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET last_used_at=? WHERE id=?",
                (time.time(), token_id),
            )
            self._conn.commit()

    def revoke(self, token_id: str, owner: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tokens SET status='revoked', revoked_at=? WHERE id=? AND owner=? AND status='active'",
                (time.time(), token_id, owner),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def rotate(self, token_id: str, owner: str) -> Optional[tuple[str, str]]:
        """轮换:旧钥立即作废,返回 (new_token_id, new_token_key)。"""
        old = self.get(token_id)
        if old is None or old["owner"] != owner or old["status"] != "active":
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET status='revoked', revoked_at=? WHERE id=?",
                (time.time(), token_id),
            )
            self._conn.commit()
        new_id, new_key = self.create(owner, name=old.get("name") or "",
                                      scopes=old.get("scopes") or [],
                                      expires_days=0)
        return new_id, new_key
