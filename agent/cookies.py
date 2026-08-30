"""Cookie 存储层 — 登录态持久化,免重复登录。

对齐项目「账号凭证存 accounts/」的既有约定,登录态存 `accounts/cookies/`,
格式直接用 Playwright 的 storage_state(JSON:cookies + localStorage/origins),
`browser.new_context(storage_state=...)` 原生支持,零自定义解析。

用法:
    from agent.cookies import cookie_path, has_cookies, load_cookies, save_cookies

    ck = cookie_path("claude.ai", "me@gmail.com")
    if has_cookies(ck):                      # 有未过期的登录态
        with BrowserSession(cookies_path=ck) as s:   # 自动注入
            ...
    # 登录/注册成功后持久化(刷新过期时间)
    save_cookies(session.context, ck)

语义:
- 按「站点域 + 邮箱」分文件(不同账号互不串);
- 读取时过滤已过期 Cookie(session cookie expires=-1 保留);
- 文件路径是安全的:域/邮箱里的非字母数字字符全部下划线化。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

__all__ = [
    "cookies_dir",
    "cookie_path",
    "has_cookies",
    "load_cookies",
    "save_cookies",
    "delete_cookies",
]

_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # resource-hub/
_COOKIES_DIR = _PROJECT_ROOT / "accounts" / "cookies"


def cookies_dir() -> Path:
    return _COOKIES_DIR


def _sanitize(part: str) -> str:
    """域名/邮箱 → 文件名安全片段(a-zA-Z0-9 与 _,-,. 之外的字符下划线化)。"""
    s = re.sub(r"[^a-zA-Z0-9._-]", "_", part or "").strip("._")
    return s or "default"


def cookie_path(site_or_domain: str, email: str = "") -> Path:
    """登录态文件路径:accounts/cookies/{域}__{邮箱}.json(无邮箱则只按域)。"""
    key = _sanitize(site_or_domain)
    if email:
        key = f"{key}__{_sanitize(email)}"
    return _COOKIES_DIR / f"{key}.json"


def _expired(cookie: dict, now: float) -> bool:
    exp = cookie.get("expires", -1)
    if exp is None:
        return False
    if exp < 0:      # -1 = session cookie(会话内有效,保留)
        return False
    return exp <= now


def has_cookies(path: str | Path, now: Optional[float] = None) -> bool:
    """文件存在且至少有一个未过期 Cookie。"""
    state = load_cookies(path, now=now)
    return state is not None and bool(state.get("cookies"))


def load_cookies(path: str | Path, now: Optional[float] = None) -> Optional[dict]:
    """读取并过滤过期 Cookie,返回 storage_state dict;无有效 Cookie 返回 None。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    cookies = data.get("cookies") or []
    now = time.time() if now is None else now
    valid = [c for c in cookies if isinstance(c, dict) and not _expired(c, now)]
    if not valid:
        return None
    if len(valid) != len(cookies):
        data = dict(data)
        data["cookies"] = valid
    return data


def save_cookies(context, path: str | Path) -> Path:
    """把浏览器 context 的 cookies+localStorage 落盘(context.storage_state)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(p))
    return p


def delete_cookies(path: str | Path) -> bool:
    """删除登录态文件(重新登录用)。返回是否删掉了东西。"""
    p = Path(path)
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            pass
    return False
