"""引擎解析器单元测试 — 用真实抓取的 HTML fixture(离线,不联网)。

fixture 保存在 search/_samples/(本机实测抓取)。通过实例级 _get 注入
fixture 响应,验证解析器能正确提取 标题/URL/摘要。
"""

import unittest
from pathlib import Path

from search.engines import (
    BaiduEngine, BingEngine, DuckDuckGoEngine, MojeekEngine, So360Engine,
)

SAMPLES = Path(__file__).resolve().parents[1] / "search" / "_samples"


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def _make_engine(engine_cls, fixture: str):
    """创建引擎实例并把 _get 换成读 fixture(离线,不触发真实网络)。"""
    eng = engine_cls()
    html = (SAMPLES / fixture).read_text(encoding="utf-8")
    eng._get = lambda *a, **k: _FakeResponse(html)  # 实例级覆盖基类方法
    return eng


class TestBing(unittest.TestCase):
    def test_parses_results_and_decodes_url(self):
        eng = _make_engine(BingEngine, "bing.html")
        results = eng.search("python requests 教程 pdf")
        self.assertGreaterEqual(len(results), 5)
        for r in results:
            self.assertTrue(r.title)
            self.assertTrue(r.url.startswith("http"))
        # 跳转链接 u= base64 应解出真实 URL(带尾斜杠)
        self.assertEqual(results[0].url, "https://www.python.org/")

    def test_decode_handles_html_entities(self):
        # Bing 跳转链接里的 & 是 HTML 实体 &amp; —— 必须 unescape 后才能匹配 u= 参数
        from search.engines.bing import _decode_bing_url

        href = ("https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u="
                "a1aHR0cHM6Ly93d3cucHl0aG9uLm9yZy8&amp;ntb=1")
        self.assertEqual(_decode_bing_url(href), "https://www.python.org/")

    def test_breadcrumb_cite_rejected(self):
        from search.engines.bing import _valid_url

        self.assertFalse(_valid_url("https://haowallpaper.com › homeViewLook"))
        self.assertFalse(_valid_url("https://x.com has space"))
        self.assertTrue(_valid_url("https://haowallpaper.com/homeViewLook"))


class TestBingCnDirectUrls(unittest.TestCase):
    """cn.bing.com 直接给真实 URL(非 /ck/a 跳转)+ cite 面包屑 → 结果不能被丢弃。"""

    def _parse(self, html):
        from search.engines.bing import BingEngine

        eng = BingEngine()

        class _R:
            text = html
            status_code = 200

        eng._get = lambda *a, **k: _R()
        return eng.search("测试", per_engine=5)

    def test_cn_bing_direct_url_not_dropped(self):
        # 报告案例:cn.bing 的 h2 href 直接是真实 URL(无 u= 参数),
        # 旧逻辑全部走 cite 兜底→面包屑被拒→恒 0 结果
        html = """
        <ol id="b_results">
        <li class="b_algo"><h2><a href="https://www.leshugu.info/book/207942" h="ID=SERP,1.1">师兄实在太稳健了 txt下载 - 乐书谷</a></h2>
        <div class="b_caption"><p class="b_lineclamp2">全文txt下载</p></div>
        <cite>https://www.leshugu.info › book › 207942</cite></li>
        <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;p=abc&amp;u=a1aHR0cHM6Ly93d3cucHl0aG9uLm9yZy8&amp;ntb=1">python</a></h2>
        <div class="b_caption"><p class="b_lineclamp2">desc</p></div>
        <cite>https://www.python.org/</cite></li>
        </ol>
        """
        urls = [r.url for r in self._parse(html)]
        self.assertIn("https://www.leshugu.info/book/207942", urls)  # 直接 URL 不再被丢弃
        self.assertIn("https://www.python.org/", urls)               # ck/a 解码仍工作

    def test_cite_breadcrumb_fallback_takes_first_segment(self):
        # cite 是 "https://x.com › sub › page" 面包屑时取第一段,而不是整条丢弃
        html = """
        <li class="b_algo"><h2><a href="/relative/x" h="ID=SERP,1.1">标题</a></h2>
        <cite>https://example.com › sub › page</cite></li>
        """
        urls = [r.url for r in self._parse(html)]
        self.assertEqual(urls, ["https://example.com"])


class TestMojeek(unittest.TestCase):
    def test_parses_results(self):
        eng = _make_engine(MojeekEngine, "mojeek.html")
        results = eng.search("python requests tutorial pdf")
        self.assertGreaterEqual(len(results), 5)
        self.assertEqual(results[0].url, "https://pdf.co/tutorials/fill-pdf-form-in-python")
        self.assertTrue(all(r.title and r.url.startswith("http") for r in results))


class TestBaidu(unittest.TestCase):
    def test_parses_results_with_real_urls(self):
        eng = _make_engine(BaiduEngine, "baidu.html")
        results = eng.search("python requests 教程 pdf")
        self.assertGreaterEqual(len(results), 5)
        for r in results:
            self.assertTrue(r.url.startswith("http"), r.url)
            self.assertNotIn("baidu.com/link", r.url)  # 应为 mu= 真实 URL,非跳转
        self.assertTrue(any("php.cn" in r.url or "csdn.net" in r.url for r in results))


class TestSo360(unittest.TestCase):
    def test_parses_results(self):
        eng = _make_engine(So360Engine, "so360.html")
        results = eng.search("python requests 教程 pdf")
        self.assertGreaterEqual(len(results), 3)
        for r in results:
            self.assertTrue(r.title)
            self.assertTrue(r.url.startswith("http"))

    def test_redirect_links_not_deduped_to_death(self):
        # 360 结果全是 so.com/link?m=<opaque> 跳转:去重 key 必须是完整 URL
        from search.engines.base import SearchEngine
        from search.models import SearchResult

        results = [
            SearchResult(title=f"t{i}", url=f"https://www.so.com/link?m={i}" * 0 or
                         f"https://www.so.com/link?m=opaque{i}", engine="so360", rank=i)
            for i in range(5)
        ]
        kept = SearchEngine._dedupe_in_engine(results)
        self.assertEqual(len(kept), 5)


class TestDuckDuckGo(unittest.TestCase):
    def test_parses_results(self):
        eng = _make_engine(DuckDuckGoEngine, "ddg_lite.html")
        results = eng.search("python requests pdf")
        self.assertGreaterEqual(len(results), 5)
        self.assertTrue(all(r.title and r.url.startswith("http") for r in results))
        # DDG 常直接给出 PDF 直链(archive.org / readthedocs 等)
        self.assertTrue(any(".pdf" in r.url for r in results))


if __name__ == "__main__":
    unittest.main()
