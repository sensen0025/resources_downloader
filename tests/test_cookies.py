"""登录态 Cookie 存储层(agent/cookies.py)单元测试。

不依赖 playwright:save 用鸭子类型的假 context(storage_state(path=...)),
过期过滤/路径生成/删除全是纯逻辑,可离线验证。
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from agent.cookies import (
    _sanitize,
    cookie_path,
    delete_cookies,
    has_cookies,
    load_cookies,
    save_cookies,
)


def _cookie(name: str, expires: float, domain: str = "example.com") -> dict:
    return {"name": name, "value": "v", "domain": domain, "path": "/",
            "expires": expires, "httpOnly": True, "secure": True,
            "sameSite": "Lax"}


def _state(*cookies) -> dict:
    return {"cookies": list(cookies), "origins": []}


class FakeContext:
    """鸭子类型 context:storage_state(path=...) 落盘。"""

    def __init__(self, state: dict):
        self._state = state

    def storage_state(self, path: str = None):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(self._state), encoding="utf-8")
        return self._state


class TestPath(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(_sanitize("claude.ai"), "claude.ai")
        self.assertEqual(_sanitize("me+alias@gmail.com"), "me_alias_gmail.com")
        self.assertEqual(_sanitize("a/b:c"), "a_b_c")
        self.assertEqual(_sanitize(""), "default")

    def test_cookie_path_shape(self):
        p = cookie_path("claude.ai", "me@gmail.com")
        self.assertEqual(p.name, "claude.ai__me_gmail.com.json")
        self.assertIn("cookies", str(p.parent))

    def test_cookie_path_no_email(self):
        p = cookie_path("planetminecraft.com", "")
        self.assertEqual(p.name, "planetminecraft.com.json")


class TestExpiry(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="rh_ck_")
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _write(self, state: dict) -> Path:
        p = self.dir / "ck.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        return p

    def test_expired_filtered(self):
        now = time.time()
        p = self._write(_state(_cookie("old", now - 3600)))
        self.assertIsNone(load_cookies(p))
        self.assertFalse(has_cookies(p))

    def test_valid_kept(self):
        now = time.time()
        p = self._write(_state(_cookie("sid", now + 3600)))
        state = load_cookies(p)
        self.assertIsNotNone(state)
        self.assertEqual(len(state["cookies"]), 1)
        self.assertTrue(has_cookies(p))

    def test_session_cookie_kept(self):
        # expires=-1 = session cookie,会话内有效 → 保留
        p = self._write(_state(_cookie("session", -1)))
        self.assertTrue(has_cookies(p))

    def test_mixed_filters_only_expired(self):
        now = time.time()
        p = self._write(_state(_cookie("old", now - 10), _cookie("sid", now + 3600)))
        state = load_cookies(p)
        self.assertEqual([c["name"] for c in state["cookies"]], ["sid"])

    def test_missing_file(self):
        self.assertIsNone(load_cookies(self.dir / "nope.json"))
        self.assertFalse(has_cookies(self.dir / "nope.json"))

    def test_bad_json(self):
        p = self.dir / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        self.assertIsNone(load_cookies(p))


class TestSaveDelete(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="rh_ck2_")
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_save_roundtrip(self):
        now = time.time()
        p = self.dir / "site__user.json"
        ctx = FakeContext(_state(_cookie("sid", now + 3600)))
        saved = save_cookies(ctx, p)
        self.assertEqual(saved, p)
        self.assertTrue(p.exists())
        self.assertTrue(has_cookies(p))
        self.assertEqual(load_cookies(p)["cookies"][0]["name"], "sid")

    def test_delete(self):
        p = self.dir / "x.json"
        p.write_text("{}", encoding="utf-8")
        self.assertTrue(delete_cookies(p))
        self.assertFalse(p.exists())
        self.assertFalse(delete_cookies(p))  # 已删 → False


if __name__ == "__main__":
    unittest.main()
