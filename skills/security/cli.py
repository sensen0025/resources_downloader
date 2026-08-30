"""下载安全查毒 CLI — 演示 / 联调 / Agent 脚本调用。

用法(在 resource-hub/ 目录下):

    # 扫描文件/目录(多引擎:ClamAV clamd/clamscan + YARA + 启发式)
    python -m skills.security.cli scan downloads/xxx.litematic [--json]
    python -m skills.security.cli scan downloads/ --engine clamav

    # 查看引擎可用性(clamd 是否在跑 / clamscan 是否在 PATH / yara 是否装了)
    python -m skills.security.cli engines

    # 生成 EICAR 测试文件并扫描,验证杀毒引擎链路是否打通
    python -m skills.security.cli eicar

退出码:0=全部干净; 1=检出病毒/可疑; 2=无引擎可用或出错; 3=参数错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .config import SecurityConfig
from .scanner import ENGINE_NAMES, detect_engines, install_hint, scan_file

EICAR = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m skills.security.cli",
        description="Resource Hub 下载安全查毒工具(ClamAV + YARA + 启发式)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    ps = sub.add_parser("scan", help="扫描文件/目录")
    ps.add_argument("paths", nargs="+", help="要扫描的路径(文件或目录)")
    ps.add_argument("--engine", nargs="+", choices=ENGINE_NAMES, default=None,
                    help="指定引擎子集,默认全部可用引擎")
    ps.add_argument("--rules", default="", help="额外 YARA 规则目录")
    ps.add_argument("--strict/--no-strict", dest="strict", action=argparse.BooleanOptionalAction,
                    default=None, help="suspicious 是否算失败(默认按配置,通常 true)")
    ps.add_argument("--timeout", type=int, default=None, help="单引擎超时秒数")
    ps.add_argument("--json", action="store_true", help="输出 JSON(供脚本/Agent 消费)")

    pe = sub.add_parser("engines", help="探测引擎可用性")
    pe.add_argument("--json", action="store_true")

    pt = sub.add_parser("eicar", help="生成 EICAR 测试文件并扫描(验证杀毒链路)")
    return p


def _scan_one(path: str, args: argparse.Namespace, cfg: SecurityConfig):
    overrides = {}
    if args.timeout is not None:
        overrides["SECURITY_TIMEOUT"] = str(args.timeout)
    if args.strict is not None:
        overrides["SECURITY_STRICT"] = "1" if args.strict else "0"
    if overrides:
        cfg = SecurityConfig.from_env(overrides=overrides)
    return scan_file(path, cfg=cfg, engines=args.engine, yara_rules=args.rules)


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = SecurityConfig.from_env()
    any_bad = False
    rows = []
    for path in args.paths:
        r = _scan_one(path, args, cfg)
        rows.append(r.to_dict())
        if r.verdict in ("infected", "suspicious"):
            any_bad = True
        print(r.summary, file=sys.stderr if args.json else sys.stdout)
        if not args.json:
            for f in r.findings:
                mark = {"clean": "✅", "infected": "🦠", "suspicious": "⚠️",
                        "error": "❌", "skipped": "⏭️"}.get(f.status, "·")
                avail = "" if f.available else " (不可用)"
                print(f"    {mark} [{f.engine}] {f.detail}{avail}")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    if any_bad:
        return 1
    if not rows:
        return 2
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    avail = detect_engines()
    if args.json:
        print(json.dumps(avail, ensure_ascii=False, indent=2))
        return 0
    for name in ENGINE_NAMES:
        mark = "✅ 可用" if avail.get(name) else "❌ 不可用"
        print(f"  {name:<10} {mark}")
    hint = install_hint()
    if hint:
        print("\n" + hint)
    return 0 if any(avail.values()) else 2


def cmd_eicar(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="rh_eicar_") as td:
        p = Path(td) / "eicar_test.txt"
        p.write_text(EICAR, encoding="ascii")
        print(f"已生成 EICAR 测试文件: {p}")
        r = scan_file(p)
        print(r.summary)
        for f in r.findings:
            print(f"    [{f.engine}] {f.detail}")
        if r.verdict == "infected":
            print("✅ 杀毒引擎正常:检出 EICAR 测试病毒")
            return 0
        if r.verdict == "unknown":
            print("⚠️ 没有杀毒引擎(ClamAV/YARA),启发式不识别 EICAR(它不是启发式特征)。")
            print(install_hint())
            return 2
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "engines":
        return cmd_engines(args)
    if args.command == "eicar":
        return cmd_eicar(args)
    return 3


if __name__ == "__main__":
    sys.exit(main())
