"""认证与身份 — 令牌 / 按 IP 的网页租户 / 管理员。

安全模型(公网直连部署):
- **有效 Bearer 令牌** → 令牌 owner(程序化 API 主通道);
- **网页同源请求 / 个人模式匿名请求** → 按客户端 IP 的租户 `ip_<addr>`:
  公网多访客互不可见(任务/文件/历史隔离),不再共享 local(原缺陷:任何人能看到
  所有人的任务与文件);
- **令牌模式(token_mode=true)下匿名非网页请求** → 401;
- **管理员**:持有 RH_ADMIN_TOKEN(Bearer 或 X-RH-Admin 头)或来自本地回环 ——
  只有管理员能改配置/查任意文件/触发 LLM 测试(原缺陷:公网可覆写配置锁死控制台)。
"""

from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request

from .token_store import TokenStore, hash_key

__all__ = [
    "is_token_mode",
    "LOCAL_OWNER",
    "current_owner",
    "resolve_owner",
    "client_ip",
    "web_owner",
    "is_web_request",
    "is_loopback",
    "is_admin",
    "require_admin",
    "get_token_store",
]

LOCAL_OWNER = "local"

_ts: Optional[TokenStore] = None


def get_token_store() -> TokenStore:
    global _ts
    if _ts is None:
        _ts = TokenStore()
    return _ts


def is_token_mode() -> bool:
    return bool(os.environ.get("RH_API_SECRET"))


def client_ip(request: Request) -> str:
    """客户端 IP(取 TCP 对端,不信任公网 X-Forwarded-For 防伪造)。"""
    host = "unknown"
    if request.client is not None and request.client.host:
        host = request.client.host
    return host.replace(":", "_")  # IPv6 冒号不适合做 owner 标识


def web_owner(request: Request) -> str:
    """网页/匿名访客的按 IP 租户(会话级隔离,互不可见)。"""
    return f"ip_{client_ip(request)}"


def is_loopback(request: Request) -> bool:
    return client_ip(request) in ("127.0.0.1", "::1", "localhost")


def is_web_request(request: Request) -> bool:
    """是否浏览器网页同源请求(免令牌)。显式头优先,Sec-Fetch-Site 兜底。"""
    if request.headers.get("x-rh-web") == "1":
        return True
    return request.headers.get("sec-fetch-site") in ("same-origin", "same-site")


def resolve_owner(request: Request, store: TokenStore) -> str:
    """解析请求所属 owner(数据隔离的唯一下游)。

    优先级:有效 Bearer 令牌 → 网页同源 → 个人模式匿名(按 IP)→ 401。
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
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
    if is_web_request(request):
        return web_owner(request)
    if not is_token_mode():
        return web_owner(request)
    raise HTTPException(status_code=401, detail="缺少 Bearer 令牌(先 POST /api/v1/tokens 申请)")


def current_owner(request: Request, store: TokenStore = Depends(get_token_store)) -> str:
    """FastAPI 依赖:解析请求所属 owner(网页/匿名按 IP 隔离)。"""
    return resolve_owner(request, store)


def admin_token() -> str:
    return os.environ.get("RH_ADMIN_TOKEN", "")


def is_admin(request: Request) -> bool:
    """管理员判定:本地回环,或提供正确的 RH_ADMIN_TOKEN(Bearer / X-RH-Admin 头)。"""
    if is_loopback(request):
        return True
    tok = admin_token()
    if not tok:
        return False
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].strip() == tok:
        return True
    return request.headers.get("x-rh-admin", "") == tok


def require_admin(request: Request) -> None:
    """FastAPI 依赖式守卫:非管理员 → 403(配置覆写/任意文件扫描/LLM 测试等高危操作)。"""
    if not is_admin(request):
        raise HTTPException(status_code=403,
                            detail="需要管理员凭证(RH_ADMIN_TOKEN 或本地回环访问)")
