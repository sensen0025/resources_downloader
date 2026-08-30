"""万能下载技能(skills/universal)单元测试。

本地 ThreadingHTTPServer:播放页(内嵌 m3u8 的 JS)+ m3u8 分段 + CF 挑战页,不依赖外网。
browser_cookies 需要 playwright,跳过(逻辑层用 is_cf_challenge 单测覆盖)。
"""

import hashlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from skills.universal import (
    download_page_stream,
    is_cf_challenge,
    is_page_url,
    resolve_stream,
    universal_download,
)
from skills.universal.cf import CF_MARKERS

_SEED = b"universal-test-" * 1024
SEG0 = _SEED[:4096]
SEG1 = _SEED[4096:8192]
SEG2 = _SEED[8192:12288]
JOIN_SHA = hashlib.sha256(SEG0 + SEG1 + SEG2).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/play.html":
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            body = (
                "<html><head><title>凡人修仙传 第10集</title></head><body>"
                f"<script>var player={{url:'{base}/stream.m3u8'}};</script>"
                f"<video src='{base}/stream.m3u8'></video>"
                "<a href='https://ads.example.com/banner'>广告</a>"
                "</body></html>"
            ).encode()
            self._ok(body, "text/html; charset=utf-8")
        elif self.path == "/escaped.html":
            body = (b'<script>var u = "https:\\/\\/cdn.example.com\\/s\\/v.m3u8?token=1";</script>')
            self._ok(body, "text/html")
        elif self.path == "/stream.m3u8":
            body = ("#EXTM3U\n#EXTINF:10.0,\nseg0.ts\n#EXTINF:10.0,\nseg1.ts\n"
                    "#EXTINF:10.0,\nseg2.ts\n#EXT-X-ENDLIST\n").encode()
            self._ok(body, "application/vnd.apple.mpegurl")
        elif self.path == "/cf.html":
            body = b"<html><title>Just a moment...</title>Checking your browser</html>"
            self._ok(body, "text/html")
        elif self.path.startswith("/seg"):
            data = {"seg0.ts": SEG0, "seg1.ts": SEG1, "seg2.ts": SEG2}.get(
                self.path.lstrip("/"))
            if data is None:
                self.send_response(404)
                self.end_headers()
            else:
                self._ok(data, "video/mp2t")
        else:
            self.send_response(404)
            self.end_headers()


class _Server:
    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self._httpd.shutdown()


_srv = _Server()


def tearDownModule():
    _srv.close()


class TestCfDetection(unittest.TestCase):
    def test_markers(self):
        self.assertTrue(is_cf_challenge("<title>Just a moment...</title>"))
        self.assertTrue(is_cf_challenge("cf-challenge checking your browser"))
        self.assertTrue(is_cf_challenge("安全检查 请稍候"))
        self.assertFalse(is_cf_challenge("凡人修仙传 第10集 在线观看"))
        self.assertFalse(is_cf_challenge(""))

    def test_marker_list_nonempty(self):
        self.assertGreater(len(CF_MARKERS), 5)


class TestResolveStream(unittest.TestCase):
    def test_js_and_video_source(self):
        streams = resolve_stream(_srv.url("/play.html"))
        self.assertTrue(any("stream.m3u8" in s for s in streams))
        # 广告链接不进流候选
        self.assertFalse(any("ads.example.com" in s for s in streams))

    def test_escaped_js_url(self):
        streams = resolve_stream(_srv.url("/escaped.html"))
        self.assertTrue(any("v.m3u8" in s for s in streams))
        self.assertTrue(any("https://" in s for s in streams))

    def test_empty_page(self):
        self.assertEqual(resolve_stream("http://127.0.0.1:1/nope"), [])


class TestUniversalDownload(unittest.TestCase):
    def test_download_play_page_stream(self):
        """播放页 → 自动找 m3u8 → 切片合并落地。"""
        with tempfile.TemporaryDirectory() as td:
            r = universal_download(_srv.url("/play.html"), td, use_browser=False)
            self.assertTrue(r.ok, r.error)
            path = Path(r.path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), JOIN_SHA)
            self.assertEqual(path.stat().st_size, len(SEG0 + SEG1 + SEG2))

    def test_download_direct_m3u8(self):
        with tempfile.TemporaryDirectory() as td:
            r = universal_download(_srv.url("/stream.m3u8"), td, use_browser=False)
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.strategy, "hls")
            self.assertEqual(hashlib.sha256(Path(r.path).read_bytes()).hexdigest(), JOIN_SHA)

    def test_download_page_stream_helper(self):
        with tempfile.TemporaryDirectory() as td:
            r = download_page_stream(_srv.url("/play.html"), td)
            self.assertTrue(r.ok, r.error)

    def test_is_page_url(self):
        self.assertTrue(is_page_url("http://x/play.html"))
        self.assertTrue(is_page_url("http://x/video/12345/"))
        self.assertFalse(is_page_url("http://x/video.mp4"))
        self.assertFalse(is_page_url("http://x/stream.m3u8"))


class TestToolRegistration(unittest.TestCase):
    def test_universal_download_registered(self):
        import ai.skills  # noqa: F401
        from skills.core import get_registry

        self.assertTrue(get_registry().has("universal_download"))

    def test_tool_invoke_local_play_page(self):
        import ai.skills  # noqa: F401
        from skills.core import get_registry

        with tempfile.TemporaryDirectory() as td:
            r = get_registry().invoke(
                "universal_download",
                {"url": _srv.url("/play.html"), "dest_dir": td})
            self.assertTrue(r.ok, r.message)
            self.assertEqual(r.data["strategy"], "hls")


if __name__ == "__main__":
    unittest.main()
