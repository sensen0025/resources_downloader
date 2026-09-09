# Per-IP submission quota for the download API (no auth; everyone incl. the
# web console is rate-limited because every submit burns an LLM agent run).
#
# Windows (per client IP):
#   1 hour <= RD_SUBMIT_HOURLY  (default 10)
#   1 day  <= RD_SUBMIT_DAILY   (default 25)
#   7 days <= RD_SUBMIT_WEEKLY  (default 50)
#
# History is persisted in the same sqlite DB so a restart does not reset it.
import os
import sqlite3
import time

from web.task_engine import DB_PATH  # noqa: E402  (same DB as tasks)

HOUR = 3600
DAY = 86400
WEEK = 604800

LIMITS = {
    "hourly": int(os.environ.get("RD_SUBMIT_HOURLY", "10")),
    "daily": int(os.environ.get("RD_SUBMIT_DAILY", "25")),
    "weekly": int(os.environ.get("RD_SUBMIT_WEEKLY", "50")),
}

_cursor_lock = None  # sqlite connections are per-call below; no shared lock needed


def init_quota_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS submit_log (
            ip TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_submit_log ON submit_log(ip, ts)")
    conn.commit()
    conn.close()


def _count_since(ip: str, since: float) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM submit_log WHERE ip=? AND ts>?", (ip, since))
        return int(c.fetchone()[0])
    finally:
        conn.close()


def _prune(ip: str):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("DELETE FROM submit_log WHERE ip=? AND ts<=?", (ip, time.time() - WEEK))
        conn.commit()
    finally:
        conn.close()


def record_submit(ip: str):
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("INSERT INTO submit_log (ip, ts) VALUES (?, ?)", (ip, time.time()))
        conn.commit()
    finally:
        conn.close()


def usage(ip: str) -> dict:
    now = time.time()
    return {
        "hourly": _count_since(ip, now - HOUR),
        "daily": _count_since(ip, now - DAY),
        "weekly": _count_since(ip, now - WEEK),
        "limits": dict(LIMITS),
    }


def check_and_record(ip: str) -> tuple[bool, dict]:
    """Return (allowed, info). When not allowed, info contains retry_after_sec."""
    _prune(ip)
    u = usage(ip)
    windows = (
        ("hourly", HOUR, u["limits"]["hourly"], u["hourly"]),
        ("daily", DAY, u["limits"]["daily"], u["daily"]),
        ("weekly", WEEK, u["limits"]["weekly"], u["weekly"]),
    )
    for name, span, limit, used in windows:
        if used >= limit:
            return False, {**u, "blocked_window": name, "retry_after_sec": _retry_after(ip, span)}
    record_submit(ip)
    return True, u


def _retry_after(ip: str, span: float) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("SELECT ts FROM submit_log WHERE ip=? AND ts>? ORDER BY ts ASC LIMIT 1",
                  (ip, time.time() - span))
        row = c.fetchone()
        if not row:
            return 60
        return max(1, int(row[0] + span - time.time()))
    finally:
        conn.close()


init_quota_db()
