"""VPN/代理配置(proxy.py)单元测试。

不碰网络:只验证配置解析优先级、requests/Playwright 参数形态、模板内容。
"""

import os
import unittest

import proxy as proxy_mod

_PROXY = "http://proxy.mornai.cn:7890"

# 记录原有环境变量,测试后恢复
_SAVED = {k: os.environ.get(k) for k in
          ("RH_PROXY_URL", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY")}


def tearDownModule():
    for k, v in _SAVED.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class ProxyTestCase(unittest.TestCase):
    def setUp(self):
        for k in ("RH_PROXY_URL", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            os.environ.pop(k, None)

    def test_disabled_by_default(self):
        self.assertFalse(proxy_mod.is_enabled())
        self.assertEqual(proxy_mod.proxies(), {})
        self.assertIsNone(proxy_mod.playwright_proxy())

    def test_rh_proxy_url_wins(self):
        os.environ["RH_PROXY_URL"] = _PROXY
        os.environ["HTTPS_PROXY"] = "http://other:8888"
        self.assertTrue(proxy_mod.is_enabled())
        p = proxy_mod.proxies()
        self.assertEqual(p["http"], _PROXY)
        self.assertEqual(p["https"], _PROXY)
        self.assertIn("localhost", p["no_proxy"])

    def test_standard_env_fallback(self):
        os.environ["https_proxy"] = _PROXY
        self.assertEqual(proxy_mod.proxy_url(), _PROXY)

    def test_http_proxy_fallback(self):
        os.environ["HTTP_PROXY"] = _PROXY
        self.assertEqual(proxy_mod.proxy_url(), _PROXY)

    def test_session_applies_proxy(self):
        os.environ["RH_PROXY_URL"] = _PROXY
        s = proxy_mod.session()
        self.assertEqual(s.proxies.get("https"), _PROXY)

    def test_apply_proxies_existing_session(self):
        import requests

        s = requests.Session()
        proxy_mod.apply_proxies(s)
        self.assertEqual(s.proxies, {})  # 未配置 → 无操作
        os.environ["RH_PROXY_URL"] = _PROXY
        proxy_mod.apply_proxies(s)
        self.assertEqual(s.proxies.get("http"), _PROXY)

    def test_playwright_proxy_shape(self):
        os.environ["RH_PROXY_URL"] = _PROXY
        p = proxy_mod.playwright_proxy()
        self.assertEqual(p["server"], _PROXY)
        self.assertIn("localhost", p["bypass"])

    def test_template_contains_guide(self):
        t = proxy_mod.export_env_template()
        self.assertIn("RH_PROXY_URL", t)
        self.assertIn("proxy.mornai.cn", t)
        self.assertIn("no_proxy", t)


if __name__ == "__main__":
    unittest.main()
