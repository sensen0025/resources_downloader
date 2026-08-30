"""OpenCV 图像预处理 — 为 OCR 生成多个抗扭曲变体。

每个函数输入/输出 numpy 数组(BGR 或灰度),纯本地、无外部依赖(cv2+numpy)。
"""

from __future__ import annotations

import cv2
import numpy as np


def to_gray(img: np.ndarray) -> np.ndarray:
    """转灰度(已灰度则原样返回)。"""
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def denoise(gray: np.ndarray, strength: float = 7) -> np.ndarray:
    """非局部均值去噪:压掉噪点/细干扰线,尽量保留字符边缘。"""
    return cv2.fastNlMeansDenoising(gray, None, strength, 7, 21)


def binarize_otsu(gray: np.ndarray) -> np.ndarray:
    """OTSU 自动阈值二值化:字符与背景分离(自适应阈值,不需手调)。"""
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def binarize_adaptive(gray: np.ndarray, block: int = 35, c: int = 15) -> np.ndarray:
    """高斯自适应阈值:处理光照不均的验证码。"""
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c
    )


def morphology_clean(th: np.ndarray, kernel_size: tuple[int, int] = (2, 2)) -> np.ndarray:
    """开运算去掉孤立噪点,闭运算连回被噪声切断的笔画。"""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
    opened = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)
    return closed


def upscale(img: np.ndarray, factor: int = 2) -> np.ndarray:
    """放大:小字验证码放大后识别率明显提升。"""
    if factor <= 1:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (w * factor, h * factor), interpolation=cv2.INTER_CUBIC)
