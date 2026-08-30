"""图形验证码求解器 — 本地 OCR(ddddocr)+ 检测裁剪 + 多变体投票 + 置信度三态。

对齐 skyvern `solve_captcha` 的「三态 + 可插拔阶梯」思路(见 前人经验总结 §B.2):

  solved    : 置信度 >= 0.9,可直接使用
  uncertain : 有候选但置信不足,调用方应重试新图 / 走 VLM / 人工核对
  none      : 完全没识别出来

抗扭曲策略:
  1. det 模型先定位文字框:大图/文字只占一角时,裁剪后再识别(单框裁剪、多框逐字拼接);
  2. 同一张图生成多个预处理变体(原图/灰度/去噪/OTSU/形态学/放大…);
  3. 每个变体独立识别并带置信度(ddddocr probability 模式);
  4. 投票按「跨模型一致 > 票数 > 置信度」排序,只有跨模型一致才给多票加成 ——
     单模型自嗨(如中文验证码 std=胸/beta=困)不会误判为高置信;
  5. 低置信时可选 VLM 兜底(配置 CAPTCHA_VLM_URL 后自动启用)。

用法:
    from skills.captcha import CaptchaSolver
    r = CaptchaSolver().solve("captcha.png")   # 也支持 bytes / ndarray
    if r.status == SolveStatus.SOLVED:
        submit_code(r.text)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np

try:
    import ddddocr
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "skills/captcha 需要 ddddocr: pip install ddddocr "
        "(本机可用 resource-hub-research/venv-demo 的 Python)"
    ) from exc

if not (hasattr(ddddocr.DdddOcr, "classification") and hasattr(ddddocr.DdddOcr, "set_ranges")):
    raise ImportError(
        "ddddocr 版本过旧(缺少 classification/set_ranges API)。"
        "请升级: pip install -U ddddocr,或改用 resource-hub-research/venv-demo 的 Python。"
    )

from .preprocess import (
    binarize_adaptive,
    binarize_otsu,
    denoise,
    morphology_clean,
    to_gray,
    upscale,
)
from .vlm import solve_with_vlm, vlm_available

ImageSource = Union[bytes, bytearray, memoryview, str, Path, np.ndarray]


class SolveStatus(str, Enum):
    SOLVED = "solved"
    UNCERTAIN = "uncertain"
    NONE = "none"


@dataclass
class SolveResult:
    text: str
    confidence: float  # 0..1
    status: SolveStatus
    method: str = ""  # 命中的预处理变体 / "vlm"
    agreement: int = 1  # 多少个变体得到相同结果
    candidates: list = field(default_factory=list)  # [(conf, text, variant, model)]


def _imread(img: ImageSource) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img
    if isinstance(img, (str, Path)):
        data = Path(img).read_bytes()
    elif isinstance(img, (bytes, bytearray, memoryview)):
        data = bytes(img)
    else:
        raise TypeError(f"不支持的图片类型: {type(img)}")
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("无法解码图片(格式不支持或已损坏)")
    return arr


def _enc(arr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise ValueError("图片编码失败")
    return buf.tobytes()


class CaptchaSolver:
    """图形验证码求解器。ddddocr 实例非线程安全,多线程须每线程一个实例。"""

    SOLVED_CONF = 0.9

    # 变体保真度:raw/gray 等无损处理权重高;otsu/morph 等有损二值化可能腐蚀细笔画,权重低
    FIDELITY = {
        "raw": 2, "gray": 2, "inverted": 2, "clahe": 2, "denoise": 2, "denoise10": 2,
        "otsu": 1, "morph": 1, "adaptive": 1, "otsu2x": 1, "morph2x": 1,
        "det-crop": 1, "det-concat": 1, "beta": 2,
    }

    def __init__(
        self,
        charset: Optional[str] = None,
        show_ad: bool = False,
        use_vlm_fallback: bool = True,
        use_detection: bool = True,
        use_beta: bool = True,
    ) -> None:
        self._ocr = ddddocr.DdddOcr(show_ad=show_ad)
        self._det = ddddocr.DdddOcr(show_ad=show_ad, det=True) if use_detection else None
        self._beta = ddddocr.DdddOcr(show_ad=show_ad, beta=True) if use_beta else None
        if charset:
            self._ocr.set_ranges(charset)
            if self._beta is not None:
                self._beta.set_ranges(charset)
        self.charset = charset
        self.use_vlm_fallback = use_vlm_fallback

    # ------------------------------------------------------------- 主入口

    def solve(self, img: ImageSource, vlm_fallback: Optional[bool] = None) -> SolveResult:
        """识别一张验证码。vlm_fallback=None 时按构造参数决定是否启用 VLM 兜底。"""
        arr = _imread(img)
        if not self._has_ink(arr):
            return SolveResult("", 0.0, SolveStatus.NONE, method="blank")
        result = self._solve_local(arr)

        use_vlm = self.use_vlm_fallback if vlm_fallback is None else vlm_fallback
        if use_vlm and result.status != SolveStatus.SOLVED and vlm_available():
            text = solve_with_vlm(_enc(arr), charset=self.charset)
            if text:
                result = SolveResult(
                    text=text,
                    confidence=1.0,
                    status=SolveStatus.SOLVED,
                    method="vlm",
                    agreement=1,
                    candidates=result.candidates,
                )
        return result

    # ------------------------------------------------------------ 本地识别

    @staticmethod
    def _has_ink(arr: np.ndarray, dark_ratio_min: float = 0.001) -> bool:
        """墨迹检测:几乎没有暗像素(纯白/纯底)直接判 NONE,防模型在空白图上幻觉。"""
        gray = to_gray(arr)
        return float((gray < 100).mean()) >= dark_ratio_min

    def _classify(self, data: bytes, ocr=None) -> Optional[tuple[float, str]]:
        try:
            res = (ocr or self._ocr).classification(data, probability=True, png_fix=True)
        except Exception:
            return None
        if not isinstance(res, dict):
            return None
        text = str(res.get("text", "") or "").strip()
        conf = float(res.get("confidence", 0.0) or 0.0)
        return (conf, text) if text else None

    def _solve_local(self, arr: np.ndarray) -> SolveResult:
        candidates: list[tuple[float, str, str, str]] = []

        # A) 全图预处理变体(std 模型)
        for name, data in self._build_variants(arr):
            c = self._classify(data)
            if c:
                candidates.append((c[0], c[1], name, "std"))

        # B) 检测定位文字框 → 裁剪识别(std 模型)
        if self._det is not None:
            candidates += self._detect_and_crop(arr)

        # C) beta 模型(可选,第二个独立模型)
        if self._beta is not None:
            c = self._classify(_enc(arr), ocr=self._beta)
            if c:
                candidates.append((c[0], c[1], "beta", "beta"))

        if not candidates:
            return SolveResult("", 0.0, SolveStatus.NONE, method="none")
        return self._aggregate(candidates)

    def _detect_and_crop(self, arr: np.ndarray) -> list[tuple[float, str, str, str]]:
        out: list[tuple[float, str, str, str]] = []
        try:
            boxes = self._det.detection(_enc(arr))
        except Exception:
            return out
        if not boxes:
            return out
        h, w = arr.shape[:2]
        valid = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if not valid:
            return out

        if len(valid) == 1:
            crop = self._crop(arr, valid[0])
            c = self._classify(_enc(crop))
            if c:
                out.append((c[0], c[1], "det-crop", "std"))
        else:
            # 多框:按 x 排序逐框识别,拼接成完整验证码
            parts: list[tuple[float, str]] = []
            for box in sorted(valid, key=lambda b: (b[0], b[1])):
                crop = self._crop(arr, box)
                c = self._classify(_enc(crop))
                if c:
                    parts.append(c)
            if len(parts) >= 2:
                text = "".join(p[1] for p in parts)
                conf = min(p[0] for p in parts)
                out.append((conf, text, "det-concat", "std"))
        return out

    @staticmethod
    def _crop(arr: np.ndarray, box, pad: int = 6) -> np.ndarray:
        x1, y1, x2, y2 = box
        h, w = arr.shape[:2]
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        return arr[y1:y2, x1:x2]

    def _aggregate(self, candidates: list[tuple[float, str, str, str]]) -> SolveResult:
        # 忽略大小写分组:ddddocr 字母大小写输出不可靠(std 与 beta 常只差大小写),
        # 字符内容一致即视为同一答案;返回时取保真度最高的拼写。
        by_lower: dict[str, list[tuple[float, str, str, str]]] = defaultdict(list)
        for c in candidates:
            by_lower[c[1].lower()].append(c)

        def weight_of(group) -> int:
            return sum(self.FIDELITY.get(v, 1) for _, _, v, _ in group)

        def rank(item) -> tuple[int, int, float]:
            _lower, group = item
            models = {m for _, _, _, m in group}
            return (len(models), weight_of(group), max(c for c, _, _, _ in group))

        best_lower, group = max(by_lower.items(), key=rank)
        confs = [c for c, _, _, _ in group]
        models = {m for _, _, _, m in group}
        all_models = {m for _, _, _, m in candidates}
        votes = len(group)
        max_conf = max(confs)
        # 代表拼写:优先高保真变体(raw/gray),其次高置信
        rep_text = max(group, key=lambda c: (self.FIDELITY.get(c[2], 1), c[0]))[1]

        # 置信度规则:
        # - 跨模型一致(忽略大小写后 std+beta 同答案):强信号,多票加成
        # - 多模型但答案分歧:不可信,重罚 → uncertain(调用方重试/换图)
        # - 单模型多票:轻微保留(有损变体可能一致地错)
        if len(models) >= 2:
            conf = min(1.0, max_conf * (1.0 + 0.1 * (votes - 1)))
        elif len(all_models) >= 2:
            conf = max_conf * 0.8  # 模型间内容分歧
        elif weight_of(group) >= 6:
            conf = max_conf * 0.95
        else:
            conf = min(max_conf, 0.85)  # 单票无旁证:封顶,防幻觉(如 beta 在空白图输出'一')

        status = SolveStatus.SOLVED if conf >= self.SOLVED_CONF else SolveStatus.UNCERTAIN
        method = next(v for c, t, v, m in candidates if t == rep_text)
        return SolveResult(
            text=rep_text,
            confidence=round(conf, 4),
            status=status,
            method=method,
            agreement=votes,
            candidates=sorted(candidates, reverse=True),
        )

    # --------------------------------------------------------- 预处理变体

    def _build_variants(self, arr: np.ndarray) -> list[tuple[str, bytes]]:
        gray = to_gray(arr)
        variants: list[tuple[str, bytes]] = [
            ("raw", _enc(arr)),
            ("gray", _enc(gray)),
            ("inverted", _enc(cv2.bitwise_not(gray))),
        ]
        den5 = denoise(gray, strength=5)
        variants.append(("denoise", _enc(den5)))
        variants.append(("denoise10", _enc(denoise(gray, strength=10))))
        th = binarize_otsu(den5)
        variants.append(("otsu", _enc(th)))
        clean = morphology_clean(th)
        variants.append(("morph", _enc(clean)))
        variants.append(("morph2x", _enc(upscale(clean, 2))))
        variants.append(("otsu2x", _enc(upscale(th, 2))))
        variants.append(("adaptive", _enc(binarize_adaptive(den5))))
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        variants.append(("clahe", _enc(clahe)))
        return variants


def solve_with_retry(
    fetch_image,
    solver: Optional[CaptchaSolver] = None,
    max_attempts: int = 3,
    need_solved: bool = True,
) -> Optional[SolveResult]:
    """带重试的稳定识别:每次 fetch_image() 取一张「新的」验证码(站点支持刷新)。

    - solved 且 need_solved=True → 立即返回;
    - 不确定/失败 → 换新图重试,取历史最高置信结果兜底;
    - 全部失败 → 返回 None(调用方转人工/VLM)。

    单次成功率 p 时,重试 N 次的有效成功率 ≈ 1-(1-p)^N —— 这是重度扭曲下
    「稳定」的关键手段:识别率低不怕,怕的是不刷新硬闯。
    """
    if solver is None:
        solver = CaptchaSolver()
    best: Optional[SolveResult] = None
    for _ in range(max_attempts):
        try:
            r = solver.solve(fetch_image())
        except Exception:
            continue
        if best is None or r.confidence > best.confidence:
            best = r
        if need_solved and r.status == SolveStatus.SOLVED:
            return r
    return best
