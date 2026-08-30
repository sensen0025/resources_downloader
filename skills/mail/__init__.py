"""邮箱收码技能 — 对外 API。

    from skills.mail import ImapMailbox, extract_verification_code
"""

from .extractor import (
    CodeResult,
    MailMessage,
    extract_link,
    extract_verification_code,
    parse_raw_message,
)
from .imap_client import ImapMailbox

__all__ = [
    "CodeResult",
    "MailMessage",
    "ImapMailbox",
    "extract_link",
    "extract_verification_code",
    "parse_raw_message",
]
__version__ = "0.1.0"
