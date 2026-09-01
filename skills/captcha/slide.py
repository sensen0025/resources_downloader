"""滑块验证码(拼图缺口)识别与拖拽 — 并入 skills/captcha(与 OCR/VLM 并列的第三种解法)。

后端优先级:
1. **ddddocr.slide_match**(装了 ddddocr 时)—— 输入缺口图 target + 背景图 background,
   返回缺口坐标;
2. **cv2 模板匹配**(有 opencv 无 ddddocr 时)—— 模板匹配缺口块;
3. 都没有 → 明确报错(不静默失败)。

百度 wappass 滑块流程:页面有两张图(背景图 + 缺口图) → 缺口检测 → 拖拽距离 =
缺口中心 x − 滑块起点 x → Playwright 分步拖拽 → 失败自动 ±10px 微调重试 2 次。

诚实边界:滑块验证码依赖图片可截取、缺口可检测、拖拽轨迹被放行;反自动化强的
站点可能多次失败,此时应走 human 人工介入(与字符验证码一致)。
"""

from __future__ import annotations

import io
import time
from typing import Optional

__all__ = ["detect_slide_gap", "drag_slider", "SLIDE_BACKENDS"]

SLIDE_BACKENDS: list[str] = []


def detect_slide_gap(target_bytes: bytes, background_bytes: bytes,
                     prefer: str = "auto") -> Optional[int]:
    """检测缺口中心 x 坐标(像素)。target=缺口图, background=背景图。

    prefer: auto=ddddocr 优先,cv2 兜底; "cv2"=强制模板匹配(测试用,确定性);
    "ddddocr"=只用 ddddocr。都不可用返回 None。
    """
    if prefer != "cv2":
        try:
            import ddddocr

            det = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
            res = det.slide_match(target_bytes, background_bytes)
            if res:
                if "ddddocr" not in SLIDE_BACKENDS:
                    SLIDE_BACKENDS.append("ddddocr")
                t = res.get("target") or res.get("template") or []
                if len(t) >= 2:
                    return int(t[0] + t[2] / 2)  # 缺口中心 x = x + w/2
        except ImportError:
            pass
        except Exception:
            pass
    # cv2 模板匹配兜底
    try:
        import cv2
        import numpy as np

        bg = cv2.imdecode(np.frombuffer(background_bytes, np.uint8), cv2.IMREAD_COLOR)
        tg = cv2.imdecode(np.frombuffer(target_bytes, np.uint8), cv2.IMREAD_COLOR)
        if bg is None or tg is None:
            return None
        if "cv2" not in SLIDE_BACKENDS:
            SLIDE_BACKENDS.append("cv2")
        res = cv2.matchTemplate(bg, tg, cv2.TM_CCOEFF_NORMED)
        _, _, _, loc = cv2.minMaxLoc(res)
        return int(loc[0] + tg.shape[1] / 2)
    except ImportError:
        return None
    except Exception:
        return None


def drag_slider(page, gap_x: int, slider_x: int = 0, *, slider_w: int = 42,
                attempts: int = 3, y_offset: int = 0) -> bool:
    """Playwright 拖拽滑块到缺口。slider_x 为滑块起点 x(0=自动找).

    返回是否成功(以页面不再出现滑块/验证区为判据由调用方确认,这里尽力拖拽)。
    失败按 ±10px 微调重试(滑块验证常见做法)。
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
    except ImportError:
        return False
    if slider_x <= 0:
        slider_x = _find_slider_x(page)
    base = gap_x - slider_x
    for attempt in range(attempts):
        adjust = (attempt - attempts // 2) * 10  # 0, -10, +10
        dist = max(base + adjust, 10)
        try:
            page.mouse.move(slider_x + slider_w // 2, 260 + y_offset)
            page.mouse.down()
            # 分步移动模拟人手轨迹(先快后慢)
            steps = max(8, dist // 15)
            for i in range(1, steps + 1):
                x = slider_x + slider_w // 2 + int(dist * (i / steps))
                page.mouse.move(x, 260 + y_offset + (2 if i % 3 == 0 else 0))
                time.sleep(0.008)
            page.mouse.up()
            time.sleep(1.2)
            if not _slider_still_present(page):
                return True
        except PWTimeout:
            pass
        except Exception:
            pass
    return False


def _find_slider_x(page) -> int:
    """从页面找滑块按钮的左边界 x;找不到返回 10(靠左默认)。"""
    try:
        js = """
        () => {
          const imgs = [...document.querySelectorAll('img')];
          const cands = imgs.filter(i => /slider|slide|jigsaw|puzzle|yidun|target/i.test(i.src||''));
          if (cands.length) { const r = cands[0].getBoundingClientRect(); return r.left; }
          const el = document.querySelector('[class*="slider"],[class*="slide"],[id*="slider"]');
          if (el) { const r = el.getBoundingClientRect(); return r.left; }
          return 10;
        }
        """
        val = page.evaluate(js)
        return int(val or 10)
    except Exception:
        return 10


def _slider_still_present(page) -> bool:
    """验证是否已通过(页面不再有滑块区域)。"""
    try:
        return page.evaluate(
            "() => !!document.querySelector('[class*=\"slider\"],[class*=\"slide\"],"
            "[id*=\"slider\"],canvas')"
        )
    except Exception:
        return True


def find_captcha_images(page, bg_hint: str = "", target_hint: str = "") -> tuple[bytes, bytes, dict]:
    """在页面里找缺口图与背景图(img 元素),返回 (target_bytes, bg_bytes, rects)。

    优先按 src 特征匹配(bg/background vs target/slider/jigsaw/puzzle/yidun);
    找不到特征时取最大的两个 img(常见布局:大图=背景,小图=缺口)。
    """
    try:
        items = page.evaluate(
            "() => [...document.querySelectorAll('img')].map(i => {"
            " const r = i.getBoundingClientRect();"
            " return {src: i.src||'', w: r.width, h: r.height, x: r.x, y: r.y};"
            "})"
        )
    except Exception:
        return b"", b"", {}

    def shot(x, y, w, h) -> bytes:
        clip = page.screenshot(clip={"x": x, "y": y, "width": max(w, 1), "height": max(h, 1)})
        return clip

    imgs = [i for i in items if (i.get("w") or 0) > 40 and (i.get("h") or 0) > 40]
    bg_keywords = ("bg", "background", "back", "canvas")
    tg_keywords = ("target", "slider", "slide", "jigsaw", "puzzle", "yidun", "gap")
    bg = next((i for i in imgs if any(k in (i.get("src") or "").lower() for k in bg_keywords)), None)
    tg = next((i for i in imgs if any(k in (i.get("src") or "").lower() for k in tg_keywords)), None)
    if tg is None or bg is None:
        # 退化:取最大的两个图(大=背景,小=缺口)
        ordered = sorted(imgs, key=lambda i: -(i["w"] * i["h"]))
        if len(ordered) >= 2:
            bg, tg = ordered[0], ordered[1]
    if tg is None or bg is None:
        return b"", b"", {}
    try:
        target_bytes = shot(tg["x"], tg["y"], tg["w"], tg["h"])
        bg_bytes = shot(bg["x"], bg["y"], bg["w"], bg["h"])
    except Exception:
        return b"", b"", {}
    return target_bytes, bg_bytes, {"target": tg, "background": bg}
