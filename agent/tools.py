"""技能注册 — 全部工具通过 DSH 式 @tool 装饰器单点定义。

对齐 DeepSeek Harness defineTool:name/description/参数 schema/category/
超时/并发安全 一处声明,注册表是唯一真相源。Agent 只认注册表:
- 模型看到 catalog() 的工具目录;
- 执行走 registry.invoke(先校验参数,违规即拒绝并回馈 violations);
- 新增技能 = 一个 @tool 函数,不改 agent 循环。

本模块在 import 时完成注册(side-effect),与 DSH「注册是效果」一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from skills.core import ToolResult, tool, get_registry

# 站点信誉技能(LLM 打分 + 向量检索)—— import side-effect 注册 record_site_feedback/site_lookup
import skills.siterep as _siterep  # noqa: F401
# B站音频抓取技能(浏览器会话过反爬 + DASH 流提取)
import skills.bilibili as _bilibili  # noqa: F401
# AI 本地程序沙盒(read/write/run,自写脚本解决站点特定问题)
import skills.sandbox as _sandbox  # noqa: F401

# ---------------------------------------------------------------- 邮箱收码

from skills.mail import ImapMailbox, extract_link, extract_verification_code
from skills.mail.config import MailConfig


def _wait_mail_secret(sender_hint: str = "", timeout: int = 150,
                      poll_interval: int = 8) -> dict:
    import time

    cfg = MailConfig.from_env()
    cfg.require_credentials()
    deadline = time.monotonic() + timeout
    seen: set[str] = set()
    with ImapMailbox(host=cfg.host, email=cfg.email, password=cfg.password,
                     port=cfg.port, folder=cfg.folder, timeout=cfg.timeout) as mb:
        while time.monotonic() < deadline:
            try:
                for m in mb.fetch_unseen(mark_seen=False):
                    key = m.message_id or f"{m.from_}|{m.subject}|{m.date}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if sender_hint and sender_hint.lower() not in m.from_.lower() \
                            and sender_hint.lower() not in m.subject.lower():
                        continue
                    code_r = extract_verification_code(m)
                    link = extract_link(m)
                    if code_r or link:
                        mb.mark_seen(m.num)
                        return {"code": code_r.code if code_r else None, "link": link}
            except Exception:
                mb.ensure_connected()
            time.sleep(poll_interval)
    return {"code": None, "link": None}


@tool(
    "mail_code",
    "等待邮箱验证码邮件并返回验证码或安全登录链接。页面提示'验证码/链接已发送到邮箱'后调用。"
    "若拿到的是链接,下一步用 goto 打开它完成验证;若拿到数字码,type 进输入框。",
    parameters={
        "type": "object",
        "properties": {
            "sender": {"type": "string", "description": "发件人关键字(域名/邮箱/主题子串),可留空等任意邮件"},
            "timeout": {"type": "integer", "description": "等待预算秒数,默认 150,防死等"},
        },
        "required": [],
    },
    category="fetch",
    timeout_ms=180_000,
    concurrency_safe=False,
)
def _mail_code_tool(sender: str = "", timeout: int = 150) -> ToolResult:
    secret = _wait_mail_secret(sender_hint=sender, timeout=timeout)
    parts = []
    if secret.get("code"):
        parts.append(f"验证码: {secret['code']}")
    if secret.get("link"):
        parts.append(f"安全链接: {secret['link']}")
    if parts:
        return ToolResult.success(
            "收到邮件 → " + "; ".join(parts) + "(若拿到的是安全链接,下一步用 goto 动作打开它)",
            data=secret,
        )
    return ToolResult.failure("等待超时,未收到含验证码/链接的邮件(可再次点击'发送/Resend'后重试)")


# ---------------------------------------------------------------- 图形验证码

def _solve_captcha(img_bytes: bytes, charset: Optional[str] = None) -> Optional[str]:
    """本地 OCR 优先(skills/captcha,需 ddddocr 环境),失败降级 VLM。"""
    try:
        from skills.captcha import CaptchaSolver, SolveStatus

        solver = CaptchaSolver(charset=charset or None)
        r = solver.solve(img_bytes)
        if r.status == SolveStatus.SOLVED and r.text:
            return r.text
    except Exception:
        pass
    # VLM 兜底(DeepSeek 视觉)
    from .tools_vision import solve_captcha_vlm

    return solve_captcha_vlm(img_bytes, charset=charset)


@tool(
    "captcha",
    "识别当前页面上的图形验证码(截图交给视觉/OCR)。页面出现图片验证码时调用,"
    "识别结果用于 type 进验证码输入框。",
    parameters={
        "type": "object",
        "properties": {
            "charset": {"type": "string", "description": "字符集提示,如 '0123456789'(纯数字验证码精度最高)"},
        },
        "required": [],
    },
    category="read",
    timeout_ms=90_000,
)
def _captcha_tool(charset: str = "", ctx=None) -> ToolResult:
    page = ctx.session.page if ctx and ctx.session else None
    if page is None:
        return ToolResult.failure("当前无浏览器页面")
    try:
        data = page.screenshot(full_page=False)
        if not data:
            return ToolResult.failure("截图失败")
    except Exception as e:
        return ToolResult.failure(f"截图失败: {type(e).__name__}: {str(e)[:120]}")
    code = _solve_captcha(data, charset=charset or None)
    if code:
        return ToolResult.success(f"验证码识别结果: {code}", data=code)
    return ToolResult.failure("验证码识别失败(可能是滑块/点选等类型),请用 human 工具请求人工介入")


# ---------------------------------------------------------------- 滑块验证码

@tool(
    "slide_captcha",
    "解决滑块拼图验证码(如百度安全验证的拼图滑块):在页面上定位缺口图与背景图,"
    "检测缺口位置并自动拖拽滑块;失败自动 ±10px 微调重试。"
    "页面出现'拖动滑块完成拼图'类验证码时调用;需要 ddddocr 或 opencv。",
    parameters={
        "type": "object",
        "properties": {
            "attempts": {"type": "integer", "description": "拖拽尝试次数,默认 3"},
        },
        "required": [],
    },
    category="execute",
    timeout_ms=90_000,
    concurrency_safe=False,
)
def _slide_captcha_tool(attempts: int = 3, ctx=None) -> ToolResult:
    page = ctx.session.page if ctx and ctx.session else None
    if page is None:
        return ToolResult.failure("当前无浏览器页面")
    from skills.captcha import detect_slide_gap, drag_slider, find_captcha_images

    target, bg, rects = find_captcha_images(page)
    if not target or not bg:
        return ToolResult.failure("页面未找到缺口图/背景图(截图检查页面是否真的出现滑块验证码)")
    gap = detect_slide_gap(target, bg)
    if gap is None:
        return ToolResult.failure("缺口检测失败: 需要 ddddocr 或 opencv(参考 skills/captcha 安装说明)")
    slider_x = rects.get("target", {}).get("x", 0)
    if slider_x <= 0:
        slider_x = rects.get("background", {}).get("x", 10)
    ok = drag_slider(page, gap, slider_x, attempts=attempts)
    if ok:
        return ToolResult.success(f"滑块拖拽完成(缺口 x={gap}px),请观察页面是否通过验证")
    return ToolResult.failure("滑块拖拽 3 次未通过(可能需要人工介入或页面未刷新验证状态)")


# ---------------------------------------------------------------- 人工介入

@tool(
    "human",
    "暂停任务,向操作者提问(过不去的验证码 / 高风险操作确认)。低风险流程不要调用。",
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string", "description": "向用户提的问题"}},
        "required": ["question"],
    },
    category="other",
    timeout_ms=600_000,
    concurrency_safe=False,
)
def _human_tool(question: str) -> ToolResult:
    print("\n" + "=" * 60)
    print("⚠️  需要人工介入:", question)
    try:
        answer = input("人工回复(直接回车=放弃): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ToolResult.failure("人工介入放弃", error="HUMAN_ABORT")
    print("=" * 60)
    if answer:
        return ToolResult.success(f"人工回复: {answer}", data=answer)
    return ToolResult.failure("人工介入放弃", error="HUMAN_ABORT")


# ---------------------------------------------------------------- 页面分析

@tool(
    "analyze_page",
    "分析当前页面(或指定 URL),提取下载链接/网盘链接,并给出页面分级"
    "(直链/下载页/网盘/需登录/聚合页)。用于从搜索结果页或详情页定位资源。"
    "在浏览器会话中调用时,分析的是浏览器当前看到的页面(已过 Cloudflare/登录),"
    "比外部 HTTP 抓取更准确。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要分析的 URL,留空则分析当前页面"},
        },
        "required": [],
    },
    category="search",
    timeout_ms=60_000,
)
def _analyze_page_tool(url: str = "", ctx=None) -> ToolResult:
    target = url or (ctx.session.current_url() if ctx and ctx.session else "")
    if not target:
        return ToolResult.failure("没有可分析的 URL")
    # 浏览器会话优先:分析浏览器当前看到的页面(过 CF/登录后的真实状态)
    if ctx and ctx.session and ctx.session.page:
        page = ctx.session.page
        same_page = (not url) or page.url == target or page.url.split("?")[0] == target.split("?")[0]
        if same_page:
            try:
                html = page.content()
                from pages import analyze_document

                a = analyze_document(html, page.url, probe=False)
                return _format_analysis(a)
            except Exception as e:
                return ToolResult.failure(f"浏览器页面分析失败: {type(e).__name__}: {str(e)[:120]}")
    from pages import analyze_page as analyze

    a = analyze(target, probe=True)
    return _format_analysis(a)


def _format_analysis(a) -> ToolResult:
    lines = [
        f"页面分级: {a.page_class.value} ({a.reason})",
        f"标题: {a.title[:120]}",
    ]
    if a.needs_login:
        lines.append("⚠️ 需要登录/验证码")
    lines.append(f"资源候选 {len(a.resources)} 个:")
    for r in a.best_resources[:10]:
        # 图片/直链 URL 常超长(如 hdslb CDN ~120 字符),截断到 100 会让 Agent
        # 拿不到完整直链被迫绕道脚本 —— 对可行动资源放宽到 400 字符
        limit = 400 if r.kind in ("direct_file", "image", "pan_share", "download_button") else 100
        lines.append(f"  [{r.kind}] {r.url[:limit]}" + (f"  ← {r.text[:40]}" if r.text else ""))
    return ToolResult.success("\n".join(lines), data=a.to_dict())


# ---------------------------------------------------------------- 下载

def _task_out_dir(ctx, default: str) -> str:
    """任务上下文强制落盘目录。

    Agent 在 fetch_resource 任务内运行时,ctx.task.out_dir 已由任务层注入,
    所有写文件工具(下载/流式/拼接)必须落到任务目录 —— 否则文件落在
    downloads/(工作目录),交付层收集不到,网页任务显示 done 却没有下载按钮。
    独立脚本/账号注册流程(ctx.task 为空)保持默认目录。
    """
    if ctx is not None:
        task = getattr(ctx, "task", None)
        out = getattr(task, "out_dir", None)
        if out:
            return str(out)
    return default


@tool(
    "download",
    "下载文件到本地目录。用于拿到直链后下载资源文件;下载成功(文件落地)才算任务完成。"
    "use_session=true 时走当前浏览器会话下载(带上已通过的 Cloudflare/登录 Cookie),"
    "适用于普通 HTTP 下载被 403/反爬拦截的站点(如 PlanetMinecraft)。"
    "若 URL 是 .m3u8 播放流或播放页:自动用万能下载器(分段合并/CF 反制/ffmpeg 兜底),"
    "不要手动拼 .ts 切片。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "文件直链 / m3u8 流 / 播放页"},
            "dest_dir": {"type": "string", "description": "保存目录,默认 downloads/"},
            "filename": {"type": "string", "description": "输出文件名(可选;列表条目用 序号_标题 便于区分)"},
            "expected_ext": {"type": "string", "description": "期望扩展名,如 .litematic / .schematic / .zip"},
            "min_size": {"type": "integer", "description": "最小字节数(防空文件),默认 0"},
            "use_session": {"type": "boolean", "description": "是否走浏览器会话下载(默认 false;普通 HTTP 403 时改 true)"},
        },
        "required": ["url"],
    },
    category="execute",
    timeout_ms=3_600_000,
    concurrency_safe=False,
)
def _download_tool(url: str, dest_dir: str = "downloads", filename: str = "",
                   expected_ext: str = "", min_size: int = 0,
                   use_session: bool = False, ctx=None) -> ToolResult:
    # 任务上下文强制落到任务目录(否则交付层收不到文件,网页无下载按钮)
    dest_dir = _task_out_dir(ctx, dest_dir or "downloads")
    # B站播放页:裸 HTTP 必 412,只有浏览器会话能拿 playinfo → 路由到 bilibili 技能
    if url and "bilibili.com" in url.lower():
        from skills.bilibili import bilibili_download

        r = bilibili_download(url, dest_dir, session=(ctx.session if ctx else None),
                              filename=filename)
        if r.get("ok"):
            return ToolResult.success(
                f"B站视频下载成功: {r['path']} ({r['size']} 字节, codecs={r.get('codecs','')})",
                data=r,
            )
        return ToolResult.failure(f"B站下载失败: {r.get('error','')}(需浏览器会话/页面有登录限制)")
    # m3u8 流/播放页 → 万能下载器(自带合并;会话可用时复用其 cookie)
    low = url.lower()
    if low.split("?")[0].endswith((".m3u8", ".m3u")) or ".m3u8" in low:
        from skills.universal import universal_download

        referer = ctx.session.current_url() if (ctx and ctx.session) else ""
        r = universal_download(url, dest_dir, filename=filename,
                               referer=referer, use_browser=use_session)
        if r.ok:
            return ToolResult.success(
                f"流式下载成功: {r.path} ({r.size} 字节, 策略={r.strategy})",
                data={"path": r.path, "size": r.size},
            )
        return ToolResult.failure(f"流式下载失败: {r.error}")
    if use_session and ctx and ctx.session and ctx.session.context is not None:
        return _download_via_session(ctx.session, url, dest_dir, expected_ext, min_size,
                                     filename=filename)
    from delivery import download

    dest = Path(dest_dir)
    # 防盗链:传当前页面 URL 作 Referer(图站常见)
    referer = ctx.session.current_url() if (ctx and ctx.session) else ""
    result = download(url, dest, expected_ext=expected_ext, min_size=min_size,
                      referer=referer, filename=filename)
    if result.ok:
        return ToolResult.success(
            f"下载成功: {result.path} ({result.size} 字节, 续传={result.resumed})",
            data={"path": result.path, "size": result.size},
        )
    return ToolResult.failure(f"下载失败: {result.error}(可尝试 use_session=true 走浏览器会话)")


def _download_via_session(session, url: str, dest_dir: str, expected_ext: str,
                          min_size: int, filename: str = "") -> ToolResult:
    """走浏览器会话下载:先试 context.request,失败(403/CF)用浏览器下载事件。"""
    import time
    from pathlib import Path as _P

    dest = _P(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    def _final_name(guess: str) -> str:
        if filename:
            return filename
        if expected_ext and not guess.lower().endswith(expected_ext.lower()):
            return f"{_P(guess).stem}{expected_ext}"
        return guess

    # 1) context.request(共享 Cookie,普通情况够用)
    try:
        resp = session.context.request.get(url, timeout=60_000)
        if resp.status in (200, 206):
            body = resp.body()
            if min_size and len(body) < min_size:
                return ToolResult.failure(f"文件过小: {len(body)}B < {min_size}B")
            name = _final_name(_P(url.split("?")[0]).name or f"download_{int(time.time())}.bin")
            path = dest / name
            path.write_bytes(body)
            return ToolResult.success(
                f"下载成功(浏览器会话): {path} ({len(body)} 字节)",
                data={"path": str(path), "size": len(body)},
            )
    except Exception:
        pass
    # 2) 浏览器下载事件(CF/反爬保护站:页面触发下载,Playwright 捕获)
    try:
        page = session.page
        with page.expect_download(timeout=60_000) as dl_info:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        dl = dl_info.value
        name = _final_name(dl.suggested_filename or _P(url.split("?")[0]).name or f"download_{int(time.time())}.bin")
        path = dest / name
        dl.save_as(str(path))
        if min_size and path.stat().st_size < min_size:
            return ToolResult.failure(f"文件过小: {path.stat().st_size}B < {min_size}B")
        return ToolResult.success(
            f"下载成功(浏览器下载事件): {path} ({path.stat().st_size} 字节)",
            data={"path": str(path), "size": path.stat().st_size},
        )
    except Exception as e:
        return ToolResult.failure(f"浏览器会话下载失败: {type(e).__name__}: {str(e)[:150]}")


# ---------------------------------------------------------------- 注册确认

def registered_names() -> list[str]:
    return [s.name for s in get_registry().all()]
