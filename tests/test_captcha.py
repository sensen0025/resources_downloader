"""skills/captcha 单元测试(需 ddddocr/cv2/numpy,建议用 venv-demo 的 Python 跑)。"""

import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np

    import ddddocr
    from skills.captcha import CaptchaSolver, SolveStatus
    from skills.captcha.preprocess import binarize_otsu, morphology_clean, to_gray, upscale
    from skills.captcha.tools.generate_captchas import generate_captcha
    _DDDDOCR_OK = hasattr(ddddocr.DdddOcr, "classification") and \
        hasattr(ddddocr.DdddOcr, "set_ranges")
except ImportError:  # 环境缺 ddddocr/cv2 时优雅跳过,不阻塞 discover
    cv2 = None
    np = None
    CaptchaSolver = SolveStatus = generate_captcha = None
    to_gray = binarize_otsu = morphology_clean = upscale = None
    _DDDDOCR_OK = False

_DEP_MISSING = cv2 is None or np is None or CaptchaSolver is None or not _DDDDOCR_OK
_SKIP = unittest.skipIf(_DEP_MISSING, "缺少 ddddocr/cv2/numpy,请用 venv-demo 的 Python 运行")

SAMPLES = Path(r"C:\Users\sense\Desktop\1\resource-hub-research\ddddocr\samples")


@_SKIP
class TestPreprocess(unittest.TestCase):
    def test_pipeline_shapes(self):
        img = np.full((40, 120, 3), 255, np.uint8)
        cv2.putText(img, "ab12", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        gray = to_gray(img)
        self.assertEqual(gray.ndim, 2)
        th = binarize_otsu(gray)
        self.assertEqual(th.ndim, 2)
        self.assertEqual(th.shape, gray.shape)
        clean = morphology_clean(th)
        self.assertEqual(clean.shape, gray.shape)
        up = upscale(th, 2)
        self.assertEqual(up.shape[0], gray.shape[0] * 2)


@_SKIP
class TestSolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.solver = CaptchaSolver(show_ad=False)

    def test_yzm1_known_sample(self):
        p = SAMPLES / "yzm1.png"
        if not p.exists():
            self.skipTest("缺少样本文件")
        r = self.solver.solve(p.read_bytes())
        self.assertEqual(r.text, "3n3d", f"实际得到 {r.text!r} (状态 {r.status.value})")

    def test_blank_returns_not_solved(self):
        blank = np.full((40, 120, 3), 255, np.uint8)
        r = self.solver.solve(blank)
        self.assertNotEqual(r.status, SolveStatus.SOLVED)

    def test_synthetic_easy_solved(self):
        """合成易档验证码应当高置信解决。"""
        img = generate_captcha("abcd", difficulty="easy", seed=42)
        buf = cv2.imencode(".png", np.array(img))[1].tobytes()
        r = self.solver.solve(buf)
        self.assertEqual(r.status, SolveStatus.SOLVED, f"实际 {r.text!r} conf={r.confidence}")

    def test_charset_digits_restricts_output(self):
        img = generate_captcha("482913", difficulty="medium", seed=7)
        buf = cv2.imencode(".png", np.array(img))[1].tobytes()
        s = CaptchaSolver(charset="0123456789", show_ad=False)
        r = s.solve(buf)
        self.assertTrue(r.text.isdigit(), f"受限字符集却输出 {r.text!r}")


if __name__ == "__main__":
    unittest.main()
