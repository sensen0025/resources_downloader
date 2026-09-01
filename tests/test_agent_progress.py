"""Agent 自汇报进度:AccountAgent 的 progress 回调收到 AI 每步的 观察/推理/动作,
任务层(_agent_fetch)把它转发为 on_stage("agent", ...) 事件(SSE/网页日志可见)。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.agent import AccountAgent, AgentResult


class _FakePage:
    def __init__(self):
        self.evaluated = 0

    def evaluate(self, js: str):
        self.evaluated += 1
        if "document.body ? document.body.innerText" in js:
            return "登录页: 请输入邮箱与密码后点击登录"
        return []  # 无可交互元素 → 观察为空


class _FakeSession:
    def __init__(self):
        self.page = _FakePage()

    def current_url(self) -> str:
        return "https://x.com/login"

    def title(self) -> str:
        return "登录"

    def wait(self, ms: int) -> None:
        pass


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply

    def chat(self, messages, *, temperature=0.2, max_tokens=2048, json_mode=False) -> str:
        return self.reply


class TestAccountAgentSelfProgress(unittest.TestCase):
    """AccountAgent 把每一步的观察/推理/动作通过 progress 回调自汇报。"""

    def test_progress_receives_ai_thought_and_step(self):
        llm = _FakeLLM(
            '{"thought":"页面是登录表单,需要先填邮箱","action":"done",'
            '"args":{"success":false,"value":"需要人工介入"}}'
        )
        got: list[str] = []
        agent = AccountAgent(session=_FakeSession(), llm=llm, goal="测试任务",
                             max_steps=5, verbose=False, progress=got.append)
        res = agent.run()
        self.assertFalse(res.success)
        joined = "\n".join(got)
        self.assertIn("步骤 1/5", joined)            # 步数自汇报
        self.assertIn("[观察]", joined)              # 观察自汇报
        self.assertIn("[AI] 页面是登录表单", joined)   # 模型自己的推理
        self.assertIn("[动作]", joined)              # 执行结果自汇报

    def test_progress_callback_error_does_not_break_loop(self):
        def boom(_msg):
            raise RuntimeError("进度回调失败")

        llm = _FakeLLM(
            '{"thought":"直接结束","action":"done","args":{"success":true,"value":"完成"}}'
        )
        agent = AccountAgent(session=_FakeSession(), llm=llm, goal="t",
                             max_steps=5, verbose=False, progress=boom)
        res = agent.run()
        self.assertTrue(res.success)  # 进度回调异常被吞掉,主循环不受影响


class TestAgentFetchForwardsProgress(unittest.TestCase):
    """_agent_fetch 把 AccountAgent 的 progress 转发为 on_stage("agent", ...) 事件。"""

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.fr = importlib.import_module("agent.tasks.fetch_resource")

    def test_multiline_progress_flattened_and_tagged_agent(self):
        fr = self.fr
        events: list[tuple[str, str]] = []

        class _FakeAgent:
            def __init__(self, session, llm, goal, allowed_domain="", max_steps=30,
                         verbose=True, progress=None):
                from types import SimpleNamespace

                self.progress = progress
                self.ctx = SimpleNamespace(session=session, task=None)

            def run(self):
                task_dir = getattr(getattr(self.ctx, "task", None), "out_dir", None)
                self.progress(f"任务目录={task_dir}")
                self.progress("步骤 1/60: 打开页面\n观察 https://x.com | 登录页 | 元素 3 个")
                self.progress("推理: 这是登录表单,先填邮箱 → 动作: type")
                return AgentResult(False, "未获文件")

        class _FakeBS:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def goto(self, url):
                pass

        class _FakeLLMClient:
            def __init__(self, *a, **k):
                pass

        with tempfile.TemporaryDirectory() as td:
            with mock.patch("agent.AccountAgent", _FakeAgent), \
                 mock.patch("agent.browser.BrowserSession", _FakeBS), \
                 mock.patch("agent.llm.LLMClient", _FakeLLMClient):
                ok = fr._agent_fetch("https://x.com/login", (".txt",), Path(td),
                                     verbose=False,
                                     on_stage=lambda s, m: events.append((s, m)))
        self.assertFalse(ok)
        self.assertEqual([s for s, _ in events], ["agent", "agent", "agent"])
        # _agent_fetch 把任务目录注入 ctx.task → 工具的落盘目录被强制到任务目录
        self.assertTrue(any("任务目录=" in m and str(Path(td).resolve()) in m
                            for _, m in events))
        first = events[1][1]
        self.assertNotIn("\n", first)   # 多行日志压成单行
        self.assertTrue(first.startswith("步骤 1/60"))
        self.assertIn("https://x.com", first)
        self.assertTrue(events[2][1].startswith("推理:"))


class TestToolTaskOutDir(unittest.TestCase):
    """任务上下文(ctx.task.out_dir)注入后,写文件工具必须落到任务目录,
    否则文件在 downloads/ 交付层收不到,网页 done 却没有下载按钮(线上事故复现)。"""

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.tools = importlib.import_module("agent.tools")

    def test_download_tool_forces_task_out_dir(self):
        from types import SimpleNamespace

        calls: list = []

        def fake_download(url, dest, expected_ext="", min_size=0, referer=""):
            calls.append((url, dest))
            return SimpleNamespace(ok=True, path=str(Path(dest) / "a.txt"),
                                   size=1, resumed=False, error="")

        ctx = SimpleNamespace(session=None, task=SimpleNamespace(out_dir="/tmp/rh_task_xyz"))
        with mock.patch("delivery.download", side_effect=fake_download):
            r = self.tools._download_tool("https://x.com/a.txt", ctx=ctx)
        self.assertTrue(r.ok)
        # 跨平台:Path 会把 /tmp/... 规范化为 \tmp\...(Windows),比较解析后的结果
        self.assertEqual(Path(calls[0][1]), Path("/tmp/rh_task_xyz"))  # 强制任务目录

    def test_download_tool_keeps_default_outside_task(self):
        from types import SimpleNamespace

        calls: list = []

        def fake_download(url, dest, expected_ext="", min_size=0, referer=""):
            calls.append((url, dest))
            return SimpleNamespace(ok=True, path=str(Path(dest) / "a.txt"),
                                   size=1, resumed=False, error="")

        # 无 ctx / 无 task → 保持默认 downloads/(独立脚本/注册流程)
        with mock.patch("delivery.download", side_effect=fake_download):
            self.tools._download_tool("https://x.com/a.txt")
            self.tools._download_tool("https://x.com/b.txt",
                                      ctx=SimpleNamespace(session=None, task=None))
        self.assertEqual([str(d) for _, d in calls], ["downloads", "downloads"])


if __name__ == "__main__":
    unittest.main()
