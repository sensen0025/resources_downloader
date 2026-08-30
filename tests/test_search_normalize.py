"""URL 规范化 / 去重键 / 网盘识别 单元测试。"""

import unittest

from search.normalize import dedup_key, file_ext, is_pan_share, is_tracking_host, normalize_url


class TestNormalize(unittest.TestCase):
    def test_tracking_params_stripped(self):
        url = "https://example.com/a/b?utm_source=x&utm_medium=y&id=5&spm=123"
        self.assertEqual(normalize_url(url), "https://example.com/a/b?id=5")

    def test_www_scheme_case_and_slash(self):
        # 统一 https、去 www、去尾斜杠;路径大小写保留(URL 路径区分大小写)
        self.assertEqual(normalize_url("HTTP://WWW.Example.com/Path/"), "https://example.com/Path")

    def test_fragment_stripped(self):
        self.assertEqual(normalize_url("https://example.com/page#section"), "https://example.com/page")

    def test_pan_pwd_kept(self):
        url = "https://pan.baidu.com/s/1abc?pwd=wx8g&from=search"
        self.assertEqual(normalize_url(url), "https://pan.baidu.com/s/1abc?pwd=wx8g")

    def test_dedup_key_merges_dupes(self):
        a = dedup_key("https://www.Example.com/a?utm_source=1&id=3")
        b = dedup_key("http://example.com/a?id=3")
        self.assertEqual(a, b)

    def test_file_ext(self):
        self.assertEqual(file_ext("https://x.com/a/book.pdf?dl=1"), ".pdf")
        self.assertEqual(file_ext("https://x.com/a"), "")

    def test_image_extensions_are_direct(self):
        from search.normalize import DIRECT_EXTENSIONS

        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"):
            self.assertIn(ext, DIRECT_EXTENSIONS, f"{ext} 应属于直链扩展名")
            self.assertEqual(file_ext(f"https://x.com/wallpaper{ext}"), ext)

    def test_github_disguise_not_direct(self):
        # 报告案例:仓库名以 .zip 结尾、/blob/ 预览页 —— 都是 HTML,不是直链
        from search.normalize import is_direct_file_url

        self.assertFalse(is_direct_file_url("https://github.com/Hnockat/v1-pack.zip"))
        self.assertFalse(is_direct_file_url(
            "https://github.com/Hnockat/v1-pack.zip/blob/main/pack.zip"))
        self.assertFalse(is_direct_file_url(
            "https://github.com/Hnockat/v1-pack/tree/main/some/pack.zip"))

    def test_github_real_direct(self):
        from search.normalize import is_direct_file_url

        self.assertTrue(is_direct_file_url(
            "https://github.com/Hnockat/v1-pack/releases/download/v1/pack.zip"))
        self.assertTrue(is_direct_file_url(
            "https://raw.githubusercontent.com/Hnockat/v1-pack/main/pack.zip"))
        self.assertTrue(is_direct_file_url(
            "https://github.com/Hnockat/v1-pack/archive/refs/heads/main.zip"))
        # 普通站点的 .zip 仍是直链
        self.assertTrue(is_direct_file_url("https://cdn.example.com/files/pack.zip"))

    def test_pan_share_detection(self):
        self.assertTrue(is_pan_share("https://pan.baidu.com/s/1abc"))
        self.assertTrue(is_pan_share("https://www.123pan.com/s/xyz"))
        self.assertFalse(is_pan_share("https://example.com/book.pdf"))

    def test_tracking_host_blocked(self):
        self.assertTrue(is_tracking_host("https://adservice.google.com/x"))
        self.assertTrue(is_tracking_host("https://pos.baidu.com/y"))
        self.assertFalse(is_tracking_host("https://example.com/x"))


if __name__ == "__main__":
    unittest.main()
