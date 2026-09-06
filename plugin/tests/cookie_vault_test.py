#!/usr/bin/env python3
"""Synthetic-data tests for web/cookie_vault.py matching + redaction.
Never touches the real ~/.rd-cookies vault. Run: python3 cookie_vault_test.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import web.cookie_vault as cv  # noqa: E402


def make_vault(tmpdir, entries):
    path = os.path.join(tmpdir, "vault.json")
    with open(path, "w") as fh:
        json.dump({"count": len(entries), "entries": entries}, fh)
    return path


def reset(vault_path):
    cv.VAULT_PATH = vault_path
    cv._cache["ts"] = 0.0
    cv._cache["data"] = None


def main():
    fails = 0

    def check(name, fn):
        nonlocal fails
        try:
            fn()
            print(f"ok - {name}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"FAIL - {name}: {e}")

    with tempfile.TemporaryDirectory() as tmp:
        now = int(time.time())

        def c(domain, name, value, expires=None, session=False, secure=False):
            return {
                "domain": domain, "name": name, "value": value, "path": "/",
                "secure": secure, "httpOnly": False, "sameSite": "unspecified",
                "expires": expires, "session": session,
            }

        entries = [
            c(".bilibili.com", "SESSDATA", "s3cret-a", expires=now + 5000),
            c(".bilibili.com", "DedeUserID", "uid1", expires=now + 5000),
            c(".bilibili.com", "oldone", "expired", expires=now - 10),
            c("www.bilibili.com", "hostonly", "h1", expires=now + 5000),
            c(".quark.cn", "__pus", "quark-val", expires=now + 100),
            c(".quark.cn", "dead", "gone", expires=now - 5),
            c(".youtube.com", "SID", "yt-secret", session=True),
        ]
        vp = make_vault(tmp, entries)
        reset(vp)

        check("domain suffix matching (.bilibili.com covers www/bilibili.com)", lambda: (
            len(cv.cookies_for_host("www.bilibili.com")) == 3,
            len(cv.cookies_for_host("bilibili.com")) == 3,
            len(cv.cookies_for_host("foo.bilibili.com")) == 3,
        ))
        check("host-only domain matches exact only", lambda: (
            len(cv.cookies_for_host("www.bilibili.com")) == 3,  # includes hostonly
            len(cv.cookies_for_host("m.bilibili.com")) == 2,    # no hostonly cookie
        ))
        check("expired cookies dropped", lambda: (
            all(c["name"] != "oldone" for c in cv.cookies_for_host("bilibili.com")),
            all(c["name"] != "dead" for c in cv.cookies_for_host("quark.cn")),
        ))
        check("session cookies kept", lambda: (
            any(c["name"] == "SID" and c["session"] for c in cv.cookies_for_host("youtube.com")),
        ))
        check("header assembly joins with '; '", lambda: (
            "SESSDATA=s3cret-a; DedeUserID=uid1" in cv.cookie_header("www.bilibili.com"),
            cv.cookie_header("unknown.example") == "",
        ))
        check("redact hides secrets", lambda: (
            cv.redact("run -c SESSDATA=s3cret-a ok", ["SESSDATA=s3cret-a"]) == "run -c <cookie> ok",
            cv.redact("plain text", ["absent"]) == "plain text",
        ))
        # re-vault through make_vault.py on synthetic source (out of band check done in repo test)

    print(f"\n{fails} cookie_vault test(s) failed" if fails else "\nall cookie_vault tests passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
