"""万能下载 CLI — 直链 / m3u8 / CF 墙 / 播放页自动找流。

    python -m skills.universal.cli download <url> [--out DIR] [--name NAME] [--json]
    python -m skills.universal.cli resolve <播放页URL>     # 提取页面里的 m3u8 流地址
    python -m skills.universal.cli ffmpeg                  # 检测 ffmpeg 是否可用
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .cf import is_cf_challenge
from .ffmpeg_tool import find_ffmpeg
from .universal import download_page_stream, resolve_stream, universal_download


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m skills.universal.cli",
                                description="万能下载器(直链/m3u8/CF 反制/播放页找流)")
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("download", help="万能下载")
    pd.add_argument("url")
    pd.add_argument("--out", default="downloads")
    pd.add_argument("--name", default="")
    pd.add_argument("--referer", default="")
    pd.add_argument("--no-browser", action="store_true", help="禁用浏览器 CF 反制")
    pd.add_argument("--json", action="store_true")

    pr = sub.add_parser("resolve", help="播放页提取 m3u8 流地址")
    pr.add_argument("url")
    pr.add_argument("--json", action="store_true")

    pf = sub.add_parser("ffmpeg", help="检测 ffmpeg")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "ffmpeg":
        ff = find_ffmpeg()
        print(f"ffmpeg: {'✅ ' + ff if ff else '❌ 未安装(fmp4 流需它合并)'}")
        return 0 if ff else 2
    if args.command == "resolve":
        streams = resolve_stream(args.url)
        if args.json:
            print(json.dumps(streams, ensure_ascii=False, indent=2))
        else:
            for s in streams:
                print(s)
            if not streams:
                print("(未找到 m3u8 流地址)", file=sys.stderr)
        return 0 if streams else 2
    if args.command == "download":
        r = universal_download(args.url, args.out, filename=args.name,
                               referer=args.referer, use_browser=not args.no_browser)
        if args.json:
            print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
            return 0 if r.ok else 1
        if r.ok:
            print(f"✅ 下载完成: {r.path} ({r.size} 字节, 策略={r.strategy})")
            return 0
        print(f"❌ 下载失败: {r.error}")
        return 1
    return 3


if __name__ == "__main__":
    sys.exit(main())
