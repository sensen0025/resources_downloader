"""广告识别数据池 — 类 EasyList 精简版(零依赖,纯规则)。

用于资源获取链路过滤广告内容:检索结果、页面链接、下载探针。
分层判定(全部大小写不敏感):
1. 域名后缀黑名单(命中 = 直接丢弃,广告联盟/广告主);
2. URL 路径/参数模式(命中 = 高度可疑,扣大分);
3. 标题/摘要/链接文本标记(命中 = 扣分,防误杀正文里提到"广告"的资源)。

注意:统计类域名(cnzz/51.la/googletagmanager/mmstat)不是广告,刻意不拦。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

__all__ = [
    "is_ad_domain",
    "ad_url_pattern_hit",
    "has_ad_text",
    "is_ad_url",
    "ad_penalty",
    "filter_ad_links",
    "reload_pool",
    "pool_stats",
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

# ---------------------------------------------------------------- 开源数据池
# update_rules.py 从 EasyList 系/AdGuard 拉取并生成 data/rules.json;
# 运行时懒加载合并到内置规则,可用 RH_ADBLOCK_OFF=1 关闭。
_POOL_PATH = Path(__file__).resolve().parent / "data" / "rules.json"
_pool: dict | None = None
_pool_mtime: float = 0.0


def _host_dot_suffixes(host: str) -> list[str]:
    """host 在点边界处的所有后缀:cdn.ads.example.com → [cdn.ads.example.com, ads.example.com, example.com, com]。

    域名规则「example.com」命中 host 当且仅当 example.com ∈ 这些后缀 —— 集合查表 O(标签数)。
    """
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def reload_pool() -> dict:
    """重新加载开源数据池(文件变化才重读)。返回 {"domains","patterns","whitelist","domains_set"}。"""
    global _pool, _pool_mtime
    if not _POOL_PATH.exists():
        _pool = {"domains": [], "patterns": [], "whitelist": [], "domains_set": set()}
        return _pool
    try:
        mtime = _POOL_PATH.stat().st_mtime
        if _pool is not None and mtime == _pool_mtime:
            return _pool
        data = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
        domains = [d.lower() for d in data.get("domains", [])]
        _pool = {
            "domains": domains,
            "patterns": [p.lower() for p in data.get("patterns", [])],
            "whitelist": [w.lower() for w in data.get("whitelist", [])],
            "domains_set": set(domains),
        }
        _pool_mtime = mtime
    except Exception:
        _pool = {"domains": [], "patterns": [], "whitelist": [], "domains_set": set()}
    return _pool


def _match_pool_domain(host: str, pool: dict) -> bool:
    """host 是否命中池内广告域名:点边界后缀逐个 set 查表,微秒级。"""
    ds = pool.get("domains_set", set())
    return any(s in ds for s in _host_dot_suffixes(host))


def pool_stats() -> dict:
    pool = reload_pool()
    return {"domains": len(pool["domains"]), "patterns": len(pool["patterns"])}


def _pool_enabled() -> bool:
    return os.environ.get("RH_ADBLOCK_OFF", "0") != "1"


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
    # 开源数据池(EasyList 系)
    if _pool_enabled():
        pool = reload_pool()
        wl = pool["whitelist"]
        if host in wl or any(host.endswith("." + w) for w in wl):
            return False
        if _match_pool_domain(host, pool):
            return True
    return False


def ad_url_pattern_hit(url: str) -> bool:
    if any(p.search(url) for p in AD_URL_PATTERNS):
        return True
    # 开源数据池路径模式(子串匹配)
    if _pool_enabled():
        low = url.lower()
        pool = reload_pool()
        for pat in pool["patterns"]:
            if pat in low:
                return True
    return False


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
