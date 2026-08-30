"""页面内容提取 — 薄版 GenericFetcher(yt-dlp 教训:不堆站点特例)。

只做标准模式:
- 元数据:<title> / OpenGraph / JSON-LD(headline、作者、描述);
- 资源链接:文件直链(按扩展名)、网盘分享链接、下载按钮、iframe 嵌入;
- 相对路径解析、去跟踪参数、去重、打分排序。
站点特殊逻辑一律不进这里 —— 交给 classify 的 LLM 复核或上层 Agent。
"""

from __future__ import annotations

import html as html_mod
import json
import re
import urllib.parse
from typing import Optional

from search.normalize import (
    DIRECT_EXTENSIONS,
    file_ext,
    is_direct_file_url,
    is_pan_share,
    normalize_url,
)
from skills.adblock import has_ad_text, is_ad_url

from .models import ExtractedResource

__all__ = ["extract_metadata", "extract_resources", "analyze_html"]

# 下载按钮文本特征(中英)
_DOWNLOAD_TEXT = re.compile(
    r"download|获取|下载|立即|保存|save|\.zip$|\.rar$|直接|click here", re.I
)
# 排除明显无关的锚文本(导航/社交)
_SKIP_TEXT = re.compile(r"^(home|sign in|log in|register|登录|注册|menu|about|contact|搜索|首页)$", re.I)

_LINK_RE = re.compile(r'<a\b[^>]*>.*?</a>', re.S | re.I)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_IFRAME_RE = re.compile(r'<iframe[^>]*src="([^"]+)"', re.I)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_META_OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]*content="([^"]*)"', re.I)
# 大图属性优先级:懒加载库常用 data-original/data-src 存真实大图,src 只放缩略图
_IMG_URL_ATTRS = ("data-original", "data-src", "data-lazy-src", "data-url", "data-image", "src")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)="(og:title|og:description|description|author|og:image|og:type|og:url|keywords)"[^>]*content="([^"]*)"',
    re.I,
)
_LDJSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _strip(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _resolve(base: str, href: str) -> str:
    href = html_mod.unescape(href.strip())
    if not href or href.startswith(("javascript:", "mailto:", "#", "data:")):
        return ""
    return urllib.parse.urljoin(base, href)


def _walk_json(obj, wanted: tuple, found: list) -> None:
    """递归找 JSON-LD 里的 headline/name/author 等字段。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in wanted and isinstance(v, str) and v and not found:
                found.append(v)
            elif k in ("author", "creator") and isinstance(v, (str, dict)):
                if isinstance(v, str):
                    found.append(v)
                elif isinstance(v, dict) and v.get("name"):
                    found.append(v["name"])
            _walk_json(v, wanted, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json(item, wanted, found)


def extract_metadata(html: str) -> dict:
    """提取页面元数据:title / description / author / OG 字段 / JSON-LD。"""
    meta: dict = {}
    m = _TITLE_RE.search(html)
    if m:
        meta["title"] = _strip(m.group(1))[:300]
    for key, content in _META_RE.findall(html):
        key = key.lower()
        if key not in meta:
            meta[key] = html_mod.unescape(content.strip())[:500]
    # JSON-LD
    ld_values: list[str] = []
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(_strip(block))
        except json.JSONDecodeError:
            continue
        _walk_json(data, ("headline", "name", "description"), ld_values)
    if ld_values:
        meta.setdefault("ld_headline", ld_values[0])
        if len(ld_values) > 1:
            meta.setdefault("ld_description", ld_values[1])
    return meta


def _score_link(url: str, text: str) -> tuple[str, float]:
    """资源类型打分:真直链 > 网盘 > 下载按钮 > 普通链接。

    扩展名是提示不是证据:GitHub 仓库名以 .zip 结尾、/blob/ 预览页等
    会伪装成直链 —— 用 is_direct_file_url 权威判定。
    广告链接(adblock 数据池)→ 负分,聚合排序自然沉底且不会被当直链。
    """
    if is_ad_url(url) or has_ad_text(text):
        return "ad", -1.0
    if is_direct_file_url(url):
        return "direct_file", 3.0
    if is_pan_share(url):
        return "pan_share", 2.5
    if _DOWNLOAD_TEXT.search(text):
        return "download_button", 2.0
    return "link", 0.5


def _img_url(tag: str, base_url: str) -> tuple[str, float]:
    """从 <img> 标签提取大图 URL 与分数。

    优先级:data-* 懒加载大图(2.5)> srcset 最大候选(1.8)> src 缩略图(1.2)。
    """
    attrs: dict[str, str] = {}
    for m in re.finditer(r'([a-zA-Z_:][\w:.-]*)\s*=\s*"([^"]*)"', tag):
        attrs[m.group(1).lower()] = html_mod.unescape(m.group(2))
    # 1) 懒加载真实大图(data-original/data-src 等)
    for attr in _IMG_URL_ATTRS[:-1]:  # 排除纯 src
        v = attrs.get(attr, "")
        if v and v.startswith(("http://", "https://", "/", "./", "../")):
            url = _resolve(base_url, v)
            if url:
                return url, 2.5
    # 2) srcset 最大候选(大于 src 缩略图)
    srcset = attrs.get("srcset", "")
    if srcset:
        best_url, best_w = "", 0
        for cand in srcset.split(","):
            parts = cand.strip().split()
            if not parts:
                continue
            url = _resolve(base_url, parts[0])
            w = 0
            if len(parts) > 1:
                m = re.search(r"(\d+)w", parts[1])
                if m:
                    w = int(m.group(1))
            if url and w >= best_w:
                best_url, best_w = url, w
        if best_url:
            return best_url, 1.8
    # 3) 纯 src(缩略图)
    v = attrs.get("src", "")
    if v and v.startswith(("http://", "https://", "/", "./", "../")):
        url = _resolve(base_url, v)
        if url:
            return url, 1.2
    return "", 0.0


# 过滤页脚、备案、头像、营业执照、图标等通用噪音图片
_NOISE_IMG_RE = re.compile(
    r"license|beian|footer|avatar|header|logo|icon|badge|qrcode|weixin|alipay|"
    r"营业执照|资质|备案|认证|宣传|ad_|banner|spm|report",
    re.I,
)


def _is_noise_image(url: str, text: str = "") -> bool:
    return bool(_NOISE_IMG_RE.search(url) or _NOISE_IMG_RE.search(text))


def extract_resources(html: str, base_url: str, max_items: int = 40) -> list[ExtractedResource]:
    """提取资源候选链接(直链/网盘/下载按钮/iframe/图片/潜在资源页)，并附加富语义名称。"""
    found: dict[str, ExtractedResource] = {}
    
    # 提取页面标题作为默认兜底
    m_title = _TITLE_RE.search(html)
    page_title = _strip(m_title.group(1)) if m_title else ""

    def _get_tag_attr(tag: str, *attr_names: str) -> str:
        for attr in attr_names:
            m = re.search(rf'\b{attr}\s*=\s*"([^"]*)"', tag, re.I)
            if not m:
                m = re.search(rf"\b{attr}\s*=\s*'([^']*)'", tag, re.I)
            if m and m.group(1).strip():
                return html_mod.unescape(m.group(1).strip())
        return ""

    def _add(url: str, kind: str, score: float, text: str = "", name: str = "") -> None:
        norm = normalize_url(url)
        if not norm:
            return
        res_name = (name or text or page_title)[:120]
        prev = found.get(norm)
        if prev is None:
            found[norm] = ExtractedResource(
                url=norm, kind=kind, name=res_name,
                file_ext=file_ext(norm), text=text, score=score
            )
        elif score > prev.score:
            prev.kind, prev.score, prev.text = kind, score, text
            if res_name:
                prev.name = res_name

    for anchor in _LINK_RE.findall(html):
        m = _HREF_RE.search(anchor)
        if not m:
            continue
        url = _resolve(base_url, m.group(1))
        if not url:
            continue
        text = _strip(anchor)[:120]
        if text and _SKIP_TEXT.match(text.strip()):
            continue
        name = _get_tag_attr(anchor, "title", "aria-label", "data-name", "data-title")
        kind, score = _score_link(url, text)
        if kind == "ad":
            continue  # 广告链接(adblock 数据池)→ 不进候选
        _add(url, kind, score, text=text, name=name)

    for tag in _IMG_RE.findall(html):
        url, score = _img_url(tag, base_url)
        if url and score >= 1.0 and not _is_noise_image(url):
            name = _get_tag_attr(tag, "alt", "title", "aria-label", "data-name")
            _add(url, "image", score, name=name)

    for src in _IFRAME_RE.findall(html):
        url = _resolve(base_url, src)
        if url:
            _add(url, "iframe", 1.0)

    og = _META_OG_IMAGE_RE.search(html)
    if og:
        url = _resolve(base_url, html_mod.unescape(og.group(1)))
        if url and not _is_noise_image(url):
            _add(url, "image", 2.8, name=page_title)

    # 扫描 <model-viewer> 等 3D 组件标签与名称
    for mv_tag in re.findall(r'<model-viewer\b[^>]*>', html, re.I):
        m_src = re.search(r'src="([^"]+)"', mv_tag, re.I)
        if not m_src:
            continue
        url = _resolve(base_url, m_src.group(1))
        if url and is_direct_file_url(url):
            mv_name = _get_tag_attr(mv_tag, "alt", "title", "aria-label", "data-name", "data-model-name") or page_title
            _add(url, "direct_file", 3.0, text="3D Model Viewer", name=mv_name)

    # 扫描页面脚本/JSON状态中内嵌的 3D 模型直链
    for m_3d in re.findall(r'https?://[^\s"\'<>\\]+\.(?:glb|gltf|obj|fbx|stl|ply|blend|dem)', html, re.I):
        url = _resolve(base_url, m_3d)
        if url and is_direct_file_url(url):
            _add(url, "direct_file", 3.0, text="3D Embedded Asset", name=page_title)

    return sorted(found.values(), key=lambda r: r.score, reverse=True)[:max_items]


def analyze_html(html: str, base_url: str) -> tuple[dict, list[ExtractedResource]]:
    """一次完成 元数据 + 资源链接 提取。"""
    return extract_metadata(html), extract_resources(html, base_url)
