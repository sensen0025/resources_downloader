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

    def test_noise_image_anchor_not_direct_file(self):
        # <a href="…jpg">营业执照</a> 这类噪音图片锚点不应当文件直链(与 <img> 通道一致)
        html = """
        <a href="https://cdn.x.com/license.jpg">营业执照</a>
        <a href="https://cdn.x.com/logo.png">logo</a>
        <a href="https://cdn.x.com/icp.jpg">增值电信业务经营许可证（ICP证090200）</a>
        <a href="https://cdn.x.com/wallpaper.jpg">高清壁纸 4k</a>
        <a href="https://cdn.x.com/book.zip">小说全集txt下载</a>
        """
        links = extract_resources(html, "https://x.com/")
        direct = [l for l in links if l.kind == "direct_file"]
        urls = [l.url for l in direct]
        self.assertNotIn("https://cdn.x.com/license.jpg", urls)
        self.assertNotIn("https://cdn.x.com/logo.png", urls)
        self.assertNotIn("https://cdn.x.com/icp.jpg", urls)
        self.assertIn("https://cdn.x.com/wallpaper.jpg", urls)  # 真壁纸仍算直链
        self.assertIn("https://cdn.x.com/book.zip", urls)

    def test_client_install_button_not_download_button(self):
        # 酷狗等站点「下载酷狗/下载客户端/立即安装」是装客户端,不是资源下载按钮
        # (线上事故:歌曲页的「下载」按钮实为 93MB 客户端安装包)
        # 标记为 client_install:不进直链下载,但页面会路由给 Agent 找真实资源
        html = """
        <a href="https://download.kugou.com/download/kugou_mac">下载酷狗音乐客户端</a>
        <a href="https://x.com/app/download">下载客户端</a>
        <a href="https://x.com/install">立即安装</a>
        <a href="https://x.com/down/song/1234">大爱炼天 歌曲下载</a>
        """
        links = extract_resources(html, "https://x.com/")
        btns = [l for l in links if l.kind == "download_button"]
        urls = [l.url for l in btns]
        self.assertNotIn("https://download.kugou.com/download/kugou_mac", urls)
        self.assertNotIn("https://x.com/app/download", urls)
        self.assertNotIn("https://x.com/install", urls)
        # 客户端安装按钮单独标记(client_install),不进直链下载但保留给 Agent
        ci = [l.url for l in links if l.kind == "client_install"]
        self.assertIn("https://download.kugou.com/download/kugou_mac", ci)
        self.assertIn("https://x.com/app/download", ci)
        self.assertIn("https://x.com/install", ci)
        # 真下载链接仍算下载按钮
        self.assertIn("https://x.com/down/song/1234", urls)

    def test_client_install_url_with_generic_text(self):
        # 按钮文本是通用「下载」,但 URL 指向站点客户端安装包 → 仍按 client_install
        # (酷狗歌曲页的 pb_download 按钮文本就是「下载」,URL 是 kugou_mac 安装包)
        html = """
        <a href="https://download.kugou.com/download/kugou_mac">下载</a>
        <a href="https://dl.stream.qqmusic.qq.com/C400001.mp3">下载</a>
        """
        links = extract_resources(html, "https://kugou.com/song/1")
        ci = [l.url for l in links if l.kind == "client_install"]
        self.assertIn("https://download.kugou.com/download/kugou_mac", ci)
        # 真音频 CDN 链接不受影响(按直链处理,非 client_install)
        mp3 = next(l for l in links if "C400001.mp3" in l.url)
        self.assertNotEqual(mp3.kind, "client_install")

    def test_seo_book_page_link_not_download_button(self):
        # 阅读平台 SEO 引导页:「txt下载」锚文本指向书页/章节页 → 不是下载按钮
        html = """
        <a href="https://xs8.cn/bookquery/jdvxiqzf">师兄实在太稳健了txt精校版下载</a>
        <a href="https://qidian.com/chapter/1016572786/498215647">开始阅读</a>
        <a href="https://x.com/download?id=12345">txt全集下载</a>
        """
        links = extract_resources(html, "https://x.com/")
        btns = [l for l in links if l.kind == "download_button"]
        self.assertNotIn("https://xs8.cn/bookquery/jdvxiqzf", [l.url for l in btns])
        self.assertNotIn("https://qidian.com/chapter/1016572786/498215647", [l.url for l in btns])
        self.assertEqual([l.url for l in btns], ["https://x.com/download?id=12345"])

    def test_bqg_seo_crosspromo_not_download_button(self):
        # 笔趣阁类 SEO 站:「XXX下载」锚文本指向站内其它书页(/xs/<id>) → 不是下载按钮
        html = """
        <a href="https://bqg5555.cc/xs/162283">开局地摊卖大力下载</a>
        <a href="https://bqg5555.cc/xs/b4128431">阵问长生txt下载</a>
        <a href="https://bqg5555.cc/down/207942">阵问长生txt下载</a>
        <a href="https://bqg5555.cc/download?id=5">全文下载</a>
        """
        links = extract_resources(html, "https://bqg5555.cc/xs/b4128431")
        btns = [l for l in links if l.kind == "download_button"]
        urls = [l.url for l in btns]
        self.assertNotIn("https://bqg5555.cc/xs/162283", urls)
        self.assertNotIn("https://bqg5555.cc/xs/b4128431", urls)
        self.assertEqual(urls, ["https://bqg5555.cc/down/207942",
                                "https://bqg5555.cc/download?id=5"])

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


class TestFetcherStubFollow(unittest.TestCase):
    """fetch_page 应跟随 JS/meta-refresh 跳转存根(so.com/link 等中转链接)。"""

    def test_js_redirect_stub_followed(self):
        import pages.fetcher as fetcher_mod
        from pages.fetcher import fetch_page

        calls: list[str] = []

        class _FakeResp:
            def __init__(self, url, text, status=200, ctype="text/html"):
                self.url = url
                self.text = text
                self.status_code = status
                self.headers = {"Content-Type": ctype}

        def fake_get(url, **kw):
            calls.append(url)
            if "so.com/link" in url:
                return _FakeResp(
                    url,
                    '<script>window.location.replace("https://real.example.com/book")</script>'
                    '<noscript><meta http-equiv="refresh" content="0;URL=\'https://real.example.com/book\'"></noscript>',
                )
            if url == "https://real.example.com/book":
                return _FakeResp(url, "<html><title>Real Book Page</title><a href='/dl/a.txt'>下载</a></html>")
            raise AssertionError(f"unexpected url {url}")

        old = fetcher_mod._session
        fetcher_mod._session = type("S", (), {"get": staticmethod(fake_get)})()
        fetcher_mod.clear_cache()
        try:
            info = fetch_page("https://so.com/link?m=abc%2Fdef", use_cache=False)
        finally:
            fetcher_mod._session = old
        self.assertEqual(info.url, "https://real.example.com/book")  # 解析基准=最终 URL
        self.assertIn("Real Book Page", info.html)
        self.assertIn("so.com/link", calls[0])
        self.assertEqual(calls[-1], "https://real.example.com/book")

    def test_normal_page_not_treated_as_stub(self):
        import pages.fetcher as fetcher_mod
        from pages.fetcher import fetch_page

        class _FakeResp:
            def __init__(self, url, text):
                self.url = url
                self.text = text
                self.status_code = 200
                self.headers = {"Content-Type": "text/html"}

        def fake_get(url, **kw):
            return _FakeResp(url, "<html><title>Normal</title><p>content here</p></html>")

        old = fetcher_mod._session
        fetcher_mod._session = type("S", (), {"get": staticmethod(fake_get)})()
        fetcher_mod.clear_cache()
        try:
            info = fetch_page("https://example.com/page", use_cache=False)
        finally:
            fetcher_mod._session = old
        self.assertEqual(info.url, "https://example.com/page")
        self.assertIn("Normal", info.html)

    def test_gbk_page_without_charset_decoded(self):
        # 报告案例:中文小说站(乐书谷等)不声明 charset,requests 默认 latin-1 →
        # 全文乱码 → 下载链接(锚文本"下载")与标题全失效。fetch_page 应还原真实编码。
        import pages.fetcher as fetcher_mod
        from pages.fetcher import fetch_page

        content = ('<html><head><meta charset="gbk"></head><body>'
                   '<a href="http://down.x.com/down/207942">TXT下载</a>'
                   '<title>师兄实在太稳健了全文阅读</title></body></html>').encode("utf-8")

        class _FakeResp:
            encoding = None  # 无 charset → requests 会按 latin-1 解

            def __init__(self):
                self.content = content
                self.headers = {"Content-Type": "text/html"}
                self.url = "https://x.com/book/207942"
                self.status_code = 200
                self.apparent_encoding = "utf-8"

            @property
            def text(self):
                return self.content.decode(self.encoding or "ISO-8859-1", "replace")

        def fake_get(url, **kw):
            return _FakeResp()

        old = fetcher_mod._session
        fetcher_mod._session = type("S", (), {"get": staticmethod(fake_get)})()
        fetcher_mod.clear_cache()
        try:
            info = fetch_page("https://x.com/book/207942", use_cache=False)
        finally:
            fetcher_mod._session = old
        self.assertIn("TXT下载", info.html)          # 中文锚文本不再乱码
        self.assertIn("师兄实在太稳健了", info.title)


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

    def test_login_nav_link_not_wall(self):
        # 报告案例:普通站点带登录按钮/“loginbox”CSS 类 → 不应判为登录墙
        # (否则无邮箱时整站被跳过,乐书谷类有下载链接的页面也进不来)
        a = self._analyze()
        page = PageInfo(
            url="https://x.com/book/207942",
            html='<dl class="fr loginbox"><a href="/login">登录</a></dl>'
                 '<p>全文TXT下载</p><a href="http://down.x.com/down/207942">TXT下载</a>')
        classify_page(page, a)
        self.assertNotEqual(a.page_class, PageClass.LOGIN_REQUIRED)

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
