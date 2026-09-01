"""技能管理器(mod 式装/卸)单元测试。

隔离:把 manager.SKILLS_DIR / STATE_FILE / ARCHIVE_DIR 指到临时目录;
用唯一工具名(不影响其他测试)。
"""

import json
import tempfile
import unittest
from pathlib import Path

import skills.manager as mgr
from skills.core import ToolResult, ToolSpec, get_registry, tool


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rh_skill_"))
        mgr.SKILLS_DIR = self.tmp / "skills"
        mgr.STATE_FILE = self.tmp / "skills" / ".state.json"
        mgr.ARCHIVE_DIR = self.tmp / "skills_archive"
        mgr.SKILLS_DIR.mkdir(parents=True)
        # 注册唯一测试工具
        self.tool_name = "zz_test_skill_tool"
        if not get_registry().has(self.tool_name):
            @tool(self.tool_name, "测试工具", parameters={}, category="other")
            def _t() -> ToolResult:
                return ToolResult.success("ok")
        self._reg = get_registry()

    def tearDown(self):
        self._reg.enable(self.tool_name)

    def _make_skill(self, name: str, tools: list[str]) -> Path:
        p = mgr.SKILLS_DIR / name
        p.mkdir(parents=True)
        (p / "__init__.py").write_text("", encoding="utf-8")
        (p / "skill.json").write_text(json.dumps(
            {"name": name, "version": "1.0.0", "description": "test",
             "tools": tools, "requires": []}), encoding="utf-8")
        return p

    def test_list_empty(self):
        self.assertEqual(mgr.list_skills(), [])

    def test_install_from_external(self):
        ext = Path(tempfile.mkdtemp()) / "my_mod"
        ext.mkdir()
        (ext / "__init__.py").write_text("", encoding="utf-8")
        (ext / "skill.json").write_text(json.dumps(
            {"name": "my_mod", "version": "1.0.0", "description": "d",
             "tools": [self.tool_name], "requires": []}), encoding="utf-8")
        self.assertEqual(mgr.cmd_install(str(ext)), 0)
        self.assertTrue((mgr.SKILLS_DIR / "my_mod").exists())
        names = [s["name"] for s in mgr.list_skills()]
        self.assertIn("my_mod", names)

    def test_disable_hides_tool_from_catalog_and_invoke(self):
        self._make_skill("zz_skill", [self.tool_name])
        self.assertEqual(mgr.cmd_toggle("zz_skill", False), 0)
        self.assertNotIn(self.tool_name, [e["function"]["name"] for e in self._reg.catalog()])
        r = self._reg.invoke(self.tool_name, {})
        self.assertFalse(r.ok)
        self.assertIn("停用", r.message)
        # 恢复
        mgr.cmd_toggle("zz_skill", True)
        self.assertTrue(self._reg.invoke(self.tool_name, {}).ok)

    def test_uninstall_moves_to_archive_and_disables(self):
        self._make_skill("zz_skill2", [self.tool_name])
        self.assertEqual(mgr.cmd_uninstall("zz_skill2"), 0)
        self.assertFalse((mgr.SKILLS_DIR / "zz_skill2").exists())
        self.assertTrue((mgr.ARCHIVE_DIR / "zz_skill2").exists())
        self.assertFalse(self._reg.invoke(self.tool_name, {}).ok)

    def test_apply_state_restores_disabled(self):
        self._make_skill("zz_skill3", [self.tool_name])
        mgr.cmd_toggle("zz_skill3", False)
        # 模拟新进程:清空注册表禁用集合后 apply_state
        self._reg.enable(self.tool_name)
        mgr.apply_state()
        self.assertFalse(self._reg.invoke(self.tool_name, {}).ok)

    def test_unknown_command_returns_2(self):
        from unittest.mock import patch

        with patch("sys.argv", ["manager", "uninstall", "no_such_skill"]):
            rc = mgr.main(["uninstall", "no_such_skill"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
