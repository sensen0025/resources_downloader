"""广告识别数据池 — 类 EasyList 精简版(零依赖,纯规则)。

用于资源获取链路过滤广告内容:检索结果、页面链接、下载探针。
分层判定(全部大小写不敏感):
1. 域名后缀黑名单(命中 = 直接丢弃,广告联盟/广告主);
2. URL 路径/参数模式(命中 = 高度可疑,扣大分);
3. 标题/摘要/链接文本标记(命中 = 扣分,防误杀正文里提到"广告"的资源)。

注意:统计类域名(cnzz/51.la/googletagmanager/mmstat)不是广告,刻意不拦。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = [
    "is_ad_domain",
    "ad_url_pattern_hit",
    "has_ad_text",
    "is_ad_url",
    "ad_penalty",
    "filter_ad_links",
]

# 1) 广告域名后缀(host 等于或以此结尾即命中)
AD_DOMAIN_SUFFIXES: tuple[str, ...] = (
    # Google/通用广告联盟
    "adservice.google.com", "doubleclick.net", "googlesyndication.com",
    "googleadservices.com", "googletagservices.com", "google-analytics.com",
    # 海外程序化广告
    "adnxs.com", "adform.net", "criteo.com", "taboola.com", "outbrain.com",
    "adroll.com", "adsafeprotected.com", "rubiconproject.com", "pubmatic.com",
    "openx.net", "spotxchange.com", "smartadserver.com", "mgid.com",
    # 中文广告联盟/广告主
    "cpro.baidu.com", "pos.baidu.com", "cpro2.baidu.com", "union.baidu.com",
    "adsame.com", "tanx.com", "alimama.com", "mmstat.com", "adkmob.com",
    "yj.qq.com", "adm.qq.com", "ads.qq.com", "gamegt.com", "dashang.qq.com",
    "union.uc.cn", "adpush.cn", "miaozhen.com", "baidustatic.com",
    # 通用形态: ad./ads./ad- 子域
    "ad.", "ads.", "ad-", "-ads.",
)

# 2) URL 路径/参数模式(命中高度可疑)
AD_URL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"/ad(?:s|click|vert|service)?[/?]", re.I),
    re.compile(r"[?&]ad(?:id|url|click)?=", re.I),
    re.compile(r"/banner(?:s)?[/?]", re.I),
    re.compile(r"/promot(?:e|ion)?[/?]", re.I),
    re.compile(r"/tuiguang[/?]", re.I),
    re.compile(r"/advert(?:is(?:e|ing))?[/?]", re.I),
    re.compile(r"/sponsor(?:ed)?[/?]", re.I),
    re.compile(r"/aff(?:iliate)?[/?]", re.I),
    re.compile(r"/gg[_/-]", re.I),
    re.compile(r"/redirect[?/].*(?:ad|adv|banner)", re.I),
)

# 3) 文本标记(标题/摘要/链接文本)
AD_TEXT_MARKS: tuple[str, ...] = (
    "广告", "推广", "赞助", "广告位", "广告招商", "banner", "advertisement",
    "advert", "sponsored", "promoted", "promotion", "ads by",
)

# 防误杀:标题同时含资源关键词与广告字样(如「××漫画-广告」)时只扣分不丢弃


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def is_ad_domain(url: str) -> bool:
    host = _host_of(url)
    if not host:
        return False
    for suf in AD_DOMAIN_SUFFIXES:
        if host == suf or host.endswith("." + suf.lstrip(".")):
            return True
        # 通配形态: ad.* / ads.* / ad-xxx
        if suf in ("ad.", "ads.", "ad-", "-ads."):
            if host.startswith(suf) or host.endswith(suf):
                return True
    return False


def ad_url_pattern_hit(url: str) -> bool:
    return any(p.search(url) for p in AD_URL_PATTERNS)


def has_ad_text(*texts: str) -> bool:
    low = " ".join(t or "" for t in texts).lower()
    return any(mark in low for mark in AD_TEXT_MARKS)


def is_ad_url(url: str) -> bool:
    """权威判定:广告域名或强广告 URL 模式 → 直接丢弃。"""
    return is_ad_domain(url) or ad_url_pattern_hit(url)


def ad_penalty(url: str, title: str = "", snippet: str = "") -> float:
    """广告扣分(relevance_score 用):域名 -3.0 / URL 模式 -2.0 / 文本标记 -1.0。"""
    penalty = 0.0
    if is_ad_domain(url):
        penalty += 3.0
    elif ad_url_pattern_hit(url):
        penalty += 2.0
    if has_ad_text(title, snippet):
        penalty += 1.0
    return penalty


def filter_ad_links(urls: list[str]) -> list[str]:
    """页面提取的候选链接过广告池。"""
    return [u for u in urls if not is_ad_url(u)]
