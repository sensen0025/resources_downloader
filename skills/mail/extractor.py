"""邮箱验证码提取 — 把原始邮件解析为纯文本,再用关键字正则提取验证码。

纯标准库实现。职责:
1. 解析 MIME(优先 text/plain,回退 text/html,HTML 去标签转文本);
2. 中文/英文两类关键字模式提取 4~10 位数字验证码;
3. 返回结构化 CodeResult(code / source / pattern / context),供 Agent 技能层直接消费。

扩展点:在 PATTERNS 里加正则即可支持更多格式。
"""

from __future__ import annotations

import html.parser
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from typing import Optional

__all__ = [
    "CodeResult",
    "MailMessage",
    "html_to_text",
    "parse_raw_message",
    "extract_verification_code",
    "extract_link",
]


@dataclass
class MailMessage:
    """一封已解析的邮件(扁平文本表示,不保留 MIME 结构)。"""

    subject: str = ""
    from_: str = ""
    date: str = ""
    message_id: str = ""
    body: str = ""
    num: str = ""  # IMAP 消息编号(用于标记已读/删除)
    links: list[str] = field(default_factory=list)  # 正文中的 https 链接(如安全登录链接)


@dataclass
class CodeResult:
    """一次成功的验证码提取结果。"""

    code: str
    source: str  # "subject" | "body"
    pattern: str  # 命中的模式名
    context: str = ""  # 命中位置上下文片段,便于人工核对


# ---------------------------------------------------------------------------
# 邮件文本解析
# ---------------------------------------------------------------------------


def _decode_header_value(value: Optional[str]) -> str:
    """解码邮件头(=?utf-8?B?...?= 等),失败时原样返回。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _decode_bytes(raw: bytes, charset: Optional[str]) -> str:
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _HtmlToText(html.parser.HTMLParser):
    """HTML → 可读纯文本:丢弃 script/style,块级标签处换行;同时收集 <a href> 链接。"""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "td"):
            self._parts.append("\n")
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(raw: str) -> str:
    p = _HtmlToText()
    try:
        p.feed(raw)
    except Exception:
        return raw
    return p.text()


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _collect_links(text: str, existing: list[str]) -> list[str]:
    out = list(existing)
    for m in _URL_RE.findall(text):
        url = m.rstrip(".,;:)]}")
        if url not in out:
            out.append(url)
    return out


def _get_body_parts(msg: Message) -> tuple[str, list[str]]:
    """返回 (正文纯文本, 收集到的链接)。"""
    links: list[str] = []
    if msg.is_multipart():
        plain = None
        html_raw = None
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if ct == "text/plain" and plain is None and payload:
                plain = _decode_bytes(payload, part.get_content_charset())
            elif ct == "text/html" and html_raw is None and payload:
                html_raw = _decode_bytes(payload, part.get_content_charset())
        if html_raw:
            p = _HtmlToText()
            try:
                p.feed(html_raw)
            except Exception:
                pass
            links = _collect_links(html_raw, p.links)
            if plain:
                return plain, _collect_links(plain, links)
            return p.text(), links
        if plain:
            return plain, _collect_links(plain, links)
        return "", links
    ct = msg.get_content_type()
    payload = msg.get_payload(decode=True)
    if payload:
        text = _decode_bytes(payload, msg.get_content_charset())
        if ct == "text/html":
            p = _HtmlToText()
            try:
                p.feed(text)
            except Exception:
                pass
            return p.text(), _collect_links(text, p.links)
        return text, _collect_links(text, links)
    return msg.get_payload() or "", links


def parse_raw_message(raw: bytes, num: str = "") -> MailMessage:
    """把 RFC822 原始字节解析为 MailMessage。

    用 policy.default 解析:兼容 RFC2047 编码词(=?utf-8?B?...?=),
    也能正确解码部分国产邮件服务商发出的「裸 8-bit UTF-8 头」(compat32 会变乱码)。
    """
    msg = message_from_bytes(raw, policy=policy.default)
    body, links = _get_body_parts(msg)
    return MailMessage(
        subject=_decode_header_value(msg.get("Subject")),
        from_=_decode_header_value(msg.get("From")),
        date=msg.get("Date", "") or "",
        message_id=(msg.get("Message-ID", "") or "").strip("<>"),
        body=body,
        num=num,
        links=links,
    )


# ---------------------------------------------------------------------------
# 验证码提取
# ---------------------------------------------------------------------------

_CN_KEY = r"(?:验证码|校验码|确认码|动态码|安全码)"
_EN_KEY = (
    r"(?:verification\s+code|security\s+code|one[- ]?time\s+(?:password|code)"
    r"|passcode|\botp\b|\bcaptcha\b|\bcode\b|\bpin\b)"
)

# 精确模式:关键字 + 可选分隔符 + 数字(数字在关键字之后、相隔不超过几个标点)
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"{_CN_KEY}\s*(?:是|为)?\s*[:：,，。.\s]*\s*([0-9]{{4,10}})"), "中文关键字"),
    (re.compile(rf"{_EN_KEY}\s*(?:is\s+)?[:：]?\s*([0-9]{{4,10}})", re.IGNORECASE), "英文关键字"),
]

# 兜底:整行包含关键字时,取行内第一个独立的 4~8 位数字
_FALLBACK_KEY = re.compile(r"(验证码|校验码|确认码|verification|code|otp|pin)", re.IGNORECASE)
_FALLBACK_CODE = re.compile(r"(?<![\d])([0-9]{4,8})(?![\d年月日号时分秒分钟])")

_SPACE_RUN = re.compile(r"[ \t]+")


def _context(text: str, start: int, end: int, radius: int = 40) -> str:
    s = max(0, start - radius)
    e = min(len(text), end + radius)
    return text[s:e].replace("\n", " ")


def extract_verification_code(
    mail: MailMessage, subject_first: bool = False
) -> Optional[CodeResult]:
    """从 MailMessage 中提取验证码。

    subject_first=True 时先查主题(部分服务把验证码放主题里)。
    未识别到返回 None。
    """
    sources: list[tuple[str, str]] = []
    if mail.body:
        sources.append(("body", _SPACE_RUN.sub(" ", mail.body.strip())))
    if mail.subject:
        sources.append(("subject", _SPACE_RUN.sub(" ", mail.subject.strip())))
    if subject_first:
        sources.reverse()

    for source_name, text in sources:
        # 1) 精确关键字模式
        for rx, pname in PATTERNS:
            m = rx.search(text)
            if m:
                code = m.group(1)
                return CodeResult(
                    code=code,
                    source=source_name,
                    pattern=pname,
                    context=_context(text, m.start(), m.end()),
                )
        # 2) 行内兜底:含关键字的行里取第一个独立数字
        for line in text.splitlines():
            if _FALLBACK_KEY.search(line):
                nums = _FALLBACK_CODE.findall(line)
                if nums:
                    return CodeResult(
                        code=nums[0],
                        source=source_name,
                        pattern="行内兜底(关键字行)",
                        context=line.strip()[:120],
                    )
    return None


def extract_link(mail: MailMessage, hint: str = "") -> Optional[str]:
    """从邮件中提取链接(如 Claude 的「安全登录链接」,无验证码时用)。

    hint: URL 子串过滤,如 "claude.ai" / "verify";不传时返回第一个 https 链接。
    """
    if not mail.links:
        return None
    if not hint:
        return mail.links[0]
    h = hint.lower()
    for url in mail.links:
        if h in url.lower():
            return url
    return None
