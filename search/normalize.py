"""URL 规范化与去重键 — 对齐 crawlee RequestQueue 的 uniqueKey 思路。

要点:
- 去跟踪参数(utm_* / spm / ref 等),保留有意义参数(网盘分享的 pwd/提取码);
- 统一 scheme/host 大小写、去默认端口、去 fragment、去尾斜杠;
- 网盘分享域名识别(probe 的快速通道,不用发请求)。
"""

from __future__ import annotations

import re
import urllib.parse

__all__ = [
    "normalize_url",
    "dedup_key",
    "host_of",
    "is_pan_share",
    "file_ext",
    "is_tracking_host",
    "BLOCKED_HOSTS",
    "PAN_HOST_SUFFIXES",
]

# 无意义的跟踪/跳转参数(值不影响资源身份)
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "spm_id_from", "from", "ref", "refer", "referrer", "source",
    "src", "share_source", "share_medium", "share_campaign", "rd",
    "pid", "posid", "clicktime", "eid", "from_search", "from_source",
    "app", "appid", "version", "scene", "ssr", "buvid", "x1", "x2",
}
# 网盘分享参数(必须保留:提取码/分享码)
_PAN_PARAMS = {"pwd", "code", "extraction", "password", "tk", "shareid", "uk"}

# 常见资源直链扩展名
DIRECT_EXTENSIONS = {
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ".epub", ".mobi", ".azw3", ".djvu", ".txt", ".md",
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".ts",
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg",
    ".iso", ".exe", ".msi", ".apk", ".dmg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    ".torrent", ".nzb",
    # 图片/壁纸
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg", ".avif", ".ico", ".tiff",
    # 3D模型与工程仿真/地形资产
    ".glb", ".gltf", ".obj", ".fbx", ".stl", ".blend", ".ply", ".dae", ".3ds", ".step", ".stp", ".usdz", ".dem",
    # 我的世界建筑投影/存档
    ".litematic", ".schematic", ".schem", ".mcworld", ".mctheme",
}

# 网盘分享页域名后缀(命中即视为 pan_share,不做网络探测)
PAN_HOST_SUFFIXES = (
    "pan.baidu.com", "pan.quark.cn", "quark.cn", "aliyundrive.com", "alipan.com",
    "123pan.com", "lanzou.com", "lanzoux.com", "lanzouo.com", "lanzoui.com",
    "pan.xunlei.com", "cloud.189.cn", "115.com", "caiyun.com", "uc.cn",
    "weiyun.com", "wps.cn", "yun.cn",
)

# 跟踪/广告/无价值域名(直接丢弃)
BLOCKED_HOSTS = {
    "doubleclick.net", "googleadservices.com", "googlesyndication.com",
    "taboola.com", "outbrain.com", "adservice.google.com", "adsrvr.org",
    "adroll.com", "criteo.com", "criteo.net", "quantserve.com", "scorecardresearch.com",
    "cnzz.com", "51.la", "umeng.com", "hm.baidu.com", "pos.baidu.com",
    "cpro.baidu.com", "baidustatic.com", "bdstatic.com", "alicdn.com",
    "taobaocdn.com", "amazon-adsystem.com", "facebook.com/tr", "t.co",
}

# ---------------------------------------------------------------- 真直链判定
# 扩展名是"提示"不是"证据":仓库名/预览页/路由参数可以伪装成文件扩展名。
# 权威判定 = 扩展名命中 且 不属于"网页伪装"模式。
#
# 网页伪装:路径后缀是扩展名,但实际返回 HTML(预览页/仓库主页/路由)
WEBPAGE_DISGUISE_PATTERNS: list[tuple[str, str, str]] = [
    (".github.com", r"/blob/", "GitHub 文件预览页"),
    (".github.com", r"/tree/", "GitHub 目录页"),
    (".github.com", r"/releases/tag/", "GitHub 发布说明页"),
    (".github.com", r"/commit/", "GitHub 提交页"),
    (".github.com", r"^/[^/]+/[^/]+/?$", "GitHub 仓库主页(仓库名可能以 .zip 结尾)"),
    (".gitlab.com", r"/-/blob/", "GitLab 文件预览页"),
    (".gitlab.com", r"/-/tree/", "GitLab 目录页"),
    (".gitee.com", r"/blob/", "Gitee 文件预览页"),
]
# 真直链:主机/路径明确是文件服务(即使形态奇怪)
DIRECT_FILE_PATTERNS: list[tuple[str, str, str]] = [
    (".github.com", r"/releases/download/", "GitHub release 资源直链"),
    (".github.com", r"/archive/", "GitHub 仓库归档直链"),
    (".github.com", r"/raw/", "GitHub raw 直链"),
    (".raw.githubusercontent.com", r"", "GitHub raw CDN"),
    (".objects.githubusercontent.com", r"", "GitHub 资源 CDN"),
    (".codeload.github.com", r"", "GitHub 归档下载"),
    (".gitlab.com", r"/-/raw/", "GitLab raw 直链"),
    (".gitee.com", r"/raw/", "Gitee raw 直链"),
]

_HOST_SUFFIX_RE = re.compile(r"^(?:www\.)?(.+)$")


def _host_match(suffix: str, host: str) -> bool:
    if suffix.startswith("."):
        return host == suffix[1:] or host.endswith(suffix)  # apex + 子域
    return host == suffix


def is_webpage_disguise(url: str) -> bool:
    """路径后缀是扩展名,但该 URL 实为网页(预览页/仓库主页/路由)。"""
    try:
        p = urllib.parse.urlsplit(url)
        host, path = p.netloc.lower(), p.path
    except Exception:
        return False
    for hs, pat, _ in WEBPAGE_DISGUISE_PATTERNS:
        if _host_match(hs, host) and re.search(pat, path):
            return True
    return False


def is_direct_file_url(url: str) -> bool:
    """权威直链判定:扩展名命中 且 (非网页伪装 或 属真直链模式)。"""
    if file_ext(url) not in DIRECT_EXTENSIONS:
        return False
    if is_webpage_disguise(url):
        return False
    try:
        p = urllib.parse.urlsplit(url)
        host, path = p.netloc.lower(), p.path
    except Exception:
        return True
    for hs, pat, _ in DIRECT_FILE_PATTERNS:
        if _host_match(hs, host) and (not pat or re.search(pat, path)):
            return True
    return True  # 无伪装、无特殊模式 → 普通文件直链


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def normalize_url(url: str) -> str:
    """规范化:去跟踪参数、保留网盘参数、统一格式。失败返回原串。"""
    url = url.strip()
    try:
        p = urllib.parse.urlsplit(url)
        scheme = p.scheme.lower() or "https"
        # http/https 视为同一资源,统一为 https(反爬时代 http 站点普遍 301 到 https)
        if scheme in ("http", "https"):
            scheme = "https"
        host = p.netloc.lower()
        # 去默认端口与 www
        if host.endswith(":80") and scheme == "http":
            host = host[:-3]
        elif host.endswith(":443") and scheme == "https":
            host = host[:-4]
        host = re.sub(r"^www\.", "", host)
        path = p.path or "/"
        # 去重复斜杠(保留协议双斜杠)
        path = re.sub(r"/{2,}", "/", path)
        if len(path) > 1:
            path = path.rstrip("/")
        # 查询参数:丢弃跟踪参数,保留其余(含网盘参数)
        keep = []
        if p.query:
            for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
                kl = k.lower()
                if kl in _TRACKING_PARAMS:
                    continue
                if kl in _PAN_PARAMS and v:
                    keep.append(f"{k}={v}")
                elif kl not in _PAN_PARAMS:
                    keep.append(f"{k}={v}")
        query = "&".join(keep)
        out = urllib.parse.urlunsplit((scheme, host, path, query, ""))
        return out
    except Exception:
        return url


def dedup_key(url: str) -> str:
    """去重键 = 规范化 URL(小写主机 + 保留资源参数)。"""
    return normalize_url(url)


def file_ext(url: str) -> str:
    """取路径扩展名(小写,含点);无扩展名返回空。"""
    try:
        path = urllib.parse.urlsplit(url).path
        m = re.search(r"\.([a-zA-Z0-9]{1,12})$", path)
        return ("." + m.group(1).lower()) if m else ""
    except Exception:
        return ""


def is_pan_share(url: str) -> bool:
    """网盘分享页判断(仅看域名,不做网络请求)。"""
    host = host_of(url)
    return any(host == s or host.endswith("." + s) for s in PAN_HOST_SUFFIXES)


def is_tracking_host(url: str) -> bool:
    host = host_of(url)
    return any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS)
