"""Mojeek 搜索引擎适配器 — 对爬虫最友好的引擎之一,零跳转直链。

结构: <ul class="results-standard"><li class="r1">
        <h2><a class="title" href="URL">TITLE</a></h2>
        <p class="s">SNIPPET</p></li>
"""

from __future__ import annotations

import re

from ..models import SearchResult
from .base import SearchEngine, strip_tags

LI_RE = re.compile(r'<li class="r\d+[^"]*".*?</li>', re.S)
TITLE_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', re.S)
SNIPPET_RE = re.compile(r'<p class="s">(.*?)</p>', re.S)


class MojeekEngine(SearchEngine):
    name = "mojeek"
    label = "Mojeek"

    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        resp = self._get("https://www.mojeek.com/search", params={"q": query})
        self._check(resp)
        html = resp.text
        results: list[SearchResult] = []
        for rank, li in enumerate(LI_RE.findall(html), start=1):
            m = TITLE_RE.search(li)
            if not m:
                continue
            url, title_html = m.group(1), m.group(2)
            sn = SNIPPET_RE.search(li)
            snippet = strip_tags(sn.group(1)) if sn else ""
            r = self._mk(title_html, url, snippet, rank)
            if r:
                results.append(r)
            if len(results) >= per_engine:
                break
        return self._dedupe_in_engine(results)
