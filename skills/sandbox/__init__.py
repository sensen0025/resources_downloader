"""AI 本地程序沙盒 — 允许 AI 在任务目录内 read / write / run,自写脚本解决站点特定问题。

动机:规则技能(m3u8 正则/客户端按钮黑名单)是打地鼠 —— 每个站点都要预写规则。
给 AI 一个能自己写代码、跑代码的沙盒,遇到新站点(如某站签名 API)自己写脚本解析,
不再依赖预写规则。对齐 DSH「文件沙盒」理念,但面向任务:

- 沙盒根 = 任务输出目录:write 的文件会被 FileProbe 收集交付,也能读已下载候选;
- read/write 做路径沙箱校验(禁止越出任务目录、防符号链接逃逸);
- run 在沙盒 cwd 内执行,带超时 + 输出上限 + 最小化环境(不带任何密钥);
- 这是**软沙盒**:与服务器同用户权限,防不了恶意/被恶意网页注入的模型 ——
  靠提示词约束 + 路径限制 + 超时兜底;真正的 OS 级隔离(firejail/docker)是后续升级。

三个工具:
- sandbox_read(path)   : 读沙盒内文件文本(≤200KB);
- sandbox_write(path, content): 写文件到沙盒(自动建父目录);
- sandbox_python(code): 用项目 venv 在沙盒 cwd 跑一段 Python(最常用:写提取脚本);
- sandbox_run(cmd)     : 执行 shell 命令(超时/输出上限/危险命令黑名单)。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from skills.core import ToolResult, tool

__all__ = [
    "sandbox_root", "resolve_in_root", "run_command",
    "sandbox_read", "sandbox_write", "sandbox_python", "sandbox_run",
]

_MAX_TEXT = 200 << 10      # 读写/输出上限 200KB
_MAX_TIMEOUT = 300          # 命令超时上限(s)
_SESSION_COOKIES = "_session_cookies.json"   # 浏览器会话 cookie 落盘,供 AI 脚本过反爬

# 明显破坏性命令前缀(软沙盒兜底;真正的隔离靠 OS 级)
_BLOCKED_PREFIX = (
    "rm -rf /", "rm -fr /", "rm -rf /*", "mkfs.", "dd if=/dev/zero",
    ":(){", "chmod -R 777 /", "> /dev/sd", "sudo ", "shutdown", "reboot",
    "halt", "init 0", "kill -9 1",
)


def sandbox_root(ctx) -> Optional[Path]:
    """沙盒根 = 任务输出目录(文件可被交付层收集)。无任务上下文 → None(拒绝操作)。"""
    if ctx is None:
        return None
    task = getattr(ctx, "task", None)
    out = getattr(task, "out_dir", None)
    return Path(out) if out else None


def resolve_in_root(root: Path, path: str) -> Optional[Path]:
    """路径沙箱:解析后必须仍在沙盒根内(防 ../ 逃逸与符号链接)。"""
    try:
        rp = root.resolve()
        p = (root / path).resolve()
        if p.is_relative_to(rp):
            return p
    except (OSError, ValueError, AttributeError):
        try:
            rp = root.resolve()
            p = (root / path).resolve()
            if str(rp) in str(p) and str(p) != str(rp):
                return p
        except Exception:
            return None
    return None


def _minimal_env() -> dict:
    """最小化环境:不带任何密钥(RH_*/LLM_*/MAIL_*)。"""
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": os.path.expanduser("~"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }


def _ensure_session_cookies(root: Path, ctx) -> None:
    """把浏览器会话 cookie 写入沙盒 `_session_cookies.json`,供 AI 脚本带 cookie 过反爬。

    这些是站点会话 cookie(如 buvid3/SESSDATA),不是全局密钥;
    脚本用 `requests.get(url, cookies=json.load(open('_session_cookies.json')))` 即可。
    """
    try:
        session = getattr(ctx, "session", None) if ctx is not None else None
        if session is None or getattr(session, "context", None) is None:
            return
        cookies = {c["name"]: c["value"] for c in session.context.cookies()}
        if not cookies:
            return
        (root / _SESSION_COOKIES).write_text(
            json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def run_command(cmd: str, cwd: Path, timeout: float = 60.0) -> dict:
    """在沙盒 cwd 执行 shell 命令(超时/输出上限/危险前缀拦截)。返回 dict。"""
    if not cmd or not cmd.strip():
        return {"ok": False, "error": "命令为空"}
    stripped = cmd.strip()
    low = stripped.lower()
    if any(low.startswith(b) for b in _BLOCKED_PREFIX):
        return {"ok": False, "error": f"命令被沙盒拦截(危险前缀): {stripped[:80]}"}
    timeout = max(1.0, min(float(timeout), _MAX_TIMEOUT))
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(cwd), env=_minimal_env(),
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[:_MAX_TEXT],
            "stderr": (proc.stderr or "")[:_MAX_TEXT],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"命令超时(>{timeout:.0f}s)", "exit_code": -1,
                "stdout": "", "stderr": ""}
    except Exception as e:
        return {"ok": False, "error": f"执行异常: {type(e).__name__}: {str(e)[:120]}",
                "exit_code": -1, "stdout": "", "stderr": ""}


def _venv_python() -> str:
    """项目 venv 的 python(脚本可 import 项目模块);无 venv 时用当前解释器。"""
    import sys

    here = Path(__file__).resolve().parents[2]  # skills/sandbox → 项目根
    for p in (here / ".venv" / "bin" / "python", here / ".venv" / "Scripts" / "python.exe"):
        if p.exists():
            return str(p)
    return sys.executable or "python3"


# ---------------------------------------------------------------- 工具

@tool(
    "sandbox_read",
    "读取沙盒内文件文本(任务输出目录内,≤200KB)。用于查看自己写的脚本/脚本输出/已下载文件。"
    "只能读任务目录内的路径,服务器其他文件读不到。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "沙盒内相对路径,如 script.py 或 out.txt"}},
        "required": ["path"],
    },
    category="read",
    timeout_ms=30_000,
)
def sandbox_read(path: str, ctx=None) -> ToolResult:
    root = sandbox_root(ctx)
    if root is None:
        return ToolResult.failure("当前无任务沙盒上下文")
    p = resolve_in_root(root, path or "")
    if p is None or not p.is_file():
        return ToolResult.failure(f"沙盒内文件不存在或路径越界: {path}")
    try:
        data = p.read_bytes()[:_MAX_TEXT]
        return ToolResult.success(data.decode("utf-8", "replace"))
    except OSError as e:
        return ToolResult.failure(f"读取失败: {type(e).__name__}: {str(e)[:120]}")


@tool(
    "sandbox_write",
    "写文件到沙盒(任务输出目录内,自动建父目录)。用于写脚本/配置/中间产物;"
    "写入的文件会被任务交付层收集(可下载)。只能写任务目录内,禁止越界。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "沙盒内相对路径,如 extract.py"},
            "content": {"type": "string", "description": "文件内容(文本)"},
        },
        "required": ["path", "content"],
    },
    category="edit",
    timeout_ms=30_000,
)
def sandbox_write(path: str, content: str, ctx=None) -> ToolResult:
    root = sandbox_root(ctx)
    if root is None:
        return ToolResult.failure("当前无任务沙盒上下文")
    p = resolve_in_root(root, path or "")
    if p is None:
        return ToolResult.failure(f"路径越界(只能写任务目录内): {path}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((content or "").encode("utf-8"))
        return ToolResult.success(f"已写入 {path} ({p.stat().st_size} 字节)")
    except OSError as e:
        return ToolResult.failure(f"写入失败: {type(e).__name__}: {str(e)[:120]}")


@tool(
    "sandbox_python",
    "在沙盒内用项目 Python 环境运行一段代码(自动写临时文件再执行,cwd=任务目录)。"
    "用于自写站点解析脚本(如某站签名 API、JSON 提取);代码可 import 项目模块。"
    "输出截断到 200KB,超时 60s(可调)。",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python 代码"},
            "timeout": {"type": "integer", "description": "超时秒数,默认 60,最大 300"},
        },
        "required": ["code"],
    },
    category="execute",
    timeout_ms=360_000,
    concurrency_safe=False,
)
def sandbox_python(code: str, timeout: int = 60, ctx=None) -> ToolResult:
    root = sandbox_root(ctx)
    if root is None:
        return ToolResult.failure("当前无任务沙盒上下文")
    script = resolve_in_root(root, "_ai_script.py")
    if script is None:
        return ToolResult.failure("沙盒不可写")
    try:
        script.write_text(code or "", encoding="utf-8")
    except OSError as e:
        return ToolResult.failure(f"脚本写入失败: {str(e)[:120]}")
    _ensure_session_cookies(root, ctx)  # 会话 cookie 落盘供脚本过反爬
    timeout = max(1.0, min(float(timeout), _MAX_TIMEOUT))
    try:
        # 列表参数直跑(不走 shell):跨平台且无命令注入
        proc = subprocess.run(
            [_venv_python(), str(script)], cwd=str(root), env=_minimal_env(),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(f"脚本超时(>{timeout:.0f}s)")
    except Exception as e:
        return ToolResult.failure(f"执行异常: {type(e).__name__}: {str(e)[:120]}")
    if proc.returncode == 0:
        out = (proc.stdout or "").strip()
        return ToolResult.success(out or "(执行成功,无输出)")
    err = (proc.stderr or proc.stdout or "执行失败").strip()
    return ToolResult.failure(f"脚本执行失败(exit {proc.returncode}): {err[-1500:]}")


@tool(
    "sandbox_run",
    "在沙盒内执行 shell 命令(cwd=任务目录,超时/输出上限,危险命令被拦截)。"
    "用于跑脚本、curl 探测、解析等;只能影响任务目录内文件,环境不带任何密钥。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数,默认 60,最大 300"},
        },
        "required": ["command"],
    },
    category="execute",
    timeout_ms=360_000,
    concurrency_safe=False,
)
def sandbox_run(command: str, timeout: int = 60, ctx=None) -> ToolResult:
    root = sandbox_root(ctx)
    if root is None:
        return ToolResult.failure("当前无任务沙盒上下文")
    _ensure_session_cookies(root, ctx)  # 会话 cookie 落盘供脚本过反爬
    r = run_command(command or "", root, timeout=timeout)
    if not r.get("ok") and r.get("error") and not r.get("exit_code") and not r.get("stdout"):
        return ToolResult.failure(r["error"])
    parts = []
    if r.get("stdout"):
        parts.append(r["stdout"])
    if r.get("stderr"):
        parts.append("STDERR:\n" + r["stderr"])
    out = "\n".join(parts).strip()
    return ToolResult.success(
        out or f"(exit {r.get('exit_code')},无输出)",
        data={"exit_code": r.get("exit_code"), "stdout": r.get("stdout"),
              "stderr": r.get("stderr")},
    )
