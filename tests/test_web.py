"""网页控制台测试:页面可达、状态/配置(脱敏与读写 .env)、查毒端点、LLM 测试。

隔离:RH_DB_PATH 指向临时库;api.settings.ENV_PATH 指向临时 .env(绝不碰真实 .env)。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="rh_web_test_")
os.environ["RH_DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ.pop("RH_API_SECRET", None)
for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "MAIL_IMAP_HOST",
          "MAIL_IMAP_PORT", "MAIL_EMAIL", "MAIL_PASSWORD", "RH_PROXY_URL"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from importlib import import_module  # noqa: E402

import api.settings as settings_mod  # noqa: E402

api_mod = import_module("api.app")
settings_mod.ENV_PATH = Path(_tmp) / ".env"  # 测试用临时 .env
client = TestClient(api_mod.app)


class TestPage(unittest.TestCase):
    def test_dashboard_served(self):
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Resource Hub", r.text)

    def test_static_assets(self):
        self.assertEqual(client.get("/static/app.js").status_code, 200)
        self.assertEqual(client.get("/static/style.css").status_code, 200)
        self.assertIn("refreshStatus", client.get("/static/app.js").text)


class TestStatus(unittest.TestCase):
    def test_status_shape(self):
        r = client.get("/api/v1/status")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertIn("llm_key_set", data)
        self.assertIn("token_mode", data)
        self.assertIn("clamav", data)


_SCHEMA_KEYS = ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "MAIL_IMAP_HOST",
                "MAIL_IMAP_PORT", "MAIL_EMAIL", "MAIL_PASSWORD", "RH_PROXY_URL",
                "RH_API_SECRET"]


def _clear_env_keys():
    for k in _SCHEMA_KEYS:
        os.environ.pop(k, None)


class TestSettings(unittest.TestCase):
    def setUp(self):
        # 清空临时 .env 与相关环境变量(防真实 .env 的 load_dotenv 污染)
        if settings_mod.ENV_PATH.exists():
            settings_mod.ENV_PATH.unlink()
        _clear_env_keys()

    def test_settings_empty(self):
        r = client.get("/api/v1/settings")
        data = r.json()["data"]["fields"]
        self.assertFalse(data["llm_api_key"]["set"])
        self.assertFalse(data["mail_password"]["set"])

    def test_masked_no_plaintext(self):
        settings_mod.ENV_PATH.write_text("LLM_API_KEY=sk-secret-1234567890\n", encoding="utf-8")
        r = client.get("/api/v1/settings")
        field = r.json()["data"]["fields"]["llm_api_key"]
        self.assertTrue(field["set"])
        self.assertIn("****", field["value"])
        self.assertNotIn("secret-1234567890", field["value"])  # 不回明文
        self.assertTrue(field["value"].startswith("sk-"))

    def test_update_writes_env_and_file(self):
        r = client.put("/api/v1/settings",
                       json={"llm_api_key": "sk-newvalue987654", "proxy_url": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(os.environ.get("LLM_API_KEY"), "sk-newvalue987654")
        os.environ.pop("LLM_API_KEY")  # 断言后清理,避免污染其他用例
        content = settings_mod.ENV_PATH.read_text(encoding="utf-8")
        self.assertIn("LLM_API_KEY=sk-newvalue987654", content)
        self.assertNotIn("RH_PROXY_URL", content)  # 空值 = 清除

    def test_update_none_keeps_existing(self):
        settings_mod.ENV_PATH.write_text("LLM_API_KEY=sk-keepme\n", encoding="utf-8")
        r = client.put("/api/v1/settings", json={"llm_base_url": "https://x/v1"})
        self.assertEqual(r.status_code, 200)
        # 未提交字段 → 文件保留,环境变量不动
        content = settings_mod.ENV_PATH.read_text(encoding="utf-8")
        self.assertIn("LLM_API_KEY=sk-keepme", content)
        self.assertIn("LLM_BASE_URL=https://x/v1", content)
        self.assertNotIn("LLM_API_KEY", os.environ)

    def test_clear_value(self):
        os.environ["RH_PROXY_URL"] = "http://p:1"
        r = client.put("/api/v1/settings", json={"proxy_url": ""})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("RH_PROXY_URL", os.environ)


class TestScanEndpoint(unittest.TestCase):
    def test_scan_suspicious_file(self):
        p = Path(_tmp) / "photo.jpg"
        p.write_bytes(b"MZ" + b"\x90" * 64)
        r = client.post("/api/v1/scan", json={"path": str(p)})
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertEqual(data["verdict"], "suspicious")

    def test_scan_missing_path(self):
        r = client.post("/api/v1/scan", json={"path": "/no/such/file"})
        self.assertEqual(r.status_code, 404)


class TestLlmTest(unittest.TestCase):
    def test_without_key_fails_gracefully(self):
        from unittest import mock

        _clear_env_keys()
        # 打桩 load_dotenv,防止 _config() 重新加载真实 .env 把 key 带回来
        with mock.patch("agent.llm.load_dotenv", lambda *a, **k: None):
            r = client.post("/api/v1/test-llm")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertFalse(data["ok"])
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
