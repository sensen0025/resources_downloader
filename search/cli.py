"""资源检索 CLI — 多引擎聚合 + 可用性筛选。

用法:
    python -m search.cli "Python requests 教程 pdf"                 # 全部引擎,不打分探测
    python -m search.cli "设计模式 pdf" --engines bing,mojeek       # 指定引擎
    python -m search.cli "某书 epub" --probe --limit 15             # 探测可用性并重排
    python -m search.cli "xxx" --json                               # JSON 输出(脚本消费)
    python -m search.cli --list-engines                             # 列出引擎

退出码: 0=有结果, 2=无结果, 3=全部引擎失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .aggregator import search, search_engines
from .engines import all_engine_names, is_healthy, reset_health
from .models import KIND_DIRECT_FILE, KIND_PAN_SHARE, KIND_WEBPAGE

_KIND_ICON = {
    KIND_DIRECT_FILE: "📄直链",
    KIND_PAN_SHARE: "☁️网盘",
    KIND_WEBPAGE: "🌐网页",
}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="search", description="多引擎资源检索 + 可用性筛选")
    ap.add_argument("query", nargs="?", help="检索词(缺省时仅 --list-engines)")
    ap.add_argument("--engines", default="", help="逗号分隔引擎列表,默认全部")
    ap.add_argument("--per-engine", type=int, default=10, help="每引擎最多取多少条(默认 10)")
    ap.add_argument("--probe", action="store_true", help="对候选做 HTTP 可用性探测并重排")
    ap.add_argument("--limit", type=int, default=15, help="最终输出条数(默认 15)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--list-engines", action="store_true", help="列出引擎")
    args = ap.parse_args(argv)

    if args.list_engines or not args.query:
        print("可用引擎(顺序=优先级):")
        for name in all_engine_names():
            state = "✅" if is_healthy(name) else "⏸ 冷却中"
            print(f"  {name:12s} {state}")
        return 0 if args.list_engines else 2

    engines = [e.strip() for e in args.engines.split(",") if e.strip()] if args.engines else None
    reset_health()  # CLI 每次独立运行,重置熔断状态

    outcomes = search_engines(args.query, engines=engines, per_engine=args.per_engine)
    ok = [o for o in outcomes if not o.error]
    errs = [o for o in outcomes if o.error]
    for e in errs:
        print(f"  ⚠️  [{e.name}] {e.error}", file=sys.stderr)

    if not ok:
        print("所有引擎均失败(网络/反爬/限流)。可重试或换 --engines。", file=sys.stderr)
        return 3

    results = search(args.query, engines=engines, per_engine=args.per_engine,
                     probe=args.probe, limit=args.limit, _outcomes=outcomes)
    if not results:
        print("无结果(全部被过滤或零相关)。", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "query": args.query,
            "engines_ok": [o.name for o in ok],
            "engines_failed": [o.name for o in errs],
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"\n🔎 {args.query!r} — {len(results)} 条候选"
          f"(引擎: {', '.join(o.name for o in ok)};失败: {', '.join(o.name for o in errs) or '无'})")
    print("=" * 100)
    for i, r in enumerate(results, start=1):
        probe_txt = ""
        if r.probe:
            p = r.probe
            kind = p.kind
            icon = _KIND_ICON.get(kind, kind)
            size = f" {p.size // 1024}KB" if p.size > 0 else ""
            probe_txt = f" [{icon}{size}]" if p.status else f" [{icon}]"
        engines_txt = "+".join(r.engines) if len(r.engines) > 1 else r.engine
        print(f"{i:>2}. [{r.score:5.1f}] {r.title[:70]}{probe_txt}")
        print(f"     {r.url[:110]}")
        if r.snippet:
            print(f"     {r.snippet[:130]}")
        print(f"     引擎: {engines_txt}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
