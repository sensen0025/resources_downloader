"""广告过滤数据池(skills/adblock)单元测试。"""

import unittest

from skills.adblock import (
    ad_penalty,
    filter_ad_links,
    has_ad_text,
    is_ad_domain,
    is_ad_url,
)


class TestAdDomain(unittest.TestCase):
    def test_ad_networks(self):
        self.assertTrue(is_ad_domain("https://adservice.google.com/x"))
        self.assertTrue(is_ad_domain("https://static.doubleclick.net/x"))
        self.assertTrue(is_ad_domain("https://cpro.baidu.com/x"))
        self.assertTrue(is_ad_domain("https://pos.baidu.com/x"))
        self.assertTrue(is_ad_domain("https://www.taboola.com/x"))
        self.assertTrue(is_ad_domain("http://ads.example.com/banner"))

    def test_legit_domains_not_blocked(self):
        self.assertFalse(is_ad_domain("https://www.bilibili.com/video/1"))
        self.assertFalse(is_ad_domain("https://pan.baidu.com/s/1"))
        self.assertFalse(is_ad_domain("https://github.com/user/repo"))
        # 统计域名刻意不拦
        self.assertFalse(is_ad_domain("https://hm.baidu.com/hm.js"))


class TestUrlPattern(unittest.TestCase):
    def test_patterns(self):
        self.assertTrue(is_ad_url("https://x.com/ad/123"))
        self.assertTrue(is_ad_url("https://x.com/ads?k=1"))
        self.assertTrue(is_ad_url("https://x.com/go?adclick=1"))
        self.assertTrue(is_ad_url("https://x.com/promote/1"))
        self.assertTrue(is_ad_url("https://x.com/tuiguang/1"))
        self.assertFalse(is_ad_url("https://x.com/download/1"))
        self.assertFalse(is_ad_url("https://x.com/archive/1"))


class TestTextMarks(unittest.TestCase):
    def test_marks(self):
        self.assertTrue(has_ad_text("【广告】点击领取"))
        self.assertTrue(has_ad_text("", "Sponsored content"))
        self.assertFalse(has_ad_text("凡人修仙传 第10集"))


class TestPenalty(unittest.TestCase):
    def test_penalty_levels(self):
        self.assertGreater(ad_penalty("https://adservice.google.com/x"), 2.0)
        self.assertGreater(ad_penalty("https://x.com/ad/1"), 1.0)
        self.assertGreater(ad_penalty("https://x.com/video/1", "广告 推广"), 0.0)
        self.assertEqual(ad_penalty("https://x.com/video/1"), 0.0)


class TestFilter(unittest.TestCase):
    def test_filter_links(self):
        urls = [
            "https://x.com/video.mp4",
            "https://adservice.google.com/x",
            "https://x.com/ad/1",
        ]
        kept = filter_ad_links(urls)
        self.assertEqual(kept, ["https://x.com/video.mp4"])


class TestSearchIntegration(unittest.TestCase):
    def test_filter_results_drops_ads(self):
        from search.models import SearchResult

        from search.filter import filter_results

        results = [
            SearchResult(title="凡人修仙传 第10集", url="https://x.com/video/1",
                         snippet="在线观看", engine="test", rank=1),
            SearchResult(title="广告推广", url="https://adservice.google.com/x",
                         snippet="点击", engine="test", rank=2),
        ]
        kept = filter_results("凡人修仙传", results)
        self.assertEqual(len(kept), 1)
        self.assertIn("x.com/video", kept[0].url)


class TestExtractorIntegration(unittest.TestCase):
    def test_ad_link_not_in_candidates(self):
        from pages.extractor import extract_resources

        html = (
            '<html><body>'
            '<a href="https://x.com/video.mp4">下载</a>'
            '<a href="https://adservice.google.com/click">广告</a>'
            "</body></html>"
        )
        res = extract_resources(html, "https://x.com/")
        urls = [r.url for r in res]
        self.assertIn("https://x.com/video.mp4", urls)
        self.assertFalse(any("adservice" in u for u in urls))


if __name__ == "__main__":
    unittest.main()
