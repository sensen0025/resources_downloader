"""技能内核(DSH 式 @tool 注册表)单元测试。"""

import unittest

from skills.core import ToolResult, ToolSpec, get_registry, tool, validate_args


def _fresh_registry():
    from skills.core import ToolRegistry

    return ToolRegistry()


class TestValidateArgs(unittest.TestCase):
    def test_missing_required(self):
        params = {"type": "object",
                  "properties": {"url": {"type": "string"}},
                  "required": ["url"]}
        self.assertEqual(validate_args(params, {}),
                         ["args: 缺少必填字段 'url'"])

    def test_type_mismatch(self):
        params = {"type": "object",
                  "properties": {"n": {"type": "integer"}},
                  "required": ["n"]}
        self.assertIn("期望 integer", validate_args(params, {"n": "x"})[0])

    def test_enum(self):
        params = {"type": "object",
                  "properties": {"k": {"type": "string", "enum": ["a", "b"]}},
                  "required": ["k"]}
        self.assertTrue(validate_args(params, {"k": "c"}))

    def test_nested(self):
        params = {"type": "object",
                  "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                  "required": ["items"]}
        self.assertTrue(validate_args(params, {"items": [1, 2]}))


class TestRegistry(unittest.TestCase):
    def test_register_invoke_validate(self):
        reg = _fresh_registry()

        def handler(url: str, min_size: int = 0) -> ToolResult:
            return ToolResult.success(f"downloaded {url}", data={"url": url})

        reg.register(ToolSpec(name="dl", description="download",
                              parameters={"type": "object",
                                          "properties": {"url": {"type": "string"},
                                                         "min_size": {"type": "integer"}},
                                          "required": ["url"]},
                              category="execute", handler=handler))
        # 合法
        r = reg.invoke("dl", {"url": "https://x/a.litematic"})
        self.assertTrue(r.ok)
        self.assertIn("downloaded", r.message)
        # 非法(缺必填)
        r = reg.invoke("dl", {})
        self.assertFalse(r.ok)

    def test_extra_args_ignored(self):
        """LLM 偶尔带 schema 外多余字段(如 human 带 success)→ 忽略不 TypeError。"""
        reg = _fresh_registry()

        def handler(question: str) -> ToolResult:
            return ToolResult.success(f"q={question}")

        reg.register(ToolSpec(name="human", description="ask",
                              parameters={"type": "object",
                                          "properties": {"question": {"type": "string"}},
                                          "required": ["question"]},
                              category="other", handler=handler))
        r = reg.invoke("human", {"question": "hi", "success": True, "extra": 1})
        self.assertTrue(r.ok)
        self.assertIn("q=hi", r.message)
        # 未知工具
        r = reg.invoke("nope", {})
        self.assertFalse(r.ok)
        # handler 抛异常 → 规范化失败
        def boom():
            raise RuntimeError("boom")

        reg.register(ToolSpec(name="boom", description="x", parameters={},
                              category="execute", handler=boom))
        r = reg.invoke("boom", {})
        self.assertFalse(r.ok)
        self.assertIn("RuntimeError", r.error)

    def test_ctx_injected_by_name(self):
        reg = _fresh_registry()
        captured = {}

        def handler(ctx=None):
            captured["ctx"] = ctx
            return ToolResult.success("ok")

        reg.register(ToolSpec(name="t", description="x", parameters={},
                              category="read", handler=handler))
        reg.invoke("t", {}, ctx="CTX_VALUE")
        self.assertEqual(captured["ctx"], "CTX_VALUE")

    def test_catalog_openai_format(self):
        reg = _fresh_registry()
        reg.register(ToolSpec(name="t", description="desc",
                              parameters={"type": "object", "properties": {}, "required": []},
                              category="search", handler=lambda: ToolResult.success("")))
        entry = reg.catalog(["t"])[0]
        self.assertEqual(entry["type"], "function")
        self.assertEqual(entry["function"]["name"], "t")
        self.assertIn("parameters", entry["function"])

    def test_duplicate_register_rejected(self):
        reg = _fresh_registry()
        reg.register(ToolSpec(name="t", description="x", parameters={}, handler=lambda: None))
        with self.assertRaises(ValueError):
            reg.register(ToolSpec(name="t", description="y", parameters={}, handler=lambda: None))

    def test_bad_category_rejected(self):
        reg = _fresh_registry()
        with self.assertRaises(ValueError):
            reg.register(ToolSpec(name="t", description="x", parameters={}, category="bogus",
                                  handler=lambda: None))


class TestToolDecorator(unittest.TestCase):
    def test_decorator_registers(self):
        reg = _fresh_registry()

        @tool("demo_tool", "demo", {"type": "object", "properties": {}, "required": []},
              category="read", registry=reg)
        def _demo():
            return ToolResult.success("hi")

        self.assertTrue(reg.has("demo_tool"))
        r = reg.invoke("demo_tool", {})
        self.assertEqual(r.message, "hi")


if __name__ == "__main__":
    unittest.main()
