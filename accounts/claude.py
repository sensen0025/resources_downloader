"""站点场景定义 — 第一个目标:claude.ai 注册/登录。

每个场景 = {目标描述, 起始 URL, 允许域, 提示补充}。Agent 据此自主行动,
站点改版时无需改代码 —— 这正是「适配任何格式」的意义。
"""

from __future__ import annotations

CLAUDE_REGISTER = {
    "name": "claude-register",
    "start_url": "https://claude.ai/login",
    "allowed_domain": "claude.ai",
    "goal": (
        "在 claude.ai 注册一个新账号:打开登录页后选择用邮箱注册,"
        "输入给定的邮箱地址,点击 Continue/继续,等待邮箱发来的安全登录链接(不是验证码),"
        "用 goto 打开链接完成验证。进入 claude.ai 主界面或 /onboarding 引导页即视为注册成功,"
        "直接 done(success=true),无需完成引导流程。若邮箱已注册过,则按登录流程处理。"
    ),
    "extra_prompt": (
        "注意:Claude 登录页有 invisible hCaptcha(自动化环境可能触发 Cloudflare 'Just a moment...' 校验,"
        "等待即可通过)。提交邮箱后页面会显示 'Enter verification code',但 Anthropic 邮件发的是"
        "「安全登录链接」(Sign in with the secure link below),不是数字验证码 —— "
        "用 mail_code 工具拿到链接后用 goto 动作打开它,浏览器会自动完成登录。"
    ),
}

CLAUDE_LOGIN = {
    "name": "claude-login",
    "start_url": "https://claude.ai/login",
    "allowed_domain": "claude.ai",
    "goal": (
        "登录已有的 Claude 账号:打开登录页,输入给定的邮箱地址,点击 Continue/继续,"
        "等待邮箱发来的安全登录链接(不是验证码),用 goto 动作打开链接完成登录。"
        "进入 claude.ai 主界面(/new 或 /onboarding)即视为登录成功,直接 done(success=true)。"
    ),
    "extra_prompt": "",
}

SCENARIOS = {
    "claude-register": CLAUDE_REGISTER,
    "claude-login": CLAUDE_LOGIN,
}
