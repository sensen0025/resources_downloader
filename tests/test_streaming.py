"""流式下载技能(skills/streaming)单元测试。

本地起 ThreadingHTTPServer 提供大文件 / m3u8 分段 / AES-128 加密分段,不依赖外网。
覆盖:probe 字段、Range 分片并发下载完整性(sha256)、分片级断点续传、
单流限速、进度回调、HLS 明文/AES 合并、fmp4 拒绝、工具注册。
"""

import base64
import hashlib
import io
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from skills.streaming import (
    StreamResult,
    parse_m3u8,
    probe_stream,
    stream_download,
)

_SEED = b"streaming-skill-test-data-" * 4096   # ~110KB 种子块


def _make_data(mb: int) -> bytes:
    h = hashlib.sha256(_SEED).digest()
    out = bytearray()
    while len(out) < mb << 20:
        out += h
        h = hashlib.sha256(h + _SEED[:16]).digest()
    return bytes(out[: mb << 20])


BIG = _make_data(3)  # 3MB(≥分段阈值 5MB 会走单流,这里显式 segments 测分片)
BIG_SHA = hashlib.sha256(BIG).hexdigest()

SEG0 = b"seg0-" + _SEED[:4096]
SEG1 = b"seg1-" + _SEED[:4096]
SEG2 = b"seg2-" + _SEED[:4096]
HLS_JOIN_SHA = hashlib.sha256(SEG0 + SEG1 + SEG2).hexdigest()

AES_KEY = b"0123456789abcdef"  # 16 字节
AES_IV = bytes.fromhex("00000000000000000000000000000000")


def _aes_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    pad = 16 - (len(data) % 16)
    data = data + bytes([pad]) * pad
    c = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return c.update(data) + c.finalize()


try:  # cryptography 可选:没有时跳过 AES 用例,不影响其余测试
    ENC0 = _aes_encrypt(SEG0, AES_KEY, AES_IV)
    ENC1 = _aes_encrypt(SEG1, AES_KEY, AES_IV)
    CRYPTO_OK = True
except ImportError:
    ENC0 = ENC1 = b""
    CRYPTO_OK = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _serve(self) -> tuple[int, dict, bytes]:
        """路由:返回 (status, headers, body);GET/HEAD 共用。"""
        if self.path in ("/big.bin", "/big.mp4"):
            data = BIG
            return 200, {"Content-Type": "application/octet-stream",
                         "Accept-Ranges": "bytes", "Content-Length": str(len(data))}, data
        if self.path.startswith("/norange.bin"):
            return 200, {"Content-Type": "application/octet-stream",
                         "Content-Length": str(len(BIG))}, BIG
        if self.path == "/index.m3u8":
            body = ("#EXTM3U\n#EXT-X-VERSION:3\n"
                    "#EXTINF:10.0,\nseg0.ts\n#EXTINF:10.0,\nseg1.ts\n"
                    "#EXTINF:10.0,\nseg2.ts\n#EXT-X-ENDLIST\n").encode()
            return 200, {"Content-Type": "application/vnd.apple.mpegurl",
                         "Content-Length": str(len(body))}, body
        if self.path == "/master.m3u8":
            body = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000,RESOLUTION=640x360\n"
                    "low/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=512000,RESOLUTION=1280x720\n"
                    "high/index.m3u8\n").encode()
            return 200, {"Content-Type": "application/vnd.apple.mpegurl",
                         "Content-Length": str(len(body))}, body
        if self.path == "/high/index.m3u8":
            body = ("#EXTM3U\n#EXTINF:10.0,\n../seg0.ts\n#EXTINF:10.0,\n../seg1.ts\n"
                    "#EXTINF:10.0,\n../seg2.ts\n#EXT-X-ENDLIST\n").encode()
            return 200, {"Content-Type": "application/vnd.apple.mpegurl",
                         "Content-Length": str(len(body))}, body
        if self.path == "/enc/index.m3u8":
            body = (f"#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"/enc/key.bin\",IV=0x{AES_IV.hex()}\n"
                    "#EXTINF:10.0,\nseg0.ts\n#EXTINF:10.0,\nseg1.ts\n#EXT-X-ENDLIST\n").encode()
            return 200, {"Content-Type": "application/vnd.apple.mpegurl",
                         "Content-Length": str(len(body))}, body
        if self.path == "/fmp4/index.m3u8":
            body = ("#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n"
                    "#EXTINF:10.0,\nseg0.m4s\n#EXT-X-ENDLIST\n").encode()
            return 200, {"Content-Type": "application/vnd.apple.mpegurl",
                         "Content-Length": str(len(body))}, body
        segs = {"/seg0.ts": SEG0, "/seg1.ts": SEG1, "/seg2.ts": SEG2,
                "/enc/seg0.ts": ENC0, "/enc/seg1.ts": ENC1, "/enc/key.bin": AES_KEY}
        if self.path in segs:
            data = segs[self.path]
            return 200, {"Content-Type": "application/octet-stream",
                         "Accept-Ranges": "bytes", "Content-Length": str(len(data))}, data
        return 404, {"Content-Type": "text/plain"}, b"not found"

    def _send(self, status: int, headers: dict, body: bytes, head_only: bool):
        rng = self.headers.get("Range", "")
        if rng and status == 200 and "Accept-Ranges" in headers and not head_only:
            m = rng.split("=")[1].split("-")
            start = int(m[0])
            end = int(m[1]) if m[1] else len(body) - 1
            end = min(end, len(body) - 1)
            body = body[start:end + 1]
            status = 206
            headers = dict(headers)
            headers["Content-Range"] = f"bytes {start}-{end}/{len(BIG) if '/big' in self.path else 0}"
            headers["Content-Length"] = str(len(body))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only and body:
            self.wfile.write(body)

    def do_HEAD(self):
        status, headers, body = self._serve()
        self._send(status, headers, body, head_only=True)

    def do_GET(self):
        status, headers, body = self._serve()
        self._send(status, headers, body, head_only=False)


class _Server:
    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._t.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self._httpd.shutdown()


_srv = _Server()


def tearDownModule():
    _srv.close()


class TestProbe(unittest.TestCase):
    def test_direct_probe(self):
        p = probe_stream(_srv.url("/big.mp4"))
        self.assertTrue(p.ok)
        self.assertEqual(p.content_length, len(BIG))
        self.assertTrue(p.accept_ranges)
        self.assertFalse(p.is_hls)
        self.assertTrue(p.is_media)  # .mp4 扩展名命中媒体

    def test_unknown_ext_not_media(self):
        p = probe_stream(_srv.url("/big.bin"))
        self.assertTrue(p.ok)
        self.assertFalse(p.is_media)  # octet-stream + .bin → 不按媒体处理

    def test_hls_probe(self):
        p = probe_stream(_srv.url("/index.m3u8"))
        self.assertTrue(p.ok)
        self.assertTrue(p.is_hls)

    def test_missing(self):
        p = probe_stream(_srv.url("/nope.bin"))
        self.assertFalse(p.ok)


class TestDirectSegments(unittest.TestCase):
    def test_segments_download_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            r = stream_download(_srv.url("/big.mp4"), td, segments=4,
                                min_segment_bytes=1024)  # 强制走分片并发
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.strategy, "direct-segments")
            path = Path(r.path)
            self.assertEqual(path.stat().st_size, len(BIG))
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(h, BIG_SHA)

    def test_segment_resume(self):
        """分片级断点续传:预置上次中断留下的完整分片 → 跳过下载直接合并。"""
        with tempfile.TemporaryDirectory() as td:
            n = 4
            chunk = len(BIG) // n
            part_dir = Path(td) / "big.mp4.parts"
            part_dir.mkdir()
            for i in range(n):
                start = i * chunk
                end = len(BIG) - 1 if i == n - 1 else (i + 1) * chunk - 1
                (part_dir / f"{i:04d}.part").write_bytes(BIG[start:end + 1])
            r = stream_download(_srv.url("/big.mp4"), td, segments=4,
                                min_segment_bytes=1024)
            self.assertTrue(r.ok, r.error)
            self.assertTrue(r.resumed)  # 分片命中缓存,未重新下载
            self.assertEqual(hashlib.sha256(Path(r.path).read_bytes()).hexdigest(), BIG_SHA)

    def test_progress_monotonic(self):
        with tempfile.TemporaryDirectory() as td:
            seen = []
            r = stream_download(_srv.url("/big.mp4"), td, segments=4,
                                min_segment_bytes=1024,
                                on_progress=lambda d, t: seen.append(d))
            self.assertTrue(r.ok)
            self.assertEqual(seen, sorted(seen))
            self.assertGreater(len(seen), 0)

    def test_single_stream_no_range(self):
        with tempfile.TemporaryDirectory() as td:
            r = stream_download(_srv.url("/norange.bin"), td, segments=4)
            self.assertTrue(r.ok)
            self.assertEqual(r.strategy, "direct-single")
            self.assertEqual(hashlib.sha256(Path(r.path).read_bytes()).hexdigest(), BIG_SHA)

    def test_speed_limit(self):
        with tempfile.TemporaryDirectory() as td:
            t0 = time.monotonic()
            r = stream_download(_srv.url("/big.mp4"), td, segments=1,
                                min_segment_bytes=1024,
                                speed_limit=1 << 20)  # 1MB/s,3MB → ≥3s
            elapsed = time.monotonic() - t0
            self.assertTrue(r.ok)
            self.assertGreaterEqual(elapsed, 1.5)


class TestHls(unittest.TestCase):
    def test_parse_m3u8(self):
        pl = parse_m3u8("#EXTM3U\n#EXTINF:10.0,\nseg0.ts\n#EXT-X-ENDLIST\n",
                        "http://x/play/")
        self.assertEqual(len(pl.segments), 1)
        self.assertEqual(pl.segments[0]["uri"], "http://x/play/seg0.ts")

    def test_master_selects_best_variant(self):
        with tempfile.TemporaryDirectory() as td:
            r = stream_download(_srv.url("/master.m3u8"), td)
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.strategy, "hls")
            got = Path(r.path).read_bytes()
            self.assertEqual(hashlib.sha256(got).hexdigest(), HLS_JOIN_SHA)
            self.assertEqual(got, SEG0 + SEG1 + SEG2)

    def test_plain_hls(self):
        with tempfile.TemporaryDirectory() as td:
            r = stream_download(_srv.url("/index.m3u8"), td)
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.segments, 3)
            self.assertEqual(hashlib.sha256(Path(r.path).read_bytes()).hexdigest(), HLS_JOIN_SHA)

    def test_aes128_hls(self):
        if not CRYPTO_OK:
            self.skipTest("cryptography 未安装")
        with tempfile.TemporaryDirectory() as td:
            r = stream_download(_srv.url("/enc/index.m3u8"), td)
            self.assertTrue(r.ok, r.error)
            self.assertEqual(hashlib.sha256(Path(r.path).read_bytes()).hexdigest(),
                             hashlib.sha256(SEG0 + SEG1).hexdigest())

    def test_fmp4_rejected_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            r = stream_download(_srv.url("/fmp4/index.m3u8"), td)
            self.assertFalse(r.ok)
            self.assertIn("ffmpeg", r.error)


class TestToolRegistration(unittest.TestCase):
    def test_download_stream_registered(self):
        import ai.skills  # noqa: F401
        from skills.core import get_registry

        self.assertTrue(get_registry().has("download_stream"))

    def test_tool_invoke_failure_on_bad_url(self):
        import ai.skills  # noqa: F401
        from skills.core import get_registry

        r = get_registry().invoke("download_stream",
                                  {"url": "http://127.0.0.1:1/nope.mp4"})
        self.assertFalse(r.ok)  # 探测失败 → 失败,不崩溃

    def test_tool_invoke_success_local(self):
        import ai.skills  # noqa: F401
        from skills.core import get_registry

        with tempfile.TemporaryDirectory() as td:
            r = get_registry().invoke(
                "download_stream", {"url": _srv.url("/index.m3u8"), "dest_dir": td})
            self.assertTrue(r.ok, r.message)
            self.assertEqual(r.data["strategy"], "hls")
            self.assertTrue(Path(r.data["path"]).exists())


if __name__ == "__main__":
    unittest.main()
