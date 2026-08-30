"""相关性打分 / 过滤 / 探测分类 单元测试。"""

import unittest

from search.filter import filter_results, rank_results, relevance_score, tokenize_query
from search.models import SearchResult
from search.probe import _classify
from search.models import (
    KIND_BLOCKED, KIND_DEAD, KIND_DIRECT_FILE, KIND_UNREACHABLE, KIND_WEBPAGE,
)


def _r(title, url, snippet=""):
    return SearchResult(title=title, url=url, snippet=snippet, engine="t")


class TestTokenize(unittest.TestCase):
    def test_latin_and_cjk(self):
        toks = tokenize_query("Python requests 教程 pdf")
        self.assertIn("python", toks)
        self.assertIn("教程", toks)

    def test_long_cjk_phrase_gets_bigrams(self):
        # 『凡人修仙传壁纸』必须能匹配到被空格/标点切开的标题(凡人修仙传 4K高清壁纸)
        toks = tokenize_query("凡人修仙传壁纸")
        self.assertIn("凡人修仙传壁纸", toks)
        self.assertIn("凡人", toks)
        self.assertIn("修仙", toks)
        self.assertIn("壁纸", toks)
        # 标题含『凡人修仙传 4K高清壁纸』应命中多个 bigram
        hay = "凡人修仙传 4K高清壁纸".lower()
        self.assertGreaterEqual(sum(1 for t in toks if t in hay), 5)

    def test_cjk_substring_matching(self):
        # 修复前:整词 in title 为 False → 有效结果被过滤
        q = "凡人修仙传壁纸"
        r = SearchResult(title="凡人修仙传 4K高清壁纸", url="https://x.com/a",
                         snippet="高清壁纸下载", engine="t")
        self.assertGreater(relevance_score(q, r), 0)
        kept = filter_results(q, [r])
        self.assertEqual(len(kept), 1)


class TestScoring(unittest.TestCase):
    def test_title_weights_highest(self):
        q = "设计模式 pdf"
        title_hit = _r("设计模式 pdf 下载", "https://x.com/a", "无关内容")
        snippet_hit = _r("无关标题", "https://x.com/b", "这里有 设计模式 pdf")
        self.assertGreater(relevance_score(q, title_hit), relevance_score(q, snippet_hit))

    def test_pan_bonus(self):
        q = "某书 pdf"
        pan = _r("某书", "https://pan.baidu.com/s/1abc?pwd=1", "某书 pdf")
        web = _r("某书", "https://x.com/page", "某书 pdf")
        self.assertGreater(relevance_score(q, pan), relevance_score(q, web))

    def test_direct_file_bonus(self):
        q = "book"
        direct = _r("book", "https://x.com/book.pdf")
        page = _r("book", "https://x.com/book")
        self.assertGreater(relevance_score(q, direct), relevance_score(q, page))


class TestFilter(unittest.TestCase):
    def test_zero_relevance_dropped(self):
        kept = filter_results("python 爬虫", [_r("完全无关内容", "https://x.com/other")])
        self.assertEqual(kept, [])

    def test_tracking_host_dropped(self):
        kept = filter_results("python", [_r("python", "https://adservice.google.com/x")])
        self.assertEqual(kept, [])


class TestProbeClassify(unittest.TestCase):
    def test_pdf_direct(self):
        self.assertEqual(_classify(200, "application/pdf", ".pdf"), KIND_DIRECT_FILE)

    def test_html_webpage(self):
        self.assertEqual(_classify(200, "text/html; charset=utf-8", ""), KIND_WEBPAGE)

    def test_zip_direct(self):
        self.assertEqual(_classify(200, "application/zip", ".zip"), KIND_DIRECT_FILE)

    def test_404_dead(self):
        self.assertEqual(_classify(404, "text/html", ""), KIND_DEAD)

    def test_403_blocked(self):
        self.assertEqual(_classify(403, "text/html", ""), KIND_BLOCKED)

    def test_429_blocked(self):
        self.assertEqual(_classify(429, "text/html", ""), KIND_BLOCKED)

    def test_timeout_unreachable(self):
        self.assertEqual(_classify(0, "", ""), KIND_UNREACHABLE)

    def test_challenge_marker_blocked(self):
        self.assertEqual(
            _classify(200, "text/html", "", "Just a moment... verifying you are human"),
            KIND_BLOCKED,
        )


if __name__ == "__main__":
    unittest.main()
