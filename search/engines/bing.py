"""Bing 搜索引擎适配器(HTML 解析)。

结构: <li class="b_algo"> → <h2><a href="https://www.bing.com/ck/a?...&u=<base64>">TITLE</a></h2>
      → <div class="b_caption"><p class="b_lineclamp2">SNIPPET</p>
真实 URL 藏在跳转链接的 u= 参数(Base64,前缀 a1),另有 <cite> 兜底。
"""

from __future__ import annotations

import base64
import html as html_mod
import re

from ..models import SearchResult
from .base import EngineError, SearchEngine, strip_tags

BLOCK_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
H2_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', re.S)
CAPTION_RE = re.compile(r'<div class="b_caption"[^>]*>\s*<p[^>]*>(.*?)</p>', re.S)
CITE_RE = re.compile(r"<cite>(.*?)</cite>", re.S)


def _decode_bing_url(href: str) -> str:
    """从 bing.com/ck/a 跳转链接解出真实 URL(u= 参数是 base64)。

    Bing 跳转链接里的 & 是 HTML 实体(&amp;),必须先 unescape 才能匹配 u= 参数。
    """
    href = html_mod.unescape(href)  # 关键修复:&amp; → &
    m = re.search(r"[?&]u=([^&]+)", href)
    if not m:
        return ""
    b64 = m.group(1)
    if b64.startswith("a1"):
        b64 = b64[2:]
    b64 = b64.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        return base64.b64decode(b64).decode("utf-8", "ignore")
    except Exception:
        return ""


def _valid_url(url: str) -> bool:
    """过滤面包屑/非法 URL(<cite> 兜底常见 `https://x.com › sub`)。"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    if " › " in url or " " in url:
        return False
    return True


class BingEngine(SearchEngine):
    name = "bing"
    label = "Bing"

    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        resp = self._get("https://www.bing.com/search", params={"q": query})
        self._check(resp)
        html = resp.text
        results: list[SearchResult] = []
        for rank, block in enumerate(BLOCK_RE.findall(html), start=1):
            m = H2_RE.search(block)
            if not m:
                continue
            href, title_html = m.group(1), m.group(2)
            url = _decode_bing_url(href)
            if not _valid_url(url):
                cite = CITE_RE.search(block)
                url = strip_tags(cite.group(1)) if cite else ""
            if not _valid_url(url):
                continue  # 解码失败且 cite 是面包屑 → 丢弃该条,不产出垃圾 URL
            sn = CAPTION_RE.search(block)
            snippet = strip_tags(sn.group(1)) if sn else ""
            r = self._mk(title_html, url, snippet, rank)
            if r:
                results.append(r)
            if len(results) >= per_engine:
                break
        return self._dedupe_in_engine(results)
