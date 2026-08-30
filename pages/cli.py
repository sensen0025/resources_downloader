"""页面分析服务入口 — fetch → analyze → classify 一条链路。

提供:
- `analyze_page(url, probe=True, llm=False) -> PageAnalysis`:轻量探测 + 深度分析 + 分级
- CLI:`python -m pages.cli <url> [--probe] [--llm] [--json]`
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .classify import classify_page, verify_with_llm
from .extractor import analyze_html
from .fetcher import fetch_page
from .models import ExtractedResource, PageAnalysis, PageInfo

__all__ = ["analyze_page", "analyze_document"]


def analyze_page(url: str, probe: bool = True, llm: bool = False,
                 impersonate: bool = False) -> PageAnalysis:
    """抓取并分析一个页面。probe=True 先做轻量探测(直链/反爬/失效快速判定)。"""
    page = fetch_page(url, impersonate=impersonate)
    if not page.ok and page.error:
        return PageAnalysis(page_url=url, page_class=__import__("pages.models", fromlist=["PageClass"]).PageClass.UNKNOWN,
                            reason=f"抓取失败: {page.error}")
    return analyze_document(page.html or "", page.url, probe=probe, llm=llm)


def analyze_document(html: str, url: str, probe: bool = False,
                     llm: bool = False) -> PageAnalysis:
    """分析一份已拿到的 HTML 文档(浏览器会话/缓存场景用,不再自己抓取)。"""
    meta, resources = analyze_html(html, url)
    analysis = PageAnalysis(
        page_url=url,
        title=meta.get("title", ""),
        description=meta.get("og:description") or meta.get("description") or meta.get("ld_description", ""),
        author=meta.get("author") or meta.get("ld_author", ""),
        meta=meta,
        resources=resources,
    )
    probe_kind = ""
    if probe:
        try:
            from search.probe import probe_url

            pinfo = probe_url(url)
            probe_kind = pinfo.kind
            if pinfo.kind == "direct_file" and url not in {r.url for r in resources}:
                from search.normalize import file_ext as _fe

                resources.insert(0, ExtractedResource(url=url, kind="direct_file",
                                                      file_ext=_fe(url), score=3.5))
        except Exception:
            pass
    classify_page(PageInfo(url=url, html=html, status=200), analysis, probe_kind=probe_kind)
    if llm:
        verify_with_llm(analysis)
    return analysis


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pages", description="页面分析: 抓取→提取→分级")
    ap.add_argument("url", help="要分析的页面 URL")
    ap.add_argument("--no-probe", action="store_true", help="跳过轻量探测")
    ap.add_argument("--llm", action="store_true", help="对 UNKNOWN 页面做 LLM 复核")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)

    analysis = analyze_page(args.url, probe=not args.no_probe, llm=args.llm)
    if args.json:
        print(json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"页面: {analysis.page_url}")
    print(f"分级: {analysis.page_class.value}  (reason: {analysis.reason})")
    if analysis.needs_login:
        print("⚠️  需要登录/验证码")
    if analysis.title:
        print(f"标题: {analysis.title[:120]}")
    if analysis.description:
        print(f"描述: {analysis.description[:150]}")
    print(f"资源候选: {len(analysis.resources)} 个")
    for i, r in enumerate(analysis.best_resources[:15], start=1):
        print(f"  {i:>2}. [{r.kind}] ({r.score:.1f}) {r.url[:100]}" + (f"  ← {r.text[:40]}" if r.text else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
