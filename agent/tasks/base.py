"""任务基类 — 对齐计划 v4 §3.5:阶段独立超时/重试,完成以验证器为准。

任务 = {goal, allowed_domain, budget, done_verifier}。
done_verifier(下载探针/URL 断言)是唯一成功标准 —— 模型"以为完成"不算数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class TaskResult:
    success: bool
    summary: str = ""
    files: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "summary": self.summary,
                "files": self.files, "sources": self.sources, "error": self.error}


@dataclass
class BaseTask:
    goal: str
    allowed_domain: str = ""
    max_steps: int = 40
    done_verifier: Optional[Callable[[], bool]] = None

    def verify_done(self) -> bool:
        """默认:无验证器时按模型判定;有验证器时以它为准。"""
        return bool(self.done_verifier and self.done_verifier())


class FileProbe:
    """下载探针(skyvern 经验):文件落地 + 扩展名 + 非空 才算完成。

    以创建时的目录快照为基线,只统计任务期间新增的文件
    (避免把历史下载误算进本次任务结果)。
    """

    def __init__(self, out_dir: str | Path, expected_exts: tuple[str, ...]) -> None:
        self.out_dir = Path(out_dir)
        self.expected_exts = tuple(e.lower() for e in expected_exts)
        self._baseline = set(self._scan())

    def _scan(self) -> list[str]:
        if not self.out_dir.exists():
            return []
        files = []
        for p in self.out_dir.iterdir():
            if p.is_file() and p.name.endswith(".part"):
                continue
            if p.suffix.lower() in self.expected_exts or not self.expected_exts:
                if p.stat().st_size > 0:
                    files.append(str(p))
        return files

    def found_files(self) -> list[str]:
        return [f for f in self._scan() if f not in self._baseline]

    def all_files(self) -> list[str]:
        return self._scan()

    def __call__(self) -> bool:
        return bool(self.found_files())
