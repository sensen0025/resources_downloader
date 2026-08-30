"""认证 — 令牌即身份(配置即启用,默认个人模式)。

- 未配 RH_API_SECRET → 个人模式:不强制认证,owner="local";
- 配置 RH_API_SECRET → 令牌模式:全部业务端点强制 `Authorization: Bearer <key>`,
  校验哈希/状态/过期,owner=令牌所属身份(数据隔离)。
"""

from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request

from .token_store import TokenStore, hash_key

__all__ = ["is_token_mode", "LOCAL_OWNER", "current_owner", "get_token_store"]

LOCAL_OWNER = "local"

_ts: Optional[TokenStore] = None


def get_token_store() -> TokenStore:
    global _ts
    if _ts is None:
        _ts = TokenStore()
    return _ts


def is_token_mode() -> bool:
    return bool(os.environ.get("RH_API_SECRET"))


def current_owner(request: Request, store: TokenStore = Depends(get_token_store)) -> str:
    """FastAPI 依赖:解析请求所属 owner。令牌模式下无效/过期令牌一律 401。"""
    if not is_token_mode():
        return LOCAL_OWNER
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer 令牌(先 POST /api/v1/tokens 申请)")
    key = auth[7:].strip()
    if not key:
        raise HTTPException(status_code=401, detail="令牌为空")
    token = store.get_by_hash(hash_key(key))
    if token is None or token["status"] != "active":
        raise HTTPException(status_code=401, detail="令牌无效或已吊销")
    if token.get("expires_at") and token["expires_at"] < time.time():
        raise HTTPException(status_code=401, detail="令牌已过期")
    store.touch(token["id"])
    return token["owner"]
