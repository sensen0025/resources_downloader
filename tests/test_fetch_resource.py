"""fetch_resource 云盘链接收集与 TaskResult 结构单元测试(不依赖网络)。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 站点信誉库隔离:测试写临时库,不污染真实信誉库(必须在调用前设置)
_siterep_tmp = tempfile.mkdtemp(prefix="rh_fr_siterep_")
os.environ["RH_SITEREP_PATH"] = str(Path(_siterep_tmp) / "siterep.json")

from agent.tasks.base import TaskResult
from pages.models import PageClass


def _rep_reset():
    from skills.siterep.store import clear

    clear()


def _rep_seed(host, score, description="测试"):
    from skills.siterep.store import record

    record(host, score=score, description=description, query="测试")


def _rep_score(host):
    from skills.siterep.store import score_of

    return score_of(host)


class _FakeRes:
    def __init__(self, url: str, kind: str = "link", text: str = ""):
        self.url = url
        self.kind = kind
        # 与真实 ExtractedResource 一致:从 URL 路径取扩展名(无扩展名 = "")
        from urllib.parse import urlsplit

        last = urlsplit(url).path.rsplit("/", 1)[-1]
        self.file_ext = ("." + last.rsplit(".", 1)[-1].lower()) if "." in last else ""
        self.text = text


class _FakeSearch:
    def __init__(self, url: str):
        self.url = url
        self.title = "candidate"
        self.snippet = ""


class _FakeAnalysis:
    def __init__(self, page_class, resources):
        self.page_class = page_class
        self.reason = "fake"
        self.best_resources = resources


class TestTaskResultPanLinks(unittest.TestCase):
    def test_to_dict_includes_pan_links(self):
        r = TaskResult(success=False, files=["a.bin"],
                       pan_links=["https://pan.baidu.com/s/1"])
        d = r.to_dict()
        self.assertEqual(d["pan_links"], ["https://pan.baidu.com/s/1"])
        self.assertIn("files", d)
        self.assertIn("error", d)


class TestFetchResourcePanLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 注意:agent/tasks/__init__.py 把 fetch_resource 属性重绑成了函数,
        # 必须用 import_module 拿真实模块才能 mock 其内部函数
        import importlib

        cls.fr = importlib.import_module("agent.tasks.fetch_resource")

    def setUp(self):
        # 保存原函数:test_api 等可能 patch 过模块属性,测试内要调用真正的实现
        self._orig = self.fr.fetch_resource

    def tearDown(self):
        self.fr.fetch_resource = self._orig

    def test_pan_links_collected_on_analysis(self):
        """分析阶段发现 pan_share → 结果里带回云盘链接(即使无文件落地)。"""
        fr = self.fr

        pan = _FakeRes("https://pan.baidu.com/s/1xyz", kind="pan_share")
        # 聚合页:只有网盘分享,无直链,无 agent 兜底
        analysis = _FakeAnalysis(PageClass.AGGREGATOR, [pan])
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://x.com/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis):
                result = fr.fetch_resource(query="测试", out_dir=td,
                                           use_agent_fallback=False, verbose=False)
        self.assertFalse(result.success)
        self.assertEqual(result.pan_links, ["https://pan.baidu.com/s/1xyz"])
        self.assertIn("pan_links", result.to_dict())

    def test_pan_links_deduplicated(self):
        fr = self.fr

        pan1 = _FakeRes("https://pan.baidu.com/s/1xyz", kind="pan_share")
        pan2 = _FakeRes("https://pan.baidu.com/s/1xyz", kind="pan_share")
        analysis = _FakeAnalysis(PageClass.AGGREGATOR, [pan1, pan2])
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://x.com/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis):
                result = fr.fetch_resource(query="测试", out_dir=td,
                                           use_agent_fallback=False, verbose=False)
        self.assertEqual(result.pan_links, ["https://pan.baidu.com/s/1xyz"])


class TestInferFileTypes(unittest.TestCase):
    """查询关键词 → 文件类型推断(显式格式词优先,不被关键词数量带偏)。"""

    def _infer(self, q):
        from agent.tasks.fetch_resource import _infer_file_types

        return _infer_file_types(q, None)

    def test_explicit_txt_wins_over_video_keyword(self):
        # 报告案例:「全集」属于视频词,但用户明确写了 txt → 必须锁定文档类
        exts = self._infer("师兄实在是太稳健了 全集 txt 文本")
        self.assertIn(".txt", exts)
        self.assertIn(".pdf", exts)
        self.assertNotIn(".mp4", exts)
        self.assertNotIn(".m3u8", exts)

    def test_novel_quanji_document(self):
        # 「小说 全集」无显式格式词 → 显式词“小说”锁定文档
        exts = self._infer("某小说 全集 免费阅读")
        self.assertIn(".txt", exts)
        self.assertNotIn(".mp4", exts)

    def test_wallpaper_image(self):
        exts = self._infer("凡人修仙传 壁纸 4k")
        self.assertIn(".jpg", exts)
        self.assertNotIn(".txt", exts)

    def test_video_series(self):
        exts = self._infer("凡人修仙传 第10集 在线观看")
        self.assertIn(".mp4", exts)
        self.assertIn(".m3u8", exts)

    def test_software_exe(self):
        exts = self._infer("鬼谷八荒 修改器 下载 exe")
        self.assertIn(".exe", exts)
        self.assertIn(".zip", exts)

    def test_explicit_file_types_param_wins(self):
        from agent.tasks.fetch_resource import _infer_file_types

        self.assertEqual(_infer_file_types("随便什么", (".zip",)), (".zip",))


class TestAgentRouting(unittest.TestCase):
    """Agent 候选路由:登录墙跳过、同站去重、下载按钮优先。"""

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.fr = importlib.import_module("agent.tasks.fetch_resource")

    def setUp(self):
        self._orig = self.fr.fetch_resource

    def tearDown(self):
        self.fr.fetch_resource = self._orig

    def test_login_required_skipped_without_email(self):
        # 无 login_email 时,「需登录」候选不应进 Agent(否则空耗数分钟)
        fr = self.fr

        analysis = _FakeAnalysis(PageClass.LOGIN_REQUIRED, [])
        calls: list[str] = []

        def fake_agent(*a, **k):
            calls.append(a[0])
            return False

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://x.com/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis), \
                 mock.patch.object(fr, "_agent_fetch", side_effect=fake_agent):
                fr.fetch_resource(query="测试", out_dir=td,
                                  use_agent_fallback=True, verbose=False)
        self.assertEqual(calls, [])

    def test_agent_candidates_host_dedup_and_priority(self):
        # 同站点网络只试一次;带下载按钮的详情页优先
        fr = self.fr

        dlbtn = _FakeAnalysis(PageClass.DOWNLOAD_PAGE,
                              [_FakeRes("https://qq.com/a.xyz", kind="direct_file"),
                               _FakeRes("https://qq.com/dl", kind="download_button", text="下载")])
        agg1 = _FakeAnalysis(PageClass.AGGREGATOR, [])
        agg2 = _FakeAnalysis(PageClass.AGGREGATOR, [])
        calls: list[str] = []

        def fake_agent(*a, **k):
            calls.append(a[0])
            return False

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines", return_value=[
                    _FakeSearch("https://qq.com/1"), _FakeSearch("https://m.qq.com/2"),
                    _FakeSearch("https://reader.example.org/3")]), \
                 mock.patch.object(fr, "analyze_page", side_effect=[dlbtn, agg1, agg2]), \
                 mock.patch.object(fr, "_download_one", return_value=None), \
                 mock.patch.object(fr, "_agent_fetch", side_effect=fake_agent):
                fr.fetch_resource(query="测试", out_dir=td,
                                  use_agent_fallback=True, verbose=False)
        # qq.com/1(dl-btn 优先)与 reader.example.org/3 各试一次;m.qq.com/2 因同族被跳过
        self.assertEqual(calls, ["https://qq.com/1", "https://reader.example.org/3"])

    def test_download_button_fastpath(self):
        # 下载按钮通常就是直链(乐书谷类):先快路径直下,命中即成功,不再进 Agent
        fr = self.fr
        analysis = _FakeAnalysis(
            PageClass.DOWNLOAD_PAGE,
            [_FakeRes("https://x.com/down/207942", kind="download_button", text="TXT下载")])
        calls: list[str] = []

        def fake_download_one(url, out_dir, file_types, stream_media=True,
                              on_stage=None, is_cancelled=None):
            calls.append(url)
            p = Path(out_dir) / "book.txt"
            p.write_text("正文内容", encoding="utf-8")
            return str(p)

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://x.com/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis), \
                 mock.patch.object(fr, "_download_one", side_effect=fake_download_one):
                result = fr.fetch_resource(query="测试 txt", out_dir=td,
                                           use_agent_fallback=False, verbose=False)
        self.assertIn("https://x.com/down/207942", calls)
        self.assertTrue(result.success)

    def test_client_install_page_routed_to_agent(self):
        # 页面只有客户端安装按钮(下载酷狗):不盲下直链,把页面交给 Agent 找真实资源
        # (酷狗歌曲页的真实音频在 hash API 里,只有 AI 能找)
        fr = self.fr
        analysis = _FakeAnalysis(
            PageClass.DOWNLOAD_PAGE,
            [_FakeRes("https://download.kugou.com/download/kugou_mac",
                      kind="client_install", text="下载酷狗音乐客户端")])
        calls: list[str] = []

        def fake_agent(*a, **k):
            calls.append(a[0])
            return False

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://kugou.com/song/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis), \
                 mock.patch("skills.bilibili.search_videos", return_value=[]), \
                 mock.patch.object(fr, "_agent_fetch", side_effect=fake_agent):
                fr.fetch_resource(query="歌曲 下载", out_dir=td,
                                  use_agent_fallback=True, verbose=False)
        # 页面进了 Agent 候选;客户端安装按钮没有被当直链下载
        self.assertEqual(calls, ["https://kugou.com/song/1"])

    def test_media_second_round_adds_bilibili_candidates(self):
        # 媒体查询直链无果 → B站搜索换源(酷狗登录墙下走 B站播放流)
        fr = self.fr
        analysis = _FakeAnalysis(PageClass.AGGREGATOR, [])
        calls: list[str] = []

        def fake_agent(*a, **k):
            calls.append(a[0])
            return False

        hits = [{"url": "https://www.bilibili.com/video/BV1abc",
                 "title": "大爱炼天 蛊真人同人歌曲", "author": "u"}]
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://kugou.com/song/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis), \
                 mock.patch("skills.bilibili.search_videos", return_value=hits), \
                 mock.patch.object(fr, "_agent_fetch", side_effect=fake_agent):
                fr.fetch_resource(query="大爱炼天 歌曲下载", out_dir=td,
                                  use_agent_fallback=True, verbose=False)
        # B站候选进入 Agent(优先于同分的普通候选由信誉排序决定,但一定会被尝试)
        self.assertIn("https://www.bilibili.com/video/BV1abc", calls)

    def test_non_media_query_no_bilibili_round(self):
        # 非媒体查询不触发 B站二次检索
        fr = self.fr
        analysis = _FakeAnalysis(PageClass.AGGREGATOR, [])
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://x.com/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis), \
                 mock.patch("skills.bilibili.search_videos") as m, \
                 mock.patch.object(fr, "_agent_fetch", return_value=False):
                fr.fetch_resource(query="故宫 建筑 投影", out_dir=td,
                                  use_agent_fallback=True, verbose=False)
        m.assert_not_called()

    def test_agent_success_without_landed_file_not_success(self):
        # Agent 报告成功但任务目录没有文件落地 → 不得宣判成功
        # (否则网页显示 done 却无可下载文件 —— 线上事故复现)
        fr = self.fr
        analysis = _FakeAnalysis(PageClass.AGGREGATOR, [])
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://x.com/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis), \
                 mock.patch.object(fr, "_agent_fetch", return_value=True):
                result = fr.fetch_resource(query="测试", out_dir=td,
                                           use_agent_fallback=True, verbose=False)
        self.assertFalse(result.success)
        self.assertEqual(result.files, [])

    def test_site_reputation_recorded_after_run(self):
        # 任务结束后自动把访问过的站点录入信誉库(LLM 不可用 → 启发式评分)
        # 注意:主机名不能用受保护域名(x.com=Twitter 等会被跳过)
        _rep_reset()
        fr = self.fr
        analysis = _FakeAnalysis(PageClass.AGGREGATOR, [])
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines",
                                   return_value=[_FakeSearch("https://sample-noise.net/1")]), \
                 mock.patch.object(fr, "analyze_page", return_value=analysis):
                fr.fetch_resource(query="测试", out_dir=td,
                                  use_agent_fallback=False, verbose=False)
        self.assertIsNotNone(_rep_score("sample-noise.net"))

    def test_agent_candidates_reputation_reorder(self):
        # 信誉库已知差站(≤2)排最后:Agent 预算不浪费在差站上;高分好站优先
        _rep_reset()
        _rep_seed("bad.example.net", 1, "SEO 假下载页")
        _rep_seed("good.example.com", 8, "直链可下的好站")
        fr = self.fr
        analysis_bad = _FakeAnalysis(PageClass.AGGREGATOR, [])
        analysis_good = _FakeAnalysis(PageClass.AGGREGATOR, [])
        calls: list[str] = []

        def fake_agent(*a, **k):
            calls.append(a[0])
            return False

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(fr, "search_engines", return_value=[
                    _FakeSearch("https://bad.example.net/1"),
                    _FakeSearch("https://good.example.com/2")]), \
                 mock.patch.object(fr, "analyze_page",
                                   side_effect=[analysis_bad, analysis_good]), \
                 mock.patch.object(fr, "_agent_fetch", side_effect=fake_agent):
                fr.fetch_resource(query="测试", out_dir=td,
                                  use_agent_fallback=True, verbose=False)
        # 差站虽然在搜索结果里排前面,但信誉重排后最后才试
        self.assertEqual(calls, ["https://good.example.com/2", "https://bad.example.net/1"])


class TestNovelHelpers(unittest.TestCase):
    """小说意图/查询改写/付费站判定。"""

    def _fns(self):
        import importlib

        fr = importlib.import_module("agent.tasks.fetch_resource")
        return fr._is_novel_intent, fr._rewrite_novel_query, fr._is_paid_novel_host

    def test_novel_intent(self):
        is_novel, _, _ = self._fns()
        self.assertTrue(is_novel("下载 阵问长生 小说的txt全文", None))
        self.assertTrue(is_novel("临渊行 txt全书", None))
        self.assertFalse(is_novel("故宫 建筑 投影", None))
        self.assertFalse(is_novel("测试", None))  # 全格式兜底(含 .txt)不算小说意图
        self.assertTrue(is_novel("随便什么", (".txt",)))

    def test_query_rewrite(self):
        _, rw, _ = self._fns()
        self.assertEqual(rw("下载 阵问长生 小说的txt全文"), "阵问长生 txt 下载 网盘")
        self.assertEqual(rw("临渊行 txt全书"), "临渊行 txt 下载 网盘")
        # 非小说查询不改写
        self.assertEqual(rw("python requests 教程 pdf"), "")

    def test_paid_host(self):
        _, _, paid = self._fns()
        self.assertTrue(paid("https://www.qidian.com/book/1"))
        self.assertTrue(paid("https://book.qq.com/kol-rec/abc"))
        self.assertTrue(paid("https://mwbook.read.qq.com/bqq/xyz"))
        self.assertFalse(paid("https://bqg5555.cc/xs/b4128431"))
        self.assertFalse(paid("https://80xs.la/txtxz/1/down.html"))


class TestMediaGuard(unittest.TestCase):
    """音视频直链防护:安装包 URL 预检 + 下载内容魔数校验。

    线上事故:酷狗歌曲页「下载」按钮实为 93MB 客户端安装包,被当歌曲直链下载;
    酷狗 getdata 返回 171B 假 mp3 也被当作成功落地。
    """

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.fr = importlib.import_module("agent.tasks.fetch_resource")

    def test_non_media_url_precheck(self):
        self.assertTrue(self.fr._is_non_media_url("https://download.kugou.com/kugou_mac.exe"))
        self.assertTrue(self.fr._is_non_media_url("https://x.com/app.apk?from=web"))
        self.assertTrue(self.fr._is_non_media_url("https://x.com/logo.png"))
        # 酷狗客户端安装包:无扩展名,靠 URL 形状识别(93MB 白下事故)
        self.assertTrue(self.fr._is_non_media_url("https://download.kugou.com/download/kugou_mac"))
        self.assertFalse(self.fr._is_non_media_url("https://x.com/song/1234.mp3"))

    def test_media_magic_sniff(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t"
            p.write_bytes(b"ID3\x03\x00" + b"\x00" * 100)          # MP3
            self.assertTrue(self.fr._is_media_file(p))
            p.write_bytes(b"fLaC" + b"\x00" * 100)                  # FLAC
            self.assertTrue(self.fr._is_media_file(p))
            p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8)  # WAV
            self.assertTrue(self.fr._is_media_file(p))
            p.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8)  # M4A
            self.assertTrue(self.fr._is_media_file(p))
            p.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)     # Mach-O 客户端
            self.assertFalse(self.fr._is_media_file(p))
            p.write_bytes(b"<html><body>login</body></html>")       # 登录页 HTML
            self.assertFalse(self.fr._is_media_file(p))
            p.write_bytes(b"x" * 171)                               # 171B 假 mp3
            self.assertFalse(self.fr._is_media_file(p))

    def test_download_one_discards_non_media(self):
        # 流式下载"成功"但内容是客户端安装包 → 丢弃并按失败处理
        # (URL 用 .mp3 伪装躲过预检,魔数校验兜底)
        fr = self.fr
        with tempfile.TemporaryDirectory() as td:
            fake_path = Path(td) / "fake_song.mp3"
            fake_path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)

            class _R:
                ok = True
                path = str(fake_path)

            events = []
            with mock.patch("skills.streaming.stream_download", return_value=_R()):
                r = fr._download_one("https://x.com/fake_song.mp3",
                                     Path(td), (".mp3", ".flac", ".wav"),
                                     on_stage=lambda s, m: events.append((s, m)))
            self.assertIsNone(r)
            self.assertFalse(fake_path.exists())       # 已删除
            self.assertTrue(any("非音视频" in m for _, m in events))

    def test_download_one_skips_client_install_url(self):
        # 酷狗客户端安装包 URL:预检直接拦下,根本不发起下载(93MB 白下事故)
        fr = self.fr
        with tempfile.TemporaryDirectory() as td:
            events = []
            with mock.patch("skills.streaming.stream_download") as m:
                r = fr._download_one("https://download.kugou.com/download/kugou_mac",
                                     Path(td), (".mp3", ".flac", ".wav"),
                                     on_stage=lambda s, e: events.append((s, e)))
            self.assertIsNone(r)
            m.assert_not_called()                      # 未发起流式下载
            self.assertTrue(any("跳过" in e for _, e in events))


class TestReachability(unittest.TestCase):
    """站点可达性预检:连接层失败才算不可达,HTTP 错误(403/412/5xx)算可达。

    线上事故:『凡人修仙传 第10集』候选 v0-frontend-project-implementation-phi.
    vercel.app 连不通,浏览器 Agent 在 chrome-error 页重试 900s 吃光兜底预算。
    """

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.fr = importlib.import_module("agent.tasks.fetch_resource")

    def test_connection_error_unreachable(self):
        import requests as _requests

        with mock.patch("requests.get",
                        side_effect=_requests.exceptions.ConnectTimeout()):
            self.assertFalse(self.fr._is_reachable("https://dead.example.com/video"))

    def test_http_error_still_reachable(self):
        # 403(反爬)/412(B站 WAF)/500:服务器可达,浏览器 Agent 可能能过 → 不算不可达
        for status in (403, 412, 500, 404):
            r = mock.MagicMock()
            r.status_code = status
            with mock.patch("requests.get", return_value=r):
                self.assertTrue(self.fr._is_reachable("https://x.example.com/page"),
                                f"HTTP {status} 应视为可达")

    def test_bad_url_unreachable(self):
        self.assertFalse(self.fr._is_reachable(""))
        self.assertFalse(self.fr._is_reachable("not-a-url"))


if __name__ == "__main__":
    unittest.main()
