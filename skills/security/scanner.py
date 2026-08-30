"""下载安全查毒扫描核心 — 多引擎:ClamAV(clamd/clamscan)+ YARA + 内置启发式。

对齐技能层定位(DSH 式「规则做工具」):这是**底层引擎模块**,零第三方依赖
(启发式纯标准库;yara-python 可选;clamd 用原生 socket 协议,不依赖 pyclamd)。
AI 决策型工具 `scan_file` 在 `ai/skills.py` 注册,这里只提供纯规则能力。

引擎:
1. **clamav** — 开源杀毒 ClamAV。优先连 clamd 守护进程(INSTREAM 协议流式扫描,
   病毒库常驻内存、不落盘),不可用时回退 clamscan 命令行;
2. **yara** — 规则匹配(可选,yara-python)。内置 rules/ 目录 + 用户规则目录;
3. **heuristic** — 纯标准库启发式:魔法字节伪装、压缩炸弹、脚本载荷、
   双重扩展名。永远可用,作为「没有杀毒引擎时的底线覆盖」。

verdict 语义(给 AI 的安全闸门):
- clean       全部引擎干净;
- infected    检出病毒签名(ClamAV/YARA),必须删除;
- suspicious  启发式/YARA 低危规则命中,默认拒绝(strict=False 可放行但标记);
- unknown     无杀毒引擎可用,仅启发式覆盖(不阻塞,但诚实标注覆盖不足)。
"""

from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .config import SecurityConfig

__all__ = [
    "VERDICTS",
    "EngineFinding",
    "ScanResult",
    "scan_file",
    "detect_engines",
    "install_hint",
]

VERDICTS = ("clean", "infected", "suspicious", "unknown")
ENGINE_NAMES = ("clamav", "yara", "heuristic")

# ---------------------------------------------------------------- 结果协议

@dataclass
class EngineFinding:
    """单个引擎的扫描结论。status ∈ clean/infected/suspicious/error/skipped。"""

    engine: str                 # clamav / yara / heuristic
    status: str
    detail: str = ""
    available: bool = True      # 引擎本身是否可用(False=没装/没配好)

    def to_dict(self) -> dict:
        return {"engine": self.engine, "status": self.status,
                "detail": self.detail, "available": self.available}


@dataclass
class ScanResult:
    """一次 scan_file 的完整结果:verdict + 各引擎明细。"""

    path: str
    verdict: str
    findings: list[EngineFinding] = field(default_factory=list)
    summary: str = ""

    @property
    def ok(self) -> bool:
        """安全闸门判定:clean/unknown 放行;suspicious 由调用方 strict 决定。"""
        return self.verdict in ("clean", "unknown")

    def to_dict(self) -> dict:
        return {"path": self.path, "verdict": self.verdict,
                "findings": [f.to_dict() for f in self.findings],
                "summary": self.summary, "ok": self.ok}

    def __str__(self) -> str:
        return self.summary


# ---------------------------------------------------------------- 引擎探测

def detect_engines(cfg: Optional[SecurityConfig] = None) -> dict[str, bool]:
    """探测各引擎是否可用(clamd 走 PING,耗时极短)。返回 {engine: available}。"""
    cfg = cfg or SecurityConfig.from_env()
    out = {"clamav": False, "yara": False, "heuristic": True}
    if cfg.clamd_enabled:
        out["clamav"] = _clamd_alive(cfg)
    if not out["clamav"] and cfg.effective_clamscan():
        out["clamav"] = True
    if cfg.yara_enabled:
        try:
            import yara  # noqa: F401

            out["yara"] = True
        except Exception:
            out["yara"] = False
    return out


def _clamd_alive(cfg: SecurityConfig, timeout: float = 2.0) -> bool:
    try:
        s = _clamd_connect(cfg, timeout)
        try:
            s.sendall(b"zPING\0")
            return s.recv(16).startswith(b"PONG")
        finally:
            s.close()
    except Exception:
        return False


def _clamd_connect(cfg: SecurityConfig, timeout: float):
    import socket as _s

    if cfg.clamd_socket:
        s = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(cfg.clamd_socket)
        return s
    return _s.create_connection((cfg.clamd_host, cfg.clamd_port), timeout=timeout)


def install_hint(cfg: Optional[SecurityConfig] = None) -> str:
    """引擎缺失时的安装指引(跨平台)。"""
    cfg = cfg or SecurityConfig.from_env()
    lines = []
    if cfg.clamd_enabled and not _clamd_alive(cfg):
        if cfg.effective_clamscan() is None:
            lines.append(
                "ClamAV 未安装(开源杀毒,推荐):\n"
                "  Windows: winget install Cisco.ClamAV\n"
                "  macOS:   brew install clamav\n"
                "  Ubuntu:  sudo apt install clamav clamav-daemon\n"
                "安装后先更新病毒库: freshclam\n"
                "然后启动守护进程: clamd(默认监听 127.0.0.1:3310)\n"
                "或只用命令行: 把 clamscan 放进 PATH 即可(逐文件较慢)。"
            )
        else:
            lines.append("clamd 未运行(已找到 clamscan):启动 clamd 可加速,或直接用命令行模式。")
    if cfg.yara_enabled:
        try:
            import yara  # noqa: F401
        except Exception:
            lines.append("可选 YARA 规则引擎未安装: pip install yara-python(装后自动启用)。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主入口

def scan_file(
    path: str | Path,
    cfg: Optional[SecurityConfig] = None,
    engines: Optional[Iterable[str]] = None,
    yara_rules: str = "",
    strict: Optional[bool] = None,
) -> ScanResult:
    """扫描一个文件(或目录:目录仅走 clamscan -r,启发式/YARA 逐文件不做)。

    engines: 指定引擎子集(clamav/yara/heuristic),默认全部可用引擎;
    yara_rules: 额外 YARA 规则目录(追加到配置);
    strict: None=用配置默认;True=suspicious 判失败(verdict 不受影响,ok 受影响)。
    """
    cfg = cfg or SecurityConfig.from_env()
    p = Path(path)
    if not p.exists():
        return ScanResult(path=str(p), verdict="unknown",
                          summary=f"文件不存在: {path}")
    enabled = set(engines) if engines else set(ENGINE_NAMES)
    findings: list[EngineFinding] = []

    if p.is_dir():
        # 目录:递归 clamscan(启发式按文件跑太慢,不做)
        if "clamav" in enabled:
            findings.append(_scan_clamscan_dir(cfg, p))
        else:
            findings.append(EngineFinding("clamav", "skipped", "未指定 clamav 引擎"))
        return _finish(p, findings, strict if strict is not None else cfg.strict)

    if "clamav" in enabled:
        findings.append(_scan_clamd(cfg, p))
        # clamd 不可用/报错 → clamscan 命令行兜底
        last = findings[-1]
        if last.status in ("error", "skipped"):
            findings[-1] = _scan_clamscan(cfg, p)
    if "yara" in enabled:
        findings.append(_scan_yara(cfg, p, extra_dir=yara_rules))
    if "heuristic" in enabled:
        findings.append(_scan_heuristic(cfg, p))
    return _finish(p, findings, strict if strict is not None else cfg.strict)


def _finish(p: Path, findings: list[EngineFinding], strict: bool) -> ScanResult:
    infected = [f for f in findings if f.status == "infected"]
    suspicious = [f for f in findings if f.status == "suspicious"]
    av_ran = any(f.engine in ("clamav", "yara")
                 and f.status in ("clean", "infected", "suspicious") for f in findings)

    if infected:
        verdict = "infected"
    elif suspicious:
        verdict = "suspicious"
    elif av_ran:
        verdict = "clean"
    else:
        verdict = "unknown"

    if verdict == "infected":
        sigs = "; ".join(f.detail for f in infected if f.detail)[:300]
        summary = f"🦠 检出恶意(verdict=infected): {sigs or '命中病毒签名'}。文件必须删除,禁止交付。"
    elif verdict == "suspicious":
        why = "; ".join(f.detail for f in suspicious if f.detail)[:300]
        summary = (f"⚠️ 可疑(verdict=suspicious): {why or '启发式命中'}。"
                   f"{'默认拒绝,建议删除换源' if strict else 'strict=false 已放行,请人工复核'}。")
    elif verdict == "clean":
        summary = (f"✅ 安全(verdict=clean): {p.name} 通过 "
                   f"{[f.engine for f in findings if f.status == 'clean']} 扫描。")
    else:
        av_missing = [f.engine for f in findings if f.status in ("error", "skipped") and f.engine != "heuristic"]
        summary = (f"ℹ️ 覆盖不足(verdict=unknown): 杀毒引擎未就绪({av_missing or 'clamav/yara'}),"
                   f"仅启发式扫描通过。建议安装 ClamAV 提升覆盖。")

    return ScanResult(path=str(p), verdict=verdict, findings=findings, summary=summary)


# ---------------------------------------------------------------- ClamAV:clamd

def _recv_clamd(sock, max_bytes: int = 1 << 20) -> bytes:
    """读 clamd 响应到 NUL 终止符(clamd 协议响应以 \0 结尾)。"""
    buf = bytearray()
    while len(buf) < max_bytes:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf.extend(chunk)
        if b"\0" in chunk:
            break
    return bytes(buf)


def _scan_clamd(cfg: SecurityConfig, p: Path) -> EngineFinding:
    if not cfg.clamd_enabled:
        return EngineFinding("clamav", "skipped", "SECURITY_CLAMD_ENABLED=0")
    if not _clamd_alive(cfg):
        return EngineFinding("clamav", "error", "clamd 未运行/未安装",
                             available=False)
    try:
        s = _clamd_connect(cfg, timeout=cfg.timeout)
        try:
            # INSTREAM:流式上传文件字节,不依赖 clamd 的文件系统权限
            s.sendall(b"zINSTREAM\0")
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    s.sendall(struct.pack(">I", len(chunk)) + chunk)
            s.sendall(struct.pack(">I", 0))
            resp = _recv_clamd(s).decode("utf-8", "replace").strip()
        finally:
            s.close()
        m = re.match(r"stream:\s*(.*)", resp)
        if not m:
            return EngineFinding("clamav", "error", f"clamd 响应异常: {resp[:120]}")
        line = m.group(1).strip()
        if line.upper() == "OK":
            return EngineFinding("clamav", "clean", "clamd: 未检出")
        if "FOUND" in line.upper():
            return EngineFinding("clamav", "infected", f"clamd: {line}")
        return EngineFinding("clamav", "error", f"clamd: {line[:120]}")
    except Exception as e:
        return EngineFinding("clamav", "error", f"clamd 异常: {type(e).__name__}: {str(e)[:120]}")


# ---------------------------------------------------------------- ClamAV:clamscan

def _scan_clamscan(cfg: SecurityConfig, p: Path) -> EngineFinding:
    exe = cfg.effective_clamscan()
    if not exe:
        return EngineFinding("clamav", "error",
                             "ClamAV 未安装(clamd 不可用且无 clamscan)", available=False)
    try:
        proc = subprocess.run(
            [exe, "--no-summary", "--infected", "--stdout", str(p)],
            capture_output=True, text=True, timeout=cfg.timeout,
        )
    except subprocess.TimeoutExpired:
        return EngineFinding("clamav", "error", "clamscan 超时")
    except Exception as e:
        return EngineFinding("clamav", "error",
                             f"clamscan 执行失败: {type(e).__name__}: {str(e)[:120]}")
    out = ((proc.stdout or "") + (proc.stderr or ""))
    m = re.search(r":\s*(.+?)\s*FOUND", out)
    if m:
        return EngineFinding("clamav", "infected", f"clamscan: {m.group(1).strip()}")
    if proc.returncode == 0:
        return EngineFinding("clamav", "clean", "clamscan: 未检出")
    if proc.returncode == 1:
        return EngineFinding("clamav", "infected", "clamscan: 命中(输出未解析)")
    return EngineFinding("clamav", "error",
                         f"clamscan 退出码 {proc.returncode}: {out[:200]}")


def _scan_clamscan_dir(cfg: SecurityConfig, d: Path) -> EngineFinding:
    exe = cfg.effective_clamscan()
    if not exe:
        return EngineFinding("clamav", "error",
                             "ClamAV 未安装(扫描目录需要 clamscan -r)", available=False)
    try:
        proc = subprocess.run(
            [exe, "-r", "--no-summary", "--infected", "--stdout", str(d)],
            capture_output=True, text=True, timeout=cfg.timeout,
        )
    except subprocess.TimeoutExpired:
        return EngineFinding("clamav", "error", "clamscan 目录扫描超时")
    except Exception as e:
        return EngineFinding("clamav", "error",
                             f"clamscan 执行失败: {type(e).__name__}: {str(e)[:120]}")
    out = ((proc.stdout or "") + (proc.stderr or ""))
    hits = re.findall(r"(.+?):\s*(.+?)\s*FOUND", out)
    if hits:
        detail = "; ".join(f"{Path(a).name}: {b.strip()}" for a, b in hits[:5])
        return EngineFinding("clamav", "infected", f"clamscan 检出 {len(hits)} 个: {detail}")
    if proc.returncode == 0:
        return EngineFinding("clamav", "clean", "clamscan: 目录未检出")
    return EngineFinding("clamav", "error",
                         f"clamscan 退出码 {proc.returncode}: {out[:200]}")


# ---------------------------------------------------------------- YARA

def _scan_yara(cfg: SecurityConfig, p: Path, extra_dir: str = "") -> EngineFinding:
    if not cfg.yara_enabled:
        return EngineFinding("yara", "skipped", "SECURITY_YARA_ENABLED=0")
    try:
        import yara
    except Exception:
        return EngineFinding("yara", "error",
                             "yara-python 未安装(可选: pip install yara-python)",
                             available=False)
    rule_files: list[Path] = []
    for d in cfg.yara_rule_dirs():
        rule_files.extend(sorted(d.glob("*.yar")))
        rule_files.extend(sorted(d.glob("*.yara")))
    if extra_dir:
        ed = Path(extra_dir)
        if ed.is_dir():
            rule_files.extend(sorted(ed.glob("*.yar")))
            rule_files.extend(sorted(ed.glob("*.yara")))
    if not rule_files:
        return EngineFinding("yara", "skipped", "无可用规则文件")
    try:
        rules = yara.compile(filepaths={str(i): str(f) for i, f in enumerate(rule_files)})
        matches = rules.match(str(p), timeout=cfg.timeout)
    except Exception as e:
        return EngineFinding("yara", "error",
                             f"YARA 扫描异常: {type(e).__name__}: {str(e)[:120]}")
    if not matches:
        return EngineFinding("yara", "clean", f"yara: 未命中 {len(rule_files)} 个规则文件")
    names = [m.rule for m in matches]
    high = any(_yara_severity(m) == "high" for m in matches)
    detail = "yara 命中: " + ", ".join(names[:6])
    if high:
        return EngineFinding("yara", "infected", detail)
    return EngineFinding("yara", "suspicious", detail)


def _yara_severity(match) -> str:
    try:
        meta = match.meta
        if isinstance(meta, dict):
            return str(meta.get("severity", "low")).lower()
    except Exception:
        pass
    return "low"


# ---------------------------------------------------------------- 启发式(纯标准库)

# 扩展名 → 允许的魔数 kind 集合
_MAGIC_EXPECT: dict[str, set[str]] = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp"},
    "pdf": {"pdf"},
    "doc": {"zip", "gzip"},                 # docx/odt 本质是 zip
    "archive": {"zip", "7z", "rar", "gzip", "nbt"},
    "media": {"mp4", "mkv", "mp3", "ogg"},
    "schematic": {"gzip", "zip", "nbt"},    # .litematic/.schematic
}

_EXEC_MAGIC = {"exe", "elf", "script"}
# 可执行扩展名:伪装检查豁免(本身就该是可执行/脚本)
_EXEC_OK_EXTS = {
    ".exe", ".dll", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js",
    ".jar", ".msi", ".com", ".sh", ".py", ".hta", ".lnk",
}
# 文档类扩展名:跳过脚本载荷内容匹配(避免把教程文档误报)
_SCRIPT_EXEMPT_EXTS = {
    ".txt", ".md", ".html", ".htm", ".json", ".xml", ".log", ".csv", ".svg",
    ".css", ".js", ".py", ".sh", ".bat", ".cmd", ".ps1",
}

_SCRIPT_PATTERNS: list[tuple[str, str]] = [
    ("PowerShell 编码/下载执行", re.compile(
        r"powershell[^\r\n]{0,120}?(-enc\b|-encodedcommand\b|iex\b|frombase64string)", re.I)),
    ("Invoke-Expression", re.compile(r"invoke-expression", re.I)),
    ("PowerShell 远程下载执行", re.compile(
        r"(downloadstring|downloadfile|new-object\s+net\.webclient)", re.I)),
    ("VBS/JS 下载器", re.compile(
        r"(msxml2\.xmlhttp|adodb\.stream|wscript\.shell)", re.I)),
    ("certutil 解码", re.compile(r"certutil\s+-urlcache|-decode", re.I)),
    ("bitsadmin 传输", re.compile(r"bitsadmin\s+/transfer", re.I)),
    ("curl 下载可执行文件", re.compile(
        r"curl[^\r\n]{0,80}?-o[^\r\n]{0,80}?\.(exe|bat|vbs|ps1)\b", re.I)),
]

_ZIP_BOMB_ENTRIES = 5000
_ZIP_BOMB_TOTAL_RATIO = 200.0        # 解压总量/压缩量
_ZIP_BOMB_TOTAL_COMP_MIN = 512 << 10 # 压缩后至少 512KB 才做总量比例判定(防小 zip 误报)
_ZIP_BOMB_ENTRY_RATIO = 500.0        # 单条目解压/压缩比
_ZIP_BOMB_ENTRY_UNCOMP_MIN = 64 << 20  # 单条目解压至少 64MB


def _scan_heuristic(cfg: SecurityConfig, p: Path) -> EngineFinding:
    if not cfg.heuristic_enabled:
        return EngineFinding("heuristic", "skipped", "SECURITY_HEURISTIC_ENABLED=0")
    issues: list[str] = []

    try:
        head = p.read_bytes()[: (512 << 10)]
    except Exception as e:
        return EngineFinding("heuristic", "error",
                             f"读取文件失败: {type(e).__name__}: {str(e)[:120]}")
    size = p.stat().st_size if p.exists() else 0
    ext = p.suffix.lower()
    info = _sniff_magic(head)
    magic = (info or {}).get("kind", "")

    # 1) 可执行/脚本魔数伪装成良性扩展名
    if magic in _EXEC_MAGIC and ext not in _EXEC_OK_EXTS:
        issues.append(f"伪装可执行文件: 扩展名 {ext or '(无)'} 实为 {magic}")
    # 2) 扩展名族 vs 魔数族不匹配(如 .jpg 实为 zip / .pdf 实为 exe)
    if magic and not issues:
        family = _ext_family(ext)
        if family and magic not in _MAGIC_EXPECT[family]:
            issues.append(f"格式不符: {ext or '(无扩展名)'} 期望 {sorted(_MAGIC_EXPECT[family])} 实为 {magic}")
    # 3) 压缩炸弹(zip 中央目录即可判定,不解压)
    if ext in (".zip", ".jar", ".litematic", ".schematic", ".schem", ".mcworld"):
        bomb = _zip_bomb_check(p)
        if bomb:
            issues.append(bomb)
    # 4) 脚本载荷内容(非文档类扩展名里出现 PowerShell/下载器特征)
    if ext not in _SCRIPT_EXEMPT_EXTS and magic not in ("exe", "elf"):
        try:
            text = head.decode("utf-8", "replace")
        except Exception:
            text = ""
        if text:
            for label, pat in _SCRIPT_PATTERNS:
                if pat.search(text):
                    issues.append(f"脚本载荷特征: {label}")
                    break
    # 5) 双重扩展名 / 尾随空格伪装
    if _double_ext(p.name):
        issues.append(f"双重扩展名伪装: {p.name!r}")

    if issues:
        return EngineFinding("heuristic", "suspicious", "; ".join(dict.fromkeys(issues)))
    return EngineFinding("heuristic", "clean", f"启发式: 未发现异常(大小 {size}B)")


def _ext_family(ext: str) -> str:
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"):
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods"):
        return "doc"
    if ext in (".zip", ".7z", ".rar", ".tar", ".gz", ".jar"):
        return "archive"
    if ext in (".mp4", ".mkv", ".webm", ".mp3", ".flac", ".wav", ".ogg", ".mov", ".avi"):
        return "media"
    if ext in (".litematic", ".schematic", ".schem", ".mcworld"):
        return "schematic"
    return ""


def _zip_bomb_check(p: Path) -> str:
    try:
        with zipfile.ZipFile(p) as z:
            infos = z.infolist()
    except Exception:
        return ""
    if len(infos) > _ZIP_BOMB_ENTRIES:
        return f"压缩炸弹: 条目过多({len(infos)} 个)"
    comp = sum(i.compress_size for i in infos)
    uncomp = sum(i.file_size for i in infos)
    if comp > _ZIP_BOMB_TOTAL_COMP_MIN and uncomp > comp * _ZIP_BOMB_TOTAL_RATIO:
        return (f"压缩炸弹: 解压 {uncomp >> 20}MB / 压缩 {comp >> 20}MB"
                f"(比例 {uncomp / max(comp, 1):.0f} 倍)")
    for i in infos:
        if (i.compress_size > 0 and i.file_size > _ZIP_BOMB_ENTRY_UNCOMP_MIN
                and i.file_size / i.compress_size > _ZIP_BOMB_ENTRY_RATIO):
            return (f"压缩炸弹: 条目 {i.filename[:60]} 解压 {i.file_size >> 20}MB"
                    f"(比例 {i.file_size / i.compress_size:.0f} 倍)")
    return ""


def _double_ext(name: str) -> bool:
    low = name.lower().rstrip()
    if low.endswith((".", " ")):
        return True
    # photo.jpg.exe / photo.pdf.bat / 中文.图片.png.exe
    m = re.search(r"\.([a-z0-9]{2,5})\.([a-z0-9]{2,5})$", low)
    if not m:
        return False
    first, second = m.group(1), m.group(2)
    benign = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "pdf", "doc", "docx",
              "xls", "xlsx", "zip", "rar", "7z", "txt", "litematic", "schematic"}
    exec_ext = {"exe", "bat", "cmd", "vbs", "vbe", "ps1", "scr", "com", "js", "jar", "msi", "hta", "lnk", "sh"}
    return first in benign and second in exec_ext


# 魔数表(独立于 ai/magic,技能层不反向依赖 AI 层):kind 与 ai.magic 词汇对齐
_MAGIC_TABLE: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"\x1f\x8b\x08", "gzip"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x1a\x45\xdf\xa3", "mkv"),
    (b"ftyp", "mp4"),
    (b"ID3", "mp3"),
    (b"OggS", "ogg"),
    (b"MZ", "exe"),
    (b"\x7fELF", "elf"),
    (b"\xca\xfe\xba\xbe", "class"),
    (b"\x0a", "nbt"),  # 未压缩 NBT(.schematic 可能)
]
_SCRIPT_SHEBANG = re.compile(rb"^#!\s*(/\w+)+/(sh|bash|python|perl|ruby|node|pwsh|powershell)\b")


def _sniff_magic(head: bytes) -> Optional[dict]:
    if _SCRIPT_SHEBANG.match(head):
        return {"kind": "script", "note": "脚本(shebang)"}
    for magic, kind in _MAGIC_TABLE:
        if head.startswith(magic):
            return {"kind": kind, "note": kind.upper()}
    # WebP:RIFF + WEBP 标记
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return {"kind": "webp", "note": "WebP"}
    # BMP
    if head.startswith(b"BM"):
        return {"kind": "bmp", "note": "BMP"}
    return None
