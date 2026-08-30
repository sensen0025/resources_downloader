"""广告过滤技能 — 对外 API。

    from skills.adblock import is_ad_url, ad_penalty, filter_ad_links

数据池在 rules.py;search/filter 与 pages/extractor 已集成(检索候选剔除/页面链接降权)。
"""

from .rules import (
    AD_DOMAIN_SUFFIXES,
    AD_TEXT_MARKS,
    AD_URL_PATTERNS,
    ad_penalty,
    ad_url_pattern_hit,
    filter_ad_links,
    has_ad_text,
    is_ad_domain,
    is_ad_url,
)

__all__ = [
    "is_ad_url",
    "is_ad_domain",
    "ad_url_pattern_hit",
    "has_ad_text",
    "ad_penalty",
    "filter_ad_links",
    "AD_DOMAIN_SUFFIXES",
    "AD_URL_PATTERNS",
    "AD_TEXT_MARKS",
]
__version__ = "0.1.0"
