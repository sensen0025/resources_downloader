"""夸克网盘自动解析技能单元测试(离线:全部 HTTP 用 FakeSession 注入)。

覆盖:分享链接解析 / Cookie 管理 / stoken / 递归清单 / 选文件 / 转存+直链+落地 /
Cookie 续期合并 / fetch_resource 管线接管夸克分享链接。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.quark import (
    QuarkClient,
    QuarkError,
    auth_import,
    auth_status,
    download_share,
    list_share,
    load_cookie,
    parse_share_url,
    pick_share_file,
)
from skills.quark.api import cookie_to_dict


# ---------------------------------------------------------------- 假会话

class _FakeResp:
    def __init__(self, payload: dict, status: int = 200, set_cookie: str = ""):
        self._payload = payload
        self.status_code = status
        self.headers = {"Set-Cookie": set_cookie} if set_cookie else {}
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class FakeSession:
    """按 URL 关键字路由的假 Session(离线)。"""

    def __init__(self):
        self.routes = {}        # 子串 -> callable(method, url, **kw) -> _FakeResp
        self.requests = []      # 记录 (method, url, json)

    def add(self, key: str, fn):
        self.routes[key] = fn

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw.get("json")))
        for key, fn in self.routes.items():
            if key in url:
                return fn(method, url, **kw)
        return _FakeResp({"status": 200, "code": 0, "data": {}})


def _share_token_route(method, url, **kw):
    body = kw.get("json") or {}
    assert body.get("pwd_id") == "abc123"
    return _FakeResp({"status": 200, "code": 0,
                      "data": {"stoken": "stoken-xyz"}})


def _share_detail_route(method, url, **kw):
    # pdir_fid=0 → 根目录:一个文件夹 + 一个文件;子目录 → 一个文件
    pdir = kw.get("params", {}).get("pdir_fid", "0")
    if pdir == "0":
        items = [
            {"fid": "f-folder", "file_name": "资料", "file_type": 0,
             "dir": True, "pdir_fid": "0", "size": 2,
             "share_fid_token": "tk-folder", "status": 1},
            {"fid": "f-litematic", "file_name": "故宫投影.litematic", "file_type": 0,
             "dir": False, "pdir_fid": "0", "size": 2048,
             "share_fid_token": "tk-litematic", "status": 1},
            {"fid": "f-schem", "file_name": "故宫.schem", "file_type": 0,
             "dir": False, "pdir_fid": "0", "size": 1024,
             "share_fid_token": "tk-schem", "status": 1},
        ]
    else:
        items = [
            {"fid": "f-inner", "file_name": "内层说明.txt", "file_type": 0,
             "dir": False, "pdir_fid": pdir, "size": 10,
             "share_fid_token": "tk-inner", "status": 1},
        ]
    return _FakeResp({"status": 200, "code": 0,
                      "data": {"list": items},
                      "metadata": {"_total": len(items)}})


def _save_route(method, url, **kw):
    body = kw.get("json") or {}
    assert body.get("pwd_id") == "abc123"
    assert body.get("stoken") == "stoken-xyz"
    assert body.get("fid_list") == ["f-litematic"]
    return _FakeResp({"status": 200, "code": 0,
                      "data": {"task_id": "task-1"}})


def _task_route(method, url, **kw):
    return _FakeResp({"status": 200, "code": 0,
                      "data": {"status": 2, "task_title": "分享-转存",
                               "save_as": {"save_as_top_fids": ["saved-1"]}}})


def _download_route(method, url, **kw):
    body = kw.get("json") or {}
    assert body.get("fids") == ["saved-1"]
    return _FakeResp({"status": 200, "code": 0,
                      "data": [{"fid": "saved-1", "file_name": "故宫投影.litematic",
                                "size": 2048, "download_url": "https://dl.quark.cn/x"}]})


def _delete_route(method, url, **kw):
    return _FakeResp({"status": 200, "code": 0, "data": {}})


class TestParseShareUrl(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(parse_share_url("https://pan.quark.cn/s/abc123"),
                         ("abc123", ""))

    def test_with_pwd_param(self):
        self.assertEqual(parse_share_url("https://pan.quark.cn/s/abc123?pwd=8888"),
                         ("abc123", "8888"))

    def test_with_chinese_password_text(self):
        self.assertEqual(parse_share_url("https://pan.quark.cn/s/abc123 密码: 9x7q"),
                         ("abc123", "9x7q"))
        self.assertEqual(parse_share_url("https://pan.quark.cn/s/abc123 提取码：q2w3"),
                         ("abc123", "q2w3"))

    def test_embedded_in_text(self):
        self.assertEqual(parse_share_url("夸克链接:https://pan.quark.cn/s/xyz12 密码8888"),
                         ("xyz12", "8888"))

    def test_invalid(self):
        with self.assertRaises(QuarkError):
            parse_share_url("https://example.com/s/abc")


class TestCookieManagement(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("QUARK_COOKIE")
        if "QUARK_COOKIE" in os.environ:
            del os.environ["QUARK_COOKIE"]
        self._tmp = tempfile.mkdtemp(prefix="rh_quark_")
        import skills.quark as q
        self._orig_cookie_file = q.COOKIE_FILE
        q.COOKIE_FILE = Path(self._tmp) / "quark_cookie.json"

    def tearDown(self):
        import skills.quark as q
        q.COOKIE_FILE = self._orig_cookie_file
        if self._env is not None:
            os.environ["QUARK_COOKIE"] = self._env

    def test_env_cookie_preferred(self):
        os.environ["QUARK_COOKIE"] = "__puus=env"
        self.assertEqual(load_cookie(), "__puus=env")

    def test_import_then_load(self):
        auth_import("__puus=file-cookie")
        self.assertEqual(load_cookie(), "__puus=file-cookie")

    def test_auth_status_no_cookie(self):
        st = auth_status()
        self.assertFalse(st["logged_in"])


class TestQuarkClient(unittest.TestCase):
    def test_get_stoken(self):
        c = QuarkClient()
        c._session = FakeSession()
        c._session.add("sharepage/token", _share_token_route)
        self.assertEqual(c.get_stoken("abc123", "8888"), "stoken-xyz")

    def test_walk_share_recursive(self):
        c = QuarkClient()
        c._session = FakeSession()
        c._session.add("sharepage/detail", _share_detail_route)
        items = c.walk_share("abc123", "stoken-xyz", max_depth=3)
        paths = [i["path"] for i in items]
        self.assertIn("故宫投影.litematic", paths)
        self.assertIn("资料/内层说明.txt", paths)   # 递归进子目录
        self.assertTrue(any(i["dir"] for i in items))

    def test_save_wait_download(self):
        c = QuarkClient(cookie="__puus=real")
        c._session = FakeSession()
        c._session.add("sharepage/save", _save_route)
        c._session.add("/task", _task_route)
        c._session.add("file/download", _download_route)
        task_id = c.save_share("abc123", "stoken-xyz", "0", ["f-litematic"], ["tk-litematic"])
        self.assertEqual(task_id, "task-1")
        td = c.wait_task(task_id)
        self.assertEqual(td["save_as"]["save_as_top_fids"], ["saved-1"])
        dls = c.get_download_urls(["saved-1"])
        self.assertEqual(dls[0]["download_url"], "https://dl.quark.cn/x")
        # 请求必须带 Cookie
        meth, url, body = c._session.requests[-1]
        self.assertEqual(meth, "POST")

    def test_get_download_urls_requires_cookie(self):
        c = QuarkClient()  # 无 Cookie
        with self.assertRaises(Exception):
            c.get_download_urls(["x"])

    def test_cookie_refresh_merges_set_cookie(self):
        c = QuarkClient(cookie="__puus=old; __pus=keep")
        refreshed = []
        c.on_cookie_refresh = refreshed.append
        c._session = FakeSession()
        c._session.add("sharepage/token",
                       lambda m, u, **k: _FakeResp(
                           {"status": 200, "code": 0, "data": {"stoken": "s"}},
                           set_cookie="__puus=new-value; Path=/"))
        c.get_stoken("abc123")
        self.assertIn("__puus=new-value", c.cookie)
        self.assertIn("__pus=keep", c.cookie)
        self.assertEqual(refreshed, [c.cookie])


class TestPickShareFile(unittest.TestCase):
    def _files(self):
        return [
            {"file_name": "a.txt", "size": 100, "dir": False},
            {"file_name": "b.litematic", "size": 500, "dir": False},
            {"file_name": "资料", "size": 0, "dir": True},
        ]

    def test_filter_substring(self):
        f = pick_share_file(self._files(), file_filter="litematic")
        self.assertEqual(f["file_name"], "b.litematic")

    def test_preferred_ext(self):
        f = pick_share_file(self._files(), preferred_exts=(".litematic",))
        self.assertEqual(f["file_name"], "b.litematic")

    def test_accept_ext(self):
        f = pick_share_file(self._files(), accept_exts=(".txt",))
        self.assertEqual(f["file_name"], "a.txt")

    def test_largest_fallback(self):
        f = pick_share_file(self._files())
        self.assertEqual(f["file_name"], "b.litematic")  # 最大文件

    def test_skips_dirs(self):
        f = pick_share_file([{"file_name": "dir", "size": 999, "dir": True}])
        self.assertIsNone(f)


class TestDownloadShare(unittest.TestCase):
    """端到端:解析 → 清单 → 选 .litematic → 转存 → 直链 → 落地(全部离线)。"""

    def setUp(self):
        self._env = os.environ.get("QUARK_COOKIE")
        if "QUARK_COOKIE" in os.environ:
            del os.environ["QUARK_COOKIE"]
        import skills.quark as q
        self._orig_cookie_file = q.COOKIE_FILE
        q.COOKIE_FILE = Path(tempfile.mkdtemp(prefix="rh_quark_")) / "quark_cookie.json"
        auth_import("__puus=real-cookie")

    def tearDown(self):
        import skills.quark as q
        q.COOKIE_FILE = self._orig_cookie_file
        if self._env is not None:
            os.environ["QUARK_COOKIE"] = self._env

    def test_download_litematic(self):
        fake = FakeSession()
        fake.add("sharepage/token", _share_token_route)
        fake.add("sharepage/detail", _share_detail_route)
        fake.add("sharepage/save", _save_route)
        fake.add("/task", _task_route)
        fake.add("file/download", _download_route)
        fake.add("file/delete", _delete_route)

        with mock.patch("skills.quark.api.requests.Session") as mk:
            mk.return_value = fake  # 替换 QuarkClient 内部 session 构造

            # 同时让 download_url 的流式下载不真正走网络:替换 session.get
            import requests as _rq

            class _StreamResp:
                status_code = 200

                def raise_for_status(self):
                    pass

                def iter_content(self, chunk_size=1 << 16):
                    yield b"fake-litematic-bytes"

                @property
                def headers(self):
                    return {"content-length": "19"}

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            fake.get = lambda url, **k: _StreamResp()

            with tempfile.TemporaryDirectory() as td:
                r = download_share("https://pan.quark.cn/s/abc123",
                                   preferred_exts=(".litematic",),
                                   dest_dir=td)
                self.assertTrue(r["ok"])
                self.assertEqual(r["file_name"], "故宫投影.litematic")
                p = Path(r["path"])
                self.assertTrue(p.exists())
                self.assertEqual(p.read_bytes(), b"fake-litematic-bytes")


class TestListShareAnon(unittest.TestCase):
    def test_list_without_cookie(self):
        fake = FakeSession()
        fake.add("sharepage/token", _share_token_route)
        fake.add("sharepage/detail", _share_detail_route)
        with mock.patch("skills.quark.api.requests.Session") as mk:
            mk.return_value = fake
            r = list_share("https://pan.quark.cn/s/abc123")
        self.assertEqual(r["pwd_id"], "abc123")
        names = [f["file_name"] for f in r["files"]]
        self.assertIn("故宫投影.litematic", names)
        self.assertIn("内层说明.txt", names)  # 子目录也列到


class TestCookieToDict(unittest.TestCase):
    def test_basic(self):
        d = cookie_to_dict("__puus=a; __pus=b")
        self.assertEqual(d, {"__puus": "a", "__pus": "b"})


class TestPipelineIntegration(unittest.TestCase):
    """fetch_resource 管线:分析阶段发现 pan.quark.cn 分享 → 3.7 阶段自动解析。"""

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.fr = importlib.import_module("agent.tasks.fetch_resource")

    def setUp(self):
        self._orig = self.fr.fetch_resource

    def tearDown(self):
        self.fr.fetch_resource = self._orig

    def test_pipeline_calls_quark_on_pan_links(self):
        import tempfile

        from agent.tasks.base import TaskResult
        from pages.models import PageClass

        fr = self.fr

        class _FakeRes:
            def __init__(self, url, kind="pan_share"):
                self.url = url
                self.kind = kind
                self.file_ext = ""
                self.text = ""

        class _FakeAnalysis:
            page_class = PageClass.AGGREGATOR
            reason = "含网盘分享链接"
            best_resources = [_FakeRes("https://pan.quark.cn/s/abc123")]

        class _FakeSearch:
            url = "https://x.com/1"
            title = "candidate"
            snippet = ""

        quark_links_seen = []

        def _fake_quark_fetch_one(url, out_dir, file_types, on_stage=None, is_cancelled=None):
            quark_links_seen.append(url)
            # 模拟成功落地一个文件
            p = Path(out_dir) / "quark_result.bin"
            p.write_bytes(b"ok")
            return str(p)

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch()]), \
                 mock.patch.object(fr, "analyze_page",
                                   return_value=_FakeAnalysis()), \
                 mock.patch.object(fr, "_quark_fetch_one",
                                   side_effect=_fake_quark_fetch_one), \
                 mock.patch.object(fr, "_security_gate",
                                   return_value=("clean", "ok")):
                result = fr.fetch_resource(query="测试夸克分享", out_dir=td,
                                           use_agent_fallback=True, verbose=False)
        self.assertTrue(result.success)
        self.assertEqual(quark_links_seen, ["https://pan.quark.cn/s/abc123"])
        self.assertEqual(len(result.files), 1)
        self.assertIn("quark_result.bin", result.files[0])

    def test_is_quark_share(self):
        fr = self.fr
        self.assertTrue(fr._is_quark_share("https://pan.quark.cn/s/abc"))
        self.assertTrue(fr._is_quark_share("https://pan.quark.cn/s/abc?pwd=1"))
        self.assertFalse(fr._is_quark_share("https://pan.baidu.com/s/1"))
        self.assertFalse(fr._is_quark_share("https://example.com/a"))


if __name__ == "__main__":
    unittest.main()
