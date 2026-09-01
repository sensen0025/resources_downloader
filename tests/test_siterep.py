"""站点信誉库(skills/siterep)单元测试:存储/向量检索/LLM 打分/工具注册。

隔离:RH_SITEREP_PATH 指向临时文件(import 前设置),不污染真实信誉库。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="rh_siterep_test_")
os.environ["RH_SITEREP_PATH"] = str(Path(_tmp) / "siterep.json")

from skills.siterep import (  # noqa: E402
    heuristic_score,
    record_feedback,
    score_sites_with_llm,
    site_lookup,
)
from skills.siterep import store  # noqa: E402
from skills.siterep.store import embed_text, lookup, record, score_of  # noqa: E402


class TestStore(unittest.TestCase):
    def setUp(self):
        store.clear()

    def test_record_and_score_of(self):
        record("bqg5555.cc", url="https://bqg5555.cc/xs/1", score=7,
               description="笔趣阁镜像,分章小说可拼接", query="小说 txt")
        self.assertEqual(score_of("bqg5555.cc"), 7)
        self.assertIsNone(score_of("unknown.example.com"))

    def test_lookup_topk_by_similarity(self):
        record("bqg5555.cc", score=8, description="笔趣阁镜像,小说章节全文阅读", query="阵问长生 txt")
        record("80xs.la", score=6, description="电子书站,提供全本 txt 打包下载", query="小说 txt 网盘")
        record("wallpaper.example.com", score=7, description="4k 壁纸图片站", query="壁纸 高清")
        hits = lookup("小说 txt 下载 全文", top_k=2)
        self.assertEqual(len(hits), 2)
        self.assertIn(hits[0]["host"], ("bqg5555.cc", "80xs.la"))
        self.assertGreater(hits[0]["similarity"], 0.1)
        self.assertIn("score", hits[0])

    def test_lookup_min_score_filter(self):
        record("bad.example.com", score=1, description="SEO 假下载页", query="小说")
        record("good.example.com", score=8, description="直链下载站", query="小说")
        hits = lookup("小说", top_k=5, min_score=5)
        self.assertEqual([h["host"] for h in hits], ["good.example.com"])

    def test_record_upsert_increments_visit_count(self):
        record("x.com", score=5, query="a")
        record("x.com", score=8, query="b")
        e = store.load()["x.com"]
        self.assertEqual(e["score"], 8)      # 新分覆盖
        self.assertEqual(e["visit_count"], 2)
        self.assertEqual(e["query"], "b")

    def test_score_of_subdomain_fallback(self):
        # 录的是注册域,搜索/分析命中 www/m 子域也要能匹配
        record("zhihu.com", score=3, description="x")
        self.assertEqual(score_of("zhihu.com"), 3)
        self.assertEqual(score_of("www.zhihu.com"), 3)
        self.assertEqual(score_of("m.zhihu.com"), 3)
        self.assertIsNone(score_of("zhihu.net"))
        self.assertIsNone(score_of("not-recorded.com"))


class TestScoring(unittest.TestCase):
    def setUp(self):
        store.clear()

    def test_llm_batch_scoring_parsed(self):
        sites = [{"host": "good.com", "url": "https://good.com/a", "page_class": "download_page",
                  "downloaded": True},
                 {"host": "bad.com", "url": "https://bad.com/b", "page_class": "aggregator",
                  "fake": True}]
        reply = ('[{"host":"good.com","score":8,"description":"直链可下,真实文件"},'
                 '{"host":"bad.com","score":1,"description":"下载按钮指向书页,虚假"}]')
        with mock.patch("agent.llm.LLMClient") as Fake:
            Fake.return_value.chat.return_value = reply
            scored = score_sites_with_llm(sites)
        self.assertEqual(scored["good.com"][0], 8)
        self.assertEqual(scored["bad.com"][0], 1)
        self.assertIn("直链可下", scored["good.com"][1])

    def test_llm_failure_falls_back_heuristic(self):
        sites = [{"host": "down.com", "page_class": "download_page", "downloaded": True},
                 {"host": "blocked.com", "page_class": "blocked"}]
        with mock.patch("agent.llm.LLMClient") as Fake:
            Fake.return_value.chat.side_effect = RuntimeError("llm down")
            scored = score_sites_with_llm(sites)
        self.assertEqual(scored["down.com"][0], 8)      # 下载成功 → 8
        self.assertEqual(scored["blocked.com"][0], 1)   # 被拦截 → 1
        self.assertTrue(scored["down.com"][1])

    def test_heuristic_signals(self):
        self.assertEqual(heuristic_score({"downloaded": True}), 8)
        self.assertEqual(heuristic_score({"fake": True}), 1)
        self.assertEqual(heuristic_score({"page_class": "blocked"}), 1)
        self.assertEqual(heuristic_score({"page_class": "login_required"}), 2)
        self.assertEqual(heuristic_score({"outcome": "merge_ok"}), 7)


class TestRecordFeedback(unittest.TestCase):
    def setUp(self):
        store.clear()

    def test_record_feedback_dedupes_and_stores(self):
        sites = [
            {"host": "good.com", "url": "https://good.com/1", "query": "小说", "downloaded": True},
            {"host": "good.com", "url": "https://good.com/2", "query": "小说", "agent_ok": True},
            {"host": "bad.com", "url": "https://bad.com/1", "query": "小说", "fake": True},
        ]
        with mock.patch("agent.llm.LLMClient") as Fake:
            Fake.return_value.chat.return_value = (
                '[{"host":"good.com","score":8,"description":"好站"},'
                '{"host":"bad.com","score":1,"description":"差站"}]')
            result = record_feedback(sites)
        self.assertEqual(result["good.com"], 8)
        self.assertEqual(result["bad.com"], 1)
        entries = store.load()
        self.assertEqual(len(entries), 2)          # 按 host 去重
        self.assertEqual(entries["good.com"]["visit_count"], 1)

    def test_cap_batch(self):
        sites = [{"host": f"site{i}.com", "query": "q"} for i in range(30)]
        with mock.patch("agent.llm.LLMClient") as Fake:
            Fake.return_value.chat.return_value = (
                '[' + ",".join(f'{{"host":"site{i}.com","score":5,"description":"x"}}'
                               for i in range(15)) + ']')
            result = record_feedback(sites)
        self.assertLessEqual(len(result), 15)

    def test_protected_hosts_never_recorded(self):
        """受保护站点(教育/AI/邮箱等敏感账号站)不录入,不被标噪音。"""
        import json
        import tempfile
        from pathlib import Path

        import skills.siterep.protected as prot

        # 用临时保护规则文件隔离(不改服务器真实 data/protected_sites.json)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump({"patterns": [r"canvas", r"deepseek", r"\.edu$"]}, tmp)
        tmp.close()
        orig = prot._DATA
        prot._DATA = Path(tmp.name)
        prot._cache["mtime"] = 0.0
        try:
            sites = [
                {"host": "canvas.instructure.com", "query": "作业", "page_class": "unknown"},
                {"host": "chat.deepseek.com", "query": "ai", "page_class": "login_required"},
                {"host": "state.edu", "query": "x", "page_class": "login_required"},
                {"host": "noise-news.com", "query": "x", "page_class": "aggregator"},
            ]
            with mock.patch("agent.llm.LLMClient") as Fake:
                Fake.return_value.chat.return_value = (
                    '[{"host":"noise-news.com","score":2,"description":"噪音"},'
                    '{"host":"canvas.instructure.com","score":1,"description":"x"},'
                    '{"host":"chat.deepseek.com","score":1,"description":"x"},'
                    '{"host":"state.edu","score":1,"description":"x"}]')
                result = record_feedback(sites)
        finally:
            prot._DATA = orig
            prot._cache["mtime"] = 0.0
            Path(tmp.name).unlink(missing_ok=True)
        # 只有噪音站被录入;受保护的全被跳过
        self.assertIn("noise-news.com", result)
        self.assertNotIn("canvas.instructure.com", result)
        self.assertNotIn("chat.deepseek.com", result)
        self.assertNotIn("state.edu", result)
        self.assertEqual(store.load().get("canvas.instructure.com"), None)

    def test_is_protected_subdomain(self):
        """保护判定支持子域:www.google.com / chat.deepseek.com 都要命中。"""
        import json
        import tempfile
        from pathlib import Path

        import skills.siterep.protected as prot

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump({"patterns": [r"^google", r"deepseek", r"instructure", r"\.edu$"]}, tmp)
        tmp.close()
        orig = prot._DATA
        prot._DATA = Path(tmp.name)
        prot._cache["mtime"] = 0.0
        try:
            self.assertTrue(prot.is_protected("www.google.com"))
            self.assertTrue(prot.is_protected("chat.deepseek.com"))
            self.assertTrue(prot.is_protected("canvas.instructure.com"))
            self.assertTrue(prot.is_protected("www.mit.edu"))
            self.assertFalse(prot.is_protected("noise-news.com"))
        finally:
            prot._DATA = orig
            prot._cache["mtime"] = 0.0
            Path(tmp.name).unlink(missing_ok=True)


class TestTools(unittest.TestCase):
    def setUp(self):
        store.clear()
        import importlib

        importlib.import_module("skills.siterep")

    def test_tools_registered_and_invocable(self):
        from skills.core import get_registry

        reg = get_registry()
        self.assertTrue(reg.has("record_site_feedback"))
        self.assertTrue(reg.has("site_lookup"))
        record("bqg5555.cc", score=8, description="笔趣阁镜像,小说章节全文", query="小说 txt")
        r = reg.invoke("site_lookup", {"query": "小说 txt 下载", "top_k": 3})
        self.assertTrue(r.ok)
        self.assertIn("bqg5555.cc", r.message)
        data = r.data
        self.assertTrue(data)
        self.assertEqual(data[0]["host"], "bqg5555.cc")
        self.assertEqual(data[0]["score"], 8)


if __name__ == "__main__":
    unittest.main()
