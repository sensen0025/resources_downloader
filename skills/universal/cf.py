"""Cloudflare 反制 — 浏览器会话(系统 Chrome)过 CF 校验,复用 cf_clearance 做 HTTP 下载。

原理:CF 的 "Just a moment..." 是 JS 挑战,requests 解不了 —— 但 Playwright 驱动真实
Chrome 能过。流程:
1. 打开 URL(headless,channel=chrome 指纹完整);
2. 轮询页面标题,直到不再是 CF 挑战页(或超时);
3. 取 context cookies(含 cf_clearance)供 requests 复用(通常数分钟内有效);
4. 同时把登录态存到 accounts/cookies/(复用 agent.cookies)。

诚实边界:重度 CF(JS 强校验 + 交互式)可能多次挑战或超时;这里解决的是最常见的
"等几秒自动放行"档。仍不行的站点,Agent 可改用浏览器会话整体下载(use_session)。
"""

from __future__ import annotations

import time
from typing import Optional

__all__ = ["is_cf_challenge", "browser_cookies"]

CF_MARKERS = (
    "just a moment", "cf-challenge", "cf_chl", "__cf_chl",
    "cf-browser-verification", "checking your browser", "verify you are human",
    "安全验证", "请稍候", "cf_clearance",
    "attention required",  # CF 硬墙 403(IP 风控/高风险判定),通常连浏览器也过不去
)


def is_cf_challenge(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in CF_MARKERS)


def browser_cookies(url: str, *, headless: bool = True, wait_seconds: int = 12,
                    max_wait: float = 45.0) -> Optional[tuple[dict, str]]:
    """用 Playwright 打开 URL 等 CF 通过,返回 (requests_cookies_dict, user_agent)。

    失败/超时返回 None。cookies 是 {name: value} 扁平字典,直接给 requests 用。
    """
    try:
        from agent.browser import BrowserSession, USER_AGENT
        from agent.cookies import cookie_path, save_cookies
    except ImportError:
        return None

    try:
        with BrowserSession(headless=headless) as session:
            session.goto(url, timeout=30000)
            deadline = time.monotonic() + max_wait
            passed = False
            while time.monotonic() < deadline:
                title = session.title() or ""
                if not is_cf_challenge(title):
                    session.wait(wait_seconds * 1000)  # 等 JS 渲染完
                    passed = True
                    break
                session.wait(2000)
            if not passed:
                return None
            cookies = {c["name"]: c["value"] for c in session.context.cookies()}
            if not cookies:
                return None
            # 持久化到 accounts/cookies(供后续浏览器会话/Agent 复用)
            try:
                from urllib.parse import urlsplit

                domain = (urlsplit(url).hostname or "unknown").lower()
                save_cookies(session.context, cookie_path(domain))
            except Exception:
                pass
            return cookies, USER_AGENT
    except Exception:
        return None
