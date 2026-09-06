#!/usr/bin/env python3
"""Generic browser worker for resources_downloader (plugin rd-tools).

Persistent mode (env RD_BROWSER_PERSIST=1): reads one JSON request per line on
stdin and answers one JSON result per line, keeping the browser context alive
between requests (cookies + the open page survive). profile_dir may change
between requests; the context is restarted then. Non-persistent mode keeps the
old single-shot behaviour.

Actions: open | act | eval | cookies | download | screenshot | solve | close | selfcheck
act steps: click | fill | press | wait | wait_selector | screenshot | shot (element shot) | drag | eval
solve kinds: ocr | slider | geetest  (delegates to captcha.py via subprocess)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    PW_OK = True
    PW_ERR = ""
except Exception as e:  # pragma: no cover
    PW_OK = False
    PW_ERR = str(e)

CAPTCHA_PY = str(Path(__file__).resolve().parent / "captcha.py")
BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]

def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()

def text_of(page, limit=60000):
    try:
        t = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        t = ""
    return (t or "")[:limit]

def run_captcha(req):
    p = subprocess.run([sys.executable, "-u", CAPTCHA_PY], input=json.dumps(req),
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    if not lines:
        return {"ok": False, "error": f"captcha worker empty output: {p.stderr[-400:]}"}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"ok": False, "error": f"captcha worker bad json: {lines[-1][-300:]}"}

class Runner:
    def __init__(self, headless=True):
        self._pw = None
        self.headless = headless
        self.ctx = None
        self.page = None
        self.profile = None

    def start(self, profile_dir):
        if profile_dir == self.profile and self.ctx is not None:
            return  # reuse
        self.stop()
        if not PW_OK:
            raise RuntimeError("playwright not installed: run `pip install playwright && playwright install chromium` (python) on the DSH host")
        self._pw = sync_playwright().start()
        kwargs = {"headless": self.headless, "args": BROWSER_ARGS,
                  "viewport": {"width": 1366, "height": 900}, "accept_downloads": True}
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(profile_dir, **kwargs)
        except Exception:
            for channel in ("chrome", "msedge"):
                try:
                    self.ctx = self._pw.chromium.launch_persistent_context(profile_dir, channel=channel, **kwargs)
                    break
                except Exception:
                    continue
            else:
                raise
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.profile = profile_dir

    def stop(self):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
            self.ctx = None
            self.page = None
            self.profile = None

def handle(runner, req):
    action = req.get("action")
    if action == "selfcheck":
        return {"ok": PW_OK, "action": action,
                "result": "playwright available" if PW_OK else PW_ERR}
    profile_dir = req.get("profile_dir")
    if not profile_dir and action != "close":
        return {"ok": False, "action": action, "error": "missing profile_dir"}
    if profile_dir:
        runner.start(profile_dir)
    page = runner.page
    if action == "open":
        page.goto(req["url"], wait_until=req.get("wait_until", "domcontentloaded"),
                  timeout=req.get("timeout_ms", 60000))
        ms = req.get("wait_ms") or req.get("cf_wait_ms") or 0
        if ms:
            page.wait_for_timeout(ms)
        return {"ok": True, "action": action, "url": page.url,
                "title": (page.title() or "")[:500], "text": text_of(page)}
    if action == "act":
        return act_steps(page, req)
    if action == "eval":
        res = page.evaluate(req["expression"])
        if isinstance(res, (dict, list)):
            res = json.dumps(res, ensure_ascii=False, default=str)
        return {"ok": True, "action": action, "result": str(res)[:60000]}
    if action == "cookies":
        cookies = runner.ctx.cookies()
        header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        return {"ok": True, "action": action, "cookie_header": header, "count": len(cookies)}
    if action == "download":
        out_dir = req.get("out_dir") or tempfile.gettempdir()
        if req.get("selector"):
            with page.expect_download(timeout=req.get("timeout_ms", 60000)) as dl:
                page.locator(req["selector"]).first.click(timeout=req.get("timeout_ms", 60000))
            download = dl.value
        elif req.get("url"):
            with page.expect_download(timeout=req.get("timeout_ms", 60000)) as dl:
                page.goto(req["url"], timeout=req.get("timeout_ms", 60000))
            download = dl.value
        else:
            return {"ok": False, "action": action, "error": "download needs selector or url"}
        name = re.sub(r'[\\/:*?"<>|]', "_", download.suggested_filename or "download.bin")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, name)
        download.save_as(path)
        return {"ok": True, "action": action, "path": path, "file_name": name,
                "size": os.path.getsize(path)}
    if action == "screenshot":
        page.screenshot(path=req["path"], full_page=req.get("full_page", True))
        return {"ok": True, "action": action, "path": req["path"]}
    if action == "solve":
        return solve_captcha(page, runner, req)
    if action == "close":
        runner.stop()
        return {"ok": True, "action": action}
    return {"ok": False, "action": action, "error": f"unknown action: {action}"}

def act_steps(page, req):
    steps = req.get("steps") or []
    shot_path = req.get("shot_path")
    eval_result = ""
    for s in steps:
        kind = s.get("action")
        try:
            if kind == "click":
                page.locator(s["selector"]).first.click(timeout=s.get("timeout_ms", 10000))
            elif kind == "fill":
                page.locator(s["selector"]).first.fill(s.get("value", ""), timeout=s.get("timeout_ms", 10000))
            elif kind == "press":
                page.keyboard.press(s.get("key") or s.get("value") or "Enter")
            elif kind == "wait":
                page.wait_for_timeout(int(s.get("ms") or s.get("value") or 1000))
            elif kind == "wait_selector":
                page.wait_for_selector(s["selector"], state="visible", timeout=s.get("timeout_ms", 15000))
            elif kind == "screenshot":
                page.screenshot(path=shot_path or s.get("path"), full_page=True)
            elif kind == "shot":
                page.locator(s["selector"]).first.screenshot(path=s["path"])
            elif kind == "drag":
                dx = int(s.get("dx") or s.get("value") or 0)
                drag_by(page, s["selector"], dx, s.get("duration", 500))
                page.wait_for_timeout(int(s.get("wait_after") or 0))
            elif kind == "eval":
                res = page.evaluate(s["expression"])
                if isinstance(res, (dict, list)):
                    res = json.dumps(res, ensure_ascii=False, default=str)
                eval_result = str(res)[:60000]
        except Exception as e:
            return {"ok": False, "action": "act", "step": kind, "error": str(e)[:400],
                    "url": page.url, "title": (page.title() or "")[:300],
                    "text": text_of(page, 20000)}
    return {"ok": True, "action": "act", "url": page.url,
            "title": (page.title() or "")[:500], "text": text_of(page),
            "result": eval_result, "shot_path": shot_path or ""}

def drag_by(page, selector, dx, duration_ms=500):
    box = page.locator(selector).first.bounding_box()
    if not box:
        raise RuntimeError(f"drag: no bounding box for {selector}")
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    steps = max(5, int(duration_ms / 20))
    for i in range(1, steps + 1):
        page.mouse.move(start_x + dx * (i / steps), start_y + ((i % 2) * 0.8))
        page.wait_for_timeout(12)
    page.mouse.move(start_x + dx, start_y)
    page.mouse.up()

def solve_captcha(page, runner, req):
    kind = req.get("kind")
    tmp = req.get("tmp_dir") or tempfile.gettempdir()
    os.makedirs(tmp, exist_ok=True)
    if kind == "ocr":
        img = req.get("img_selector")
        inp = req.get("input_selector")
        if not img:
            return {"ok": False, "action": "solve", "error": "ocr needs img_selector"}
        path = os.path.join(tmp, f"cap_{os.getpid()}.png")
        page.locator(img).first.screenshot(path=path)
        r = run_captcha({"action": "ocr", "image_path": path})
        if not r.get("ok"):
            return {"ok": False, "action": "solve", "kind": kind, "error": r.get("error", "ocr failed")}
        if inp:
            page.locator(inp).first.fill(r["text"], timeout=8000)
        return {"ok": True, "action": "solve", "kind": kind, "result": r["text"]}
    if kind == "slider":
        bg, tg = req.get("bg_selector"), req.get("target_selector")
        handle = req.get("handle_selector")
        if not (bg and handle):
            return {"ok": False, "action": "solve", "kind": kind, "error": "slider needs bg_selector and handle_selector"}
        bgp = os.path.join(tmp, f"bg_{os.getpid()}.png")
        page.locator(bg).first.screenshot(path=bgp)
        if tg:
            tpp = os.path.join(tmp, f"tp_{os.getpid()}.png")
            page.locator(tg).first.screenshot(path=tpp)
        else:
            return {"ok": False, "action": "solve", "kind": kind,
                    "error": "slider needs target_selector (the small puzzle piece image)"}
        r = run_captcha({"action": "slide_gap", "background_path": bgp, "target_path": tpp})
        if not r.get("ok"):
            return {"ok": False, "action": "solve", "kind": kind, "error": r.get("error", "gap detect failed")}
        drag_by(page, handle, int(r["gap"]["x"]), req.get("duration", 700))
        page.wait_for_timeout(int(req.get("wait_after") or 2500))
        return {"ok": True, "action": "solve", "kind": kind, "result": f"dragged {r['gap']['x']}px"}
    if kind == "geetest":
        r = run_captcha({"action": "geetest", "captcha_id": req.get("captcha_id"),
                         "risk_type": req.get("risk_type", "slide")})
        if not r.get("ok"):
            return {"ok": False, "action": "solve", "kind": kind, "error": r.get("error", "geetest failed")}
        return {"ok": True, "action": "solve", "kind": kind, "result": json.dumps(r.get("tokens"), ensure_ascii=False)}
    return {"ok": False, "action": "solve", "error": f"unknown solve kind: {kind}"}

def main():
    persistent = os.environ.get("RD_BROWSER_PERSIST") == "1"
    runner = Runner(headless=os.environ.get("RD_HEADLESS", "1") != "0")
    if not persistent:
        raw = sys.stdin.read()
        try:
            req = json.loads(raw)
        except Exception as e:
            out({"ok": False, "action": "?", "error": f"bad input: {e}"})
            return
        try:
            out(handle(runner, req))
        except Exception as e:
            out({"ok": False, "action": req.get("action"), "error": str(e)[:800]})
        finally:
            try:
                runner.stop()
            except Exception:
                pass
        return
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            out({"ok": False, "error": f"bad input: {e}"})
            continue
        try:
            out(handle(runner, req))
            if req.get("action") == "close":
                return
        except Exception as e:
            out({"ok": False, "action": req.get("action"), "error": str(e)[:800]})
    runner.stop()

if __name__ == "__main__":
    main()
