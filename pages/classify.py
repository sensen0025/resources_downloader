"""页面分级 — 规则启发式为主,不确定交 LLM 复核(双轨,不堆特例)。

规则只认标准信号:探测结果(直链/反爬/失效)、网盘域名、下载链接存在性、
登录表单特征、聚合页特征。任何不确定 → `verify_with_llm` 一次调用复核。
"""

from __future__ import annotations

import re
from typing import Optional

from search.normalize import is_pan_share
from search.probe import (
    KIND_BLOCKED, KIND_DEAD, KIND_DIRECT_FILE, KIND_PAN_SHARE,
)

from .models import ExtractedResource, PageAnalysis, PageClass, PageInfo

__all__ = ["classify_page", "verify_with_llm"]

_LOGIN_MARKERS = re.compile(
    r"sign in|log in|login|登录|sign up|注册|forgot password|password|密码|"
    r"<input[^>]+type=[\"']password[\"']|verification code|验证码",
    re.I,
)
_AGGREGATOR_MARKERS = re.compile(
    r"schematics?|downloads?|library|collection|archive|合集|列表|目录|分类|下一页|next page|page \d+",
    re.I,
)
_CF_MARKERS = re.compile(r"just a moment|cf-challenge|cf_chl|verify you are human|安全验证", re.I)


def _has_direct_file(resources: list[ExtractedResource]) -> bool:
    return any(r.kind == "direct_file" for r in resources)


def _has_pan(resources: list[ExtractedResource]) -> bool:
    return any(r.kind == "pan_share" or is_pan_share(r.url) for r in resources)


def _has_download_button(resources: list[ExtractedResource]) -> bool:
    return any(r.kind == "download_button" for r in resources)


def classify_page(page: PageInfo, analysis: PageAnalysis,
                  probe_kind: str = "") -> PageAnalysis:
    """按规则分级页面;probe_kind 传入 search.probe 的探测分类可提高精度。"""
    a = analysis
    url = page.url or a.page_url
    html_head = page.html[:20000] if page.html else ""
    title = (a.title or page.title or "").lower()

    # 1) 探测信号优先
    if probe_kind == KIND_DIRECT_FILE:
        a.page_class, a.reason = PageClass.DIRECT_FILE, "探测为直链文件"
        return a
    if probe_kind == KIND_BLOCKED or _CF_MARKERS.search(html_head):
        a.page_class, a.reason = PageClass.BLOCKED, "反爬/验证码墙"
        return a
    if probe_kind == KIND_DEAD or page.status in (404, 410):
        a.page_class, a.reason = PageClass.DEAD, f"失效(HTTP {page.status})"
        return a

    # 2) 网盘域名
    if _has_pan(a.resources) or is_pan_share(url):
        a.page_class, a.reason = PageClass.PAN_SHARE, "含网盘分享链接"
        return a

    # 3) 页面内资源信号
    if _has_direct_file(a.resources):
        a.page_class, a.reason = PageClass.DOWNLOAD_PAGE, "含文件直链"
        return a
    if _has_download_button(a.resources) and a.resources:
        a.page_class, a.reason = PageClass.DOWNLOAD_PAGE, "含下载按钮"
        return a

    # 4) 登录墙(无直接资源 + 登录特征)
    a.needs_login = bool(_LOGIN_MARKERS.search(html_head))
    if a.needs_login:
        a.page_class, a.reason = PageClass.LOGIN_REQUIRED, "检测到登录/验证码特征"
        return a

    # 5) 聚合页(标题/正文特征 + 链接丰富)
    if _AGGREGATOR_MARKERS.search(title) or len(a.resources) >= 8:
        a.page_class, a.reason = PageClass.AGGREGATOR, "疑似列表/索引页"
        return a

    a.page_class, a.reason = PageClass.UNKNOWN, "未匹配规则,可交 LLM 复核"
    return a


def verify_with_llm(analysis: PageAnalysis, llm=None) -> PageAnalysis:
    """LLM 复核(仅对 UNKNOWN 或低置信页面调用一次,防特例堆叠)。"""
    if analysis.page_class != PageClass.UNKNOWN:
        return analysis
    if llm is None:
        try:
            from agent.llm import LLMClient

            llm = LLMClient()
        except Exception:
            return analysis  # 无 LLM key 时维持 UNKNOWN
    prompt = (
        "你是页面分析器。根据以下页面信息判断它属于哪一类:\n"
        "direct_file(本身就是文件)/ download_page(页面内有下载链接)/ "
        "pan_share(网盘分享页)/ login_required(需登录或验证码才能拿到资源)/ "
        "aggregator(列表/索引页)/ dead / blocked / unknown\n"
        f"标题: {analysis.title!r}\n"
        f"描述: {(analysis.description or '')[:200]!r}\n"
        f"提取到的资源链接({len(analysis.resources)}):\n"
        + "\n".join(f"  - [{r.kind}] {r.url} ({r.text[:50]})" for r in analysis.resources[:10])
        + "\n只输出一个 JSON: {\"class\": \"...\", \"needs_login\": bool, \"reason\": \"一句话\"}"
    )
    try:
        data = llm.chat_json([{"role": "user", "content": prompt}], max_tokens=400)
        cls = str(data.get("class", "")).strip().lower()
        if cls in (c.value for c in PageClass):
            analysis.page_class = PageClass(cls)
            analysis.needs_login = bool(data.get("needs_login", analysis.needs_login))
            analysis.reason = str(data.get("reason", ""))[:200]
            analysis.llm_verified = True
    except Exception:
        pass
    return analysis
