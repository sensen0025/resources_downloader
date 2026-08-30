"""站点场景定义 — tripo3d.ai(3D 模型生成站)注册/登录。

tripo3d 用「验证码登录」:输入邮箱 → Send Code → 邮箱收验证码 → 输入即登录(注册)。
"""

from __future__ import annotations

TRIPO_REGISTER = {
    "name": "tripo-register",
    "start_url": "https://studio.tripo3d.ai/",
    "allowed_domain": "tripo3d.ai",
    "goal": (
        "在 tripo3d.ai(3D 模型生成站)注册新账号:首页点击 'Sign up/Log in',"
        "弹窗中选择 'Continue with Email',输入给定的邮箱地址,点击 'Send Code',"
        "等待邮箱验证码邮件,输入验证码完成登录(=注册)。"
        "进入工作台/主页(能访问 Creator Hub 或看到账户界面)即视为成功,done(success=true)。"
    ),
    "extra_prompt": (
        "验证码登录流程:Send Code 点击后按钮可能短暂禁用,邮件来自 Tripo/VAST(发件人含 tripo 或 vast),"
        "验证码是数字。若输入邮箱后提示 'already registered' 之类,说明该邮箱已注册,可改走登录流程。"
    ),
}

TRIPO_LOGIN = {
    "name": "tripo-login",
    "start_url": "https://studio.tripo3d.ai/",
    "allowed_domain": "tripo3d.ai",
    "goal": (
        "登录已有的 Tripo3D 账号:首页点击 'Sign up/Log in',选择 'Continue with Email',"
        "输入给定的邮箱,点击 'Send Code',等待邮箱验证码并输入,完成登录。"
        "进入工作台/主页即成功,done(success=true)。"
    ),
    "extra_prompt": "",
}

SCENARIOS = {
    "tripo-register": TRIPO_REGISTER,
    "tripo-login": TRIPO_LOGIN,
}
