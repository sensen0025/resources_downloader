"""技能管理器 — 像 mod 一样安装/卸载/启停 skill。

技能 = `skills/<name>/` 目录 + `skill.json` 元数据(声明提供哪些工具)。
- **install**   :从外部路径拷贝技能包到 skills/,读取 skill.json,启用其工具;
- **uninstall** :禁用其工具并把目录移出到 skills_archive/(可再 install 恢复);
- **enable/disable**:切换启用状态(工具级生效:停用工具不进 LLM catalog、invoke 拒绝);
- **list**      :列出已安装技能与工具状态。

状态持久化在 skills/.state.json;进程启动时 skills/__init__ 调用 apply_state()
把上次的禁用状态恢复到注册表。

用法(在 resource-hub/ 下):
    python -m skills.manager list
    python -m skills.manager install ./my_skill            # 拷贝并启用
    python -m skills.manager uninstall security            # 停用工具 + 移出到 skills_archive/
    python -m skills.manager enable captcha
    python -m skills.manager disable streaming
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

from .core import get_registry

SKILLS_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = SKILLS_DIR.parent / "skills_archive"
STATE_FILE = SKILLS_DIR / ".state.json"

# 内置核心(不可卸载,提供 download/search/analyze_page 等基础工具)
CORE_SKILLS = {"mail", "captcha", "security", "streaming", "universal", "adblock"}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def read_skill_meta(name: str) -> dict:
    """读 skill.json;缺省构造(向后兼容无元数据的技能包)。"""
    meta_file = SKILLS_DIR / name / "skill.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"name": name, "version": "0.0.0", "description": "", "tools": [], "requires": []}


def list_skills() -> list[dict]:
    # 触发工具注册点(ai.skills / agent.tools 的 @tool 副作用),让「注册 X/N」真实
    try:
        import ai.skills  # noqa: F401
        import agent.tools  # noqa: F401
    except Exception:
        pass
    state = load_state()
    out = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not (d.is_dir() and (d / "__init__.py").exists()):
            continue
        if d.name == "__pycache__":
            continue
        meta = read_skill_meta(d.name)
        tools = meta.get("tools", [])
        enabled = state.get(d.name, {}).get("enabled", True)
        reg = get_registry()
        out.append({
            "name": d.name,
            "version": meta.get("version", "?"),
            "description": meta.get("description", ""),
            "tools": tools,
            "enabled": enabled,
            "registered": [t for t in tools if reg.has(t)],
            "disabled": [t for t in tools if reg.has(t) and t in reg.disabled_names()],
        })
    return out


def apply_state() -> None:
    """进程启动时把上次的禁用状态恢复到注册表(由 skills/__init__ 调用)。"""
    reg = get_registry()
    for name, st in load_state().items():
        if not st.get("enabled", True):
            meta = read_skill_meta(name)
            for tool in meta.get("tools", []):
                reg.disable(tool)


def _set_enabled(name: str, enabled: bool) -> dict:
    state = load_state()
    entry = state.setdefault(name, {})
    entry["enabled"] = enabled
    save_state(state)
    reg = get_registry()
    for tool in read_skill_meta(name).get("tools", []):
        if enabled:
            reg.enable(tool)
        else:
            reg.disable(tool)
    return state


def cmd_list() -> int:
    items = list_skills()
    if not items:
        print("(没有可管理的技能包)")
        return 0
    for s in items:
        mark = "✅" if s["enabled"] else "⏸️ "
        tools = ", ".join(s["tools"]) or "(无工具,纯数据池)"
        reg = f"注册 {len(s['registered'])}/{len(s['tools'])}" if s["tools"] else ""
        print(f"{mark} {s['name']} v{s['version']}  {s['description'][:40]}")
        print(f"    工具: {tools}  {reg}  {('停用中: ' + ', '.join(s['disabled'])) if s['disabled'] else ''}")
    print(f"\n存档目录: {ARCHIVE_DIR}")
    return 0


def cmd_install(path: str) -> int:
    src = Path(path).resolve()
    if not (src / "__init__.py").exists():
        print(f"不是合法的技能包(缺 __init__.py): {src}", file=sys.stderr)
        return 2
    name = src.name
    meta = read_skill_meta(name)
    if not meta.get("name"):
        meta["name"] = name
    dest = SKILLS_DIR / name
    if dest.exists():
        print(f"技能 {name} 已存在,先 uninstall 再安装", file=sys.stderr)
        return 2
    shutil.copytree(src, dest)
    _set_enabled(name, True)
    print(f"✅ 已安装技能 {name} v{meta.get('version', '?')}: {meta.get('description', '')}")
    print(f"   工具: {', '.join(meta.get('tools', [])) or '(无)'}(可用 python -m skills.manager list 查看)")
    return 0


def cmd_uninstall(name: str) -> int:
    src = SKILLS_DIR / name
    if not src.exists():
        print(f"技能不存在: {name}", file=sys.stderr)
        return 2
    _set_enabled(name, False)  # 先停用工具
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))
    print(f"🗑️  已卸载 {name} → {dest}(工具已停用;重新 install 可恢复)")
    return 0


def cmd_toggle(name: str, enabled: bool) -> int:
    if not (SKILLS_DIR / name).exists():
        print(f"技能不存在: {name}", file=sys.stderr)
        return 2
    _set_enabled(name, enabled)
    print(f"{'✅ 已启用' if enabled else '⏸️  已停用'} {name}(工具级生效)")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m skills.manager",
                                 description="Resource Hub 技能管理器(mod 式安装/卸载)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    pi = sub.add_parser("install", help="安装技能包(从外部路径拷贝到 skills/)")
    pi.add_argument("path")
    pu = sub.add_parser("uninstall", help="卸载技能(停用工具 + 移出到 skills_archive/)")
    pu.add_argument("name")
    for cmd, help_ in (("enable", "启用"), ("disable", "停用")):
        p = sub.add_parser(cmd, help=help_ + "技能")
        p.add_argument("name")
    args = ap.parse_args(argv)

    if args.command == "list":
        return cmd_list()
    if args.command == "install":
        return cmd_install(args.path)
    if args.command == "uninstall":
        return cmd_uninstall(args.name)
    if args.command == "enable":
        return cmd_toggle(args.name, True)
    if args.command == "disable":
        return cmd_toggle(args.name, False)
    return 2


if __name__ == "__main__":
    sys.exit(main())
