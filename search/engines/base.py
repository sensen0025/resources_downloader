"""搜索引擎适配器基类 + 通用 HTML 清洗工具。

对齐 yt-dlp「统一契约 + 注册表」:每个引擎实现 search() 返回统一的 SearchResult 列表,
聚合层不关心具体引擎的页面结构。失败抛 EngineError,由聚合层容错。
"""

from __future__ import annotations

import html as html_mod
import re
import time
from abc import ABC, abstractmethod
from typing import Optional

import requests

from proxy import apply_proxies

from ..models import SearchResult

__all__ = ["SearchEngine", "EngineError", "strip_tags", "unescape"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class EngineError(RuntimeError):
    pass


def strip_tags(text: str) -> str:
    """去 HTML 标签 + 去实体 + 压缩空白。"""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def unescape(text: str) -> str:
    return html_mod.unescape(text or "")


class SearchEngine(ABC):
    """搜索引擎适配器。name 是注册键,label 用于展示。"""

    name: str = "base"
    label: str = "Base"
    timeout: float = 12.0

    def __init__(self, timeout: Optional[float] = None) -> None:
        if timeout:
            self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        apply_proxies(self._session)  # VPN/代理:RH_PROXY_URL 或标准环境变量

    # ------------------------------------------------------------ 主入口

    @abstractmethod
    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        """执行检索,返回统一结果(URL 必须去跟踪参数规范化)。"""

    # ------------------------------------------------------------ 工具

    def _get(self, url: str, params: Optional[dict] = None, data: Optional[dict] = None,
             headers: Optional[dict] = None, timeout: Optional[float] = None,
             referer: str = "") -> requests.Response:
        try:
            hdrs = dict(DEFAULT_HEADERS)
            if headers:
                hdrs.update(headers)
            if referer:
                hdrs["Referer"] = referer
            if data is not None:
                resp = self._session.post(url, data=data, headers=hdrs,
                                          timeout=timeout or self.timeout)
            else:
                resp = self._session.get(url, params=params, headers=hdrs,
                                         timeout=timeout or self.timeout)
            return resp
        except requests.RequestException as e:
            raise EngineError(f"{self.name}: 请求失败 {type(e).__name__}: {str(e)[:120]}") from e

    def _check(self, resp: requests.Response) -> None:
        if resp.status_code != 200:
            raise EngineError(f"{self.name}: HTTP {resp.status_code}")
        if "just a moment" in resp.text[:3000].lower() or "cf-challenge" in resp.text[:3000].lower():
            raise EngineError(f"{self.name}: 被 Cloudflare 挑战拦截")

    def _mk(self, title: str, url: str, snippet: str, rank: int) -> Optional[SearchResult]:
        title = strip_tags(title)
        url = (url or "").strip()
        if not title or not url or not url.startswith(("http://", "https://")):
            return None
        if url.startswith(("javascript:", "mailto:", "#")):
            return None
        return SearchResult(
            title=title[:300], url=url, snippet=strip_tags(snippet)[:500],
            engine=self.name, rank=rank,
        )

    @staticmethod
    def _dedupe_in_engine(results: list[SearchResult]) -> list[SearchResult]:
        """站内去重:用规范化后的完整 URL 作 key。

        不能用 split('?')[0] —— 360 的 so.com/link?m=<opaque> 跳转链接 query 是
        唯一标识,截断后全部相同会把结果误杀到只剩 1 条。
        """
        from ..normalize import normalize_url

        seen: set[str] = set()
        out = []
        for r in results:
            key = normalize_url(r.url)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out
