/* Alice GUI：初始化 + 流式聊天 + 记忆栏 + 人格面板。无构建链，原生 JS。 */
"use strict";

const $ = (s) => document.querySelector(s);
let busy = false;
let memory = null;
let state = null;
let sessionEpoch = null;
let historyCursor = 0;
let historyVersion = 0;
let syncingHistory = false;
let shuttingDown = false;
let activity = null;
let syncTimer = null;
let uncertainSend = null;
let composerWorkerReference = null;
let deliveryRefreshIndex = 0;
let sourceRefreshIndex = 0;
let sourceProjectionVersion = 0;
let sourceExpiryTimer = null;
let followingLatest = true;
let historyLoaded = false;
let composing = false;
const turnNodes = new Map();
const requestNodes = new Map();
const userWorkerTargets = new WeakMap();

async function api(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  if (!headers.has("X-Alice-Intent")) headers.set("X-Alice-Intent", "gui");
  const r = await fetch(path, { ...opts, headers, cache: "no-store" });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const error = new Error(body.error || `HTTP ${r.status}`);
    error.status = r.status;
    error.code = body.code;
    if (body.import_status && typeof body.import_status === "object" && !Array.isArray(body.import_status)) {
      error.importStatus = body.import_status;
    }
    throw error;
  }
  return r.json();
}

const postJSON = (path, data) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

/* ---------- 小工具 ---------- */

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");

let toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

function workerCollaborator(value = "dsh-agent") {
  return ["dsh-agent", "claude-agent"].includes(value) ? value : null;
}

function workerDisplayName(collaborator) {
  return { "dsh-agent": "DSH", "claude-agent": "Claude" }[workerCollaborator(collaborator)] || "协作者";
}

function validWorkerTarget(target) {
  return !!target && !!workerCollaborator(target.collaborator) && typeof target.task_id === "string"
    && /^[A-Za-z0-9_-]{16,128}$/.test(target.task_id);
}

function renderComposerWorkerReference() {
  const reference = composerWorkerReference, locked = busy || !!uncertainSend;
  $("#composer-worker-reference").classList.toggle("hidden", !reference);
  $("#composer-worker-label").textContent = reference ? `${workerDisplayName(reference.collaborator)} · ${reference.title || `会话 ${reference.taskId.slice(-8)}`}` : "";
  $("#composer-worker-open").title = reference ? `查看协作任务：${reference.title || reference.taskId}` : "";
  $("#composer-worker-status").textContent = !reference ? "" : busy ? "正在提交引用消息，引用已锁定"
    : uncertainSend ? "请求结果待确认，引用已锁定"
    : reference.collaborator === "claude-agent" ? "让 Alice 查看或讨论此任务；Claude 暂不支持同会话续跑"
    : "让 Alice 处理此任务的补充或追问";
  $("#composer-worker-clear").disabled = locked || shuttingDown;
  $("#composer-worker-open").disabled = shuttingDown || !reference || reference.epoch !== sessionEpoch;
}

function setComposerWorkerReference(value) {
  if (!state || !sessionEpoch || value?.session_epoch !== sessionEpoch) {
    toast("会话尚未同步或已经更换，请同步记录后重新选择任务。"); return false;
  }
  if (busy || shuttingDown || uncertainSend) {
    toast("当前请求尚未确认，暂时不能更换任务引用。请先同步记录。"); return false;
  }
  if (!validWorkerTarget(value)) return false;
  composerWorkerReference = { collaborator: workerCollaborator(value.collaborator), taskId: value.task_id, epoch: sessionEpoch,
    title: typeof value.title === "string" && value.title.trim() ? value.title.trim().slice(0, 240) : "" };
  renderComposerWorkerReference();
  return true;
}

function confirmComposerRequest(requestId) {
  if (!uncertainSend || uncertainSend.requestId !== requestId) return;
  const pending = uncertainSend, reference = pending.workerReference;
  uncertainSend = null;
  if (reference && pending.draftUnchanged && $("#input").value.trim() === pending.text) {
    $("#input").value = ""; resizeComposer();
  }
  if (reference && composerWorkerReference?.taskId === reference.taskId && composerWorkerReference.epoch === reference.epoch
      && composerWorkerReference.collaborator === reference.collaborator) {
    composerWorkerReference = null;
  }
  renderComposerWorkerReference();
}

async function openReferencedWorkerTask(taskId, collaborator = "dsh-agent") {
  if (!validWorkerTarget({ task_id: taskId, collaborator })) return;
  if (typeof window.AliceWorkers?.openTask !== "function") { toast("协作会话入口尚未就绪，请刷新页面。"); return; }
  try { await window.AliceWorkers.openTask(taskId, collaborator); }
  catch { toast("暂时无法打开这项协作任务，请稍后同步记录。"); }
}

function markUserWorkerTarget(node, turn, localTitle = "") {
  if (!Object.hasOwn(turn, "worker_target")) return;
  const target = turn.worker_target;
  const channel = turn.channel || node.dataset.channel || "gui";
  if (!node.classList.contains("user") || channel !== "gui" || !validWorkerTarget(target)) {
    node.querySelector(".message-worker-target")?.remove(); userWorkerTargets.delete(node); return;
  }
  const previous = userWorkerTargets.get(node);
  const collaborator = workerCollaborator(target.collaborator);
  const available = target.available === true || target.available === undefined && !!localTitle;
  const record = { collaborator, taskId: target.task_id, epoch: sessionEpoch, available,
    title: available ? localTitle || (previous?.taskId === target.task_id && previous.collaborator === collaborator ? previous.title : "") : "" };
  userWorkerTargets.set(node, record);
  let button = node.querySelector(".message-worker-target");
  if (!button) {
    button = document.createElement("button"); button.type = "button"; button.className = "message-worker-target";
    button.onclick = () => {
      const current = userWorkerTargets.get(node);
      if (node.isConnected && current?.available && current.epoch === sessionEpoch) openReferencedWorkerTask(current.taskId, current.collaborator);
    };
    node.insertBefore(button, node.querySelector(".txt"));
  }
  button.textContent = `${workerDisplayName(record.collaborator)} · ${record.title || `会话 ${record.taskId.slice(-8)}`}`
    + (record.available ? "" : " · 已不可用");
  button.disabled = !record.available || record.epoch !== sessionEpoch;
  button.title = `${button.disabled ? "此任务引用当前不可用" : "查看引用的协作会话"} · ${record.taskId}`;
}

$("#composer-worker-clear").onclick = () => {
  if (busy || uncertainSend || shuttingDown) return;
  composerWorkerReference = null; renderComposerWorkerReference(); $("#input").focus({ preventScroll: true });
};
$("#composer-worker-open").onclick = () => {
  if (composerWorkerReference?.epoch === sessionEpoch && !shuttingDown) {
    openReferencedWorkerTask(composerWorkerReference.taskId, composerWorkerReference.collaborator);
  }
};
window.AliceComposer = { setWorkerReference: setComposerWorkerReference };

/* 极简 markdown：标题、列表、引用、表格、粗体、行内代码——够看人格文件了 */
function renderMd(md) {
  const out = [];
  let inList = false;
  let inQuote = false;
  let table = [];
  const closeList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };
  const closeQuote = () => {
    if (inQuote) { out.push("</blockquote>"); inQuote = false; }
  };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?[\s:|-]+\|?$/.test(r));
    table = [];
    if (!rows.length) return;
    const cells = (r) => r.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    const [head, ...rest] = rows;
    out.push(
      "<table><thead><tr>" + cells(head).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
      rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
      "</tbody></table>",
    );
  };
  for (const raw of String(md).split("\n")) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (/^\|.*\|$/.test(line.trim())) { closeList(); closeQuote(); table.push(line.trim()); continue; }
    flushTable();
    if ((m = line.match(/^>\s?(.*)$/))) {
      closeList();
      if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
      out.push(`<p>${inline(m[1])}</p>`);
    } else if ((m = line.match(/^###\s+(.*)$/))) { closeQuote(); closeList(); out.push(`<h3>${inline(m[1])}</h3>`); }
    else if ((m = line.match(/^##\s+(.*)$/))) { closeQuote(); closeList(); out.push(`<h2>${inline(m[1])}</h2>`); }
    else if ((m = line.match(/^#\s+(.*)$/))) { closeQuote(); closeList(); out.push(`<h1>${inline(m[1])}</h1>`); }
    else if ((m = line.match(/^[-*]\s+(.*)$/))) {
      closeQuote();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (!line.trim()) { closeList(); closeQuote(); }
    else { closeQuote(); closeList(); out.push(`<p>${inline(line)}</p>`); }
  }
  flushTable();
  closeList();
  closeQuote();
  return out.join("");
}

/* ---------- 聊天渲染 ---------- */

/* 表达层从简：情绪由文字本身表达（语气/措辞/节奏），GUI 只做纯文本渲染；
   历史上的言语行为标记（【情绪】/(动作)）即使出现也原样显示，不做特殊渲染。 */
function setBubbleText(txt, raw) {
  txt.dataset.raw = raw || "";
  txt.textContent = raw || "";
  const msg = txt.closest(".msg");
  if (msg) msg.classList.toggle("silence", !(raw || "").trim());
}

function bubble(role, text, { animate = false } = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  // Only newly accepted live messages enter; history, retries and off-screen reads stay still.
  if (animate && followingLatest && !document.hidden && document.documentElement.dataset.motion === "on") {
    div.classList.add("message-arriving");
  }
  const txt = document.createElement("div");
  txt.className = "txt";
  if (text) setBubbleText(txt, text);
  div.appendChild(txt);
  $("#msgs").appendChild(div);
  updateChatEmpty();
  scrollBottom();
  return div;
}

function clearMessageArrival(event) {
  if (event.animationName === "message-arrive") event.target.classList.remove("message-arriving");
}
$("#msgs").addEventListener("animationend", clearMessageArrival);
$("#msgs").addEventListener("animationcancel", clearMessageArrival);

// Presentation only: reflects this page's accepted stream, never a guessed model state.
function replyMotion(node, active) {
  $("#chat").classList.toggle("is-receiving", active);
  node?.classList.toggle("is-streaming", active);
}

function sysline(text) {
  const div = document.createElement("div");
  div.className = "sys";
  div.textContent = text;
  $("#msgs").appendChild(div);
  scrollBottom();
}

function updateChatEmpty() {
  const empty = $("#chat-empty");
  if (empty) empty.classList.toggle("hidden", !historyLoaded || !!$("#msgs").querySelector(".msg"));
}

function updateLatestButton() {
  $("#chat-latest")?.classList.toggle("hidden", followingLatest || !$("#msgs").querySelector(".msg"));
}

function scrollBottom(force = false) {
  const box = $("#msgs");
  if (force) followingLatest = true;
  if (followingLatest) box.scrollTop = box.scrollHeight;
  updateLatestButton();
}

$("#msgs").addEventListener("scroll", () => {
  const box = $("#msgs");
  followingLatest = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  updateLatestButton();
}, { passive: true });
$("#chat-latest")?.addEventListener("click", () => scrollBottom(true));

function resizeComposer() {
  const input = $("#input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  input.style.overflowY = input.scrollHeight > 180 ? "auto" : "hidden";
}

function connectionStatus(message, kind = "ready") {
  const status = $("#connection-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.state = kind;
}

function renderMemory(m) {
  if (!m || !Number.isFinite(m.active)) return;
  memory = m;
  $("#memory").textContent =
    `活跃 ${m.active} · 仓库 ${m.warehouse} · 长期 ${m.longterm}`
    + (m.pressure ? ` · 高压隔离 ${m.pressure}` : "")
    + ` · 已忘 ${m.forgotten} · 技能 ${m.skills}`;
}

/* 稳定行 ID 与请求 ID 只用于界面合并；不将 QQ 身份拼进对话正文。 */
function requestKey(requestId, role) {
  return requestId ? `${requestId}:${role}` : null;
}

function noteRequestNode(node, requestId, role) {
  const key = requestKey(requestId, role);
  if (key) {
    node.dataset.requestId = requestId;
    requestNodes.set(key, node);
  }
}

const DELIVERY_LABELS = {
  ready: "QQ 已有分条待处理（桥暂停或终止时可能延后更新）",
  pending: "QQ 投递状态尚未登记；请查看桥状态",
  sending: "QQ 分条提交中，尚未确认结果",
  platform_confirmed: "QQ 发送片段已获平台确认；可能为长回复摘录，不代表已读",
  confirmed: "QQ 发送片段已获平台确认；可能为长回复摘录，不代表已读",
  failed: "QQ 有片段未发出或发送失败，可能已部分发送",
  send_failed: "QQ 有片段未发出或发送失败，可能已部分发送",
  unknown: "QQ 至少一条结果未知，可能已部分发送；请先到 QQ 核对",
  send_unknown: "QQ 至少一条结果未知，可能已部分发送；请先到 QQ 核对",
  expired: "QQ 回复窗口已过，可能已部分发送",
  partial: "QQ 已有部分片段获平台确认，其余结果请先核对",
  partial_confirmed: "QQ 已有部分片段获平台确认，其余结果请先核对",
};
// failed/unknown 可在人工核对后更新，不能永久停止刷新它们。
const DELIVERY_FINAL = new Set(["platform_confirmed", "confirmed", "expired"]);

const SOURCE_HISTORY_PLACEHOLDER = "（这一轮参考了外部资料；资料正文仅在仍获准的来源附件中提供。）";
const WORKER_HISTORY_PLACEHOLDER = "（这一轮使用了协作任务资料；正文仅在 GUI 内、任务资料仍获准时可见。）";
const WORKER_REPORT_STATES = { succeeded: "执行已结束", failed: "执行失败", cancelled: "已停止", interrupted: "执行已中断", unknown: "执行状态待确认" };

function workerReportFor(node, turn) {
  const channel = turn.channel || node.dataset.channel || "gui";
  const assistant = turn.role ? turn.role === "assistant" : node.classList.contains("assistant");
  const report = turn.worker_report;
  if (!assistant || channel !== "gui" || !report || typeof report.task_id !== "string" || !report.task_id
      || typeof report.attempt_id !== "string" || !report.attempt_id) return null;
  return { collaborator: workerCollaborator(report.collaborator), taskId: report.task_id,
    state: typeof report.state === "string" ? report.state : "unknown",
    kind: ["alice_summary", "status"].includes(report.kind) ? report.kind : "legacy_transcript" };
}

function historyDisplayContent(node, turn, fallback = "") {
  const report = workerReportFor(node, turn);
  // Older services can still return the full host transcript. Never place it in
  // the main conversation, including the raw-text cache or source refreshes.
  if (report?.kind === "legacy_transcript") return `${workerDisplayName(report.collaborator)} ${WORKER_REPORT_STATES[report.state] || "任务状态已更新"}。`;
  if (report && turn.source_bound === true && (turn.source_available !== true
      || !Number.isFinite(Number(turn.source_expires_at)) || Number(turn.source_expires_at) * 1000 <= Date.now())) {
    return WORKER_HISTORY_PLACEHOLDER;
  }
  if (report?.kind === "status") return turn.content || `${workerDisplayName(report.collaborator)} ${WORKER_REPORT_STATES[report.state] || "任务状态已更新"}。`;
  return turn.content || fallback;
}

function renderTurnMetadata(node) {
  const meta = node.querySelector(".message-meta");
  if (!meta) return;
  meta.removeAttribute("title");
  const channel = node.dataset.channel || "gui", fromQQ = channel.startsWith("qq");
  const assistant = node.classList.contains("assistant");
  const unavailable = node.dataset.sourceBound === "true" && node.dataset.sourceAvailable !== "true";
  if (channel === "gui" && node.classList.contains("worker-report")) {
    const summary = node.dataset.workerReportKind === "alice_summary";
    const taskId = node.dataset.workerTaskId;
    const collaborator = workerCollaborator(node.dataset.workerReportCollaborator);
    const workerName = workerDisplayName(collaborator);
    let link = meta.querySelector(".worker-report-link");
    // Keep a focused entry intact when a source-only poll repeats the same report.
    if (!link || link.dataset.taskId !== taskId || link.dataset.collaborator !== (collaborator || "")
        || meta.dataset.workerReportKind !== node.dataset.workerReportKind) {
      meta.textContent = summary ? `${workerName} 任务总结 · ` : "";
      link = document.createElement("button");
      link.type = "button"; link.className = "link worker-report-link"; link.textContent = "查看协作会话";
      link.dataset.taskId = taskId; meta.dataset.workerReportKind = node.dataset.workerReportKind;
      link.dataset.collaborator = collaborator || "";
      link.disabled = !collaborator;
      link.onclick = async () => {
        if (!node.isConnected || node.dataset.channel !== "gui" || node.dataset.workerTaskId !== taskId
            || node.dataset.workerReportCollaborator !== collaborator) return;
        await openReferencedWorkerTask(taskId, collaborator);
      };
      meta.appendChild(link);
    }
    meta.title = summary ? `Alice 基于 ${workerName} 公开自报生成的总结，未核验产物；${workerName} 原文保留在协作会话。`
      : node.dataset.workerReportKind === "legacy_transcript" ? `历史宿主状态通知；${workerName} 原文仅在协作会话展示，这不是 Alice 总结。`
      : "宿主任务状态通知；这不是 Alice 总结。";
    if (unavailable) meta.title += node.dataset.sourceAvailable === "unknown" ? " 正在核对任务资料资格。" : " 任务正文当前不可用，仍可查看任务状态。";
    return;
  }
  delete meta.dataset.workerReportKind;
  meta.textContent = fromQQ ? (assistant ? "回复 QQ" : "QQ · 所有者")
    : channel === "local" ? "本机历史（来源未记录）" : "本机网页";
  if (!fromQQ) {
    if (node.dataset.workerBound === "true") meta.textContent += unavailable ? " · 协作任务正文当前不可用" : " · 含协作任务资料";
    return;
  }
  if (assistant) {
    meta.textContent += ` · ${DELIVERY_LABELS[node.dataset.delivery] || "QQ 发送状态待确认"}`;
    meta.title = "平台接收不代表对方已读；发送失败不会重新生成答案。";
    if (!unavailable && ["failed", "send_failed", "expired", "partial", "partial_confirmed"].includes(node.dataset.delivery)) {
      meta.textContent += "；完整回复保留在此";
    }
  }
  if (unavailable) {
    const reason = node.dataset.sourceAvailable === "unknown" ? "正在核对来源资格" : "来源期限或渠道范围";
    meta.textContent += ` · ${assistant ? "原回复" : "原消息"}当前不可在此展示（${reason}）；${assistant ? "投递" : "消息"}记录保留`;
  }
  if (assistant && channel === "gui" && !node.classList.contains("worker-report")) noteInspectorLink(node);
}

// 上下文观测插件入口（20261107 插件设计 §7）：从回复携带 request_id 进入；缺 ID 只能进列表。
let inspectorEnabled = null;
async function inspectorProbe() {
  if (inspectorEnabled !== null) return inspectorEnabled;
  try {
    const r = await fetch("/api/plugins/context-inspector/status",
      { headers: { "X-Alice-Intent": "observer" }, cache: "no-store" });
    const d = await r.json().catch(() => ({}));
    inspectorEnabled = d.status === "ok" && d.plugin && d.plugin.enabled === true;
  } catch { inspectorEnabled = false; }
  return inspectorEnabled;
}

function noteInspectorLink(node) {
  const requestId = node.dataset.requestId;
  if (!requestId) return;  // 缺 request_id 不伪造入口，只能从插件页列表进入
  const meta = node.querySelector(".message-meta");
  if (!meta || meta.querySelector(".ci-jump")) return;
  inspectorProbe().then((ok) => {
    if (!ok || !node.isConnected) return;
    const link = document.createElement("a");
    link.className = "link ci-jump";
    link.href = `/addons/context-inspector/?request_id=${encodeURIComponent(requestId)}`;
    link.target = "_blank";
    link.rel = "opener";
    link.textContent = "查看本轮上下文";
    meta.append(document.createTextNode(" · "), link);
  });
}

function redactSourceNode(node, pending = false) {
  const txt = node.querySelector(".txt");
  if (txt) setBubbleText(txt, node.dataset.workerBound === "true" ? WORKER_HISTORY_PLACEHOLDER : SOURCE_HISTORY_PLACEHOLDER);
  // Streamed reasoning can contain the same source material as the final text.
  node.querySelector("details.think")?.remove();
  node.dataset.sourceAvailable = pending ? "unknown" : "false";
  renderTurnMetadata(node);
}

function expireSourceDisplays() {
  const now = Date.now();
  for (const node of turnNodes.values()) {
    if (node.dataset.sourceBound !== "true" || node.dataset.sourceAvailable === "false") continue;
    const expires = Number(node.dataset.sourceExpiresAt) * 1000;
    if (!Number.isFinite(expires) || expires <= now) redactSourceNode(node);
  }
  scheduleSourceExpiry();
}

function scheduleSourceExpiry() {
  clearTimeout(sourceExpiryTimer);
  let next = Infinity;
  for (const node of turnNodes.values()) {
    if (!node.isConnected || node.dataset.sourceBound !== "true" || node.dataset.sourceAvailable === "false") continue;
    const expires = Number(node.dataset.sourceExpiresAt) * 1000;
    if (Number.isFinite(expires)) next = Math.min(next, expires);
  }
  if (Number.isFinite(next)) sourceExpiryTimer = setTimeout(expireSourceDisplays, Math.max(1, Math.min(next - Date.now(), 2147483647)));
}

function markSourceProjection(node, turn) {
  if (typeof turn.worker_bound === "boolean") node.dataset.workerBound = String(turn.worker_bound);
  const report = workerReportFor(node, turn);
  node.classList.toggle("worker-report", !!report);
  node.classList.toggle("worker-summary", report?.kind === "alice_summary");
  node.classList.toggle("worker-report-notice", !!report && report.kind !== "alice_summary");
  if (report) {
    node.dataset.workerTaskId = report.taskId;
    node.dataset.workerReportCollaborator = report.collaborator || "";
    node.dataset.workerReportState = report.state;
    node.dataset.workerReportKind = report.kind;
    node.querySelector("details.think")?.remove();
  } else {
    delete node.dataset.workerTaskId; delete node.dataset.workerReportCollaborator;
    delete node.dataset.workerReportState; delete node.dataset.workerReportKind;
  }
  if (typeof turn.source_bound !== "boolean") return;
  node.dataset.sourceBound = String(turn.source_bound);
  if (!turn.source_bound) {
    delete node.dataset.sourceExpiresAt; delete node.dataset.sourceAvailable;
    renderTurnMetadata(node);
    return;
  }
  node.dataset.sourceExpiresAt = String(turn.source_expires_at || 0);
  node.dataset.sourceAvailable = String(turn.source_available === true);
  if (!turn.source_available || Number(node.dataset.sourceExpiresAt) * 1000 <= Date.now()) redactSourceNode(node);
  else renderTurnMetadata(node);
}

function applySourceUpdates(updates) {
  for (const update of Array.isArray(updates) ? updates : []) {
    const node = turnNodes.get(update.id);
    if (!node?.isConnected || typeof update.source_bound !== "boolean") continue;
    markUserWorkerTarget(node, update);
    const txt = node.querySelector(".txt");
    if (txt && typeof update.content === "string") setBubbleText(txt, historyDisplayContent(node, update));
    markSourceProjection(node, update);
  }
  scheduleSourceExpiry();
}

function sourceProjectionIds() {
  // A disabled legacy grant may be resumed without revocation. Recheck task
  // projections even while hidden; only the server can restore their text.
  return [...turnNodes].filter(([, node]) => node.isConnected && (userWorkerTargets.has(node)
    || node.dataset.workerBound === "true"
    || node.dataset.sourceBound !== "false" && node.dataset.sourceAvailable !== "false")).map(([id]) => id);
}

function invalidateSourceDisplays() {
  sourceProjectionVersion++;
  if (composerWorkerReference) composerWorkerReference.title = "";
  if (uncertainSend?.workerReference) uncertainSend.workerReference.title = "";
  renderComposerWorkerReference();
  for (const node of turnNodes.values()) {
    if (node.dataset.sourceBound === "true" && node.dataset.sourceAvailable !== "false") redactSourceNode(node, true);
    const target = userWorkerTargets.get(node), button = node.querySelector(".message-worker-target");
    if (target && button) {
      target.available = false; target.title = ""; button.disabled = true;
      button.textContent = `${workerDisplayName(target.collaborator)} · 会话 ${target.taskId.slice(-8)} · 状态待同步`;
      button.title = `任务引用状态待同步 · ${target.taskId}`;
    }
  }
  expireSourceDisplays();
  syncHistory();
}

window.addEventListener("alice:sources-changed", invalidateSourceDisplays);
window.addEventListener("alice:discussion-saved", () => { syncHistory(); window.AliceObserver?.refreshAfterTurn(); });

function markTurn(node, turn) {
  if (Number.isSafeInteger(turn.id) && turn.id > 0) {
    const duplicate = turnNodes.get(turn.id);
    if (duplicate && duplicate !== node) duplicate.remove();
    turnNodes.set(turn.id, node);
    node.dataset.turnId = String(turn.id);
  }
  noteRequestNode(node, turn.request_id, turn.role);
  const channel = turn.channel || node.dataset.channel || "gui";
  node.dataset.channel = channel;
  const fromQQ = channel.startsWith("qq");
  let meta = node.querySelector(".message-meta");
  if (!meta) {
    meta = document.createElement("div");
    meta.className = "message-meta";
    node.appendChild(meta);
  }
  const delivery = typeof turn.delivery === "string" ? turn.delivery : turn.delivery_status;
  if (fromQQ && turn.role === "assistant") {
    node.dataset.delivery = delivery || "pending";
  }
  node.classList.toggle("from-qq", fromQQ);
  markUserWorkerTarget(node, turn);
  markSourceProjection(node, turn);
  renderTurnMetadata(node);
  window.AliceChatSearch?.decorateTurn(node, turn);
  scheduleSourceExpiry();
}

function mergeTurn(turn) {
  if (!["user", "assistant"].includes(turn.role) || !Number.isSafeInteger(turn.id)) return;
  const key = requestKey(turn.request_id, turn.role);
  let node = turnNodes.get(turn.id) || (key && requestNodes.get(key));
  if (!node || !node.isConnected) node = bubble(turn.role, "");
  const txt = node.querySelector(".txt");
  if (turn.role === "assistant") setBubbleText(txt, historyDisplayContent(node, turn, "（沉默）"));
  else txt.textContent = turn.content || "";
  markTurn(node, turn);
  const later = [...$("#msgs").querySelectorAll(".msg[data-turn-id]")]
    .find((other) => Number(other.dataset.turnId) > turn.id);
  if (later && later !== node) $("#msgs").insertBefore(node, later);
  if (uncertainSend?.requestId === turn.request_id && turn.role === "assistant") {
    confirmComposerRequest(turn.request_id);
    setChatNotice("");
  }
  scrollBottom();
}

function setChatNotice(message) {
  $("#chat-notice").textContent = message;
  $("#chat-notice").classList.toggle("hidden", !message);
  if (!message) $("#chat-new-attempt").classList.add("hidden");
}

function renderActivity() {
  const el = $("#chat-activity");
  if (!activity) return;
  window.AliceChatSearch?.observeActivity(activity);
  connectionStatus(activity.status === "draining" ? "正在关闭"
    : activity.status === "busy" ? "正在处理" : "本机已连接",
  activity.status === "draining" ? "waiting" : activity.status === "busy" ? "busy" : "ready");
  const fromQQ = String(activity.channel || activity.active_channel || "").startsWith("qq");
  el.textContent = activity.status === "draining" ? "服务正在关闭，暂停接收新消息"
    : activity.status === "busy" ? (fromQQ ? "QQ 消息处理中；你可以先写好下一句话" : "Alice 正在处理消息；输入会保留")
      : activity.channels_enabled ? "QQ 与本机共用同一段对话" : "";
  el.classList.toggle("hidden", !el.textContent);
  $("#send").disabled = busy || shuttingDown || activity.status === "busy" || activity.status === "draining";
}

async function refreshActivity() {
  if (shuttingDown) return;
  activity = await api("/api/chat/activity");
  renderActivity();
  return activity;
}

function resetHistory(epoch) {
  const hadReference = !!composerWorkerReference || !!uncertainSend?.workerReference;
  window.AliceChatSearch?.reset();
  historyVersion += 1;
  sourceProjectionVersion++;
  sourceRefreshIndex = 0;
  clearTimeout(sourceExpiryTimer);
  sessionEpoch = epoch || null;
  historyCursor = 0;
  turnNodes.clear();
  requestNodes.clear();
  uncertainSend = null;
  composerWorkerReference = null;
  renderComposerWorkerReference();
  if (hadReference) toast("会话已切换，任务引用已清除；输入文字仍保留。", 4000);
  historyLoaded = false;
  followingLatest = true;
  // Preserve the interface's empty-state element if it lives inside the transcript.
  $("#msgs").querySelectorAll(".msg, .sys").forEach((node) => node.remove());
  updateChatEmpty();
  updateLatestButton();
}

async function syncHistory({ reset = false } = {}) {
  expireSourceDisplays();
  if (!state || syncingHistory || busy || shuttingDown || document.hidden) return;
  syncingHistory = true;
  const version = historyVersion;
  const sourceVersion = sourceProjectionVersion;
  const sourceIds = sourceProjectionIds();
  const rotation = sourceIds.length ? sourceRefreshIndex % sourceIds.length : 0;
  const sourceQueue = sourceIds.slice(rotation).concat(sourceIds.slice(0, rotation));
  let sourceOffset = 0, historyRequests = 0;
  const addSources = (params) => {
    const batch = sourceQueue.slice(sourceOffset, sourceOffset + 100);
    if (batch.length) params.set("source_turn_ids", batch.join(","));
    return batch.length;
  };
  const acceptSources = (h, count) => {
    applySourceUpdates(h.source_updates);
    sourceOffset += count; sourceRefreshIndex += count;
  };
  const box = $("#msgs");
  const stickToBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  try {
    const info = await refreshActivity();
    if (busy || version !== historyVersion || sourceVersion !== sourceProjectionVersion || shuttingDown) return;
    if (sessionEpoch && info?.session_epoch && info.session_epoch !== sessionEpoch) {
      resetHistory(info.session_epoch);
      setChatNotice("会话已经更换，旧消息已从当前页面移除。输入框中的草稿仍保留，请确认后再发送。");
      return;
    }
    if (info?.status === "busy" || info?.status === "draining") return;
    let after = reset || !sessionEpoch ? null : historyCursor;
    for (let page = 0; page < 3; page++) {
      const params = new URLSearchParams({ limit: "100" });
      if (after !== null) params.set("after_id", String(after));
      if (sessionEpoch) params.set("session_epoch", sessionEpoch);
      const sourceCount = addSources(params);
      const h = await api(`/api/history?${params}`);
      historyRequests++;
      if (busy || version !== historyVersion || sourceVersion !== sourceProjectionVersion || shuttingDown) return;
      if (sessionEpoch && h.session_epoch && h.session_epoch !== sessionEpoch) {
        resetHistory(h.session_epoch);
        setChatNotice("会话已经更换；输入框中的草稿仍保留，请确认后再发送。");
        return;
      }
      sessionEpoch = h.session_epoch || sessionEpoch;
      historyLoaded = true;
      (h.turns || []).forEach(mergeTurn);
      acceptSources(h, sourceCount);
      updateChatEmpty();
      if (h.memory) renderMemory(h.memory);
      const next = h.next_after_id;
      if (Number.isSafeInteger(next)) historyCursor = Math.max(historyCursor, next);
      else for (const turn of h.turns || []) {
        if (Number.isSafeInteger(turn.id)) historyCursor = Math.max(historyCursor, turn.id);
      }
      if (!h.has_more || !Number.isSafeInteger(next) || next === after) break;
      after = next;
    }
    // Reproject already displayed source turns independently of the append cursor.
    // Keep each refresh bounded; rotate through long open transcripts over time.
    while (sourceOffset < sourceQueue.length && historyRequests < 3) {
      const params = new URLSearchParams({ after_id: String(historyCursor), limit: "1", session_epoch: sessionEpoch });
      const count = addSources(params);
      const h = await api(`/api/history?${params}`);
      historyRequests++;
      if (busy || version !== historyVersion || sourceVersion !== sourceProjectionVersion || shuttingDown) return;
      if (h.session_epoch !== sessionEpoch) { resetHistory(h.session_epoch); return; }
      acceptSources(h, count);
    }
    // 发送回执可能晚于助手正文：按原行 ID 更新，避免把状态变化误作新消息。
    const pending = [...turnNodes.values()].filter((n) => n.classList.contains("assistant")
      && n.dataset.channel?.startsWith("qq") && !DELIVERY_FINAL.has(n.dataset.delivery));
    if (pending.length) {
      const node = pending[deliveryRefreshIndex++ % pending.length];
      const id = Number(node.dataset.turnId);
      const h = await api(`/api/history?${new URLSearchParams({ after_id: String(id - 1), limit: "1", session_epoch: sessionEpoch })}`);
      if (!busy && version === historyVersion && sourceVersion === sourceProjectionVersion && h.session_epoch === sessionEpoch) {
        const turn = h.turns?.find((t) => t.id === id);
        if (turn) mergeTurn(turn);
      }
    }
    if (stickToBottom && followingLatest) scrollBottom();
    // Appending while reading does not scroll. In particular, do not restore a
    // position captured before the requests: the reader may have moved meanwhile.
    updateLatestButton();
  } catch (e) {
    if (e.code === "session_changed") {
      resetHistory(null);
      setChatNotice("会话已经更换，正在重新读取；草稿保留，请确认后再发送。");
    } else if (e.code !== "status_unavailable" && e.code !== "busy") {
      connectionStatus("连接待确认", "error");
      $("#chat-activity").textContent = "暂时无法同步对话；页面保留上次记录";
      $("#chat-activity").classList.remove("hidden");
    } else {
      connectionStatus("状态暂不可用", "waiting");
    }
  } finally {
    syncingHistory = false;
  }
}

function startHistorySync() {
  clearInterval(syncTimer);
  syncTimer = setInterval(() => {
    if (!document.hidden && !$("#app").classList.contains("hidden")) syncHistory();
  }, 3000);
}

document.addEventListener("visibilitychange", () => {
  expireSourceDisplays();
  if (!document.hidden && !$("#app").classList.contains("hidden")) syncHistory();
});
window.addEventListener("pagehide", () => { clearInterval(syncTimer); clearTimeout(sourceExpiryTimer); });
$("#chat-refresh").onclick = () => state ? syncHistory() : boot().catch(showStartupError);
$("#chat-new-attempt").onclick = () => {
  if (busy || !uncertainSend?.canStartNew) return;
  if (!window.confirm("原请求已经失败或中断，但用户消息或部分记忆操作可能已保存。作为新的一轮发送可能重复这些操作，并再次产生模型费用。\n确认允许新建一轮吗？")) return;
  const text = uncertainSend.text;
  const reference = uncertainSend.workerReference;
  uncertainSend = null;
  if (reference?.epoch === sessionEpoch) composerWorkerReference = { ...reference };
  renderComposerWorkerReference();
  if (!$("#input").value.trim()) $("#input").value = text;
  resizeComposer();
  $("#chat-new-attempt").classList.add("hidden");
  setChatNotice("已允许新建一轮。请核对输入，再点击发送；本次没有自动发送或重跑。");
  $("#input").focus();
};

async function confirmQQReset() {
  try { await refreshActivity(); } catch (_) { /* 后端仍检查重置与在途任务。 */ }
  if (activity?.channels_enabled) {
    return window.confirm("已启用 QQ 接入。重置前请先暂停 QQ 桥，并核对待发送或发送结果未知的回复。\n重置后旧 QQ 队列不能进入新会话；已经提交 QQ 的消息仍可能晚到。已完成这些操作，继续吗？");
  }
  return true;
}

/* ---------- 启动 ---------- */

async function boot() {
  connectionStatus("正在连接", "waiting");
  state = await api("/api/state");
  $("#boot-status")?.classList.add("hidden");
  connectionStatus("本机已连接");
  setChatNotice("");
  const forceSetup = new URLSearchParams(location.search).has("setup");  // ?setup 可主动回到初始化页
  if (state.turns > 0 && !forceSetup) await startChat(state);
  else showWelcome(state, forceSetup);
}

async function startChat(s) {
  state = s;
  $("#welcome").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#name").textContent = s.name;
  document.title = s.name;
  renderMemory(s.memory);
  resetHistory(null);
  await syncHistory({ reset: true });
  startHistorySync();
  scrollBottom();
  $("#input").disabled = false;
  resizeComposer();
  $("#input").focus();
}

/* ---------- 初始化（第一次打开） ---------- */

function personaMeta(p) {
  if (!p || !p.installed) return "还没有人格";
  const ver = p.version ? `v${p.version}` : "初始版";
  return `${ver} · ${p.installed_at || "刚安装"} · ${p.chars} 字`;
}

function showWelcome(s, reseting = false) {
  state = s;
  $("#app").classList.add("hidden");
  $("#welcome").classList.remove("hidden");
  $("#seed-name").textContent = s.name;
  $("#w-sub").textContent = reseting
    ? "正在重新设置人格。初始化＝从头开始，旧会话会被归档。"
    : s.persona && s.persona.installed
      ? "她已经有一个先装好的人格。也可以换一个人格再开始。"
      : "先定下她的底色，再开始。";

  /* 初始化＝新会话：旧对话和记忆归档（data/archive/）后丢弃 */
  const mem = s.memory || {};
  const lost = (mem.active || 0) + (mem.warehouse || 0) + (mem.longterm || 0);
  const warn = $("#w-warn");
  if (s.turns > 0 || lost > 0) {
    warn.textContent = `初始化会丢弃现有的 ${s.turns} 轮对话与 ${lost} 条记忆` +
      "（先归档到 data/archive/，日后还能翻出来）。想接着现在这个她聊，就点上面的「以她开始」。";
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }

  if (s.persona && s.persona.installed) {
    $("#current-persona").classList.remove("hidden");
    $("#cur-meta").textContent = personaMeta(s.persona);
    $("#cur-view").onclick = () => openPersonaView();
    $("#cur-start").onclick = () => startChat(s);
  }

  $("#tab-tags .hint").textContent = s.persona && s.persona.installed
    ? "用标签重新生成，会替换她现在的底色（旧版自动快照，可回滚）。想保留现在的人格，点上面的「以她开始」。"
    : "选 2~4 个气质标签当底色。剩下的性格，由你们的经历慢慢长。";

  /* 标签 */
  const list = $("#tag-list");
  list.textContent = "";
  const picked = new Set();
  s.tags.forEach((tag) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "tag";
    el.setAttribute("aria-pressed", "false");
    el.textContent = tag;
    el.onclick = () => {
      if (picked.has(tag)) picked.delete(tag);
      else if (picked.size >= 4) return;
      else picked.add(tag);
      el.classList.toggle("on", picked.has(tag));
      el.setAttribute("aria-pressed", String(picked.has(tag)));
      $("#seed-go").disabled = !(picked.size >= 2);
    };
    list.appendChild(el);
  });
  $("#seed-go").onclick = async () => {
    if (!await confirmQQReset()) return;
    const btn = $("#seed-go");
    btn.disabled = true;
    btn.textContent = "生成底色中…";
    try {
      const st = await postJSON("/api/seed", { tags: [...picked], reset: true });
      await startChat(st);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "生成底色并开始";
      toast(e.message, 4000);
    }
  };

  /* 导入 */
  wireImport(
    {
      drop: $("#drop"),
      pick: $("#pick-file"),
      file: $("#import-file"),
      box: $("#import-md"),
      check: $("#import-check"),
      preview: $("#import-preview"),
      go: $("#import-go"),
      idle: "安装并开始",
      busy: "安装中…",
      reset: true,
      back: () => closeModal(),
    },
    async (md, src, resumeOpId, installed) => {
      if (!await confirmQQReset()) {
        const error = new Error("已取消初始化；请先处理 QQ 桥的待发送回复。");
        error.importCancelled = true;
        throw error;
      }
      const st = await postJSON("/api/persona", { persona_md: md, source: src, reset: true,
        ...(resumeOpId ? {resume_op_id: resumeOpId} : {}) });
      installed();
      await startChat(st);
      showInstalledCoverage(st.expression_coverage);
    },
  );

  /* 标签页 */
  const welcomeTabs = [...document.querySelectorAll("#welcome .tab")];
  const chooseTab = (tab, focus = false) => {
    welcomeTabs.forEach((t) => {
      const selected = t === tab;
      t.classList.toggle("on", selected);
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
    });
    const toImport = tab.dataset.tab === "import";
    $("#tab-tags").classList.toggle("hidden", toImport);
    $("#tab-import").classList.toggle("hidden", !toImport);
    if (focus) tab.focus();
  };
  welcomeTabs.forEach((tab, index) => {
    tab.id = `welcome-tab-${tab.dataset.tab}`;
    tab.setAttribute("aria-controls", `tab-${tab.dataset.tab}`);
    const pane = $(`#tab-${tab.dataset.tab}`);
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", tab.id);
    tab.onclick = () => chooseTab(tab);
    tab.onkeydown = (event) => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % welcomeTabs.length;
      else if (event.key === "ArrowLeft") next = (index + welcomeTabs.length - 1) % welcomeTabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = welcomeTabs.length - 1;
      else return;
      event.preventDefault();
      chooseTab(welcomeTabs[next], true);
    };
  });
  chooseTab(welcomeTabs.find((tab) => tab.classList.contains("on")) || welcomeTabs[0]);
}

/* ---------- 人格文档导入（初始化页与弹层共用） ---------- */

function validateMd(md) {
  const text = md.trim();
  if (!text) return { ok: false, msg: "" };
  /* Alice 格式直接安装；外部角色文档走提取、样本适配与认知审查。 */
  const aliceShape = /^##\s*底色/m.test(text);
  const words = text.length;
  if (!aliceShape) {
    const msg = words > 12000
      ? `原始角色文档：先提取风格、情境和样本，再审查认知；原文不会直接常驻。${words} 字偏长，导入处理可能较慢。`
      : "原始角色文档：先提取风格、情境和样本，再审查认知；准备成功后才安装，原文不会直接常驻。";
    return { ok: true, note: !words || words <= 12000, warn: words > 12000, msg };
  }
  if (words > 12000) {
    return { ok: true, warn: true, msg: `含「## 底色」小节，但 ${words} 字偏长——太长会挤压每轮上下文，建议压到 2000 字内。` };
  }
  return { ok: true, msg: "含「## 底色」小节，可以安装。缺失的生长小节会自动补齐。" };
}

// Keep the pending operation across preview/modal recreation, in this page only.
// The server remains authoritative for source, policy, epoch and attempt limits.
let personaImportAttempt = null;
let personaImportPending = false;

function wireImport(els, install) {
  let md = els.box.value;
  let source = els.box.dataset.importSource || "";
  let pending = false;
  let fileReadVersion = 0;
  const binding = () => JSON.stringify([md.trim(), source.trim().slice(0, 60) || "用户导入", !!els.reset]);
  const attempt = () => personaImportAttempt?.binding === binding() ? personaImportAttempt : null;
  const paint = () => {
    const v = validateMd(md);
    const current = attempt();
    els.check.textContent = current?.message || (!md.trim() ? "" : (v.ok ? "✓ " : "✗ ") + v.msg);
    els.check.className = "check" + (current ? " warn" : (v.ok && !v.warn ? " ok" : "") + (v.warn ? " warn" : "") + (v.note ? " note" : ""));
    els.go.disabled = pending || !v.ok || current?.blocked === true;
    els.go.textContent = pending ? els.busy : current?.resumeOpId ? "继续安装" : current?.installed ? "已安装" : els.idle;
    els.preview.disabled = pending || !md.trim();
    els.box.disabled = pending;
    els.pick.disabled = pending;
    els.file.disabled = pending;
  };
  const refresh = (text) => {
    fileReadVersion += 1;
    md = text;
    els.box.value = text;
    els.box.dataset.importSource = source;
    if (personaImportAttempt && personaImportAttempt.binding !== binding()) personaImportAttempt = null;
    paint();
  };
  const readFile = async (file) => {
    const version = ++fileReadVersion;
    try {
      const text = await file.text();
      if (pending || version !== fileReadVersion) return;
      source = file.name;
      refresh(text);
    } catch (e) {
      toast(`文件读取失败：${e.message}`, 4000);
    }
  };

  els.pick.onclick = () => els.file.click();
  els.file.onchange = async (e) => {
    const f = e.target.files[0];
    if (!f || pending) return;
    await readFile(f);
  };
  els.box.oninput = () => { source = ""; refresh(els.box.value); };

  /* 拖放 */
  els.drop.ondragover = (e) => {
    e.preventDefault();
    els.drop.classList.add("over");
  };
  els.drop.ondragleave = () => els.drop.classList.remove("over");
  els.drop.ondrop = async (e) => {
    e.preventDefault();
    els.drop.classList.remove("over");
    if (pending) return;
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (!/\.(md|markdown|txt)$/i.test(f.name)) {
      toast("只认 .md / .markdown / .txt", 3000);
      return;
    }
    await readFile(f);
  };

  els.preview.onclick = () => {
    openModal("人格预览", `${md.trim().length} 字`, `<div class="md">${renderMd(md)}</div>`, [
      { label: "返回编辑", onClick: () => (els.back ? els.back(md, source) : closeModal()) },
      { label: "关闭", onClick: closeModal },
    ]);
  };

  els.go.onclick = async () => {
    if (pending || attempt()?.blocked || !validateMd(md).ok) return;
    if (personaImportPending) { toast("已有一次人格安装正在处理，请先等它返回结果。", 4000); return; }
    const submittedBinding = binding();
    const resumeOpId = attempt()?.resumeOpId;
    pending = true;
    personaImportPending = true;
    paint();
    try {
      await install(md, source || "用户导入", resumeOpId, () => {
        // Installation already succeeded even if a later view refresh fails.
        personaImportAttempt = {binding: submittedBinding, installed: true, blocked: true,
          message: "人格已安装。请查看当前人格，不要重复安装。"};
      });
    } catch (e) {
      if (attempt()?.installed) {
        toast(`人格已安装，但界面刷新未完成：${e.message}。请查看当前人格。`, 6000);
      } else if (e.importCancelled) {
        toast(e.message, 5000);
      } else {
        const status = e.importStatus;
        const canResume = status?.can_resume === true && typeof status.resume_op_id === "string"
          && /^imp_(?:[0-9a-f]{32}|[0-9]{8}_[0-9]{6}_[0-9a-f]{6})$/.test(status.resume_op_id);
        const note = canResume ? "已保留通过验收的阶段。点击「继续安装」只继续未完成阶段，已用额度不会重置。"
          : status ? "本次不能继续；请先核对导入记录和失败原因，不会自动重新生成。"
          : "安装结果暂不能确认。请先核对当前人格与导入记录，不要连续重新提交。";
        personaImportAttempt = {binding: submittedBinding, resumeOpId: canResume ? status.resume_op_id : null,
          blocked: !canResume, message: `${e.message} ${note}`};
        toast(personaImportAttempt.message, 7000);
      }
    } finally {
      pending = false;
      personaImportPending = false;
      paint();
    }
  };
  paint();
  return (text, name = "") => { source = name; refresh(text); };
}

/* ---------- 弹层 ---------- */

function openModal(title, meta, bodyHtml, buttons) {
  $("#modal-title").textContent = title;
  $("#modal-meta").textContent = meta || "";
  $("#modal-body").innerHTML = bodyHtml;
  const foot = $("#modal-foot");
  foot.textContent = "";
  buttons.forEach((b) => {
    const el = document.createElement("button");
    el.className = b.cls || "btn ghost";
    el.textContent = b.label;
    el.onclick = b.onClick;
    foot.appendChild(el);
  });
  $("#modal").classList.remove("hidden");
}

function closeModal() {
  $("#modal").classList.add("hidden");
}

function downloadMd(md, name = "persona.md") {
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

async function openPersonaView() {
  let p;
  try {
    p = await api("/api/persona");
  } catch (e) {
    toast(`读取人格失败：${e.message}`);
    return;
  }
  const meta = p.installed ? `${personaMeta(p)}${p.turns ? ` · 聊过 ${p.turns} 轮` : ""}` : "还没有人格";
  const body = p.installed
    ? (p.growth_note ? `<p class="hint">${esc(p.growth_note)}</p>` : "") +
      (p.expression?.coverage_status ? `<p class="hint">${esc(p.expression.coverage_status)}</p>` : "") +
      expressionCoverage(p.expression) +
      `<div class="md">${renderMd(p.display_md ?? p.md)}</div>` +
      (p.changelog && p.changelog.length
        ? `<div class="changelog"><div class="panel-title small">最近变更</div><ul>${p.changelog
            .map((x) => `<li>${esc(x)}</li>`)
            .join("")}</ul></div>`
        : "")
    : `<p class="hint">还没有安装人格。点下面的按钮导入一份，或以气质标签生成。</p>`;
  openModal("当前人格", meta, body, [
    ...(p.expression?.sections && p.expression.format !== "invalid" ? [{ label: "编辑说话习惯", onClick: () => openExpressionEditor(p.expression) }] : []),
    ...(p.expression ? [{ label: "从原稿更新表达", onClick: () => openExpressionUpdate(p.expression) }] : []),
    { label: "导入新的人格", cls: "btn primary", onClick: () => openPersonaImport() },
    ...(p.installed ? [{ label: "下载 .md", onClick: () => downloadMd(p.md) }] : []),
    {
      label: "重新初始化",
      cls: "btn danger",
      onClick: async () => {
        const ok = window.confirm(
          `重新初始化＝丢掉这 ${p.turns || 0} 轮对话和全部记忆，从「第一次见面」重新开始。\n` +
            "旧数据会先归档到 data/archive/，之后还能找回。确定吗？",
        );
        if (!ok) return;
        if (!await confirmQQReset()) return;
        try {
          await postJSON("/api/reset", {});
          location.href = "/?setup";
        } catch (e) {
          toast(`重置失败：${e.message}`);
        }
      },
    },
    { label: "关闭", onClick: closeModal },
  ]);
}

function openExpressionEditor(expression) {
  if (!expression?.sections || expression.format === "invalid") {
    toast("当前表达格式无法编辑，请先核对更新记录和当前人格版本。", 5000); return;
  }
  const fields = Object.entries(expression.sections);
  const structured = expression.format === "expression-v2";
  const revisionModeNote = title => ({
    explicit_section: "此节已采用整节自由文本保留范围；本次继续编辑会沿用该整节范围，未勾选不会自动撤销原选择。",
    protected_legacy: "此节沿用旧格式整节保护；无法可靠区分原稿与后改的内容不会被默认覆盖。",
    legacy_unknown: "此节的旧修订记录无法区分逐项范围；从原稿更新前需先明确处理，不能推定你已删除新规则。",
  }[expression.revision_modes?.[title]] || "");
  const inputValue = (index) => {
    const raw = $(`#expression-${index}`).value;
    return structured && $(`#expression-free-${index}`)?.checked && !raw.startsWith("【整节自由文本】")
      ? `【整节自由文本】\n${raw}` : raw;
  };
  let saving = false;
  const body = `<p class="hint">这是你明确选定的表达偏好。直接编辑三节正文，来源和关联由系统记录；保存前会核对容量与结构。</p>` +
    (structured ? `<p class="hint">按现有条目字段编辑，可保留未改条目。若要用自由文字替换整节，请勾选该节的“整节自由文本”：整节原映射与相关共享资格将失效；下次原稿更新会明确处理这个整节选择。</p>` : "") +
    `<p id="expression-change-scope" class="hint" role="status">尚未修改。</p>` +
    (expression.pending_associations?.length ? `<p class="hint">待处理关联：${esc(expression.pending_associations.join("、"))}</p>` : "") +
    fields.map(([title, text], index) =>
      `<label class="expression-field">${esc(title)}<textarea id="expression-${index}" rows="7" spellcheck="false">${esc(text)}</textarea></label>` +
      (revisionModeNote(title) ? `<p class="hint">${esc(revisionModeNote(title))}</p>` : "") +
      (structured ? `<label class="hint expression-free-choice"><input type="checkbox" id="expression-free-${index}"> 整节自由文本（替换本节，重新核对关联与共享范围）</label>` : "")
    ).join("");
  openModal("编辑说话习惯", "保存后只修改这三节；对话、记忆与生长内容保留，旧版自动快照。", body, [
    { label: "取消", onClick: () => { if (!saving) openPersonaView(); } },
    { label: "保存表达偏好", cls: "btn primary", onClick: async () => {
      if (saving) return;
      const sections = Object.fromEntries(fields.map(([title], index) =>
        [title, inputValue(index)]));
      saving = true;
      try {
        const result = await postJSON("/api/persona/expression", {
          revision: expression.revision, session_epoch: expression.session_epoch, sections });
        state = result.state;
        if (result.changed && (result.pending_associations?.length || result.changed_sections?.length)) {
          openModal("表达偏好已保存", "修改已记为用户配置。后续从原稿更新表达时保留这些选择。",
            `<p>变更范围：${esc((result.changed_sections || []).join("、") || "表达三节")}</p>` +
            `<p>待处理关联：${esc((result.pending_associations || []).join("、") || "无")}</p>`,
            [{label: "查看当前人格", onClick: openPersonaView}]);
        } else {
          toast(result.changed ? "表达偏好已保存，旧版已快照" : "表达偏好没有变化");
          await openPersonaView();
        }
      } catch (error) {
        toast(`保存未能确认：${error.message}。请保留文本，核对当前人格后再操作。`);
      } finally {
        saving = false;
      }
    } },
  ]);
  fields.forEach((_, index) => {
    const updateScope = () => {
      const changed = fields.filter(([, original], i) => inputValue(i) !== original).map(([title]) => title);
      $("#expression-change-scope").textContent = changed.length
        ? `本次变更：${changed.join("、")}。修改部分记为用户修订，旧来源映射及依赖关联将更新或标为待处理。` : "尚未修改。";
    };
    $(`#expression-${index}`).addEventListener("input", updateScope);
    $(`#expression-free-${index}`)?.addEventListener("change", updateScope);
  });
}

/* Prepared expression candidates are private local records. No automatic activation or retry. */
const expressionUpdateAPI = (data) => postJSON("/api/persona/expression-update", data);
const expressionStateLabel = (status) => ({candidate_inactive: "候选未激活", uncovered: "表达覆盖不满足",
  prepare_failed: "准备失败", applied: "已应用", rolled_back: "已回退", cancelled: "已取消",
  recovery_conflict: "恢复冲突", preparing: "正在准备", unreadable: "记录暂时无法读取",
  state_unconfirmed: "操作状态尚未完整核对"}[status] || status || "状态待核对");
const expressionViewVisible = (view) => view.isConnected && !$("#modal").classList.contains("hidden");
const expressionText = (value) => typeof value === "string" ? value : JSON.stringify(value ?? "", null, 2);
const expressionDisposition = (status) => ({retained: "原意保留", adapted: "局部适配后保留", merged: "合并保留",
  background: "背景资料归档", excluded: "排除", unresolved: "未决", user_revised: "用户修订"}[status] || status || "待核对");
const expressionLegacyChoice = (choice) => ({preserve: "保留当前本节", replace: "采用原稿本节新表达",
  merge: "保留两者：原稿新表达＋当前本节"}[choice] || "范围待核对");

function expressionCoverage(value) {
  const report = value?.coverage_report;
  const warnings = Array.isArray(value?.warnings) ? value.warnings.filter(item => typeof item === "string") : [];
  const notices = Array.isArray(value?.review_notices) ? value.review_notices.filter(item => typeof item === "string") : [];
  if (!report && !warnings.length && value?.coverage_policy !== "source_coverage_v1") return "";
  const metric = (key, label) => {
    const row = report?.[key];
    const validCount = number => Number.isInteger(number) && number >= 0;
    const count = validCount(row?.covered) && validCount(row?.total) ? `${row.covered}/${row.total}` : "数量待确认";
    const percent = typeof row?.percent === "number" && Number.isFinite(row.percent) && row.percent >= 0 && row.percent <= 100
      && validCount(row?.total) && row.total > 0 ? `${row.percent}%` : "未提供/待确认";
    return `<li>${esc(label)}：${esc(percent)}（${esc(count)}）</li>`;
  };
  return `<section class="expression-review"><h3>原稿覆盖</h3>` +
    `<p class="hint">按原稿源单元与场景统计；规则和样本分别记录，不等于逐条行为要求全部保留，也不代表真人效果已验收。</p>` +
    (report?.reviewed === true ? "" : `<p class="hint">此覆盖记录尚未完成独立核对，不能作为通过结论。</p>`) +
    `<ul>${metric("sources", "源单元")}${metric("rules", "规则场景")}${metric("examples", "样本场景")}</ul>` +
    (warnings.length ? `<h4>覆盖提示</h4><p class="hint">普通遗漏可带提示安装；事实、失真和硬约束未满足仍阻止安装。</p><ul>` +
      warnings.map(warning => `<li>${esc(warning)}</li>`).join("") + "</ul>" : "") +
    (notices.length ? `<h4>表达审阅提示</h4><p class="hint">以下措辞或语气意见不作为安装否决，也不改变覆盖统计，可结合成品人工判断。</p><ul>` +
      notices.map(notice => `<li>${esc(notice)}</li>`).join("") + "</ul>" : "") + `</section>`;
}

function showInstalledCoverage(value) {
  const warnings = Array.isArray(value?.warnings) ? value.warnings.filter(item => typeof item === "string") : [];
  const notices = Array.isArray(value?.review_notices) ? value.review_notices.filter(item => typeof item === "string") : [];
  if (!warnings.length && !notices.length) return;
  openModal("人格已安装", "已保留可用表达，覆盖缺口和表达审阅提示可在当前人格页继续查看。",
    expressionCoverage(value), [{label: "查看当前人格", onClick: openPersonaView}, {label: "开始聊天", cls: "btn primary", onClick: closeModal}]);
}

function expressionReviews(reviews, coveragePolicy) {
  const label = value => ({passed: "已完成核对", failed: "核对不满足", rejected: "核对不满足",
    partial: coveragePolicy === "source_coverage_v1" ? "存在普通覆盖缺口" : "结果见核对详情",
    not_reviewed: "尚未核对", user_config: "用户明确配置", invalidated_by_user: "用户修订后原核对已失效"}[value] || "结果见核对详情");
  const rowsLabel = (rows, valid) => rows.length ? (rows.every(row => row.verdict === valid) ? "已完成核对" : "核对不满足") : "尚无完整回执";
  const fact = Array.isArray(reviews?.fact) ? rowsLabel(reviews.fact, "valid") : label(reviews?.fact);
  const fidelityRows = ["fidelity_sources", "fidelity_items", "fidelity_scenarios"].flatMap(key => Array.isArray(reviews?.[key]) ? reviews[key] : []);
  const ordinaryMissing = coveragePolicy === "source_coverage_v1" && fidelityRows.some(row => row.verdict === "missing")
    && fidelityRows.every(row => ["passed", "missing"].includes(row.verdict));
  const fidelity = ordinaryMissing ? "存在普通覆盖缺口，详见提示" : fidelityRows.length ? rowsLabel(fidelityRows, "passed") : label(reviews?.fidelity);
  return `<p>成品事实：${esc(fact)}；表达保真与覆盖：${esc(fidelity)}。</p>` +
    `<details><summary>核对详情</summary><pre>${esc(expressionText(reviews))}</pre></details>`;
}

function expressionDetails(result) {
  const gaps = Array.isArray(result.gaps) ? result.gaps : [];
  const diff = Array.isArray(result.diff) ? result.diff : [];
  const currentDiff = Array.isArray(result.current_diff) ? result.current_diff : [];
  return `<p class="expression-status" role="status">${esc(expressionStateLabel(result.status))}</p>` +
    `<p class="hint">${esc(result.baseline_policy || result.user_revision_note || "此记录未保存当前表达的保留策略，请核对版本与差异。")}</p>` +
    (result.legacy_choices && Object.keys(result.legacy_choices).length ?
      `<details class="expression-review" open><summary>本次准备已确定的旧格式处理范围</summary><ul>` +
      Object.entries(result.legacy_choices).map(([title, choice]) => `<li>${esc(title)}：${esc(expressionLegacyChoice(choice))}</li>`).join("") +
      `</ul><p class="hint">这些选择与本条候选的准备版本绑定。调整范围需要另行准备并重新核对，不会改动本条候选。</p></details>` : "") +
    (result.rollback_token ? `<p class="hint">此次表达更新已通过独立回退操作恢复。原更新记录和回退记录分别保留，可在下方核对关联。</p>` : "") +
    `<p class="hint">表达长度：${result.projection_chars == null ? "未知" : esc(result.projection_chars)} 字符。审议结果仅供核对，不代表真人效果通过。</p>` +
    expressionCoverage(result) +
    (gaps.length ? `<h3>缺口与阻止应用的原因</h3><ul>${gaps.map(g => `<li>${esc(expressionText(g))}</li>`).join("")}</ul>` : "") +
    `<details class="expression-review" open><summary>准备时的表达 → 候选表达（${currentDiff.length} 节）</summary>` +
    (currentDiff.length ? currentDiff.map(d => `<details class="expression-item"><summary>${esc(d.section || "表达小节")} · ${d.before === d.after ? "正文未变" : "含变更"}</summary>` +
      `<p class="hint">${esc(expressionText(d.policy || result.baseline_policy || "保留范围待核对"))}</p>` +
      `<h4>准备时的表达</h4><pre>${esc(expressionText(d.before))}</pre><h4>候选表达</h4><pre>${esc(expressionText(d.after))}</pre></details>`).join("") :
      `<p class="hint">此记录没有可用的当前→候选对照；不能据此认定既有修订已保留。未生成候选时请先查看缺口；历史记录请另核对当时版本。</p>`) + `</details>` +
    `<details class="expression-review"><summary>事实核对与表达保真核对</summary>${expressionReviews(result.reviews, result.coverage_policy)}</details>` +
    `<details class="expression-review"><summary>原文 → 适配 → 排除或缺口（${diff.length} 项）</summary>` +
    (diff.length ? diff.map(d => `<details class="expression-item"><summary>${esc(d.source_id || "源单元")} · ${esc(expressionDisposition(d.disposition))}</summary>` +
      `<h4>原文</h4><pre>${esc(expressionText(d.original))}</pre><h4>适配后</h4><pre>${esc(expressionText(d.adapted))}</pre>` +
      `<p>${esc(expressionText(d.reason))}</p></details>`).join("") : `<p class="hint">本记录没有可展示的逐项差异。</p>`) + `</details>` +
    `<details class="expression-review"><summary>版本信息</summary><p>原稿版本：${esc(result.source_hash || "未知")}</p>` +
    `<p>人格版本：${esc(result.revision || "未知")}</p><p>本条记录：${esc(result.token || "未知")}</p>` +
    (result.rollback_token ? `<p>回退操作：${esc(result.rollback_token)}（回退本条更新）</p>` : "") +
    (result.rollback_of ? `<p>原更新操作：${esc(result.rollback_of)}（本条是它的回退操作）</p>` : "") + `</details>`;
}

async function openExpressionUpdate(expression, prefill = {}) {
  openModal("从原稿更新表达", "先准备候选；通过门槛后可应用。只更新说话习惯，保留会话、记忆、认知与关系。",
    `<div id="expression-update-view"><p class="hint">正在读取可用原稿与更新记录…</p></div>`, [{label: "返回人格", onClick: openPersonaView}]);
  const view = $("#expression-update-view");
  let listing;
  try { listing = await expressionUpdateAPI({action: "list"}); }
  catch (e) { if (expressionViewVisible(view)) view.textContent = `读取更新记录失败：${e.message}`; return; }
  if (!expressionViewVisible(view)) return;
  const sources = Array.isArray(listing.sources) ? listing.sources : [];
  const listedPrefill = sources.find(s => s.op_id === prefill.source_op_id);
  if (listedPrefill && listedPrefill.source_hash !== prefill.source_hash) {
    prefill = {...prefill, legacy_choices: undefined,
      notice: "所选原稿与旧候选记录的版本不同，未沿用旧范围选择。请根据当前原稿重新确认；旧格式默认保护全部三节。"};
  }
  if (prefill.source_op_id && !sources.some(s => s.op_id === prefill.source_op_id)) {
    sources.unshift({op_id: prefill.source_op_id, source_name: "上一条候选所用原稿", source_hash: prefill.source_hash});
  }
  const operations = Array.isArray(listing.operations) ? listing.operations : [];
  const legacyFields = expression.format === "v1" ? Object.entries(expression.sections || {}) : [];
  view.innerHTML = `<p class="hint">选择原始文档，或粘贴完整原文。准备会调用已配置模型，原稿与适配记录保存在本机。原稿中的角色指令只作为资料。</p>` +
    (prefill.notice ? `<p class="hint">${esc(prefill.notice)}</p>` : "") +
    `<label class="expression-field">材料来源<select id="expression-source"><option value="">上传文件或粘贴原文</option>` +
    sources.map(s => `<option value="${esc(s.op_id)}">${esc(s.source_name || s.op_id)} · ${esc((s.source_hash || "").slice(0, 12))}</option>`).join("") + `</select></label>` +
    `<div id="expression-source-input"><label class="expression-field">原稿文件<input id="expression-source-file" type="file" accept=".md,.markdown,.txt,text/markdown,text/plain"></label>` +
    `<label class="expression-field">原稿名称<input id="expression-source-name" type="text" maxlength="120" value="用户选择的原稿"></label>` +
    `<label class="expression-field">完整原文<textarea id="expression-source-text" rows="10" spellcheck="false"></textarea></label></div>` +
    (legacyFields.length ? `<div class="expression-review"><h3>旧格式表达的处理范围</h3>` +
      `<p class="hint">每节默认保留当前全文。有必需原稿内容无法合入时，候选会保留缺口。可明确选择采用原稿，或保留两者；准备后请核对当前→候选及原稿→适配。</p>` +
      `<p class="hint">采用原稿会替换当前本节。保留两者会完整保留原稿的新条目，再把当前本节追加为仅用于本机聊天的私有配置；不会自动去重，冲突仍可能阻止应用。</p>` +
      legacyFields.map(([title, text], index) => `<details class="expression-item" open><summary>${esc(title)}</summary>` +
        `<h4>当前表达</h4><pre>${esc(text)}</pre><label class="expression-field">本次处理<select id="expression-legacy-${index}">` +
        ["preserve", "replace", "merge"].map(choice => `<option value="${choice}">${esc(expressionLegacyChoice(choice))}</option>`).join("") +
        `</select></label></details>`).join("") +
      `<p id="expression-legacy-scope" class="hint" role="status"></p></div>` :
      `<p class="hint">当前结构化表达中的用户修订会按已记录的范围合入；准备后请核对当前→候选及原稿→适配。</p>`) +
    `<p class="hint">缺少原稿时可继续使用原表达；这里不会从摘要反推原文。关闭窗口不会激活候选，也不会取消正在进行的准备；可从更新记录找回结果并取消候选。</p>` +
    `<p id="expression-update-status" class="hint" role="status"></p><button id="expression-prepare" class="btn primary" type="button">准备表达候选</button>` +
    `<details class="expression-review"><summary>已有候选与恢复记录（${operations.length} 项）</summary><div id="expression-operation-list"></div></details>`;
  if (prefill.source_op_id) {
    $("#expression-source").value = prefill.source_op_id;
    $("#expression-source-input").classList.toggle("hidden", !!$("#expression-source").value);
  }
  const legacyChoices = () => Object.fromEntries(legacyFields.map(([title], index) =>
    [title, $(`#expression-legacy-${index}`).value]));
  let legacyScopeNote = "";
  const updateLegacyScope = () => {
    if (!legacyFields.length) return;
    $("#expression-legacy-scope").textContent = legacyScopeNote + Object.entries(legacyChoices()).map(([title, choice]) =>
      `${title}：${expressionLegacyChoice(choice)}`).join("；") +
      "。选择只用于下次准备，已有候选保持原范围；新候选重新核对后仍需明确点击应用。";
  };
  const resetLegacyChoices = () => {
    if (!legacyFields.length) return;
    legacyFields.forEach((_, index) => { $(`#expression-legacy-${index}`).value = "preserve"; });
    legacyScopeNote = "原稿来源或正文已变化，处理范围已重置为全部保留；请重新确认各节选择。";
    updateLegacyScope();
  };
  if (legacyFields.length) {
    legacyFields.forEach(([title], index) => {
      const select = $(`#expression-legacy-${index}`);
      const chosen = prefill.legacy_choices?.[title];
      select.value = ["preserve", "replace", "merge"].includes(chosen) ? chosen : "preserve";
      select.onchange = () => { legacyScopeNote = ""; updateLegacyScope(); };
    });
    updateLegacyScope();
  }
  $("#expression-source").onchange = () => {
    $("#expression-source-input").classList.toggle("hidden", !!$("#expression-source").value);
    resetLegacyChoices();
  };
  $("#expression-source-text").oninput = resetLegacyChoices;
  let reading = false;
  $("#expression-source-file").onchange = async (event) => {
    const file = event.target.files[0]; if (!file) return;
    reading = true; $("#expression-prepare").disabled = true;
    try {
      if (file.size > 1048576) throw new Error("文件超过 1 MiB，请明确缩小原稿范围后再准备");
      const content = await file.text();
      if (!expressionViewVisible(view)) return;
      $("#expression-source-text").value = content; $("#expression-source-name").value = file.name.slice(0, 120);
      resetLegacyChoices();
      $("#expression-update-status").textContent = `已读取 ${content.length} 字符；服务会进一步核对原文上限。`;
    } catch (e) { if (expressionViewVisible(view)) $("#expression-update-status").textContent = e.message; }
    finally { reading = false; if (expressionViewVisible(view)) $("#expression-prepare").disabled = false; }
  };
  const operationList = $("#expression-operation-list");
  operations.forEach(op => {
    const row = document.createElement("div"); row.className = "expression-operation";
    const label = document.createElement("span"); label.textContent = `${expressionStateLabel(op.status)} · ${op.source_name || op.token || "更新记录"}`;
    const inspect = document.createElement("button"); inspect.type = "button"; inspect.className = "btn ghost"; inspect.textContent = "查看记录";
    inspect.onclick = async () => {
      inspect.disabled = true;
      try { const detail = await expressionUpdateAPI({action: "detail", token: op.token}); if (expressionViewVisible(view)) showExpressionCandidate(detail); }
      catch (e) { if (expressionViewVisible(view)) $("#expression-update-status").textContent = `读取记录失败：${e.message}`; }
      finally { inspect.disabled = false; }
    };
    row.append(label, inspect); operationList.appendChild(row);
  });
  let preparing = false;
  $("#expression-prepare").onclick = async () => {
    if (preparing || reading) return;
    const sourceId = $("#expression-source").value;
    const payload = {action: "prepare", revision: expression.revision, session_epoch: expression.session_epoch};
    if (legacyFields.length) payload.legacy_choices = legacyChoices();
    if (sourceId) {
      const selectedSource = sources.find(source => source.op_id === sourceId);
      if (!selectedSource?.source_hash) {
        $("#expression-update-status").textContent = "所选原稿缺少版本信息，请刷新后重新选择。"; return;
      }
      payload.source_op_id = sourceId;
      payload.source_hash = selectedSource.source_hash;
    }
    else {
      payload.source_text = $("#expression-source-text").value;
      payload.source_name = $("#expression-source-name").value;
      if (!payload.source_text.trim()) { $("#expression-update-status").textContent = "请先选择或粘贴原稿。"; return; }
    }
    preparing = true;
    view.querySelectorAll("input,textarea,select,button").forEach(el => el.disabled = true);
    $("#expression-update-status").textContent = "正在登记原文、适配表达并分别核对事实与保真。完成前当前人格保持原样。";
    try {
      const result = await expressionUpdateAPI(payload);
      if (expressionViewVisible(view)) showExpressionCandidate(result);
      else toast("表达准备已结束，可从人格 → 从原稿更新表达 → 更新记录查看。", 6000);
    } catch (e) {
      if (expressionViewVisible(view)) $("#expression-update-status").textContent = `准备结果未能确认：${e.message}。请返回后查看更新记录再决定下一步。`;
    } finally {
      preparing = false;
      if (expressionViewVisible(view)) view.querySelectorAll("input,textarea,select,button").forEach(el => el.disabled = false);
    }
  };
}

function showExpressionCandidate(result) {
  let pending = false;
  const adjustLegacyScope = async () => {
    if (pending) return;
    pending = true;
    const view = $("#expression-candidate-view");
    $("#modal-foot").querySelectorAll("button").forEach(el => el.disabled = true);
    try {
      const current = await api("/api/persona");
      if (!expressionViewVisible(view)) return;
      if (current.expression?.format !== "v1") throw new Error("当前已不是可用的旧格式表达，请返回当前人格重新准备");
      const sameVersion = current.expression.revision === result.revision && current.expression.session_epoch === result.session_epoch;
      await openExpressionUpdate(current.expression, {source_op_id: result.token, source_hash: result.source_hash,
        legacy_choices: sameVersion ? result.legacy_choices : undefined,
        notice: sameVersion ? "正在另行准备同份原稿。请调整范围后点击准备；原候选及其范围保持不变。" :
          "当前人格或会话已变化，未沿用旧候选的范围选择。请根据当前正文重新确认；旧格式默认保护全部三节。"});
    } catch (e) {
      if (expressionViewVisible(view)) $("#expression-candidate-error").textContent = `暂时无法调整范围：${e.message}`;
    } finally {
      pending = false;
      if (expressionViewVisible(view)) $("#modal-foot").querySelectorAll("button").forEach(el => el.disabled = false);
    }
  };
  const inspectRelated = async (token) => {
    if (pending) return;
    pending = true;
    const view = $("#expression-candidate-view");
    $("#modal-foot").querySelectorAll("button").forEach(el => el.disabled = true);
    try {
      const record = await expressionUpdateAPI({action: "detail", token});
      if (expressionViewVisible(view)) showExpressionCandidate(record);
    } catch (e) {
      if (expressionViewVisible(view)) $("#expression-candidate-error").textContent = `关联记录暂时无法读取：${e.message}`;
    } finally {
      pending = false;
      if (expressionViewVisible(view)) $("#modal-foot").querySelectorAll("button").forEach(el => el.disabled = false);
    }
  };
  const run = async (action) => {
    if (pending) return;
    pending = true;
    const view = $("#expression-candidate-view");
    $("#modal-foot").querySelectorAll("button").forEach(el => el.disabled = true);
    try {
      let revision = result.revision, epoch = result.session_epoch;
      if (action === "rollback") {
        const current = await api("/api/persona");
        revision = current.expression?.revision; epoch = current.expression?.session_epoch;
        if (!revision || !epoch) throw new Error("无法核对当前人格，请返回查看状态");
      }
      const response = await expressionUpdateAPI({action, token: result.token, revision, session_epoch: epoch});
      if (!expressionViewVisible(view)) { toast("表达操作已结束，请从更新记录核对结果。", 5000); return; }
      // A rollback is a second transaction; this view remains the original update.
      showExpressionCandidate({...result, ...response, token: result.token,
        ...(action === "rollback" ? {rollback_token: response.rollback_token} : {})});
    } catch (e) {
      if (expressionViewVisible(view)) $("#expression-candidate-error").textContent = `操作未能确认：${e.message}。请先查看更新记录，避免重复准备。`;
    } finally {
      pending = false;
      if (expressionViewVisible(view)) $("#modal-foot").querySelectorAll("button").forEach(el => el.disabled = false);
    }
  };
  const inactive = ["candidate_inactive", "uncovered", "prepare_failed"].includes(result.status);
  openModal("表达更新记录", "准备、应用与回退分别记录；通过模型核对仍需真人观察表达效果。",
    `<div id="expression-candidate-view">${expressionDetails(result)}<p id="expression-candidate-error" class="hint" role="status"></p></div>`, [
      ...(result.ready === true && result.status === "candidate_inactive" ? [{label: "应用此表达候选", cls: "btn primary", onClick: () => run("apply")}] : []),
      ...(inactive && result.token ? [{label: "取消此候选", onClick: () => run("cancel")}] : []),
      ...(inactive && result.token && result.legacy_choices && Object.keys(result.legacy_choices).length ?
        [{label: "调整范围并重新准备", onClick: adjustLegacyScope}] : []),
      ...(result.status === "applied" ? [{label: "回退此次表达更新", onClick: () => run("rollback")}] : []),
      ...(result.rollback_token ? [{label: "查看回退记录", onClick: () => inspectRelated(result.rollback_token)}] : []),
      ...(result.rollback_of ? [{label: "查看原更新记录", onClick: () => inspectRelated(result.rollback_of)}] : []),
      {label: "查看当前人格", onClick: openPersonaView}, {label: "关闭", onClick: closeModal},
    ]);
}

function openPersonaImport(prefill = "", prefillSource = "") {
  $("#modal-title").textContent = "导入人格文档";
  $("#modal-meta").textContent = "会替换当前人格；旧版自动存进 history，可回滚。";
  $("#modal-body").innerHTML = `
    <div class="drop" id="m-drop">
      <div class="drop-icon">↓</div>
      <div class="drop-main">把文档拖到这里，或 <button class="link" id="m-pick" type="button">选择文件</button></div>
      <div class="hint">须含一行「## 底色」；生长小节缺失会自动补齐</div>
      <input type="file" id="m-file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden>
    </div>
    <textarea id="m-md" rows="10" spellcheck="false" placeholder="# Alice 的人格&#10;&#10;## 底色 · 气质与风格&#10;&#10;- …"></textarea>
    <div id="m-check" class="check"></div>
    <div class="row">
      <button id="m-preview" class="btn ghost" disabled>预览</button>
      <button id="m-go" class="btn primary" disabled>替换并保存</button>
    </div>`;
  const foot = $("#modal-foot");
  foot.textContent = "";
  const cancel = document.createElement("button");
  cancel.className = "btn ghost";
  cancel.textContent = "取消";
  cancel.onclick = () => openPersonaView();
  foot.appendChild(cancel);
  $("#modal").classList.remove("hidden");

  const setImportText = wireImport(
    {
      drop: $("#m-drop"),
      pick: $("#m-pick"),
      file: $("#m-file"),
      box: $("#m-md"),
      check: $("#m-check"),
      preview: $("#m-preview"),
      go: $("#m-go"),
      idle: "替换并保存",
      busy: "替换中…",
      reset: false,
      back: (text, name) => openPersonaImport(text, name),
    },
    async (md, src, resumeOpId, markInstalled) => {
      const installed = await postJSON("/api/persona", { persona_md: md, source: src,
        ...(resumeOpId ? {resume_op_id: resumeOpId} : {}) });
      markInstalled();
      state = await api("/api/state");
      const warningCount = (Array.isArray(installed.expression_coverage?.warnings) ? installed.expression_coverage.warnings.length : 0)
        + (Array.isArray(installed.expression_coverage?.review_notices) ? installed.expression_coverage.review_notices.length : 0);
      toast(warningCount ? `人格已替换，旧版已快照；有 ${warningCount} 项覆盖或表达审阅提示，请在当前人格页查看。` : "人格已替换，旧版已快照", warningCount ? 6000 : 3000);
      await openPersonaView();
    },
  );
  if (prefill) {
    setImportText(prefill, prefillSource);
  }
  setTimeout(() => $("#m-md").focus(), 30);
}

$("#modal-x").onclick = closeModal;
$("#modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) closeModal();
});
$("#persona-btn").onclick = () => openPersonaView();

/* ---------- 设置 ---------- */

let restartCommand = null;

function showRestartCommand(command) {
  if (typeof command === "string" && command.trim()) restartCommand = command;
  $("#restart-command").textContent = restartCommand
    || "未能读取本次启动命令。请使用启动此服务时的原命令，并保留 --config 和 --port 参数。";
}

async function loadRuntimeInfo() {
  restartCommand = null;
  try {
    const info = await api("/api/runtime", { headers: { "X-Alice-Intent": "runtime" } });
    showRestartCommand(info.restart_command);
  } catch (e) {
    showRestartCommand(null);
  }
}

async function loadSettings() {
  const thinking = $("#thinking");
  thinking.disabled = true;
  try {
    const d = await api("/api/settings");
    if (d.thinking !== "on" && d.thinking !== "off") throw new Error("思考深度状态无效");
    thinking.value = d.thinking;
    thinking.disabled = false;
  } catch (e) {
    toast("读取设置失败：" + e.message, 4000);
  }
}

function openSettings(section = "general") {
  $("#settings-modal").classList.remove("hidden");
  window.AliceUI?.selectSettings(section);
  window.AliceQQ?.open();
  loadSettings();
  loadModelSettings();
  loadRuntimeInfo();
}

function closeSettings() {
  $("#settings-modal").classList.add("hidden");
  window.AliceWorkers?.setSettingsVisible(false);
  window.AliceClaudeSettings?.setVisible(false);
  window.AliceQQ?.close();
}

function openQQSettings() {
  openSettings("qq");
  $("#qq-settings").scrollIntoView({ block: "start" });
  $("#qq-settings").focus({ preventScroll: true });
}

$("#qq-btn").onclick = openQQSettings;
$("#welcome-qq-btn").onclick = openQQSettings;

$("#settings-btn").onclick = () => openSettings();
$("#welcome-settings-btn").onclick = () => openSettings();
$("#settings-x").onclick = closeSettings;
$("#settings-modal").addEventListener("click", (e) => {
  if (e.target.id === "settings-modal") closeSettings();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#settings-modal").classList.contains("hidden")) closeSettings();
});

$("#thinking").onchange = async () => {
  const thinking = $("#thinking");
  thinking.disabled = true;
  try {
    const d = await postJSON("/api/settings", { thinking: thinking.value });
    thinking.value = d.thinking;
    thinking.disabled = false;
  } catch (e) {
    toast(e.message);
    loadSettings();
  }
};

const MODEL_PRESETS = {
  zhipu: {
    base_url: "https://open.bigmodel.cn/api/paas/v4/",
    chat_model: "glm-4.6",
    cheap_model: "glm-4.5-flash",
  },
  deepseek: {
    base_url: "https://api.deepseek.com",
    chat_model: "deepseek-v4-pro",
    cheap_model: "deepseek-flash",
  },
};
const MODEL_IDS = ["#model-provider", "#model-base-url", "#model-chat", "#model-cheap",
  "#model-key-mode", "#model-test", "#model-save"];
let modelRevision = null;
let modelWorking = false;

function modelBusy(on) {
  modelWorking = on;
  MODEL_IDS.forEach((id) => { $(id).disabled = on || !modelRevision; });
  $("#model-key").disabled = on || !modelRevision || $("#model-key-mode").value !== "replace";
}

function modelResult(message, kind = "") {
  const el = $("#model-result");
  el.textContent = message;
  el.className = "settings-result" + (kind ? ` ${kind}` : "");
}

async function loadModelSettings() {
  if (modelWorking) return;
  modelRevision = null;
  modelBusy(true);
  modelResult("正在读取本机配置…");
  try {
    const d = await api("/api/model-config", {
      headers: { "X-Alice-Intent": "model-config" },
    });
    modelRevision = d.revision;
    $("#model-provider").value = d.provider || "custom";
    $("#model-base-url").value = d.base_url || "";
    $("#model-chat").value = d.chat_model || "";
    $("#model-cheap").value = d.cheap_model || "";
    $("#model-key-mode").value = "keep";
    $("#model-key").value = "";
    const source = d.key_source === "file" ? "配置文件" : d.key_source === "environment" ? "环境变量" : "";
    $("#model-key-status").textContent = d.key_present
      ? `当前密钥已配置（${source}），不会回显。`
      : "当前没有可用密钥；请选择“替换密钥”。";
    modelResult("");
  } catch (e) {
    modelResult(`读取失败：${e.message}`, "error");
  } finally {
    modelBusy(false);
  }
}

$("#model-provider").onchange = () => {
  const preset = MODEL_PRESETS[$("#model-provider").value];
  if (!preset) return;
  $("#model-base-url").value = preset.base_url;
  $("#model-chat").value = preset.chat_model;
  $("#model-cheap").value = preset.cheap_model;
  modelResult("已填入预设建议值；保存前可继续修改。", "");
};

$("#model-key-mode").onchange = () => {
  $("#model-key").disabled = modelWorking || $("#model-key-mode").value !== "replace";
  if ($("#model-key-mode").value !== "replace") $("#model-key").value = "";
};

function modelPayload() {
  return {
    provider: $("#model-provider").value,
    base_url: $("#model-base-url").value.trim(),
    chat_model: $("#model-chat").value.trim(),
    cheap_model: $("#model-cheap").value.trim(),
    replace_api_key: $("#model-key-mode").value === "replace",
    api_key: $("#model-key-mode").value === "replace" ? $("#model-key").value : "",
    revision: modelRevision,
  };
}

async function postModel(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Alice-Intent": "model-config" },
    body: JSON.stringify(payload),
  });
}

$("#model-test").onclick = async () => {
  if (!modelRevision || modelWorking) return;
  modelBusy(true);
  modelResult("正在用候选配置测试两个模型，可能需要几十秒…");
  try {
    const d = await postModel("/api/model-config/test", modelPayload());
    modelResult(d.message || "候选连接可用。", "ok");
  } catch (e) {
    modelResult(`候选连接失败：${e.message}；当前配置未改变。`, "error");
  } finally {
    modelBusy(false);
  }
};

$("#model-save").onclick = async () => {
  if (!modelRevision || modelWorking) return;
  modelBusy(true);
  modelResult("正在验证候选配置并保存；当前对话轮次会先完成…");
  try {
    const d = await postModel("/api/model-config", modelPayload());
    modelRevision = d.revision;
    $("#model-key-mode").value = "keep";
    $("#model-key").value = "";
    $("#model-key-status").textContent = "当前密钥已配置，不会回显。";
    modelResult("已保存；随后开始的轮次立即使用新配置，无需重启。", "ok");
  } catch (e) {
    modelResult(`保存失败：${e.message}；请关闭设置后重新打开，核对当前配置。`, "error");
  } finally {
    modelBusy(false);
  }
};

$("#shutdown-btn").onclick = async () => {
  try { await refreshActivity(); } catch (_) { /* 关闭接口会再核验生命周期。 */ }
  const qqHint = activity?.channels_enabled
    ? "\nQQ 桥会暂停提交；尚未发送或发送结果未知的回复需在桥记录中核对。建议先暂停 QQ 桥。" : "";
  const ok = window.confirm("确定关闭 Alice 服务吗？\n正在进行的回复与记忆整理会完成，然后服务进程退出。关闭浏览器标签页本身不会退出服务。" + qqHint);
  if (!ok) return;
  shuttingDown = true;
  clearInterval(syncTimer);
  $("#settings-modal").classList.add("hidden");
  window.AliceClaudeSettings?.setVisible(false);
  window.AliceQQ?.close();
  window.AliceObserver?.shutdown();
  window.AliceEvaluations?.shutdown();
  window.AliceWorkers?.shutdown();
  $("#shutdown-screen").classList.remove("hidden");
  $("#shutdown-title").textContent = "正在关闭 Alice";
  $("#shutdown-state").textContent = "正在等待当前回复完成，并整理本机记忆…";
  $("#shutdown-return").classList.add("hidden");
  showRestartCommand(restartCommand);
  $("#shutdown-btn").disabled = true;
  $("#send").disabled = true;
  $("#input").disabled = true;
  try {
    const r = await fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Alice-Intent": "shutdown" },
      body: "{}",
    });
    const d = await r.json().catch(() => ({}));
    showRestartCommand(d.restart_command);
    if (r.ok && d.status === "closed") {
      $("#shutdown-title").textContent = "Alice 服务已退出";
      $("#shutdown-state").textContent = "当前回复和退出前整理已完成；对话与记忆保存在本机。";
    } else if (d.status === "closed_with_warning") {
      $("#shutdown-title").textContent = "服务已退出，记忆整理未完成";
      $("#shutdown-state").textContent = d.error || "原始对话已保存在本机，重新启动后可继续整理。";
    } else {
      $("#shutdown-title").textContent = "退出失败或未能确认";
      $("#shutdown-state").textContent = d.error || `服务器返回 HTTP ${r.status}；请检查本机进程与数据。`;
      $("#shutdown-return").classList.remove("hidden");
    }
  } catch (e) {
    $("#shutdown-title").textContent = "无法确认服务是否已退出";
    $("#shutdown-state").textContent = "关闭请求的连接已中断。请在终端检查进程，再决定是否重新启动；不要盲目重发正在进行的对话。";
    $("#shutdown-return").classList.remove("hidden");
  }
};
$("#shutdown-return").onclick = () => location.reload();

/* ---------- 发送与流式接收 ---------- */

function ensureThinking(node) {
  let d = node.querySelector("details.think");
  if (!d) {
    d = document.createElement("details");
    d.className = "think";
    d.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "思考过程";
    const body = document.createElement("div");
    body.className = "r-txt";
    d.appendChild(summary);
    d.appendChild(body);
    node.insertBefore(d, node.querySelector(".txt"));
  }
  return d.querySelector(".r-txt");
}

function handleEvent(ev, node, userNode, requestId) {
  if (ev.type === "done" || ev.type === "error") {
    replyMotion(node, false);
    window.AliceWorkers?.refreshAfterTurn();
  }
  const txt = node.querySelector(".txt");
  const raw = () => txt.dataset.raw || "";
  if (ev.type === "start" || ev.type === "accepted") {
    if (ev.session_epoch) {
      sessionEpoch = ev.session_epoch;
      if (uncertainSend?.requestId === (ev.request_id || requestId)) uncertainSend.epoch = ev.session_epoch;
    }
    noteRequestNode(userNode, ev.request_id || requestId, "user");
    noteRequestNode(node, ev.request_id || requestId, "assistant");
    markUserWorkerTarget(userNode, { channel: "gui", ...(Object.hasOwn(ev, "worker_target") ? { worker_target: ev.worker_target } : {}) });
  } else if (ev.type === "reasoning") {
    ensureThinking(node).textContent += ev.text;
    scrollBottom();
  } else if (ev.type === "delta") {
    setBubbleText(txt, raw() + ev.text);
    scrollBottom();
  } else if (ev.type === "tool") {
    window.AliceWorkers?.noteTool(ev.name);
    const toolMessages = {
      remember: "（正在整理可保存的记忆…）",
      discovery_search: "（正在查找资料…）",
      observe_interest: "（正在整理兴趣依据…）",
      learn_from_sources: "（正在择要保存见闻…）",
      reflect_on_sources: "（正在整理带来源的初步看法…）",
      condense_observation: "（正在浓缩见闻…）",
      reuse_observation: "（正在复用已有见闻…）"
    };
    sysline(toolMessages[ev.name] || "（翻了一下记忆…）");
  } else if (ev.type === "search") {
    window.AliceChatSearch?.progress(ev, node);
  } else if (ev.type === "meta") {
    sysline(ev.text);
  } else if (ev.type === "done") {
    if (ev.reply && ev.reply !== raw()) setBubbleText(txt, ev.reply);
    if (!raw().trim()) txt.textContent = "（沉默）";
    renderMemory(ev.memory);
    if (ev.session_epoch) sessionEpoch = ev.session_epoch;
    const id = ev.request_id || requestId;
    markTurn(userNode, { id: ev.user_turn_id, role: "user", request_id: id, channel: "gui",
      ...(Object.hasOwn(ev, "worker_target") ? { worker_target: ev.worker_target } : {}) });
    markTurn(node, { id: ev.assistant_turn_id, role: "assistant", request_id: id, channel: "gui" });
    noteInspectorLink(node);  // request_id 此时才确定；新回复的入口在完成时补挂（元数据行不会自行重渲染）
    confirmComposerRequest(id);
    setChatNotice("");
    scrollBottom();
    window.AliceObserver?.refreshAfterTurn();
  } else if (ev.type === "error") {
    if (!raw().trim() && !txt.textContent.trim()) node.remove();
    sysline(ev.message);
    setChatNotice("本次处理未能完成确认；请先同步历史记录。再次发送相同文字会沿用原请求，不自动重新生成。");
    window.AliceObserver?.refreshAfterTurn();
  }
}

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!state || !text || busy || shuttingDown) return;
  if (!sessionEpoch) {
    setChatNotice("请先同步当前会话，核对记录后再点击发送；草稿与本页 P1 许可已保留。");
    await syncHistory();
    return;
  }
  if (composerWorkerReference && composerWorkerReference.epoch !== sessionEpoch) {
    composerWorkerReference = null; renderComposerWorkerReference();
    setChatNotice("任务引用属于旧会话，已清除。请重新选择任务后再发送；输入文字保留。");
    return;
  }
  if (uncertainSend?.workerReference && (uncertainSend.text !== text || uncertainSend.epoch !== sessionEpoch)) {
    setChatNotice("上一条任务引用消息的结果尚未确认，不能更换文字或目标后作为重试发送。请先同步记录，或核对原请求结果。");
    return;
  }
  if (window.AliceEvaluations?.canSend() === false) {
    toast("核查许可正在登记，请等待登记完成再发送。", 4000);
    return;
  }
  const retry = uncertainSend?.text === text && uncertainSend.epoch === sessionEpoch ? uncertainSend : null;
  busy = true;
  connectionStatus("正在提交", "busy");
  const requestId = retry?.requestId || crypto.randomUUID().replaceAll("-", "");
  const reference = retry ? retry.workerReference : composerWorkerReference;
  const workerReference = reference ? { ...reference } : null;
  const workerTarget = workerReference ? { collaborator: workerReference.collaborator, task_id: workerReference.taskId } : null;
  // 查询旧请求不能挪用后来新授权的 P1；原请求没有许可时明确保持空值。
  const evaluationId = retry ? retry.evaluationId : window.AliceEvaluations?.peekForSend();
  const payload = { text, request_id: requestId,
    ...(sessionEpoch ? { session_epoch: sessionEpoch } : {}),
    ...(evaluationId ? { evaluation_id: evaluationId } : {}),
    ...(workerTarget ? { worker_target: workerTarget } : {}) };
  const epochAtSend = sessionEpoch;
  uncertainSend = { requestId, text, evaluationId, epoch: epochAtSend, workerReference, draftUnchanged: true };
  renderComposerWorkerReference();
  window.AliceChatSearch?.begin(requestId, epochAtSend);
  $("#chat-new-attempt").classList.add("hidden");
  input.disabled = true;
  $("#send").disabled = true;
  let node = null;
  let userNode = null;
  let accepted = false;
  let terminal = false;
  let rejected = false;
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Alice-Intent": evaluationId ? "evaluations" : "gui" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const error = await r.json().catch(() => ({}));
      const existing = /^request_(accepted|running|user_committed|interrupted|failed|generated|completed)/.test(error.code || "");
      rejected = !existing && ((error.code !== "status_unavailable" && r.status < 500)
        || ["busy", "draining", "not_ready"].includes(error.code));
      // 明确拒绝（尤其 busy）不消费 P1 的本页下一轮许可，也不清空草稿。
      // busy 只表示本次没新接单，仍保留原请求 ID，避免重试时漏掉已有记录。
      if (rejected && error.code !== "busy") uncertainSend = null;
      if (existing) {
        setChatNotice("这条请求已有处理记录。请先同步历史；再次点发送只会查询原请求，不会自动重跑。");
        if (["request_failed", "request_interrupted"].includes(error.code)) {
          uncertainSend.canStartNew = true;
          $("#chat-new-attempt").classList.remove("hidden");
        }
        return;
      }
      if (error.code === "session_changed") {
        resetHistory(null);
        setChatNotice("会话已更换，草稿已保留；请同步记录后确认是否发送到新会话。");
      } else {
        setChatNotice(error.error || `未能接收本条消息（HTTP ${r.status}）；草稿已保留。`);
      }
      return;
    }
    accepted = true;
    connectionStatus("正在回复", "busy");
    window.AliceEvaluations?.consumeForSend(evaluationId);
    input.value = "";
    resizeComposer();
    setChatNotice("");
    userNode = requestNodes.get(requestKey(requestId, "user"));
    if (!userNode?.isConnected) userNode = bubble("user", text, { animate: !retry });
    node = requestNodes.get(requestKey(requestId, "assistant"));
    if (!node?.isConnected) node = bubble("assistant", "", { animate: !retry });
    noteRequestNode(userNode, requestId, "user");
    noteRequestNode(node, requestId, "assistant");
    markUserWorkerTarget(userNode, { channel: "gui", worker_target: workerTarget }, workerReference?.title || "");
    if (!r.body) throw new Error("没有收到回复数据流");
    replyMotion(node, true);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const data = frame.split("\n").filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const event = JSON.parse(data);
        if (workerReference && (sessionEpoch !== epochAtSend || event.session_epoch && event.session_epoch !== epochAtSend)) {
          const nextEpoch = sessionEpoch !== epochAtSend ? sessionEpoch : event.session_epoch;
          if (!input.value.trim()) { input.value = text; resizeComposer(); }
          resetHistory(nextEpoch || null);
          setChatNotice("会话已更换，旧请求回复未并入当前会话。任务引用已清除，输入文字保留；请重新选择任务。");
          await reader.cancel();
          return;
        }
        if (event.type === "done" || event.type === "error") terminal = true;
        handleEvent(event, node, userNode, requestId);
      }
    }
    if (!terminal) throw new Error("连接结束，但尚未收到处理结果");
  } catch (e) {
    replyMotion(node, false);
    connectionStatus("结果待确认", "error");
    setChatNotice("连接中断，结果尚未确认。请先同步记录；不要把断线当成没有处理。再次发送相同文字会沿用原请求。");
    window.AliceObserver?.refreshAfterTurn();
    if (node && !node.querySelector(".txt").textContent.trim()) node.remove();
  } finally {
    replyMotion(node, false);
    // 未收到确认仍可能已接受；不能让一次性 P1 许可被另一个新请求复用。
    if (!accepted && !rejected) window.AliceEvaluations?.consumeForSend(evaluationId);
    busy = false;
    if (uncertainSend?.requestId === requestId && uncertainSend.workerReference && !input.value.trim()) {
      input.value = uncertainSend.text; resizeComposer();
    }
    renderComposerWorkerReference();
    window.AliceChatSearch?.setBusy(false);
    window.AliceEvaluations?.afterTurn();
    if (!shuttingDown) {
      input.disabled = false;
      $("#send").disabled = false;
      if (!document.querySelector(".modal:not(.hidden)")
          && [document.body, input, $("#send")].includes(document.activeElement)) input.focus({ preventScroll: true });
      await syncHistory();
    }
  }
}

$("#send").onclick = send;
$("#input").addEventListener("input", () => {
  if (uncertainSend) uncertainSend.draftUnchanged = false;
  resizeComposer();
});
$("#input").addEventListener("compositionstart", () => { composing = true; });
$("#input").addEventListener("compositionend", () => { composing = false; resizeComposer(); });
window.addEventListener("resize", resizeComposer);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !composing && e.keyCode !== 229) {
    e.preventDefault();
    send();
  }
});

function showStartupError() {
  $("#boot-status")?.classList.add("hidden");
  $("#app").classList.remove("hidden");
  connectionStatus("连接未完成", "error");
  setChatNotice("暂时无法读取 Alice 的状态。请确认本机服务正在运行，再点击“同步记录”重试；此时不会发送消息。");
  $("#send").disabled = true;
  $("#input").disabled = true;
  updateChatEmpty();
}

boot().catch(showStartupError);

/* ---------- 界面布局与焦点：只管理可见性，不接触业务接口 ---------- */
(() => {
  const byId = (id) => document.getElementById(id);
  const app = byId("app");
  const nav = byId("app-nav");
  const narrow = matchMedia("(max-width: 900px)");
  const modals = [byId("modal"), byId("settings-modal")];
  const shutdownScreen = byId("shutdown-screen");
  let activeModal = null;
  let modalReturn = null;
  let lastTrigger = null;
  const visible = (el) => !!el && !el.closest(".hidden, [inert]") && el.getClientRects().length > 0;
  const focusable = (root) => [...root.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])')].filter(visible);
  const focus = (el) => { if (visible(el)) el.focus({ preventScroll: true }); };

  function selectSettings(section = "general") {
    if (!["general", "appearance", "qq", "workers", "claude", "service"].includes(section)) section = "general";
    document.querySelectorAll("[data-settings]").forEach((el) => el.setAttribute("aria-pressed", String(el.dataset.settings === section)));
    document.querySelectorAll("[data-settings-section]").forEach((el) => el.classList.toggle("hidden", el.dataset.settingsSection !== section));
    byId("settings-modal").querySelector(".modal-body").scrollTop = 0;
    window.AliceWorkers?.setSettingsVisible(section === "workers" && !byId("settings-modal").classList.contains("hidden"));
    window.AliceClaudeSettings?.setVisible(section === "claude" && !byId("settings-modal").classList.contains("hidden"));
  }
  window.AliceUI = { selectSettings };
  document.querySelectorAll("[data-settings]").forEach((el) => el.addEventListener("click", () => selectSettings(el.dataset.settings)));
  // Keep one-turn paid permission visible even when developer navigation is closed.
  function syncEvaluationNotice() {
    const armed = !byId("eval-disarm").classList.contains("hidden");
    const uncertain = !byId("eval-forget-uncertain").classList.contains("hidden");
    const notice = byId("composer-evaluation");
    notice.classList.toggle("hidden", !armed && !uncertain);
    notice.textContent = armed ? "P1 已授权 · 下一轮附加核查（最多 2 请求）· 查看"
      : uncertain ? "P1 授权结果未确认 · 查看与处理" : "";
  }
  new MutationObserver(syncEvaluationNotice).observe(byId("eval-binding"), { childList: true, subtree: true, characterData: true });
  byId("composer-evaluation").addEventListener("click", () => {
    if (byId("evaluations").classList.contains("hidden")) byId("evaluations-btn").click();
    focus(byId("eval-close"));
  });
  syncEvaluationNotice();

  function setNav(open, returnFocus = true) {
    open = open && narrow.matches;
    app.classList.toggle("nav-open", open);
    byId("nav-backdrop").classList.toggle("hidden", !open);
    byId("nav-toggle").setAttribute("aria-expanded", String(open));
    // On a small screen the navigation overlays the page, so its background is inert.
    for (const id of ["chat", "observer", "evaluations"]) byId(id).inert = open;
    document.querySelectorAll(".worker-panel").forEach((panel) => { panel.inert = open; });
    if (open) focus(byId("nav-close"));
    else if (returnFocus) focus(byId("nav-toggle"));
  }
  byId("nav-toggle").addEventListener("click", () => setNav(!app.classList.contains("nav-open")));
  byId("nav-close").addEventListener("click", () => setNav(false));
  byId("nav-backdrop").addEventListener("click", () => setNav(false));
  narrow.addEventListener("change", () => {
    const wasOpen = app.classList.contains("nav-open");
    setNav(false, wasOpen && narrow.matches);
  });
  document.addEventListener("click", (e) => {
    const trigger = e.target.closest("button");
    if (trigger) lastTrigger = trigger;
  }, true);
  nav.addEventListener("click", (e) => {
    if (!e.target.closest(".nav-item")) return;
    if (app.classList.contains("nav-open")) setNav(false);
  });
  byId("chat-nav").addEventListener("click", () => {
    window.AliceObserver?.close();
    window.AliceEvaluations?.close();
    window.AliceWorkers?.close(false);
    setNav(false, false);
    focus(byId("input"));
  });

  function syncModal() {
    const next = !shutdownScreen.classList.contains("hidden") ? shutdownScreen
      : modals.find((el) => !el.classList.contains("hidden")) || null;
    if (next === activeModal) {
      if (next && !next.contains(document.activeElement)) focus(focusable(next)[0] || next);
      return;
    }
    if (next) {
      if (!activeModal) modalReturn = lastTrigger || document.activeElement;
      setNav(false, false);
      activeModal = next;
      app.inert = true;
      byId("welcome").inert = true;
      modals.forEach((el) => { el.inert = el !== next; });
      next.setAttribute("tabindex", "-1");
      // QQ explicitly focuses its section. Preserve that focus if already set.
      if (!next.contains(document.activeElement)) focus(focusable(next)[0] || next);
    } else {
      activeModal = null;
      app.inert = false;
      byId("welcome").inert = false;
      modals.forEach((el) => { el.inert = false; });
      if (visible(modalReturn)) focus(modalReturn);
      else if (!app.classList.contains("hidden")) focus(narrow.matches ? byId("nav-toggle") : byId("input"));
      modalReturn = null;
    }
  }
  const modalObserver = new MutationObserver(syncModal);
  modals.forEach((el) => modalObserver.observe(el, { attributes: true, attributeFilter: ["class"] }));
  modalObserver.observe(byId("modal-body"), { childList: true });
  modalObserver.observe(byId("modal-foot"), { childList: true });
  modalObserver.observe(shutdownScreen, { attributes: true, attributeFilter: ["class"] });

  // Desktop developer panels are non-modal. Move focus in/out without trapping it.
  for (const [panelId, buttonId, closeId] of [["observer", "observer-btn", "obs-close"], ["evaluations", "evaluations-btn", "eval-close"]]) {
    const panel = byId(panelId);
    let wasOpen = false;
    new MutationObserver(() => {
      const open = !panel.classList.contains("hidden");
      if (open === wasOpen) return;
      wasOpen = open;
      if (open) { byId("developer-nav").open = true; focus(byId(closeId)); }
      else if (panel.contains(document.activeElement) || document.activeElement === document.body) {
        focus(narrow.matches ? byId("nav-toggle") : byId(buttonId));
      }
    }).observe(panel, { attributes: true, attributeFilter: ["class"] });
  }
  document.addEventListener("keydown", (e) => {
    const navOpen = app.classList.contains("nav-open");
    const root = activeModal || (navOpen ? nav : null);
    if (e.key === "Escape") {
      if (root) {
        e.preventDefault(); e.stopImmediatePropagation();
        if (activeModal === shutdownScreen) return;
        if (activeModal === byId("settings-modal")) byId("settings-x").click();
        else if (activeModal) byId("modal-x").click();
        else setNav(false);
      } else if (byId("observer").contains(document.activeElement)) byId("obs-close").click();
      else if (byId("evaluations").contains(document.activeElement)) byId("eval-close").click();
      return;
    }
    if (e.key !== "Tab" || !root) return;
    const items = focusable(root);
    const first = items[0], last = items[items.length - 1];
    if (!first) { e.preventDefault(); root.focus(); }
    else if (e.shiftKey && (document.activeElement === first || !items.includes(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !items.includes(document.activeElement))) { e.preventDefault(); first.focus(); }
  }, true);
  document.addEventListener("focusin", (e) => {
    const root = activeModal || (app.classList.contains("nav-open") ? nav : null);
    if (root && !root.contains(e.target)) focus(focusable(root)[0] || root);
  });
  // Keep the composer above mobile keyboards without reserving an arbitrary overlay.
  const viewport = window.visualViewport;
  const fitViewport = () => { app.style.height = `${viewport ? viewport.height : window.innerHeight}px`; };
  viewport?.addEventListener("resize", fitViewport);
  window.addEventListener("resize", fitViewport);
  fitViewport();
  document.querySelectorAll(".card-x").forEach((button) => {
    if (!button.hasAttribute("aria-label")) button.setAttribute("aria-label", button.title || "关闭");
  });
})();

/* ---------- 本机外观偏好：与会话、服务配置和真实运行状态无关 ---------- */
(() => {
  const key = "alice.ui.motion";
  const root = document.documentElement;
  const control = document.getElementById("ui-motion");
  const state = document.getElementById("ui-motion-state");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const forcedColors = window.matchMedia("(forced-colors: active)");
  let preference = "system";
  let canSave = true;
  try { if (localStorage.getItem(key) === "off") preference = "off"; }
  catch { canSave = false; }

  function apply() {
    const enabled = preference !== "off" && !reduced.matches && !forcedColors.matches;
    root.dataset.motion = enabled ? "on" : "off";
    if (!enabled) document.querySelectorAll(".message-arriving").forEach((node) => node.classList.remove("message-arriving"));
    control.value = preference;
    state.textContent = preference === "off" ? "已关闭界面动效。"
      : reduced.matches ? "已跟随系统减少动态效果，当前为静态界面。"
      : forcedColors.matches ? "已跟随系统高对比度设置，当前为静态界面。"
      : "已启用轻量动效。";
    if (!canSave) state.textContent += " 此浏览器无法保存偏好，当前选择仅在本页生效。";
  }
  control.addEventListener("change", () => {
    preference = control.value === "off" ? "off" : "system";
    try { localStorage.setItem(key, preference); canSave = true; }
    catch { canSave = false; }
    apply();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== key && event.key !== null) return;
    preference = event.newValue === "off" ? "off" : "system";
    apply();
  });
  reduced.addEventListener("change", apply);
  forcedColors.addEventListener("change", apply);
  const pause = () => {
    root.dataset.motionPaused = String(document.hidden);
    if (document.hidden) document.querySelectorAll(".message-arriving").forEach((node) => node.classList.remove("message-arriving"));
  };
  document.addEventListener("visibilitychange", pause);
  pause();
  apply();
})();

/* ---------- 静态屏幕纹理：独立于动态偏好，不接触业务配置 ---------- */
(() => {
  const key = "alice.ui.texture";
  const root = document.documentElement;
  const control = document.getElementById("ui-texture");
  const state = document.getElementById("ui-texture-state");
  const forcedColors = window.matchMedia("(forced-colors: active)");
  const normalize = (value) => ["soft", "off"].includes(value) ? value : "scanlines";
  let preference = "scanlines", canSave = true;
  try { preference = normalize(localStorage.getItem(key)); }
  catch { canSave = false; }
  function apply() {
    root.dataset.texture = forcedColors.matches ? "off" : preference;
    control.value = preference;
    state.textContent = forcedColors.matches ? "高对比度模式下已隐藏屏幕纹理。"
      : preference === "off" ? "已关闭屏幕纹理，保持清晰显示。"
      : preference === "soft" ? "已启用柔和扫描线。" : "已启用标准扫描线。";
    if (!canSave) state.textContent += " 此浏览器无法保存偏好，当前选择仅在本页生效。";
  }
  control.addEventListener("change", () => {
    preference = normalize(control.value);
    try { localStorage.setItem(key, preference); canSave = true; }
    catch { canSave = false; }
    apply();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== key && event.key !== null) return;
    preference = normalize(event.newValue);
    apply();
  });
  forcedColors.addEventListener("change", apply);
  apply();
})();

/* ---------- 背景视差：独立平面与细小噪点，不接管点击、选择或滚动 ---------- */
(() => {
  const root = document.documentElement;
  const chat = document.getElementById("chat");
  const layer = chat.querySelector(".chat-atmosphere");
  const field = layer.querySelector(".atmosphere-grains");
  const grains = [];
  const fragment = document.createDocumentFragment();
  // Fixed decorative anchors, not live data. No particles are created while moving.
  for (let row = 0; row < 6; row++) {
    for (let column = 0; column < 9; column++) {
      const index = row * 9 + column;
      const u = (8 + column * 10.5 + (row % 2 ? 1.8 : -1.8)) / 100;
      const v = (14 + row * 13 + ((column * 3 + row) % 5 - 2) * .5) / 100;
      const grain = document.createElement("span");
      grain.className = "atmosphere-grain";
      grain.style.left = `${u * 100}%`;
      grain.style.top = `${v * 100}%`;
      grain.style.setProperty("--grain-width", `${index % 5 === 0 ? 5 : 2}px`);
      grain.style.setProperty("--grain-height", `${index % 5 === 0 ? 1 : 2}px`);
      grain.style.setProperty("--grain-alpha", `${.26 + index % 4 * .07}`);
      const spark = document.createElement("span");
      spark.className = "grain-spark";
      grain.appendChild(spark);
      fragment.appendChild(grain);
      grains.push({ node: grain, u, v, signature: "" });
    }
  }
  field.appendChild(fragment);
  // Free dust is separate from the fixed anchors used by the association paths.
  // Reuse a bounded pool; reseed only in the fully transparent cycle boundary.
  const motes = layer.querySelector(".atmosphere-motes");
  const moteCells = new WeakMap();
  const random = (low, high) => low + Math.random() * (high - low);
  function scatterMote(mote, index) {
    mote.style.left = `${6 + (index % 6 + random(.12, .88)) / 6 * 88}%`;
    mote.style.top = `${8 + (Math.floor(index / 6) + random(.12, .88)) / 6 * 82}%`;
    mote.style.setProperty("--mote-size", `${random(1, 2.2).toFixed(1)}px`);
    mote.style.setProperty("--mote-alpha", random(.3, .62).toFixed(2));
    mote.style.setProperty("--mote-mid-x", `${random(-18, 18).toFixed(1)}px`);
    mote.style.setProperty("--mote-mid-y", `${random(-22, 16).toFixed(1)}px`);
    mote.style.setProperty("--mote-end-x", `${random(-38, 38).toFixed(1)}px`);
    mote.style.setProperty("--mote-end-y", `${random(-48, 30).toFixed(1)}px`);
  }
  const moteFragment = document.createDocumentFragment();
  for (let index = 0; index < 36; index++) {
    const mote = document.createElement("span");
    mote.className = "atmosphere-mote";
    const duration = random(14, 28);
    mote.style.setProperty("--mote-cycle", `${duration.toFixed(2)}s`);
    mote.style.setProperty("--mote-phase", `${(-Math.random() * duration).toFixed(2)}s`);
    scatterMote(mote, index);
    moteCells.set(mote, index);
    moteFragment.appendChild(mote);
  }
  motes.appendChild(moteFragment);
  motes.addEventListener("animationiteration", (event) => {
    if (event.animationName !== "mote-wander" || !moteCells.has(event.target)
        || root.dataset.motion !== "on" || document.hidden || chat.closest("[inert], .hidden")) return;
    // Duration/phase remain fixed: changing them here would jump the CSS clock.
    scatterMote(event.target, moteCells.get(event.target));
  });
  // A visual metaphor for association, not a trace of private model reasoning.
  // Every route connects existing decorative anchors; no live data or polling.
  const thoughtField = layer.querySelector(".atmosphere-thoughts");
  const svgNS = "http://www.w3.org/2000/svg";
  const svgElement = (tag, attributes) => {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    return node;
  };
  const thoughtRoutes = [
    [0, 9, 19], [36, 28, 20], [8, 17, 25],
    [53, 44, 34], [46, 47, 48, 49], [1, 2, 3, 4],
  ].map((anchors, index) => {
    const route = svgElement("g", { class: "thought-route" });
    const duration = 21 + index % 3 * 3;
    route.style.setProperty("--thought-cycle", `${duration}s`);
    route.style.setProperty("--thought-phase", `${-index * 4.7}s`);
    route.style.setProperty("--thought-echo-phase", `${-index * 4.7 - duration * .5}s`);
    const points = anchors.map((anchor) => [grains[anchor].u * 1000, grains[anchor].v * 800]);
    let path = `M ${points[0].join(" ")}`;
    for (let i = 1; i < points.length; i++) {
      const [x0, y0] = points[i - 1], [x1, y1] = points[i];
      const midY = (y0 + y1) / 2;
      path += ` C ${x0} ${midY}, ${x1} ${midY}, ${x1} ${y1}`;
    }
    for (const type of ["trace", "signal"]) {
      route.appendChild(svgElement("path", {
        d: path, pathLength: "100", class: `thought-${type}`, "vector-effect": "non-scaling-stroke",
      }));
    }
    const echo = svgElement("g", { class: "thought-echo" });
    echo.appendChild(svgElement("path", {
      d: path, pathLength: "100", class: "thought-signal", "vector-effect": "non-scaling-stroke",
    }));
    route.appendChild(echo);
    const [endX, endY] = points[points.length - 1];
    const arrival = svgElement("g", { class: "thought-arrival" });
    arrival.appendChild(svgElement("circle", { cx: endX, cy: endY, r: "8", class: "thought-halo" }));
    arrival.appendChild(svgElement("circle", { cx: endX, cy: endY, r: "2", class: "thought-core" }));
    route.appendChild(arrival);
    thoughtField.appendChild(route);
    return { node: route, anchors, attention: "" };
  });
  const mouse = window.matchMedia("(hover: hover) and (pointer: fine)");
  let frame = 0;
  let clientX = 0, clientY = 0;
  const available = () => root.dataset.motion === "on" && mouse.matches
    && !document.hidden && !chat.closest("[inert], .hidden");

  function reset() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    for (const name of ["--look-x", "--look-y", "--view-pitch", "--view-yaw"]) layer.style.removeProperty(name);
    for (const grain of grains) {
      for (const name of ["--drift-x", "--drift-y", "--wake"]) grain.node.style.removeProperty(name);
      grain.signature = "";
      grain.wake = 0;
    }
    for (const route of thoughtRoutes) {
      route.node.style.removeProperty("--thought-attention");
      route.attention = "";
    }
  }
  function paint() {
    frame = 0;
    if (!available()) { reset(); return; }
    // One untransformed rectangle and fixed anchors avoid feedback from moving grains.
    const rect = layer.getBoundingClientRect();
    if (!rect.width || !rect.height) { reset(); return; }
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    const y = Math.max(0, Math.min(rect.height, clientY - rect.top));
    const nx = x / rect.width * 2 - 1, ny = y / rect.height * 2 - 1;
    layer.style.setProperty("--look-x", `${(-nx * 28).toFixed(1)}px`);
    layer.style.setProperty("--look-y", `${(-ny * 22).toFixed(1)}px`);
    layer.style.setProperty("--view-pitch", `${(-ny * 3.5).toFixed(2)}deg`);
    layer.style.setProperty("--view-yaw", `${(nx * 4.5).toFixed(2)}deg`);
    const radius = Math.min(260, Math.max(150, rect.width * .22));
    for (const grain of grains) {
      // Each plane extends 6% past the clipped frame to leave room for parallax.
      const dx = x - (grain.u * 1.12 - .06) * rect.width;
      const dy = y - (grain.v * 1.12 - .06) * rect.height;
      const proximity = Math.max(0, 1 - Math.hypot(dx, dy) / radius);
      const wake = proximity * proximity * (3 - 2 * proximity);
      grain.wake = wake;
      const driftX = (-dx / radius * 6 * wake).toFixed(1);
      const driftY = (-dy / radius * 6 * wake).toFixed(1);
      const strength = wake.toFixed(2);
      const signature = `${driftX}/${driftY}/${strength}`;
      if (signature === grain.signature) continue;
      grain.signature = signature;
      grain.node.style.setProperty("--drift-x", `${driftX}px`);
      grain.node.style.setProperty("--drift-y", `${driftY}px`);
      grain.node.style.setProperty("--wake", strength);
    }
    for (const route of thoughtRoutes) {
      const attention = Math.max(...route.anchors.map((index) => grains[index].wake)).toFixed(2);
      if (attention === route.attention) continue;
      route.attention = attention;
      route.node.style.setProperty("--thought-attention", attention);
    }
  }
  chat.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || event.buttons || !available()) { reset(); return; }
    clientX = event.clientX;
    clientY = event.clientY;
    if (!frame) frame = requestAnimationFrame(paint);
  }, { passive: true });
  for (const event of ["pointerleave", "pointercancel", "pointerdown"]) chat.addEventListener(event, reset, { passive: true });
  window.addEventListener("blur", reset);
  window.addEventListener("resize", reset);
  window.addEventListener("pagehide", reset);
  document.addEventListener("visibilitychange", reset);
  mouse.addEventListener("change", reset);
  new MutationObserver(reset).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
  // Dialogs and the mobile navigation make the conversation inert.
  const panelObserver = new MutationObserver(() => { if (!available()) reset(); });
  panelObserver.observe(document.getElementById("app"), { attributes: true, attributeFilter: ["class", "inert"] });
  panelObserver.observe(chat, { attributes: true, attributeFilter: ["inert"] });
  new ResizeObserver(reset).observe(chat);
})();
