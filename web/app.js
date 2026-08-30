/* Resource Hub 控制台逻辑 — 原生 JS,零依赖;SSE 用 fetch 流解析(支持 Bearer 头)。 */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "rh_console_token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";

// ---------------- API 基础 ----------------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  const resp = await fetch(path, { ...opts, headers });
  let body = null;
  try { body = await resp.json(); } catch (e) { /* 非 JSON */ }
  if (resp.status === 401) {
    showLogin(true);
    throw new Error("需要令牌登录");
  }
  if (!resp.ok || (body && body.code !== 0)) {
    const msg = (body && (body.message || body.detail)) || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return body ? body.data : null;
}

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), 3200);
}

// SSE over fetch(带 Authorization;解析 event/data 行)
async function sse(path, handlers) {
  const headers = TOKEN ? { Authorization: "Bearer " + TOKEN } : {};
  const resp = await fetch(path, { headers });
  if (!resp.ok) throw new Error(`SSE HTTP ${resp.status}`);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let ev = "message", data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) ev = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
        else if (line.startsWith(":")) continue; // keep-alive
      }
      if (data) {
        try { handlers[ev] && handlers[ev](JSON.parse(data)); }
        catch (e) { /* 忽略坏帧 */ }
      }
    }
  }
}

// ---------------- 顶栏状态 ----------------
async function refreshStatus() {
  try {
    const st = await api("/api/v1/status");
    const set = (k, ok, txt) => {
      const b = document.querySelector(`.pill[data-k="${k}"] b`);
      if (!b) return;
      b.textContent = txt;
      b.closest(".pill").className = "pill " + (ok ? "ok" : ok === null ? "dim" : "no");
    };
    set("llm", st.llm_key_set, st.llm_key_set ? "已配置" : "未配置");
    set("mail", st.mail_set, st.mail_set ? "已配置" : "未配置");
    set("proxy", !!st.proxy_url, st.proxy_url ? st.proxy_url.replace(/^https?:\/\//, "").slice(0, 22) : "直连");
    set("token", !!st.token_mode, st.token_mode ? "强制" : "个人");
    set("clamav", st.clamav, st.clamav ? "ClamAV ✅" : "仅启发式");
    $("loginBtn").style.display = st.token_mode ? "" : "none";
  } catch (e) { /* 401 会触发登录框 */ }
}

// ---------------- Tab 切换 ----------------
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab-page").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("tab-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "history") loadHistory();
    if (t.dataset.tab === "tokens") loadTokens();
    if (t.dataset.tab === "settings") loadSettings();
  });
});

// ---------------- 下载 ----------------
const KIND_TYPES = {
  image: [".jpg", ".jpeg", ".png", ".webp", ".gif"],
  audio: [".mp3", ".flac", ".wav", ".ogg", ".m4a"],
  video: [".mp4", ".mkv", ".webm", ".avi", ".mov"],
  document: [".pdf", ".epub", ".mobi", ".txt"],
  schematic: [".litematic", ".schematic", ".schem", ".zip"],
};

async function submitTask() {
  const query = $("dlQuery").value.trim();
  if (!query) return toast("请输入要下载的内容", true);
  const kind = $("dlKind").value;
  const payload = {
    query,
    label: query.slice(0, 40),
    file_types: kind === "auto" ? [] : KIND_TYPES[kind],
    login_email: $("dlEmail").value.trim(),
    callback_url: $("dlCallback").value.trim(),
  };
  $("dlSubmit").disabled = true;
  $("dlCancel").disabled = false;
  $("dlProgress").hidden = false;
  $("dlEvents").textContent = "";
  $("dlFiles").innerHTML = "";
  $("dlBar").style.width = "0%";
  $("dlStage").textContent = "queued";
  $("dlStage").className = "badge";
  $("dlStageMsg").textContent = "已提交,等待执行…";
  try {
    const data = await api("/api/v1/tasks", { method: "POST", body: JSON.stringify(payload) });
    $("dlTaskId").textContent = data.task_id;
    logEvent("queued", "任务已提交,file_token=" + (data.file_token || "").slice(0, 8) + "…");
    const tid = data.task_id;
    await sse(data.events_url, {
      stage: (e) => {
        const pct = e.percent || 0;
        $("dlStage").textContent = e.stage || "stage";
        $("dlStage").className = "badge running";
        $("dlBar").style.width = pct + "%";
        $("dlStageMsg").textContent = (e.message || "") + (pct ? ` [${pct}%]` : "");
        logEvent(e.stage, e.message || "");
      },
      done: async (e) => {
        $("dlStage").textContent = e.status;
        $("dlStage").className = "badge " + e.status;
        $("dlBar").style.width = "100%";
        logEvent(e.status, "任务结束: " + e.status);
        if (e.status === "done") {
          const task = await api(`/api/v1/tasks/${tid}`);
          renderFiles(task.files, $("dlFiles"), tid);
        }
        $("dlSubmit").disabled = false;
        $("dlCancel").disabled = true;
      },
      failed: async (e) => { $("dlSubmit").disabled = false; $("dlCancel").disabled = true; logEvent("failed", "任务失败"); },
      cancelled: async (e) => { $("dlSubmit").disabled = false; $("dlCancel").disabled = true; logEvent("cancelled", "任务已取消"); },
    });
  } catch (e) {
    logEvent("error", String(e.message || e));
    toast("提交失败: " + (e.message || e), true);
    $("dlSubmit").disabled = false;
    $("dlCancel").disabled = true;
  }
}

function logEvent(ev, msg) {
  const el = $("dlEvents");
  el.textContent += `[${new Date().toLocaleTimeString()}] ${ev}: ${msg}\n`;
  el.scrollTop = el.scrollHeight;
}

$("dlSubmit").addEventListener("click", submitTask);
$("dlCancel").addEventListener("click", async () => {
  const tid = $("dlTaskId").textContent.trim();
  if (!tid) return;
  try { await api(`/api/v1/tasks/${tid}/cancel`, { method: "POST" }); logEvent("cancel", "已请求取消"); }
  catch (e) { toast(e.message, true); }
});

// ---------------- 历史 ----------------
async function loadHistory() {
  try {
    const data = await api("/api/v1/tasks?limit=50");
    const tb = $("histTable").querySelector("tbody");
    tb.innerHTML = "";
    for (const t of data.items) {
      const tr = document.createElement("tr");
      tr.className = "click";
      tr.innerHTML = `
        <td class="mono">${t.id}</td>
        <td>${esc(t.query || "")}</td>
        <td><span class="status-chip ${t.status}">${t.status}</span></td>
        <td>${t.stage || ""} ${t.percent || 0}%</td>
        <td>${t.files ? t.files.length : 0}</td>
        <td>${fmtTime(t.created_at)}</td>`;
      tr.addEventListener("click", () => showHistoryDetail(t.id));
      tb.appendChild(tr);
    }
    if (!data.items.length) tb.innerHTML = '<tr><td colspan="6" class="hint">暂无任务记录</td></tr>';
  } catch (e) { toast(e.message, true); }
}

async function showHistoryDetail(tid) {
  try {
    const t = await api(`/api/v1/tasks/${tid}`);
    $("histDetail").hidden = false;
    $("histDetailTitle").textContent = `任务 ${tid} · ${t.status}`;
    const evs = (t.events || []).map((e) => `[${fmtTime(e.ts)}] ${e.type}: ${e.message || ""}`).join("\n");
    const res = t.result || {};
    $("histDetailBody").textContent =
      `查询: ${t.query || ""}\n状态: ${t.status}\n阶段: ${t.stage} ${t.percent}%\n` +
      `结果: ${res.summary || ""}\n错误: ${t.error || res.error || "(无)"}\n\n-- 事件流 --\n${evs}`;
    renderFiles(t.files || [], $("histDetailFiles"), tid);
  } catch (e) { toast(e.message, true); }
}

$("histRefresh").addEventListener("click", loadHistory);

// ---------------- 文件卡片 ----------------
function renderFiles(files, container, tid) {
  container.innerHTML = "";
  if (!files || !files.length) { container.innerHTML = '<p class="hint">(无文件)</p>'; return; }
  for (const f of files) {
    const card = document.createElement("div");
    card.className = "file-card";
    const verdict = f.verdict || "unknown";
    card.innerHTML = `
      <div class="name">${esc(f.name)}<span class="verdict ${verdict}">${verdict}</span></div>
      <div class="meta">${fmtSize(f.size)} · sha256: ${(f.sha256 || "").slice(0, 16)}…</div>
      <a class="btn sm" href="${f.url}" download>⬇️ 下载</a>
      ${f.url ? `<a class="btn sm ghost" href="${f.url}" target="_blank">预览</a>` : ""}`;
    container.appendChild(card);
  }
}

// ---------------- 配置 ----------------
const CFG_FIELDS = ["llm_api_key", "llm_base_url", "llm_model", "mail_imap_host",
  "mail_imap_port", "mail_email", "mail_password", "proxy_url", "api_secret"];

async function loadSettings() {
  try {
    const { fields } = await api("/api/v1/settings");
    for (const f of CFG_FIELDS) {
      const el = $("cfg_" + f);
      const v = fields[f];
      el.placeholder = v.set ? (v.sensitive ? "已配置(" + v.value + "),留空=不修改,输入=替换" : "当前: " + v.value) : "";
      el.dataset.masked = v.set && v.sensitive ? v.value : "";
      el.value = "";
    }
  } catch (e) { toast(e.message, true); }
}

$("cfgSave").addEventListener("click", async () => {
  const payload = {};
  for (const f of CFG_FIELDS) {
    const el = $("cfg_" + f);
    const val = el.value.trim();
    if (val === "") continue;                      // 留空 = 不修改(掩码占位不提交)
    if (el.dataset.masked && val === el.dataset.masked) continue;  // 用户把掩码贴回来 → 忽略
    payload[f] = val;
  }
  if (!Object.keys(payload).length) return toast("没有要保存的改动");
  try {
    const data = await api("/api/v1/settings", { method: "PUT", body: JSON.stringify(payload) });
    $("cfgMsg").textContent = "✅ 已保存 " + data.changes.length + " 项,立即生效";
    await refreshStatus();
    await loadSettings();
  } catch (e) { toast("保存失败: " + e.message, true); }
});

$("cfgTestLlm").addEventListener("click", async () => {
  $("cfgTestLlm").textContent = "测试中…";
  try {
    const r = await api("/api/v1/test-llm", { method: "POST" });
    $("cfgMsg").textContent = r.ok
      ? `✅ LLM 连通,耗时 ${r.latency_ms}ms,回复: ${r.reply || ""}`
      : `❌ LLM 测试失败: ${r.error}`;
  } catch (e) { $("cfgMsg").textContent = "❌ " + e.message; }
  $("cfgTestLlm").textContent = "🧪 测试 LLM";
});

// ---------------- 令牌 ----------------
$("tokApply").addEventListener("click", async () => {
  try {
    const data = await api("/api/v1/tokens", { method: "POST", body: JSON.stringify({ name: $("tokName").value.trim() }) });
    const box = $("tokNewKey");
    box.hidden = false;
    box.textContent = `token_key: ${data.token_key}\n(明文仅此一次! 请立即保存,遗失只能轮换)\nowner: ${data.owner}`;
    await loadTokens();
  } catch (e) { toast(e.message, true); }
});

async function loadTokens() {
  try {
    const data = await api("/api/v1/tokens");
    const tb = $("tokTable").querySelector("tbody");
    tb.innerHTML = "";
    for (const t of data.items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="mono">${t.id}</td>
        <td>${esc(t.name || "")}</td>
        <td><span class="status-chip ${t.status === "active" ? "running" : "failed"}">${t.status}</span></td>
        <td>${fmtTime(t.created_at)}</td>
        <td>${t.last_used_at ? fmtTime(t.last_used_at) : "—"}</td>
        <td>
          ${t.status === "active" ? `
            <button class="btn sm" data-act="rotate" data-id="${t.id}">轮换</button>
            <button class="btn sm" data-act="revoke" data-id="${t.id}">吊销</button>` : "—"}
        </td>`;
      tb.appendChild(tr);
    }
    tb.querySelectorAll("button[data-act]").forEach((b) => {
      b.addEventListener("click", async () => {
        const id = b.dataset.id;
        try {
          if (b.dataset.act === "rotate") {
            const r = await api(`/api/v1/tokens/${id}/rotate`, { method: "POST" });
            $("tokNewKey").hidden = false;
            $("tokNewKey").textContent = `新 token_key: ${r.token_key}\n(旧令牌已作废,明文仅此一次!)`;
          } else {
            await api(`/api/v1/tokens/${id}`, { method: "DELETE" });
            toast("已吊销");
          }
          await loadTokens();
        } catch (e) { toast(e.message, true); }
      });
    });
  } catch (e) { toast(e.message, true); }
}

// ---------------- 查毒 ----------------
$("scanBtn").addEventListener("click", async () => {
  const path = $("scanPath").value.trim();
  if (!path) return toast("请输入路径", true);
  $("scanBtn").disabled = true;
  try {
    const r = await api("/api/v1/scan", { method: "POST", body: JSON.stringify({ path }) });
    const box = $("scanResult");
    box.hidden = false;
    let rows = (r.findings || []).map((f) =>
      `<div>${f.engine}: <b>${f.status}</b> ${esc(f.detail || "")}</div>`).join("");
    box.innerHTML = `
      <div class="scan-verdict ${r.verdict}">verdict: ${r.verdict}</div>
      <div class="meta mono">${esc(r.summary || "")}</div>
      <div style="margin-top:8px">${rows}</div>`;
  } catch (e) { toast(e.message, true); }
  $("scanBtn").disabled = false;
});

// ---------------- 登录弹窗 ----------------
function showLogin(show) {
  $("loginModal").hidden = !show;
  if (show) $("loginToken").focus();
}
$("loginBtn").addEventListener("click", () => showLogin(true));
$("loginOk").addEventListener("click", async () => {
  const t = $("loginToken").value.trim();
  if (!t) return;
  TOKEN = t;
  localStorage.setItem(TOKEN_KEY, t);
  showLogin(false);
  try { await refreshStatus(); toast("✅ 已登录"); }
  catch (e) { toast("令牌无效: " + e.message, true); }
});
$("loginCancel").addEventListener("click", () => showLogin(false));

// ---------------- 工具函数 ----------------
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmtSize(n) { if (!n && n !== 0) return "?"; if (n > 1 << 30) return (n / (1 << 30)).toFixed(2) + " GB"; if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB"; if (n > 1024) return (n / 1024).toFixed(0) + " KB"; return n + " B"; }
function fmtTime(ts) { if (!ts) return "—"; const d = new Date(ts * 1000); return d.toLocaleString("zh-CN", { hour12: false }); }

// 启动
refreshStatus();
loadHistory();
