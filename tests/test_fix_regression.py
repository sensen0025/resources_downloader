"""修复回归测试:决策 JSON 容错解析 + 连续缺参熔断(离线)。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.agent import _parse_decision  # noqa: E402


class TestParseDecisionRepair(unittest.TestCase):
    """线上事故:LLM 输出截断/尾文本/单引号导致决策解析失败,每次白烧一轮 LLM。"""

    def test_normal_json(self):
        d = _parse_decision('{"thought":"a","action":"goto","value":"https://x.com"}')
        self.assertEqual(d["action"], "goto")

    def test_truncated_missing_brace(self):
        # 线上日志:输出在 "index":0 处截断(缺右括号)
        d = _parse_decision('{"thought":"页面未直接暴露直链","action":"sandbox_write","index":0')
        self.assertEqual(d["action"], "sandbox_write")

    def test_truncated_dangling_string_value(self):
        d = _parse_decision('{"thought":"a","action":"sandbox_write","value":"abc')
        self.assertEqual(d["action"], "sandbox_write")

    def test_trailing_text_after_json(self):
        d = _parse_decision('{"thought":"a","action":"wait","args":{"ms":2000}} 好的,继续等待')
        self.assertEqual(d["action"], "wait")
        self.assertEqual(d["args"]["ms"], 2000)

    def test_code_fence(self):
        d = _parse_decision('```json\n{"thought":"a","action":"done","args":{"success":true}}\n```')
        self.assertTrue(d["args"]["success"])

    def test_single_quote_python_style(self):
        d = _parse_decision("{'thought': 'a', 'action': 'done', 'args': {'success': False}}")
        self.assertFalse(d["args"]["success"])

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            _parse_decision("完全不是 JSON 的输出")


class TestSkinQueryTypeInference(unittest.TestCase):
    """线上事故:『我的世界 银狼lv999 皮肤 下载』被"下载"关键词带偏成软件类型,
    导致下到 FLiNG 修改器 exe。皮肤/skin 必须识别为图片类型,且"下载"不再暗示软件。"""

    def _infer(self, q):
        from agent.tasks.fetch_resource import _infer_file_types

        return _infer_file_types(q, None)

    def test_skin_query_image_type(self):
        exts = self._infer("我的世界 银狼lv999 皮肤 下载")
        self.assertIn(".png", exts)
        self.assertIn(".jpg", exts)
        self.assertNotIn(".exe", exts)
        self.assertNotIn(".zip", exts)

    def test_skin_alone_image(self):
        exts = self._infer("我的世界 皮肤")
        self.assertIn(".png", exts)
        self.assertNotIn(".exe", exts)

    def test_software_explicit_still_software(self):
        exts = self._infer("鬼谷八荒 修改器 下载 exe")
        self.assertIn(".exe", exts)

    def test_image_content_gate(self):
        from agent.tasks.fetch_resource import _is_image_file

        import tempfile

        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "trainer.exe"
            exe.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00\xb8")
            self.assertFalse(_is_image_file(exe))
            png = Path(td) / "skin.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32)
            self.assertTrue(_is_image_file(png))


class TestBilibiliVideoMode(unittest.TestCase):
    """线上事故:bilibili_fetch 只下音频 m4a,视频任务"无文件落地"。"""

    def test_prefer_detection_from_task_file_types(self):
        from skills.core import get_registry

        spec = get_registry().get("bilibili_fetch")
        self.assertIsNotNone(spec)
        props = spec.parameters["properties"]
        self.assertIn("prefer", props)          # 新增 prefer 参数
        self.assertEqual(props["prefer"]["enum"], ["auto", "video", "audio"])
        # auto 默认
        self.assertNotIn("prefer", spec.parameters.get("required", []))

    def test_bilibili_download_media_audio_mode(self):
        from unittest import mock

        import tempfile

        from skills.bilibili import bilibili_download_media

        playinfo = {"data": {"dash": {"audio": [
            {"id": 30280, "baseUrl": "https://a.m4s", "bandwidth": 192000, "codecs": "mp4a.40.2"}]}}}

        class _Resp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_content(self, n):
                yield b"ID3" + b"\x00" * 64

        class _FakePage:
            url = "https://www.bilibili.com/video/BV1"

            def evaluate(self, js):
                return playinfo

            def content(self):
                return ""

            def goto(self, url, **kw):
                self.url = url

            def wait_for_timeout(self, ms):
                pass

        class _FakeSession:
            def __init__(self):
                self.page = _FakePage()
                self.context = SimpleNamespace(cookies=lambda: [])

        with tempfile.TemporaryDirectory() as td:
            with mock.patch("skills.bilibili.requests.get", return_value=_Resp()):
                r = bilibili_download_media("https://www.bilibili.com/video/BV1",
                                            td, session=_FakeSession(), mode="audio")
            self.assertTrue(r["ok"])
            self.assertEqual(r["mode"], "audio")


class TestSkinNameMatchAndLittleskin(unittest.TestCase):
    """皮肤任务验收:页面标题名字匹配 + littleskin 皮肤页直链提取。

    线上事故:littleskin 皮肤「星穹铁道 银狼 LV.999」找不到 —— 搜索引擨不收录 JS
    站点,且像素级视觉校验对 64x64 皮肤不可靠;正确信号是页面名 + /raw/{tid} 直链。
    """

    def test_name_match_normalized(self):
        from agent.tasks.fetch_resource import _skin_name_match

        q = "银狼lv999 皮肤 我的世界"
        self.assertTrue(_skin_name_match("星穹铁道 银狼 LV.999 - LittleSkin", q))
        self.assertFalse(_skin_name_match("Level999Villager | Minecraft Skin", q))
        self.assertFalse(_skin_name_match("", q))

    def test_littleskin_show_page_yields_raw_direct_file(self):
        from pages.extractor import extract_resources

        html = "<html><head><title>星穹铁道 银狼 LV.999 - LittleSkin</title></head><body></body></html>"
        res = extract_resources(html, "https://littleskin.cn/skinlib/show/810649")
        raws = [r for r in res if r.kind == "direct_file" and "littleskin.cn/raw/" in r.url]
        self.assertTrue(any("810649" in r.url for r in raws), [r.url for r in raws])


class TestListIntent(unittest.TestCase):
    """列表意图检测:合集/全集/歌单/第N个 等触发列表模式(预算放大 + goal 列表指令)。"""

    def _is_list(self, q, fts):
        from agent.tasks.fetch_resource import _is_list_intent

        return _is_list_intent(q, fts)

    def test_hits(self):
        vid = (".mp4", ".mkv")
        aud = (".mp3", ".flac")
        self.assertTrue(self._is_list("凡人修仙传 动漫 合集", vid))
        self.assertTrue(self._is_list("xxx 合集 第4集", vid))
        self.assertTrue(self._is_list("xxx 第4个", vid))
        self.assertTrue(self._is_list("轻音乐 歌单", aud))
        self.assertTrue(self._is_list("收藏夹 全部", vid))

    def test_episode_selection_is_list_aware(self):
        # 第X集 也应触发列表意识:Agent 在剧集页要选对集数而非抓第一个
        self.assertTrue(self._is_list("凡人修仙传 第10集 在线观看", (".mp4", ".mkv")))

    def test_misses(self):
        self.assertFalse(self._is_list("某书 合集", (".txt", ".pdf")))   # 非音视频
        self.assertFalse(self._is_list("壁纸 合集", (".jpg", ".png")))   # 非音视频
        self.assertFalse(self._is_list("凡人修仙传 壁纸 4k", (".jpg", ".png")))
        self.assertFalse(self._is_list("", (".mp4",)))

    def test_agent_fetch_list_budget_and_goal(self):
        """列表意图 → max_steps=120 / max_seconds=900 / goal 含列表指令。"""
        from unittest import mock

        import importlib

        # agent/tasks/__init__.py 把 fetch_resource 属性重绑成了函数,必须 import_module 拿真实模块
        fr = importlib.import_module("agent.tasks.fetch_resource")

        calls: dict = {}

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def goto(self, *a, **k):
                pass

        class FakeAgent:
            def __init__(self, **kw):
                calls.update(kw)

            def run(self):
                return SimpleNamespace(success=True, final_title="")

        with mock.patch("agent.browser.BrowserSession", return_value=FakeSession()), \
             mock.patch("agent.AccountAgent", FakeAgent), \
             mock.patch("agent.llm.LLMClient"), \
             mock.patch("agent.cookies.find_cookies", return_value=None), \
             mock.patch("agent.cookies.has_cookies", return_value=False):
            fr._agent_fetch("https://bilibili.com/video/BV1", (".mp4",),
                            Path("_tmp_out"), query="xxx 合集 全部")
        self.assertEqual(calls["max_steps"], 120)
        self.assertEqual(calls["max_seconds"], 900.0)
        self.assertIn("列表", calls["goal"])
        self.assertIn("已下载 X/N", calls["goal"])


if __name__ == "__main__":
    unittest.main()
