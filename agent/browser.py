"""Playwright 浏览器封装 — 无头/有头、超时、页面快照、动作执行底座。

含「下载看门狗」(skyvern 经验):page.on("download") 自动把浏览器触发的
下载保存到 downloads_dir —— 反爬站的下载走"点击按钮→浏览器下载事件",
不经 HTTP 客户端,天然携带 CF/登录 Cookie。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class BrowserSession:
    def __init__(self, headless: bool = True, slow_mo: int = 50,
                 viewport: tuple[int, int] = (1280, 900),
                 channel: Optional[str] = "chrome",
                 downloads_dir: Optional[str | Path] = None,
                 cookies_path: Optional[str | Path] = None) -> None:
        self.headless = headless
        self.slow_mo = slow_mo
        self.viewport = viewport
        self.channel = channel  # 优先系统 Chrome(指纹完整,过 Cloudflare 更稳)
        self.downloads_dir = Path(downloads_dir) if downloads_dir else None
        self.cookies_path = Path(cookies_path) if cookies_path else None
        self.downloaded_files: list[str] = []
        self._pw = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def start(self) -> None:
        self._pw = sync_playwright().start()
        launch_kwargs = dict(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if self.channel:
            launch_kwargs["channel"] = self.channel
        try:
            self.browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception as e:
            print(f"[browser] 系统 Chrome 启动失败({type(e).__name__}),回退 playwright Chromium")
            launch_kwargs.pop("channel", None)
            self.browser = self._pw.chromium.launch(**launch_kwargs)
        ctx_kwargs = dict(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            user_agent=USER_AGENT,
            locale="en-US",
            accept_downloads=True,  # 捕获浏览器下载(CF 保护站的下载走事件,不走 HTTP)
        )
        # 登录态注入:有未过期 Cookie 就直接带进 context,免重复登录
        if self.cookies_path is not None:
            from .cookies import load_cookies

            state = load_cookies(self.cookies_path)
            if state is not None:
                ctx_kwargs["storage_state"] = state
                print(f"[browser] 已注入登录态 Cookie: {self.cookies_path.name}", flush=True)
            else:
                print(f"[browser] 无有效 Cookie(过期或不存在),冷启动: {self.cookies_path.name}", flush=True)
        # VPN/代理:浏览器 Agent 走 RH_PROXY_URL(未配置则不传)
        from proxy import playwright_proxy

        _proxy = playwright_proxy()
        if _proxy is not None:
            ctx_kwargs["proxy"] = _proxy
            print(f"[browser] 浏览器走代理: {_proxy['server']}", flush=True)
        self.context = self.browser.new_context(**ctx_kwargs)
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)
        # 下载看门狗:任何浏览器触发的下载自动落盘到 downloads_dir
        if self.downloads_dir is not None:
            self.downloads_dir.mkdir(parents=True, exist_ok=True)

            def _on_download(dl) -> None:
                try:
                    name = dl.suggested_filename or f"download_{len(self.downloaded_files)}.bin"
                    path = self.downloads_dir / name
                    dl.save_as(str(path))
                    self.downloaded_files.append(str(path))
                    print(f"[browser] 下载已捕获: {path}", flush=True)
                except Exception as e:
                    print(f"[browser] 下载保存失败: {type(e).__name__}: {str(e)[:100]}", flush=True)

            self.page.on("download", _on_download)

    def stop(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
            if self.browser is not None:
                self.browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()

    def save_cookies(self, path: Optional[str | Path] = None) -> Optional[Path]:
        """把当前 context 的登录态(cookies+localStorage)落盘。

        path 缺省用 self.cookies_path;都没有则返回 None。
        """
        if self.context is None:
            return None
        target = Path(path) if path else self.cookies_path
        if target is None:
            return None
        from .cookies import save_cookies as _save

        return _save(self.context, target)

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------- 基础动作

    def goto(self, url: str, timeout: int = 45000) -> str:
        assert self.page is not None
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except PWTimeout:
            pass  # 网络慢也继续,靠 Agent 判断页面状态
        return self.page.url

    def current_url(self) -> str:
        assert self.page is not None
        return self.page.url

    def title(self) -> str:
        assert self.page is not None
        try:
            return self.page.title()
        except Exception:
            return ""

    def wait(self, ms: int) -> None:
        assert self.page is not None
        self.page.wait_for_timeout(ms)

    def screenshot(self) -> bytes:
        assert self.page is not None
        return self.page.screenshot(type="png")

    def evaluate(self, script: str):
        assert self.page is not None
        return self.page.evaluate(script)
