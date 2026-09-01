"""图形验证码识别技能 — 对外 API。

    from skills.captcha import CaptchaSolver, SolveStatus          # 字符验证码(OCR/VLM)
    from skills.captcha import detect_slide_gap, drag_slider       # 滑块拼图验证码
"""

from .slide import SLIDE_BACKENDS, detect_slide_gap, drag_slider, find_captcha_images
from .solver import CaptchaSolver, SolveResult, SolveStatus

__all__ = [
    "CaptchaSolver", "SolveResult", "SolveStatus",
    "detect_slide_gap", "drag_slider", "find_captcha_images", "SLIDE_BACKENDS",
]
__version__ = "0.2.0"
