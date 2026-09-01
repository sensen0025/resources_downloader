"""小说章节拼接器单元测试(离线,内联 HTML)。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.novel import (
    clean_book_title,
    extract_chapter_links,
    extract_chapter_text,
    fetch_novel_txt,
)


class TestExtractChapterLinks(unittest.TestCase):
    def test_sorted_deduped_skips_nonchapter(self):
        html = """
        <a href="/xs/1/3.html">第3章 末尾</a>
        <a href="/xs/1/1.html">第1章 开头</a>
        <a href="/xs/1/2.html">第2章 中段</a>
        <a href="/xs/1/1.html">第1章 重复(同 URL)</a>
        <a href="/xs/162283">开局地摊卖大力下载</a>
        """
        urls = extract_chapter_links(html, "https://bqg.com/xs/b1")
        self.assertEqual(urls, ["https://bqg.com/xs/1/1.html",
                                "https://bqg.com/xs/1/2.html",
                                "https://bqg.com/xs/1/3.html"])

    def test_chinese_numerals(self):
        html = """
        <a href="/a/2.html">第一百二十五章 收官</a>
        <a href="/a/1.html">第一章 开局</a>
        """
        urls = extract_chapter_links(html, "https://x.com/")
        self.assertEqual(urls, ["https://x.com/a/1.html", "https://x.com/a/2.html"])

    def test_no_chapters(self):
        self.assertEqual(extract_chapter_links("<a href='/x'>首页</a>", "https://x.com/"), [])


class TestExtractChapterText(unittest.TestCase):
    def test_content_container(self):
        html = ('<html><body><div id="content">第一章正文开始。' + "内容" * 60 +
                '</div><p>导航</p></body></html>')
        text = extract_chapter_text(html)
        self.assertIn("第一章正文开始", text)
        self.assertNotIn("导航", text)

    def test_fallback_longest_block(self):
        html = ('<html><body><p>短导航</p><p>' + "正文段落内容。" * 40 + "</p></body></html>")
        text = extract_chapter_text(html)
        self.assertIn("正文段落内容", text)


class TestCleanBookTitle(unittest.TestCase):
    def test_strips_site_noise(self):
        t = clean_book_title("阵问长生txt下载最新章节_阵问长生txt下载免费全文阅读_笔趣阁")
        self.assertEqual(t, "阵问长生")


class TestFetchNovelTxt(unittest.TestCase):
    def test_full_merge(self):
        import skills.novel.merger as m

        book_html = """
        <html><head><title>阵问长生txt下载最新章节_阵问长生txt下载免费全文阅读_笔趣阁</title></head>
        <body>
          <a href="/xs/1/5.html">第5章</a>
          <a href="/xs/1/1.html">第1章</a>
          <a href="/xs/1/3.html">第3章</a>
          <a href="/xs/1/2.html">第2章</a>
          <a href="/xs/1/4.html">第4章</a>
        </body></html>
        """

        def fake_fetch(url, timeout=15.0, **kw):
            from pages.models import PageInfo

            if "/book/" in url:
                return PageInfo(url=url, status=200, html=book_html,
                                title="阵问长生txt下载 - 笔趣阁")
            no = url.rstrip("/").split("/")[-1].split(".")[0]
            html = ('<div id="content">第%s章 正文' + "内容" * 40 + "</div>") % no
            return PageInfo(url=url, status=200, html=html, title=f"第{no}章")

        with mock.patch.object(m, "fetch_page", side_effect=fake_fetch):
            with tempfile.TemporaryDirectory() as td:
                r = fetch_novel_txt("https://bqg.com/book/1", out_dir=td, per_page_sleep=0)
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["chapters"], 5)
                p = Path(r["path"])
                self.assertTrue(p.exists())
                text = p.read_text(encoding="utf-8")
                self.assertIn("阵问长生", text[:40])           # 书名头
                self.assertIn("第1章 正文", text)
                self.assertIn("第5章 正文", text)

    def test_bailout_on_low_extraction_rate(self):
        # 渐进早退:前 15 章只提取到 <3 章 → 判为 SEO 列表页,快速失败
        import skills.novel.merger as m

        book_html = "".join(f'<a href="/xs/{i}.html">第{i}章</a>' for i in range(1, 30))

        def fake_fetch(url, timeout=15.0, **kw):
            from pages.models import PageInfo

            if "/book/" in url:
                return PageInfo(url=url, status=200, html=book_html,
                                title="某小说txt下载 - 笔趣阁")
            no = url.rstrip("/").split("/")[-1].split(".")[0]
            # 大多数章节页无正文(模拟 SEO 列表页的无关链接)
            html = '<div id="content">第%s章</div>' % no if int(no) % 10 == 1 else "<html><body>无内容</body></html>"
            return PageInfo(url=url, status=200, html=html, title=f"第{no}章")

        with mock.patch.object(m, "fetch_page", side_effect=fake_fetch):
            with tempfile.TemporaryDirectory() as td:
                r = fetch_novel_txt("https://bqg.com/book/1", out_dir=td,
                                    per_page_sleep=0, min_text_len=50)
        self.assertFalse(r["ok"])
        self.assertIn("候选页不匹配", r.get("error", ""))

    def test_no_chapter_list(self):
        import skills.novel.merger as m

        def fake_fetch(url, timeout=15.0, **kw):
            from pages.models import PageInfo

            return PageInfo(url=url, status=200, html="<html><body>无目录</body></html>", title="x")

        with mock.patch.object(m, "fetch_page", side_effect=fake_fetch):
            with tempfile.TemporaryDirectory() as td:
                r = fetch_novel_txt("https://x.com/book/1", out_dir=td)
        self.assertFalse(r["ok"])
        self.assertIn("未发现章节目录", r.get("error", ""))


if __name__ == "__main__":
    unittest.main()
