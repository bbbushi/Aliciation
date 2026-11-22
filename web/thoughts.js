/* Read-only refresh; paid execution and setting changes require explicit actions.
   设置改为「主开关 + 首启分步向导」（2026-11-22 用户裁定）：
   - 开关即 wake_state：开=清醒（空闲时自主活动），关=睡眠待机（仍回应消息与任务）；
   - 开启时先查留档（settings_archived = autonomy_settings 是否落过盘）：
     无留档 → 6 步向导（按旧表单从上往下顺序），完成后一次保存并开启；
     有留档 → 直接按档案开启；「更改设置」重开向导（编辑态，预填当前值）。
   去掉了旧的单页表单、设为清醒/进入睡眠按钮与脏草稿机制。 */
"use strict";
(() => {
  const panel = document.getElementById("thought-settings");
  if (!panel) return;
  const nav = document.querySelector(".settings-nav");
  const navButton = document.createElement("button");
  navButton.type = "button"; navButton.dataset.settings = "thoughts";
  navButton.textContent = "思想与探索"; navButton.setAttribute("aria-pressed", "false");
  navButton.setAttribute("aria-controls", "thought-settings");
  nav.appendChild(navButton);
  panel.innerHTML = `
    <div class="thought-head settings-page-head"><h2 id="thought-title" class="panel-title" tabindex="-1">思想与探索</h2><button id="thought-refresh" class="link" type="button">刷新</button></div>
    <p id="thought-status" class="settings-result" role="status" aria-live="polite">尚未读取</p>
    <div id="thought-master" class="thought-master settings-group">
      <div class="thought-master-row">
        <label class="thought-switch"><input id="thought-enabled" type="checkbox"><span>开启思想与探索</span></label>
        <div class="thought-master-tools">
          <button id="thought-rest" class="btn ghost" type="button" hidden>让她休息一会儿</button>
          <button id="thought-edit" class="link" type="button" hidden>更改设置</button>
        </div>
      </div>
      <span id="thought-lifecycle" role="status" class="thought-lifecycle-line">正在读取状态</span>
    </div>
    <p id="thought-master-hint" class="settings-hint">开启后 Alice 按你的设置在空闲时自主探索、思考、自我迭代或主动联系；关闭时待机，仍回应你的消息和任务。首次开启会先带你把设置走一遍。</p>
    <div id="thought-wizard" class="thought-wizard" hidden>
      <div class="wizard-top">
        <div id="thought-wizard-progress" class="wizard-progress" role="img"></div>
        <button id="thought-wizard-cancel" class="link" type="button">放弃</button>
      </div>
      <h3 id="thought-wizard-title" class="thought-wizard-title" tabindex="-1"></h3>
      <p id="thought-wizard-hint" class="hint"></p>
      <div id="thought-wizard-body" class="thought-wizard-body"></div>
      <p id="thought-wizard-status" class="hint" role="status"></p>
      <div class="wizard-actions">
        <button id="thought-wizard-back" class="btn ghost" type="button" hidden>上一步</button>
        <button id="thought-wizard-skip" class="btn ghost" type="button">跳过</button>
        <button id="thought-wizard-next" class="btn primary" type="button">继续</button>
      </div>
    </div>
    <div id="thought-operational">
      <h3>未解决的疑问</h3><form id="thought-record" class="settings-group"><label for="thought-question">留下一个想继续研究的问题</label><textarea id="thought-question" maxlength="300" required></textarea><div class="settings-actions"><button class="btn ghost" type="submit">收纳疑问</button></div></form>
      <div class="thought-actions"><button id="thought-run" class="btn ghost" type="button">运行一轮自主活动（调用模型）</button><button id="thought-stop" class="link" type="button">暂停当前活动</button></div>
      <div id="thought-questions"></div><details><summary>已解决与关闭的问题</summary><div id="thought-closed"></div></details>
      <h3>已采纳的思辨</h3><p class="settings-hint">这是当前看法，不表示所有主张已经独立验证。点击查看依据与未知。</p><div id="thought-stances"></div>
      <details><summary>最近探索</summary><div id="thought-runs"></div></details>
      <details><summary>邀请与联系</summary><div id="thought-invitations"></div></details>
      <details><summary>旧版主动交流记录</summary><div id="thought-contacts"></div></details>
      <details id="thought-detail"><summary>所选记录详情</summary><pre id="thought-detail-body"></pre></details>
    </div>`;
  const el = (id) => document.getElementById(id);
  const modes = {interest:"兴趣探索", problem:"问题思考", iteration:"自我迭代", contact:"主动交流"};
  const groups = {memory:"记忆读取", history:"历史读取", persona:"人格材料读取", sources:"来源资料读取", search:"外部搜索"};
  const groupsFor = (mode) => mode === "contact" ? ["memory", "history"] : Object.keys(groups);
  const reasons = {value_waiting:"暂无值得推进的新目标，等待新材料", ready_for_test:"等待下一个调度节拍（测试无间隔）", running:"正在执行", queued:"已排队，尚未执行", foreground_priority:"优先处理当前对话或任务", model_permission_required:"等待模型使用许可", daily_model_budget:"今日后台模型额度已用完", run_budget_disabled:"单轮额度为0", requested_sleep:"睡眠待机", outside_wake_window:"清醒时段外，正在待机", waiting_material:"等待值得推进的内容", waiting_idle:"等待空闲", waiting_interval:"等待下一次活动窗口", mode_disabled:"此模式已暂停", no_content:"暂无可推进事项", waiting_new_material:"等待新资料或冷却", actionable_question:"有可推进疑问", new_material_or_curiosity:"可了解新内容", sleeping:"睡眠待机", model_call_budget:"本窗口模型额度已用完", run_time_budget:"本窗口时长已用完", settings_changed:"设置变化，已暂停", idle_rest:"发呆中（自主休息，不调用模型）", no_actionable_work:"无可推进事项", same_material_window:"同依据推演窗口已用尽", voluntary_rest:"可主动休息", idle_disabled:"发呆未启用", idle_rest_run:"发呆休息"};
  const labels = {open:"待探索", exploring:"探索中", waiting_user:"等待用户信息", waiting_source:"等待资料", waiting_permission:"等待权限或资格", waiting_budget:"等待额度", paused:"已暂停", resolved:"已解决", closed:"已关闭", queued:"已排队", running:"运行中", completed:"本次结束", failed:"失败", unknown:"结果未知，不自动重放", cancelled:"已取消", stale:"来源或会话已变化", pending_next_chat:"等待下次聊天", needs_review:"等待 Alice 重新判断", incorporating:"本轮采用中", incorporated:"已作为回应材料采用", delivered:"已写入站内聊天", expired:"已过时", migrated:"已迁移为邀请意图", prepared:"已准备，待投递", awaiting_reply:"等待你的回应", accepted:"已接受，本轮展开", expanded:"已展开", deferred:"已延期，保留意图", declined:"已拒绝该话题", withdrawn:"已撤销", idle:"发呆"};
  let snapshot = null, visible = false, reading = false, writing = false, wizard = null;
  const communicationChoices = {invite:"现在邀请", later:"留待以后", none:"无需联系", unknown:"未完成判断"};
  const communicationOutcomes = {no_contact:"本窗口不发起联系", intent_recorded:"意图已保留，待后续重新判断", invitation_prepared:"已准备简短邀请", delivery_blocked:"意图已保留，当前投递受阻", not_evaluated:"没有收到交流决定"};
  const contactBlockers = {waiting_user_availability:"等待用户恢复方便，先做其他事", topic_waiting_user:"本话题等待相关回应或约定", master_disabled:"总开关关闭", not_awake:"当前待机", contact_permission_disabled:"未开放主动邀请", resting:"发呆休息中", awaiting_reply:"已有邀请等待回应", source_changed:"内容或来源已变化", outside_contact_window:"联系时段外", motive_disabled:"此类邀请未开放", retry_not_due:"尚未到再邀时间", contact_capacity:"联系次数或冷却限制"};
  const blockerText = (items) => (items || []).map((key) => contactBlockers[key] || key).join("、");
  Object.assign(reasons, {contact_evaluated:"已完成交流判断", contact_context_reviewed:"已判断本批材料适不适合主动交流",
    contact_attempt_limit:"本批材料已尝试，未形成判断。等待新材料或约定时间", model_finished:"模型已结束，未提交结束回执"});
  Object.assign(contactBlockers, {contact_delivery_mode:"需要选择站内主动消息", mode_disabled:"主动交流模式已暂停"});
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
        if (action === "record") el("thought-question").value = "";
        render(result);
        if (action === "settings") {
          status(result.settings.wake_state === "awake"
            ? "已按上次的设置开启。要调整可点「更改设置」。"
            : "已关闭：Alice 进入待机，仍回应你的消息和任务。");
        } else status("操作已记录。运行和送达状态请看下方实际记录。");
      }
    } catch (error) { status(error.message, true); renderSwitch(); }
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
  function renderSwitch() {
    if (!snapshot) return;
    el("thought-enabled").checked = snapshot.settings.wake_state === "awake";
    el("thought-edit").hidden = !snapshot.settings_archived;
  }
  function render(next) {
    if (snapshot && snapshot.session_epoch === next.session_epoch && next.revision < snapshot.revision) return;
    if (snapshot && snapshot.session_epoch !== next.session_epoch) {
      el("thought-detail-body").textContent = "";
      status("会话已切换，已清除旧记录。请核对设置后操作。");
    }
    snapshot = next;
    renderSwitch();
    const awake = next.lifecycle?.effective === "awake";
    const resting = Boolean(next.scheduler?.idle?.active);
    const idleLeft = next.scheduler?.idle?.until_at ? `，至 ${new Date(next.scheduler.idle.until_at*1000).toLocaleTimeString()}` : "";
    el("thought-lifecycle").textContent = `${awake ? "清醒" : "睡眠"}${resting ? " · 发呆中" : ""} · ${reasons[next.scheduler?.status] || "待机"}${resting ? idleLeft : ""}${!awake && next.lifecycle?.requested === "awake" ? "（已设清醒，当前时段外）" : ""}`;
    el("thought-lifecycle").dataset.state = awake ? "awake" : "sleeping";
    const restButton = el("thought-rest");
    restButton.hidden = !resting && next.settings.idle_rest_seconds === 0;
    restButton.textContent = resting ? "结束发呆，重新判断" : "让她休息一会儿";
    for (const id of ["thought-questions","thought-closed","thought-stances","thought-runs","thought-contacts"]) el(id).replaceChildren();
    for (const q of next.questions) {
      const node = card(q.question, q.value_waiting ? "本次已收住，等待新价值或明确重开" : labels[q.status] || q.status);
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
    for (const run of next.runs.filter((item) => item.activity?.activity !== "idle").slice(0,6)) {
      const plan = run.activity_plan?.detail_state === "loaded" ? run.activity_plan : null;
      const item = card(`${modes[run.activity?.activity] || "探索"} · ${labels[run.status] || run.status}`,
        plan?.result?.change || plan?.object || reasons[run.reason] || contactBlockers[run.reason] || "");
      if (run.status === "paused") item.appendChild(button("继续", () => act("resume", {run_id:run.id})));
      el("thought-runs").appendChild(item);
    }
    const invitationList = el("thought-invitations");
    invitationList.replaceChildren();
    for (const wait of next.contact_waits || []) {
      if (wait.intent_id) continue;
      invitationList.appendChild(card("等待你方便时再联系", wait.resume_at
        ? `已约定 ${new Date(wait.resume_at*1000).toLocaleString()} 后重新判断；当前先做其他事。`
        : "未回应或暂时忙碌，暂停主动邀请；恢复方便时可在聊天中告诉 Alice。"));
    }
    for (const attempt of next.invitations || []) {
      const waitingUntil = attempt.state === "awaiting_reply" && attempt.expires_at
        ? `（等待至 ${new Date(attempt.expires_at*1000).toLocaleTimeString()}）` : "";
      const reason = {source_changed:"话题材料已失效", reply_not_delivered:"未确认本轮展开回复",
        expansion_binding_changed:"展开所依据的邀请已变化"}[attempt.deferred_reason];
      const node = card(`${labels[attempt.state] || attempt.state} · ${attempt.topic || "（无主题）"}`,
        [attempt.invitation_text + waitingUntil, reason].filter(Boolean).join(" · "));
      if (attempt.state === "prepared") {
        const note = document.createElement("p");
        note.textContent = attempt.blockers?.length ? `当前未投递：${blockerText(attempt.blockers)}` : "等待宿主下一个投递节拍";
        node.appendChild(note);
      }
      if (attempt.state === "awaiting_reply") {
        node.appendChild(button("稍后再聊", () => act("contact_reply", {id:attempt.id, version:attempt.version, decision:"defer"})));
        node.appendChild(button("不聊这个", () => act("contact_reply", {id:attempt.id, version:attempt.version, decision:"decline"})));
      }
      invitationList.appendChild(node);
    }
    for (const intent of next.intents || []) {
      const node = card(`${labels[intent.state] || intent.state} · ${intent.topic}`, intent.reason);
      if (intent.state === "active") {
        const note = document.createElement("p");
        note.textContent = `保留的交流意图；${intent.blockers?.length ? `当前条件：${blockerText(intent.blockers)}` : "当前投递条件允许，仍以具体邀请记录为准"}`;
        node.append(note, button("结束该意图", () => act("intent_cancel", {id:intent.id, version:intent.version})));
      }
      invitationList.appendChild(node);
    }
    if (!(next.invitations || []).length && !(next.intents || []).length && !(next.contact_waits || []).length) invitationList.textContent = "暂无邀请与联系意图。";
    for (const contact of next.contacts) {
      const node = card(labels[contact.status] || contact.status, contact.message);
      if (!["delivered","incorporated","cancelled","expired"].includes(contact.status)) node.appendChild(button("撤销待发", () => act("contact_cancel", {id:contact.id,version:contact.version})));
      el("thought-contacts").appendChild(node);
    }
  }
  /* ---------- 分步设置向导 ---------- */
  const WIZARD_STEPS = [
    {name:"探索权限", title:"探索可以用什么？", hint:"不允许模型调用时，探索无法真正运行。",
     render(host) {
       host.innerHTML = `
       <label><input name="allow_paid" type="checkbox">允许探索使用已配置模型（产生费用）</label>
       <label><input name="allow_external" type="checkbox">允许探索使用已开放平台的搜索与读取</label>
       <p class="settings-hint">外部读取还需在联网搜索中开放相应平台；本机思考不依赖内容发现总开关。现有后台维护与探索共用运行许可，允许探索使用模型后，共用同一份后台模型额度。</p>`;
     }},
    {name:"运行额度与时段", title:"探索多勤、一次跑多久？", hint:"时段起止相同表示全天。时间预算在步骤之间检查，在途模型调用可能需要等待返回。",
     render(host) {
       host.innerHTML = `<div class="thought-fields">
       <label>每日后台模型调用上限<input name="daily_models" type="number" min="0" max="1000" required></label>
       <label>每次探索窗口模型调用<input name="run_models" type="number" min="0" max="20" required></label>
       <label>每次探索窗口工具步骤<input name="run_steps" type="number" min="0" max="100" required></label>
       <label>每次运行时间预算（秒）<input name="run_seconds" type="number" min="0" max="86400" required></label>
       <label>开始探索前空闲（秒）<input name="idle_seconds" type="number" min="0" max="86400" required></label>
       <label>探索间隔（秒）<input name="interval_seconds" type="number" min="0" max="86400" required></label>
       <label>清醒开始时间（北京时间）<input name="window_start" type="time" required></label>
       <label>清醒结束时间（北京时间）<input name="window_end" type="time" required></label>
       </div>`;
     }},
    {name:"活动时长与工具权限", title:"每类活动允许做多久、读什么？", hint:"时长为单段上限，0暂停该模式；实际还受单轮时间与共享额度限制。模式倾向由内容和执行历史形成。",
     render(host) {
       host.innerHTML = `<div id="thought-wizard-activities"></div>`;
       const host0 = el("thought-wizard-activities");
       for (const [mode, title] of Object.entries(modes)) {
         const fieldset = document.createElement("fieldset");
         const legend = document.createElement("legend"); legend.textContent = title; fieldset.appendChild(legend);
         const time = document.createElement("label"); time.textContent = "单段最长时间（秒）";
         const seconds = document.createElement("input"); seconds.name = `activity-${mode}-seconds`; seconds.type = "number"; seconds.min = "0"; seconds.max = "86400"; seconds.required = true;
         time.appendChild(seconds); fieldset.appendChild(time);
         const tools = document.createElement("div"); tools.className = "thought-actions";
         for (const group of groupsFor(mode)) {
           const wrapper = document.createElement("label"), input = document.createElement("input");
           input.type = "checkbox"; input.name = `activity-${mode}-${group}`; wrapper.append(input, document.createTextNode(groups[group])); tools.appendChild(wrapper);
         }
         fieldset.appendChild(tools); host0.appendChild(fieldset);
       }
     }},
    {name:"主动联系", title:"要主动来找你吗？", hint:"主动交流模式需要允许主动联系并选择站内聊天。Alice 先问你是否方便，你接受后再聊具体话题。",
     render(host) {
       host.innerHTML = `
       <label><input name="proactive_enabled" type="checkbox">允许主动联系</label>
       <label>联系方式<select name="delivery"><option value="next_chat">下次聊天时按需要提起</option><option value="gui">主动写入站内聊天</option></select></label>
       <p class="settings-hint">站内主动消息由服务运行时写入聊天历史；页面打开时自动同步。当前不向 QQ 或关闭页面后的系统通知推送。下次聊天模式由 Alice 结合你当时的话决定是否采用。</p>
       <div class="thought-actions"><label><input name="progress" type="checkbox">进展反馈</label><label><input name="request" type="checkbox">必要询问</label><label><input name="share" type="checkbox">兴趣分享</label></div>`;
     }},
    {name:"联系次数与时段", title:"多久可以来找你一次？", hint:"主动联系先发邀请，用户接受后才展开正文。未回复或忙碌时持续等待，相关回应或约定时间才重新判断。",
     render(host) {
       host.innerHTML = `<div class="thought-fields">
       <label>每日主动联系上限<input name="daily_contacts" type="number" min="0" max="1000" required></label>
       <label>联系冷却（秒）<input name="contact_cooldown" type="number" min="0" max="86400" required></label>
       <label>邀请等待（秒）<input name="invitation_wait_seconds" type="number" min="60" max="86400" required></label>
       <label>同主题再邀间隔（秒）<input name="same_topic_retry_seconds" type="number" min="0" max="86400" required></label>
       <label>可联系开始时间（北京时间）<input name="contact_start" type="time" required></label>
       <label>可联系结束时间（北京时间）<input name="contact_end" type="time" required></label>
       </div>`;
     }},
    {name:"休息与推演", title:"累了怎么歇、题目怎么反复推敲？", hint:"发呆期间不调用模型也不投递邀请。",
     render(host) {
       host.innerHTML = `<div class="thought-fields">
       <label>每次发呆时长（秒，0 停用）<input name="idle_rest_seconds" type="number" min="0" max="86400" required></label>
       <label>同依据推演窗口数<input name="same_material_windows" type="number" min="1" max="20" required></label>
       </div>`;
     }},
  ];
  function startWizard(mode) {
    if (!snapshot) return;
    wizard = {mode, step:0, draft:{
      ...snapshot.settings,
      activities:Object.fromEntries(Object.entries(snapshot.settings.activities || {}).map(([mode, value]) => [mode, {seconds:value.seconds, tools:[...value.tools]}])),
    }};
    delete wizard.draft.wake_state;      // 开关由主开关/最终保存决定，不进向导
    delete wizard.draft.enabled;         // 派生影子字段，不回传
    delete wizard.draft.test_controls_only;   // 快照派生键（不在 DEFAULTS），回传会被字段校验拒绝
    applyWizardVisibility();
    renderWizard();
  }
  function applyWizardVisibility() {
    const active = Boolean(wizard);
    el("thought-wizard").hidden = !active;
    el("thought-operational").hidden = active;
    el("thought-master").hidden = active;
    el("thought-master-hint").hidden = active;
  }
  function wizardStatus(message, error = false) {
    el("thought-wizard-status").textContent = message;
    el("thought-wizard-status").classList.toggle("thought-error", error);
  }
  function fillStep(host) {
    for (const [key, value] of Object.entries(wizard.draft)) {
      const field = host.querySelector(`[name="${key}"]`);
      if (field) { if (field.type === "checkbox") field.checked = Boolean(value); else field.value = value; }
    }
    for (const [mode, value] of Object.entries(wizard.draft.activities || {})) {
      const seconds = host.querySelector(`[name="activity-${mode}-seconds"]`);
      if (seconds) seconds.value = value.seconds;
      for (const group of groupsFor(mode)) {
        const box = host.querySelector(`[name="activity-${mode}-${group}"]`);
        if (box) box.checked = value.tools.includes(group);
      }
    }
  }
  function collectStep(host) {
    for (const field of host.querySelectorAll("[name]")) {
      if (field.name.startsWith("activity-")) continue;
      wizard.draft[field.name] = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
    }
    if (host.querySelector("[name^='activity-']")) {
      wizard.draft.activities = Object.fromEntries(Object.keys(modes).map((mode) => [mode, {
        seconds: Number(host.querySelector(`[name="activity-${mode}-seconds"]`).value),
        tools: groupsFor(mode).filter((group) => host.querySelector(`[name="activity-${mode}-${group}"]`).checked),
      }]));
    }
  }
  function stepValid(host) {
    for (const field of host.querySelectorAll("input, select")) {
      if (!field.checkValidity()) { field.reportValidity(); return false; }
    }
    return true;
  }
  function setWizardBusy(on) {
    for (const button of el("thought-wizard").querySelectorAll("button")) button.disabled = on;
  }
  function renderWizard() {
    const step = WIZARD_STEPS[wizard.step];
    window.AliceUI?.wizardProgress(el("thought-wizard-progress"), WIZARD_STEPS.map((item) => item.name), wizard.step + 1);
    el("thought-wizard-title").textContent = step.title;
    el("thought-wizard-hint").textContent = step.hint;
    const host = el("thought-wizard-body");
    host.innerHTML = "";
    step.render(host);
    fillStep(host);
    el("thought-wizard-back").hidden = wizard.step === 0;
    el("thought-wizard-skip").hidden = wizard.step === WIZARD_STEPS.length - 1;
    el("thought-wizard-next").textContent = wizard.step === WIZARD_STEPS.length - 1
      ? (wizard.mode === "fresh" ? "保存并开启" : "保存设置") : "继续";
    wizardStatus("");
    el("thought-wizard-title").focus();
  }
  async function saveWizard() {
    if (!snapshot || writing) return;
    writing = true;
    setWizardBusy(true);
    const mode = wizard.mode;
    wizardStatus(mode === "fresh" ? "正在保存设置并开启…" : "正在保存设置…");
    try {
      const settings = {...wizard.draft, wake_state:"awake"};
      const result = await request({action:"settings", session_epoch:snapshot.session_epoch, revision:snapshot.revision, settings});
      wizard = null;
      applyWizardVisibility();
      render(result);
      status(mode === "fresh" ? "已开启，按你的设置运行。要调整可点「更改设置」。" : "设置已保存。");
    } catch (error) {
      wizardStatus(`${error.message}。可点「放弃」重新读取，再按新设置更改。`, true);
    } finally { writing = false; setWizardBusy(false); }
  }
  el("thought-wizard-next").onclick = () => {
    if (!wizard || writing) return;
    const host = el("thought-wizard-body");
    if (!stepValid(host)) return;
    collectStep(host);
    if (wizard.step === WIZARD_STEPS.length - 1) saveWizard();
    else { wizard.step += 1; renderWizard(); }
  };
  el("thought-wizard-back").onclick = () => {
    if (!wizard || writing || wizard.step === 0) return;
    wizard.step -= 1; renderWizard();     // 返回不收集：草稿保持最近一次「继续」时的值
  };
  el("thought-wizard-skip").onclick = () => {
    if (!wizard || writing) return;
    if (wizard.step === WIZARD_STEPS.length - 1) saveWizard();
    else { wizard.step += 1; renderWizard(); }   // 跳过 = 该步保留草稿当前值
  };
  el("thought-wizard-cancel").onclick = () => {
    wizard = null;
    applyWizardVisibility();
    renderSwitch();
    refresh();
    status("已放弃本次设置修改。");
  };
  el("thought-enabled").addEventListener("change", () => {
    if (!snapshot || writing || wizard) { renderSwitch(); return; }
    if (el("thought-enabled").checked) {
      if (!snapshot.settings_archived) { startWizard("fresh"); return; }
      act("settings", {revision:snapshot.revision, settings:{wake_state:"awake"}});
    } else act("settings", {revision:snapshot.revision, settings:{wake_state:"sleeping"}});
  });
  el("thought-edit").onclick = () => { if (!writing) startWizard("editing"); };
  el("thought-record").onsubmit = (event) => { event.preventDefault(); act("record", {question:el("thought-question").value}); };
  el("thought-run").onclick = () => act("run");
  el("thought-rest").onclick = () => act(snapshot?.scheduler?.idle?.active ? "idle_wake" : "idle_rest");
  el("thought-stop").onclick = () => act("pause");
  el("thought-refresh").onclick = refresh;
  const timer = setInterval(refresh, 10000);
  const modal = el("settings-modal");
  function syncVisibility() {
    const modalHidden = modal.classList.contains("hidden");
    const next = !modalHidden && !panel.classList.contains("hidden");
    if (next === visible) return;
    visible = next;
    if (visible) refresh();
  }
  const visibilityObserver = new MutationObserver(syncVisibility);
  visibilityObserver.observe(modal, {attributes:true,attributeFilter:["class"]});
  visibilityObserver.observe(panel, {attributes:true,attributeFilter:["class"]});
  syncVisibility();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("pagehide", () => clearInterval(timer));
})();
