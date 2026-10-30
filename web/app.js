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
let deliveryRefreshIndex = 0;
let sourceRefreshIndex = 0;
let sourceProjectionVersion = 0;
let sourceExpiryTimer = null;
let followingLatest = true;
let historyLoaded = false;
let composing = false;
const turnNodes = new Map();
const requestNodes = new Map();

async function api(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  if (!headers.has("X-Alice-Intent")) headers.set("X-Alice-Intent", "gui");
  const r = await fetch(path, { ...opts, headers, cache: "no-store" });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const error = new Error(body.error || `HTTP ${r.status}`);
    error.status = r.status;
    error.code = body.code;
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

function renderTurnMetadata(node) {
  const meta = node.querySelector(".message-meta");
  if (!meta) return;
  const channel = node.dataset.channel || "gui", fromQQ = channel.startsWith("qq");
  const assistant = node.classList.contains("assistant");
  meta.textContent = fromQQ ? (assistant ? "回复 QQ" : "QQ · 所有者")
    : channel === "local" ? "本机历史（来源未记录）" : "本机网页";
  if (!fromQQ) return;
  const unavailable = node.dataset.sourceBound === "true" && node.dataset.sourceAvailable !== "true";
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
}

function redactSourceNode(node, pending = false) {
  const txt = node.querySelector(".txt");
  if (txt) setBubbleText(txt, SOURCE_HISTORY_PLACEHOLDER);
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
    const txt = node.querySelector(".txt");
    if (txt && typeof update.content === "string") setBubbleText(txt, update.content);
    markSourceProjection(node, update);
  }
  scheduleSourceExpiry();
}

function sourceProjectionIds() {
  return [...turnNodes].filter(([, node]) => node.isConnected && node.dataset.sourceBound !== "false"
    && node.dataset.sourceAvailable !== "false").map(([id]) => id);
}

function invalidateSourceDisplays() {
  sourceProjectionVersion++;
  for (const node of turnNodes.values()) {
    if (node.dataset.sourceBound === "true" && node.dataset.sourceAvailable !== "false") redactSourceNode(node, true);
  }
  expireSourceDisplays();
  syncHistory();
}

window.addEventListener("alice:sources-changed", invalidateSourceDisplays);

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
  markSourceProjection(node, turn);
  renderTurnMetadata(node);
  scheduleSourceExpiry();
}

function mergeTurn(turn) {
  if (!["user", "assistant"].includes(turn.role) || !Number.isSafeInteger(turn.id)) return;
  const key = requestKey(turn.request_id, turn.role);
  let node = turnNodes.get(turn.id) || (key && requestNodes.get(key));
  if (!node || !node.isConnected) node = bubble(turn.role, "");
  const txt = node.querySelector(".txt");
  if (turn.role === "assistant") setBubbleText(txt, turn.content || "（沉默）");
  else txt.textContent = turn.content || "";
  markTurn(node, turn);
  const later = [...$("#msgs").querySelectorAll(".msg[data-turn-id]")]
    .find((other) => Number(other.dataset.turnId) > turn.id);
  if (later && later !== node) $("#msgs").insertBefore(node, later);
  if (uncertainSend?.requestId === turn.request_id && turn.role === "assistant") {
    uncertainSend = null;
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
  historyVersion += 1;
  sourceProjectionVersion++;
  sourceRefreshIndex = 0;
  clearTimeout(sourceExpiryTimer);
  sessionEpoch = epoch || null;
  historyCursor = 0;
  turnNodes.clear();
  requestNodes.clear();
  uncertainSend = null;
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
  uncertainSend = null;
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
      back: () => closeModal(),
    },
    async (md, src) => {
      if (!await confirmQQReset()) throw new Error("已取消初始化；请先处理 QQ 桥的待发送回复。");
      const st = await postJSON("/api/persona", { persona_md: md, source: src, reset: true });
      await startChat(st);
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
  /* Alice 格式：认「## 底色」；外部角色提示词（无底色）：整份装进底色层 */
  const aliceShape = /^##\s*底色/m.test(text);
  const words = text.length;
  if (!aliceShape) {
    const msg = words > 12000
      ? `没有「## 底色」：会整份放进底色层；但 ${words} 字偏长，每轮上下文都会被它占一块，建议压到 4000 字内。`
      : "没有「## 底色」小节：把整份文档放进底色层（适合角色提示词 / 角色卡），生长三节自动补占位。";
    return { ok: true, note: !words || words <= 12000, warn: words > 12000, msg };
  }
  if (words > 12000) {
    return { ok: true, warn: true, msg: `含「## 底色」小节，但 ${words} 字偏长——太长会挤压每轮上下文，建议压到 2000 字内。` };
  }
  return { ok: true, msg: "含「## 底色」小节，可以安装。缺失的生长小节会自动补齐。" };
}

function wireImport(els, install) {
  let md = "";
  let source = "";
  const refresh = (text) => {
    md = text;
    els.box.value = text;
    const v = validateMd(text);
    els.check.textContent = !text.trim() ? "" : (v.ok ? "✓ " : "✗ ") + v.msg;
    els.check.className = "check" + (v.ok && !v.warn ? " ok" : "") + (v.warn ? " warn" : "") + (v.note ? " note" : "");
    els.go.disabled = !v.ok;
    els.preview.disabled = !text.trim();
  };

  els.pick.onclick = () => els.file.click();
  els.file.onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    source = f.name;
    refresh(await f.text());
  };
  els.box.oninput = () => { source = ""; refresh(els.box.value); };

  /* 拖放 */
  els.drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.drop.classList.add("over");
  });
  els.drop.addEventListener("dragleave", () => els.drop.classList.remove("over"));
  els.drop.addEventListener("drop", async (e) => {
    e.preventDefault();
    els.drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (!/\.(md|markdown|txt)$/i.test(f.name)) {
      toast("只认 .md / .markdown / .txt", 3000);
      return;
    }
    source = f.name;
    refresh(await f.text());
  });

  els.preview.onclick = () => {
    openModal("人格预览", `${md.trim().length} 字`, `<div class="md">${renderMd(md)}</div>`, [
      { label: "返回编辑", onClick: () => (els.back ? els.back(md) : closeModal()) },
      { label: "关闭", onClick: closeModal },
    ]);
  };

  els.go.onclick = async () => {
    els.go.disabled = true;
    const idle = els.go.textContent;
    els.go.textContent = els.busy;
    try {
      await install(md, source || "用户导入");
    } catch (e) {
      toast(e.message, 5000);
      els.go.textContent = idle;
      els.go.disabled = !validateMd(md).ok;
    }
  };
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
    ? `<div class="md">${renderMd(p.md)}</div>` +
      (p.changelog && p.changelog.length
        ? `<div class="changelog"><div class="panel-title small">最近变更</div><ul>${p.changelog
            .map((x) => `<li>${esc(x)}</li>`)
            .join("")}</ul></div>`
        : "")
    : `<p class="hint">还没有安装人格。点下面的按钮导入一份，或以气质标签生成。</p>`;
  openModal("当前人格", meta, body, [
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

function openPersonaImport(prefill = "") {
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

  wireImport(
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
      back: (text) => openPersonaImport(text),
    },
    async (md, src) => {
      await postJSON("/api/persona", { persona_md: md, source: src });
      state = await api("/api/state");
      toast("人格已替换，旧版已快照");
      await openPersonaView();
    },
  );
  if (prefill) {
    const box = $("#m-md");
    box.value = prefill;
    box.dispatchEvent(new Event("input"));
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
  window.AliceQQ?.close();
  window.AliceObserver?.shutdown();
  window.AliceEvaluations?.shutdown();
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
  if (ev.type === "done" || ev.type === "error") replyMotion(node, false);
  const txt = node.querySelector(".txt");
  const raw = () => txt.dataset.raw || "";
  if (ev.type === "start" || ev.type === "accepted") {
    if (ev.session_epoch) {
      sessionEpoch = ev.session_epoch;
      if (uncertainSend?.requestId === (ev.request_id || requestId)) uncertainSend.epoch = ev.session_epoch;
    }
    noteRequestNode(userNode, ev.request_id || requestId, "user");
    noteRequestNode(node, ev.request_id || requestId, "assistant");
  } else if (ev.type === "reasoning") {
    ensureThinking(node).textContent += ev.text;
    scrollBottom();
  } else if (ev.type === "delta") {
    setBubbleText(txt, raw() + ev.text);
    scrollBottom();
  } else if (ev.type === "tool") {
    sysline(ev.name === "remember" ? "（悄悄记下了一件事…）" : "（翻了一下记忆…）");
  } else if (ev.type === "meta") {
    sysline(ev.text);
  } else if (ev.type === "done") {
    if (ev.reply && ev.reply !== raw()) setBubbleText(txt, ev.reply);
    if (!raw().trim()) txt.textContent = "（沉默）";
    renderMemory(ev.memory);
    if (ev.session_epoch) sessionEpoch = ev.session_epoch;
    const id = ev.request_id || requestId;
    markTurn(userNode, { id: ev.user_turn_id, role: "user", request_id: id, channel: "gui" });
    markTurn(node, { id: ev.assistant_turn_id, role: "assistant", request_id: id, channel: "gui" });
    if (uncertainSend?.requestId === id) uncertainSend = null;
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
  if (window.AliceEvaluations?.canSend() === false) {
    toast("核查许可正在登记，请等待登记完成再发送。", 4000);
    return;
  }
  busy = true;
  connectionStatus("正在提交", "busy");
  const retry = uncertainSend?.text === text && uncertainSend.epoch === sessionEpoch ? uncertainSend : null;
  const requestId = retry?.requestId || crypto.randomUUID().replaceAll("-", "");
  // 查询旧请求不能挪用后来新授权的 P1；原请求没有许可时明确保持空值。
  const evaluationId = retry ? retry.evaluationId : window.AliceEvaluations?.peekForSend();
  const payload = { text, request_id: requestId,
    ...(sessionEpoch ? { session_epoch: sessionEpoch } : {}),
    ...(evaluationId ? { evaluation_id: evaluationId } : {}) };
  const epochAtSend = sessionEpoch;
  uncertainSend = { requestId, text, evaluationId, epoch: epochAtSend };
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
$("#input").addEventListener("input", resizeComposer);
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
    if (!["general", "appearance", "qq", "service"].includes(section)) section = "general";
    document.querySelectorAll("[data-settings]").forEach((el) => el.setAttribute("aria-pressed", String(el.dataset.settings === section)));
    document.querySelectorAll("[data-settings-section]").forEach((el) => el.classList.toggle("hidden", el.dataset.settingsSection !== section));
    byId("settings-modal").querySelector(".modal-body").scrollTop = 0;
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
