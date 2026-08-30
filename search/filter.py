"""候选筛选与相关性打分 — 「筛选可用的」第一步:质量过滤 + 相关性排序。

- 丢弃:空标题/空 URL、跟踪域名、明显无关(URL 或标题不含任何查询词);
- 相关性:查询词在 标题(权重 2.0)/ URL(1.5)/ 摘要(1.0) 的命中数;
- 加分:网盘分享(+2.5)、直链扩展名(+2.0)、知名资源站(+1.0)、域名含查询词(+1.0)。
- 中英文统一处理:拉丁词按小写子串匹配,中文按子串匹配。
"""

from __future__ import annotations

import re
from typing import Iterable

from .models import SearchResult
from .normalize import (
    DIRECT_EXTENSIONS,
    file_ext,
    is_direct_file_url,
    is_pan_share,
    is_tracking_host,
)
from skills.adblock import ad_penalty, is_ad_url

__all__ = ["tokenize_query", "relevance_score", "filter_results", "rank_results"]

# 资源类知名域名(命中加分,不强过滤)
RESOURCE_HOST_HINTS = (
    "github.com", "archive.org", "gutenberg.org", "readthedocs", "docs.",
    "wikipedia.org", "stackoverflow.com", "csdn.net", "cnblogs.com",
    "jianshu.com", "zhihu.com", "bilibili.com", "pan.baidu.com",
    "aliyundrive", "alipan", "123pan", "lanzou", "quark", "115.com",
)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _cjk_bigrams(part: str) -> list[str]:
    """长中文词组拆成二元组:『凡人修仙传壁纸』→ 凡人/人修/修仙/仙传/传壁/壁纸。

    解决页面标题里词组被空格/标点切开(如『凡人修仙传 4K高清壁纸』)时,
    整词子串匹配不到的问题。二元组对"人""传"这类高频单字不敏感,
    不会把无关页面放进候选。
    """
    chars = [c for c in part if _CJK_RE.match(c)]
    if len(chars) >= 4:
        return ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)]
    return []


def tokenize_query(query: str) -> list[str]:
    """切分查询词:拉丁词小写;中文词组额外拆二元组。"""
    terms: list[str] = []
    for part in _TOKEN_RE.findall(query):
        low = part.lower()
        if low and low not in terms:
            terms.append(low)
        for bg in _cjk_bigrams(part):
            if bg not in terms:
                terms.append(bg)
    return terms


def _hit_count(terms: list[str], text: str) -> int:
    t = (text or "").lower()
    if not t:
        return 0
    return sum(1 for term in terms if term in t)


def relevance_score(query: str, r: SearchResult) -> float:
    terms = tokenize_query(query)
    if not terms:
        return 0.0
    score = 0.0
    score += 2.0 * _hit_count(terms, r.title)
    score += 1.5 * _hit_count(terms, r.url)
    score += 1.0 * _hit_count(terms, r.snippet)
    # 域名含查询词(如查询里有 github)
    host = (r.url.split("/")[2] if "//" in r.url else "").lower()
    if any(term in host for term in terms):
        score += 1.0
    # 资源类型加分
    if is_pan_share(r.url):
        score += 2.5
    # 资源类型加分(真直链才加,仓库主页/预览页不加)
    if is_direct_file_url(r.url):
        score += 2.0
    if any(h in host for h in RESOURCE_HOST_HINTS):
        score += 1.0
    # 广告过滤:广告域名/URL 模式扣大分,文本标记扣分(adblock 数据池)
    score -= ad_penalty(r.url, r.title, r.snippet)
    return round(score, 3)


def filter_results(query: str, results: Iterable[SearchResult]) -> list[SearchResult]:
    """丢弃明显无用的候选(含广告)。"""
    terms = tokenize_query(query)
    out: list[SearchResult] = []
    for r in results:
        if not r.title or not r.url:
            continue
        if is_tracking_host(r.url):
            continue
        if is_ad_url(r.url):   # 广告域名/强广告模式 → 直接剔除
            continue
        # 与查询零相关的直接丢(至少要命中一个词)
        if terms:
            hay = f"{r.title} {r.url} {r.snippet}"
            if not _hit_count(terms, hay):
                continue
        out.append(r)
    return out


def rank_results(query: str, results: Iterable[SearchResult]) -> list[SearchResult]:
    """打分并按相关性排序(同分按原顺序稳定)。"""
    scored = []
    for r in results:
        r.score = relevance_score(query, r)
        scored.append(r)
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored
