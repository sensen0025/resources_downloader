# Private cookie-vault access for the task engine (LOCAL ONLY — never commit data).
# Reads ~/.rd-cookies/vault.json (env RD_COOKIE_DIR overrides) and matches cookies
# to hosts. Cookie values are returned to the caller for building tool args and
# MUST be masked before ever being written into task logs / DB / SSE.
import json
import os
import time

DEFAULT_DIR = os.path.expanduser("~/.rd-cookies")
COOKIE_DIR = os.environ.get("RD_COOKIE_DIR", DEFAULT_DIR)
VAULT_PATH = os.path.join(COOKIE_DIR, "vault.json")

_cache = {"ts": 0.0, "data": None}


def _load(ttl=5.0):
    """Return list of cookie dicts; empty on any failure. Cached briefly."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < ttl:
        return _cache["data"]
    try:
        with open(VAULT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh).get("entries", [])
        _cache.update(ts=now, data=data)
        return data
    except Exception:
        _cache.update(ts=now, data=[])
        return []


def _domain_matches(cookie_domain: str, host: str) -> bool:
    """Cookie domain semantics: '.bilibili.com' matches bilibili.com + subdomains;
    a bare host-only domain ('www.x.com') matches only itself."""
    d = cookie_domain.strip().lower()
    h = host.strip().lower()
    if not d or not h:
        return False
    if d.startswith("."):
        d = d[1:]
        return h == d or h.endswith("." + d)
    return h == d


def cookies_for_host(host: str):
    """Cookies matching host, session-cookie safe, un-expired. No path filtering
    (callers pass the request path if they care)."""
    now = int(time.time())
    return [
        c for c in _load()
        if _domain_matches(c.get("domain", ""), host)
        and (c.get("session") or (c.get("expires") or 0) > now)
    ]


def cookie_header(host: str) -> str:
    """'name=value; name2=value2' Cookie header string for host (empty if none)."""
    parts = [f"{c['name']}={c['value']}" for c in cookies_for_host(host)]
    return "; ".join(parts)


def redact(text: str, secrets) -> str:
    """Redact a list of secret strings from a log line."""
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "<cookie>")
    return out
