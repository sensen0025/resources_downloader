"""360 搜索(so.com)适配器 — 中文资源检索的重要补充。

结构: <li class="res-list"><h3 class="res-title ..."><a href="https://www.so.com/link?m=...">TITLE</a></h3>
      <p class="res-desc">SNIPPET</p>
链接是 so.com/link 跳转(probe 阶段会自动跟随),标题直出。
"""

from __future__ import annotations

import re

from ..models import SearchResult
from .base import SearchEngine, strip_tags

LI_RE = re.compile(r'<li class="res-list[^"]*".*?</li>', re.S)
TITLE_RE = re.compile(r'<h3\s+class="res-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>', re.S)
DESC_RE = re.compile(r'<p class="res-desc"[^>]*>(.*?)</p>', re.S)


class So360Engine(SearchEngine):
    name = "so360"
    label = "360搜索"

    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        resp = self._get("https://www.so.com/s", params={"q": query},
                         referer="https://www.so.com/")
        self._check(resp)
        html = resp.text
        results: list[SearchResult] = []
        for rank, li in enumerate(LI_RE.findall(html), start=1):
            m = TITLE_RE.search(li)
            if not m:
                continue
            url, title_html = m.group(1), m.group(2)
            sn = DESC_RE.search(li)
            snippet = strip_tags(sn.group(1)) if sn else ""
            r = self._mk(title_html, url, snippet, rank)
            if r:
                results.append(r)
            if len(results) >= per_engine:
                break
        return self._dedupe_in_engine(results)
