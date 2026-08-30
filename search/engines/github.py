"""GitHub API 适配器 — 软件/代码/电子书仓库检索(免费、稳定、结构化)。

search/repositories 无需 token 也可用(低限流);有 GITHUB_TOKEN 时额度更高。
结果: full_name 作标题, html_url 作 URL, description 作摘要。
"""

from __future__ import annotations

import os

from ..models import SearchResult
from .base import EngineError, SearchEngine

_API = "https://api.github.com/search/repositories"


class GitHubEngine(SearchEngine):
    name = "github"
    label = "GitHub"

    def __init__(self, timeout: float | None = None, token: str = "") -> None:
        super().__init__(timeout)
        self.token = token or os.environ.get("GITHUB_TOKEN", "")

    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self._get(
            _API,
            params={"q": query, "per_page": per_engine},
            headers=headers,
        )
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            raise EngineError("GitHub API 限流(无 token 时 10 次/分钟),建议配 GITHUB_TOKEN")
        self._check(resp)
        try:
            items = resp.json().get("items", [])
        except ValueError as e:
            raise EngineError(f"GitHub 响应非 JSON: {str(e)[:80]}") from e
        results: list[SearchResult] = []
        for rank, it in enumerate(items, start=1):
            r = self._mk(
                it.get("full_name") or it.get("name") or "",
                it.get("html_url") or "",
                it.get("description") or "",
                rank,
            )
            if r:
                results.append(r)
        return results
