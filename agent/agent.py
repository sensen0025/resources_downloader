"""Agent 主循环 — 观察 → LLM 决策 → 执行 → 回写历史,带预算与停止判定。

对齐 browser-use/skyvern 的循环(见 前人经验总结 §B):观察=可交互元素索引,
行动=结构化 JSON 动作,工具=邮箱/验证码/人工,预算=步数上限,停止=solved/failed。
"""

from __future__ import annotations

import json
import re
import time
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

# 连续「工具参数不合法」熔断阈值:LLM 反复漏填必填字段时及时止损,
# 不把预算耗在同样的 schema 错误上(线上事故:一轮任务 4 次缺参空转)
_MAX_INVALID_ARGS = 2

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
   - human: 需要人工介入时使用(args: {"question": "向用户提的问题"})
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
6.5 聚焦:你的任务只针对目标 URL 及其所在站点。候选列表/搜索结果里的其他站点交给各自的
    Agent 尝试,**不要**在脚本或工具里访问其他候选站点,不要写脚本去抓别的站的接口;
    专用工具只能用于对应站点(bilibili_fetch 仅限 bilibili.com 页面调用)。
    若发现页面 URL 已偏离目标站点域(如误跳转到无关视频/文章页),用 goto 回到目标 URL 再继续。
7. 如果页面标题是 "Just a moment..." 或页面显示 Cloudflare 校验:说明在做浏览器校验,
   用 wait(5000~10000ms) 等它完成,校验通过后页面会正常加载;最多等 5 次,仍不行再 done(success=false)。
8. 预算:你最多有 __MAX_STEPS__ 步,每步都要推进任务。重复动作超过 3 次视为卡住,应换策略或 done(success=false);
   工具参数不合法(如缺必填字段)连续 2 次视为同样的 schema 错误,直接 done(success=false),
   不要反复用缺参的工具调用空转。
9. 搜索引擎中转/跳转页(URL 含 so.com/link、baidu.com/link、bing.com/ck 等,URL 可能超长含乱码参数):
   **不要**重新编码或重复 goto 该 URL(参数已加密,人工还原必然出错);先用 wait(3000~5000ms)
   等页面 JS 自动跳转到真实站点,再重新观察;观察到的 URL/标题变化后再继续找资源。
10. 阅读平台的 SEO 引导页(URL 含 bookquery/kol-rec/chapter/bookrecommend 等):页面上的
    「TXT下载/全集下载」按钮通常指向**另一张同类书页**而不是文件。若连续点击下载按钮后
    仍在同一站点的书页/章节页之间跳转(URL 仍是 bookquery/chapter),说明是 SEO 引导迷宫,
    停止点击,直接 done(success=false),不要浪费时间。
11. 站点信誉:用 site_lookup 查询历史 AI 对站点的评分(0-9)与短描述(向量相似度 top-k)。
    选择访问/下载目标时优先高分站(≥6);避开低分站(≤2)或描述含"虚假/HTML 冒充/登录墙"
    的站 —— 历史教训:这些站只会空耗预算。站点出现在搜索结果里但信誉库评分很低时,
    直接跳过它找下一个候选。
12. B站(bilibili.com/video 或 /bangumi)页面:调用 bilibili_fetch 工具(参数 url 必填,勿缺参)——
    它会用当前浏览器会话(已过 B站反爬 412)从页面 playinfo 读 DASH 流:视频任务自动下载视频流
    并与音频合并为 mp4;纯音乐任务可设 prefer=audio 只下音频。不要点页面上的「下载」按钮
    (那是 APP 客户端),不要尝试裸 HTTP 抓页面(必 412)。
13. 沙盒工具(sandbox_read/write/python/run):当规则技能搞不定某站(签名 API、加密流、
    特殊格式),自己写脚本解决 —— 用 sandbox_write 写脚本 → sandbox_python/run 执行 →
    sandbox_read 看输出 → 迭代直到拿到直链/文件。脚本需要过反爬时,读沙盒里的
    `_session_cookies.json`(浏览器会话 cookie 的 JSON dict),用
    requests.get(url, cookies=json.load(open('_session_cookies.json'))) 带上。
    **只在任务目录内读写,禁止触碰服务器其他文件;禁止删除任务文件;脚本不得访问
    .env/密钥;别跑危险命令(会被拦截)。** 写文件注意:任务完成后文件会被收集交付,
    脚本/中间产物可留但命名要清晰。
14. 图片/壁纸资源:必须拿原图/高清直链。URL 含 /thumbnail/ /small/ /thumb 等缩略图
    路径时,先尝试替换为 /large/ /mw1024/ /source/ /origin 等原图路径再下载;
    几 KB 的缩略图不算合格的壁纸,不要以缩略图宣布完成。"""


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
    final_title: str = ""   # 最后观察到的页面标题(皮肤任务名字匹配验收用)


class AccountAgent:
    def __init__(
        self,
        session: BrowserSession,
        llm: LLMClient,
        goal: str,
        allowed_domain: str = "",
        max_steps: int = 30,
        max_seconds: Optional[float] = None,   # 时间预算:超过即终止本候选(防"卡死")
        verbose: bool = True,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.goal = goal
        self.allowed_domain = allowed_domain
        self.max_steps = max_steps
        self.max_seconds = max_seconds
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
            try:
                self.progress(msg)
            except Exception:
                pass  # 进度回调失败不影响 Agent 主循环

    # ------------------------------------------------------------ 主循环

    def run(self) -> AgentResult:
        repeated: dict[str, int] = {}
        parse_fails = 0  # 连续 LLM 决策解析失败计数(防死循环空转)
        invalid_args = 0  # 连续工具参数不合法计数(缺必填字段熔断)
        started = time.monotonic()
        self._last_title = ""
        for step in range(1, self.max_steps + 1):
            if self.max_seconds is not None and time.monotonic() - started > self.max_seconds:
                self._log(f"[预算] 超过时间预算 {self.max_seconds:.0f}s,终止本候选")
                return AgentResult(False, f"超过时间预算 {self.max_seconds:.0f}s",
                                   self.history, self.session.current_url(),
                                   self._last_title)
            self._log(f"\n--- 步骤 {step}/{self.max_steps} ---")
            obs = self.perception.observe()
            if not obs.elements:
                # 页面可能还在加载/Cloudflare 校验中,自动等待后再观察一次
                self._log("[观察] 无可交互元素,等待页面加载...")
                self.session.wait(2500)
                obs = self.perception.observe()
            self._last_title = obs.title
            self._log(f"[观察] {obs.url} | {obs.title} | 元素 {len(obs.elements)} 个")

            decision = self._decide(obs)
            if decision is None:
                parse_fails += 1
                if parse_fails >= 3:
                    self._log("[LLM] 连续 3 次决策解析失败,终止本候选(多为超长 URL 截断)")
                    return AgentResult(False, "连续决策解析失败", self.history, obs.url,
                                       self._last_title)
                self._log("[LLM] 决策解析失败,等待后重试")
                self.session.wait(2000)
                continue
            parse_fails = 0

            # AI 自汇报:把模型自己的推理(thought)推给进度流(SSE/任务日志),
            # 用户能看到 AI 为什么这么做,而不是干等
            thought = str(decision.get("thought") or "").strip().replace("\n", " ")
            if thought:
                self._log(f"[AI] {thought}")

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
                    return AgentResult(False, f"卡住: 重复动作 {key} 3 次", self.history,
                                       obs.url, self._last_title)

            # 工具参数不合法熔断:LLM 反复漏填必填字段(缺 url/path/code/content 等)
            # 时,每次白烧一整轮 LLM 调用 —— 连续 2 次直接止损,不继续空转。
            if action in TOOL_ACTIONS:
                if "参数不合法" in str(result):
                    invalid_args += 1
                    if invalid_args >= _MAX_INVALID_ARGS:
                        self._log(
                            f"[卡住] 连续 {invalid_args} 次工具参数不合法(缺必填字段),终止本候选")
                        return AgentResult(
                            False, f"工具参数连续不合法({invalid_args} 次): {str(result)[:100]}",
                            self.history, obs.url, self._last_title)
                else:
                    invalid_args = 0

            self.history.append(StepRecord(step, f"{obs.url} | {obs.title}", decision, result))
            self._log(f"[动作] {action} {decision.get('index', '')} → {result.message[:200]}")

            if action == "done":
                success = bool(decision.get("args", {}).get("success", False))
                return AgentResult(success, decision.get("value", ""), self.history,
                                   obs.url, self._last_title)

        self._log(f"\n[预算] 达到最大步数 {self.max_steps},终止")
        return AgentResult(False, "达到最大步数未完成", self.history,
                           self.session.current_url(), self._last_title)

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
        steps = self.history[-8:]
        for i, rec in enumerate(steps):
            # 最近一步的动作结果必须完整可见:analyze_page 返回的直链/图片 URL
            # 常超 120 字符,历史截断会让 LLM 误以为"URL 被截断"而去写脚本空转
            # (线上事故:壁纸任务因看不到完整 hdslb URL 陷入 sandbox 循环)
            limit = 1500 if i == len(steps) - 1 else 120
            lines.append(f"  步{rec.step}: 动作={rec.decision.get('action')} 结果={str(rec.result)[:limit]}")
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
    """容错解析 LLM 的 JSON 决策(多层修复)。

    覆盖:代码围栏、尾随垃圾文本、截断 JSON(补右括号/去悬空残缺键值)、
    单引号 Python 字典风格。全部失败才抛 ValueError。
    """
    text = (text or "").strip()
    candidates: list[str] = [text]

    # 1) 去掉 ```json ... ``` 围栏
    fence = re.sub(r"```(?:json)?", "", text).strip("` \n")
    if fence and fence != text:
        candidates.append(fence)

    # 2) 首个完整 JSON 对象(字符串感知配平);截断则尝试补全
    body = fence or text
    obj = _extract_json_object(body)
    if obj is not None:
        candidates.append(obj)
    else:
        start = body.find("{")
        if start >= 0:
            for repaired in _repair_truncated(body[start:]):
                candidates.append(repaired)

    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue

    # 3) Python 字面量风格兜底:单引号 dict / True/False/None
    #    (ast.literal_eval 只解析字面量,安全;JSON 标准不接受这些,最后才试)
    import ast

    for cand in candidates:
        try:
            v = ast.literal_eval(cand)
            if isinstance(v, dict):
                return v
        except Exception:
            continue
    raise ValueError("无法解析决策 JSON")


def _extract_json_object(text: str) -> Optional[str]:
    """字符串感知括号配平:返回从第一个 '{' 开始、配平的 JSON 对象原文。
    不配平(输出被截断)返回 None。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair_truncated(prefix: str) -> list[str]:
    """截断 JSON 修复:补右括号;再去掉悬空的残缺键值后补右括号。"""
    out: list[str] = []
    depth = 0
    in_str = esc = False
    for ch in prefix:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    if depth <= 0:
        return out
    out.append(prefix + "}" * depth)
    # 末尾残缺 `,"value":"abc`(字符串未闭合)→ 去掉该残缺片段
    m = re.search(r',\s*"[^"]*"\s*:\s*"[^"]*$', prefix)
    if m:
        out.append(prefix[: m.start()] + "}" * depth)
    # 末尾残缺 `,"index":0`(值不完整)→ 去掉
    m2 = re.search(r',\s*"[^"]*"\s*:\s*[^,}\s]+$', prefix)
    if m2:
        out.append(prefix[: m2.start()] + "}" * depth)
    # 末尾残缺 `,"key":`(等值)
    m3 = re.search(r',\s*"[^"]*"\s*:\s*$', prefix)
    if m3:
        out.append(prefix[: m3.start()] + "}" * depth)
    return out
