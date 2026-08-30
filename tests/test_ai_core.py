"""AgentCore 循环单测 — mock LLM 驱动,确定性验证决策/校验/状态/停止机制。"""

import json
import unittest

from ai.core import AgentCore, AgentResult, HarnessConfig
from ai.magic import sniff_bytes, sniff_file
from skills.core import get_registry


class FakeLLM:
    """脚本化 LLM:按调用次数返回预设决策;可注入观察历史的钩子。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, history, **kw):
        self.calls.append(list(history))
        if self.script:
            return json.dumps(self.script.pop(0), ensure_ascii=False)
        return json.dumps({"done": True, "success": False, "summary": "无更多决策"})


def _core(llm) -> AgentCore:
    cfg = HarnessConfig(max_steps=10, stall_limit=2, llm=llm)
    return AgentCore(cfg)


def _patch_intent_and_research(reg=None, file_path="downloads/x.litematic"):
    """把联网技能换成测试桩(保持循环机制被测,不碰真实网络/LLM)。"""
    from skills.core import ToolResult

    reg = reg or get_registry()

    def fake_intent(request, ctx=None):
        from ai.state import ResourceIntent

        intent = ResourceIntent(query=request, kind="schematic",
                                preferred_exts=(".litematic",),
                                accept_exts=(".schematic", ".zip"), binding="strict")
        if ctx and ctx.state:
            ctx.state.intent = intent
        return ToolResult.success("意图: strict litematic", data=intent.to_dict())

    def fake_research(query, out_dir="downloads", max_candidates=6, ctx=None):
        return ToolResult.success(f"下载成功: {file_path}",
                                  data={"files": [file_path], "path": file_path})

    reg._tools["intent_parse"].handler = fake_intent
    reg._tools["batch_research"].handler = fake_research


class TestAgentCoreLoop(unittest.TestCase):
    def test_full_success_flow(self):
        _patch_intent_and_research()
        # 脚本: 意图 → 粗扫 → done(success)
        llm = FakeLLM([
            {"thought": "先解析意图", "skill": "intent_parse",
             "args": {"request": "故宫投影,只要 litematic"}},
            {"thought": "粗扫", "skill": "batch_research",
             "args": {"query": "故宫 投影 litematic", "out_dir": "downloads"}},
            {"done": True, "success": True, "summary": "下载完成"},
        ])
        core = _core(llm)
        result = core.run("故宫投影,只要 litematic")
        self.assertTrue(result.success)
        # 意图解析结果写入状态
        self.assertEqual(core.state.intent.binding, "strict")
        self.assertEqual(core.state.intent.preferred_exts, (".litematic",))
        # 审计记录完整
        skills_used = [d["skill"] for d in result.decisions]
        self.assertIn("intent_parse", skills_used)
        self.assertIn("batch_research", skills_used)
        self.assertIn("downloads/x.litematic", result.files)

    def test_unknown_skill_rejected_and_recovers(self):
        llm = FakeLLM([
            {"skill": "nonexistent_skill", "args": {}},
            {"done": True, "success": False, "summary": "放弃"},
        ])
        core = _core(llm)
        result = core.run("测试")
        self.assertFalse(result.success)
        # 第一条被拒后 LLM 收到"未知技能"反馈
        second_user = llm.calls[1][-1]["content"]
        self.assertIn("未知技能", second_user)

    def test_stall_detection(self):
        llm = FakeLLM([
            {"skill": "verify_file", "args": {"path": "a"}},
            {"skill": "verify_file", "args": {"path": "a"}},
            {"skill": "verify_file", "args": {"path": "a"}},
        ])
        core = _core(llm)
        result = core.run("测试")
        self.assertEqual(result.error, "STALLED")
        self.assertFalse(result.success)

    def test_done_without_files_rejected(self):
        # 模型说成功但没文件 → 诚实校验拒绝
        llm = FakeLLM([{"done": True, "success": True, "summary": "搞定了"}])
        core = _core(llm)
        result = core.run("测试")
        self.assertFalse(result.success)
        self.assertIn("无文件落地", result.summary)

    def test_invalid_args_feedback(self):
        # verify_file 缺必填 path → 收到参数不合法反馈,模型自纠后 done
        llm = FakeLLM([
            {"skill": "verify_file", "args": {}},  # 缺 path
            {"done": True, "success": False, "summary": "参数错了,放弃"},
        ])
        core = _core(llm)
        result = core.run("测试")
        self.assertFalse(result.success)
        second = llm.calls[1][-1]["content"]
        self.assertIn("不合法", second)


class TestMagicSniff(unittest.TestCase):
    def test_known_magic(self):
        self.assertEqual(sniff_bytes(b"\x89PNG\r\n\x1a\nrest")["kind"], "png")
        self.assertEqual(sniff_bytes(b"\xff\xd8\xff\xe0junk")["kind"], "jpg")
        self.assertEqual(sniff_bytes(b"%PDF-1.7...")["kind"], "pdf")
        self.assertEqual(sniff_bytes(b"PK\x03\x04....")["kind"], "zip")
        self.assertEqual(sniff_bytes(b"\x1f\x8b\x08\x00....")["kind"], "gzip")
        self.assertEqual(sniff_bytes(b"\x0a\x00\x00rest")["kind"], "nbt")

    def test_unknown(self):
        self.assertIsNone(sniff_bytes(b"\x00\x01\x02\x03\x04random"))

    def test_webp_special(self):
        self.assertEqual(sniff_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8 ")["kind"], "webp")
        # RIFF 但非 WEBP → 不误判
        self.assertIsNone(sniff_bytes(b"RIFF\x10\x00\x00\x00AVI LIST"))

    def test_audio_magic(self):
        self.assertEqual(sniff_bytes(b"fLaC\x00\x00\x00\x22rest")["kind"], "flac")
        self.assertEqual(sniff_bytes(b"ID3\x04\x00\x00\x00rest")["kind"], "mp3")
        # RIFF + WAVE 标记 → wav(与 WebP 同容器区分)
        self.assertEqual(sniff_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")["kind"], "wav")

    def test_document_magic(self):
        self.assertEqual(sniff_bytes(b"BOOKMOBI\x00\x00rest")["kind"], "mobi")
        self.assertEqual(sniff_bytes(b"AT&TFORM\x00rest")["kind"], "djvu")

    def test_sniff_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"%PDF-1.7 fake pdf content")
            p = f.name
        info = sniff_file(p)
        self.assertEqual(info["kind"], "pdf")


class TestSkillRegistration(unittest.TestCase):
    def test_ai_skills_registered(self):
        reg = get_registry()
        for name in ("intent_parse", "search", "verify_file", "inspect_archive", "batch_research"):
            self.assertTrue(reg.has(name), f"{name} 未注册")


class TestVerifyFileContent(unittest.TestCase):
    """verify_file 的内容级验证:格式对了不代表内容对(营业执照也是合法 JPEG)。"""

    def _verify(self, verdict_ok, subject="Minecraft 皮肤"):
        import tempfile
        from unittest import mock

        from ai.skills import _verify_file_tool

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0 fake jpeg bytes")
            p = f.name
        with mock.patch("ai.vision.verify_image_content",
                        return_value={"ok": verdict_ok, "content": "营业执照", "reason": "内容为证件"}):
            r = _verify_file_tool(p, expected_kind="jpg", expected_subject=subject)
        return r

    def test_wrong_content_rejected(self):
        # 视觉判定内容不符(执照冒充皮肤)→ 验证失败,即使格式是合法 JPEG
        r = self._verify(verdict_ok=False)
        self.assertFalse(r.ok)
        self.assertIn("内容不符", r.message)

    def test_matching_content_passed(self):
        r = self._verify(verdict_ok=True)
        self.assertTrue(r.ok)
        self.assertIn("内容验证通过", r.message)

    def test_vision_unavailable_not_blocking(self):
        # 视觉不可用(ok=None) → 不阻塞,维持格式验证结果
        r = self._verify(verdict_ok=None)
        self.assertTrue(r.ok)


class TestVerifyFileFamilies(unittest.TestCase):
    """verify_file 格式族匹配:音频/视频/文档等期望类型不再误判(回归:周杰伦 FLAC 案例)。"""

    @staticmethod
    def _verify(data: bytes, suffix: str, expected_kind: str):
        import tempfile

        from ai.skills import _verify_file_tool

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            p = f.name
        return _verify_file_tool(p, expected_kind=expected_kind)

    def test_flac_audio_passes(self):
        """回归:真实 FLAC 文件头 + expected_kind=audio → 通过(此前误判为未知)。"""
        r = self._verify(b"fLaC\x00\x00\x00\x22" + b"\x00" * 64, ".flac", "audio")
        self.assertTrue(r.ok)
        self.assertIn("flac", r.data["magic_kind"])

    def test_wav_audio_passes(self):
        r = self._verify(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32, ".wav", "audio")
        self.assertTrue(r.ok)
        self.assertEqual(r.data["magic_kind"], "wav")

    def test_mp3_audio_passes(self):
        r = self._verify(b"ID3\x04\x00\x00\x00" + b"\x00" * 32, ".mp3", "audio")
        self.assertTrue(r.ok)

    def test_audio_expected_but_image_fails(self):
        r = self._verify(b"\xff\xd8\xff\xe0 fake jpeg", ".jpg", "audio")
        self.assertFalse(r.ok)
        self.assertIn("期望 audio", r.message)

    def test_video_media_passes(self):
        r = self._verify(b"\x1a\x45\xdf\xa3" + b"\x00" * 32, ".mkv", "video")
        self.assertTrue(r.ok)

    def test_epub_document_passes(self):
        # epub/docx 是 zip 外衣 → document 族认 zip
        r = self._verify(b"PK\x03\x04" + b"\x00" * 32, ".epub", "document")
        self.assertTrue(r.ok)

    def test_exact_kind_matches(self):
        r = self._verify(b"fLaC\x00\x00\x00\x22" + b"\x00" * 16, ".flac", "flac")
        self.assertTrue(r.ok)


if __name__ == "__main__":
    unittest.main()
