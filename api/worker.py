"""任务执行器 — 有界线程池(每个任务烧 LLM+网络,必须限并发)。

RH_MAX_WORKERS 环境变量控制并发上限(默认 2);超出的任务排队等待,
队列上限 128,满了直接拒绝(429 语义由 API 层转 503/429)。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

__all__ = ["TaskExecutor", "get_executor"]

_QUEUE_MAX = 128


class TaskExecutor:
    def __init__(self, max_workers: int = 2, queue_max: int = _QUEUE_MAX) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="rh-task")
        self._queue_max = queue_max
        self._lock = threading.Lock()
        self._running = 0

    @property
    def running(self) -> int:
        with self._lock:
            return self._running

    def submit(self, task_id: str, fn: Callable[[], None]) -> bool:
        """提交任务。队列已满返回 False(调用方应拒绝新任务)。"""
        with self._lock:
            if self._running >= self._queue_max:
                return False
            self._running += 1

        def _wrapper() -> None:
            try:
                fn()
            finally:
                with self._lock:
                    self._running -= 1

        self._pool.submit(_wrapper)
        return True


_executor: Optional[TaskExecutor] = None


def get_executor() -> TaskExecutor:
    global _executor
    if _executor is None:
        _executor = TaskExecutor(max_workers=int(os.environ.get("RH_MAX_WORKERS", "2")))
    return _executor
