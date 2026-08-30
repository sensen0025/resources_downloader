"""Agent 主循环 — 观察 → LLM 决策 → 执行 → 回写历史,带预算与停止判定。

对齐 browser-use/skyvern 的循环(见 前人经验总结 §B):观察=可交互元素索引,
行动=结构化 JSON 动作,工具=邮箱/验证码/人工,预算=步数上限,停止=solved/failed。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Optional

from .actions import ActionExecutor, ActionResult
from .browser import BrowserSession
from .llm import LLMClient
from .perception import Perception
from skills.core import get_registry

# 注册所有技能工具(import side-effect,对齐 DSH「注册是效果」)
import agent.tools as _tools  # noqa: F401  (确保 @tool 注册执行)

# 允许的浏览器原生动作(白名单,防 LLM 发明动作)
ACTIONS = {
    "click", "type", "press_enter", "wait", "goto",
    "screenshot", "read_body", "done",
}
# 技能类动作(来自注册表,参数 schema 校验后执行)
TOOL_ACTIONS = frozenset(_tools.registered_names())

SYSTEM_PROMPT = """你是一个网页自动化 Agent,正在执行一个账号注册/登录任务。
规则:
1. 每次观察会给你「可交互元素索引 [n]」和页面文本,你必须基于它们行动,不要臆造不存在的元素。
2. 只输出一个 JSON 对象(不要任何其他文字),格式:
   {"thought":"简短推理","action":"动作","index":n,"value":"...","args":{...}}
3. 可用动作:
   - click: 点击元素 [index](按钮/链接/勾选框)
   - type: 向元素 [index] 输入文本(value)
   - press_enter: 按回车(可省略 index)
   - wait: 等待页面加载/渲染(args: {"ms": 3000},最多 15000)
   - goto: 跳转(value=URL)
   - mail_code: 等待邮箱验证码邮件并返回验证码(args: {"sender":"发件人关键字","timeout":150}),
     邮件可能包含「安全登录链接」(如 Claude)而非验证码,工具会两者都返回;
     若返回的是链接,用 goto 动作打开该链接完成验证,不要尝试把链接填进输入框
   - captcha: 识别当前页面上的图形验证码(截图后交给 OCR/视觉模型,args: {"charset":"0123456789"})
   - analyze_page: 分析当前页面/指定 URL,提取下载链接与页面分级(找资源用)
   - download: 下载文件到本地(args: {"url":..., "expected_ext":".litematic"}),文件落地才算成功;
     **m3u8 播放流/播放页**会走万能下载器自动合并切片,不要手动拼 .ts;被 Cloudflare
     "Just a moment" 拦截时设 use_session=true 走浏览器会话(已自动处理 CF)
   - screenshot: 截图(视觉确认页面状态)
   - read_body: 读取页面可见文本
   - human: 需要人工介入时使用(value=向用户提的问题)
   - done: 任务完成或无法继续(value=总结,args: {"success": true/false})
4. 邮箱验证:页面提示"验证码/链接已发送到邮箱"后,用 mail_code 等邮件。
   Claude 的邮件是「安全登录链接」(发件人含 anthropic.com),不含数字验证码 ——
   拿到链接后必须用 goto 动作打开它完成验证。若页面同时提供验证码输入框且邮件也给了码,则填码。
   验证码登录(如 tripo3d 的 "Verification Code Login"):填邮箱 → 点 Send Code → mail_code 等验证码 → 填入 → 完成。
4.5 注册成功标志:进入站点主界面 / onboarding 引导页(如 claude.ai/onboarding)即视为注册成功,
   直接 done(success=true),不需要完成引导流程。
5. 如果某个动作失败(结果含"失败"),换一种方式重试,不要无限重复同一动作。
5.5 表单输入:观察里的输入框会带状态(empty/filled=0/filled=1/value=.../checked=N)。
    已 filled 的字段**不要重复输入**;填完所有 empty 字段、勾选必选勾选框(checked=0 的),
    然后找到文本含 提交/注册/Register/Submit/Create 的按钮或链接点击它完成提交。
    页面重渲染导致编号变化时,以最新观察为准。
5.6 注册后必查邮箱:点击注册/提交后,**无论页面是否显示错误**(如 ?error=captcha、验证码错误),
    都用 mail_code 工具查邮箱 —— 部分站点即使报验证码错误也会照常创建账号并发「激活/确认」邮件
    (含链接或验证码)。若邮件含激活/确认链接,用 goto 打开它完成激活;
    若含数字验证码,填入对应输入框。激活/确认完成后再回登录页登录。
5.7 下载:普通 download 返回 403/拦截时,改用 download 工具并设 use_session=true
    (走浏览器会话,带上已通过的 Cloudflare/登录 Cookie)。
6. 安全:只在任务指定域内操作;绝不提交付费、绝不下载与任务无关的文件。
7. 如果页面标题是 "Just a moment..." 或页面显示 Cloudflare 校验:说明在做浏览器校验,
   用 wait(5000~10000ms) 等它完成,校验通过后页面会正常加载;最多等 5 次,仍不行再 done(success=false)。
8. 预算:你最多有 __MAX_STEPS__ 步,每步都要推进任务。重复动作超过 3 次视为卡住,应换策略或 done(success=false)。"""


@dataclass
class StepRecord:
    step: int
    observation_summary: str
    decision: dict
    result: str


@dataclass
class AgentResult:
    success: bool
    summary: str
    steps: list = field(default_factory=list)
    final_url: str = ""


class AccountAgent:
    def __init__(
        self,
        session: BrowserSession,
        llm: LLMClient,
        goal: str,
        allowed_domain: str = "",
        max_steps: int = 30,
        verbose: bool = True,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.goal = goal
        self.allowed_domain = allowed_domain
        self.max_steps = max_steps
        self.verbose = verbose
        self.progress = progress
        self.perception = Perception(session)
        self.actions = ActionExecutor(session)
        self.ctx = SimpleNamespace(session=session, task=None)  # 注入给技能工具
        self.history: list[StepRecord] = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)
        if self.progress:
            self.progress(msg)

    # ------------------------------------------------------------ 主循环

    def run(self) -> AgentResult:
        repeated: dict[str, int] = {}
        for step in range(1, self.max_steps + 1):
            self._log(f"\n--- 步骤 {step}/{self.max_steps} ---")
            obs = self.perception.observe()
            if not obs.elements:
                # 页面可能还在加载/Cloudflare 校验中,自动等待后再观察一次
                self._log("[观察] 无可交互元素,等待页面加载...")
                self.session.wait(2500)
                obs = self.perception.observe()
            self._log(f"[观察] {obs.url} | {obs.title} | 元素 {len(obs.elements)} 个")

            decision = self._decide(obs)
            if decision is None:
                self._log("[LLM] 决策解析失败,等待后重试")
                self.session.wait(2000)
                continue

            action = decision.get("action", "")
            if action not in ACTIONS and action not in TOOL_ACTIONS:
                self._log(f"[LLM] 非法动作 {action!r},按等待处理")
                decision = {"action": "wait", "args": {"ms": 2000}}
                action = "wait"

            result = self._execute(decision, obs)
            if isinstance(result, str):
                result = ActionResult(True, result)

            # 卡住检测:只对「推进型」动作计数(wait 等待 CF/加载不算卡住)
            if action not in ("wait", "screenshot", "read_body"):
                key = f"{action}:{decision.get('index')}:{str(decision.get('value') or '')[:20]}"
                repeated[key] = repeated.get(key, 0) + 1
                if repeated[key] >= 3:
                    self._log("[卡住] 同一动作重复 3 次,终止")
                    return AgentResult(False, f"卡住: 重复动作 {key} 3 次", self.history, obs.url)

            self.history.append(StepRecord(step, f"{obs.url} | {obs.title}", decision, result))
            self._log(f"[动作] {action} {decision.get('index', '')} → {result.message[:200]}")

            if action == "done":
                success = bool(decision.get("args", {}).get("success", False))
                return AgentResult(success, decision.get("value", ""), self.history, obs.url)

        self._log(f"\n[预算] 达到最大步数 {self.max_steps},终止")
        return AgentResult(False, "达到最大步数未完成", self.history, self.session.current_url())

    # ------------------------------------------------------------ 决策

    def _decide(self, obs) -> Optional[dict]:
        user = self._build_user_prompt(obs)
        system = SYSTEM_PROMPT.replace("__MAX_STEPS__", str(self.max_steps))
        last_err = None
        for attempt in range(3):  # LLM 调用失败自动重试(推理模型偶发空输出)
            try:
                text = self.llm.chat(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.2,
                    max_tokens=2048,
                )
                break
            except Exception as e:
                last_err = e
                self._log(f"[LLM] 调用失败({attempt + 1}/3): {e}")
                self.session.wait(2000 * (attempt + 1))
        else:
            return None
        try:
            decision = _parse_decision(text)
        except Exception as e:
            self._log(f"[LLM] 输出解析失败({e}): {text[:200]!r}")
            return None
        return decision

    def _build_user_prompt(self, obs) -> str:
        lines = [
            f"任务: {self.goal}",
            "",
            "当前页面观察:",
            obs.to_prompt(),
            "",
            "最近执行记录:",
        ]
        for rec in self.history[-8:]:
            lines.append(f"  步{rec.step}: 动作={rec.decision.get('action')} 结果={str(rec.result)[:120]}")
        if not self.history:
            lines.append("  (无)")
        lines.append("")
        lines.append("请输出下一步动作 JSON。")
        return "\n".join(lines)

    # ------------------------------------------------------------ 执行

    def _execute(self, decision: dict, obs) -> str:
        action = decision.get("action", "")
        index = decision.get("index")
        value = decision.get("value", "")
        args = decision.get("args") or {}
        ex = self.actions

        if action == "click":
            return str(ex.click(index))
        if action == "type":
            return str(ex.type(index, value))
        if action == "press_enter":
            return str(ex.press_enter(index))
        if action == "wait":
            return str(ex.wait(int(args.get("ms", 3000))))
        if action == "goto":
            return str(ex.goto(value))
        if action == "screenshot":
            r = ex.screenshot()
            if r.ok and r.data:
                from .tools_vision import solve_captcha_vlm

                self._log("[截图] 页面截图已获取,尝试用视觉模型识别验证码/状态")
                code = solve_captcha_vlm(r.data)
                if code:
                    return f"截图成功;视觉模型识别出验证码: {code}"
            return str(r)
        if action == "read_body":
            return str(ex.read_body())
        if action in TOOL_ACTIONS:
            # 技能工具:注册表校验参数后执行,违规参数回馈 violations 让模型自纠
            args = dict(decision.get("args") or {})
            result = get_registry().invoke(action, args, ctx=self.ctx)
            return str(result)
        if action == "done":
            return "任务结束标记"
        return f"未知动作 {action}"


def _parse_decision(text: str) -> dict:
    """容错解析 LLM 的 JSON 决策。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    cleaned = re.sub(r"```(?:json)?", "", text).strip("` \n")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"无法解析决策 JSON") from exc
