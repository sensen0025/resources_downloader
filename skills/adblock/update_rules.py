#!/usr/bin/env python3
"""广告规则更新器 — 从开源广告过滤列表(EasyList 系 / AdGuard)拉取并转成数据池。

用法(在服务器上):
    python -m skills.adblock.update_rules            # 拉取全部源,生成 data/rules.json
    python -m skills.adblock.update_rules --sources chinese,easylist,privacy
    python -m skills.adblock.update_rules --no-download   # 只重解析本地已下载的 .txt

源(filters.adtidy.org 官方 CDN,服务器已验证连通):
    chinese  = 224_optimized.txt   AdGuard 中文广告过滤(uBO 版)
    easylist = 101_optimized.txt   EasyList(英文通用)
    privacy  = 102_optimized.txt   EasyPrivacy(统计/追踪)

解析 EasyList 语法(子集):
    ||example.com^        → 域名规则(host==example.com 或 *.example.com)
    @@||example.com^      → 例外域名(白名单,优先于黑名单)
    /path/pattern/        → URL 路径/子串模式
    其余(元素隐藏 ##、复杂正则 @@…、$ 修饰符)忽略 —— 我们只需要 URL/域名级过滤。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "skills" / "adblock" / "data"
RAW_DIR = DATA_DIR / "raw"
OUT = DATA_DIR / "rules.json"

SOURCES: dict[str, str] = {
    "chinese": "https://filters.adtidy.org/extension/ublock/filters/224_optimized.txt",
    "easylist": "https://filters.adtidy.org/extension/ublock/filters/101_optimized.txt",
    "privacy": "https://filters.adtidy.org/extension/ublock/filters/102_optimized.txt",
}

_DOMAIN_RULE = re.compile(r"^\|\|([a-z0-9*._-]+)\^$", re.I)
_EXCEPTION_DOMAIN = re.compile(r"^@@\|\|([a-z0-9*._-]+)\^$", re.I)
_PATH_RULE = re.compile(r"^/([^/$*]+)/$")
_DOMAIN_WITH_PATH = re.compile(r"^\|\|([a-z0-9*._-]+)(/[^$*]+)\^$", re.I)
_EXCEPTION_WITH_PATH = re.compile(r"^@@\|\|([a-z0-9*._-]+)(/[^$*]+)\^$", re.I)
_GLOB_RULE = re.compile(r"^\*([^*]+)\*$", re.I)


def _download(url: str, dest: Path, timeout: float = 60.0) -> bool:
    print(f"[update] 下载 {url.split('/')[-1]} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "resource-hub/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as e:
        print(f"[update] 下载失败: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"[update] 已保存 {len(data)} 字节 → {dest.name}", flush=True)
    return True


def parse_file(path: Path) -> tuple[set[str], set[str], set[str]]:
    """解析单个列表:返回 (domains, patterns, whitelist)。"""
    domains: set[str] = set()
    patterns: set[str] = set()
    whitelist: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return domains, patterns, whitelist
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("!", "#", "[Adblock")):
            continue
        m = _EXCEPTION_WITH_PATH.match(line)
        if m:
            whitelist.add(m.group(1).lower().lstrip("*."))
            continue
        m = _EXCEPTION_DOMAIN.match(line)
        if m:
            whitelist.add(m.group(1).lower().lstrip("*."))
            continue
        # ||example.com/ads^ → 域名 + 子串模式 example.com/ads
        m = _DOMAIN_WITH_PATH.match(line)
        if m:
            domain = m.group(1).lower().lstrip("*.")
            path = m.group(2).lower().rstrip("^")
            domains.add(domain)
            if len(path) >= 4:
                patterns.add(domain + path)
            continue
        m = _DOMAIN_RULE.match(line)
        if m:
            domains.add(m.group(1).lower().lstrip("*."))
            continue
        m = _PATH_RULE.match(line)
        if m:
            pat = m.group(1).lower()
            if len(pat) >= 4:  # 太短的路径模式易误杀
                patterns.add(pat)
            continue
        m = _GLOB_RULE.match(line)
        if m:
            pat = m.group(1).strip().lower()
            if len(pat) >= 4:
                patterns.add(pat)
    return domains, patterns, whitelist


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m skills.adblock.update_rules")
    ap.add_argument("--sources", default="chinese,easylist,privacy",
                    help="逗号分隔:chinese/easylist/privacy")
    ap.add_argument("--no-download", action="store_true", help="只解析已下载的 raw 文件")
    args = ap.parse_args(argv)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    chosen = [s.strip() for s in args.sources.split(",") if s.strip() in SOURCES]
    if not chosen:
        print("没有可用的源,可用:", list(SOURCES), file=sys.stderr)
        return 2

    all_domains: set[str] = set()
    all_patterns: set[str] = set()
    all_whitelist: set[str] = set()

    for name in chosen:
        raw = RAW_DIR / f"{name}.txt"
        if not args.no_download:
            if not _download(SOURCES[name], raw):
                continue
        if not raw.exists():
            print(f"[update] 缺 {raw.name},跳过", flush=True)
            continue
        d, p, w = parse_file(raw)
        all_domains |= d
        all_patterns |= p
        all_whitelist |= w
        print(f"[update] {name}: 域名 {len(d)} / 路径模式 {len(p)} / 例外 {len(w)}", flush=True)

    # 白名单优先:黑名单里的例外域名剔除
    effective_domains = sorted(d for d in all_domains if d not in all_whitelist)
    effective_patterns = sorted(all_patterns)
    rules = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": chosen,
        "domain_count": len(effective_domains),
        "pattern_count": len(effective_patterns),
        "domains": effective_domains,
        "patterns": effective_patterns,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[update] 生成 {OUT} : 域名 {len(effective_domains)} 条 / "
          f"路径模式 {len(effective_patterns)} 条", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
