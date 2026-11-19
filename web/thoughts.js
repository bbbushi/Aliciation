/* Read-only refresh; paid execution and setting changes require explicit actions. */
"use strict";
(() => {
  const panel = document.getElementById("thought-settings");
  if (!panel) return;
  const nav = document.querySelector(".settings-nav");
  const navButton = document.createElement("button");
  navButton.type = "button"; navButton.dataset.settings = "thoughts";
  navButton.textContent = "思想与探索"; navButton.setAttribute("aria-pressed", "false");
  navButton.setAttribute("aria-controls", "thought-settings");
  navButton.addEventListener("click", () => window.AliceUI?.selectSettings("thoughts"));
  nav.appendChild(navButton);
  panel.innerHTML = `
    <div class="thought-head settings-page-head"><h2 id="thought-title" class="panel-title">思想与探索</h2><button id="thought-refresh" class="link" type="button">刷新</button></div>
    <p id="thought-status" class="settings-result" role="status" aria-live="polite">尚未读取</p>
    <p class="settings-hint settings-page-note">自主探索与主动联系分别授权，服务关闭后不运行。</p>
    <details class="settings-group settings-inline-details" open><summary>自主探索与联系</summary><form id="thought-config">
      <fieldset class="settings-fieldset"><legend>自主探索</legend>
      <label><input name="enabled" type="checkbox">空闲时自主探索</label>
      <label><input name="allow_paid" type="checkbox">允许探索使用已配置模型（产生费用）</label>
      <label><input name="allow_external" type="checkbox">允许探索使用已开放平台的搜索与读取</label>
      <p class="settings-hint">外部读取还需在联网搜索中开放相应平台；本机思考不依赖内容发现总开关。现有后台维护与探索共用运行许可，允许探索使用模型后，共用下面的后台模型额度。</p>
      <details class="settings-help settings-inline-details"><summary>运行额度与时段</summary><div class="thought-fields">
        <label>每日后台模型调用上限<input name="daily_models" type="number" min="0" max="1000" required></label>
        <label>每次探索窗口模型调用<input name="run_models" type="number" min="0" max="20" required></label>
        <label>每次探索窗口工具步骤<input name="run_steps" type="number" min="0" max="100" required></label>
        <label>每次运行时间预算（秒）<input name="run_seconds" type="number" min="0" max="86400" required></label>
        <label>开始探索前空闲（秒）<input name="idle_seconds" type="number" min="0" max="86400" required></label>
        <label>探索间隔（秒）<input name="interval_seconds" type="number" min="0" max="86400" required></label>
        <label>探索开始时间（北京时间）<input name="window_start" type="time" required></label>
        <label>探索结束时间（北京时间）<input name="window_end" type="time" required></label>
      </div></details></fieldset>
      <fieldset class="settings-fieldset"><legend>主动联系</legend>
      <label><input name="proactive_enabled" type="checkbox">允许主动联系</label>
      <label>联系方式<select name="delivery"><option value="next_chat">下次聊天时按需要提起</option><option value="gui">主动写入站内聊天</option></select></label>
      <p class="settings-hint">站内主动消息由服务运行时写入聊天历史；页面打开时自动同步。当前不向 QQ 或关闭页面后的系统通知推送。下次聊天模式由 Alice 结合你当时的话决定是否采用。</p>
      <div class="thought-actions"><label><input name="progress" type="checkbox">进展反馈</label><label><input name="request" type="checkbox">必要询问</label><label><input name="share" type="checkbox">兴趣分享</label></div>
      <details class="settings-help settings-inline-details"><summary>联系次数与时段</summary><div class="thought-fields">
        <label>每日主动联系上限<input name="daily_contacts" type="number" min="0" max="1000" required></label>
        <label>联系冷却（秒）<input name="contact_cooldown" type="number" min="0" max="86400" required></label>
        <label>可联系开始时间（北京时间）<input name="contact_start" type="time" required></label>
        <label>可联系结束时间（北京时间）<input name="contact_end" type="time" required></label>
      </div><p class="settings-hint">起止相同表示全天。时间预算在步骤之间检查，在途模型调用可能需要等待返回。</p></details></fieldset>
      <div class="thought-actions settings-actions"><button id="thought-discard" class="link" type="button">放弃草稿并重新读取</button><button class="btn primary" type="submit">保存设置</button></div>
    </form></details>
    <h3>未解决的疑问</h3><form id="thought-record" class="settings-group"><label for="thought-question">留下一个想继续研究的问题</label><textarea id="thought-question" maxlength="300" required></textarea><div class="settings-actions"><button class="btn ghost" type="submit">收纳疑问</button></div></form>
    <div class="thought-actions"><button id="thought-run" class="btn ghost" type="button">现在探索一轮（调用模型）</button><button id="thought-stop" class="link" type="button">暂停当前探索</button></div>
    <div id="thought-questions"></div><details><summary>已解决与关闭的问题</summary><div id="thought-closed"></div></details>
    <h3>已采纳的思辨</h3><p class="settings-hint">这是当前看法，不表示所有主张已经独立验证。点击查看依据与未知。</p><div id="thought-stances"></div>
    <details><summary>探索记录与实际用量</summary><p id="thought-budget"></p><div id="thought-runs"></div></details>
    <details><summary>主动交流队列</summary><div id="thought-contacts"></div></details>
    <details><summary>事件记录</summary><pre id="thought-events"></pre></details>
    <details id="thought-detail"><summary>所选记录详情</summary><pre id="thought-detail-body"></pre></details>`;
  const el = (id) => document.getElementById(id);
  const labels = {open:"待探索", exploring:"探索中", waiting_user:"等待用户信息", waiting_source:"等待资料", paused:"已暂停", resolved:"已解决", closed:"已关闭", queued:"已排队", running:"运行中", completed:"本次结束", failed:"失败", unknown:"结果未知，不自动重放", cancelled:"已取消", stale:"来源或会话已变化", pending_next_chat:"等待下次聊天", needs_review:"等待 Alice 重新判断", incorporating:"本轮采用中", incorporated:"已作为回应材料采用", delivered:"已写入站内聊天", expired:"已过时"};
  let snapshot = null, visible = false, reading = false, writing = false, dirty = false, draftRevision = null;
  const form = el("thought-config");
  form.addEventListener("input", () => { dirty = true; });
  function status(message, error = false) {
    el("thought-status").textContent = message;
    el("thought-status").classList.toggle("thought-error", error);
  }
  async function request(body) {
    const response = await fetch("/api/thoughts", {method: body ? "POST" : "GET", cache:"no-store",
      headers: {"X-Alice-Intent":"thoughts", ...(body ? {"Content-Type":"application/json"} : {})},
      ...(body ? {body: JSON.stringify(body)} : {})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "操作未能确认，请刷新核对");
    return result;
  }
  async function refresh() {
    if (reading || writing || !visible || document.hidden) return;
    reading = true;
    try { const next = await request(); render(next); }
    catch (error) { status(error.message, true); }
    finally { reading = false; }
  }
  async function act(action, data = {}, detail = false) {
    if (!snapshot || writing) return;
    writing = true;
    status("正在处理…");
    panel.querySelectorAll("button:not(.ui-window-close)").forEach((button) => { button.disabled = true; });
    try {
      const result = await request({action, session_epoch:snapshot.session_epoch, ...data});
      if (detail) {
        el("thought-detail-body").textContent = typeof result.content === "string" ? result.content : JSON.stringify(result, null, 2);
        if (window.AlicePanels) window.AlicePanels.open("thought-detail");
        else el("thought-detail").open = true;
      } else {
        if (action === "settings") dirty = false;
        if (action === "record") el("thought-question").value = "";
        render(result);
        status("操作已记录。运行和送达状态请看下方实际记录。");
      }
    } catch (error) { status(error.message, true); }
    finally { writing = false; panel.querySelectorAll("button:not(.ui-window-close)").forEach((button) => { button.disabled = false; }); }
  }
  function button(label, run) {
    const node = document.createElement("button"); node.type = "button"; node.className = "link";
    node.textContent = label; node.onclick = run; return node;
  }
  function card(title, content = "") {
    const node = document.createElement("article");
    const heading = document.createElement("strong"); heading.textContent = title; node.appendChild(heading);
    if (content) { const p = document.createElement("p"); p.textContent = content; node.appendChild(p); }
    return node;
  }
  function render(next) {
    if (snapshot && snapshot.session_epoch !== next.session_epoch) {
      el("thought-detail-body").textContent = "";
      dirty = false;
      status("会话已切换，已清除旧记录。请核对设置后操作。");
    }
    snapshot = next;
    if (!dirty) {
      draftRevision = next.revision;
      for (const [key, value] of Object.entries(next.settings)) {
        const field = form.elements.namedItem(key);
        if (field) { if (field.type === "checkbox") field.checked = value; else field.value = value; }
      }
    }
    status(`自主探索${next.settings.enabled ? "已开启" : "关闭"} · 主动联系${next.settings.proactive_enabled ? "已开启" : "关闭"}${dirty ? " · 有未保存设置" : ""}`);
    for (const id of ["thought-questions","thought-closed","thought-stances","thought-runs","thought-contacts"]) el(id).replaceChildren();
    for (const q of next.questions) {
      const node = card(q.question, labels[q.status] || q.status);
      node.appendChild(button("详情", () => act("detail", {kind:"question",id:q.id,version:q.version}, true)));
      node.appendChild(button("探索此问题", () => act("run", {id:q.id,version:q.version})));
      node.appendChild(button(["closed","resolved","paused"].includes(q.status) ? "重新打开" : "搁置", () => act("question", {id:q.id,version:q.version,status:["closed","resolved","paused"].includes(q.status) ? "open" : "paused"})));
      if (!["closed","resolved"].includes(q.status)) node.appendChild(button("关闭", () => act("question", {id:q.id,version:q.version,status:"closed"})));
      el(["resolved","closed"].includes(q.status) ? "thought-closed" : "thought-questions").appendChild(node);
    }
    if (!next.questions.some((q) => !["resolved","closed"].includes(q.status))) el("thought-questions").textContent = "暂时没有未解决的疑问。";
    for (const view of next.stances) {
      const node = card(view.title); node.appendChild(button("查看", () => act("detail", {kind:"stance",id:view.id}, true)));
      el("thought-stances").appendChild(node);
    }
    el("thought-budget").textContent = `${next.budget.day} · 已预约后台模型调用 ${next.budget.models} / ${next.settings.daily_models}；结果未知的调用仍计入。`;
    for (const run of next.runs) {
      const node = card(labels[run.status] || run.status, `${run.reason} · 模型 ${run.model_calls} 次 · 工具 ${run.steps} 步`);
      node.appendChild(button("查看记录", () => act("detail", {kind:"run",id:run.id}, true)));
      if (run.status === "paused") node.appendChild(button("继续（调用模型）", () => act("resume", {run_id:run.id})));
      el("thought-runs").appendChild(node);
    }
    for (const contact of next.contacts) {
      const node = card(labels[contact.status] || contact.status, contact.message);
      if (!["delivered","incorporated","cancelled","expired"].includes(contact.status)) node.appendChild(button("撤销待发", () => act("contact_cancel", {id:contact.id,version:contact.version})));
      el("thought-contacts").appendChild(node);
    }
    el("thought-events").textContent = next.events.map((event) => `${new Date(event.created_at*1000).toLocaleString()} ${event.kind} ${event.object_id} ${event.detail}`).join("\n");
  }
  form.onsubmit = (event) => {
    event.preventDefault(); if (!snapshot) return;
    const settings = {};
    for (const field of form.elements) if (field.name) settings[field.name] = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
    act("settings", {revision:draftRevision, settings});
  };
  el("thought-record").onsubmit = (event) => { event.preventDefault(); act("record", {question:el("thought-question").value}); };
  el("thought-run").onclick = () => act("run");
  el("thought-stop").onclick = () => act("pause");
  el("thought-refresh").onclick = refresh;
  el("thought-discard").onclick = () => { dirty = false; refresh(); };
  window.AliceThoughts = {setVisible(value) { visible = value; if (value) refresh(); }};
  const timer = setInterval(refresh, 10000);
  new MutationObserver(() => {
    visible = !panel.classList.contains("hidden") && !document.getElementById("settings-modal").classList.contains("hidden");
    if (visible) refresh();
  }).observe(document.getElementById("settings-modal"), {attributes:true,attributeFilter:["class"]});
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("pagehide", () => clearInterval(timer));
})();
