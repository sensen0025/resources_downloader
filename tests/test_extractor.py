"""extractor / parse_raw_message 单元测试(纯标准库 unittest)。"""

import unittest
from pathlib import Path

from skills.mail.extractor import (
    extract_link,
    extract_verification_code,
    parse_raw_message,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestParsing(unittest.TestCase):
    def _mail(self, name: str):
        raw = (FIXTURES / name).read_bytes()
        return parse_raw_message(raw)

    def test_cn_plain(self):
        m = self._mail("cn_plain.eml")
        self.assertIn("验证码", m.subject)
        r = extract_verification_code(m)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.code, "123456")
        self.assertEqual(r.source, "body")
        self.assertEqual(r.pattern, "中文关键字")

    def test_en_html(self):
        m = self._mail("en_html.eml")
        self.assertNotIn("<", m.body)  # HTML 已去标签
        self.assertIn("verification code", m.body.lower())
        r = extract_verification_code(m)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.code, "654321")
        self.assertEqual(r.source, "body")
        self.assertEqual(r.pattern, "英文关键字")

    def test_subject_code(self):
        m = self._mail("subject_code.eml")
        r = extract_verification_code(m)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.code, "888888")
        self.assertEqual(r.source, "subject")

    def test_scattered_fallback(self):
        """关键字与数字相隔一段文字 → 走行内兜底。"""
        m = self._mail("scattered.eml")
        r = extract_verification_code(m)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.code, "123456")
        self.assertEqual(r.pattern, "行内兜底(关键字行)")

    def test_no_code(self):
        m = self._mail("no_code.eml")
        self.assertIsNone(extract_verification_code(m))

    def test_subject_first_flag(self):
        m = self._mail("subject_code.eml")
        r = extract_verification_code(m, subject_first=True)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.source, "subject")

    def test_secure_link(self):
        """Claude 类邮件:无验证码,但有安全登录链接(HTML href)。"""
        m = self._mail("secure_link.eml")
        self.assertIsNone(extract_verification_code(m))
        link = extract_link(m)
        self.assertIsNotNone(link)
        assert link is not None
        self.assertTrue(link.startswith("https://claude.ai/magic-link"), link)
        link_hint = extract_link(m, hint="claude.ai")
        self.assertEqual(link_hint, link)


if __name__ == "__main__":
    unittest.main()
