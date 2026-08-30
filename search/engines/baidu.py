"""百度搜索引擎适配器(HTML 解析)。

结构(新 coso UI): 结果容器 div 带 mu="真实URL" 属性,内部 <h3 class="cosc-title ...">
  > <a href="http://www.baidu.com/link?url=...">(跳转链接)</a> + 标题文本在
  <span class="tts-b-hl"> 里(含 <em> 高亮)。
策略: 按 mu=" 切块,取真实 URL(跳过百度内部域名/nourl/广告),块内首个 h3 提取标题。
"""

from __future__ import annotations

import re

from ..models import SearchResult
from .base import EngineError, SearchEngine, strip_tags

H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
ABSTRACT_RE = re.compile(
    r'<div class="(?:c-abstract|cosc-desc|content-right_[^"]*)"[^>]*>(.*?)</div>', re.S
)
# 百度站内/无意义链接
_SKIP_MU = re.compile(r"baidu\.com|nourl|hao123|^https?:////", re.I)


class BaiduEngine(SearchEngine):
    name = "baidu"
    label = "百度"

    def search(self, query: str, per_engine: int = 10) -> list[SearchResult]:
        resp = self._get(
            "https://www.baidu.com/s",
            params={"wd": query},
            referer="https://www.baidu.com/",
        )
        self._check(resp)
        html = resp.text
        # 百度可能返回安全验证页(短页面,无 mu 结果块)
        if len(html) < 20000:
            raise EngineError("百度返回验证页/空结果")
        results: list[SearchResult] = []
        # 按 mu=" 切块:每块 = 一个结果容器
        chunks = re.split(r'mu="', html)[1:]
        for chunk in chunks:
            mu = chunk.split('"', 1)[0]
            if not mu.startswith("http") or _SKIP_MU.search(mu):
                continue
            h3 = H3_RE.search(chunk)
            title = strip_tags(h3.group(1)) if h3 else ""
            sn = ABSTRACT_RE.search(chunk)
            snippet = strip_tags(sn.group(1)) if sn else ""
            r = self._mk(title, mu, snippet, len(results) + 1)
            if r:
                results.append(r)
            if len(results) >= per_engine:
                break
        return self._dedupe_in_engine(results)
