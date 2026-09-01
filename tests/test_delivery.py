"""delivery 下载交付单元测试(离线)。"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from delivery.downloader import _fix_filename, _guess_filename, download


class TestGuessFilename(unittest.TestCase):
    def test_extensionless_url_gets_placeholder(self):
        name = _guess_filename("http://down.x.com/down/207942")
        self.assertTrue(name.endswith(".bin"))

    def test_normal_url_keeps_name(self):
        self.assertEqual(_guess_filename("https://x.com/a/book.txt"), "book.txt")


class TestWallClockTimeout(unittest.TestCase):
    """慢速但不断流的服务器(92wx.la 类 ~1KB/s)必须按墙钟超时终止,
    否则任务会挂死数十分钟(requests 的 timeout 只管单次 socket 读写)。"""

    def test_slow_stream_times_out_and_keeps_part(self):
        class _SlowResp:
            status_code = 200
            headers = {"Content-Type": "application/octet-stream",
                       "Content-Length": "99999999"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_content(self, n):
                for _ in range(200):
                    time.sleep(0.05)
                    yield b"x" * n

        with tempfile.TemporaryDirectory() as td:
            with mock.patch("delivery.downloader.requests.get",
                            return_value=_SlowResp()):
                r = download("https://slow.example.com/big.zip", td,
                             timeout=1.0, retries=1)
            self.assertIn("超时", r.error)
            # 断点保留:后续可续传
            parts = list(Path(td).glob("*.part"))
            self.assertEqual(len(parts), 1)
            self.assertGreater(parts[0].stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
