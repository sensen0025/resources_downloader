"""账号场景包。"""

from .claude import CLAUDE_LOGIN, CLAUDE_REGISTER
from .tripo import TRIPO_LOGIN, TRIPO_REGISTER

SCENARIOS = {
    "claude-register": CLAUDE_REGISTER,
    "claude-login": CLAUDE_LOGIN,
    "tripo-register": TRIPO_REGISTER,
    "tripo-login": TRIPO_LOGIN,
}

__all__ = ["CLAUDE_LOGIN", "CLAUDE_REGISTER", "TRIPO_LOGIN", "TRIPO_REGISTER", "SCENARIOS"]
