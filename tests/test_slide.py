"""滑块验证码检测(skills/captcha/slide.py)单元测试。

用 PIL/numpy 合成「背景图 + 缺口块」,验证 detect_slide_gap 缺口定位精度。
无 ddddocr/opencv 时跳过(服务器等精简环境)。
"""

import io
import unittest

try:
    import numpy as np
    from PIL import Image

    HAVE_IMAGING = True
except ImportError:
    HAVE_IMAGING = False


def _make_slider_pair(gap_x: int = 150, bg_w: int = 320, bg_h: int = 160,
                      gap_w: int = 48, gap_h: int = 62) -> tuple[bytes, bytes]:
    """合成滑块验证码图片对:背景(挖洞)+ 缺口块(洞区域原样裁剪,验证算法路径)。"""
    rng = np.random.default_rng(42)
    bg = rng.integers(90, 180, (bg_h, bg_w, 3), dtype=np.uint8)
    y0 = (bg_h - gap_h) // 2
    # 背景挖洞(浅色底 + 深色边)
    hole = np.array(bg)
    hole[y0:y0 + gap_h, gap_x:gap_x + gap_w] = 235
    hole[y0, gap_x:gap_x + gap_w] = 90
    hole[y0 + gap_h - 1, gap_x:gap_x + gap_w] = 90
    hole[y0:y0 + gap_h, gap_x] = 90
    hole[y0:y0 + gap_h, gap_x + gap_w - 1] = 90
    # 缺口块 = 洞区域原样(含边框),不带白边(保证模板精确命中)
    piece = hole[y0:y0 + gap_h, gap_x:gap_x + gap_w]
    tg = np.array(piece)

    def to_png(arr: np.ndarray) -> bytes:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        return buf.getvalue()

    return to_png(tg), to_png(hole)


@unittest.skipUnless(HAVE_IMAGING, "PIL/numpy 未安装")
class TestSlideGap(unittest.TestCase):
    def test_cv2_detects_precisely(self):
        """cv2 模板匹配路径(确定性):缺口中心 = 左边缘 + 半宽,应精确。"""
        from skills.captcha import detect_slide_gap

        gap_w = 48
        for gap_x in (150, 90):
            tg, bg = _make_slider_pair(gap_x=gap_x, gap_w=gap_w)
            gap = detect_slide_gap(tg, bg, prefer="cv2")
            expected = gap_x + gap_w // 2  # 缺口中心
            self.assertIsNotNone(gap)
            self.assertGreaterEqual(gap, expected - 5)
            self.assertLessEqual(gap, expected + 5)

    def test_ddddocr_smoke(self):
        """ddddocr 路径冒烟(合成图非其训练分布,只断言不崩、返回 int 或 None)。"""
        try:
            import ddddocr  # noqa: F401
        except ImportError:
            self.skipTest("ddddocr 未安装")
        from skills.captcha import detect_slide_gap

        tg, bg = _make_slider_pair(gap_x=120)
        gap = detect_slide_gap(tg, bg, prefer="ddddocr")
        self.assertTrue(gap is None or isinstance(gap, int))

    def test_bad_inputs_none(self):
        from skills.captcha import detect_slide_gap

        self.assertIsNone(detect_slide_gap(b"", b""))
        self.assertIsNone(detect_slide_gap(b"not-an-image", b"not-an-image"))


if __name__ == "__main__":
    unittest.main()
