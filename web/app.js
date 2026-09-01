/* Resource Hub 控制台逻辑 — 原生 JS,零依赖;SSE 用 fetch 流解析(支持 Bearer 头)。 */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "rh_console_token";
const ADMIN_KEY = "rh_admin_token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";
let ADMIN_TOKEN = localStorage.getItem(ADMIN_KEY) || "";

// ---------------- API 基础 ----------------
// 网页访问免令牌:统一带 X-RH-Web 标记(令牌只约束程序化 API 客户端)
const WEB_HEADERS = { "X-RH-Web": "1" };
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...WEB_HEADERS, ...(opts.headers || {}) };
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  if (ADMIN_TOKEN) headers["X-RH-Admin"] = ADMIN_TOKEN;
  const resp = await fetch(path, { ...opts, headers });
  let body = null;
  try { body = await resp.json(); } catch (e) { /* 非 JSON */ }
  if (resp.status === 401) {
    throw new Error("需要令牌(网页会话异常,刷新页面重试)");
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
  const headers = { ...WEB_HEADERS };
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
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
    set("token", !!st.token_mode, st.token_mode ? "仅程序化API" : "个人");
    set("clamav", st.clamav, st.clamav ? "ClamAV ✅" : "仅启发式");
  } catch (e) { /* 网页会话异常 */ }
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
  if (!query) return toast("请输入想要获取的内容", true);
  const kind = $("dlKind").value;
  const payload = {
    query,
    label: query.slice(0, 40),
    file_types: kind === "auto" ? [] : KIND_TYPES[kind],
    login_email: $("dlEmail").value.trim(),
  };
  $("dlSubmit").disabled = true;
  $("dlCancel").disabled = false;
  $("dlProgress").hidden = false;
  $("dlEvents").textContent = "";
  $("dlFiles").innerHTML = "";
  $("dlPanLinks").innerHTML = "";
  $("dlPanCard").hidden = true;
  $("dlFilesCard").hidden = true;
  $("dlBar").style.width = "0%";
  $("dlStage").textContent = "queued";
  $("dlStage").className = "badge";
  $("dlStageMsg").textContent = "已提交,等待执行…";
  try {
    const data = await api("/api/v1/tasks", { method: "POST", body: JSON.stringify(payload) });
    $("dlTaskId").textContent = data.task_id;
    logEvent("queued", "任务已提交,file_token=" + (data.file_token || "").slice(0, 8) + "…");
    const tid = data.task_id;
    _taskEventsSeen = 0;
    _taskFinished = false;
    try {
      await sse(data.events_url, {
        stage: (e) => renderStageEvent(tid, e),
        done: async (e) => {
          if (e.status) { logEvent(e.status, "任务结束: " + e.status); await finishTask(tid); }
        },
        failed: async () => { logEvent("failed", "任务失败"); await finishTask(tid); },
        cancelled: async () => { logEvent("cancelled", "任务已取消"); await finishTask(tid); },
      });
    } finally {
      // SSE 流结束(网络波动/手机息屏切回/服务端关闭)而任务未终态 → 轮询补偿,
      // 进度条不再永久卡在中间状态
      pollUntilDone(tid);
    }
  } catch (e) {
    logEvent("error", String(e.message || e));
    toast("提交失败: " + (e.message || e), true);
    $("dlSubmit").disabled = false;
    $("dlCancel").disabled = true;
  }
}

// ---------------- 任务进度(SSE 实时 + 轮询补偿双通道) ----------------
let _taskEventsSeen = 0;   // 轮询补偿时已渲染的事件数
let _taskFinished = false; // 终态已处理(防止双通道重复渲染)

function renderStageEvent(tid, e) {
  const pct = e.percent || 0;
  $("dlStage").textContent = e.stage || "stage";
  $("dlStage").className = "badge running";
  $("dlBar").style.width = pct + "%";
  $("dlStageMsg").textContent = (e.message || "") + (pct ? ` [${pct}%]` : "");
  logEvent(e.stage, e.message || "");
}

async function finishTask(tid) {
  if (_taskFinished) return;
  _taskFinished = true;
  let task;
  try { task = await api(`/api/v1/tasks/${tid}`); }
  catch (e) {
    $("dlStageMsg").textContent = "任务已结束(状态获取失败)";
    $("dlSubmit").disabled = false;
    $("dlCancel").disabled = true;
    return;
  }
  const st = task.status || "done";
  $("dlStage").textContent = st;
  $("dlStage").className = "badge " + st;
  $("dlBar").style.width = "100%";
  if (st === "done") {
    renderPanLinks(task.pan_links || [], $("dlPanLinks"));
    $("dlPanCard").hidden = !(task.pan_links || []).length;
    $("dlPanCount").textContent = `(${(task.pan_links || []).length})`;
    renderFiles(task.files, $("dlFiles"), tid);
    $("dlFilesCard").hidden = !(task.files || []).length;
    $("dlFilesCount").textContent = `(${(task.files || []).length})`;
    $("dlStageMsg").textContent = (task.result && task.result.summary) || "任务完成";
  } else if (st === "failed") {
    // 失败也可能带回云盘链接(用户可手动去网盘)
    renderPanLinks(task.pan_links || [], $("dlPanLinks"));
    $("dlPanCard").hidden = !(task.pan_links || []).length;
    $("dlPanCount").textContent = `(${(task.pan_links || []).length})`;
    $("dlStageMsg").textContent = `任务失败: ${(task.result && task.result.summary) || task.error || ""}`;
  } else {
    $("dlStageMsg").textContent = `任务已${st}`;
  }
  $("dlSubmit").disabled = false;
  $("dlCancel").disabled = true;
}

async function pollUntilDone(tid, attempts = 400) {
  for (let i = 0; i < attempts && !_taskFinished; i++) {
    try {
      const task = await api(`/api/v1/tasks/${tid}`);
      const evs = task.events || [];
      for (const e of evs.slice(_taskEventsSeen)) {
        if (e.type === "stage") renderStageEvent(tid, e);
        else logEvent(e.type, e.message || "");
      }
      _taskEventsSeen = evs.length;
      if (["done", "failed", "cancelled"].includes(task.status)) { await finishTask(tid); return; }
    } catch (e) { /* 网络抖动:下一轮再试 */ }
    await new Promise((r) => setTimeout(r, 2500));
  }
}

function logEvent(ev, msg) {
  const el = $("dlEvents");
  el.textContent += `[${new Date().toLocaleTimeString()}] ${ev}: ${msg}\n`;
  el.scrollTop = el.scrollHeight;
}

$("dlSubmit").addEventListener("click", submitTask);
// 示例 prompt 一键填充
document.querySelectorAll("#dlChips .chip").forEach((c) => {
  c.addEventListener("click", () => {
    $("dlQuery").value = c.dataset.q;
    $("dlQuery").focus();
  });
});
$("dlCancel").addEventListener("click", async () => {
  const tid = $("dlTaskId").textContent.trim();
  if (!tid) return;
  // 即时反馈:立刻进入 cancelling 灰色等待态(不再显示"仍在运行"造成"取消无效"困惑)
  $("dlCancel").disabled = true;
  $("dlStage").textContent = "cancelling";
  $("dlStage").className = "badge cancelling";
  $("dlStageMsg").textContent = "取消请求已提交,任务正在停止…";
  logEvent("cancel", "已请求取消,等待任务停止…");
  try { await api(`/api/v1/tasks/${tid}/cancel`, { method: "POST" }); }
  catch (e) { toast(e.message, true); $("dlCancel").disabled = false; }
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
    // 云盘链接 + 文件
    const panBox = document.getElementById("histPan");
    if (panBox) {
      const pan = t.pan_links || [];
      panBox.hidden = !pan.length;
      panBox.querySelector(".pan-grid").innerHTML = "";
      renderPanLinks(pan, panBox.querySelector(".pan-grid"));
    }
    renderFiles(t.files || [], $("histDetailFiles"), tid);
  } catch (e) { toast(e.message, true); }
}

$("histRefresh").addEventListener("click", loadHistory);

// ---------------- 云盘链接卡片 ----------------
function panInfo(url) {
  const u = new URL(url);
  const h = u.hostname;
  if (h.includes("pan.baidu.com")) return { icon: "📗", label: "百度网盘" };
  if (h.includes("quark")) return { icon: "🔵", label: "夸克网盘" };
  if (h.includes("alipan") || h.includes("aliyundrive")) return { icon: "📘", label: "阿里云盘" };
  if (h.includes("lanzou")) return { icon: "🔶", label: "蓝奏云" };
  if (h.includes("123pan")) return { icon: "🔷", label: "123云盘" };
  if (h.includes("115")) return { icon: "🟣", label: "115 网盘" };
  if (h.includes("pan.xunlei") || h.includes("xl")) return { icon: "🟢", label: "迅雷云盘" };
  return { icon: "📎", label: h.replace(/^www\./, "") };
}

function renderPanLinks(links, container) {
  container.innerHTML = "";
  if (!links || !links.length) {
    container.innerHTML = '<p class="hint">(未发现网盘分享链接)</p>';
    return;
  }
  for (const raw of links) {
    let info;
    try { info = panInfo(raw); } catch (e) { info = { icon: "📎", label: "链接" }; }
    const card = document.createElement("div");
    card.className = "pan-card";
    card.innerHTML = `
      <div class="pan-title">${info.icon} ${esc(info.label)}</div>
      <div class="pan-url mono">${esc(raw.slice(0, 90))}</div>
      <div class="form-row">
        <a class="btn sm primary" href="${esc(raw)}" target="_blank" rel="noopener">打开</a>
        <button class="btn sm copy-btn">复制</button>
      </div>`;
    card.querySelector(".copy-btn").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(raw); toast("✅ 已复制链接"); }
      catch (e) { toast("复制失败,请手动选择复制", true); }
    });
    container.appendChild(card);
  }
}

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
    $("cfgAdminBox").hidden = true;
    for (const f of CFG_FIELDS) {
      const el = $("cfg_" + f);
      const v = fields[f];
      el.placeholder = v.set ? (v.sensitive ? "已配置(" + v.value + "),留空=不修改,输入=替换" : "当前: " + v.value) : "";
      el.dataset.masked = v.set && v.sensitive ? v.value : "";
      el.value = "";
    }
  } catch (e) {
    // 配置读取需要管理员凭证 → 显示解锁框
    $("cfgAdminBox").hidden = false;
    toast(e.message, true);
  }
}

$("cfgAdminUnlock").addEventListener("click", () => {
  const t = $("cfgAdminToken").value.trim();
  if (!t) return toast("请输入管理员令牌", true);
  ADMIN_TOKEN = t;
  localStorage.setItem(ADMIN_KEY, t);
  $("cfgAdminToken").value = "";
  loadSettings();
});

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
// ---------------- 令牌 tab(仅程序化 API 客户端使用) ----------------
$("tokApply").addEventListener("click", async () => {
  try {
    const data = await api("/api/v1/tokens", { method: "POST", body: JSON.stringify({ name: $("tokName").value.trim() }) });
    const box = $("tokNewKey");
    box.hidden = false;
    box.textContent = `token_key: ${data.token_key}\n(申请一次长期有效,明文仅此一次! 程序化 API 用它做 Bearer 认证;网页访问无需令牌)\nowner: ${data.owner}`;
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

// ---------------- 工具函数 ----------------
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmtSize(n) { if (!n && n !== 0) return "?"; if (n > 1 << 30) return (n / (1 << 30)).toFixed(2) + " GB"; if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB"; if (n > 1024) return (n / 1024).toFixed(0) + " KB"; return n + " B"; }
function fmtTime(ts) { if (!ts) return "—"; const d = new Date(ts * 1000); return d.toLocaleString("zh-CN", { hour12: false }); }

// 启动
refreshStatus();
loadHistory();
