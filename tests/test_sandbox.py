"""AI 本地程序沙盒单元测试:路径沙箱/读写/执行/超时/危险命令拦截/工具注册。"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from skills.sandbox import resolve_in_root, run_command, sandbox_root
from skills.sandbox import sandbox_read, sandbox_write, sandbox_run  # noqa: F401


def _ctx(td: str):
    return SimpleNamespace(task=SimpleNamespace(out_dir=td), session=None)


class TestPathSandbox(unittest.TestCase):
    def test_allows_inside_blocks_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(resolve_in_root(root, "a/b.txt"),
                             (root / "a" / "b.txt").resolve())
            self.assertEqual(resolve_in_root(root, "script.py"),
                             (root / "script.py").resolve())
            self.assertIsNone(resolve_in_root(root, "../etc/passwd"))
            self.assertIsNone(resolve_in_root(root, "/etc/passwd"))
            self.assertIsNone(resolve_in_root(root, ".."))
            # 绝对路径拼接(以 / 开头会覆盖 root)也拦截
            self.assertIsNone(resolve_in_root(root, "/etc/passwd"))


class TestReadWrite(unittest.TestCase):
    def test_write_then_read(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = _ctx(td)
            r = sandbox_write("sub/extract.py", "print('hi')", ctx=ctx)
            self.assertTrue(r.ok)
            self.assertTrue((Path(td) / "sub" / "extract.py").exists())
            r2 = sandbox_read("sub/extract.py", ctx=ctx)
            self.assertTrue(r2.ok)
            self.assertIn("print", r2.message)

    def test_write_escape_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = sandbox_write("../evil.txt", "x", ctx=_ctx(td))
            self.assertFalse(r.ok)
            self.assertIn("越界", r.message)

    def test_no_ctx_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(sandbox_write("a.txt", "x").ok)


class TestRun(unittest.TestCase):
    def test_echo(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_command("echo hello-sandbox", Path(td))
        self.assertTrue(r["ok"])
        self.assertIn("hello-sandbox", r["stdout"])

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_command(
                f"{sys.executable} -c \"import time; time.sleep(10)\"", Path(td), timeout=1)
        self.assertFalse(r["ok"])
        self.assertIn("超时", r["error"])

    def test_dangerous_command_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_command("rm -rf /etc", Path(td))
        self.assertFalse(r["ok"])
        self.assertIn("拦截", r["error"])

    def test_output_capped(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_command(
                f"{sys.executable} -c \"print('x'*999999)\"", Path(td), timeout=30)
        self.assertLessEqual(len(r["stdout"]), 200 << 10)


class TestSandboxPython(unittest.TestCase):
    def test_python_script_runs(self):
        from skills.sandbox import sandbox_python

        with tempfile.TemporaryDirectory() as td:
            r = sandbox_python("import os\nprint('CWD', os.getcwd().split('/')[-1])",
                               ctx=_ctx(td))
            self.assertTrue(r.ok, r.message)
            self.assertIn("CWD", r.message)

    def test_python_error_reported(self):
        from skills.sandbox import sandbox_python

        with tempfile.TemporaryDirectory() as td:
            r = sandbox_python("raise RuntimeError('boom')", ctx=_ctx(td))
        self.assertFalse(r.ok)
        self.assertIn("boom", r.message)


class TestSessionCookies(unittest.TestCase):
    def test_cookies_written_to_sandbox(self):
        from skills.sandbox import sandbox_python

        class _CtxSession:
            context = SimpleNamespace(
                cookies=lambda: [{"name": "buvid3", "value": "abc"},
                                 {"name": "SESSDATA", "value": "x"}])

        class _Ctx:
            task = SimpleNamespace(out_dir="")
            session = _CtxSession()

        with tempfile.TemporaryDirectory() as td:
            _Ctx.task = SimpleNamespace(out_dir=td)
            # sandbox_python 跑一段读 cookie 文件的脚本
            r = sandbox_python(
                "import json,os\n"
                "p=os.path.join(os.getcwd(),'_session_cookies.json')\n"
                "print('HAS', os.path.exists(p), json.load(open(p)).get('buvid3'))",
                ctx=_Ctx())
            self.assertTrue(r.ok, r.message)
            self.assertIn("HAS True abc", r.message)


class TestToolsRegistered(unittest.TestCase):
    def test_sandbox_tools_registered(self):
        import importlib

        importlib.import_module("skills.sandbox")
        from skills.core import get_registry

        reg = get_registry()
        for name in ("sandbox_read", "sandbox_write", "sandbox_python", "sandbox_run"):
            self.assertTrue(reg.has(name), name)


if __name__ == "__main__":
    unittest.main()
