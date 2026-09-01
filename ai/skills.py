"""AI 核心技能包 — 意图解析/语义检索/文件验证/拆包/一键粗扫。

全部通过 @tool 注册进 skills/core 注册表,AgentCore 按目录调用。
规则能力(检索/分析/下载)保持为底层工具,这里提供的是"AI 决策型"技能。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from skills.core import ToolResult, tool, get_registry


def _task_out_dir(ctx, default: str) -> str:
    """任务上下文强制落盘目录(与 agent/tools.py 一致):
    fetch_resource 任务内 ctx.task.out_dir 已注入,写文件必须落到任务目录,
    否则文件在 downloads/ 交付层收不到,网页 done 却没有下载按钮。"""
    if ctx is not None:
        task = getattr(ctx, "task", None)
        out = getattr(task, "out_dir", None)
        if out:
            return str(out)
    return default

# ---------------------------------------------------------------- 意图解析

@tool(
    "intent_parse",
    "解析用户请求为结构化资源意图:识别资源类型(kind)、首选格式(preferred_exts)、"
    "兜底格式(accept_exts)、绑定强度(binding=strict/lenient)。"
    "格式词汇: 投影→.litematic(首选)/.schematic/.schem/.zip;壁纸→.jpg/.jpeg/.png/.webp;"
    "文档→.pdf/.epub/.mobi;视频→.mp4/.mkv。用户说'只要X'时 binding=strict 且只填 preferred。",
    parameters={
        "type": "object",
        "properties": {"request": {"type": "string", "description": "用户原始请求"}},
        "required": ["request"],
    },
    category="read",
    timeout_ms=60_000,
)
def _intent_parse_tool(request: str, ctx=None) -> ToolResult:
    from .state import ResourceIntent

    # 已有意图且请求一致 → 直接复用(状态投影,避免重复解析)
    if ctx and ctx.state and ctx.state.intent and ctx.state.intent.query == request:
        return ToolResult.success(f"复用已有意图: {ctx.state.intent.to_dict()}",
                                  data=ctx.state.intent.to_dict())
    from agent.llm import LLMClient

    prompt = (
        "把用户的资源请求解析为 JSON,字段:\n"
        '{"query": "重写后的检索词(保留关键词)", "kind": "schematic|image|document|media|generic",\n'
        ' "preferred_exts": ["首选扩展名列表"], "accept_exts": ["兜底扩展名列表"],\n'
        ' "binding": "strict"|"lenient", "format_unknown": bool,\n'
        ' "sources_hint": ["建议来源站域名,按资源类型给"], "constraints": {}}\n'
        "规则: 说'只要/只下X'→binding=strict 且 preferred 只含 X;投影类→.litematic 首选;"
        "壁纸/皮肤类→.jpg/.jpeg/.png/.webp;无法确定格式→format_unknown=true。\n"
        "来源站建议(领域知识): Minecraft 皮肤→mcskins.org/namemc.com/skindex.com/mctoolbox.net;"
        "建筑投影→minecraft-schematics.com/mineschematic.com;壁纸→壁纸图站;"
        "软件→github releases/官网。没有明确来源站时 sources_hint 给空数组。\n"
        f"请求: {request}"
    )
    try:
        data = LLMClient().chat_json([{"role": "user", "content": prompt}], max_tokens=1500)
    except Exception as e:
        return ToolResult.failure(f"意图解析失败: {type(e).__name__}: {str(e)[:120]}")
    intent = ResourceIntent(
        query=str(data.get("query") or request),
        kind=str(data.get("kind") or "generic"),
        preferred_exts=tuple(data.get("preferred_exts") or ()),
        accept_exts=tuple(data.get("accept_exts") or ()),
        binding=str(data.get("binding") or "lenient"),
        format_unknown=bool(data.get("format_unknown", False)),
        sources_hint=tuple(data.get("sources_hint") or ()),
        constraints=dict(data.get("constraints") or {}),
    )
    if ctx and ctx.state:
        ctx.state.intent = intent  # 意图写入状态(强绑定贯穿全程)
    return ToolResult.success(f"意图: {intent.to_dict()}", data=intent.to_dict())


# ---------------------------------------------------------------- 语义检索

@tool(
    "search",
    "多引擎检索 + AI 语义重排。返回按相关性排序的候选列表,已剔除与意图无关的结果。"
    "engines 可指定(如 [\"bing\",\"baidu\"]),缺省全部;per_engine 每引擎取几条(默认 12);"
    "rerank=true(默认)时用 AI 批量打分。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词(可用意图重写后的 query)"},
            "engines": {"type": "array", "items": {"type": "string"}, "description": "引擎列表,可省"},
            "per_engine": {"type": "integer", "description": "每引擎条数,默认 12"},
            "rerank": {"type": "boolean", "description": "是否 AI 语义重排,默认 true"},
        },
        "required": ["query"],
    },
    category="search",
    timeout_ms=120_000,
)
def _search_tool(query: str, engines: Optional[list] = None, per_engine: int = 12,
                 rerank: bool = True, ctx=None) -> ToolResult:
    from search import search as search_all

    try:
        results = search_all(query, engines=engines, per_engine=per_engine,
                             probe=False, limit=per_engine * 3)
    except Exception as e:
        return ToolResult.failure(f"检索失败: {type(e).__name__}: {str(e)[:120]}")
    if rerank and results:
        from .rerank import rerank_candidates

        results = rerank_candidates(query, results, keep=10)
    if ctx and ctx.state:
        ctx.state.candidates = results
    lines = [f"候选 {len(results)} 条:"]
    for i, r in enumerate(results[:10], start=1):
        lines.append(f"  {i}. [{r.score:.1f}] {r.title[:50]} | {r.url[:90]}")
    return ToolResult.success("\n".join(lines), data=[r.to_dict() for r in results])


# ---------------------------------------------------------------- 文件验证

@tool(
    "verify_file",
    "验证下载文件:①魔法字节嗅探真实格式 ②(图片)AI 视觉判断内容是否与意图主题匹配"
    "(防止下到营业执照/广告/随机图冒充皮肤壁纸)。"
    "返回 {path, magic_kind, ok, reason}。下载后必须调用它确认文件真实可用且内容正确。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "本地文件路径"},
            "expected_kind": {"type": "string", "description": "期望类型(可选): png/jpg/zip/litematic/pdf 等"},
            "expected_subject": {"type": "string", "description": "期望内容主题(可选): 如 'Minecraft 皮肤'/'凡人修仙传壁纸';缺省用任务意图"},
        },
        "required": ["path"],
    },
    category="read",
    timeout_ms=90_000,
)
def _verify_file_tool(path: str, expected_kind: str = "", expected_subject: str = "",
                      ctx=None) -> ToolResult:
    from .magic import sniff_file

    p = Path(path)
    if not p.exists():
        return ToolResult.failure(f"文件不存在: {path}")
    if p.stat().st_size == 0:
        return ToolResult.failure(f"文件为空: {path}")
    info = sniff_file(p)
    kind = (info or {}).get("kind", "")
    note = (info or {}).get("note", "未知类型")
    ok = True
    reason = f"魔法字节={note}, 大小={p.stat().st_size}B"
    # ① 格式族匹配:期望类型 → 允许的魔数 kind 集合(魔数标签见 ai/magic.py)
    _FAMILY = {
        "image": {"jpg", "jpeg", "png", "webp", "gif", "bmp", "avif"},
        "audio": {"mp3", "ogg", "flac", "wav", "aac", "m4a", "mp4"},   # m4a/mp4 同为 ftyp 容器
        "video": {"mp4", "mkv", "webm", "avi", "mov", "ogg"},
        "media": {"mp4", "mkv", "webm", "avi", "mov", "mp3", "ogg", "flac", "wav", "m4a"},
        "document": {"pdf", "zip", "gzip", "mobi", "djvu", "txt", "html"},  # epub/docx 是 zip 外衣
        "archive": {"zip", "7z", "rar", "gzip", "tar"},
        "litematic": {"gzip", "nbt", "zip"},
        "schematic": {"gzip", "nbt", "zip"},
    }
    if expected_kind and kind != expected_kind:
        if kind in _FAMILY.get(expected_kind, set()):
            ok, reason = True, f"{reason} (属于 {expected_kind} 族)"
        else:
            ok = False
            reason = f"{reason}, 期望 {expected_kind} 但实际 {kind or '未知'}"
    # ② 图片内容级验证(视觉):格式对了不代表内容对(营业执照也是合法 JPEG)
    if ok and kind in ("jpg", "jpeg", "png", "webp", "gif", "bmp"):
        subject = expected_subject
        if not subject and ctx and ctx.state and ctx.state.intent:
            subject = ctx.state.intent.query or ctx.state.intent.kind
        if subject:
            from .vision import verify_image_content

            verdict = verify_image_content(p.read_bytes(), subject)
            if verdict.get("ok") is True:
                ok = True
                reason = f"{reason}; 内容验证通过: {verdict.get('content', '')[:80]}"
            elif verdict.get("ok") is False:
                ok = False
                reason = (f"{reason}; ❌ 内容不符: 实际内容={verdict.get('content', '')[:80]}, "
                          f"理由={verdict.get('reason', '')[:80]}")
            # ok=None(视觉不可用) → 不阻塞,保持格式验证结果
    if ok:
        return ToolResult.success(
            f"验证通过: {reason}",
            data={"path": str(p), "magic_kind": kind, "ok": True, "reason": reason},
        )
    return ToolResult.failure(
        f"验证失败: {reason}",
        data={"path": str(p), "magic_kind": kind, "ok": False, "reason": reason},
    )


# ---------------------------------------------------------------- 安全查毒

@tool(
    "scan_file",
    "下载文件安全查毒(下载安全性校对):多引擎扫描 = 开源杀毒 ClamAV(clamd/clamscan)"
    "+ YARA 规则(可选) + 内置启发式(魔法字节伪装/压缩炸弹/脚本载荷/双重扩展名)。"
    "返回 verdict: clean=安全; infected=检出病毒,必须删除换源;"
    "suspicious=可疑(严格模式默认拒绝); unknown=杀毒引擎未就绪,仅启发式覆盖(不阻塞,但覆盖不足)。"
    "下载完成后必须调用它做安全闸门(与 verify_file 内容验证互补):infected/suspicious 一律丢弃换源,"
    "不得交付给用户。引擎不可用时会给出 ClamAV 安装提示。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "本地文件路径"},
            "strict": {"type": "boolean", "description": "suspicious 是否判失败,默认 true"},
            "engines": {"type": "array", "items": {"type": "string", "enum": ["clamav", "yara", "heuristic"]},
                        "description": "指定引擎子集,默认全部可用引擎"},
            "yara_rules": {"type": "string", "description": "额外 YARA 规则目录(可选)"},
        },
        "required": ["path"],
    },
    category="read",
    timeout_ms=180_000,
)
def _scan_file_tool(path: str, strict: bool = True, engines=None,
                    yara_rules: str = "", ctx=None) -> ToolResult:
    from skills.security import scan_file

    p = Path(path)
    if not p.exists():
        return ToolResult.failure(f"文件不存在: {path}")
    r = scan_file(p, engines=engines, yara_rules=yara_rules, strict=strict)
    data = r.to_dict()
    if r.verdict == "infected" or (r.verdict == "suspicious" and strict):
        return ToolResult.failure(r.summary, data=data)
    return ToolResult.success(r.summary, data=data)


# ---------------------------------------------------------------- 流式下载(音视频)

@tool(
    "download_stream",
    "流式下载音视频等大文件:①直链分段并发(Range 分片多线程,分片级断点续传)②HLS/m3u8 "
    "自动识别(分段并发,支持 AES-128 加密)③边下边交付 ④可选限速。"
    "适合 mp4/mkv/webm/mp3/flac/m4a 等大文件与 m3u8 流;普通小文件(文档/图片/投影)用 download。"
    "返回 {path, size, strategy, segments, sha256};失败给 error 原因(如 fmp4 需 ffmpeg)。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "文件直链或 m3u8 地址"},
            "dest_dir": {"type": "string", "description": "输出目录,默认 downloads/"},
            "filename": {"type": "string", "description": "输出文件名(缺省从 URL 猜)"},
            "strategy": {"type": "string", "enum": ["auto", "direct", "hls"],
                         "description": "auto=按探测自动选(默认)"},
            "segments": {"type": "integer", "description": "直链分段并发数,默认 4"},
        },
        "required": ["url"],
    },
    category="execute",
    timeout_ms=1_800_000,
    concurrency_safe=False,
)
def _download_stream_tool(url: str, dest_dir: str = "downloads", filename: str = "",
                          strategy: str = "auto", segments: int = 4,
                          ctx=None) -> ToolResult:
    from skills.streaming import stream_download

    dest_dir = _task_out_dir(ctx, dest_dir or "downloads")

    def on_progress(done: int, total: int) -> None:
        if ctx and ctx.state:
            pass  # 进度由任务层 on_stage 上报,这里保持静默

    r = stream_download(url, dest_dir, filename=filename, strategy=strategy,
                        segments=segments, on_progress=on_progress)
    if r.ok:
        return ToolResult.success(
            f"流式下载成功: {r.path} ({r.size} 字节, 策略={r.strategy}, 分段={r.segments})",
            data=r.to_dict(),
        )
    return ToolResult.failure(f"流式下载失败: {r.error}", data=r.to_dict())


# ---------------------------------------------------------------- 万能下载

@tool(
    "universal_download",
    "万能下载器(一个入口搞定音视频):①播放页/聚合页自动找 m3u8 流地址并落地;"
    "②m3u8 切片流(HLS)分段并发合并(ts 自带合并,支持 AES-128;fmp4 自动用 ffmpeg 兜底);"
    "③Cloudflare 'Just a moment' 拦截时用浏览器会话过 CF 并复用 cookie;④直链走流式分段下载。"
    "适合:动漫/影视播放页、m3u8 流、被 CF 挡的直链。普通小文件用 download 即可。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "直链 / m3u8 / 播放页 URL"},
            "dest_dir": {"type": "string", "description": "输出目录,默认 downloads/"},
            "filename": {"type": "string", "description": "输出文件名(缺省自动生成)"},
            "referer": {"type": "string", "description": "防盗链 Referer(缺省用 URL 自身)"},
        },
        "required": ["url"],
    },
    category="execute",
    timeout_ms=3_600_000,
    concurrency_safe=False,
)
def _universal_download_tool(url: str, dest_dir: str = "downloads",
                             filename: str = "", referer: str = "",
                             ctx=None) -> ToolResult:
    from skills.universal import universal_download

    dest_dir = _task_out_dir(ctx, dest_dir or "downloads")
    r = universal_download(url, dest_dir, filename=filename, referer=referer)
    if r.ok:
        return ToolResult.success(
            f"万能下载成功: {r.path} ({r.size} 字节, 策略={r.strategy})",
            data=r.to_dict(),
        )
    return ToolResult.failure(f"万能下载失败: {r.error}", data=r.to_dict())


# ---------------------------------------------------------------- 拆包检查

@tool(
    "inspect_archive",
    "打开压缩包检查内含文件类型(投影/蓝图常打包在 zip 里)。"
    "返回文件清单与命中的意图格式(如内含 .litematic)。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "zip 文件路径"}},
        "required": ["path"],
    },
    category="read",
    timeout_ms=30_000,
)
def _inspect_archive_tool(path: str, ctx=None) -> ToolResult:
    import zipfile

    p = Path(path)
    if not p.exists() or not zipfile.is_zipfile(p):
        return ToolResult.failure(f"不是有效的 zip: {path}")
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
    except Exception as e:
        return ToolResult.failure(f"打开 zip 失败: {type(e).__name__}: {str(e)[:100]}")
    exts = [n.rsplit(".", 1)[-1].lower() for n in names if "." in n.rsplit("/", 1)[-1]]
    from collections import Counter

    counter = Counter(exts)
    preferred_hits = []
    if ctx and ctx.state and ctx.state.intent:
        for n in names:
            m = ctx.state.intent.ext_matches(n)
            if m == "preferred":
                preferred_hits.append(n)
    lines = [f"内含 {len(names)} 个文件;扩展名分布: {dict(counter.most_common(6))}"]
    if preferred_hits:
        lines.append(f"🎯 命中首选格式: {preferred_hits[:5]}")
    lines.append("前 10 个文件:")
    for n in names[:10]:
        lines.append(f"  {n[:90]}")
    return ToolResult.success("\n".join(lines),
                              data={"names": names[:50], "preferred_hits": preferred_hits,
                                    "ext_counter": dict(counter.most_common(6))})


# ---------------------------------------------------------------- 夸克网盘

@tool(
    "quark_resolve",
    "解析夸克网盘分享链接(pan.quark.cn/s/xxx,可带密码),列出分享内全部文件"
    "(含子目录,给出名称/大小/目录路径)。用于找到的候选是夸克网盘链接时,"
    "先看清单确认里面有用户要的资源,再决定下载。匿名可用,不需要 Cookie。",
    parameters={
        "type": "object",
        "properties": {
            "share_url": {"type": "string", "description": "夸克分享链接,如 https://pan.quark.cn/s/xxxx"},
            "password": {"type": "string", "description": "提取码(链接里已带 ?pwd= 时可省略)"},
            "max_depth": {"type": "integer", "description": "递归子目录深度,默认 3"},
        },
        "required": ["share_url"],
    },
    category="search",
    timeout_ms=90_000,
)
def _quark_resolve_tool(share_url: str, password: str = "", max_depth: int = 3,
                        ctx=None) -> ToolResult:
    from skills.quark import list_share

    try:
        r = list_share(share_url, password=password, max_depth=max_depth)
    except Exception as e:
        return ToolResult.failure(f"夸克解析失败: {type(e).__name__}: {str(e)[:140]}")
    files = [f for f in r["files"] if not f.get("dir")]
    lines = [f"分享 {r['pwd_id']} 内文件 {len(files)} 个:"]
    for f in sorted(files, key=lambda x: -(x.get("size") or 0))[:30]:
        sz = f.get("size") or 0
        lines.append(f"  {f.get('path', '')} ({_fmt_bytes(sz)})")
    return ToolResult.success("\n".join(lines), data=r)


@tool(
    "quark_download",
    "从夸克网盘分享链接自动下载目标文件:解析 → 按意图/文件名过滤选文件 → 转存 → 换直链 → 下载落地。"
    "用于候选是 pan.quark.cn 分享链接且 quark_resolve 已确认里面有目标资源时。"
    "file_filter 可指定文件名关键词(如 '投影');缺省按意图的格式偏好(首选扩展名>兜底>最大文件)选。"
    "需要已导入夸克 Cookie(一次性,之后全自动;未配置会明确报错)。",
    parameters={
        "type": "object",
        "properties": {
            "share_url": {"type": "string", "description": "夸克分享链接"},
            "password": {"type": "string", "description": "提取码(链接已带 ?pwd= 时可省略)"},
            "file_filter": {"type": "string", "description": "文件名关键词过滤(可选)"},
            "dest_dir": {"type": "string", "description": "输出目录,默认 downloads/"},
        },
        "required": ["share_url"],
    },
    category="execute",
    timeout_ms=600_000,
    concurrency_safe=False,
)
def _quark_download_tool(share_url: str, password: str = "", file_filter: str = "",
                         dest_dir: str = "downloads", ctx=None) -> ToolResult:
    from skills.quark import download_share

    dest_dir = _task_out_dir(ctx, dest_dir or "downloads")
    preferred = accept = ()
    if ctx and ctx.state and ctx.state.intent:
        preferred = tuple(ctx.state.intent.preferred_exts or ())
        accept = tuple(ctx.state.intent.accept_exts or ())
    try:
        r = download_share(share_url, password=password, file_filter=file_filter,
                           dest_dir=dest_dir, preferred_exts=preferred,
                           accept_exts=accept)
    except Exception as e:
        return ToolResult.failure(f"夸克下载失败: {type(e).__name__}: {str(e)[:160]}")
    return ToolResult.success(
        f"夸克下载成功: {r['file_name']} ({r['size']} 字节) -> {r['path']}",
        data={"path": r["path"], "file_name": r["file_name"], "size": r["size"]},
    )


def _fmt_bytes(n: int) -> str:
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


# ---------------------------------------------------------------- 一键粗扫

@tool(
    "batch_research",
    "一键粗扫:一次完成 检索→候选分析→直链快路径下载,返回落地的文件。"
    "适合简单请求(明确直链可得);复杂请求(需登录/SPA/格式裁决)请改用细粒度技能。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词"},
            "out_dir": {"type": "string", "description": "下载目录,默认 downloads/"},
            "max_candidates": {"type": "integer", "description": "分析候选上限,默认 16"},
        },
        "required": ["query"],
    },
    category="execute",
    timeout_ms=600_000,
    concurrency_safe=False,
)
def _batch_research_tool(query: str, out_dir: str = "downloads",
                         max_candidates: int = 16, ctx=None) -> ToolResult:
    from agent.tasks.fetch_resource import fetch_resource

    out_dir = _task_out_dir(ctx, out_dir or "downloads")
    result = fetch_resource(query=query, file_types=None, out_dir=out_dir,
                            max_candidates=max_candidates,
                            use_agent_fallback=False, verbose=False)
    if result.files:
        return ToolResult.success(
            f"粗扫完成: 下载 {len(result.files)} 个文件 -> {result.files}",
            data={"files": result.files, "summary": result.summary},
        )
    return ToolResult.failure(
        f"粗扫未获文件(直链路径无果): {result.error or '无直链'}。"
        "可改用 search/analyze_page/download 细粒度处理。",
        data={"error": result.error},
    )


@tool(
    "fetch_novel_txt",
    "小说章节拼接:给定书籍页面 URL(笔趣阁等逐章站),自动爬取章节目录、逐章抓取正文,"
    "合并为单体 txt 文件。解决小说站只有分章 HTML、没有全本 txt 下载的问题。"
    "返回落地文件路径与章节数;失败返回原因(未发现目录/正文提取失败等)。",
    parameters={
        "type": "object",
        "properties": {
            "book_url": {"type": "string", "description": "书籍页面 URL(含章节目录)"},
            "out_dir": {"type": "string", "description": "输出目录,默认 downloads/"},
            "max_chapters": {"type": "integer", "description": "最多抓取章节数,默认 1500"},
        },
        "required": ["book_url"],
    },
    category="execute",
    timeout_ms=900_000,
    concurrency_safe=False,
)
def _fetch_novel_tool(book_url: str, out_dir: str = "downloads",
                      max_chapters: int = 1500, ctx=None) -> ToolResult:
    from skills.novel import fetch_novel_txt

    out_dir = _task_out_dir(ctx, out_dir or "downloads")
    r = fetch_novel_txt(book_url, out_dir=out_dir, max_chapters=max_chapters)
    if r.get("ok"):
        note = f"({r['chapters']}/{r['total']} 章)" + (f" - {r['note']}" if r.get("note") else "")
        return ToolResult.success(f"小说拼接完成: {r['path']} {note}",
                                  data={"path": r["path"], "chapters": r["chapters"],
                                        "total": r["total"], "title": r.get("title", "")})
    return ToolResult.failure(f"小说拼接失败: {r.get('error') or '未知原因'}",
                              data={"error": r.get("error")})


def registered_names() -> list[str]:
    return [s.name for s in get_registry().all()]
