"""邮箱验证码收码 CLI — 演示 / 联调 / Agent 脚本调用。

用法(在 resource-hub/ 目录下):

    python -m skills.mail.cli check [--sender github.com] [--json]
    python -m skills.mail.cli watch --interval 10 [--sender github.com]
    python -m skills.mail.cli wait --sender github.com --timeout 120

配置:环境变量或项目根 .env(见 .env.example):
    MAIL_IMAP_HOST / MAIL_IMAP_PORT / MAIL_EMAIL / MAIL_PASSWORD / MAIL_FOLDER

退出码:0=找到验证码, 2=检查完毕但未找到, 3=等待超时。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

from .config import MailConfig
from .extractor import extract_verification_code
from .imap_client import ImapMailbox


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m skills.mail.cli",
        description="Resource Hub 邮箱验证码收码工具(IMAP 轮询,纯标准库)",
    )
    p.add_argument("--host", help="IMAP 服务器,如 imap.gmail.com(也可用 MAIL_IMAP_HOST)")
    p.add_argument("--port", type=int, help="IMAP 端口,默认 993")
    p.add_argument("--email", help="邮箱地址(也可用 MAIL_EMAIL)")
    p.add_argument("--password", help="授权码/应用专用密码(也可用 MAIL_PASSWORD)")
    p.add_argument("--folder", default=None, help="邮箱文件夹,默认 INBOX")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--sender", default=None, help="发件人过滤关键字,如 github.com")
        sp.add_argument("--mark-seen", dest="mark_seen", action="store_true", default=None, help="处理后将邮件标记已读")
        sp.add_argument("--no-mark-seen", dest="mark_seen", action="store_false", help="不标记已读")
        sp.add_argument("--subject-first", action="store_true", help="优先在主题里找验证码")
        sp.add_argument("--json", action="store_true", help="输出 JSON(供脚本/Agent 消费)")

    pc = sub.add_parser("check", help="一次性检查未读邮件并提取验证码")
    add_common(pc)
    pc.add_argument("--all", action="store_true", help="扫描整个文件夹而不是只看未读")

    pw = sub.add_parser("watch", help="持续轮询,新邮件实时打印")
    add_common(pw)
    pw.add_argument("--interval", type=int, default=10, help="轮询间隔秒数,默认 10")

    pt = sub.add_parser("wait", help="等待一封含验证码的邮件(带预算,超时返回)")
    add_common(pt)
    pt.add_argument("--interval", type=int, default=5, help="轮询间隔秒数,默认 5")
    pt.add_argument("--timeout", type=int, default=180, help="最长等待秒数,默认 180")
    return p


def _build_config(args: argparse.Namespace) -> MailConfig:
    overrides: dict[str, object] = {}
    for env_key, val in (
        ("MAIL_IMAP_HOST", args.host),
        ("MAIL_IMAP_PORT", args.port),
        ("MAIL_EMAIL", args.email),
        ("MAIL_PASSWORD", args.password),
        ("MAIL_FOLDER", args.folder),
    ):
        if val is not None:
            overrides[env_key] = val
    cfg = MailConfig.from_env(overrides=overrides)
    cfg.require_credentials()
    return cfg


def _make_mailbox(cfg: MailConfig) -> ImapMailbox:
    return ImapMailbox(
        host=cfg.host,
        email=cfg.email,
        password=cfg.password,
        port=cfg.port,
        folder=cfg.folder,
        use_ssl=cfg.use_ssl,
        starttls=cfg.starttls,
        timeout=cfg.timeout,
    )


def _sender_filter(args: argparse.Namespace, mail) -> bool:
    if not args.sender:
        return True
    h = args.sender.lower()
    return h in mail.from_.lower() or h in mail.subject.lower()


def cmd_check(cfg: MailConfig, args: argparse.Namespace) -> int:
    mark_seen = args.mark_seen if args.mark_seen is not None else False
    mb = _make_mailbox(cfg)
    try:
        mails = mb.fetch_all(mark_seen=mark_seen) if args.all else mb.fetch_unseen(mark_seen=mark_seen)
    finally:
        mb.close()

    rows = []
    for m in mails:
        if not _sender_filter(args, m):
            continue
        r = extract_verification_code(m, subject_first=args.subject_first)
        rows.append(
            {
                "num": m.num,
                "from": m.from_,
                "subject": m.subject,
                "date": m.date,
                "code": r.code if r else None,
                "pattern": r.pattern if r else None,
                "context": r.context if r else None,
            }
        )

    found = any(x["code"] for x in rows)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if found else 2

    if not rows:
        print("(没有符合条件的邮件)")
    for x in rows:
        if x["code"]:
            print(f"✅ [{x['date']}] {x['from']} | {x['subject']}")
            print(f"   验证码 = {x['code']}  (命中: {x['pattern']})")
            print(f"   上下文: {x['context']}")
        else:
            print(f"ℹ️  [{x['date']}] {x['from']} | {x['subject']}  (未识别出验证码)")
    return 0 if found else 2


def cmd_watch(cfg: MailConfig, args: argparse.Namespace) -> int:
    mark_seen = args.mark_seen if args.mark_seen is not None else True
    mb = _make_mailbox(cfg)
    seen: set[str] = set()
    print(
        f"开始监控 {cfg.email} @ {cfg.host} 的 {cfg.folder} "
        f"(每 {args.interval}s 轮询,Ctrl+C 退出)",
        file=sys.stderr,
    )
    try:
        while True:
            try:
                mails = mb.fetch_unseen(mark_seen=False)
                for m in mails:
                    key = m.message_id or f"{m.from_}|{m.subject}|{m.date}"
                    if key in seen:
                        continue
                    seen.add(key)
                    if not _sender_filter(args, m):
                        continue
                    r = extract_verification_code(m, subject_first=args.subject_first)
                    ts = time.strftime("%H:%M:%S")
                    if args.json:
                        print(
                            json.dumps(
                                {
                                    "time": ts,
                                    "from": m.from_,
                                    "subject": m.subject,
                                    "code": r.code if r else None,
                                    "pattern": r.pattern if r else None,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                    elif r:
                        print(
                            f"✅ [{ts}] {m.from_} | {m.subject} → 验证码: {r.code} ({r.pattern})",
                            flush=True,
                        )
                    else:
                        print(f"ℹ️  [{ts}] {m.from_} | {m.subject} (未识别出验证码)", flush=True)
                    if mark_seen:
                        mb.mark_seen(m.num)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"⚠️  轮询异常: {e} (尝试重连)", file=sys.stderr)
                try:
                    mb.ensure_connected()
                except Exception as e2:
                    print(f"⚠️  重连失败: {e2}", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止", file=sys.stderr)
        return 0
    finally:
        mb.close()


def cmd_wait(cfg: MailConfig, args: argparse.Namespace) -> int:
    mark_seen = args.mark_seen if args.mark_seen is not None else True
    mb = _make_mailbox(cfg)
    try:
        r = mb.wait_for_code(
            sender_hint=args.sender,
            timeout=args.timeout,
            poll_interval=args.interval,
            mark_seen=mark_seen,
            subject_first=args.subject_first,
        )
    finally:
        mb.close()

    if r is None:
        if args.json:
            print(json.dumps({"code": None, "reason": "timeout"}))
        else:
            print(f"⏰ 等待 {args.timeout}s 未收到含验证码的邮件")
        return 3
    if args.json:
        print(json.dumps({"code": r.code, "source": r.source, "pattern": r.pattern, "context": r.context}))
    else:
        print(f"✅ 验证码: {r.code}")
        print(f"   来源: {r.source} · 命中: {r.pattern}")
        print(f"   上下文: {r.context}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = _build_config(args)
    if args.command == "check":
        return cmd_check(cfg, args)
    if args.command == "watch":
        return cmd_watch(cfg, args)
    if args.command == "wait":
        return cmd_wait(cfg, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
