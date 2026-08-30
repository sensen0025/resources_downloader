"""下载安全查毒技能(skills/security)单元测试。

不依赖真实 ClamAV:启发式与 verdict 聚合可离线验证;
clamd/clamscan/yara 缺失时走 error/unknown 分支(即本机常态)。
"""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from skills.security import ScanResult, detect_engines, scan_file


def _tmp(prefix: str) -> Path:
    d = tempfile.TemporaryDirectory(prefix=f"rh_sec_{prefix}_")
    _tmp.dirs.append(d)
    return Path(d.name)


_tmp.dirs = []  # type: ignore[attr-defined]


def tearDownModule():
    for d in getattr(_tmp, "dirs", []):
        try:
            d.cleanup()
        except Exception:
            pass


class TestHeuristic(unittest.TestCase):
    def test_disguised_executable(self):
        """.jpg 实为 MZ 可执行文件 → suspicious(伪装可执行文件)。"""
        p = _tmp("exe") / "photo.jpg"
        p.write_bytes(b"MZ" + b"\x90" * 128)
        r = scan_file(p, engines=["heuristic"])
        self.assertEqual(r.verdict, "suspicious")
        self.assertFalse(r.ok)
        self.assertIn("伪装可执行文件", r.findings[0].detail)

    def test_magic_extension_mismatch(self):
        """.png 实为 zip → suspicious(格式不符)。"""
        p = _tmp("mm") / "fake.png"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        r = scan_file(p, engines=["heuristic"])
        self.assertEqual(r.verdict, "suspicious")
        self.assertIn("格式不符", r.findings[0].detail)

    def test_zip_bomb(self):
        """高压缩比 zip(100MB 零 → 百 KB)→ suspicious(压缩炸弹)。"""
        p = _tmp("bomb") / "bomb.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("payload.bin", b"\x00" * (100 << 20))
        r = scan_file(p, engines=["heuristic"])
        self.assertEqual(r.verdict, "suspicious")
        self.assertIn("压缩炸弹", r.findings[0].detail)

    def test_script_payload_in_pdf(self):
        """伪装文档里出现 PowerShell 编码命令 → suspicious(脚本载荷)。"""
        p = _tmp("script") / "readme.pdf"
        p.write_text("hello\npowershell -EncodedCommand SQBFAFgA\n", encoding="utf-8")
        r = scan_file(p, engines=["heuristic"])
        self.assertEqual(r.verdict, "suspicious")
        self.assertIn("脚本载荷", r.findings[0].detail)

    def test_double_extension(self):
        p = _tmp("dext") / "photo.jpg.exe"
        p.write_bytes(b"whatever")
        r = scan_file(p, engines=["heuristic"])
        self.assertEqual(r.verdict, "suspicious")
        self.assertIn("双重扩展名", r.findings[0].detail)

    def test_clean_png(self):
        """真 PNG 头 + 随机尾 → 启发式干净;无杀毒引擎 → verdict=unknown 且放行。"""
        p = _tmp("clean") / "ok.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(96))
        r = scan_file(p, engines=["heuristic"])
        self.assertEqual(r.findings[0].status, "clean")
        self.assertEqual(r.verdict, "unknown")  # 无 AV 引擎 → 诚实标注覆盖不足
        self.assertTrue(r.ok)                   # 但不阻塞流程

    def test_missing_file(self):
        r = scan_file(_tmp("none") / "nope.bin")
        self.assertEqual(r.verdict, "unknown")
        self.assertIn("不存在", r.summary)

    def test_detect_engines_shape(self):
        avail = detect_engines()
        self.assertIn("clamav", avail)
        self.assertIn("yara", avail)
        self.assertTrue(avail["heuristic"])


class TestScanFileTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 触发 ai.skills 的 side-effect 注册(与 AgentCore 同路径)
        import ai.skills  # noqa: F401
        from skills.core import get_registry

        cls.registry = get_registry()

    def test_tool_registered(self):
        self.assertTrue(self.registry.has("scan_file"))

    def test_invoke_clean(self):
        p = _tmp("tool_clean") / "ok.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        r = self.registry.invoke("scan_file", {"path": str(p)})
        self.assertTrue(r.ok)
        self.assertIn("verdict", str(r.data))

    def test_invoke_disguised_fails(self):
        p = _tmp("tool_bad") / "photo.jpg"
        p.write_bytes(b"MZ" + b"\x90" * 64)
        r = self.registry.invoke("scan_file", {"path": str(p)})
        self.assertFalse(r.ok)
        self.assertEqual(r.data["verdict"], "suspicious")

    def test_invoke_missing_path_rejected(self):
        r = self.registry.invoke("scan_file", {})
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "INVALID_ARGS")

    def test_invoke_unknown_path(self):
        r = self.registry.invoke("scan_file", {"path": str(_tmp("tool_none") / "x.bin")})
        self.assertFalse(r.ok)  # 文件不存在 → 失败


if __name__ == "__main__":
    unittest.main()
