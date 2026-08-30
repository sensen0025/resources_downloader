"""原子动作执行 — click/type/press_enter/wait/goto/截图,按 data-agent-idx 标记操作。

感知层在观察时给每个元素打 data-agent-idx 标记,动作层按标记定位,
保证 shadow DOM 穿透后的索引与 LLM 看到的一致。
"""

from __future__ import annotations

from typing import Optional

from .browser import BrowserSession


class ActionResult:
    def __init__(self, ok: bool, message: str, data=None) -> None:
        self.ok = ok
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return self.message


class ActionExecutor:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    def _loc(self, index: int):
        page = self.session.page
        assert page is not None
        return page.locator(f'[data-agent-idx="{index}"]')

    def click(self, index: int) -> ActionResult:
        try:
            self._loc(index).click(timeout=8000)
            self.session.wait(600)
            return ActionResult(True, f"已点击元素 [{index}]")
        except Exception as e:
            return ActionResult(False, f"点击 [{index}] 失败: {type(e).__name__}: {str(e)[:120]}")

    def type(self, index: int, value: str) -> ActionResult:
        if not value:
            return ActionResult(False, "type 需要 value 参数")
        try:
            el = self._loc(index)
            try:
                el.fill(value, timeout=8000)
            except Exception:
                el.click()
                el.press_sequentially(value, delay=30)
            self.session.wait(300)
            return ActionResult(True, f"已向元素 [{index}] 输入 {len(value)} 个字符")
        except Exception as e:
            return ActionResult(False, f"输入到 [{index}] 失败: {type(e).__name__}: {str(e)[:120]}")

    def press_enter(self, index: Optional[int] = None) -> ActionResult:
        try:
            if index is not None:
                self._loc(index).press("Enter")
            else:
                page = self.session.page
                assert page is not None
                page.keyboard.press("Enter")
            self.session.wait(500)
            return ActionResult(True, "已按回车")
        except Exception as e:
            return ActionResult(False, f"按回车失败: {str(e)[:120]}")

    def wait(self, ms: int) -> ActionResult:
        self.session.wait(min(ms, 15000))
        return ActionResult(True, f"已等待 {ms}ms")

    def goto(self, url: str) -> ActionResult:
        try:
            self.session.goto(url)
            return ActionResult(True, f"已跳转 {url},当前 URL: {self.session.current_url()}")
        except Exception as e:
            return ActionResult(False, f"跳转失败: {str(e)[:120]}")

    def screenshot(self) -> ActionResult:
        try:
            data = self.session.screenshot()
            return ActionResult(True, "已截图", data)
        except Exception as e:
            return ActionResult(False, f"截图失败: {str(e)[:120]}")

    def read_body(self, limit: int = 600) -> ActionResult:
        page = self.session.page
        assert page is not None
        try:
            text = page.evaluate(
                "() => (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim()"
            )
            return ActionResult(True, f"页面文本(前{limit}字): {(text or '')[:limit]}")
        except Exception as e:
            return ActionResult(False, f"读取页面文本失败: {str(e)[:120]}")
