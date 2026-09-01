"""资源获取任务 — 检索 → 页面分析 → 快路径直链下载 / 慢路径浏览器 Agent。

对齐计划 v4 §1 端到端流程与 §3.4 两层获取:
- 快路径:候选直链直接下载(零浏览器);
- 慢路径:详情页带下载按钮 / 需登录 / 反爬拦截 的候选 → AccountAgent(fetch_resource goal)
  打开页面,调技能(mail_code/captcha/human/analyze_page/download)取资源;
- 完成判定 = 下载探针(文件落地 + 扩展名 + 非空),模型判定不算数。

用法:
    from agent.tasks.fetch_resource import fetch_resource
    result = fetch_resource("故宫 投影 litematic", out_dir="downloads")
"""

from __future__ import annotations

import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from search import search as search_engines
from pages import analyze_page
from pages.models import PageClass
from agent.tasks.base import FileProbe, TaskResult

DEFAULT_FILE_TYPES = (".litematic", ".schematic", ".schem", ".zip")
DEFAULT_OUT = "downloads"


class TaskCancelled(Exception):
    """协作式取消:任务循环内检查到取消标志时抛出,终止整个任务。"""


class TaskTimeout(Exception):
    """任务总时间预算耗尽(防"卡死":即使 Agent 每候选有预算,整条管线也要有硬上限)。"""


_TASK_TIME_BUDGET = 600.0   # 整条管线硬上限(秒):超过直接失败,不给用户"无限等待"的观感


def _emit(on_stage: Optional[Callable[[str, str], None]], stage: str, msg: str) -> None:
    if on_stage is not None:
        try:
            on_stage(stage, msg)
        except Exception:
            pass


def _check_cancel(is_cancelled: Optional[Callable[[], bool]]) -> None:
    if is_cancelled is not None:
        try:
            if is_cancelled():
                raise TaskCancelled()
        except TaskCancelled:
            raise
        except TaskTimeout:
            raise
        except Exception:
            pass


def _progress_line(msg, limit: int = 150) -> str:
    """把 Agent 的多行日志压成单行并截断,喂给任务事件流(SSE/轮询/网页日志)。"""
    line = " ".join(str(msg).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"

# 资源类型预设:按查询关键词自动推断(file_types=None 时)—— 用户无需指定格式,AI/关键词自行判断
_TYPE_PRESETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "schematic": ((".litematic", ".schematic", ".schem", ".zip", ".mcworld"),
                  ("投影", "schematic", "litematic", "建筑", "蓝图", "地图")),
    "image": ((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"),
              ("壁纸", "图片", "图像", "高清", "wallpaper", "4k", "png", "jpg",
               "photo", "image", "皮肤", "皮肤包", "skin")),
    "audio": ((".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus"),
              ("音乐", "歌曲", "无损", "flac", "mp3", "音频", "铃声", "ost", "soundtrack")),
    "video": ((".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m3u8", ".ts"),
              ("视频", "电影", "电视剧", "动漫", "剧集", "影视", "番剧", "mp4", "video",
               "全集", "第", "集", "在线观看")),
    "document": ((".pdf", ".epub", ".mobi", ".azw3", ".djvu", ".txt", ".docx", ".doc",
                  ".rar", ".zip"),   # 电子书站常把 txt 打包成 rar/zip
                 ("pdf", "epub", "mobi", "书", "教程", "文档", "资料", "电子书", "doc", "小说")),
    "software": ((".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".msi", ".apk"),
                 ("软件", "安装包", "工具", "破解", "绿色版", "app", "客户端")),
}

# 全格式兜底:关键词未命中任何预设时,接受所有已知资源格式(下载后按真实格式裁决)
_ALL_KNOWN_TYPES = (
    ".litematic", ".schematic", ".schem", ".zip", ".mcworld",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif",
    ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus",
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m3u8", ".ts",
    ".pdf", ".epub", ".mobi", ".azw3", ".djvu", ".txt", ".docx", ".doc",
    ".rar", ".7z", ".tar", ".gz", ".exe", ".msi", ".apk",
)

# 显式格式词 → 优先锁定类型:用户明确写了 txt/壁纸/视频/安装包 等格式词时,
# 不能被关键词数量启发式带偏(如「全集 txt」里“全集”属于视频词但用户要的是 txt)。
# 按序检查,命中即返回(文档词放最前:txt/小说类查询最常见)。
_EXPLICIT_TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "document": ("txt", "文本", "pdf", "epub", "mobi", "azw3", "docx",
                 "小说", "电子书", "阅读", "章节", "全集txt", "txt全集"),
    "image": ("壁纸", "图片", "wallpaper", "png", "jpg", "jpeg",
              "4k壁纸", "手机壁纸", "桌面壁纸", "高清大图", "皮肤", "皮肤包", "skin"),
    "audio": ("mp3", "flac", "wav", "ogg", "音乐", "歌曲", "无损", "铃声", "ost"),
    "video": ("mp4", "mkv", "视频", "电影", "电视剧", "动漫", "番剧",
              "剧集", "在线观看", "全集观看"),
    "schematic": ("litematic", "schematic", "投影", "蓝图"),
    "software": ("exe", "msi", "apk", "软件", "安装包", "破解", "绿色版", "客户端"),
}

_AGENT_CLASSES = (PageClass.LOGIN_REQUIRED, PageClass.UNKNOWN,
                  PageClass.AGGREGATOR, PageClass.BLOCKED)
_MAX_ANALYZE = 16         # 分析预算(候选可能比预算多,取前 N 个)
_MAX_AGENT_ATTEMPTS = 4    # Agent 尝试上限(已按站点网络去重,放宽后仍受控)
_MAX_MERGE_ATTEMPTS = 3    # 小说章节拼接尝试上限

# 付费/正版小说平台:无账号时 Agent/拼接必败,不进预算
_PAID_NOVEL_HOSTS = (
    "qidian.com", "yuewen.com", "readnovel.com", "hongxiu.com", "xs8.cn", "shuqi.com",
    "book.qq.com", "yunqi.qq.com", "read.qq.com", "ac.qq.com", "reader.qq.com",
)

# 小说意图与查询改写:书名去噪后补「txt 下载 网盘」二次检索,
# 命中 80xs/云中书库 等带附件/网盘的真源(正版 SEO 页排不进 top-16 的场合)
_NOVEL_NOISE = re.compile(
    r"下载|小说|txt|全文|全集|全书|文本|免费|阅读|在线阅读|笔趣阁|电子书|"
    r"实体书|精校|校对|无弹窗|网盘|的|第[0-9一二三四五六七八九十百千零两]+(?:章|卷|集)",
    re.I,
)


def _infer_file_types(query: str, file_types) -> tuple[str, ...]:
    """按查询关键词推断资源类型;未命中返回全格式兜底(AI/关键词自行判断,无需用户指定)。"""
    if file_types is not None and len(file_types) > 0:
        return tuple(file_types)
    q = (query or "").lower()
    # 1) 显式格式词优先(用户明确写了要什么格式)
    for preset, words in _EXPLICIT_TYPE_WORDS.items():
        if any(w in q for w in words):
            return _TYPE_PRESETS[preset][0]
    # 2) 关键词数量启发式兜底
    best: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    for preset, (exts, kws) in _TYPE_PRESETS.items():
        if any(k in q for k in kws):
            if best is None or len(kws) > len(best[1]):
                best = (exts, kws)
    if best:
        return best[0]
    return _ALL_KNOWN_TYPES

# 需要浏览器 Agent 的页面类型(交互/登录/反爬)
_AGENT_CLASSES = (PageClass.LOGIN_REQUIRED, PageClass.UNKNOWN,
                  PageClass.AGGREGATOR, PageClass.BLOCKED)
_MAX_ANALYZE = 16         # 分析预算(候选可能比预算多,取前 N 个)
_MAX_AGENT_ATTEMPTS = 4    # Agent 尝试上限(已按站点网络去重,放宽后仍受控)
_MAX_MERGE_ATTEMPTS = 3    # 小说章节拼接尝试上限

# 付费/正版小说平台:无账号时 Agent/拼接必败,不进预算
_PAID_NOVEL_HOSTS = (
    "qidian.com", "yuewen.com", "readnovel.com", "hongxiu.com", "xs8.cn", "shuqi.com",
    "book.qq.com", "yunqi.qq.com", "read.qq.com", "ac.qq.com", "reader.qq.com",
)

# 小说意图与查询改写:书名去噪后补「txt 下载 网盘」二次检索,
# 命中 80xs/云中书库 等带附件/网盘的真源(正版 SEO 页排不进 top-16 的场合)
_NOVEL_NOISE = re.compile(
    r"下载|小说|txt|全文|全集|全书|文本|免费|阅读|在线阅读|笔趣阁|电子书|"
    r"实体书|精校|校对|无弹窗|网盘|的|第[0-9一二三四五六七八九十百千零两]+(?:章|卷|集)",
    re.I,
)

# 音视频直链预检:URL 指向安装包/网页/图片 → 不是音频视频,直接跳过
# (酷狗歌曲页「下载」按钮实为 download.kugou.com/download/kugou_mac 客户端安装包,
#  93MB 白下还误导任务 —— 线上事故)
_NON_MEDIA_EXTS = (".exe", ".dmg", ".apk", ".msi", ".iso", ".dll",
                   ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                   ".pdf", ".doc", ".docx", ".xls", ".xlsx")


def _is_non_media_url(url: str) -> bool:
    low = (url or "").lower().split("?")[0].split("#")[0]
    if low.endswith(_NON_MEDIA_EXTS):
        return True
    # URL 形状识别站点客户端安装包(酷狗「下载」按钮 → download.kugou.com/download/kugou_mac,
    # 无扩展名躲过扩展名检查,必须在下载前拦下 —— 93MB 白下事故)
    try:
        from pages.extractor import _is_client_install_url

        return _is_client_install_url(url)
    except Exception:
        return False


def _is_media_file(path) -> bool:
    """魔数嗅探:确认下载内容真实为音视频容器(MP3/FLAC/OGG/WAV/M4A/MP4/MKV/WEBM/TS)。

    防「下到客户端安装包/网页 HTML/空文件」冒充音视频:酷狗 getdata 返回的
    171 字节假 mp3、Mach-O 安装包、CSDN 登录页都不会通过。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if len(head) < 8:
        return False                      # 空文件/过小(假 mp3)
    if head.startswith(b"ID3") or (head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return True                       # MP3 / AAC(ADTS)
    if head.startswith((b"fLaC", b"OggS")):
        return True                       # FLAC / OGG
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return True                       # WAV
    if head[4:8] == b"ftyp":
        return True                       # MP4 / M4A
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return True                       # MKV / WEBM
    return head[0] == 0x47                # MPEG-TS(弱特征,流切片)


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}


def _is_image_types(file_types) -> bool:
    """file_types 是否为纯图片类型(仅图片扩展名 → 需内容校验)。"""
    fts = {str(f).lower() for f in (file_types or ())}
    return bool(fts) and fts <= _IMAGE_EXTS


# 皮肤站(定向检索 + 皮肤类任务要求 PNG)
_SKIN_HOSTS = ("namemc.com", "littleskin.cn", "mcskins.org",
               "skindex.com", "planetminecraft.com", "minecraftskins.com")


def _is_skin_intent(query: str, file_types) -> bool:
    """Minecraft 皮肤意图(皮肤/skin + 我的世界/minecraft):交付物必须是 PNG 皮肤文件。"""
    ql = (query or "").lower()
    skin_word = ("皮肤" in ql) or ("skin" in ql)
    mc_word = any(k in ql for k in ("我的世界", "minecraft", "java", "基岩"))
    if skin_word and mc_word:
        return True
    imgs = {str(f).lower() for f in (file_types or ())}
    return skin_word and bool(imgs) and imgs <= _IMAGE_EXTS


# 皮肤站搜索页模板(领域知识入口,交给 AI 在真实浏览器里执行搜索/点选/下载;
# 不做站点抓取规则 —— 线上事故:通用搜索给皮肤查询返回的全是视频/攻略页)
_SKIN_SEARCH_PAGES = (
    ("namemc.com", "https://namemc.com/search?q="),
    ("minecraftskins.com", "https://www.minecraftskins.com/search/"),
    ("planetminecraft.com", "https://www.planetminecraft.com/search/?q="),
    # littleskin 是 JS 渲染 SPA,无 ?keyword= 搜索 URL(实测 404);
    # 种到皮肤库首页,由 Agent 在页内搜索框输入名字
    ("littleskin.cn", "https://littleskin.cn/skinlib"),
)

# 皮肤站搜索名:去掉 我的世界/皮肤 等意图词,只留角色名(如 银狼lv999)
_SKIN_NAME_NOISE = re.compile(
    r"我的世界|minecraft|minecraft|java|基岩|皮肤|skin|皮肤包|下载|免费|"
    r"高清|原图|大图|壁纸|的|求|帮|给我",
    re.I,
)


def _skin_search_name(query: str) -> str:
    q = _SKIN_NAME_NOISE.sub(" ", query or "")
    q = re.sub(r"[\s，。、,.;；:：'\"“”‘’!！?？【】\[\]()（）]+", " ", q).strip()
    q = q.replace(" ", "")
    return q[:40]


def _skin_name_match(page_name: str, query: str) -> bool:
    """皮肤页标题是否匹配查询皮肤名(归一化:忽略大小写/空格/标点)。

    线上事故:littleskin 皮肤名为「星穹铁道 银狼 LV.999」,查询「银狼lv999」——
    归一化后 星穹铁道银狼lv999 包含 银狼lv999 → 匹配;而 Level999Villager
    不含「银狼」→ 不匹配。像素级视觉校验对 64x64 皮肤不可靠,页面名才是权威信号。
    """
    q = _skin_search_name(query)
    if not q:
        return False

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (s or "").lower())

    nq, nn = _norm(q), _norm(page_name)
    if not nq or not nn:
        return False
    return nq in nn or nn in nq


def _is_image_file(path) -> bool:
    """魔数嗅探:确认文件真实为图片(PNG/JPEG/WebP/GIF/BMP/AVIF/SVG)。

    防「皮肤任务下到游戏修改器 exe/网页 HTML」冒充图片 —— 线上事故:
    查询『我的世界 银狼lv999 皮肤』却下载了 FLiNG_Trainer_c30_.exe。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if len(head) < 8:
        return False
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True                       # PNG
    if head.startswith(b"\xff\xd8\xff"):
        return True                       # JPEG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return True                       # GIF
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return True                       # WebP
    if head.startswith(b"BM"):
        return True                       # BMP
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis", b"av01"):
        return True                       # AVIF
    low = head.lower()
    if low.startswith(b"<?xml") or b"<svg" in low[:16]:
        return True                       # SVG(XML 文本)
    return False


_IMAGE_MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),              # + WEBP 头二次确认
    (b"BM", "image/bmp"),
)

# 查询主题去噪词:只留核心主题词(「下载/免费/在线」等动作词对视觉主题无意义;
# 皮肤/壁纸 等类型描述词保留 —— 视觉校验需要它们;注意别去"的"(我的世界 是专名))
_SUBJECT_NOISE = re.compile(
    r"下载|免费|在线|观看|完整|高清|原图|大图|求|帮我|给我|最好|谢谢|视频|音乐|图片|链接",
    re.I,
)


def _mime_of(path) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return "image/jpeg"
    for sig, mime in _IMAGE_MIME:
        if head.startswith(sig):
            if sig == b"RIFF" and head[8:12] != b"WEBP":
                continue
            return mime
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis", b"av01"):
        return "image/avif"
    return "image/jpeg"


def _clean_subject(query: str) -> str:
    """查询 → 视觉校验主题:去动作/类型噪音词,留核心主题。"""
    q = _SUBJECT_NOISE.sub(" ", query or "")
    q = re.sub(r"[\s，。、,.;；:：'\"“”‘’!！?？【】\[\]()（）]+", " ", q).strip()
    q = q.replace(" ", "")
    return (q or (query or "").strip())[:60]


def _verify_image_file(path, query: str, file_types=(), strict: bool = True) -> tuple[Optional[bool], str]:
    """图片内容闸门:AI 视觉验证图片与查询主题是否匹配(严格模式)。

    返回 (verdict, detail):True=匹配保留;False=不匹配丢弃;None=视觉不可用(不阻塞)。
    皮肤类任务(Minecraft 皮肤)额外要求 PNG —— 皮肤文件就是 PNG,宣传图/jpg 预览不算。
    """
    if _is_image_types(file_types) and _is_skin_intent(query, file_types):
        if _mime_of(path) != "image/png":
            return False, "皮肤类任务要求 PNG 皮肤文件(当前非 PNG,可能是宣传图/预览图)"
    try:
        img = Path(path).read_bytes()
    except OSError as e:
        return None, f"读取图片失败: {type(e).__name__}"
    try:
        from ai.vision import verify_image_content
    except Exception:
        return None, "视觉模块不可用"
    subject = _clean_subject(query) or "该资源"
    try:
        r = verify_image_content(img, subject, mime=_mime_of(path), strict=strict)
        # 严格模式下"阳性复核":单次视觉判定是随机的(线上事故:同一张 Level999Villager
        # 皮肤一次判过、一次判拒),首次判匹配时必须再确认一次,两次都过才接受。
        if strict and r.get("ok") is True:
            r2 = verify_image_content(img, subject, mime=_mime_of(path), strict=strict)
            if r2.get("ok") is not True:
                return False, f"复核未通过: {(r2.get('reason') or '')[:80]}"
    except Exception as e:
        return None, f"视觉校验异常: {type(e).__name__}"
    if r.get("ok") is None:
        return None, r.get("reason", "视觉不可用")
    return bool(r["ok"]), f"{r.get('content','')} | {r.get('reason','')}"


def _pipeline(
    query: str = "",
    seed_urls: Optional[list[str]] = None,
    file_types: Optional[tuple[str, ...]] = None,
    out_dir: str | Path = DEFAULT_OUT,
    engines: Optional[list[str]] = None,
    max_candidates: int = 16,
    use_agent_fallback: bool = True,
    login_email: str = "",
    username: str = "",
    security_scan: bool = True,   # 下载后查毒闸门(ClamAV + YARA + 启发式)
    reuse_cookies: bool = True,   # 复用 accounts/cookies/ 登录态(免重复登录)
    stream_media: bool = True,    # 音视频(mp4/mkv/mp3/flac/m3u8 等)走流式下载技能
    on_stage: Optional[Callable[[str, str], None]] = None,  # 阶段回调(stage, message)
    is_cancelled: Optional[Callable[[], bool]] = None,      # 协作式取消回调
    verbose: bool = True,
    site_log: Optional[list] = None,  # 站点访问信号收集(信誉库录入用)
) -> TaskResult:
    """核心管线:检索(或给定种子 URL)→ 分析候选 → 下载(必要时 Agent 兜底)。

    file_types=None 时按查询关键词自动推断(壁纸→图片类, 投影→schematic 类等)。
    on_stage/is_cancelled 供 API 层上报进度与支持取消;取消时返回 success=False,
    error="cancelled"。site_log 记录访问过的站点信号,由 fetch_resource 包装层录入信誉库。
    """
    if site_log is None:
        site_log = []
    # 整条管线时间硬上限:包一层 deadline 感知的取消回调 —— 所有 _check_cancel 点
    # 自动检查总预算,超时抛 TaskTimeout,由 fetch_resource 包装层转为失败结果。
    _deadline = time.monotonic() + _TASK_TIME_BUDGET
    _orig_cancel = is_cancelled

    def _deadline_aware_cancel() -> bool:
        if time.monotonic() > _deadline:
            raise TaskTimeout(f"任务超过总时间预算 {_TASK_TIME_BUDGET:.0f}s")
        if _orig_cancel is not None:
            try:
                return bool(_orig_cancel())
            except Exception:
                return False
        return False

    is_cancelled = _deadline_aware_cancel
    file_types = _infer_file_types(query, file_types)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = FileProbe(out_dir, file_types)
    result = TaskResult(success=False)

    # ---------- 1. 种子 = 检索结果 + 显式给定 URL ----------
    queue: deque[tuple[str, int]] = deque()
    if seed_urls:
        for u in seed_urls:
            queue.append((u, 0))
    if query:
        if verbose:
            print(f"🔎 检索: {query!r}")
        _emit(on_stage, "search", f"检索 {query!r}")
        _check_cancel(is_cancelled)
        candidates = search_engines(query, engines=engines, per_engine=12, probe=False,
                                    limit=max_candidates)
        for c in candidates:
            queue.append((c.url, 0))
    if not queue:
        result.error = "没有可分析的起点(检索无结果且未给 seed_urls)"
        return result

    # ---------- 2. 逐候选分析:直链走快路径,交互页进 Agent ----------
    visited: set[str] = set()
    direct_urls: list[str] = []
    direct_names: dict[str, str] = {}   # 直链来源页标题(皮肤任务名字匹配验收用)
    agent_candidates: list[tuple[str, str]] = []
    pan_links: list[str] = []
    analyzed = 0
    _emit(on_stage, "analyze", f"开始分析 {len(queue)} 个候选")

    def _note_direct(urls, page_title: str) -> None:
        for u in urls:
            direct_names.setdefault(u, page_title or "")

    while queue and analyzed < _MAX_ANALYZE and len(direct_urls) < 3:
        _check_cancel(is_cancelled)
        url, _depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        analyzed += 1
        if verbose:
            print(f"\n[{analyzed}] 分析 {url[:95]}")
        analysis = analyze_page(url, probe=True)
        cls = analysis.page_class
        if verbose:
            print(f"    分级: {cls.value} ({analysis.reason})")
        _emit(on_stage, "analyze", f"[{analyzed}/{_MAX_ANALYZE}] {cls.value}: {url[:60]}")
        _log_site(site_log, url, query, page_class=cls.value,
                  reason=analysis.reason or "")

        if cls == PageClass.DIRECT_FILE:
            if not file_types or _ext_match(url, file_types):
                direct_urls.append(url)
                _note_direct([url], getattr(analysis, "title", ""))
            continue

        best = analysis.best_resources

        if cls == PageClass.DOWNLOAD_PAGE:
            files = [r.url for r in best
                     if r.kind == "direct_file"
                     and (not file_types or not r.file_ext or r.file_ext.lower() in file_types)]
            if files:
                direct_urls.extend(files)
                _note_direct(files, getattr(analysis, "title", ""))
                continue
            # 详情页带下载按钮:很多"下载按钮"其实就是直链(点击即下载,如乐书谷
            # down.xxx/down/<id> 返回 text/plain)。先走快路径直下;失败仍保留
            # Agent 候选兜底(按钮需要点击/JS 的场景)。
            btn_urls = [r.url for r in best if r.kind == "download_button"]
            if btn_urls:
                direct_urls.extend(btn_urls)
                _note_direct(btn_urls, getattr(analysis, "title", ""))
                agent_candidates.append((url, "详情页带下载按钮"))
                continue
            # 页面只有客户端安装按钮(下载酷狗/下载客户端/立即安装):规则判断不了
            # 真实资源在哪,不盲下,把页面交给 Agent 找真实链接(如酷狗 hash API)
            if any(r.kind == "client_install" for r in best):
                if (url, cls.value) not in agent_candidates:
                    agent_candidates.append(
                        (url, "页面含客户端安装按钮,交由 Agent 找真实资源链接"))
                continue

        if cls in _AGENT_CLASSES:
            if cls == PageClass.LOGIN_REQUIRED and not login_email:
                # 无邮箱时登录墙候选必败:进 Agent 只会空耗数分钟(请求人工→自动放弃)
                if verbose:
                    print(f"    跳过: 需登录且未配置 login_email")
                continue
            reason = analysis.reason or cls.value
            if cls == PageClass.BLOCKED:
                reason = f"blocked: {reason}"   # 被拦截候选排序靠后(浏览器可能能过)
            if (url, reason) not in agent_candidates:
                agent_candidates.append((url, reason))

        for r in best:
            if r.kind == "pan_share" and r.url not in pan_links:
                pan_links.append(r.url)

    # ---------- 3. 快路径:直链下载 ----------
    _emit(on_stage, "download", f"直链下载 {len(dict.fromkeys(direct_urls))} 个候选")
    for url in dict.fromkeys(direct_urls):
        _check_cancel(is_cancelled)
        if verbose:
            print(f"⬇️  下载直链: {url[:100]}")
        dl = _download_one(url, out_dir, file_types, stream_media, on_stage,
                           is_cancelled)
        if dl:
            if security_scan:
                verdict, note = _security_gate(dl)
                if verdict in ("infected", "suspicious"):
                    if verbose:
                        print(f"🛡️  安全扫描未通过({verdict}): {note[:120]} → 已删除,尝试下一个")
                    _log_site(site_log, url, query, downloaded=False,
                              note=f"安全扫描未过({verdict})")
                    continue
                if verdict == "unknown" and verbose:
                    print("🛡️  安全扫描: 无杀毒引擎(仅启发式覆盖),已放行并标注 unknown")
            # 图片内容闸门:AI 视觉验证内容与查询主题是否匹配
            # (防『皮肤』任务下到无关图片 —— 线上事故:银狼lv999 皮肤 → E宝纯图片)。
            # 皮肤任务优先用「来源页标题名字匹配」验收(64x64 皮肤像素图视觉不可靠,
            # 页面名如「星穹铁道 银狼 LV.999」归一化后含 银狼lv999 才是权威信号)。
            if _is_image_types(file_types):
                skin_name_ok = (_is_skin_intent(query, file_types)
                                and _skin_name_match(direct_names.get(url, ""), query))
                if skin_name_ok:
                    _emit(on_stage, "verify",
                          f"来源页标题匹配目标皮肤名,通过: {Path(dl).name}")
                else:
                    verdict, detail = _verify_image_file(dl, query, file_types)
                    if verdict is False:
                        try:
                            Path(dl).unlink(missing_ok=True)
                        except OSError:
                            pass
                        _emit(on_stage, "verify", f"图片内容不符主题,已丢弃: {Path(dl).name} ({detail[:80]})")
                        _log_site(site_log, url, query, downloaded=False,
                                  note=f"图片内容不符: {detail[:80]}")
                        if verbose:
                            print(f"🖼️  图片内容不符主题({detail[:100]}),已丢弃,尝试下一个")
                        continue
                    if verdict is True:
                        _emit(on_stage, "verify", f"图片内容校验通过: {Path(dl).name}")
            _emit(on_stage, "scan", f"安全扫描通过: {Path(dl).name}")
            _log_site(site_log, url, query, downloaded=True)
            result.files.append(dl)
            result.sources.append(url)
            result.pan_links = list(dict.fromkeys(pan_links))  # 云盘链接随结果返回
            if verbose:
                print(f"✅ 已下载: {dl}")
            if probe():
                result.success = True
                result.summary = f"直链下载成功: {dl}"
                return result
        elif verbose:
            print("⚠️  直链下载失败,尝试下一个")

    # ---------- 3.5 小说意图:二次检索(书名 + txt 下载 网盘)补真源 ----------
    # 正版 SEO 聚合页(起点/QQ阅读)权重高,常挤掉 80xs/云中书库 等带附件直链的真源
    if use_agent_fallback and not result.files and _is_novel_intent(query, file_types):
        rq = _rewrite_novel_query(query)
        if rq:
            if verbose:
                print(f"\n🔎 小说二次检索: {rq!r}")
            _emit(on_stage, "search", f"小说二次检索: {rq}")
            try:
                new_cands = search_engines(rq, engines=engines, per_engine=10,
                                           probe=False, limit=10)
            except Exception as e:
                new_cands = []
                if verbose:
                    print(f"二次检索失败: {type(e).__name__}: {str(e)[:100]}")
            analyzed = 0
            for c in new_cands:
                if analyzed >= 6 or len(direct_urls) >= 6:
                    break
                _check_cancel(is_cancelled)
                if c.url in visited:
                    continue
                visited.add(c.url)
                analyzed += 1
                if verbose:
                    print(f"[二次{analyzed}] 分析 {c.url[:95]}")
                analysis = analyze_page(c.url, probe=True)
                cls = analysis.page_class
                best = analysis.best_resources
                _emit(on_stage, "analyze", f"[二次 {analyzed}] {cls.value}: {c.url[:60]}")
                _log_site(site_log, c.url, query, page_class=cls.value,
                          reason=analysis.reason or "")
                if cls == PageClass.DIRECT_FILE and (not file_types or _ext_match(c.url, file_types)):
                    direct_urls.append(c.url)
                    _note_direct([c.url], getattr(analysis, "title", ""))
                elif cls == PageClass.DOWNLOAD_PAGE:
                    files = [r.url for r in best if r.kind == "direct_file"
                             and (not file_types or not r.file_ext or r.file_ext.lower() in file_types)]
                    if files:
                        direct_urls.extend(files)
                        _note_direct(files, getattr(analysis, "title", ""))
                    else:
                        btn_urls = [r.url for r in best if r.kind == "download_button"]
                        if btn_urls:
                            direct_urls.extend(btn_urls)
                            _note_direct(btn_urls, getattr(analysis, "title", ""))
                            agent_candidates.append((c.url, "二次检索: 详情页带下载按钮"))
                        elif any(r.kind == "client_install" for r in best):
                            # 只有客户端安装按钮 → 交给 Agent 找真实资源
                            if (c.url, cls.value) not in agent_candidates:
                                agent_candidates.append(
                                    (c.url, "页面含客户端安装按钮,交由 Agent 找真实资源链接"))
                elif cls in _AGENT_CLASSES:
                    if cls == PageClass.LOGIN_REQUIRED and not login_email:
                        continue
                    reason = analysis.reason or cls.value
                    if cls == PageClass.BLOCKED:
                        reason = f"blocked: {reason}"
                    if (c.url, reason) not in agent_candidates:
                        agent_candidates.append((c.url, reason))
                for r in best:
                    if r.kind == "pan_share" and r.url not in pan_links:
                        pan_links.append(r.url)
            # 二次检索的直链也尝试下载
            if direct_urls:
                _emit(on_stage, "download", f"直链下载(二次检索) {len(dict.fromkeys(direct_urls))} 个候选")
            for url in dict.fromkeys(direct_urls):
                _check_cancel(is_cancelled)
                if verbose:
                    print(f"⬇️  下载直链: {url[:100]}")
                dl = _download_one(url, out_dir, file_types, stream_media, on_stage,
                                   is_cancelled)
                if dl:
                    if security_scan:
                        verdict, note = _security_gate(dl)
                        if verdict in ("infected", "suspicious"):
                            if verbose:
                                print(f"🛡️  安全扫描未通过({verdict}): {note[:120]} → 已删除")
                            _log_site(site_log, url, query, downloaded=False,
                                      note=f"安全扫描未过({verdict})")
                            continue
                    if _is_image_types(file_types):
                        skin_name_ok = (_is_skin_intent(query, file_types)
                                        and _skin_name_match(direct_names.get(url, ""), query))
                        if skin_name_ok:
                            _emit(on_stage, "verify",
                                  f"来源页标题匹配目标皮肤名,通过: {Path(dl).name}")
                        else:
                            verdict, detail = _verify_image_file(dl, query, file_types)
                            if verdict is False:
                                try:
                                    Path(dl).unlink(missing_ok=True)
                                except OSError:
                                    pass
                                _emit(on_stage, "verify",
                                      f"图片内容不符主题,已丢弃: {Path(dl).name} ({detail[:80]})")
                                _log_site(site_log, url, query, downloaded=False,
                                          note=f"图片内容不符: {detail[:80]}")
                                if verbose:
                                    print(f"🖼️  图片内容不符主题,已丢弃,尝试下一个")
                                continue
                            if verdict is True:
                                _emit(on_stage, "verify", f"图片内容校验通过: {Path(dl).name}")
                    _emit(on_stage, "scan", f"安全扫描通过: {Path(dl).name}")
                    _log_site(site_log, url, query, downloaded=True)
                    result.files.append(dl)
                    result.sources.append(url)
                    result.pan_links = list(dict.fromkeys(pan_links))
                    if verbose:
                        print(f"✅ 已下载: {dl}")
                    if probe():
                        result.success = True
                        result.summary = f"直链下载成功(二次检索): {dl}"
                        return result
                elif verbose:
                    print("⚠️  直链下载失败,尝试下一个")

    # ---------- 3.5b 媒体二次检索:B站搜索换源 ----------
    # 酷狗/CSDN 等登录墙下,歌曲/视频在 B站常有可抓的播放流(DASH)。
    # 搜索结果不给 B站 → 直接用 B站搜索接口补源(URL 交给 Agent 用浏览器抓流)。
    if use_agent_fallback and not result.files and _is_media_intent(query, file_types):
        rq = _clean_media_query(query)
        if rq:
            if verbose:
                print(f"\n🔎 媒体二次检索(B站): {rq!r}")
            _emit(on_stage, "search", f"媒体二次检索(B站): {rq}")
            try:
                from skills.bilibili import search_videos
            except Exception as e:
                if verbose:
                    print(f"B站技能不可用: {type(e).__name__}: {str(e)[:80]}")
                search_videos = None
            if search_videos is not None:
                try:
                    hits = search_videos(rq, limit=4)
                except Exception as e:
                    hits = []
                    if verbose:
                        print(f"B站检索失败: {type(e).__name__}: {str(e)[:100]}")
                for h in hits:
                    _check_cancel(is_cancelled)
                    u = (h or {}).get("url", "")
                    if not u or "bilibili.com" not in u:
                        continue
                    if any(a[0] == u for a in agent_candidates):
                        continue
                    agent_candidates.append((u, "B站搜索: " + ((h.get("title") or "")[:30])))
                    _log_site(site_log, u, query, page_class="bilibili",
                              reason="B站搜索换源")

    # ---------- 3.5c 皮肤意图:AI 驱动补源(皮肤站搜索页作为候选种子) ----------
    # 通用搜索引擎给皮肤查询返回的多是 视频/攻略/聚合页(线上事故:『银狼lv999 皮肤』
    # 候选全是 douyin/bilibili/sohu)。把皮肤站搜索页作为候选种子交给 Agent,
    # 由 AI 在真实浏览器里执行搜索/点选/下载;验收统一走严格视觉闸门(皮肤=PNG+内容就是目标)。
    if use_agent_fallback and not result.files and _is_skin_intent(query, file_types):
        rq = _skin_search_name(query)
        if rq:
            if verbose:
                print(f"🧑‍🎨 皮肤意图:种子皮肤站搜索页 {rq!r}")
            _emit(on_stage, "agent", f"皮肤意图:种子皮肤站搜索页({rq})")
            from urllib.parse import quote

            for site, base in _SKIN_SEARCH_PAGES:
                _check_cancel(is_cancelled)
                u = base + quote(rq)
                if any(a[0] == u for a in agent_candidates):
                    continue
                agent_candidates.append((u, f"皮肤站搜索: {site}"))
                _log_site(site_log, u, query, page_class="skin-site", reason="皮肤站种子")

    # ---------- 3.6 小说章节拼接:逐章爬正文合并为单体 txt ----------
    # 笔趣阁类站只有分章 HTML,没有全本下载 —— 直接爬章节目录合并
    if use_agent_fallback and not result.files and _is_novel_intent(query, file_types):
        _emit(on_stage, "novel", "小说章节拼接:尝试合并整本书 txt")
        merge_cands = [u for u, _ in agent_candidates if not _is_paid_novel_host(u)]
        # 优选直接书页(非搜索引擎中转链)且信誉分高的站点;已知差站(≤2)排最后
        def _merge_key(u):
            s = _site_score(u) or 5
            stub = 1 if any(h in u for h in (
                "so.com/link", "baidu.com/link", "bing.com/ck")) else 0
            bad = 1 if s <= 2 else 0
            return (bad, stub, -s)

        merge_cands = sorted(dict.fromkeys(merge_cands),
                             key=_merge_key)[: _MAX_MERGE_ATTEMPTS]
        for u in merge_cands:
            _check_cancel(is_cancelled)
            if verbose:
                print(f"\n📖 章节拼接尝试: {u[:90]}")
            try:
                from skills.novel import fetch_novel_txt

                r = fetch_novel_txt(u, out_dir=out_dir, is_cancelled=is_cancelled,
                                    max_time=200,
                                    on_progress=lambda i, n: _emit(
                                        on_stage, "novel", f"拼接 {i}/{n} 章: {Path(u).name[:30]}"))
            except Exception as e:
                if verbose:
                    print(f"拼接异常: {type(e).__name__}: {str(e)[:100]}")
                _log_site(site_log, u, query, merge_ok=False,
                          note=f"拼接异常: {type(e).__name__}")
                continue
            _log_site(site_log, u, query, merge_ok=bool(r.get("ok")),
                      note=(r.get("error") or "")[:120])
            if r.get("ok"):
                merged = r["path"]
                if security_scan:
                    verdict, note = _security_gate(merged)
                    if verdict in ("infected", "suspicious"):
                        if verbose:
                            print(f"🛡️  拼接文件未过安全扫描({verdict}),删除")
                        continue
                _emit(on_stage, "scan", f"安全扫描通过: {Path(merged).name}")
                result.files = [merged]
                result.sources.append(u)
                result.pan_links = list(dict.fromkeys(pan_links))
                result.success = True
                result.summary = (f"章节拼接完成: {merged} ({r['chapters']}/{r['total']} 章)"
                                  + (f" - {r['note']}" if r.get("note") else ""))
                if verbose:
                    print(f"✅ {result.summary}")
                return result
            elif verbose:
                print(f"⚠️  拼接失败: {r.get('error', '未知原因')}")

    # ---------- 3.7 夸克网盘解析:直链无果时自动转存+直链下载 ----------
    # 搜索引擎常把资源收在 pan.quark.cn 分享里(小说/视频/软件),纯直链拿不到。
    # 有夸克 Cookie 时直接走 quark_download(一次导入,之后全自动);无 Cookie 跳过,
    # 留给 Agent 兜底或网页提示用户。百度/阿里云盘等后续阶段再接。
    if use_agent_fallback and not result.files and pan_links:
        quark_links = [u for u in pan_links if _is_quark_share(u)]
        if quark_links:
            _emit(on_stage, "quark", f"夸克网盘解析: {len(quark_links)} 个分享链接")
            for u in list(dict.fromkeys(quark_links))[:2]:
                _check_cancel(is_cancelled)
                if verbose:
                    print(f"\n☁️  夸克解析: {u[:95]}")
                try:
                    dl = _quark_fetch_one(u, out_dir, file_types, on_stage, is_cancelled)
                except Exception as e:
                    dl = None
                    if verbose:
                        print(f"    夸克解析异常: {type(e).__name__}: {str(e)[:100]}")
                if dl:
                    if security_scan:
                        verdict, note = _security_gate(dl)
                        if verdict in ("infected", "suspicious"):
                            if verbose:
                                print(f"🛡️  夸克文件未过安全扫描({verdict}),删除")
                            continue
                    _emit(on_stage, "scan", f"安全扫描通过: {Path(dl).name}")
                    _log_site(site_log, u, query, downloaded=True, note="quark")
                    result.files.append(dl)
                    result.sources.append(u)
                    result.pan_links = list(dict.fromkeys(pan_links))
                    result.success = True
                    result.summary = f"夸克网盘解析下载成功: {dl}"
                    if verbose:
                        print(f"✅ {result.summary}")
                    return result
                elif verbose:
                    print("⚠️  夸克解析未获文件(无 Cookie 或分享失效),继续下一候选")

    # ---------- 4. 慢路径:浏览器 Agent 兜底 ----------
    if use_agent_fallback and agent_candidates:
        _emit(on_stage, "agent", f"浏览器 Agent 兜底 {len(agent_candidates)} 个候选")
        # 候选排序与去重:带下载按钮的详情页优先;同一站点网络(注册域)只试一次,
        # 防止两个名额全耗在同一家(如 QQ阅读 的两个子域)的登录墙上。
        # 信誉库已知差站(≤2)排最后,高分好站优先 —— Agent 预算不浪费在差站上;
        # 被反爬拦截(blocked)的候选排最后(浏览器 Agent 或许能过,但优先级最低)。
        def _rep_key(it):
            s = _site_score(it[0]) or 5
            bad = 1 if s <= 2 else 0
            blocked = 1 if (it[1] or "").startswith("blocked") else 0
            # 皮肤站搜索页优先(领域知识入口,比视频/攻略页更可能有真皮肤)
            skin = 0 if (it[1] or "").startswith("皮肤站") else 1
            return (skin, blocked, bad, 0 if "下载按钮" in it[1] else 1, -s)

        ordered = sorted(agent_candidates, key=_rep_key)
        tried = 0
        tried_families: set[str] = set()
        agent_phase_started = time.monotonic()  # Agent 阶段总预算(防整体卡死)
        _AGENT_PHASE_BUDGET = 480.0
        for url, reason in ordered:
            if tried >= _MAX_AGENT_ATTEMPTS:
                break
            if time.monotonic() - agent_phase_started > _AGENT_PHASE_BUDGET:
                _emit(on_stage, "agent",
                      f"⏱️ Agent 阶段超过总预算 {_AGENT_PHASE_BUDGET:.0f}s,停止兜底")
                if verbose:
                    print(f"⏱️ Agent 阶段超过总预算,停止兜底")
                break
            if _is_paid_novel_host(url) and not login_email:
                # 正版付费平台无账号必败,不占用 Agent 预算
                if verbose:
                    print(f"    跳过: 付费小说平台({_host_family(url)}),未配置账号")
                _emit(on_stage, "agent", f"跳过付费小说平台({_host_family(url)}),未配置账号")
                _log_site(site_log, url, query, skipped="paid")
                continue
            fam = _host_family(url)
            if fam in tried_families:
                _emit(on_stage, "agent", f"跳过同站候选(已试过 {fam}): {url[:60]}")
                continue
            tried_families.add(fam)
            tried += 1
            _check_cancel(is_cancelled)
            if verbose:
                print(f"\n🤖 浏览器 Agent 处理: {url[:90]} ({reason})")
            _emit(on_stage, "agent",
                  f"🤖 第 {tried}/{_MAX_AGENT_ATTEMPTS} 次 Agent 尝试: {url[:80]} ({reason})")
            ares = _agent_fetch(url, file_types, out_dir, login_email, username,
                                verbose, reuse_cookies, is_cancelled, on_stage=on_stage,
                                query=query)
            ok = bool(ares and getattr(ares, "success", False))
            _log_site(site_log, url, query, agent_ok=ok)
            # 皮肤任务:Agent 最后停留页面的标题用于名字匹配验收
            # (64x64 皮肤像素图视觉校验不可靠,页面名才是权威信号;
            #  如 littleskin「星穹铁道 银狼 LV.999」归一化后含 银狼lv999)
            agent_page_title = getattr(ares, "final_title", "") or ""
            if ok:
                result.files = probe.found_files()
                if not result.files:
                    # Agent 报告成功但任务目录没有文件落地(工具把文件写到了别处):
                    # 不能宣判成功 —— 否则网页会显示 done 却无可下载文件
                    _emit(on_stage, "agent",
                          f"⚠️ 第 {tried} 次 Agent 报告成功但任务目录无文件落地,继续下一候选")
                    continue
                if security_scan and result.files:
                    kept = []
                    for f in result.files:
                        verdict, note = _security_gate(f)
                        if verdict in ("infected", "suspicious"):
                            if verbose:
                                print(f"🛡️  Agent 落地文件未过安全扫描({verdict}): {f} → 已删除")
                            continue
                        kept.append(f)
                    result.files = kept
                    if not kept:
                        if verbose:
                            print("🛡️  Agent 落地文件全部未过安全扫描,判定失败")
                        continue
                # 图片内容闸门:Agent 也可能下到无关图片(线上事故:银狼lv999 皮肤
                # → Agent 从 B站无关收藏夹下了张 E宝纯图片)。逐张校验,全不符则判失败。
                # 皮肤任务优先用「皮肤页标题名字匹配」验收(权威),无名字信号才走视觉严格校验。
                if _is_image_types(file_types) and result.files:
                    skin_name_ok = (_is_skin_intent(query, file_types)
                                    and _skin_name_match(agent_page_title, query))
                    passed = []
                    for f in result.files:
                        if skin_name_ok:
                            _emit(on_stage, "verify",
                                  f"皮肤页标题匹配目标名,通过: {Path(f).name}")
                            passed.append(f)
                            continue
                        verdict, detail = _verify_image_file(f, query, file_types)
                        if verdict is False:
                            _emit(on_stage, "verify",
                                  f"Agent 文件内容不符主题,已丢弃: {Path(f).name} ({detail[:80]})")
                            if verbose:
                                print(f"🖼️  Agent 落地图片内容不符({detail[:100]}),删除")
                            try:
                                Path(f).unlink(missing_ok=True)
                            except OSError:
                                pass
                            continue
                        passed.append(f)
                    result.files = passed
                    if not passed:
                        _emit(on_stage, "agent",
                              f"⚠️ 第 {tried} 次 Agent 图片全部内容不符,继续下一候选")
                        if verbose:
                            print("🖼️  Agent 落地图片全部内容不符主题,判定失败")
                        continue
                    _emit(on_stage, "verify", f"Agent 图片内容校验通过 {len(passed)} 张")
                result.sources.append(url)
                result.pan_links = list(dict.fromkeys(pan_links))
                result.success = True
                result.summary = f"Agent 路径完成,文件落地 {len(result.files)} 个"
                _emit(on_stage, "agent",
                      f"✅ 第 {tried} 次 Agent 成功: 文件落地 {len(result.files)} 个")
                return result
            _emit(on_stage, "agent", f"❌ 第 {tried} 次 Agent 未获文件: {url[:60]}")

    result.files = probe.found_files()
    result.pan_links = list(dict.fromkeys(pan_links))  # 网盘链接独立字段(用户可见)
    result.error = "所有候选均未获取到资源文件"
    if result.files:
        result.success = True
        result.summary = f"已获得 {len(result.files)} 个文件(部分成功)"
    elif pan_links and verbose:
        print(f"\n☁️  发现网盘分享链接 {len(pan_links)} 个(网盘解析为后续阶段):")
        for u in pan_links[:5]:
            print(f"     {u[:110]}")
    return result


def fetch_resource(
    query: str = "",
    seed_urls: Optional[list[str]] = None,
    file_types: Optional[tuple[str, ...]] = None,
    out_dir: str | Path = DEFAULT_OUT,
    engines: Optional[list[str]] = None,
    max_candidates: int = 16,
    use_agent_fallback: bool = True,
    login_email: str = "",
    username: str = "",
    security_scan: bool = True,
    reuse_cookies: bool = True,
    stream_media: bool = True,
    on_stage: Optional[Callable[[str, str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    verbose: bool = True,
) -> TaskResult:
    """核心入口(包装):跑完整管线;无论成功/失败/取消,结束后把访问过的站点
    交给 LLM 打分(0-9)生成短描述,录入站点信誉库 —— 下次任务按向量相似度
    查 top-k 优选好站、避开差站。录入为 best-effort,永不阻塞任务结果。
    """
    site_log: list[dict] = []
    try:
        return _pipeline(query=query, seed_urls=seed_urls, file_types=file_types,
                         out_dir=out_dir, engines=engines, max_candidates=max_candidates,
                         use_agent_fallback=use_agent_fallback, login_email=login_email,
                         username=username, security_scan=security_scan,
                         reuse_cookies=reuse_cookies, stream_media=stream_media,
                         on_stage=on_stage, is_cancelled=is_cancelled, verbose=verbose,
                         site_log=site_log)
    except TaskTimeout as e:
        # 总预算耗尽 → 明确失败(带已尝试信息),不让任务无限"卡住"
        result = TaskResult(success=False)
        result.files = []
        result.error = f"任务超时: {e}。搜索/分析阶段已尽,可尝试更换关键词或直接提供资源链接。"
        try:
            probe = FileProbe(out_dir, _infer_file_types(query, file_types))
            result.files = probe.found_files()
        except Exception:
            pass
        return result
    finally:
        _record_sites(site_log)


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlsplit

        return urlsplit(url).netloc.lower()
    except Exception:
        return url


def _log_site(site_log: Optional[list], url: str, query: str, **kw) -> None:
    """记录一个站点访问信号(按 host 合并,同一 host 保留最新信号)。"""
    if site_log is None:
        return
    host = _host_of(url)
    for e in site_log:
        if e.get("host") == host:
            for k, v in kw.items():
                if v is not None:
                    e[k] = v
            e.setdefault("url", url)
            e.setdefault("query", query)
            return
    entry: dict = {"host": host, "url": url, "query": query}
    for k, v in kw.items():
        if v is not None:
            entry[k] = v
    site_log.append(entry)


def _site_score(url: str) -> Optional[int]:
    """已知站点历史评分(0-9);未录入返回 None(信誉重排用)。"""
    try:
        from skills.siterep.store import score_of

        return score_of(_host_of(url))
    except Exception:
        return None


def _record_sites(site_log: list) -> None:
    """任务结束:访问过的站点交给 LLM 打分录入信誉库(best-effort,永不抛)。"""
    if not site_log:
        return
    try:
        from skills.siterep import record_feedback

        record_feedback(site_log)
    except Exception:
        pass


def _ext_match(url: str, file_types: tuple[str, ...]) -> bool:
    low = url.lower()
    if any(low.endswith(e) for e in file_types):
        return True
    # 无扩展名直链(如 littleskin raw/810649,分析已确认是文件):放行,
    # 由下载后的魔数嗅探+内容闸门验证 —— 不因缺扩展名错过真直链
    try:
        from urllib.parse import urlsplit

        last = urlsplit(url).path.rsplit("/", 1)[-1]
        return bool(last) and "." not in last
    except Exception:
        return False


def _host_family(url: str) -> str:
    """注册域近似(取域名最后两段):qq.com / xs8.cn / readnovel.com…
    用于 Agent 候选去重 —— 同一家的多个子域只试一次。"""
    try:
        from urllib.parse import urlsplit

        host = urlsplit(url).netloc.lower()
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:
        return url


def _is_paid_novel_host(url: str) -> bool:
    """付费/正版小说平台(起点/QQ阅读/红袖/书旗等):无账号时爬取/Agent 必败。"""
    try:
        from urllib.parse import urlsplit

        host = urlsplit(url).netloc.lower()
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in _PAID_NOVEL_HOSTS)


def _is_quark_share(url: str) -> bool:
    """夸克分享链接判定(pan.quark.cn/s/xxx)。"""
    try:
        from urllib.parse import urlsplit

        host = urlsplit(url).netloc.lower()
    except Exception:
        return False
    return host in ("pan.quark.cn", "quark.cn") or host.endswith(".pan.quark.cn")


def _quark_fetch_one(url: str, out_dir: Path, file_types: tuple[str, ...],
                     on_stage=None, is_cancelled=None) -> Optional[str]:
    """对单个夸克分享链接解析下载;无 Cookie / 分享失效返回 None。"""
    from skills.quark import QuarkAuthError, download_share

    preferred = [e for e in file_types if e in (".litematic", ".schematic", ".schem")]
    accept = list(file_types)
    try:
        r = download_share(url, dest_dir=str(out_dir),
                           preferred_exts=tuple(preferred),
                           accept_exts=tuple(accept))
    except QuarkAuthError as e:
        _emit(on_stage, "quark", f"夸克需 Cookie: {str(e)[:80]}")
        return None
    except Exception as e:
        _emit(on_stage, "quark", f"夸克解析失败: {type(e).__name__}: {str(e)[:80]}")
        return None
    if r.get("ok") and r.get("path"):
        return r["path"]
    return None


def _is_novel_intent(query: str, file_types) -> bool:
    """小说意图:查询含 小说/txt/章节/电子书/全文 等词;
    或显式文档类型(非全格式兜底)含 .txt。"""
    q = (query or "").lower()
    if any(k in q for k in ("小说", "txt", "章节", "电子书", "全文阅读")):
        return True
    ft = [f.lower() for f in (file_types or ())]
    if ".txt" in ft and len(ft) <= 12:  # 文档预设(10项)/显式 txt;全格式兜底(34项)不算
        return True
    return False


# 媒体二次检索噪声词:B站搜索前清掉「下载/无损/网盘」等,只留歌名/标题
_MEDIA_NOISE = re.compile(
    r"下载|歌曲|音乐|无损|mp3|flac|wav|网盘|免费|在线|完整版|mv|视频|b站|bilibili|"
    r"铃声|ost|的|第[0-9一二三四五六七八九十百千零两]+[集话期]",
    re.I,
)


def _is_media_intent(query: str, file_types) -> bool:
    """音视频意图:查询含 歌曲/音乐/无损/mp3/flac/视频/电影 等词;
    或显式媒体类型(非全格式兜底)。"""
    q = (query or "").lower()
    if any(k in q for k in ("歌", "音乐", "无损", "mp3", "flac", "wav", "铃声", "ost",
                            "视频", "mp4", "mkv", "电影", "电视剧", "动漫", "番剧",
                            "剧集", "mv")):
        return True
    ft = [f.lower() for f in (file_types or ())]
    media = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus",
             ".mp4", ".mkv", ".webm", ".avi", ".mov", ".m3u8", ".ts"}
    return any(e in ft for e in media) and len(ft) <= 12


def _clean_media_query(query: str) -> str:
    """媒体查询去噪:B站搜索用干净词(「大爱炼天 歌曲下载」→「大爱炼天」)。"""
    q = _MEDIA_NOISE.sub(" ", query or "")
    q = re.sub(r"[\s，。、,.;；:：'\"“”‘’!！?？【】\[\]()（）]+", " ", q).strip()
    q = q.replace(" ", "")
    return q or (query or "").strip()


def _rewrite_novel_query(query: str) -> str:
    """书名去噪 + 补网盘关键词,生成二次检索词。

    「下载 阵问长生 小说的txt全文」→「阵问长生 txt 下载 网盘」。
    书名过短(<2)/过长(>20)或改写无变化时返回空串。
    """
    book = _NOVEL_NOISE.sub(" ", query)
    book = re.sub(r"[\s，。、,.;；:：'\"“”‘’!！?？【】\[\]()（）]+", " ", book).strip()
    book = book.replace(" ", "")
    if len(book) < 2 or len(book) > 12:
        return ""
    rq = f"{book} txt 下载 网盘"
    return rq if rq != query else ""


def _is_media_types(file_types: tuple[str, ...]) -> bool:
    """file_types 是否包含音视频媒体类型(流式下载分流条件)。"""
    from skills.streaming import MEDIA_EXTS

    return any(ft.lower() in MEDIA_EXTS for ft in (file_types or ()))


def _download_one(url: str, out_dir: Path, file_types: tuple[str, ...],
                  stream_media: bool = True,
                  on_stage: Optional[Callable[[str, str], None]] = None,
                  is_cancelled: Optional[Callable[[], bool]] = None) -> Optional[str]:
    """下载单个直链。音视频大文件走 skills.streaming 流式下载,其余走 delivery。"""
    if stream_media and _is_media_types(file_types):
        from skills.streaming import stream_download

        # 预检:URL 指向安装包/网页/图片 → 不是音视频,不下载
        if _is_non_media_url(url):
            _emit(on_stage, "download", f"URL 指向安装包/网页而非音视频,跳过: {url[:80]}")
            return None

        _emit(on_stage, "download", f"流式下载(音视频): {url[:90]}")
        last_pct = [0]

        def on_progress(done: int, total: int) -> None:
            if total > 0:
                pct = int(done * 100 / total)
                if pct - last_pct[0] >= 5 or pct == 100:
                    last_pct[0] = pct
                    _emit(on_stage, "download", f"流式下载 {pct}% ({done}/{total} B)")

        r = stream_download(url, out_dir, on_progress=on_progress)
        if not r.ok:
            return None
        # 魔数校验:下到的是不是真音视频(酷狗客户端安装包/CSDN 登录页/171B 假 mp3
        # 都以 .ok=True 落地,但内容不是媒体 —— 必须丢弃,否则白占空间还骗过探针)
        if not _is_media_file(r.path):
            try:
                Path(r.path).unlink(missing_ok=True)
            except OSError:
                pass
            _emit(on_stage, "download",
                  f"下载内容非音视频(疑似客户端/网页/空文件),已丢弃: {Path(r.path).name}")
            return None
        return r.path

    from delivery import download

    expected_ext = ""
    for ext in file_types:
        if url.lower().endswith(ext):
            expected_ext = ext
            break
    if not expected_ext and file_types:
        # 无扩展名下载链(下载按钮直链):启用 HTML 拒绝 + 按 Content-Type 补扩展名
        # (乐书谷 down.xxx/down/<id> 无扩展名但返回 text/plain 全文)
        expected_ext = file_types[0]
    r = download(url, out_dir, expected_ext=expected_ext, min_size=100)
    if r.ok:
        # 图片任务内容闸门:扩展名对不代表内容对 —— 皮肤查询可能下到修改器 exe /
        # 网页 HTML(线上事故:『我的世界 银狼lv999 皮肤』下到 FLiNG_Trainer_c30_.exe)。
        # 纯图片类型任务必须通过魔数嗅探,否则丢弃换候选(或留给 Agent + AI 视觉复核)。
        if _is_image_types(file_types) and not _is_image_file(r.path):
            try:
                Path(r.path).unlink(missing_ok=True)
            except OSError:
                pass
            _emit(on_stage, "download",
                  f"下载内容非图片(疑似exe/网页冒充),已丢弃: {Path(r.path).name}")
            return None
        return r.path
    return None


def _security_gate(path: str) -> tuple[str, str]:
    """下载查毒闸门:扫描文件,infected/suspicious 时删除并返回 (verdict, 摘要)。

    引擎缺失时扫描器返回 verdict=unknown(ok=True)→ 放行但诚实标注覆盖不足,
    不阻塞下载;只有确认恶意/可疑才拦截。
    """
    from skills.security import scan_file

    try:
        r = scan_file(path)
    except Exception as e:
        return "unknown", f"扫描异常(放行): {type(e).__name__}: {str(e)[:120]}"
    if r.verdict in ("infected", "suspicious"):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    return r.verdict, r.summary


def _agent_fetch(url: str, file_types: tuple[str, ...], out_dir: Path,
                 login_email: str = "", username: str = "",
                 verbose: bool = True, reuse_cookies: bool = True,
                 is_cancelled: Optional[Callable[[], bool]] = None,
                 on_stage: Optional[Callable[[str, str], None]] = None,
                 query: str = "") -> Optional["AgentResult"]:
    """浏览器 Agent:访问页面 →(注册/登录)→ 定位下载 → 下载。

    reuse_cookies=True 时按站点域复用 accounts/cookies/ 的登录态
    (与 run_account.py 同一存储,免重复登录),Agent 成功后回存刷新。
    on_stage 转发 Agent 的每一步自汇报(观察/推理/动作/结果)为 agent 阶段事件。
    返回 AgentResult(含 final_title,皮肤任务名字匹配验收用);异常返回 None。
    """
    import secrets
    import string

    from urllib.parse import urlsplit

    from agent import AccountAgent
    from agent.agent import AgentResult
    from agent.browser import BrowserSession
    from agent.cookies import find_cookies, has_cookies
    from agent.llm import LLMClient

    # 子域回退查找登录态(导入的本地 cookie 按注册域存档,www/m 子域都能命中)
    ck = find_cookies(urlsplit(url).netloc.lower(), login_email)
    ck_path = ck if (reuse_cookies and ck is not None and has_cookies(ck)) else None

    exts = " / ".join(file_types)
    # 皮肤意图:AI 驱动补源 —— 给 Agent 提示皮肤站,由它决定去哪找(不写死 URL 规则)
    skin_hint = ""
    if _is_skin_intent(query, file_types):
        name = _skin_search_name(query)
        skin_hint = (
            f"\n⚠️ 这是 Minecraft 皮肤查询,目标皮肤名≈「{name or '查询主题'}」。"
            f"优先去皮肤站(namemc.com / littleskin.cn / mcskins.org / skindex.com / "
            f"planetminecraft.com)搜索该名字;交付物必须是 PNG 皮肤文件本身"
            f"(64/128/256 方形),宣传图/预览图/网页截图不算完成。"
            f"**皮肤页标题归一化后(忽略大小写/空格/标点)必须包含目标名**"
            f"(如「星穹铁道 银狼 LV.999」归一化为 星穹铁道银狼lv999 包含 银狼lv999 算匹配;"
            f"Level999Villager 不含「银狼」不算)—— 下载前先看页面标题确认。"
            f"littleskin 的皮肤详情页(littleskin.cn/skinlib/show/数字)analyze_page 会给出 "
            f"raw/数字 直链,直接下载即可(免登录)。"
        )
    # 密码由任务层生成注入(不让 LLM 自己编,减少表单循环)并持久化,防止账号丢失
    import json as _json

    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(14))
    if login_email:
        creds_path = Path("accounts/credentials.json")
        creds = {}
        if creds_path.exists():
            try:
                creds = _json.loads(creds_path.read_text(encoding="utf-8"))
            except Exception:
                creds = {}
        key = f"schematics-{username or 'default'}"
        creds[key] = {"email": login_email, "username": username, "password": password}
        try:
            creds_path.parent.mkdir(parents=True, exist_ok=True)
            creds_path.write_text(_json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    account_hint = ""
    if login_email:
        account_hint = (
            f"\n账号信息: 注册邮箱={login_email}"
            + (f",用户名={username}" if username else "")
            + f",密码={password}(用于注册/登录,请原样输入,不要修改);"
            + "邮箱验证码/安全链接用 mail_code 工具自动收取。"
        )
    else:
        account_hint = (
            "\n注意: 本次没有可用的注册邮箱。若页面需要登录/注册,用 human 工具请求人工介入,"
            "不要自行注册新账号。"
        )
    goal = (
        f"访问 {url} 寻找可下载的 {'/'.join(file_types)} 文件。\n"
        f"⚠️ 聚焦:只处理 {url} 这个页面及其所在站点;候选列表里的其他站点不要访问、"
        f"不要写脚本去抓其他站点(B站视频用 bilibili_fetch 工具,勿裸 HTTP)。\n"
        f"步骤: 1) 打开页面;若被 Cloudflare 校验,用 wait 等待通过;"
        f"若需登录/注册,按账号信息完成(见下方;若无邮箱,用 human 请求人工介入);"
        f"2) 用 analyze_page 提取下载链接;"
        f"3) 找到 {exts} 直链或下载按钮:若按钮文本含 Download/下载,直接点击它"
        f"(浏览器会自动捕获下载文件到 {out_dir});若拿到直链 URL,用 download 工具下载;"
        f"4) 文件落地(大小>0)才算完成,直接 done(success=true)。\n"
        f"若需要人工介入(验证码过不去/需要付费),用 human 工具请求。"
        f"{skin_hint}"
        f"{account_hint}"
    )
    try:
        _check_cancel(is_cancelled)
        with BrowserSession(headless=True, downloads_dir=out_dir,
                            cookies_path=ck_path) as session:
            # AI 自汇报进度:每步 观察/推理/动作/结果 → agent 阶段事件(SSE/网页日志)
            def _agent_progress(line: str) -> None:
                _emit(on_stage, "agent", _progress_line(line))

            agent = AccountAgent(session=session, llm=LLMClient(), goal=goal,
                                 allowed_domain="", max_steps=60,
                                 max_seconds=300.0,   # 每候选时间预算(防单候选卡死)
                                 progress=_agent_progress)
            # 注入任务输出目录 + 文件类型:Agent 的 download/流式/拼接等工具默认落到 downloads/,
            # 必须强制到任务目录 —— 否则交付层收不到文件,网页 done 却没有下载按钮;
            # file_types 供站点工具按任务类型选形态(bilibili_fetch 据此判 video/audio)。
            from types import SimpleNamespace

            agent.ctx.task = SimpleNamespace(out_dir=str(out_dir),
                                             file_types=tuple(file_types))
            session.goto(url)
            res = agent.run()
            _check_cancel(is_cancelled)
            if res.success and reuse_cookies:
                try:
                    session.save_cookies(ck)   # BrowserSession 方法,内部取 context.storage_state
                    if verbose:
                        print(f"🍪 已回存登录态 Cookie → {ck.name}")
                except Exception as e:
                    if verbose:
                        print(f"⚠️  Cookie 保存失败: {type(e).__name__}: {str(e)[:100]}")
            return res
    except TaskCancelled:
        raise
    except Exception as e:
        if verbose:
            print(f"Agent 执行异常: {type(e).__name__}: {str(e)[:150]}")
        _emit(on_stage, "agent", f"Agent 执行异常: {type(e).__name__}: {str(e)[:80]}")
        return None
