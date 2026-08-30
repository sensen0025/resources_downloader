"""图形验证码识别 CLI。

用法(在 resource-hub/ 目录下,用装了 ddddocr 的 Python 运行):

    # 识别单张/多张图
    python -m skills.captcha.cli solve captcha.png [captcha2.png ...] [--charset digits]
    python -m skills.captcha.cli solve captcha.png --json

    # 批量评测:文件夹里图片以「答案.后缀」命名,输出准确率
    python -m skills.captcha.cli bench bench_data/hard --charset alnum

    # 生成合成扭曲验证码(测试用)
    python -m skills.captcha.tools.generate_captchas --out bench_data --difficulty hard --count 20

退出码:0=全部 solved, 1=存在 failed/error, 2=参数错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .solver import CaptchaSolver, SolveStatus, _enc

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

_CHARSET_ALIASES = {
    "digits": "0123456789",
    "lower": "abcdefghijklmnopqrstuvwxyz",
    "upper": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "letters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "alnum": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
}


def _resolve_charset(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _CHARSET_ALIASES.get(raw.lower(), raw)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m skills.captcha.cli",
        description="Resource Hub 图形验证码识别工具(ddddocr + 预处理变体投票)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--charset", default=None, help="字符集:digits/lower/upper/letters/alnum 或自定义字符串,如 '0123456789'")
        sp.add_argument("--no-vlm", action="store_true", help="禁用 VLM 兜底(默认启用,未配置时自动跳过)")
        sp.add_argument("--no-det", action="store_true", help="禁用文字框检测裁剪")
        sp.add_argument("--no-beta", action="store_true", help="禁用第二个 beta 模型(省内存;默认启用以提供跨模型一致性信号)")
        sp.add_argument("--json", action="store_true", help="输出 JSON")

    ps = sub.add_parser("solve", help="识别单张或多张验证码图片")
    add_common(ps)
    ps.add_argument("images", nargs="+", help="图片路径")

    pb = sub.add_parser("bench", help="批量评测:文件夹内图片以「答案.后缀」命名")
    add_common(pb)
    pb.add_argument("folder", help="图片文件夹")
    pb.add_argument("--raw-baseline", action="store_true", help="同时跑原始 ddddocr(无预处理)作对照")
    return p


def cmd_solve(args: argparse.Namespace) -> int:
    solver = CaptchaSolver(
        charset=_resolve_charset(args.charset),
        use_vlm_fallback=not args.no_vlm,
        use_detection=not args.no_det,
        use_beta=not args.no_beta,
    )
    rows = []
    all_solved = True
    for path in args.images:
        p = Path(path)
        try:
            r = solver.solve(p)
            ok = r.status == SolveStatus.SOLVED
            all_solved = all_solved and ok
            rows.append({
                "file": p.name,
                "code": r.text,
                "confidence": r.confidence,
                "status": r.status.value,
                "method": r.method,
                "agreement": r.agreement,
            })
        except Exception as e:
            all_solved = False
            rows.append({"file": p.name, "code": None, "status": "error", "error": str(e)})
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for x in rows:
            if x["status"] == "error":
                print(f"❌ {x['file']}: 错误 {x['error']}")
            elif x["status"] == "solved":
                print(f"✅ {x['file']}: {x['code']}  (置信 {x['confidence']:.2f}, 命中 {x['method']}, {x['agreement']}票)")
            else:
                print(f"⚠️  {x['file']}: {x['code'] or '(空)'}  (置信 {x['confidence']:.2f}, 状态 {x['status']})")
    return 0 if all_solved else 1


def cmd_bench(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    files = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in _IMAGE_EXTS)
    if not files:
        print(f"文件夹里没有图片: {folder}")
        return 2

    solver = CaptchaSolver(
        charset=_resolve_charset(args.charset),
        use_vlm_fallback=False,
        use_detection=not args.no_det,
        use_beta=not args.no_beta,
    )
    raw_ocr = None
    if args.raw_baseline:
        import ddddocr
        raw_ocr = ddddocr.DdddOcr(show_ad=False)

    rows = []
    n_exact = n_ci = n_solved = 0
    for f in files:
        # 文件名 = 答案(生成器会加 _NN 序号后缀,忽略之)
        label = re.sub(r"_\d+$", "", f.stem)
        try:
            r = solver.solve(f)
        except Exception as e:
            rows.append((f.name, label, "", 0.0, "error", "", False, False))
            continue
        exact = r.text == label
        ci = r.text.lower() == label.lower()
        n_exact += exact
        n_ci += ci
        n_solved += r.status == SolveStatus.SOLVED
        raw_pred = ""
        if raw_ocr is not None:
            try:
                raw_pred = raw_ocr.classification(f.read_bytes()) or ""
            except Exception:
                raw_pred = ""
        rows.append((f.name, label, r.text, r.confidence, r.status.value, r.method, exact, ci, raw_pred))

    total = len(rows)
    print(f"=== bench: {folder}  ({total} 张) ===")
    print(f"{'文件':<28}{'答案':<8}{'预测':<10}{'置信':<7}{'状态':<10}{'命中':<10}{'精确':<5}{'忽略大小写':<10}")
    for row in rows:
        name, label, pred, conf, status, method, exact, ci = row[:8]
        mark = "✔" if exact else ("~" if ci else "✘")
        print(f"{name:<28}{label:<8}{pred:<10}{conf:<7.2f}{status:<10}{method:<10}{mark:<5}{'✔' if ci else '✘':<10}")
    acc_exact = n_exact / total * 100
    acc_ci = n_ci / total * 100
    print(f"\n精确匹配   : {n_exact}/{total} = {acc_exact:.1f}%")
    print(f"忽略大小写 : {n_ci}/{total} = {acc_ci:.1f}%")
    print(f"solved 占比: {n_solved}/{total} = {n_solved/total*100:.1f}%")
    if raw_ocr is not None:
        n_raw = sum(1 for row in rows if row[8] == row[1])
        print(f"[对照] 原始 ddddocr 精确匹配: {n_raw}/{total} = {n_raw/total*100:.1f}%")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "solve":
        return cmd_solve(args)
    if args.command == "bench":
        return cmd_bench(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
