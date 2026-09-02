# -*- coding: utf-8 -*-
"""DSH 定向技能 —— 5 个高价值站点的确定性提取器(规则实现,AI 负责何时调用)。

架构(单一实现源,三处消费):
1. 纯函数实现(可离线单测):各技能 `run(args: dict) -> dict`;
2. `@tool` 注册进 Resource Hub 技能表(import 即注册,AI/任务管线可调);
3. FastAPI `/api/v1/skills/run` 白名单暴露 → DSH `skill_invoke` 工具执行。

技能清单(与用户契约规范对齐):
- biquge_novel_crawler       笔趣阁小说全本/章节提取 → 单体 TXT
- haowallpaper_4k_extractor  哲风壁纸详情页 → CDN 原图直链/下载
- gdgame_resource_fetcher    gdgame 公益游戏库:详情网盘直链+解压密码 / 搜索
- littleskin_texture_extractor LittleSkin CSL/Yggdrasil API:皮肤/披风 PNG
- annas_archive_book_finder  Anna's Archive / Libgen 图书寻源(多镜像轮询+Libgen兜底)

诚实边界:annas-archive 有 FingerprintJS 指纹门 + 全球风控,纯 HTTP 常被挡 →
按规范抛 CLOUDFLARE_BLOCKED 让上层回退浏览器 Agent;Libgen 旧式 index.php
(非 search.php)是实际可用的 HTTP 搜索入口。

依赖:requests + proxy(Resource Hub 网络层)+ 可选 Pillow(图片尺寸);
纯标准库解析(正则),不引 bs4。
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin

import requests

from proxy import proxies
from skills.core import ToolResult, tool

__all__ = [
    "CloudflareBlockedError",
    "SCHEMAS",
    "run_biquge_novel_crawler", "run_haowallpaper_4k_extractor",
    "run_gdgame_resource_fetcher", "run_littleskin_texture_extractor",
    "run_annas_archive_book_finder",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class CloudflareBlockedError(RuntimeError):
    """目标站点了 CF/WAF/指纹门,纯 HTTP 无法过 —— 上层应回退浏览器 Agent。"""

    code = "CLOUDFLARE_BLOCKED"


def _headers(referer: str = "") -> dict:
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    if referer:
        h["Referer"] = referer
    return h


def _http_get(url: str, timeout: float = 20.0, referer: str = "",
              allow_redirects: bool = True, **kw) -> requests.Response:
    """GET(走 Resource Hub 代理层);编码按 apparent_encoding 兜底。"""
    r = requests.get(url, headers=_headers(referer), timeout=timeout,
                     proxies=proxies(), allow_redirects=allow_redirects, **kw)
    ct = (r.headers.get("content-type") or "").lower()
    if "charset=" not in ct and "json" not in ct:
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def _http_post_json(url: str, payload, timeout: float = 20.0, referer: str = "") -> dict:
    r = requests.post(url, json=payload, headers=_headers(referer), timeout=timeout,
                      proxies=proxies())
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"POST {url} 非 JSON 响应 HTTP {r.status_code}: {r.text[:200]}")


_CF_MARKERS = re.compile(
    r"just a moment|cf-chl|cf_chl|challenge-platform|__cf_chl|cf-browser-verification|"
    r"fingerprintjs|FingerprintJS|verify you are human|安全验证",
    re.I,
)


def _check_blocked(text: str, source: str) -> None:
    if text and _CF_MARKERS.search(text[:8000]):
        raise CloudflareBlockedError(f"{source} 触发 Cloudflare/指纹验证,需浏览器 Agent 过盾")


def _is_html_error_page(r: requests.Response) -> bool:
    return r.status_code >= 400 or (not r.text or len(r.text) < 200)


# ================================================================ schema 契约
# 与用户提供的 DSH 规范对齐(JSON Schema 子集)
BIQUGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "book_url_or_id": {"type": "string", "description":
                           "笔趣阁小说主页 URL 或书号 ID,如 'https://www.biquges123.com/50045' 或 '50045'"},
        "max_chapters": {"type": "integer", "description":
                         "最大抓取章节数(试读/分批),0 或负数 = 全本"},
        "export_txt": {"type": "boolean", "description": "清洗合并导出单体 .txt,默认 True"},
        "output_dir": {"type": "string", "description": "TXT 保存目录,默认 downloads/novels"},
    },
    "required": ["book_url_or_id"],
}

HAOWALLPAPER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "url_or_id": {"type": "string", "description":
                      "哲风壁纸页面 URL 或图片 ID,如 'https://haowallpaper.com/homeViewLook/19580937584989056' 或 '19580937584989056'"},
        "download": {"type": "boolean", "description": "是否下载图片到本地(默认 True)"},
        "output_dir": {"type": "string", "description": "保存目录,默认 downloads/wallpapers"},
    },
    "required": ["url_or_id"],
}

GDGAME_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "description":
                   "gdgame 游戏链接(如 'https://gdgame.org/n-1/1159.html')或搜索关键词(如 '风暴崛起')"},
        "action": {"type": "string", "enum": ["get_game_detail", "search_games"],
                   "description": "'get_game_detail' 提取详情+网盘直链/密码;'search_games' 搜索全库"},
    },
    "required": ["target"],
}

LITTLESKIN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "username_or_hash": {"type": "string", "description":
                             "LittleSkin 玩家角色名(如 'Steve')或材质 32 位 UUID/hash"},
        "texture_type": {"type": "string", "enum": ["skin", "cape", "all"],
                         "description": "提取类型:skin/cape/all"},
        "download": {"type": "boolean", "description": "是否下载 PNG 到本地(默认 False)"},
        "output_dir": {"type": "string", "description": "保存目录,默认 downloads/skins"},
    },
    "required": ["username_or_hash"],
}

ANNAS_ARCHIVE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "书名/作者/ISBN/MD5"},
        "preferred_format": {"type": "string", "enum": ["any", "epub", "pdf", "mobi"],
                             "description": "偏好格式,默认 any"},
    },
    "required": ["query"],
}

SCHEMAS: dict[str, dict] = {
    "biquge_novel_crawler": BIQUGE_SCHEMA,
    "haowallpaper_4k_extractor": HAOWALLPAPER_SCHEMA,
    "gdgame_resource_fetcher": GDGAME_SCHEMA,
    "littleskin_texture_extractor": LITTLESKIN_SCHEMA,
    "annas_archive_book_finder": ANNAS_ARCHIVE_SCHEMA,
}

# 各技能可写目录根(经 API 暴露时限定在此,防乱写)
_DEFAULT_OUT = {
    "biquge_novel_crawler": "downloads/novels",
    "haowallpaper_4k_extractor": "downloads/wallpapers",
    "gdgame_resource_fetcher": "",
    "littleskin_texture_extractor": "downloads/skins",
    "annas_archive_book_finder": "",
}

_TIMEOUT = 25.0


# ================================================================ 1. 笔趣阁
_BIQUGE_HOSTS = ("www.biquges123.com", "biquges123.com", "www.biquge7.xyz")
_BIQUGE_BOOK_RE = re.compile(r"/(\d+)/?$")
# biquges123 正文容器:class=article(实测正文);class=text 是字号条,排除
_BIQUGE_CONTENT_RE = re.compile(
    r'<(?:div|article)[^>]+class=["\']?article["\']?[^>]*>(.*?)</(?:div|article)>', re.S | re.I)
# 正文噪音行(广告/导航/站点水印)
_BIQUGE_NOISE_LINE = re.compile(
    r"无弹窗地址|最新章节地址|txt下载地址|手机阅读地址|请收藏本站|收藏本站|"
    r"一秒记住|天才一秒|笔趣阁|最新网址|最快更新|章节错误|点此报送|加入书签|"
    r"推荐本书|返回目录|本章未完|本章完|www\.\w+\.(?:com|xyz|net|org|cc)|"
    r"←上一章|下一章→|本书由|搜索(?:请收藏)?|手机用户|请记住|笔趣阁手机版|"
    r"^(?:14px|16px|18px|20px|22px|24px|26px|28px|30px|默认|字体|字号)$",
    re.I,
)
_BIQUGE_TITLE_INLINE = re.compile(r".{1,40}_.{1,40}笔趣阁", re.I)


def _biquge_clean_chapter(html: str) -> str:
    """章节页 → 清洗正文:容器(article)优先,行级广告过滤,<title> 残留剔除。"""
    m = _BIQUGE_CONTENT_RE.search(html or "")
    if not m:
        from skills.novel.merger import extract_chapter_text

        body = extract_chapter_text(html or "")
    else:
        body = m.group(1)
    raw = re.sub(r"<script.*?</script>", "", body, flags=re.S | re.I)
    # 块级开闭标签都换行,再逐行过滤(否则 <p>…</p> 间无换行会把整段正文粘连成一行,
    # 一行里含任何广告词就整段被误杀 —— 线上事故:clean 输出为空/丢段)
    raw = re.sub(r"</?(?:p|div|br|li)[^>]*>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    import html as html_mod

    raw = html_mod.unescape(raw)
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if _BIQUGE_NOISE_LINE.search(ln) or _BIQUGE_TITLE_INLINE.search(ln):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _fetch_chapter(url: str, timeout: float) -> str:
    r = _http_get(url, timeout=timeout, referer="https://www.biquges123.com/")
    if r.status_code != 200:
        return ""
    return _biquge_clean_chapter(r.text)


def run_biquge_novel_crawler(args: dict) -> dict:
    """目录解析 → 并发拉取 → 广告过滤 → 单体 TXT(复用 merger 的目录/书名清洗)。"""
    from skills.novel.merger import clean_book_title, extract_chapter_links

    raw = str(args.get("book_url_or_id", "")).strip()
    if not raw:
        return {"ok": False, "error": "book_url_or_id 必填"}
    if raw.isdigit():
        book_url = f"https://www.biquges123.com/{raw}/"
    else:
        book_url = raw
    max_chapters = int(args.get("max_chapters") or 0)
    export_txt = bool(args.get("export_txt", True))
    out_dir = str(args.get("output_dir") or _DEFAULT_OUT["biquge_novel_crawler"])

    r = _http_get(book_url, timeout=_TIMEOUT)
    _check_blocked(r.text, "biquge")
    if r.status_code != 200 or len(r.text) < 2000:
        return {"ok": False, "error": f"书页抓取失败 HTTP {r.status_code}(站可能换域名/被拦)"}
    links = extract_chapter_links(r.text, r.url)
    if len(links) < 2:
        return {"ok": False, "error": f"未发现章节目录(找到 {len(links)} 个章节链接),URL 可能不是书主页"}
    mt = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S)
    title = clean_book_title(mt.group(1) if mt else "", fallback=raw)
    total = len(links)
    if max_chapters and max_chapters > 0:
        links = links[:max_chapters]

    # 并发拉取(4 线程,失败重试 1 次),结果保序
    texts: dict[int, str] = {}
    lock = threading.Lock()
    fetched = 0

    def grab(idx_url):
        i, u = idx_url
        for attempt in (0, 1):
            try:
                t = _fetch_chapter(u, timeout=_TIMEOUT)
                if len(t) >= 60:
                    return i, t
            except Exception:
                if attempt == 1:
                    return i, ""
                time.sleep(0.4)
        return i, ""

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(grab, (i, u)) for i, u in enumerate(links)]
        for f in as_completed(futs):
            try:
                i, t = f.result()
            except Exception:
                continue
            with lock:
                if t:
                    texts[i] = t
                    fetched += 1
    if fetched < 3:
        return {"ok": False, "error": f"有效章节不足({fetched}/{len(links)}),站点反爬或正文结构变化",
                "chapters": fetched, "total": total, "title": title}
    ordered = "\n\n".join(texts[i] for i in sorted(texts))
    preview = ordered[:300]
    if not export_txt:
        return {"ok": True, "chapters": fetched, "total": total, "title": title,
                "exported": False, "preview": preview}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fname = re.sub(r'[\\/:*?"<>|\r\n]', "_", title)[:80] + ".txt"
    path = out / fname
    body = f"{title}\n\n{ordered}\n"
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"写入失败: {e}", "chapters": fetched, "total": total}
    note = f"({fetched}/{total} 章)" if fetched < total else f"({total} 章全本)"
    return {"ok": True, "path": str(path), "title": title,
            "chapters": fetched, "total": total, "exported": True,
            "note": note, "size": path.stat().st_size}


# ================================================================ 2. 哲风壁纸
_HAO_ID_RE = re.compile(r"homeViewLook/(\d+)")
_HAO_CDN_RE = re.compile(
    r'https?://haowallpaper\.com/link/common/file/(?:previewFileImg|fileImg)/\d+', re.I)
_HAO_OG_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.I)


def _haowallpaper_image_url(html: str, page_url: str) -> Optional[str]:
    m = _HAO_OG_RE.search(html)
    if m and "haowallpaper.com" in m.group(1):
        return m.group(1)
    hits = _HAO_CDN_RE.findall(html)
    if hits:
        return hits[0]
    return None


def run_haowallpaper_4k_extractor(args: dict) -> dict:
    raw = str(args.get("url_or_id", "")).strip()
    if not raw:
        return {"ok": False, "error": "url_or_id 必填"}
    if raw.isdigit():
        page_url = f"https://haowallpaper.com/homeViewLook/{raw}"
    else:
        page_url = raw
    want_dl = bool(args.get("download", True))
    out_dir = str(args.get("output_dir") or _DEFAULT_OUT["haowallpaper_4k_extractor"])

    r = _http_get(page_url, timeout=_TIMEOUT)
    _check_blocked(r.text, "haowallpaper")
    if r.status_code != 200:
        return {"ok": False, "error": f"详情页抓取失败 HTTP {r.status_code}"}
    mt = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S)
    title = (mt.group(1).strip()[:100] if mt else "")
    cdn = _haowallpaper_image_url(r.text, r.url)
    if not cdn:
        return {"ok": False, "error": "页面未找到 CDN 图片直链(结构可能变更)"}
    mid = _HAO_ID_RE.search(page_url)
    file_id = mid.group(1) if mid else ""

    result: dict = {"ok": True, "file_id": file_id, "title": title,
                    "image_url": cdn, "resolved_url": cdn}
    if not want_dl:
        return result

    # 公开可达图像候选(预览图与裁剪图;站内 4K 原图下载可能需登录/积分 → 交付公开最大图)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mid = re.search(r"/(?:previewFileImg|getCroppingImg)/\d+", cdn)
    cands = []
    if mid:
        prefix = cdn[:mid.start()]
        fid_part = mid.group(0).split("/")[-1]
        for kind in ("getCroppingImg", "previewFileImg"):
            cands.append(f"{prefix}/link/common/file/{kind}/{fid_part}")
    if cdn not in cands:
        cands.append(cdn)

    best_path, best_px, best_url = None, 0, ""
    warnings: list[str] = []
    for u in dict.fromkeys(cands):
        try:
            img = _http_get(u, timeout=60, referer="https://haowallpaper.com/")
            if img.status_code != 200:
                continue
            data = img.content
            if not data or (not data.startswith(b"\xff\xd8") and not data.startswith(b"\x89PNG")
                            and not data.startswith(b"RIFF")):
                warnings.append(f"{u[:60]} 非图片")
                continue
            px = 0
            try:
                from PIL import Image
                import io

                with Image.open(io.BytesIO(data)) as im:
                    px = im.size[0] * im.size[1]
            except Exception:
                px = len(data)
            if px > best_px:
                if best_path:
                    try:
                        Path(best_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                ext = ".jpg" if data.startswith(b"\xff\xd8") else (".png" if data.startswith(b"\x89PNG") else ".webp")
                name = f"{file_id or int(time.time())}{ext}"
                path = out / name
                path.write_bytes(data)
                best_path, best_px, best_url = str(path), px, u
        except Exception as e:
            warnings.append(f"{u[:60]}: {type(e).__name__}")
    if not best_path:
        return {**result, "ok": False, "error": "公开图像下载失败: " + " ".join(warnings)[:160]}
    width = height = 0
    try:
        from PIL import Image

        with Image.open(best_path) as im:
            width, height = im.size
    except Exception:
        pass
    note = ""
    if width and width < 1920:
        note = "站内 4K 原图可能需登录/积分;已交付页面公开可达的最大图"
    result.update({"path": best_path, "size": Path(best_path).stat().st_size,
                   "width": width, "height": height, "image_url": best_url,
                   "note": note if not width else (note or f"{width}x{height}")})
    if warnings and not note:
        result["warnings"] = warnings
    return result


# ================================================================ 3. gdgame
_GDGAME_ID_RE = re.compile(r"/n-\d+/(\d+)\.html")
_GDGAME_LINK_RE = re.compile(r'href="(/n-\d+/\d+\.html)"[^>]*>([^<]{2,60})</a>')
_GDGAME_PANS = (
    ("baidu", re.compile(r"(pan\.baidu\.com/s/[0-9A-Za-z_-]+)", re.I)),
    ("quark", re.compile(r"(pan\.quark\.cn/s/[0-9A-Za-z_-]+)", re.I)),
    ("uc", re.compile(r"(drive\.uc\.cn/s/[0-9A-Za-z_-]+[^\"'<\s]*)", re.I)),
)
_GDGAME_CODE_RE = re.compile(
    r"(?:提取码|解压密码|访问密码|提取密码|密码)[:：]?\s*([0-9A-Za-z]{3,10})")
_GDGAME_SEARCH_PAGE = "https://gdgame.org/?s={kw}"


def _gdgame_pans(html: str) -> list[dict]:
    out: list[dict] = []
    for provider, pat in _GDGAME_PANS:
        for u in dict.fromkeys(pat.findall(html)):
            u = u if u.startswith("http") else "https://" + u
            out.append({"provider": provider, "url": u})
    # 去重(同 url)
    seen, uniq = set(), []
    for p in out:
        if p["url"] not in seen:
            seen.add(p["url"])
            uniq.append(p)
    return uniq


def _gdgame_extract_code(html: str) -> str:
    m = _GDGAME_CODE_RE.search(html)
    return m.group(1) if m else ""


def _parse_gdgame_list(html: str, base_url: str, limit: int = 20) -> list[dict]:
    """gdgame ?s= 结果页 → [{title, url}]。

    标题容器块模型:<h2|h3|h4>…</h2> 为一个条目块,块内取游戏链接与文本
    (链接文本常为空,标题在 <a> 外的块文本里;实测 gdgame 列表即此结构)。
    """
    seen, results = set(), []
    for m in re.finditer(r"<(h2|h3|h4)[^>]*>(.*?)</\1>", html or "", re.S | re.I):
        block = m.group(2)
        href = re.search(r'href="(/n-\d+/\d+\.html)"', block)
        if not href:
            continue
        u = urljoin(base_url, href.group(1))
        if u in seen:
            continue
        seen.add(u)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", block)).strip()
        if not title:
            alts = re.findall(r'(?:alt|title)="([^"]{3,80})"', block)
            title = (alts[-1] if alts else "").strip()
        if not title:
            continue
        results.append({"title": title[:120], "url": u})
        if len(results) >= limit:
            break
    return results


def run_gdgame_resource_fetcher(args: dict) -> dict:
    target = str(args.get("target", "")).strip()
    action = args.get("action") or "get_game_detail"
    if not target:
        return {"ok": False, "error": "target 必填"}

    if action == "get_game_detail":
        if target.isdigit():
            url = f"https://gdgame.org/n-1/{target}.html"
        elif re.match(r"^https?://", target):
            url = target
        else:
            url = target if "gdgame.org" in target else f"https://gdgame.org/{target.lstrip('/')}"
        r = _http_get(url, timeout=_TIMEOUT)
        _check_blocked(r.text, "gdgame")
        if r.status_code != 200:
            return {"ok": False, "error": f"详情页抓取失败 HTTP {r.status_code}"}
        mt = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S)
        title = (mt.group(1).strip()[:160] if mt else "")
        title = re.sub(r"\s*[-_|]\s*gdgame\s*$", "", title, flags=re.I)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", r.text, re.S)
        if h1:
            t2 = re.sub(r"<[^>]+>", "", h1.group(1)).strip()
            if t2:
                title = t2[:160]
        mid = _GDGAME_ID_RE.search(url)
        gid = mid.group(1) if mid else ""
        pans = _gdgame_pans(r.text)
        code = _gdgame_extract_code(r.text)
        return {"ok": True, "id": gid, "title": title, "page_url": url,
                "pan_links": pans, "extract_code": code,
                "note": f"找到 {len(pans)} 个网盘" if pans else "未找到网盘链接(可能需登录/页面结构变化)"}

    # search_games(WordPress ?s=):标题在链接前的 <h2-h4>,链接文本常为空
    q = quote(target)
    r = _http_get(_GDGAME_SEARCH_PAGE.format(kw=q), timeout=_TIMEOUT)
    _check_blocked(r.text, "gdgame")
    if r.status_code != 200:
        return {"ok": False, "error": f"搜索失败 HTTP {r.status_code}"}
    results = _parse_gdgame_list(r.text, r.url, limit=20)
    if not results:
        return {"ok": False, "error": "搜索无结果(关键词过偏或站内搜索结构变化)"}
    return {"ok": True, "query": target, "results": results, "total": len(results)}


# ================================================================ 4. LittleSkin
_LS_PROFILES_API = "https://littleskin.cn/api/yggdrasil/api/profiles/minecraft"
_LS_SESSION_API = "https://littleskin.cn/api/yggdrasil/sessionserver/session/minecraft/profile/{uuid}"
_LS_UUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_LS_PNG_RE = re.compile(r"^data:image/png;base64,(.+)$", re.S)


def _ls_lookup(uuid_or_name: str) -> tuple[str, str]:
    """输入 → (uuid, name)。32hex 当 uuid;名字走 profiles API。"""
    if _LS_UUID_RE.match(uuid_or_name):
        return uuid_or_name.lower(), ""
    r = requests.post(_LS_PROFILES_API, json=[uuid_or_name],
                      headers=_headers("https://littleskin.cn/"), timeout=_TIMEOUT,
                      proxies=proxies())
    if r.status_code != 200:
        raise RuntimeError(f"profiles API HTTP {r.status_code}: {r.text[:150]}")
    arr = r.json()
    if not arr:
        raise RuntimeError(f"LittleSkin 未找到角色 {uuid_or_name!r}(可能未注册)")
    return arr[0]["id"], arr[0].get("name", uuid_or_name)


def run_littleskin_texture_extractor(args: dict) -> dict:
    raw = str(args.get("username_or_hash", "")).strip()
    tex_type = args.get("texture_type") or "all"
    want_dl = bool(args.get("download", False))
    out_dir = str(args.get("output_dir") or _DEFAULT_OUT["littleskin_texture_extractor"])

    uuid, name = _ls_lookup(raw)
    r = _http_get(_LS_SESSION_API.format(uuid=uuid), timeout=_TIMEOUT,
                  referer="https://littleskin.cn/")
    _check_blocked(r.text, "littleskin")
    if r.status_code != 200:
        return {"ok": False, "error": f"session API HTTP {r.status_code}: {r.text[:120]}"}
    j = r.json()
    name = name or j.get("name") or raw
    props = j.get("properties") or []
    textures: dict = {}
    for p in props:
        if p.get("name") != "textures":
            continue
        try:
            val = base64.b64decode(p.get("value", "") + "==")
            textures = (json.loads(val) or {}).get("textures") or {}
        except Exception:
            continue
    skin_url = (textures.get("SKIN") or {}).get("url") or ""
    cape_url = (textures.get("CAPE") or {}).get("url") or ""
    skin_meta = (textures.get("SKIN") or {}).get("metadata") or {}
    result: dict = {"ok": True, "uuid": uuid, "name": name,
                    "has_skin": bool(skin_url), "has_cape": bool(cape_url)}
    if tex_type in ("skin", "all") and skin_url:
        result["skin_url"] = skin_url
        result["skin_model"] = (skin_meta or {}).get("model", "steve")
    if tex_type in ("cape", "all") and cape_url:
        result["cape_url"] = cape_url

    files: list[str] = []
    if want_dl:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        targets = []
        if tex_type in ("skin", "all") and skin_url:
            targets.append(("skin", skin_url))
        if tex_type in ("cape", "all") and cape_url:
            targets.append(("cape", cape_url))
        for kind, u in targets:
            try:
                img = _http_get(u, timeout=60, referer="https://littleskin.cn/")
                data = img.content
                if not data.startswith(b"\x89PNG"):
                    # data URI 变体(个别材质以 base64 内嵌)
                    m = _LS_PNG_RE.match(data.decode("utf-8", "ignore"))
                    if not m:
                        result.setdefault("warnings", []).append(f"{kind}: 下载内容非 PNG")
                        continue
                    data = base64.b64decode(m.group(1))
                fname = f"{name or uuid}_{kind}.png"
                fname = re.sub(r'[\\/:*?"<>|]', "_", fname)
                path = out / fname
                path.write_bytes(data)
                files.append(str(path))
            except Exception as e:
                result.setdefault("warnings", []).append(f"{kind}: {type(e).__name__}: {str(e)[:80]}")
        result["files"] = files
    if not result.get("has_skin") and not result.get("has_cape"):
        result["ok"] = False
        result["error"] = "该角色没有任何可用材质(需 LittleSkin 用户上传皮肤/披风)"
    return result


# ================================================================ 5. Anna's Archive / Libgen
_ANNAS_MIRRORS = (
    "https://annas-archive.li",
    "https://annas-archive.org",
    "https://annas-archive.se",
)
_LIBGEN_MIRRORS = (
    "https://libgen.li",
    "https://libgen.vg",
    "https://libgen.is",
    "https://libgen.rs",
    "https://libgen.st",
)
_ANNAS_MD5_RE = re.compile(r"/md5/([0-9a-f]{32})")
_LIBGEN_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_LIBGEN_MD5_RE = re.compile(r"[?&]md5=([0-9a-fA-F]{32})", re.I)
_LIBGEN_EDITION_RE = re.compile(
    r'<a[^>]+title="[^"]*\|\s*([^"]{2,120})"[^>]+href="([^"]*edition\.php\?id=\d+)"', re.S | re.I)
_LIBGEN_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_FORMAT_ALIAS = {"epub": ".epub", "pdf": ".pdf", "mobi": ".mobi"}


def _clean_cell(cell: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()


def _annas_search(mirror: str, query: str, fmt: str) -> list[dict]:
    """annas 搜索(纯 HTTP):镜像多数有 FingerprintJS 门,过不了即抛 CLOUDFLARE_BLOCKED。"""
    url = f"{mirror}/search?q={quote(query)}&lang=zh"
    r = _http_get(url, timeout=_TIMEOUT, referer=mirror + "/")
    _check_blocked(r.text, f"annas {mirror}")
    if r.status_code != 200 or len(r.text) < 4000:
        raise CloudflareBlockedError(f"annas {mirror} 无有效响应(HTTP {r.status_code}, 可能需登录/指纹)")
    results: list[dict] = []
    # annas 结果行:每本书一个 div.line-clamp-2 + md5 链接 —— 宽松解析 md5 + 周边文本
    for m in _ANNAS_MD5_RE.finditer(r.text):
        md5 = m.group(1)
        seg = re.sub(r"<[^>]+>", " | ", r.text[max(0, m.start() - 800):m.end() + 200])
        seg = re.sub(r"\s+", " ", seg)
        title = ""
        mm = re.search(r"([^|]{4,120})(?:\s*\|\s*){1,3}", seg)
        if mm:
            title = mm.group(1).strip()
        results.append({"md5": md5, "title": title[:160], "source": "annas",
                        "detail_url": f"{mirror}/md5/{md5}"})
        if len(results) >= 10:
            break
    return results


def _parse_libgen_rows(html: str, mirror: str, fmt: str, limit: int = 10) -> list[dict]:
    """Libgen index.php 结果表 → 条目(标题/格式/大小/md5/详情/直链候选)。"""
    results: list[dict] = []
    for row in _LIBGEN_ROW_RE.findall(html or ""):
        md5m = _LIBGEN_MD5_RE.search(row)
        if not md5m:
            continue
        cells = [_clean_cell(c) for c in _LIBGEN_CELL.findall(row)]
        title = ""
        em = re.search(
            r'<a[^>]+title="([^"]*)"[^>]+href="[^"]*edition\.php\?id=\d+"[^>]*>', row, re.S | re.I)
        if em:
            title = em.group(1).split("|")[-1].split(" - ")[-1].strip()
        if not title or len(title) < 2:
            title = next((c for c in cells if 4 <= len(c) <= 160 and "&#" not in c
                          and "Time add" not in c), "")
        title = re.sub(r"^[^a-zA-Z\u4e00-\u9fff]{0,8}", "", title).strip()
        md5 = md5m.group(1).lower()
        size = next((c for c in cells if re.match(r"^[\d.]+ ?(kB|MB|GB)$", c)), "")
        ext = next((c.lower() for c in cells if c.lower() in (".epub", ".pdf", ".mobi", ".txt", ".djvu", ".azw3", ".fb2")), "")
        if not ext:
            ext = next((c.lower().lstrip(".") for c in cells if c.lower().rstrip(".") in ("epub", "pdf", "mobi", "txt", "djvu", "azw3")), "")
        if fmt != "any" and fmt not in ext:
            continue
        results.append({
            "title": (title or md5)[:200], "md5": md5,
            "size": size, "ext": ext, "source": "libgen",
            "detail_url": f"{mirror}/ads.php?md5={md5}",
            "download_candidates": [
                f"https://library.lol/main/{md5}",
                f"http://library.lol/main/{md5}",
            ],
        })
        if len(results) >= limit:
            break
    return results


def _libgen_search(mirror: str, query: str, fmt: str) -> list[dict]:
    """Libgen 旧式 index.php 搜索(实测可用入口,返回含 md5 的结果表)。"""
    url = f"{mirror}/index.php?req={quote(query)}&page=1"
    r = _http_get(url, timeout=_TIMEOUT, referer=mirror + "/")
    _check_blocked(r.text, f"libgen {mirror}")
    if r.status_code != 200 or len(r.text) < 3000:
        raise CloudflareBlockedError(f"libgen {mirror} 无有效响应(HTTP {r.status_code})")
    return _parse_libgen_rows(r.text, mirror, fmt)


def run_annas_archive_book_finder(args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    fmt = (args.get("preferred_format") or "any").lower()
    if not query:
        return {"ok": False, "error": "query 必填"}
    # 直接 md5 查询
    if re.match(r"^[0-9a-fA-F]{32}$", query):
        md5 = query.lower()
        return {"ok": True, "query": query, "source": "direct-md5",
                "results": [{"md5": md5, "title": "", "detail_url": f"https://libgen.li/ads.php?md5={md5}",
                             "download_candidates": [f"https://library.lol/main/{md5}"],
                             "source": "libgen"}]}

    blocked: list[str] = []
    # 1) annas 镜像轮询
    for mirror in _ANNAS_MIRRORS:
        try:
            hits = _annas_search(mirror, query, fmt)
            if hits:
                return {"ok": True, "query": query, "source": f"annas:{mirror}",
                        "results": hits, "total": len(hits),
                        "note": "annas 详情页下载需浏览器(指纹门);可用 md5 走 Libgen 直链"}
        except CloudflareBlockedError as e:
            blocked.append(str(e))
        except Exception as e:
            blocked.append(f"{mirror}: {type(e).__name__}")
    # 2) Libgen 兜底
    for mirror in _LIBGEN_MIRRORS:
        try:
            hits = _libgen_search(mirror, query, fmt)
            if hits:
                return {"ok": True, "query": query, "source": f"libgen:{mirror}",
                        "results": hits, "total": len(hits), "blocked": blocked[:2]}
        except CloudflareBlockedError as e:
            blocked.append(str(e))
        except Exception as e:
            blocked.append(f"{mirror}: {type(e).__name__}")
    return {"ok": False, "error": "所有镜像均失败", "blocked": blocked[:4],
            "hint": "Libgen 用 md5 详情页(ads.php?md5=)或 library.lol/main/{md5} 直链下载"}


# ================================================================ 统一入口
_RUNNERS = {
    "biquge_novel_crawler": run_biquge_novel_crawler,
    "haowallpaper_4k_extractor": run_haowallpaper_4k_extractor,
    "gdgame_resource_fetcher": run_gdgame_resource_fetcher,
    "littleskin_texture_extractor": run_littleskin_texture_extractor,
    "annas_archive_book_finder": run_annas_archive_book_finder,
}

_DESCRIPTIONS = {
    "biquge_novel_crawler": (
        "笔趣阁类小说全本/章节抓取并合成单体 TXT。输入书页 URL 或书号(如 biquges123.com 的 50045),"
        "自动解析章节目录→并发拉取正文→剥离广告/水印行→合并 <书名>.txt。max_chapters≤0 全本。"),
    "haowallpaper_4k_extractor": (
        "哲风壁纸(haowallpaper.com)详情页→底层 CDN 原图直链提取与下载。输入页面 URL 或图片 ID,"
        "download=True 时保存图片到本地(魔数校验+尺寸读取)。"),
    "gdgame_resource_fetcher": (
        "gdgame.org 公益单机游戏库。action=get_game_detail 提取游戏标题+百度/夸克/UC 网盘直链与"
        "提取码/解压密码;action=search_games 按关键词搜索全库返回结果列表。"),
    "littleskin_texture_extractor": (
        "LittleSkin(国内最大 Minecraft 皮肤站)官方 Yggdrasil/CSL API:按角色名或 32 位 UUID 查询"
        "玩家皮肤/披风 PNG 材质直链;download=True 时下载 PNG 到本地。Steve/Alex 等官方角色可用。"),
    "annas_archive_book_finder": (
        "Anna's Archive / Libgen 图书寻源:按书名/作者/ISBN/MD5 检索 epub/pdf/mobi,返回 md5 与"
        "下载候选直链。多镜像轮询;annas 指纹门被挡时抛 CLOUDFLARE_BLOCKED(上层应回退浏览器 Agent),"
        "Libgen 旧式 index.php 为实际可用 HTTP 入口。"),
}

_CATEGORY = {
    "biquge_novel_crawler": "fetch",
    "haowallpaper_4k_extractor": "fetch",
    "gdgame_resource_fetcher": "fetch",
    "littleskin_texture_extractor": "fetch",
    "annas_archive_book_finder": "search",
}


def _tool_result(d: dict) -> ToolResult:
    if d.get("ok"):
        msg = _render_success(d)
        return ToolResult.success(msg, data=d)
    msg = d.get("error") or d.get("hint") or "失败"
    extra = d.get("blocked")
    if extra:
        msg = f"{msg} (blocked: {'; '.join(extra[:2])})"
    return ToolResult.failure(msg, data=d)


def _render_success(d: dict) -> str:
    name = d.get("_tool", "")
    if name == "biquge_novel_crawler":
        if d.get("exported"):
            return (f"小说《{d.get('title')}》已导出: {d.get('path')} "
                    f"({d.get('chapters')}/{d.get('total')} 章, {d.get('size', 0)} 字节)")
        return (f"抓取完成(未导出): {d.get('chapters')}/{d.get('total')} 章。"
                f" 预览: {(d.get('preview') or '')[:80]}…")
    if name == "haowallpaper_4k_extractor":
        p = d.get("path")
        if p:
            return f"壁纸已下载: {p} ({d.get('size')} B, {d.get('width')}x{d.get('height')}) 原图: {d.get('image_url')}"
        return f"解析直链成功: {d.get('image_url')}"
    if name == "gdgame_resource_fetcher":
        if d.get("results") and "pan_links" not in d:
            return f"搜索到 {d.get('total')} 个结果: " + "; ".join(f"{r['title'][:30]}({r['url']})" for r in d["results"][:5])
        code = d.get("extract_code") or ""
        return (f"《{d.get('title')}》网盘 {len(d.get('pan_links') or [])} 个"
                + (f"(提取码/解压密码: {code})" if code else ""))
    if name == "littleskin_texture_extractor":
        parts = []
        if d.get("skin_url"):
            parts.append(f"skin: {d['skin_url']}")
        if d.get("cape_url"):
            parts.append(f"cape: {d['cape_url']}")
        files = " " + " ".join(d.get("files") or [])
        return f"角色 {d.get('name')} 材质: {'; '.join(parts) or '无'}{files}"
    if name == "annas_archive_book_finder":
        rs = d.get("results") or []
        lines = []
        for r0 in rs[:6]:
            line = f"- {r0.get('title', '')[:60]} [{r0.get('ext') or r0.get('source')}] md5={r0.get('md5')}"
            cands = (r0.get("download_candidates") or [])
            if cands:
                line += f"\n    {cands[0]}"
            lines.append(line)
        return f"找到 {d.get('total', len(rs))} 个结果:\n" + "\n".join(lines)
    return str(d)[:200]


def run_skill(name: str, args: dict) -> ToolResult:
    """统一入口(API/skill_invoke 用):校验 → 执行 → ToolResult。白名单名字。"""
    runner = _RUNNERS.get(name)
    if runner is None:
        return ToolResult.failure(f"未知技能 {name!r}", error="UNKNOWN_SKILL")
    from skills.core import validate_args

    violations = validate_args(SCHEMAS.get(name, {}), args or {})
    if violations:
        return ToolResult.failure(f"技能 {name} 参数不合法: {'; '.join(violations)}",
                                  error="INVALID_ARGS")
    try:
        d = runner(dict(args or {}))
        return _finalize(name, d)
    except CloudflareBlockedError as e:
        return ToolResult.failure(f"技能 {name} 失败: CLOUDFLARE_BLOCKED — {e}",
                                  error="CLOUDFLARE_BLOCKED")
    except Exception as e:
        return ToolResult.failure(f"技能 {name} 执行失败: {type(e).__name__}: {str(e)[:200]}",
                                  error=type(e).__name__)


def _finalize(name: str, d: dict) -> ToolResult:
    d["_tool"] = name
    if d.get("ok"):
        return ToolResult.success(_render_success(d), data=d)
    msg = d.get("error") or d.get("hint") or "失败"
    blocked = d.get("blocked")
    if blocked:
        msg = f"{msg} (blocked: {'; '.join(str(b)[:80] for b in blocked[:2])})"
    return ToolResult.failure(msg, data=d)


# ================================================================ @tool 注册
# @tool handler 的形参必须与 schema 属性同名(registry.invoke 按参数名注入)。
# 薄包装:具名参数 → 技能函数 dict → ToolResult。

def _biquge_tool(book_url_or_id, max_chapters: int = 0, export_txt: bool = True,
                 output_dir: str = "", **kw) -> ToolResult:
    return _finalize("biquge_novel_crawler", run_biquge_novel_crawler({
        "book_url_or_id": book_url_or_id, "max_chapters": max_chapters,
        "export_txt": export_txt, "output_dir": output_dir}))


def _haowallpaper_tool(url_or_id, download: bool = True, output_dir: str = "",
                       **kw) -> ToolResult:
    return _finalize("haowallpaper_4k_extractor", run_haowallpaper_4k_extractor({
        "url_or_id": url_or_id, "download": download, "output_dir": output_dir}))


def _gdgame_tool(target, action: str = "get_game_detail", **kw) -> ToolResult:
    return _finalize("gdgame_resource_fetcher", run_gdgame_resource_fetcher({
        "target": target, "action": action}))


def _littleskin_tool(username_or_hash, texture_type: str = "all",
                     download: bool = False, output_dir: str = "", **kw) -> ToolResult:
    return _finalize("littleskin_texture_extractor", run_littleskin_texture_extractor({
        "username_or_hash": username_or_hash, "texture_type": texture_type,
        "download": download, "output_dir": output_dir}))


def _annas_tool(query, preferred_format: str = "any", **kw) -> ToolResult:
    return _finalize("annas_archive_book_finder", run_annas_archive_book_finder({
        "query": query, "preferred_format": preferred_format}))


_TOOL_HANDLERS = {
    "biquge_novel_crawler": _biquge_tool,
    "haowallpaper_4k_extractor": _haowallpaper_tool,
    "gdgame_resource_fetcher": _gdgame_tool,
    "littleskin_texture_extractor": _littleskin_tool,
    "annas_archive_book_finder": _annas_tool,
}


def register_skills_tools() -> None:
    """注册 5 个技能进 Resource Hub 技能表(幂等)。"""
    from skills.core import get_registry

    reg = get_registry()
    for name, schema in SCHEMAS.items():
        if reg.has(name):
            continue
        tool(
            name,
            _DESCRIPTIONS[name],
            parameters=schema,
            category=_CATEGORY.get(name, "fetch"),
            timeout_ms=900_000,
            concurrency_safe=False,
        )(_TOOL_HANDLERS[name])


register_skills_tools()
