"""Resource Hub API 全量测试(httpx TestClient)。

隔离策略:
- RH_DB_PATH 指向临时 SQLite(在 import api.app 前设置);
- 覆盖 api.app.DATA_DIR 到临时沙箱;
- 用同步假执行器替换线程池,用假 fetch_resource 替换真实检索下载(不碰网络)。

覆盖:令牌生命周期/认证/隔离、任务契约/幂等/取消、SSE 事件流、
Webhook 签名、文件下载(单文件/Range 206/zip)、错误信封。
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

# ---- 隔离:必须在 import api.app 之前 ----
_tmp = tempfile.mkdtemp(prefix="rh_api_test_")
os.environ["RH_DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ.pop("RH_API_SECRET", None)  # 默认个人模式;令牌模式测试里再设置

from fastapi.testclient import TestClient  # noqa: E402

from importlib import import_module  # noqa: E402

# 注意:api/__init__.py 把 api.app 属性重绑成了 FastAPI 实例,必须用 import_module 拿模块
api_mod = import_module("api.app")
from api.tasks_store import TaskStore  # noqa: E402
from api.token_store import TokenStore  # noqa: E402

api_mod.DATA_DIR = Path(_tmp) / "sandbox" / "tasks"
api_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)

_store: TaskStore = api_mod._store
_tokens: TokenStore = api_mod._tokens


class SyncExecutor:
    """同步执行器:submit 直接跑,返回 False 模拟队列满。"""

    def __init__(self, accept: bool = True):
        self.accept = accept
        self.submitted = []

    def submit(self, task_id, fn):
        if not self.accept:
            return False
        self.submitted.append(task_id)
        fn()
        return True


def _fake_fetch(result_success: bool = True, file_name: str = "res.litematic",
                cancel_after: float | None = None, error: str = ""):
    """假 fetch_resource:写一个文件进 out_dir,按需走取消路径。

    注意:agent/tasks/__init__.py 把包的 fetch_resource 属性重绑成了函数,
    `import agent.tasks.fetch_resource as fr` 拿到的不是模块 —— 必须用 import_module。
    """
    import importlib

    fr = importlib.import_module("agent.tasks.fetch_resource")

    def fake(query="", seed_urls=None, file_types=None, out_dir="downloads",
             login_email="", username="", use_agent_fallback=True,
             security_scan=True, reuse_cookies=True,
             on_stage=None, is_cancelled=None, verbose=True):
        if on_stage:
            on_stage("search", "fake search")
            on_stage("download", "fake download")
        if cancel_after:
            # 模拟长任务:循环等待,期间可被取消
            deadline = time.monotonic() + cancel_after
            while is_cancelled and not is_cancelled():
                if time.monotonic() > deadline:
                    break
                time.sleep(0.01)
        if is_cancelled and is_cancelled():
            raise fr.TaskCancelled()
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        f = out / file_name
        f.write_bytes(b"fake-resource-content-0123456789")
        return fr.TaskResult(success=result_success, summary="fake done",
                             files=[str(f)], error=error)

    fr.fetch_resource = fake
    return fake


def _install(accept: bool = True, **fake_kwargs):
    api_mod.get_executor = lambda: SyncExecutor(accept=accept)
    _fake_fetch(**fake_kwargs)


class APITestCase(unittest.TestCase):
    def setUp(self):
        # 每个用例重置存储(截断表),保证隔离
        for conn in (_store._conn, _tokens._conn):
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM tokens")
            conn.commit()
        os.environ.pop("RH_API_SECRET", None)
        _install()


class TestTokenLifecycle(APITestCase):
    def test_apply_list_rotate_revoke(self):
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tokens", json={"name": "dev"})
        self.assertEqual(r.status_code, 201)
        data = r.json()["data"]
        self.assertTrue(data["token_key"].startswith("rh_live_"))

        # 列表(个人模式:owner=local)
        r = client.get("/api/v1/tokens")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["count"], 1)

        # 轮换:旧钥作废(记录仍在,status=revoked),新令牌 id 不同
        r = client.post(f"/api/v1/tokens/{data['token_id']}/rotate")
        self.assertEqual(r.status_code, 200)
        new_key = r.json()["data"]["token_key"]
        new_id = r.json()["data"]["token_id"]
        self.assertNotEqual(new_key, data["token_key"])
        self.assertEqual(_tokens.get_by_key(data["token_key"])["status"], "revoked")
        self.assertEqual(_tokens.get_by_key(new_key)["status"], "active")

        # 吊销新令牌:认证层应拒绝
        r = client.delete(f"/api/v1/tokens/{new_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_tokens.get_by_key(new_key)["status"], "revoked")

    def test_token_mode_requires_bearer(self):
        os.environ["RH_API_SECRET"] = "test-secret"
        client = TestClient(api_mod.app)
        # 无令牌 → 401
        r = client.get("/api/v1/me")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "UNAUTHORIZED")
        # 申请令牌(onboarding)
        r = client.post("/api/v1/tokens", json={"name": "x"})
        self.assertEqual(r.status_code, 201)
        key = r.json()["data"]["token_key"]
        # 带令牌 → 通过
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["token_mode"], True)
        # 伪造令牌 → 401
        r = client.get("/api/v1/me", headers={"Authorization": "Bearer rh_live_bogus"})
        self.assertEqual(r.status_code, 401)


class TestTaskContract(APITestCase):
    def test_create_poll_done(self):
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks", json={"query": "故宫 投影 litematic"})
        self.assertEqual(r.status_code, 202)
        data = r.json()["data"]
        task_id = data["task_id"]
        self.assertTrue(data["polling_url"].endswith(task_id))
        self.assertTrue(data["file_token"])

        r = client.get(f"/api/v1/tasks/{task_id}")
        body = r.json()["data"]
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["result"]["success"], True)
        self.assertEqual(len(body["files"]), 1)
        f = body["files"][0]
        self.assertEqual(f["name"], "res.litematic")
        self.assertTrue(f["url"].endswith("/files/res.litematic"))
        self.assertEqual(len(f["sha256"]), 64)
        self.assertIn("verdict", f)

    def test_invalid_request_envelope(self):
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks", json={"query": "", "seed_urls": []})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["code"], "INVALID_REQUEST")

    def test_failed_task(self):
        _install(result_success=False, error="no direct link")
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks", json={"query": "x"})
        task_id = r.json()["data"]["task_id"]
        body = client.get(f"/api/v1/tasks/{task_id}").json()["data"]
        self.assertEqual(body["status"], "failed")
        self.assertIn("no direct link", body["error"])

    def test_queue_full_503(self):
        _install(accept=False)
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks", json={"query": "x"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["code"], "SERVICE_UNAVAILABLE")

    def test_idempotency_key(self):
        client = TestClient(api_mod.app)
        body = {"query": "壁纸", "idempotency_key": "dup-1"}
        r1 = client.post("/api/v1/tasks", json=body)
        r2 = client.post("/api/v1/tasks", json=body)
        self.assertEqual(r1.json()["data"]["task_id"], r2.json()["data"]["task_id"])
        self.assertEqual(r2.json()["data"]["status"], "done")  # 命中既有结果

    def test_cancel(self):
        """协作式取消:任务运行中被 cancel → 状态变为 cancelled。"""
        import threading

        from api.app import TaskRequest

        _install(cancel_after=30)   # 假任务最长等 30s,期间可被取消
        client = TestClient(api_mod.app)
        tid = _store.create("local", "x")
        t = threading.Thread(target=api_mod._run_task,
                             args=(tid, TaskRequest(query="x"), "local"),
                             daemon=True)
        t.start()
        time.sleep(1.0)             # 让假任务进入等待循环
        self.assertEqual(_store.get(tid)["status"], "running")
        r = client.post(f"/api/v1/tasks/{tid}/cancel")
        self.assertEqual(r.status_code, 200)
        t.join(timeout=10)
        self.assertFalse(t.is_alive())
        self.assertEqual(_store.get(tid)["status"], "cancelled")

    def test_history_list_pagination(self):
        client = TestClient(api_mod.app)
        for i in range(3):
            client.post("/api/v1/tasks", json={"query": f"q{i}"})
        r = client.get("/api/v1/tasks?limit=2&offset=0")
        data = r.json()["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 2)
        r = client.get("/api/v1/tasks?status=done")
        self.assertEqual(r.json()["data"]["total"], 3)
        r = client.get("/api/v1/tasks?status=bogus")
        self.assertEqual(r.status_code, 422)


class TestIsolation(APITestCase):
    def test_token_isolation(self):
        os.environ["RH_API_SECRET"] = "test-secret"
        client = TestClient(api_mod.app)
        ka = client.post("/api/v1/tokens", json={"name": "A"}).json()["data"]["token_key"]
        kb = client.post("/api/v1/tokens", json={"name": "B"}).json()["data"]["token_key"]
        ha = {"Authorization": f"Bearer {ka}"}
        hb = {"Authorization": f"Bearer {kb}"}

        r = client.post("/api/v1/tasks", json={"query": "A 的任务"}, headers=ha)
        task_id = r.json()["data"]["task_id"]

        # B 看不到 A 的任务(404,不泄露存在性)
        r = client.get(f"/api/v1/tasks/{task_id}", headers=hb)
        self.assertEqual(r.status_code, 404)
        # B 的历史里也没有
        r = client.get("/api/v1/tasks", headers=hb)
        self.assertEqual(r.json()["data"]["total"], 0)
        # A 自己能看到
        r = client.get(f"/api/v1/tasks/{task_id}", headers=ha)
        self.assertEqual(r.status_code, 200)

    def test_file_access_denied_without_token(self):
        os.environ["RH_API_SECRET"] = "test-secret"
        client = TestClient(api_mod.app)
        ka = client.post("/api/v1/tokens", json={"name": "A"}).json()["data"]["token_key"]
        ha = {"Authorization": f"Bearer {ka}"}
        r = client.post("/api/v1/tasks", json={"query": "x"}, headers=ha)
        task_id = r.json()["data"]["task_id"]
        file_token = r.json()["data"]["file_token"]

        # 无凭证 → 403;带 file_token → 200
        r = client.get(f"/api/v1/tasks/{task_id}/files/res.litematic")
        self.assertEqual(r.status_code, 403)
        r = client.get(f"/api/v1/tasks/{task_id}/files/res.litematic",
                       params={"token": file_token})
        self.assertEqual(r.status_code, 200)


class TestFileDelivery(APITestCase):
    def _make_done_task(self):
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks", json={"query": "x"})
        task_id = r.json()["data"]["task_id"]
        return client, task_id

    def test_download_full_and_range(self):
        client, task_id = self._make_done_task()
        url = f"/api/v1/tasks/{task_id}/files/res.litematic"
        body = b"fake-resource-content-0123456789"

        # 完整下载
        r = client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, body)
        self.assertEqual(r.headers.get("accept-ranges"), "bytes")

        # Range 断点续传:bytes=5-14 → 206 + Content-Range + 正确切片
        r = client.get(url, headers={"Range": "bytes=5-14"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, body[5:15])
        self.assertEqual(r.headers.get("content-range"), f"bytes 5-14/{len(body)}")
        self.assertEqual(r.headers.get("content-length"), "10")

        # 后缀区间 bytes=-6 → 最后 6 字节
        r = client.get(url, headers={"Range": "bytes=-6"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, body[-6:])

        # 越界 → 416
        r = client.get(url, headers={"Range": f"bytes={len(body) + 10}-"})
        self.assertEqual(r.status_code, 416)
        self.assertEqual(r.headers.get("content-range"), f"bytes */{len(body)}")

    def test_download_all_zip(self):
        client, task_id = self._make_done_task()
        r = client.get(f"/api/v1/tasks/{task_id}/download-all")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "application/zip")
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            self.assertIn("res.litematic", z.namelist())

    def test_path_traversal_blocked(self):
        client, task_id = self._make_done_task()
        r = client.get(f"/api/v1/tasks/{task_id}/files/..%2F..%2Fcredentials.json")
        self.assertEqual(r.status_code, 404)


class TestSSE(APITestCase):
    def test_sse_stream(self):
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks", json={"query": "x"})
        task_id = r.json()["data"]["task_id"]
        # 任务已 done(同步执行器),事件流应回放 search/download/done
        lines = []
        with client.stream("GET", f"/api/v1/tasks/{task_id}/events") as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line:
                    lines.append(line)
        joined = "\n".join(lines)
        self.assertIn("event: stage", joined)
        self.assertIn("event: done", joined)
        self.assertIn("fake search", joined)


class TestWebhook(APITestCase):
    def test_signature_and_delivery(self):
        from api.webhooks import get_webhook_secret, sign

        secret = get_webhook_secret()
        payload = b'{"event": "task.done"}'
        sig = sign(payload, secret)
        import hmac

        self.assertTrue(hmac.compare_digest(sig, sign(payload, secret)))
        self.assertEqual(len(sig), 64)

    def test_unsafe_callback_rejected(self):
        client = TestClient(api_mod.app)
        r = client.post("/api/v1/tasks",
                        json={"query": "x", "callback_url": "file:///etc/passwd"})
        self.assertEqual(r.status_code, 422)
        r = client.post("/api/v1/tasks",
                        json={"query": "x", "callback_url": "javascript:alert(1)"})
        self.assertEqual(r.status_code, 422)


class TestMe(APITestCase):
    def test_me_overview(self):
        client = TestClient(api_mod.app)
        client.post("/api/v1/tasks", json={"query": "x"})
        r = client.get("/api/v1/me")
        data = r.json()["data"]
        self.assertEqual(data["tasks"], 1)
        self.assertEqual(data["token_mode"], False)
        self.assertIn("storage_bytes", data)


if __name__ == "__main__":
    unittest.main()
