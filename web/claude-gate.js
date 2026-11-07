/* Claude permission questions follow its own status controller. Read failures
 * preserve pending cards; answers are explicit, bound to the original request,
 * and never retried automatically. Worker statements are not host facts. */
(function () {
  "use strict";
  const base = "/api/workers/claude/permissions";
  const binding = ["id", "task_id", "attempt_id", "epoch", "policy_revision", "worker_id", "expires_at", "action_digest", "resource_digest"];
  const activeStates = new Set(["queued", "starting", "preparing", "running", "working", "waiting_permission", "collecting", "stopping"]);
  const records = new Map(), controllers = new Set();
  let box, list, note, refreshButton, status = null, active = false, stopped = false;
  let polling = false, answering = false, ready = false, generation = 0, nextRead = 0, failures = 0;
  let finalRead = false, lifecycle = 0;

  function el(tag, cls, text) {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function key(req) { return JSON.stringify(binding.map((name) => req[name])); }
  function summary(input) {
    try { return JSON.stringify(input || {}, null, 2); }
    catch { return "(无法展示)"; }
  }
  function canRead() { return !stopped && !document.hidden && !!box; }
  function controls() {
    if (!box) return;
    refreshButton.disabled = polling || answering || !canRead();
    for (const entry of records.values()) {
      const expired = Number(entry.req.expires_at) <= Date.now() / 1000;
      for (const button of entry.card.querySelectorAll("button")) {
        button.disabled = answering || !ready || !entry.fresh || expired || entry.req.stale || !canRead();
      }
    }
  }
  function message(text) { if (note) note.textContent = text; }
  function statusUnavailable(error) {
    ready = false; ++generation;
    message("Claude 权限状态待同步：" + (error?.message || error || "状态读取失败") + "。已有质询保留，同步后再答复。");
    if (box && records.size) box.hidden = false;
    controls();
  }
  async function request(action, payload) {
    const controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), payload ? 15000 : 8000);
    try {
      const response = await fetch(base + "/" + action, {
        method: payload ? "POST" : "GET", signal: controller.signal, cache: "no-store",
        headers: { "X-Alice-Intent": "workers", ...(payload ? { "Content-Type": "application/json" } : {}) },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.code === "host_busy" ? "Alice 正忙，权限列表尚未刷新" : data.error || "服务暂不可用（" + response.status + "）");
        error.code = data.code; throw error;
      }
      return data;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  async function answer(entry, decision) {
    if (answering || !ready || !entry.fresh || !canRead() || records.get(key(entry.req)) !== entry) return;
    if (entry.req.stale || Number(entry.req.expires_at) <= Date.now() / 1000) { controls(); return; }
    answering = true; ++generation; controls();
    const version = lifecycle, req = entry.req;
    const payload = Object.fromEntries(binding.map((name) => [name, req[name]]));
    try {
      const response = await request("answer", { ...payload, decision });
      if (stopped || version !== lifecycle) return;
      if (!response?.delivered) throw new Error(response?.error || "宿主未确认答复送达");
      entry.card.remove(); records.delete(key(req));
      message("Claude 质询已答复。");
      box.hidden = records.size === 0;
    } catch (error) {
      ready = false;
      if (!stopped && version === lifecycle) {
        message("Claude 答复未确认：" + (error.name === "AbortError" ? "请求超时" : error.message) + "。请同步质询后再决定，原首答不会被覆盖。");
        box.hidden = false;
      }
    } finally {
      answering = false; nextRead = 0; controls();
      // This refresh only reads state. It never repeats an answer POST.
      if (!stopped && version === lifecycle) window.AliceWorkers?.refresh?.("claude-agent");
    }
  }
  function createCard(req) {
    const card = el("div", "claude-gate-card"), head = el("div", "claude-gate-head");
    const entry = { req, card, fresh: true };
    head.append(el("strong", null, "Claude 权限质询"), el("span", "claude-gate-tool", req.tool));
    card.append(head, el("div", "claude-gate-input", summary(req.input)));
    if (req.resolved_resource) card.append(el("div", "claude-gate-input", "实际资源：" + req.resolved_resource));
    if (req.reason_hint) card.append(el("div", "claude-gate-hint", "工人自述（非宿主事实）：" + req.reason_hint));
    if (req.stale) card.append(el("div", "claude-gate-hint", "已超时，仅供查看。"));
    const actions = el("div", "claude-gate-actions");
    for (const [label, decision] of [["允许一次", "allow_once"], ["期限内允许相同完整文件动作", "allow_task"], ["拒绝", "deny"]]) {
      const button = el("button", "claude-gate-btn claude-gate-" + decision, label);
      button.type = "button"; button.onclick = () => answer(entry, decision); actions.append(button);
    }
    card.append(actions); return entry;
  }
  function render(data) {
    if (!Array.isArray(data?.pending) || data.pending.some((req) => !req || req.worker_id !== "claude-agent"
        || binding.some((name) => req[name] === undefined) || status && req.epoch !== status.session_epoch)) {
      throw new Error("Claude 权限列表身份或格式无效");
    }
    const partial = !!(data.scan?.exhausted || data.scan?.read_errors), incoming = new Set();
    for (const entry of records.values()) entry.fresh = false;
    for (const req of data.pending) {
      const identity = key(req); incoming.add(identity);
      let entry = records.get(identity);
      if (!entry) { entry = createCard(req); records.set(identity, entry); list.append(entry.card); }
      entry.req = req; entry.fresh = true;
    }
    if (!partial) for (const [identity, entry] of records) {
      if (!incoming.has(identity)) { entry.card.remove(); records.delete(identity); }
    }
    const messages = [];
    if (data.scan?.exhausted) messages.push("Claude 活动权限扫描达到上限，未读到的旧质询仍保留；请同步核对，勿重复派工。");
    if (data.scan?.read_errors) messages.push("有 " + data.scan.read_errors + " 项 Claude 权限记录读取失败，未将读取失败当作无待答请求。");
    if (data.answer_diagnostic?.count) messages.push(data.answer_diagnostic.last_code === "first_answer_preserved"
      ? "后来的不同答复已被拒绝，原首答保持。" : "部分 Claude 答复无法写入，请核查任务控制目录。");
    message(messages.join(" ")); ready = true; box.hidden = records.size === 0 && messages.length === 0;
  }
  async function refresh(force = true) {
    if (!canRead() || polling || answering || !force && (!active && !finalRead || Date.now() < nextRead)) return;
    polling = true; finalRead = false;
    const version = ++generation, life = lifecycle;
    controls();
    try {
      const data = await request("list");
      if (version !== generation || life !== lifecycle || !canRead()) return;
      render(data); failures = 0;
      nextRead = Date.now() + (status?.worker?.state === "waiting_permission" ? 1000 : 1800);
    } catch (error) {
      if (version !== generation || life !== lifecycle || !canRead()) return;
      failures++; nextRead = Date.now() + Math.min(30000, 2000 * 2 ** Math.min(failures - 1, 4));
      statusUnavailable(error.name === "AbortError" ? "读取超时" : error);
      box.hidden = false;
    } finally { polling = false; controls(); }
  }
  function syncStatus(data) {
    if (data?.worker_id !== "claude-agent" || !data.worker || !data.session_epoch) return;
    if (status && status.session_epoch !== data.session_epoch) {
      ++lifecycle; ++generation; ready = false;
      records.clear(); list?.replaceChildren(); if (box) box.hidden = true;
    }
    const wasActive = active, changed = status && status.revision !== data.revision;
    status = data;
    active = Number(data.worker.running) > 0 || Number(data.worker.queued) > 0 || activeStates.has(data.worker.state);
    if (changed || wasActive && !active) {
      ready = false; ++generation; nextRead = 0; finalRead = true;
      for (const entry of records.values()) entry.fresh = false;
    }
    if (!active) ready = false;
    controls(); refresh(false);
  }
  function shutdown() {
    stopped = true; ready = false; ++generation; ++lifecycle;
    controllers.forEach((controller) => controller.abort()); controls();
  }
  function init() {
    box = el("div", "claude-gate-panel"); box.hidden = true;
    const title = el("div", "claude-gate-title", "Claude 权限质询");
    refreshButton = el("button", "claude-gate-btn", "同步质询");
    refreshButton.type = "button"; refreshButton.onclick = () => refresh(true);
    title.append(refreshButton); note = el("div", "claude-gate-note", ""); list = el("div", "claude-gate-list");
    box.append(title, list, note); document.body.append(box);
    window.AliceWorkers?.refresh?.("claude-agent");
  }
  window.AliceClaudeGate = { syncStatus, statusUnavailable, refresh, shutdown };
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      ready = false; ++generation; controllers.forEach((controller) => controller.abort()); controls();
    } else { nextRead = 0; window.AliceWorkers?.refresh?.("claude-agent"); }
  });
  window.addEventListener("pagehide", shutdown);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { stopped = false; nextRead = 0; controls(); window.AliceWorkers?.refresh?.("claude-agent"); }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
