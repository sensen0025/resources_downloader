"""Resource Hub API 客户端示例 — 申请令牌 → 提交任务 → 轮询/SSE → 断点续传下载。

零依赖(requests 即可),演示完整调用格式与传输方式:

    python api_client.py --base http://127.0.0.1:8000 apply              # 申请令牌(保存到本地)
    python api_client.py --base http://127.0.0.1:8000 create "凡人修仙传壁纸"
    python api_client.py --base http://127.0.0.1:8000 poll <task_id>
    python api_client.py --base http://127.0.0.1:8000 events <task_id>    # SSE 实时进度
    python api_client.py --base http://127.0.0.1:8000 download <task_id>  # 断点续传下载全部文件

令牌模式(服务端配了 RH_API_SECRET)必须带 --token;个人模式可省。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

TOKEN_FILE = Path(__file__).parent / "data" / "api_token.json"
CHUNK = 1 << 16


def _load_token() -> str:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["token_key"]
        except Exception:
            pass
    return ""


def _save_token(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _base_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _check(resp: requests.Response) -> dict:
    """统一信封解析:code!=0 或 HTTP 非 2xx 时抛错。"""
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = {}
        raise SystemExit(f"HTTP {resp.status_code}: {body.get('message', resp.text[:200])}")
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"业务错误 {body.get('code')}: {body.get('message')}")
    return body.get("data", {})


def cmd_apply(base: str, token: str, name: str) -> None:
    r = requests.post(_base_url(base, "/api/v1/tokens"),
                      json={"name": name}, headers=_headers(token), timeout=15)
    data = _check(r)
    _save_token(data)
    print("✅ 已申请令牌并保存到", TOKEN_FILE)
    print(f"   token_id: {data['token_id']}")
    print(f"   token_key: {data['token_key']}  (明文仅此一次)")


def cmd_create(base: str, token: str, query: str, callback: str = "") -> None:
    payload = {"query": query, "label": query[:30], "callback_url": callback}
    r = requests.post(_base_url(base, "/api/v1/tasks"),
                      json=payload, headers=_headers(token), timeout=15)
    data = _check(r)
    print(f"✅ 任务已提交: {data['task_id']} status={data['status']}")
    print(f"   轮询: {data['polling_url']}")
    print(f"   SSE : {data['events_url']}")
    print(f"   文件令牌: {data['file_token']}")


def cmd_poll(base: str, token: str, task_id: str) -> None:
    while True:
        data = _check(requests.get(_base_url(base, f"/api/v1/tasks/{task_id}"),
                                   headers=_headers(token), timeout=15))
        print(f"[{data['status']}] stage={data.get('stage', '')} "
              f"percent={data.get('percent', 0)} error={data.get('error', '')}")
        if data["status"] in ("done", "failed", "cancelled"):
            _print_result(data)
            return


def cmd_events(base: str, token: str, task_id: str) -> None:
    """SSE 实时事件流(免轮询)。"""
    with requests.get(_base_url(base, f"/api/v1/tasks/{task_id}/events"),
                      headers=_headers(token), stream=True, timeout=600) as r:
        if r.status_code >= 400:
            raise SystemExit(f"HTTP {r.status_code}: {r.text[:200]}")
        for line in r.iter_lines(decode_unicode=True):
            if not line or line.startswith(":"):
                continue
            if line.startswith("event: "):
                evt = line[7:]
            elif line.startswith("data: "):
                print(f"<{evt}> {line[6:]}")
                if evt in ("done", "failed", "cancelled"):
                    return


def cmd_download(base: str, token: str, task_id: str, out_dir: str) -> None:
    """断点续传下载:已存在的部分文件带 Range 续传,完成后校验 sha256。"""
    task = _check(requests.get(_base_url(base, f"/api/v1/tasks/{task_id}"),
                               headers=_headers(token), timeout=15))
    if task["status"] != "done":
        print(f"⚠️ 任务状态 {task['status']},先轮询到 done")
        return
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in task.get("files", []):
        name, size, sha = f["name"], f["size"], f["sha256"]
        dest = out / name
        done = dest.stat().st_size if dest.exists() else 0
        url = _base_url(base, f["url"])
        headers = _headers(token)   # 个人模式空头即可;令牌模式带 Bearer
        if done and done < size:
            headers["Range"] = f"bytes={done}-"
        with requests.get(url, headers=headers, stream=True, timeout=60) as r:
            if r.status_code == 416:
                print(f"   {name}: 服务器已完整,删除本地重下")
                dest.unlink(missing_ok=True)
                continue
            mode = "ab" if r.status_code == 206 and done else "wb"
            with open(dest, mode) as fh:
                for chunk in r.iter_content(CHUNK):
                    fh.write(chunk)
        total = dest.stat().st_size
        ok = f"✅ {name}: {total} 字节"
        if sha:
            import hashlib

            h = hashlib.sha256()
            with open(dest, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            ok += f" sha256={'匹配' if h.hexdigest() == sha else '❌ 不匹配'}"
        print(ok)


def _print_result(data: dict) -> None:
    print(f"   总结: {data.get('result', {}).get('summary', '')}")
    print(f"   文件: {data.get('files', [])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="api_client", description="Resource Hub API 客户端示例")
    ap.add_argument("--base", default=os.environ.get("RH_API_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default="", help="Bearer 令牌(个人模式可省;可用 RH_API_TOKEN 环境变量)")
    ap.add_argument("--name", default="default", help="申请令牌时的名称")
    ap.add_argument("--out", default="downloads", help="下载目录")
    ap.add_argument("--callback", default="", help="Webhook 回调 URL")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply")
    sub.add_parser("create").add_argument("query")
    sub.add_parser("poll").add_argument("task_id")
    sub.add_parser("events").add_argument("task_id")
    sub.add_parser("download").add_argument("task_id")
    args = ap.parse_args(argv)

    token = args.token or os.environ.get("RH_API_TOKEN", "") or _load_token()
    if args.cmd == "apply":
        cmd_apply(args.base, token, args.name)
    elif args.cmd == "create":
        cmd_create(args.base, token, args.query, args.callback)
    elif args.cmd == "poll":
        cmd_poll(args.base, token, args.task_id)
    elif args.cmd == "events":
        cmd_events(args.base, token, args.task_id)
    elif args.cmd == "download":
        cmd_download(args.base, token, args.task_id, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
