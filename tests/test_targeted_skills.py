# -*- coding: utf-8 -*-
"""dsh_targeted_skills 自测套件(用户指定路径 test_targeted_skills.py)。

离线单测(默认,不依赖网络):
  - 契约:schema 必填/枚举/与用户规范一致;@tool 注册;run_skill 校验拒绝;
  - 纯解析函数:笔趣阁正文清洗(广告/水印/字号条)、gdgame 网盘/密码/列表、
    haowallpaper CDN 提取、libgen 行解析、CF 检测;
  - annas md5 直查路径。

在线冒烟(可选):设置环境变量 RH_LIVE=1 时跑真实站点(默认跳过)。
   python -m pytest tests/test_targeted_skills.py -v
   RH_LIVE=1 python -m pytest tests/test_targeted_skills.py -v -m live
"""
import base64
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

import dsh_targeted_skills as dts


def _live_enabled() -> bool:
    return os.environ.get("RH_LIVE", "") == "1"


LIVE = _live_enabled()


class TestContract(unittest.TestCase):
    """契约与注册:5 个技能名/必填参数与用户规范对齐。"""

    def test_five_schemas_present(self):
        self.assertEqual(set(dts.SCHEMAS),
                         {"biquge_novel_crawler", "haowallpaper_4k_extractor",
                          "gdgame_resource_fetcher", "littleskin_texture_extractor",
                          "annas_archive_book_finder"})

    def test_required_fields(self):
        self.assertEqual(dts.BIQUGE_SCHEMA["required"], ["book_url_or_id"])
        self.assertEqual(dts.HAOWALLPAPER_SCHEMA["required"], ["url_or_id"])
        self.assertEqual(dts.GDGAME_SCHEMA["required"], ["target"])
        self.assertEqual(dts.LITTLESKIN_SCHEMA["required"], ["username_or_hash"])
        self.assertEqual(dts.ANNAS_ARCHIVE_SCHEMA["required"], ["query"])

    def test_enums(self):
        self.assertEqual(dts.GDGAME_SCHEMA["properties"]["action"]["enum"],
                         ["get_game_detail", "search_games"])
        self.assertEqual(dts.LITTLESKIN_SCHEMA["properties"]["texture_type"]["enum"],
                         ["skin", "cape", "all"])
        self.assertEqual(dts.ANNAS_ARCHIVE_SCHEMA["properties"]["preferred_format"]["enum"],
                         ["any", "epub", "pdf", "mobi"])

    def test_tools_registered(self):
        from skills.core import get_registry

        reg = get_registry()
        for name in dts.SCHEMAS:
            self.assertTrue(reg.has(name), f"{name} 未注册进技能表")
            entry = reg.get(name).catalog_entry()
            self.assertEqual(entry["function"]["name"], name)

    def test_run_skill_unknown_rejected(self):
        tr = dts.run_skill("not_a_skill", {})
        self.assertFalse(tr.ok)
        self.assertEqual(tr.error, "UNKNOWN_SKILL")

    def test_run_skill_missing_required_rejected(self):
        tr = dts.run_skill("littleskin_texture_extractor", {})
        self.assertFalse(tr.ok)
        self.assertEqual(tr.error, "INVALID_ARGS")

    def test_run_skill_bad_enum_rejected(self):
        tr = dts.run_skill("littleskin_texture_extractor",
                           {"username_or_hash": "Steve", "texture_type": "bogus"})
        self.assertFalse(tr.ok)

    def test_annas_direct_md5(self):
        # 32hex 输入不联网直接构造直链
        tr = dts.run_skill("annas_archive_book_finder",
                           {"query": "b29fd79c4e6d6b25ac82cb39bc80c3a3"})
        self.assertTrue(tr.ok)
        self.assertEqual(tr.data["results"][0]["md5"], "b29fd79c4e6d6b25ac82cb39bc80c3a3")
        self.assertIn("library.lol/main/b29fd79c4e6d6b25ac82cb39bc80c3a3",
                      tr.data["results"][0]["download_candidates"][0])


class TestBiquge(unittest.TestCase):
    """笔趣阁清洗:正文容器/广告行/字号条/标题残留。"""

    def test_clean_strips_ads_and_font_bar(self):
        html = ('<div class="article">正文第一段内容足够长。'
                '<p>请收藏本站:www.biquge7.xyz 无弹窗地址</p>'
                '<p>一秒记住,天才一秒记住本站网址</p>'
                '真正的正文段落要保留下来,而且长度要超过六十个字符才行,'
                '这样才能被判定为有效章节正文内容。</div>'
                '<div class="text">14px 16px 18px 默认 字号选择条</div>')
        out = dts._biquge_clean_chapter(html)
        self.assertIn("正文第一段", out)
        self.assertIn("真正的正文段落", out)
        self.assertNotIn("收藏本站", out)
        self.assertNotIn("biquge7", out)
        self.assertNotIn("天才一秒", out)
        self.assertNotIn("字号", out)  # class=text 字号条不匹配 article 容器

    def test_clean_requires_article_container(self):
        # 只有字号条没有正文容器 → 清洗后应很短(不误把字号条当正文)
        html = '<div class="text">14px 16px 18px 20px 默认 字体</div>'
        out = dts._biquge_clean_chapter(html)
        self.assertLess(len(out), 60)


class TestGdgame(unittest.TestCase):
    """gdgame:网盘/提取码/列表解析。"""

    def test_pans_and_code(self):
        html = ('<p>百度网盘: <a href="https://pan.baidu.com/s/1abcDEF">pan.baidu.com/s/1abcDEF</a></p>'
                '<p>夸克: https://pan.quark.cn/s/xyz123</p>'
                '<p>UC: https://drive.uc.cn/s/uc001?public=1</p>'
                '<p>提取码：1234</p><p>解压密码: ab12</p>')
        pans = dts._gdgame_pans(html)
        provs = {p["provider"] for p in pans}
        self.assertEqual(provs, {"baidu", "quark", "uc"})
        self.assertEqual(dts._gdgame_extract_code(html), "1234")

    def test_parse_list_finds_h2_titles(self):
        html = ('<h2><a href="/n-10/107.html">光与影：33号远征队/Clair Obscur</a></h2>'
                '<h3><a href="/n-1/88.html">哈迪斯2/Hades II</a></h3>')
        rows = dts._parse_gdgame_list(html, "https://gdgame.org/")
        self.assertEqual(len(rows), 2)
        self.assertIn("光与影", rows[0]["title"])
        self.assertTrue(rows[0]["url"].startswith("https://gdgame.org/n-10/107.html"))

    def test_detail_id_url(self):
        # 纯数字 id → 详情 URL 构造逻辑(经 run_skill mock 网络前不可达,仅验证 schema 层接受数字)
        tr = dts.run_skill("gdgame_resource_fetcher", {"target": "1159", "action": "get_game_detail"})
        # 不 mock 网络时会真请求;此测试只保证参数校验通过(结果 ok/false 均可,error 不应是 INVALID_ARGS)
        self.assertNotEqual(tr.error, "INVALID_ARGS")


class TestHaowallpaper(unittest.TestCase):
    def test_image_url_extraction(self):
        html = ('<meta property="og:image" content="https://haowallpaper.com/link/common/file/'
                'previewFileImg/19580921404412800">'
                '<img src="/_nuxt/logo.png">')
        self.assertEqual(dts._haowallpaper_image_url(html, "https://haowallpaper.com/x"),
                         "https://haowallpaper.com/link/common/file/previewFileImg/19580921404412800")

    def test_no_cdn_url(self):
        self.assertIsNone(dts._haowallpaper_image_url("<html><body>no image</body></html>", "https://x/"))


class TestLittleskin(unittest.TestCase):
    def test_uuid_regex(self):
        self.assertTrue(dts._LS_UUID_RE.match("df273bda10b94aa18db345574d5a1e1d"))
        self.assertFalse(dts._LS_UUID_RE.match("Steve"))
        self.assertFalse(dts._LS_UUID_RE.match("df273bda10b94aa18db345574d5a1e1"))

    def test_textures_decode(self):
        # 模拟 sessionserver properties[].value(base64 的 textures JSON)
        payload = json.dumps({"textures": {"SKIN": {"url": "https://littleskin.cn/textures/abc",
                                                    "metadata": {"model": "slim"}},
                                           "CAPE": {"url": "https://littleskin.cn/textures/cape1"}}})
        value = base64.b64encode(payload.encode()).decode()
        profile = {"properties": [{"name": "textures", "value": value}]}
        # 复刻 run_littleskin 的解码核心(无网络)
        with mock.patch.object(dts, "_ls_lookup", return_value=("df273bda10b94aa18db345574d5a1e1d", "Steve")), \
             mock.patch.object(dts, "_http_get") as g:
            resp = mock.MagicMock()
            resp.status_code = 200
            resp.text = json.dumps(profile)
            resp.json.return_value = json.loads(json.dumps(profile))
            g.return_value = resp
            tr = dts.run_skill("littleskin_texture_extractor",
                               {"username_or_hash": "Steve", "texture_type": "all"})
        self.assertTrue(tr.ok)
        self.assertEqual(tr.data["skin_url"], "https://littleskin.cn/textures/abc")
        self.assertEqual(tr.data["cape_url"], "https://littleskin.cn/textures/cape1")
        self.assertEqual(tr.data["skin_model"], "slim")


class TestLibgenParse(unittest.TestCase):
    """libgen 行解析(离线构造 HTML)。"""

    _ROW = (
        '<tr>'
        '<td><a title="Add/Edit : 2026-01-16 | 忘语 - 万相之王" href="edition.php?id=206861021">万相之王</a></td>'
        '<td>忘语</td><td>XX出版社</td><td>2024</td><td>Chinese</td><td>1637</td>'
        '<td>8 MB</td><td>epub</td><td>1 2 3</td>'
        '<td><a href="/ads.php?md5=9ec401a875133b22b86362eb5f2480f3">dl</a></td>'
        '</tr>'
    )

    def test_parse_row(self):
        results = dts._parse_libgen_rows(f"<table>{self._ROW}</table>", "https://libgen.li", "any")
        self.assertEqual(len(results), 1)
        r0 = results[0]
        self.assertEqual(r0["md5"], "9ec401a875133b22b86362eb5f2480f3")
        self.assertEqual(r0["ext"], "epub")
        self.assertIn("万相之王", r0["title"])
        self.assertEqual(r0["detail_url"], "https://libgen.li/ads.php?md5=9ec401a875133b22b86362eb5f2480f3")

    def test_format_filter(self):
        results = dts._parse_libgen_rows(f"<table>{self._ROW}</table>", "https://libgen.li", "pdf")
        self.assertEqual(results, [])


class TestCfDetection(unittest.TestCase):
    def test_cf_marker(self):
        html = '<html><title>Just a moment...</title><div class="cf-chl">challenge</div></html>'
        with self.assertRaises(dts.CloudflareBlockedError):
            dts._check_blocked(html, "mirror")
        dts._check_blocked("<html>正常页面内容足够长</html>", "mirror")  # 不抛


# ================================================================ 在线冒烟
@unittest.skipUnless(LIVE, "在线冒烟: 设 RH_LIVE=1 启用")
class TestLiveSmoke(unittest.TestCase):
    def test_littleskin_steve(self):
        tr = dts.run_skill("littleskin_texture_extractor",
                           {"username_or_hash": "Steve", "texture_type": "skin"})
        self.assertTrue(tr.ok)
        self.assertTrue(tr.data.get("skin_url", "").startswith("https://littleskin.cn/textures/"))

    def test_gdgame_detail(self):
        tr = dts.run_skill("gdgame_resource_fetcher",
                           {"target": "https://gdgame.org/n-1/1159.html", "action": "get_game_detail"})
        self.assertTrue(tr.ok)
        self.assertTrue(tr.data.get("pan_links"))

    def test_haowallpaper(self):
        tr = dts.run_skill("haowallpaper_4k_extractor",
                           {"url_or_id": "19580937584989056", "download": False})
        self.assertTrue(tr.ok)
        self.assertIn("haowallpaper.com", tr.data.get("image_url", ""))

    def test_biquge_sample(self):
        tr = dts.run_skill("biquge_novel_crawler",
                           {"book_url_or_id": "50045", "max_chapters": 6, "export_txt": False})
        self.assertTrue(tr.ok)
        self.assertGreater(tr.data.get("chapters", 0), 0)

    def test_annas_or_libgen(self):
        tr = dts.run_skill("annas_archive_book_finder", {"query": "万相之王"})
        # annas 常被 CF 挡;libgen 兜底可达时 ok;全挡时给 CLOUDFLARE_BLOCKED 语义
        if not tr.ok:
            self.assertIn(tr.error, ("CLOUDFLARE_BLOCKED", "TimeoutError", "ConnectionError"))
        else:
            self.assertTrue(tr.data.get("results"))


if __name__ == "__main__":
    unittest.main()
