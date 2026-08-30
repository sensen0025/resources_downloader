"""页面提取器 / 分级器 单元测试(离线,用内联 HTML)。"""

import unittest

from pages.classify import classify_page
from pages.extractor import analyze_html, extract_metadata, extract_resources
from pages.models import ExtractedResource, PageAnalysis, PageClass, PageInfo


class TestExtractMetadata(unittest.TestCase):
    HTML = """
    <html><head>
      <title>Goldencrest Manor - Minecraft Schematic</title>
      <meta property="og:description" content="A beautiful manor house">
      <meta name="author" content="Builder123">
      <script type="application/ld+json">{"headline":"Goldencrest Manor","description":"Manor by Builder123"}</script>
    </head><body></body></html>
    """

    def test_metadata(self):
        meta = extract_metadata(self.HTML)
        self.assertEqual(meta["title"], "Goldencrest Manor - Minecraft Schematic")
        self.assertEqual(meta["og:description"], "A beautiful manor house")
        self.assertEqual(meta["author"], "Builder123")
        self.assertEqual(meta["ld_headline"], "Goldencrest Manor")


class TestExtractResources(unittest.TestCase):
    def test_direct_file_and_pan(self):
        html = """
        <a href="/schematic/31398/download/">Download</a>
        <a href="https://cdn.example.com/files/house.litematic">house.litematic</a>
        <a href="https://pan.baidu.com/s/1abc?pwd=1234">网盘</a>
        <a href="/about">About</a>
        <iframe src="https://player.example.com/video"></iframe>
        """
        links = extract_resources(html, "https://minecraft-schematics.com/")
        kinds = {l.kind for l in links}
        self.assertIn("direct_file", kinds)          # .litematic
        self.assertIn("pan_share", kinds)            # pan.baidu.com
        self.assertIn("download_button", kinds)      # Download 文本
        self.assertIn("iframe", kinds)
        # 相对路径解析
        dl = next(l for l in links if l.kind == "download_button")
        self.assertTrue(dl.url.startswith("https://minecraft-schematics.com/"))
        # 普通 about 链接不进资源
        self.assertFalse(any("about" in l.url for l in links))

    def test_relative_resolution_and_dedup(self):
        html = """
        <a href="/files/a.litematic">a</a>
        <a href="/files/a.litematic">a again</a>
        """
        links = extract_resources(html, "https://x.com/page/")
        # 同一 URL 去重(锚文本不同但 URL 相同)
        self.assertEqual(len([l for l in links if "a.litematic" in l.url]), 1)

    def test_img_and_og_image_extraction(self):
        html = """
        <img src="https://cdn.x.com/thumbs/small.jpg" data-original="https://cdn.x.com/full/hd.jpg">
        <img src="/thumbs/thumb.png" srcset="/thumbs/thumb-320.png 320w, /thumbs/thumb-1024.png 1024w">
        <meta property="og:image" content="https://cdn.x.com/og/cover.webp">
        """
        links = extract_resources(html, "https://x.com/page/")
        urls = {l.url: l for l in links}
        # data-original 大图优先于 src 缩略图
        self.assertIn("https://cdn.x.com/full/hd.jpg", urls)
        self.assertEqual(urls["https://cdn.x.com/full/hd.jpg"].kind, "image")
        # srcset 取最大候选
        self.assertIn("https://x.com/thumbs/thumb-1024.png", urls)
        # og:image 最高分
        self.assertEqual(urls["https://cdn.x.com/og/cover.webp"].kind, "image")
        self.assertGreater(urls["https://cdn.x.com/og/cover.webp"].score,
                           urls["https://cdn.x.com/full/hd.jpg"].score)

    def test_github_repo_not_direct_file(self):
        # 报告案例:仓库名以 .zip 结尾 / blob 预览页 → 不是直链
        html = """
        <a href="https://github.com/Hnockat/v1-pack.zip">v1-pack 仓库</a>
        <a href="https://github.com/Hnockat/v1-pack.zip/blob/main/pack.zip">预览</a>
        <a href="https://github.com/Hnockat/v1-pack/releases/download/v1/pack.zip">releases 直链</a>
        """
        links = extract_resources(html, "https://github.com/")
        direct = [l for l in links if l.kind == "direct_file"]
        urls = [l.url for l in direct]
        self.assertNotIn("https://github.com/Hnockat/v1-pack.zip", urls)
        self.assertNotIn("https://github.com/Hnockat/v1-pack.zip/blob/main/pack.zip", urls)
        self.assertIn("https://github.com/Hnockat/v1-pack/releases/download/v1/pack.zip", urls)


class TestClassify(unittest.TestCase):
    def _analyze(self, resources=None, title="", needs_login=False):
        return PageAnalysis(page_url="https://x.com/", title=title,
                            resources=resources or [])

    def test_probe_direct(self):
        a = self._analyze()
        classify_page(PageInfo(url="https://x.com/a.litematic"), a,
                      probe_kind="direct_file")
        self.assertEqual(a.page_class, PageClass.DIRECT_FILE)

    def test_download_page(self):
        a = self._analyze([ExtractedResource(url="https://x.com/house.litematic",
                                             kind="direct_file", score=3.0)])
        classify_page(PageInfo(url="https://x.com/schematic/1/"), a)
        self.assertEqual(a.page_class, PageClass.DOWNLOAD_PAGE)

    def test_pan_share(self):
        a = self._analyze([ExtractedResource(url="https://pan.baidu.com/s/1", kind="pan_share")])
        classify_page(PageInfo(url="https://x.com/"), a)
        self.assertEqual(a.page_class, PageClass.PAN_SHARE)

    def test_login_required(self):
        a = self._analyze()
        page = PageInfo(url="https://x.com/login/",
                        html="<input type='password' name='pw'> Please sign in")
        classify_page(page, a)
        self.assertEqual(a.page_class, PageClass.LOGIN_REQUIRED)

    def test_blocked_cf(self):
        a = self._analyze()
        page = PageInfo(url="https://x.com/", html="Just a moment... verifying you are human")
        classify_page(page, a)
        self.assertEqual(a.page_class, PageClass.BLOCKED)

    def test_dead(self):
        a = self._analyze()
        classify_page(PageInfo(url="https://x.com/", status=404), a)
        self.assertEqual(a.page_class, PageClass.DEAD)

    def test_aggregator(self):
        a = self._analyze(title="Schematics Library - All Downloads")
        classify_page(PageInfo(url="https://x.com/schematics/"), a)
        self.assertEqual(a.page_class, PageClass.AGGREGATOR)

    def test_unknown(self):
        a = self._analyze(title="Some page")
        classify_page(PageInfo(url="https://x.com/page"), a)
        self.assertEqual(a.page_class, PageClass.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
