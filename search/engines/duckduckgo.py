"""DuckDuckGo Lite 适配器 — 对资源检索价值高(常直接返回 PDF 直链,带类型徽标)。

注意:html.duckduckgo.com 会 202 挑战,lite 端点需 POST。结构:
  <a rel="nofollow" href="URL" class='result-link'>TITLE</a>
  <td class='result-snippet'>SNIPPET</td>
"""

from __future__ import annotations

import re

from ..models import SearchResult
from .base import SearchEngine, strip_tags

LINK_RE = re.compile(r"<a[^>]*rel=\"nofollow\"[^>]*href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", re.S)
SNIPPET_RE = re.compile(r"<td class='result-snippet'>(.*?)</td>", re.S)


class DuckDuckGoEngine(SearchEngine):
    name = "duckduckgo"
    label = "DuckDuckGo Lite"

    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        import time

        # 202 = 反爬挑战/需 JS:退避重试一次,仍失败再报错(进入引擎熔断)
        resp = self._get("https://lite.duckduckgo.com/lite/", data={"q": query})
        if resp.status_code == 202:
            time.sleep(2.5)
            resp = self._get("https://lite.duckduckgo.com/lite/", data={"q": query})
        self._check(resp)
        html = resp.text
        links = LINK_RE.findall(html)
        snippets = [strip_tags(s) for s in SNIPPET_RE.findall(html)]
        results: list[SearchResult] = []
        for rank, (url, title_html) in enumerate(links, start=1):
            snippet = snippets[rank - 1] if rank - 1 < len(snippets) else ""
            r = self._mk(title_html, url, snippet, rank)
            if r:
                results.append(r)
            if len(results) >= per_engine:
                break
        return self._dedupe_in_engine(results)
