"""进程内 IP 频控 — 滑动窗口(防未授权刷接口:LLM 测试等有成本/有风险的调用)。

纯内存实现,多 worker 各自独立(够用即可);key 由调用方拼接,如 "test_llm:<ip>"。
"""

from __future__ import annotations

import threading
import time
from collections import deque

__all__ = ["allow", "reset"]

_limits: dict[str, deque] = {}
_lock = threading.Lock()


def allow(key: str, limit: int = 5, window: float = 60.0) -> bool:
    """滑动窗口限流:窗口内调用次数 < limit 时放行并记账,否则拒绝。"""
    now = time.monotonic()
    with _lock:
        q = _limits.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset() -> None:
    with _lock:
        _limits.clear()
