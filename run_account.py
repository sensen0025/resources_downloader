"""账号注册器/登录器 CLI 入口(AI Agent 驱动)。

用法(用系统 Python,已装 playwright;LLM key 在 .env):

    python run_account.py register claude --email sensenx140125+claude@gmail.com [--password xxx] [--headed]
    python run_account.py login claude --email xxx@yyy.com --password xxx [--headed]

- 未给 --password 时自动生成随机密码并保存到 accounts/credentials.json;
- 默认无头模式,加 --headed 可看到浏览器操作;
- 邮箱验证码自动走 skills.mail(IMAP 轮询);图形验证码走 DeepSeek 视觉模型;
- 登录/注册成功后登录态 Cookie 自动存到 accounts/cookies/(按 站点+邮箱 分文件),
  下次 login 命中直接跳过 Agent 流程(快速通道);--no-cookies 禁用,--forget-cookies 强制重登。
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
import string
import sys
from pathlib import Path

from accounts import SCENARIOS
from agent import AccountAgent
from agent.browser import BrowserSession
from agent.cookies import cookie_path, delete_cookies, has_cookies, save_cookies
from agent.llm import LLMClient
from skills.mail.config import load_dotenv

CREDENTIALS_FILE = Path(__file__).parent / "accounts" / "credentials.json"

# 登录页 URL 特征:命中即视为「未登录」
_LOGIN_URL_MARKS = ("/login", "/signin", "/sign-in", "/auth", "login?", "signin?")


def _load_credentials() -> dict:
    if CREDENTIALS_FILE.exists():
        try:
            return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_credentials(creds: dict) -> None:
    CREDENTIALS_FILE.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")


def _gen_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _resolve_password(scenario: str, provided: str | None, creds: dict) -> str:
    if provided:
        return provided
    key = f"claude-{scenario}"
    if key in creds and creds[key].get("password"):
        return creds[key]["password"]
    pw = _gen_password()
    creds.setdefault(key, {})
    creds[key]["password"] = pw
    _save_credentials(creds)
    print(f"ℹ️  已生成随机密码并保存到 {CREDENTIALS_FILE.name}")
    return pw


def _looks_logged_in(session: BrowserSession) -> bool:
    """Cookie 快速通道的登录态判定(零 LLM 成本,纯 URL + DOM 启发式)。

    已登录:URL 不在登录页特征上、页面无邮箱/密码输入框、且正文够长(排除
    Cloudflare 校验空壳页)。判定失败(拿不准)一律返回 False → 走完整 Agent,
    宁可多花几步 LLM 也不误报「已登录」。
    """
    url = (session.current_url() or "").lower()
    if any(m in url for m in _LOGIN_URL_MARKS):
        return False
    page = session.page
    try:
        if page.query_selector(
            "input[type='email'], input[name='email'], input[type='password'], input[name='password']"
        ):
            return False
        body = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        if len(body.strip()) < 50:
            return False  # 空壳页 / Cloudflare 'Just a moment...'
    except Exception:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="run_account", description="AI 驱动的账号注册器/登录器")
    ap.add_argument("operation", choices=["register", "login"], help="register=注册 / login=登录")
    ap.add_argument("scenario", help="站点场景:claude / tripo / claude-register / claude-login / tripo-register / tripo-login(简写按 operation 自动选)")
    ap.add_argument("--email", required=True, help="邮箱(注册时用未注册过的地址,支持 Gmail +别名)")
    ap.add_argument("--password", default=None, help="密码(缺省自动生成并保存;邮箱验证码类站点用不到)")
    ap.add_argument("--headed", action="store_true", help="有头模式(默认无头)")
    ap.add_argument("--no-cookies", action="store_true",
                    help="不读不写登录态 Cookie(完全禁用 Cookie 加速)")
    ap.add_argument("--forget-cookies", action="store_true",
                    help="启动前删除该站点/邮箱已存的 Cookie(强制重新登录)")
    args = ap.parse_args(argv)

    # 简写映射:claude → claude-register/claude-login,tripo → tripo-register/tripo-login
    if args.scenario in ("claude", "tripo"):
        scenario_key = f"{args.scenario}-{args.operation}"
    elif args.scenario in SCENARIOS:
        scenario_key = args.scenario
    else:
        ap.error(f"未知场景 {args.scenario!r},可选: claude / tripo / claude-register / claude-login / tripo-register / tripo-login")

    if args.operation == "register":
        scenario = dict(SCENARIOS[f"{args.scenario.split('-')[0]}-register"])
    else:
        scenario = dict(SCENARIOS[f"{args.scenario.split('-')[0]}-login"])
    site = args.scenario.split("-")[0]

    creds = _load_credentials()
    password = _resolve_password(site, args.password, creds)

    goal = scenario["goal"]
    extra = scenario.get("extra_prompt", "")
    goal += f"\n账号信息: 邮箱={args.email}, 密码={password}(如页面要求设置密码请使用该密码,已存在则用它登录)。"
    if extra:
        goal += f"\n{extra}"

    # ---- 登录态 Cookie 存储(accounts/cookies/,按 站点域+邮箱 分文件)----
    ck = cookie_path(scenario["allowed_domain"], args.email)
    if args.forget_cookies and delete_cookies(ck):
        print(f"🍪 已删除旧登录态: {ck.name}")
    cookies_available = (not args.no_cookies) and has_cookies(ck)

    print("=" * 60)
    print(f"任务: {args.operation} @ {scenario['name']}")
    print(f"邮箱: {args.email}")
    print(f"模式: {'有头' if args.headed else '无头'}")
    print(f"登录态: {'🍪 有 Cookie,可走快速通道' if cookies_available else '❌ 无 Cookie(需完整登录)'}")
    print("=" * 60)

    with BrowserSession(headless=not args.headed,
                        cookies_path=ck if cookies_available else None) as session:
        session.goto(scenario["start_url"])

        # Cookie 快速通道:命中登录态 → 跳过整个 Agent 登录流程
        if args.operation == "login" and cookies_available and _looks_logged_in(session):
            result_success = True
            result_summary = "🍪 命中登录态 Cookie,已跳过登录流程(快速通道,零 LLM 成本)"
            final_url = session.current_url()
            print(f"\n[快速通道] {result_summary}")
        else:
            llm = LLMClient()
            agent = AccountAgent(
                session=session,
                llm=llm,
                goal=goal,
                allowed_domain=scenario["allowed_domain"],
                max_steps=35,
            )
            result = agent.run()
            result_success = result.success
            result_summary = result.summary
            final_url = result.final_url

        # 登录/注册成功后持久化登录态(下次直接复用)
        if result_success and not args.no_cookies:
            save_cookies(session, ck)
            print(f"✅ 已保存登录态 Cookie → {ck.name}(下次登录自动复用)")

    print("\n" + "=" * 60)
    print(f"结果: {'✅ 成功' if result_success else '❌ 未完成'}")
    print(f"总结: {result_summary}")
    print(f"最终 URL: {final_url}")
    print("=" * 60)

    if result_success and args.operation == "register":
        creds = _load_credentials()
        creds[f"{site}-{args.operation}"] = {"email": args.email, "password": password}
        _save_credentials(creds)
        print(f"✅ 已保存账号凭证到 {CREDENTIALS_FILE.name}")

    return 0 if result_success else 1


if __name__ == "__main__":
    sys.exit(main())
