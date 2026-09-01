"""夸克网盘自动解析 — 命令行。

用法:
    python -m skills.quark auth status
    python -m skills.quark auth import "__puus=...; __pus=..."
    python -m skills.quark list "https://pan.quark.cn/s/xxxx" [--password xxx]
    python -m skills.quark download "https://pan.quark.cn/s/xxxx" [--filter 投影] [--out downloads]
"""

from __future__ import annotations

import argparse
import sys


def _fmt_size(n: int) -> str:
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def cmd_auth(args) -> int:
    from . import auth_import, auth_status

    if args.action == "import":
        try:
            p = auth_import(args.cookie)
        except Exception as e:
            print(f"导入失败: {e}", file=sys.stderr)
            return 2
        print(f"✅ Cookie 已保存到 {p}(之后下载全自动,无需扫码)")
        return 0
    st = auth_status()
    if st["logged_in"]:
        print(f"✅ 已登录: {st['cookie']}")
        return 0
    print(f"❌ 未登录: {st.get('reason', '')}")
    print("   导入一次 Cookie 即可: python -m skills.quark auth import \"__puus=...\"")
    return 1


def cmd_list(args) -> int:
    from . import list_share

    try:
        r = list_share(args.url, password=args.password or "")
    except Exception as e:
        print(f"❌ 列文件失败: {e}", file=sys.stderr)
        return 2
    files = [f for f in r["files"] if not f.get("dir")]
    dirs = [f for f in r["files"] if f.get("dir")]
    print(f"分享 {r['pwd_id']}: 文件 {len(files)} 个, 目录 {len(dirs)} 个")
    for f in sorted(files, key=lambda x: -(x.get("size") or 0)):
        print(f"  {f.get('path', '')}  ({_fmt_size(f.get('size') or 0)})")
    for d in dirs:
        print(f"  📁 {d.get('path', '')}/")
    return 0


def cmd_download(args) -> int:
    from . import download_share

    try:
        r = download_share(args.url, password=args.password or "",
                           file_filter=args.filter or "",
                           dest_dir=args.out or "downloads")
    except Exception as e:
        print(f"❌ 下载失败: {e}", file=sys.stderr)
        return 2
    print(f"✅ 已下载: {r['file_name']} ({_fmt_size(r['size'])}) -> {r['path']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m skills.quark",
                                 description="夸克网盘自动解析")
    sub = ap.add_subparsers(dest="command", required=True)

    pa = sub.add_parser("auth", help="登录态管理")
    pa.add_argument("action", choices=["status", "import"])
    pa.add_argument("cookie", nargs="?", default="", help="Cookie 字符串(import 用)")

    pl = sub.add_parser("list", help="列出分享内文件")
    pl.add_argument("url")
    pl.add_argument("--password", default="")

    pd = sub.add_parser("download", help="解析并下载分享文件")
    pd.add_argument("url")
    pd.add_argument("--password", default="")
    pd.add_argument("--filter", default="", help="文件名过滤(如 '投影')")
    pd.add_argument("--out", default="downloads")

    args = ap.parse_args(argv)
    if args.command == "auth":
        return cmd_auth(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "download":
        return cmd_download(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
