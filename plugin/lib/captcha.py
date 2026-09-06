#!/usr/bin/env python3
"""Optional CAPTCHA-solving helpers for resources_downloader (plugin rd-tools).

Reads one JSON request on stdin, writes one JSON result on stdout. Deps are
OPTIONAL and loaded lazily; when missing, actions answer with an actionable
install hint instead of crashing the host.

Actions:
  selfcheck        -> availability of ddddocr / geeked / cv2
  ocr              {image_path}                       -> recognized text (ddddocr)
  slide_gap        {background_path, target_path}     -> gap box on background
  geetest          {captcha_id, risk_type}            -> GeekedTest tokens dict
"""
import json
import sys

# lazy availability
def _avail(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False

def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()

def action_ocr(req):
    if not _avail("ddddocr"):
        return {"ok": False, "error": "ddddocr not installed: run `pip install ddddocr` (optional dep; image-text captcha OCR)"}
    import ddddocr
    with open(req["image_path"], "rb") as f:
        data = f.read()
    text = ddddocr.DdddOcr(show_ad=False).classification(data)
    return {"ok": bool(text), "text": text}

def action_slide_gap(req):
    if not (_avail("ddddocr") and _avail("cv2")):
        return {"ok": False, "error": "ddddocr not installed: run `pip install ddddocr` (optional dep; slider-gap detection needs ddddocr/cv2)"}
    import ddddocr
    with open(req["target_path"], "rb") as f:
        target = f.read()
    with open(req["background_path"], "rb") as f:
        background = f.read()
    slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    res = slide.slide_match(target, background, simple_target=True)
    x1, y1, x2, y2 = res["target"]
    return {"ok": True, "gap": {"x": x1, "y": y1, "x2": x2, "y2": y2}}

def action_geetest(req):
    try:
        from geeked import Geeked
    except Exception:
        return {"ok": False, "error": "geeked not installed: run `pip install git+https://github.com/xKiian/GeekedTest.git` (optional dep; Geetest v4 slide/icon/gobang/ai solver)"}
    g = Geeked(req.get("captcha_id"), risk_type=req.get("risk_type", "slide"))
    try:
        sol = g.solve()
    except Exception as e:
        return {"ok": False, "error": f"geeked solve failed: {e}"}
    return {"ok": bool(sol), "tokens": sol if isinstance(sol, dict) else {"raw": str(sol)}}

def main():
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except Exception as e:
        out({"ok": False, "error": f"bad input: {e}"})
        return
    action = req.get("action")
    try:
        if action == "selfcheck":
            out({"ok": True, "action": action,
                 "ddddocr": _avail("ddddocr"), "cv2": _avail("cv2"),
                 "geeked": _avail("geeked")})
        elif action == "ocr":
            out(action_ocr(req))
        elif action == "slide_gap":
            out(action_slide_gap(req))
        elif action == "geetest":
            out(action_geetest(req))
        else:
            out({"ok": False, "error": f"unknown action: {action}"})
    except Exception as e:
        out({"ok": False, "error": f"{action} failed: {e}"})

if __name__ == "__main__":
    main()
