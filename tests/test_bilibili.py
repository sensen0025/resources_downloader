"""B站技能单元测试(离线):搜索/playinfo 提取/流选择/下载/工具注册。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from skills.bilibili import (
    best_audio_stream,
    best_video_stream,
    bilibili_download,
    extract_playinfo,
    is_bilibili_url,
    search_videos,
)

_PLAYINFO = {
    "code": 0,
    "data": {
        "dash": {
            "duration": 163,
            "audio": [
                {"id": 30216, "baseUrl": "https://cn-a.m4s?from=1",
                 "bandwidth": 65536, "codecs": "mp4a.40.2"},
                {"id": 30280, "baseUrl": "https://cn-b.m4s?from=2",
                 "bandwidth": 192000, "codecs": "mp4a.40.2"},
                {"id": 30232, "baseUrl": "https://cn-c.m4s?from=3",
                 "bandwidth": 131072, "codecs": "mp4a.40.2"},
            ],
        }
    },
}

# 番剧(bangumi)页 playinfo:pgc API 结构,根键是 result 而不是 data ——
# 线上事故:『凡人修仙传 第10集』→ bangumi/play/ep733325,旧代码只读 data.dash
# 导致音视频流全空、第10集瞬间失败。两个根都必须认。
_BANGUMI_PLAYINFO = {
    "code": 0,
    "result": {
        "is_preview": 0,
        "dash": {
            "duration": 1440,
            "video": [
                {"id": 120, "baseUrl": "https://cn-v1.m4s", "bandwidth": 9580163,
                 "codecs": "avc1.640033"},
                {"id": 112, "baseUrl": "https://cn-v2.m4s", "bandwidth": 3901891,
                 "codecs": "avc1.640032"},
            ],
            "audio": [
                {"id": 30280, "baseUrl": "https://cn-a1.m4s", "bandwidth": 192000,
                 "codecs": "mp4a.40.2"},
            ],
        },
    },
}


class _FakePage:
    def __init__(self, playinfo=None, url="https://www.bilibili.com/video/BV1"):
        self._pi = playinfo
        self.url = url
        self.goto_calls = []
        self.wait_calls = []

    def evaluate(self, js):
        return self._pi

    def content(self):
        return ""

    def goto(self, url, wait_until="domcontentloaded", timeout=45000):
        self.goto_calls.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        self.wait_calls.append(ms)


class _FakeSession:
    def __init__(self, playinfo=None):
        self.page = _FakePage(playinfo)
        self.context = SimpleNamespace(
            cookies=lambda: [{"name": "SESSDATA", "value": "sess"}])


class TestBasics(unittest.TestCase):
    def test_is_bilibili_url(self):
        self.assertTrue(is_bilibili_url("https://www.bilibili.com/video/BV1h3a4eAEp1"))
        self.assertTrue(is_bilibili_url("https://bilibili.com/video/BV1"))
        self.assertFalse(is_bilibili_url("https://kugou.com/song/1"))

    def test_search_videos_parses(self):
        payload = {"data": {"result": [
            {"bvid": "BV1AAA", "title": "<em class=\"keyword\">大爱炼天</em> 同人歌曲",
             "author": "up主"},
            {"bvid": "BV1BBB", "title": "另一首", "author": "u2"},
        ]}}
        with mock.patch("skills.bilibili.requests.get") as g:
            g.return_value.status_code = 200
            g.return_value.json.return_value = payload
            hits = search_videos("大爱炼天", limit=4)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["url"], "https://www.bilibili.com/video/BV1AAA")
        self.assertNotIn("<em", hits[0]["title"])       # HTML 标签已剥离
        self.assertIn("大爱炼天", hits[0]["title"])

    def test_best_audio_stream_picks_highest_bandwidth(self):
        s = best_audio_stream(_PLAYINFO)
        self.assertEqual(s["id"], 30280)                 # 192k > 128k > 64k
        self.assertIsNone(best_audio_stream({}))

    def test_bangumi_playinfo_result_root(self):
        """番剧页 playinfo 根键是 result:视频/音频流必须能从 result.dash 取到。"""
        v = best_video_stream(_BANGUMI_PLAYINFO)
        self.assertIsNotNone(v)
        self.assertEqual(v["id"], 120)                   # 按码率取最佳
        a = best_audio_stream(_BANGUMI_PLAYINFO)
        self.assertIsNotNone(a)
        self.assertEqual(a["id"], 30280)

    def test_bangumi_download_media_video(self):
        """bilibili_download_media 在 result 根结构下能完成视频流+音频流下载合并。"""
        from skills.bilibili import bilibili_download_media

        with tempfile.TemporaryDirectory() as td:
            session = _FakeSession(_BANGUMI_PLAYINFO)

            class _Resp:
                status_code = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def iter_content(self, n):
                    yield b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128   # fMP4 魔数

            merged = Path(td) / "merged.mp4"
            merged.write_bytes(b"\x00" * 256)

            def _fake_merge(v, a, dest, **kw):
                # 与真实合并一致:合并产物落盘 dest
                Path(dest).write_bytes(merged.read_bytes())
                return True, str(dest)

            with mock.patch("skills.bilibili.requests.get", return_value=_Resp()), \
                 mock.patch("skills.bilibili.merge_av_ffmpeg", side_effect=_fake_merge):
                r = bilibili_download_media("https://www.bilibili.com/bangumi/play/ep733325",
                                            td, session=session)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertEqual(r["mode"], "video")
            self.assertIn("merged", str(r["path"]))

    def test_bangumi_preview_note(self):
        """大会员试看集(is_preview=1):结果带诚实标注,不冒充完整正片。"""
        import copy

        from skills.bilibili import bilibili_download_media

        pi = copy.deepcopy(_BANGUMI_PLAYINFO)
        pi["result"]["is_preview"] = 1
        with tempfile.TemporaryDirectory() as td:
            session = _FakeSession(pi)

            class _Resp:
                status_code = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def iter_content(self, n):
                    yield b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128

            merged = Path(td) / "merged.mp4"
            merged.write_bytes(b"\x00" * 256)

            def _fake_merge(v, a, dest, **kw):
                Path(dest).write_bytes(merged.read_bytes())
                return True, str(dest)

            with mock.patch("skills.bilibili.requests.get", return_value=_Resp()), \
                 mock.patch("skills.bilibili.merge_av_ffmpeg", side_effect=_fake_merge):
                r = bilibili_download_media("https://www.bilibili.com/bangumi/play/ep733325",
                                            td, session=session)
            self.assertTrue(r["ok"], r.get("error"))
            self.assertIn("试看", r.get("note", ""))


class TestPlayinfo(unittest.TestCase):
    def test_extract_from_dom_global(self):
        page = _FakePage(_PLAYINFO)
        self.assertEqual(extract_playinfo(page)["code"], 0)


class TestDownload(unittest.TestCase):
    def test_download_ok(self):
        with tempfile.TemporaryDirectory() as td:
            session = _FakeSession(_PLAYINFO)

            class _Resp:
                status_code = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def iter_content(self, n):
                    yield b"ID3fakeaudio" + b"\x00" * 64

            with mock.patch("skills.bilibili.requests.get", return_value=_Resp()):
                r = bilibili_download("https://www.bilibili.com/video/BV1",
                                      td, session=session)
            self.assertTrue(r["ok"])
            self.assertTrue(Path(r["path"]).exists())
            self.assertGreater(r["size"], 0)
            self.assertEqual(r["codecs"], "mp4a.40.2")
            # 已用浏览器会话打开页面(无多余 goto,URL 已匹配)
            self.assertEqual(session.page.goto_calls, [])

    def test_download_requires_session(self):
        with tempfile.TemporaryDirectory() as td:
            r = bilibili_download("https://www.bilibili.com/video/BV1", td, session=None)
        self.assertFalse(r["ok"])
        self.assertIn("浏览器会话", r["error"])


class TestToolRegistered(unittest.TestCase):
    def test_bilibili_fetch_registered(self):
        import importlib

        importlib.import_module("skills.bilibili")
        from skills.core import get_registry

        reg = get_registry()
        self.assertTrue(reg.has("bilibili_fetch"))
        # 工具在任务上下文里落盘到任务目录
        spec = reg.get("bilibili_fetch")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.category, "execute")


if __name__ == "__main__":
    unittest.main()
