"""流式下载 CLI — 演示 / 联调 / Agent 脚本调用。

用法(在 resource-hub/ 目录下):

    # 探测 URL:大小 / 是否支持 Range / 是否 m3u8 / 是否媒体
    python -m skills.streaming.cli probe https://example.com/video.mp4

    # 流式下载(音视频大文件):直链分段并发 / HLS(m3u8) 自动识别
    python -m skills.streaming.cli download https://example.com/video.mp4 --out downloads
    python -m skills.streaming.cli download https://.../index.m3u8 --out downloads --json
    python -m skills.streaming.cli download URL --segments 8 --limit 2M   # 并发/限速

退出码:0=成功, 1=失败, 2=探测失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .streamer import probe_stream, stream_download


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m skills.streaming.cli",
        description="Resource Hub 流式下载工具(音视频大文件:分段并发 / HLS / 限速 / 进度)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("probe", help="探测 URL(大小/Range/m3u8/媒体类型)")
    pp.add_argument("url")
    pp.add_argument("--json", action="store_true")

    pd = sub.add_parser("download", help="流式下载")
    pd.add_argument("url")
    pd.add_argument("--out", default="downloads", help="输出目录,默认 downloads/")
    pd.add_argument("--name", default="", help="输出文件名(缺省从 URL 猜)")
    pd.add_argument("--strategy", choices=["auto", "direct", "hls"], default="auto")
    pd.add_argument("--segments", type=int, default=4, help="直链分段并发数,默认 4")
    pd.add_argument("--limit", default="", help="限速,如 2M / 512K / 1G(字节每秒)")
    pd.add_argument("--json", action="store_true")
    return p


def _parse_limit(s: str) -> int:
    if not s:
        return 0
    s = s.strip().upper()
    mult = 1
    for suffix, m in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if s.endswith(suffix):
            mult = m
            s = s[:-1]
            break
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def cmd_probe(url: str, as_json: bool) -> int:
    p = probe_stream(url)
    if as_json:
        print(json.dumps(p.__dict__, ensure_ascii=False, indent=2))
        return 0 if p.ok else 2
    if not p.ok:
        print(f"❌ 探测失败: {p.error}")
        return 2
    print(f"✅ {p.url}")
    print(f"   Content-Type: {p.content_type or '(未知)'}")
    print(f"   Content-Length: {p.content_length}B")
    print(f"   Accept-Ranges: {'✅ bytes(可分段并发)' if p.accept_ranges else '❌ 不支持'}")
    print(f"   HLS(m3u8): {'✅ 是' if p.is_hls else '否'}")
    print(f"   媒体: {'✅ 是' if p.is_media else '否'}")
    return 0


def cmd_download(url: str, args: argparse.Namespace) -> int:
    limit = _parse_limit(args.limit)
    r = stream_download(
        url, args.out, filename=args.name, strategy=args.strategy,
        segments=args.segments, speed_limit=limit,
        on_progress=(lambda d, t: print(f"\r  进度: {d}/{t} B", end="", flush=True)),
    )
    if args.json:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
        return 0 if r.ok else 1
    print()
    if r.ok:
        print(f"✅ 下载完成: {r.path} ({r.size} 字节)")
        print(f"   策略: {r.strategy} · 分段: {r.segments} · 续传: {r.resumed} · 耗时 {r.elapsed:.1f}s")
        return 0
    print(f"❌ 下载失败: {r.error}")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "probe":
        return cmd_probe(args.url, args.json)
    if args.command == "download":
        return cmd_download(args.url, args)
    return 3


if __name__ == "__main__":
    sys.exit(main())
