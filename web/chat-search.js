/* Conversation search progress and the source/memory bridge. Native DOM only. */
(() => {
  "use strict";
  const box = document.createElement("section");
  const $ = (id) => document.getElementById(id) || box.querySelector(`#${CSS.escape(id)}`);
  box.className = "chat-search hidden";
  box.setAttribute("aria-label", "对话内查找资料");
  box.innerHTML = `
    <div id="chat-search-progress" class="chat-search-progress hidden" aria-live="polite">
      <div class="chat-search-entry"><span id="chat-search-state"></span><button id="chat-search-stop" type="button" class="link hidden">停止查找</button></div>
      <p id="chat-search-counts"></p><ul id="chat-search-results"></ul>
    </div>`;
  const modal = document.createElement("dialog");
  modal.className = "source-memory-dialog";
  modal.setAttribute("aria-label", "记忆与来源");
  modal.innerHTML = '<div class="chat-search-entry"><h2>记忆与来源</h2><button type="button" class="btn ghost">关闭</button></div><div class="source-memory-body"></div>';
  document.body.append(modal);
  modal.querySelector("button").onclick = () => modal.close();
  let dataset = null, active = null, timer = null, expiryTimer = null, sending = false, detailVersion = 0;
  const names = { web: "全网",  tieba: "贴吧", bilibili: "B站", zhihu: "知乎", wikipedia: "维基百科", auto: "全网" };
  const phases = { queued: "正在准备查找", search: "正在搜索", read: "正在读取正文" };
  function node(tag, text, cls) { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; }
  function sourceLink(url, label) {
    try {
      const parsed = new URL(url);
      if (!["https:", "http:"].includes(parsed.protocol)) return node("span", label);
      const a = node("a", label); a.href = parsed.href; a.target = "_blank"; a.rel = "noopener noreferrer"; return a;
    } catch { return node("span", label); }
  }
  async function post(path, body, intent = "gui") {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Alice-Intent": intent }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || "操作未完成，请刷新后查看"); error.status = response.status; throw error; }
    return data;
  }
  function adopt(data) {
    if (dataset && dataset !== data.dataset_id) { reset(); }
    dataset = data.dataset_id;
  }
  async function refreshSettings() {
    try { const r = await fetch("/api/discovery", { headers: { "X-Alice-Intent": "discovery" }, cache: "no-store" }); if (r.ok) adopt(await r.json()); }
    catch { /* The next source/memory action rechecks the current session. */ }
  }
  function setBusy(value) {
    sending = value;
    if (!value && active?.phase === "idle") poll();
  }
  function begin(requestId, epoch) {
    clearTimeout(timer); clearTimeout(expiryTimer);
    setBusy(true);
    active = { request_id: requestId, session_epoch: epoch, status: "idle", phase: "idle", counts: {}, items: [] };
    box.classList.add("hidden");
  }
  function schedule() { clearTimeout(timer); if (active && active.phase !== "completed") timer = setTimeout(poll, 2000); }
  async function poll() {
    const current = active; if (!current) return;
    try {
      const data = await post("/api/chat/search/status", { request_id: current.request_id, session_epoch: current.session_epoch });
      if (active?.request_id === current.request_id) progress(data);
    } catch (error) {
      if (error.status === 409) reset();
      else if (error.status === 404 && !sending) { active = null; $("chat-search-stop").classList.add("hidden"); }
      else schedule();
    }
  }
  function progress(data, replyNode = null) {
    if (active?.request_id && active.request_id !== data.request_id) return;
    if (active?.sequence != null && data.sequence != null && data.sequence < active.sequence) return;
    clearTimeout(expiryTimer);
    data = { ...data, items: (data.items || []).filter((item) => !item.expires_at || item.expires_at * 1000 > Date.now()) };
    active = data;
    if (data.phase === "idle" || data.status === "not_used") {
      box.classList.add("hidden"); schedule(); return;
    }
    if (replyNode) replyNode.insertBefore(box, replyNode.querySelector(".txt"));
    else if (!box.isConnected) $("msgs").append(box);
    box.classList.remove("hidden");
    $("chat-search-progress").classList.remove("hidden");
    const done = data.phase === "completed";
    $("chat-search-state").textContent = done ? (data.status === "partial" ? "部分材料已取得 · " : "") + (data.message || "本次查找已结束")
      : `${phases[data.phase] || "正在查找资料"}${data.query ? `「${data.query}」` : ""}${data.phase !== "queued" && names[data.platform] ? ` · ${names[data.platform]}` : ""}`;
    $("chat-search-stop").classList.toggle("hidden", done);
    $("chat-search-stop").disabled = !!data.stop_requested;
    $("chat-search-stop").textContent = data.stop_requested ? "正在停止…" : "停止查找";
    const c = data.counts || {}, u = data.usage || {};
    $("chat-search-counts").textContent = [c.received != null ? `搜索返回 ${c.received} 条` : "", c.accepted != null ? `可读候选 ${c.accepted} 条` : "", c.attempted != null ? `尝试读取 ${c.attempted} 条` : "", c.readable != null ? `读到正文 ${c.readable} 条` : "", c.reused ? `复用 ${c.reused} 条` : "", c.returned != null ? `带回 ${c.returned} 份` : "", u.network != null ? `网络请求 ${u.network} 次` : ""].filter(Boolean).join(" · ");
    const list = $("chat-search-results"); list.replaceChildren();
    for (const item of data.items || []) {
      const li = document.createElement("li"); li.append(sourceLink(item.url, item.title || "原始来源"), node("span", ` · ${item.coverage || "阅读范围见资料卡"}${item.reused ? " · 已读材料" : ""}`)); list.append(li);
    }
    const deadlines = data.items.map((item) => item.expires_at * 1000).filter(Number.isFinite);
    if (deadlines.length) expiryTimer = setTimeout(() => { if (active === data) progress(data); }, Math.max(20, Math.min(2147483647, Math.min(...deadlines) - Date.now() + 20)));
    schedule();
  }
  $("chat-search-stop").onclick = async () => {
    if (!active) return;
    const request = { request_id: active.request_id, session_epoch: active.session_epoch };
    $("chat-search-stop").disabled = true;
    try { const data = await post("/api/chat/search/cancel", request); if (active?.request_id === request.request_id) progress(data); }
    catch (e) { $("chat-search-state").textContent = e.message; $("chat-search-stop").disabled = false; }
  };
  async function stop(requestId, epoch) {
    const data = await post("/api/chat/search/cancel", { request_id: requestId, session_epoch: epoch });
    progress(data);
  }
  function observeActivity(activity) {
    const request = activity?.active_request || activity?.request_id;
    if (active?.request_id !== request && request && activity.session_epoch && (activity.active_channel || activity.channel) === "gui") {
      active = { request_id: request, session_epoch: activity.session_epoch, phase: "idle" }; poll();
    }
  }
  async function showMemory(memoryId, epoch = dataset) {
    const generation = ++detailVersion, body = modal.querySelector(".source-memory-body");
    body.replaceChildren(node("p", "正在读取记忆与出处…")); if (!modal.open) modal.showModal();
    try {
      const d = await post("/api/discovery/memory", { memory_id: memoryId, dataset_id: epoch }, "discovery");
      if (generation !== detailVersion || !modal.open) return;
      body.replaceChildren(node("h3", d.memory.title), node("p", d.memory.content, "source-memory-content"));
      if (d.memory.value_tier && d.memory.value_tier !== 'unknown') {
        const tier = {high:'高',normal:'中',low:'低'}[d.memory.value_tier] || '未分级';
        body.append(node('p', `保留优先级：${tier} · ${d.memory.value_note || '暂无价值理由'}`));
      }
      for (const change of d.revisions || []) body.append(node('p', `观点修订：${change.old_id} → ${change.new_id} · ${change.reason}`));
      for (const change of d.condensations || []) body.append(node('p', `已浓缩：${change.reason}`));
      body.append(node("p", d.sources.length ? "带来源见闻 · 普通遗忘规则 · 不等于事实已核实" : "用户表达或对话记忆"));
      for (const source of d.sources) {
        const section = document.createElement("section");
        section.append(sourceLink(source.url, source.title || "原始出处"), node("blockquote", source.evidence || ""), node("p", source.coverage || source.uncertainty || ""), node("small", "读到时间：" + new Date(source.created_at * 1000).toLocaleString("zh-CN") + " · 短证据随记忆保留，清除来源会撤销依赖")); body.append(section);
      }
      for (const dsc of d.discussions || []) body.append(node("p", dsc.user_quote ? `当时你的表达：${dsc.user_quote}` : `关联提问：本机聊天第 ${dsc.turn_id} 条 · ${new Date(dsc.created_at * 1000).toLocaleString("zh-CN")}`));
    } catch (e) { if (generation === detailVersion) body.replaceChildren(node("p", e.message)); }
  }
  async function saveUser(turnId, button) {
    button.disabled = true;
    try {
      const r = await fetch("/api/discovery", { headers: { "X-Alice-Intent": "discovery" }, cache: "no-store" });
      if (!r.ok) throw new Error("未能确认当前会话，请稍后重试");
      const d = await r.json();
      if (dataset && dataset !== d.dataset_id) throw new Error("会话已更换，请刷新历史");
      const result = await post("/api/discovery", { action: "remember_user", turn_id: turnId, dataset_id: d.dataset_id, revision: d.settings.revision }, "discovery");
      adopt(result); button.textContent = "查看记住的表达"; button.disabled = false;
      button.onclick = () => showMemory(result.remembered_memory_id, d.dataset_id);
      window.dispatchEvent(new Event("alice:discussion-saved"));
    } catch (e) { button.textContent = e.message; button.disabled = false; }
  }
  function decorateTurn(target, turn) {
    target.querySelector(".discussion-memory-action")?.remove();
    if (turn.role !== "user" || !turn.discussion || (turn.channel || "gui") !== "gui") return;
    const state = turn.discussion;
    const button = node("button", state.status === "saved" ? "查看记住的表达" : state.status === "clarify" ? "补充我的看法" : "记住我的表达", "link discussion-memory-action");
    button.type = "button";
    button.onclick = () => {
      if (state.status === "saved") showMemory(state.memory_id);
      else if (state.status === "candidate") saveUser(turn.id, button);
      else { $("input").value = "记住：我认为"; $("input").dispatchEvent(new Event("input")); $("input").focus(); }
    };
    target.append(button);
  }
  function reset() { clearTimeout(timer); clearTimeout(expiryTimer); active = null; ++detailVersion; modal.close(); modal.querySelector(".source-memory-body").replaceChildren(); $("chat-search-results").replaceChildren(); $("chat-search-progress").classList.add("hidden"); box.classList.add("hidden"); }
  modal.addEventListener("close", () => { ++detailVersion; modal.querySelector(".source-memory-body").replaceChildren(); });
  window.addEventListener("alice:discovery-state", (event) => adopt(event.detail));
  window.addEventListener("alice:sources-changed", () => { ++detailVersion; modal.close(); clearTimeout(expiryTimer); if (active) active = { ...active, items: [] }; $("chat-search-results").replaceChildren(); refreshSettings(); });
  window.AliceChatSearch = { begin, progress, stop, setBusy, observeActivity, showMemory, decorateTurn, reset };
  refreshSettings();
})();
