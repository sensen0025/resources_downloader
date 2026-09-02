#!/usr/bin/env python3
"""rh_bridge — Resource Hub × DSH 桥接封装。

把 Resource Hub FastAPI(/api/v1/tasks)封装成结构化子命令,供 DSH agent
用 pwsh/一行命令调用:提交任务、轮询进度、拿文件下载链接。

用法:
    python rh_bridge.py fetch   "凡人修仙传 第10集" [--file-types .mp4] [--base http://127.0.0.1:8000] [--timeout 1800]
    python rh_bridge.py submit  "韩立大战天哭 第4集"          # 只提交,返回 task_id
    python rh_bridge.py poll    <task_id> [--timeout 300]    # 轮询到终态
    python rh_bridge.py files   <task_id>                    # 打印文件与下载 URL
    python rh_bridge.py cancel  <task_id>

输出一律 JSON(agent 友好)。任务在 DSH 侧只是"发起者+观察者",
资源获取的确定性执行(检索/分析/闸门/下载)全在 Resource Hub 管线里。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:8000"


def _req(base: str, method: str, path: str, body: dict | None = None,
         timeout: float = 30) -> dict:
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:2000]}


def _ok(d: dict) -> dict:
    """统一解包 {code:0,data:...},code!=0 抛错。"""
    if d.get("code") != 0:
        raise RuntimeError(f"API error code={d.get('code')}: {d.get('message') or d}")
    return d.get("data") or {}


def _poll_until(base: str, task_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_msg = ""
    while time.monotonic() < deadline:
        t = _ok(_req(base, "GET", f"/api/v1/tasks/{task_id}"))
        status = t.get("status", "")
        evs = t.get("events") or []
        if evs:
            ev = evs[-1]
            line = f"{ev.get('stage')}: {ev.get('message')}"
            if line != last_msg:
                print(f"[{status}] {line}", file=sys.stderr, flush=True)
                last_msg = line
        if status in ("done", "failed", "cancelled"):
            return t
        time.sleep(5)
    raise TimeoutError(f"task {task_id} 超过轮询预算 {timeout:.0f}s")


def _file_links(t: dict, base: str) -> list[dict]:
    out = []
    for f in (t.get("files") or []):
        out.append({
            "name": f.get("name"),
            "size": f.get("size"),
            "sha256": f.get("sha256"),
            "verdict": f.get("verdict"),
            "download_url": base.rstrip("/") + f.get("url", ""),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Resource Hub bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch")
    p.add_argument("query")
    p.add_argument("--file-types", action="append", default=None)
    p.add_argument("--seed-url", action="append", default=None)
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--timeout", type=float, default=1800.0)

    p = sub.add_parser("submit")
    p.add_argument("query")
    p.add_argument("--file-types", action="append", default=None)
    p.add_argument("--seed-url", action="append", default=None)
    p.add_argument("--base", default=DEFAULT_BASE)

    p = sub.add_parser("poll")
    p.add_argument("task_id")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--timeout", type=float, default=600.0)

    p = sub.add_parser("files")
    p.add_argument("task_id")
    p.add_argument("--base", default=DEFAULT_BASE)

    p = sub.add_parser("cancel")
    p.add_argument("task_id")
    p.add_argument("--base", default=DEFAULT_BASE)

    a = ap.parse_args()
    try:
        if a.cmd == "submit" or a.cmd == "fetch":
            body: dict = {"query": a.query}
            if a.file_types:
                body["file_types"] = [x if x.startswith(".") else "." + x
                                      for x in a.file_types]
            if a.seed_url:
                body["seed_urls"] = a.seed_url
            r = _ok(_req(a.base, "POST", "/api/v1/tasks", body=body))
            task_id = r["task_id"]
            if a.cmd == "submit":
                print(json.dumps({"task_id": task_id, "status": r.get("status")},
                                 ensure_ascii=False))
                return 0
            t = _poll_until(a.base, task_id, a.timeout)
        elif a.cmd == "poll":
            t = _poll_until(a.base, a.task_id, a.timeout)
        elif a.cmd == "files":
            t = _ok(_req(a.base, "GET", f"/api/v1/tasks/{a.task_id}"))
        elif a.cmd == "cancel":
            r = _ok(_req(a.base, "POST", f"/api/v1/tasks/{a.task_id}/cancel"))
            print(json.dumps(r, ensure_ascii=False))
            return 0
        else:  # pragma: no cover
            return 2

        result = t.get("result") or {}
        out = {
            "task_id": t.get("task_id") or a.task_id,
            "status": t.get("status"),
            "success": bool(result.get("success")),
            "summary": result.get("summary", ""),
            "error": result.get("error", "") or t.get("error", ""),
            "files": _file_links(t, a.base),
            "sources": result.get("sources", []),
            "pan_links": result.get("pan_links", []),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["success"] else 1
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
