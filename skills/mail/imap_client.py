"""IMAP 邮箱轮询客户端 — 「收验证码」技能的核心。

纯标准库(imaplib)。典型用法(Agent 的 read_mail 技能):

    from skills.mail.imap_client import ImapMailbox

    mb = ImapMailbox(host="imap.qq.com", email="me@qq.com", password="授权码")
    result = mb.wait_for_code(sender_hint="github.com", timeout=120)
    if result:
        print(result.code)   # 例如 "123456"

特性:
- SSL(993)与 STARTTLS(143)两种连接;
- 断线自动重连;
- wait_for_code:带预算的轮询,支持按发件人过滤 + 去重;
- 全部返回结构化 MailMessage / CodeResult,方便上层技能消费。
"""

from __future__ import annotations

import imaplib
import ssl
import time
from typing import Optional

from .extractor import CodeResult, MailMessage, extract_verification_code, parse_raw_message

__all__ = ["ImapMailbox"]


class ImapMailbox:
    def __init__(
        self,
        host: str,
        email: str,
        password: str,
        port: int = 993,
        folder: str = "INBOX",
        use_ssl: bool = True,
        starttls: bool = False,
        timeout: int = 30,
    ) -> None:
        self.host = host
        self.email = email
        self.password = password
        self.port = port
        self.folder = folder
        self.use_ssl = use_ssl
        self.starttls = starttls
        self.timeout = timeout
        self.conn: Optional[imaplib.IMAP4] = None

    # ------------------------------------------------------------------ 连接

    def _connect(self) -> None:
        if self.use_ssl:
            self.conn = imaplib.IMAP4_SSL(
                self.host, self.port, ssl_context=ssl.create_default_context(), timeout=self.timeout
            )
        else:
            self.conn = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
            if self.starttls:
                self.conn.starttls(ssl.create_default_context())
        self.conn.login(self.email, self.password)
        typ, resp = self.conn.select(self.folder)
        if typ != "OK":
            raise ConnectionError(f"无法打开 IMAP 文件夹 {self.folder!r}: {resp}")

    def ensure_connected(self) -> None:
        """惰性连接;已连接则用 NOOP 探测,失效自动重连。"""
        if self.conn is None:
            self._connect()
            return
        try:
            typ, _ = self.conn.noop()
            if typ == "OK":
                return
        except Exception:
            pass
        self.close()
        self._connect()

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
        self.conn = None

    def __enter__(self) -> "ImapMailbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ 读取

    def fetch_unseen(self, mark_seen: bool = False) -> list[MailMessage]:
        """拉取未读邮件。mark_seen=True 时拉取后立即标记已读。"""
        return self._search("UNSEEN", mark_seen=mark_seen)

    def fetch_all(self, mark_seen: bool = False) -> list[MailMessage]:
        """拉取整个文件夹(调试用)。"""
        return self._search("ALL", mark_seen=mark_seen)

    def _search(self, criteria: str, mark_seen: bool) -> list[MailMessage]:
        self.ensure_connected()
        assert self.conn is not None
        typ, data = self.conn.search(None, criteria)
        if typ != "OK":
            raise ConnectionError(f"IMAP 搜索失败({criteria}): {data}")
        nums = (data[0] or b"").split()
        mails: list[MailMessage] = []
        for raw_num in nums:
            num = raw_num.decode("ascii", errors="ignore")
            mail = self._fetch_one(num)
            if mail is None:
                continue
            if mark_seen:
                self.mark_seen(num)
            mails.append(mail)
        return mails

    def _fetch_one(self, num: str) -> Optional[MailMessage]:
        try:
            typ, data = self.conn.fetch(num, "(RFC822)")  # type: ignore[union-attr]
        except Exception:
            return None
        if typ != "OK" or not data or data[0] is None:
            return None
        entry = data[0]
        if isinstance(entry, tuple):
            raw = entry[1]
        elif isinstance(entry, bytes):
            raw = entry
        else:
            return None
        return parse_raw_message(raw, num=num)

    def mark_seen(self, num: str) -> None:
        try:
            self.conn.store(num, "+FLAGS", "\\Seen")  # type: ignore[union-attr]
        except Exception:
            pass

    # ------------------------------------------------------------------ 等待

    def wait_for_code(
        self,
        sender_hint: Optional[str] = None,
        timeout: float = 180,
        poll_interval: float = 5,
        mark_seen: bool = True,
        subject_first: bool = False,
    ) -> Optional[CodeResult]:
        """轮询等待一封「含验证码」的邮件,带预算(超时返回 None)。

        sender_hint: 发件人过滤关键字(域名/邮箱/主题子串),如 "github.com"。
        mark_seen:   提取成功后把邮件标记为已读(消费语义)。
        """
        deadline = time.monotonic() + timeout
        seen: set[str] = set()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                for mail in self.fetch_unseen(mark_seen=False):
                    key = mail.message_id or f"{mail.from_}|{mail.subject}|{mail.date}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if sender_hint and not _matches_hint(mail, sender_hint):
                        continue
                    result = extract_verification_code(mail, subject_first=subject_first)
                    if result is None:
                        continue
                    if mark_seen:
                        self.mark_seen(mail.num)
                    return result
            except Exception:
                self.ensure_connected()
            time.sleep(min(poll_interval, max(remaining, 0.5)))


def _matches_hint(mail: MailMessage, hint: str) -> bool:
    h = hint.lower()
    return h in mail.from_.lower() or h in mail.subject.lower() or h in (mail.date or "").lower()
