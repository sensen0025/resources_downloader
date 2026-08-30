"""合成扭曲验证码生成器 — 用于测试识别稳定性。

生成 4 档扭曲度的验证码(图片文件名 = 答案,可直接喂给 bench):

    easy    : 轻微旋转,无波动,少量噪点
    medium  : 中等旋转 + 噪点 + 干扰线
    hard    : 大旋转 + 正弦波动 + 干扰线 + 噪点
    extreme : 更大旋转/波动 + 重噪点 + 多干扰线

用法:
    python -m skills.captcha.tools.generate_captchas --out bench_data --count 20 --difficulty hard
    python -m skills.captcha.tools.generate_captchas --out bench_data --difficulty all --count 15
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\timesbd.ttf",
    r"C:\Windows\Fonts\verdana.ttf",
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
]

# 去掉易混淆字符(0/O、1/l/I),也可用 --label-charset 自定义
DEFAULT_LABEL_CHARS = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"

DIFFICULTY_PARAMS = {
    "easy":    {"max_angle": 12, "wave_amp": 0, "lines": 1, "dots": 60,  "font_range": (38, 44), "width": 170, "blur": 0, "spacing": (0.62, 0.80)},
    "medium":  {"max_angle": 20, "wave_amp": 0, "lines": 2, "dots": 130, "font_range": (34, 42), "width": 175, "blur": 0, "spacing": (0.60, 0.78)},
    "hard":    {"max_angle": 26, "wave_amp": 2, "lines": 3, "dots": 180, "font_range": (32, 40), "width": 185, "blur": 0, "spacing": (0.58, 0.76)},
    "extreme": {"max_angle": 30, "wave_amp": 2, "lines": 3, "dots": 250, "font_range": (30, 38), "width": 190, "blur": 0, "spacing": (0.56, 0.74)},
}

TEXT_COLORS = [
    (30, 30, 30), (40, 40, 90), (90, 30, 30), (30, 80, 40),
    (70, 40, 90), (20, 60, 100), (100, 60, 20),
]
BG_COLORS = [
    (245, 245, 245), (240, 245, 250), (250, 245, 240), (245, 250, 245),
]


def _available_fonts() -> list[str]:
    return [f for f in FONT_CANDIDATES if Path(f).exists()] or ["arial"]


def _wave_distort(img: Image.Image, amplitude: float, period: int) -> Image.Image:
    """正弦波动扭曲(横轴),模拟波浪形验证码。"""
    if amplitude <= 0:
        return img
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    ys = np.arange(h, dtype=np.float32)
    dx = (amplitude * np.sin(2 * math.pi * ys / period)).astype(np.float32)
    map_x = np.tile(np.arange(w, dtype=np.float32), (h, 1)) + dx[:, None]
    map_y = np.tile(np.arange(h, dtype=np.float32), (w, 1)).T
    warped = cv2.remap(arr, map_x, map_y, cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    return Image.fromarray(warped)


def generate_captcha(
    label: str,
    difficulty: str = "medium",
    height: int = 60,
    seed: int | None = None,
) -> Image.Image:
    """按难度生成一张带 label 的扭曲验证码图。"""
    p = DIFFICULTY_PARAMS[difficulty]
    rng = random.Random(seed)
    width = p["width"]
    fonts = _available_fonts()

    img = Image.new("RGB", (width, height), rng.choice(BG_COLORS))
    draw = ImageDraw.Draw(img)

    # 干扰线(画在文字下层,更接近真实验证码)
    for _ in range(p["lines"]):
        x1, y1 = rng.randrange(width), rng.randrange(height)
        x2, y2 = rng.randrange(width), rng.randrange(height)
        draw.line([(x1, y1), (x2, y2)], fill=rng.choice(TEXT_COLORS), width=rng.randint(1, 2))

    # 逐字符绘制:随机字体、随机旋转、垂直微移
    font_path = rng.choice(fonts)
    font_size = rng.randint(*p["font_range"])
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        font = ImageFont.load_default()
    x = rng.randint(10, 16)
    for ch in label:
        canvas = Image.new("RGBA", (font_size * 2, font_size * 2), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(canvas)
        cdraw.text((font_size, font_size), ch, font=font, fill=rng.choice(TEXT_COLORS), anchor="mm")
        angle = rng.uniform(-p["max_angle"], p["max_angle"])
        canvas = canvas.rotate(angle, expand=True, resample=Image.BICUBIC)
        y = (height - font_size * 2) // 2 + rng.randint(-3, 3)
        img.paste(canvas, (int(x), int(y)), canvas)
        x += rng.randint(int(font_size * p["spacing"][0]), int(font_size * p["spacing"][1]))

    # 噪点
    for _ in range(p["dots"]):
        draw.point((rng.randrange(width), rng.randrange(height)), fill=rng.choice(TEXT_COLORS))

    # 波形扭曲 + 轻微模糊
    img = _wave_distort(img, p["wave_amp"], period=rng.randint(18, 30))
    if p["blur"]:
        img = img.filter(ImageFilter.GaussianBlur(0.6))  # type: ignore[name-defined]
    return img


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成合成扭曲验证码(文件名=答案)")
    ap.add_argument("--out", default="bench_data", help="输出目录")
    ap.add_argument("--count", type=int, default=20, help="每档数量")
    ap.add_argument("--difficulty", default="all", choices=["easy", "medium", "hard", "extreme", "all"])
    ap.add_argument("--label-charset", default=DEFAULT_LABEL_CHARS, help="答案字符集")
    ap.add_argument("--length", type=int, default=0, help="答案长度(0=随机 4~5)")
    ap.add_argument("--seed", type=int, default=None, help="随机种子(可复现)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    levels = ["easy", "medium", "hard", "extreme"] if args.difficulty == "all" else [args.difficulty]
    rng = random.Random(args.seed)

    total = 0
    for level in levels:
        level_dir = out / level
        level_dir.mkdir(parents=True, exist_ok=True)
        for i in range(args.count):
            length = args.length or rng.randint(4, 5)
            label = "".join(rng.choice(args.label_charset) for _ in range(length))
            img = generate_captcha(label, difficulty=level, seed=rng.randrange(10**9))
            img.save(level_dir / f"{label}_{i:02d}.png")
            total += 1
        print(f"{level}: {args.count} 张 → {level_dir}")
    print(f"共生成 {total} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
