"""图形验证码识别技能 — 对外 API。

    from skills.captcha import CaptchaSolver, SolveStatus
"""

from .solver import CaptchaSolver, SolveResult, SolveStatus

__all__ = ["CaptchaSolver", "SolveResult", "SolveStatus"]
__version__ = "0.1.0"
