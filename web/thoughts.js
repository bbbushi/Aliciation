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
    <div class="thought-actions"><span id="thought-lifecycle" role="status">正在读取状态</span><button id="thought-wake" class="btn ghost" type="button">切换状态</button></div>
    <p class="settings-hint">清醒时选择兴趣探索、问题思考或自我迭代；睡眠时待机，仍回应你的消息和任务。服务关闭后不运行。</p>
    <details class="settings-group settings-inline-details" open><summary>探索与联系设置</summary><form id="thought-config">
      <fieldset class="settings-fieldset"><legend>自主探索</legend>
      <label>运行状态<select name="wake_state"><option value="sleeping">睡眠 · 待机</option><option value="awake">清醒 · 空闲时自主活动</option></select></label>
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
        <label>清醒开始时间（北京时间）<input name="window_start" type="time" required></label>
        <label>清醒结束时间（北京时间）<input name="window_end" type="time" required></label>
      </div></details>
      <details class="settings-help settings-inline-details"><summary>三种活动的时长与工具权限</summary><p class="settings-hint">时长为单段上限，0暂停该模式；实际还受单轮时间与共享额度限制。模式倾向由内容和执行历史形成。</p><div id="thought-activities"></div></details>
      </fieldset>
      <fieldset class="settings-fieldset"><legend>主动联系</legend>
      <label><input name="proactive_enabled" type="checkbox">允许主动联系</label>
      <label>联系方式<select name="delivery"><option value="next_chat">下次聊天时按需要提起</option><option value="gui">主动写入站内聊天</option></select></label>
      <p class="settings-hint">站内主动消息由服务运行时写入聊天历史；页面打开时自动同步。当前不向 QQ 或关闭页面后的系统通知推送。下次聊天模式由 Alice 结合你当时的话决定是否采用。</p>
      <div class="thought-actions"><label><input name="progress" type="checkbox">进展反馈</label><label><input name="request" type="checkbox">必要询问</label><label><input name="share" type="checkbox">兴趣分享</label></div>
      <details class="settings-help settings-inline-details"><summary>联系次数与时段</summary><div class="thought-fields">
        <label>每日主动联系上限<input name="daily_contacts" type="number" min="0" max="1000" required></label>
        <label>联系冷却（秒）<input name="contact_cooldown" type="number" min="0" max="86400" required></label>
        <label>邀请等待（秒）<input name="invitation_wait_seconds" type="number" min="60" max="86400" required></label>
        <label>同主题再邀间隔（秒）<input name="same_topic_retry_seconds" type="number" min="0" max="86400" required></label>
        <label>可联系开始时间（北京时间）<input name="contact_start" type="time" required></label>
        <label>可联系结束时间（北京时间）<input name="contact_end" type="time" required></label>
      </div></details>
      <p class="settings-hint">主动联系先发邀请，用户接受后才展开正文；未回复时静默结束。</p>
      </fieldset>
      <fieldset class="settings-fieldset"><legend>休息与推演</legend>
      <div class="thought-fields">
        <label>发呆单段时长（秒，0停用主动发呆）<input name="idle_rest_seconds" type="number" min="0" max="86400" required></label>
        <label>同依据推演窗口数<input name="same_material_windows" type="number" min="1" max="20" required></label>
      </div><p class="settings-hint">发呆期间不调用模型也不投递邀请。时段起止相同表示全天。时间预算在步骤之间检查，在途模型调用可能需要等待返回。</p></fieldset>
      <div class="thought-actions settings-actions"><button id="thought-discard" class="link" type="button">放弃设置草稿并重新读取</button><button class="btn primary" type="submit">保存设置</button></div>
    </form></details>
    <h3>未解决的疑问</h3><form id="thought-record" class="settings-group"><label for="thought-question">留下一个想继续研究的问题</label><textarea id="thought-question" maxlength="300" required></textarea><div class="settings-actions"><button class="btn ghost" type="submit">收纳疑问</button></div></form>
    <div class="thought-actions"><button id="thought-run" class="btn ghost" type="button">现在探索一轮（调用模型）</button><button id="thought-rest" class="link" type="button">让她休息一会儿</button><button id="thought-stop" class="link" type="button">暂停当前探索</button></div>
    <div id="thought-questions"></div><details><summary>已解决与关闭的问题</summary><div id="thought-closed"></div></details>
    <h3>已采纳的思辨</h3><p class="settings-hint">这是当前看法，不表示所有主张已经独立验证。点击查看依据与未知。</p><div id="thought-stances"></div>
    <details><summary>探索记录与实际用量</summary><p id="thought-budget"></p><div id="thought-runs"></div></details>
    <details><summary>调度与权重详情</summary><pre id="thought-scheduler"></pre></details>
    <details><summary>邀请与联系意图</summary><div id="thought-invitations"></div><div class="thought-actions"><button id="thought-migration-prepare" class="link" type="button">准备旧联系迁移</button><button id="thought-migration-apply" class="link" type="button">执行迁移</button></div><p class="settings-hint" id="thought-migration-note"></p></details>
    <details><summary>交流决策记录</summary><div id="thought-communication"></div></details>
    <details><summary>旧版主动交流记录</summary><div id="thought-contacts"></div></details>
    <details><summary>事件记录</summary><pre id="thought-events"></pre></details>
    <details id="thought-detail"><summary>所选记录详情</summary><pre id="thought-detail-body"></pre></details>`;
  const el = (id) => document.getElementById(id);
  const modes = {interest:"兴趣探索", problem:"问题思考", iteration:"自我迭代"};
  const groups = {memory:"记忆读取", history:"历史读取", persona:"人格材料读取", sources:"来源资料读取", search:"外部搜索"};
  for (const [mode, title] of Object.entries(modes)) {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend"); legend.textContent = title; fieldset.appendChild(legend);
    const time = document.createElement("label"); time.textContent = "单段最长时间（秒）";
    const seconds = document.createElement("input"); seconds.name = `activity-${mode}-seconds`; seconds.type = "number"; seconds.min = "0"; seconds.max = "86400"; seconds.required = true;
    time.appendChild(seconds); fieldset.appendChild(time);
    const tools = document.createElement("div"); tools.className = "thought-actions";
    for (const [group, label] of Object.entries(groups)) {
      const wrapper = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.name = `activity-${mode}-${group}`; wrapper.append(input, document.createTextNode(label)); tools.appendChild(wrapper);
    }
    fieldset.appendChild(tools); el("thought-activities").appendChild(fieldset);
  }
  const reasons = {ready_for_test:"等待下一个调度节拍（测试无间隔）", running:"正在执行", queued:"已排队，尚未执行", foreground_priority:"优先处理当前对话或任务", model_permission_required:"等待模型使用许可", daily_model_budget:"今日后台模型额度已用完", run_budget_disabled:"单轮额度为0", requested_sleep:"睡眠待机", outside_wake_window:"清醒时段外，正在待机", waiting_material:"等待值得推进的内容", waiting_idle:"等待空闲", waiting_interval:"等待下一次活动窗口", mode_disabled:"此模式已暂停", no_content:"暂无可推进事项", waiting_new_material:"等待新资料或冷却", actionable_question:"有可推进疑问", new_material_or_curiosity:"可了解新内容", sleeping:"睡眠待机", model_call_budget:"本窗口模型额度已用完", run_time_budget:"本窗口时长已用完", settings_changed:"设置变化，已暂停", idle_rest:"发呆中（自主休息，不调用模型）", no_actionable_work:"无可推进事项", same_material_window:"同依据推演窗口已用尽", voluntary_rest:"可主动休息", idle_disabled:"发呆未启用", idle_rest_run:"发呆休息"};
  const labels = {open:"待探索", exploring:"探索中", waiting_user:"等待用户信息", waiting_source:"等待资料", waiting_permission:"等待权限或资格", waiting_budget:"等待额度", paused:"已暂停", resolved:"已解决", closed:"已关闭", queued:"已排队", running:"运行中", completed:"本次结束", failed:"失败", unknown:"结果未知，不自动重放", cancelled:"已取消", stale:"来源或会话已变化", pending_next_chat:"等待下次聊天", needs_review:"等待 Alice 重新判断", incorporating:"本轮采用中", incorporated:"已作为回应材料采用", delivered:"已写入站内聊天", expired:"已过时", migrated:"已迁移为邀请意图", prepared:"已准备，待投递", awaiting_reply:"等待你的回应", accepted:"已接受，本轮展开", expanded:"已展开", deferred:"已延期，保留意图", declined:"已拒绝该话题", withdrawn:"已撤销", idle:"发呆"};
  let snapshot = null, visible = false, reading = false, writing = false, dirty = false, draftRevision = null;
  const communicationChoices = {invite:"现在邀请", later:"留待以后", none:"无需联系", unknown:"未完成判断"};
  const communicationOutcomes = {no_contact:"本窗口不发起联系", intent_recorded:"意图已保留，待后续重新判断", invitation_prepared:"已准备简短邀请", delivery_blocked:"意图已保留，当前投递受阻", not_evaluated:"没有收到交流决定"};
  const contactBlockers = {master_disabled:"总开关关闭", not_awake:"当前待机", contact_permission_disabled:"未开放主动邀请", resting:"发呆休息中", awaiting_reply:"已有邀请等待回应", source_changed:"内容或来源已变化", outside_contact_window:"联系时段外", motive_disabled:"此类邀请未开放", retry_not_due:"尚未到再邀时间", contact_capacity:"联系次数或冷却限制"};
  const blockerText = (items) => (items || []).map((key) => contactBlockers[key] || key).join("、");
  const form = el("thought-config");
  const normalFormHTML = form.innerHTML;
  function configureTestControls(testing) {
    if (testing === (form.dataset.testControls === "true")) return;
    form.dataset.testControls = String(testing);
    if (!testing) { form.innerHTML = normalFormHTML; return; }
    form.innerHTML = `<p class="settings-hint">后台测试：只执行总开关与工具权限。额度、时段、运行时长和活动间隔不参与；调用仍实际计费并记录。等待用户与自主休息仍可发生。</p><div id="thought-test-tools"></div><label><input name="proactive_enabled" type="checkbox">允许主动发起邀请</label><p class="settings-hint">外部搜索还须在联网搜索页面开放相应平台。原正常配置保留，可在结束测试策略后恢复。</p><div class="thought-actions"><button class="btn primary" type="submit">保存工具权限</button><button id="thought-discard" class="link" type="button">放弃草稿并重新读取</button></div>`;
    for (const [mode, title] of Object.entries(modes)) {
      const section = document.createElement("fieldset"), legend = document.createElement("legend");
      legend.textContent = title; section.appendChild(legend);
      const tools = document.createElement("div"); tools.className = "thought-actions";
      for (const [group, label] of Object.entries(groups)) {
        const wrapper = document.createElement("label"), input = document.createElement("input");
        input.type = "checkbox"; input.name = `activity-${mode}-${group}`;
        wrapper.append(input, document.createTextNode(label)); tools.appendChild(wrapper);
      }
      section.appendChild(tools); el("thought-test-tools").appendChild(section);
    }
    el("thought-discard").onclick = () => { dirty = false; refresh(); };
  }
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
  async function act(action, data = {}, detail = false, preserveDraft = false) {
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
        if (action === "settings" && !preserveDraft) dirty = false;
        if (preserveDraft) {
          draftRevision = result.revision;
          const wakeField = form.elements.namedItem("wake_state");
          if (wakeField) wakeField.value = result.settings.wake_state;
        }
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
    if (snapshot && snapshot.session_epoch === next.session_epoch && next.revision < snapshot.revision) return;
    if (snapshot && snapshot.session_epoch !== next.session_epoch) {
      el("thought-detail-body").textContent = "";
      dirty = false;
      status("会话已切换，已清除旧记录。请核对设置后操作。");
    }
    snapshot = next;
    configureTestControls(Boolean(next.settings.test_controls_only));
    for (const id of ["thought-record", "thought-run", "thought-rest", "thought-stop", "thought-migration-prepare", "thought-migration-apply"])
      el(id).hidden = Boolean(next.settings.test_controls_only);
    if (!dirty) {
      draftRevision = next.revision;
      for (const [key, value] of Object.entries(next.settings)) {
        const field = form.elements.namedItem(key);
        if (field) { if (field.type === "checkbox") field.checked = value; else field.value = value; }
      }
      for (const [mode, value] of Object.entries(next.settings.activities || {})) {
        const secondsField = form.elements.namedItem(`activity-${mode}-seconds`);
        if (secondsField) secondsField.value = value.seconds;
        for (const group of Object.keys(groups)) form.elements.namedItem(`activity-${mode}-${group}`).checked = value.tools.includes(group);
      }
    }
    const awake = next.lifecycle?.effective === "awake";
    const resting = next.scheduler?.status === "idle_rest";
    const idleLeft = next.scheduler?.idle?.until_at ? `，至 ${new Date(next.scheduler.idle.until_at*1000).toLocaleTimeString()}` : "";
    el("thought-lifecycle").textContent = `${awake ? "清醒" : "睡眠"}${resting ? " · 发呆中" : ""} · ${reasons[next.scheduler?.status] || "待机"}${resting ? idleLeft : ""}${!next.settings.test_controls_only && !awake && next.lifecycle?.requested === "awake" ? "（已设清醒，当前时段外）" : ""}`;
    el("thought-lifecycle").dataset.state = awake ? "awake" : "sleeping";
    el("thought-wake").textContent = next.settings.test_controls_only ? (awake ? "关闭后台测试" : "开启后台测试（调用模型）") : next.settings.wake_state === "awake" ? "进入睡眠" : "设为清醒";
    status(`${next.settings.test_controls_only ? "测试策略 · 不限制额度/时段/间隔 · " : ""}主动联系${next.settings.proactive_enabled ? "已允许（先邀请，接受后展开）" : "关闭"}${dirty ? " · 有未保存设置" : ""}`);
    el("thought-scheduler").textContent = Object.entries(next.scheduler?.modes || {}).map(([mode, info]) => `${modes[mode] || labels[mode] || mode}：权重 ${info.weight} · 内容 ${info.content_score} · 轮换补偿 ${info.rotation}\n${reasons[info.reason] || info.reason}${info.question_id ? ` · 疑问 #${info.question_id}` : ""}`).join("\n\n")
      + (next.scheduler?.idle?.active ? `\n\n发呆：进行中，原因 ${next.scheduler.idle.reason || "voluntary_rest"}` : "");
    el("thought-rest").textContent = resting ? "结束发呆，重新判断" : "让她休息一会儿";
    for (const id of ["thought-questions","thought-closed","thought-stances","thought-runs","thought-contacts"]) el(id).replaceChildren();
    for (const q of next.questions) {
      const node = card(q.question, labels[q.status] || q.status);
      node.appendChild(button("详情", () => act("detail", {kind:"question",id:q.id,version:q.version}, true)));
      if (!next.settings.test_controls_only) node.appendChild(button("探索此问题", () => act("run", {id:q.id,version:q.version})));
      if (!next.settings.test_controls_only) node.appendChild(button(["closed","resolved","paused"].includes(q.status) ? "重新打开" : "搁置", () => act("question", {id:q.id,version:q.version,status:["closed","resolved","paused"].includes(q.status) ? "open" : "paused"})));
      if (!next.settings.test_controls_only && !["closed","resolved"].includes(q.status)) node.appendChild(button("关闭", () => act("question", {id:q.id,version:q.version,status:"closed"})));
      el(["resolved","closed"].includes(q.status) ? "thought-closed" : "thought-questions").appendChild(node);
    }
    if (!next.questions.some((q) => !["resolved","closed"].includes(q.status))) el("thought-questions").textContent = "暂时没有未解决的疑问。";
    for (const view of next.stances) {
      const node = card(view.title); node.appendChild(button("查看", () => act("detail", {kind:"stance",id:view.id}, true)));
      el("thought-stances").appendChild(node);
    }
    el("thought-budget").textContent = `${next.budget.day} · 已预约后台模型调用 ${next.budget.models}${next.settings.test_controls_only ? "（测试不限额）" : ` / ${next.settings.daily_models}`}；结果未知的调用仍计入。`;
    for (const run of next.runs) {
      const node = card(`${modes[run.activity?.activity] || labels[run.activity?.activity] || "旧探索"} · ${labels[run.status] || run.status}`, `${reasons[run.reason] || run.reason} · 模型 ${run.model_calls} 次 · 工具 ${run.steps} 步`);
      const receipt = run.communication_receipt;
      if (receipt) { const note = document.createElement("p"); note.textContent = `交流：${communicationChoices[receipt.decision] || receipt.decision} · ${communicationOutcomes[receipt.outcome] || receipt.outcome}`; node.appendChild(note); }
      node.appendChild(button("查看记录", () => act("detail", {kind:"run",id:run.id}, true)));
      if (!next.settings.test_controls_only && run.status === "paused") node.appendChild(button("继续（调用模型）", () => act("resume", {run_id:run.id})));
      el("thought-runs").appendChild(node);
    }
    const invitationList = el("thought-invitations");
    invitationList.replaceChildren();
    for (const attempt of next.invitations || []) {
      const node = card(`${labels[attempt.state] || attempt.state} · ${attempt.topic || "（无主题）"}`,
        `${attempt.invitation_text}${attempt.expires_at ? `（等待至 ${new Date(attempt.expires_at*1000).toLocaleTimeString()}）` : ""}`);
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
    if (!(next.invitations || []).length && !(next.intents || []).length) invitationList.textContent = "暂无邀请与联系意图。";
    const communicationList = el("thought-communication");
    communicationList.replaceChildren();
    for (const receipt of next.communication_decisions || []) {
      const node = card(`${communicationChoices[receipt.decision] || receipt.decision} · ${new Date(receipt.created_at*1000).toLocaleString()}`,
        `${receipt.reason}\n${communicationOutcomes[receipt.outcome] || receipt.outcome}${receipt.blockers.length ? `；当时受阻：${blockerText(receipt.blockers)}` : ""}`);
      node.appendChild(button("查看该窗口", () => act("detail", {kind:"run",id:receipt.run_id}, true)));
      communicationList.appendChild(node);
    }
    if (!(next.communication_decisions || []).length) communicationList.textContent = "暂无交流决定记录；旧窗口未记录此项。";
    const plan = next.migration_plan;
    el("thought-migration-note").textContent = plan
      ? `已准备迁移计划 ${String(plan.identity).slice(0, 11)}…：旧待发全文联系 ${plan.legacy_contact_ids.length} 条、疑问 ${plan.question_ids.length} 个。执行后旧全文不再投递，转为待重新准备邀请。`
      : "";
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
    for (const field of form.elements) if (field.name && !field.name.startsWith("activity-")) settings[field.name] = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
    settings.activities = Object.fromEntries(Object.keys(modes).map((mode) => [mode, {seconds: form.elements.namedItem(`activity-${mode}-seconds`) ? Number(form.elements.namedItem(`activity-${mode}-seconds`).value) : snapshot.settings.activities[mode].seconds, tools:Object.keys(groups).filter((group) => form.elements.namedItem(`activity-${mode}-${group}`).checked)}]));
    if (snapshot.settings.test_controls_only) settings.allow_external = Object.values(settings.activities).some((item) => item.tools.includes("search"));
    act("settings", {revision:draftRevision, settings});
  };
  el("thought-record").onsubmit = (event) => { event.preventDefault(); act("record", {question:el("thought-question").value}); };
  el("thought-run").onclick = () => act("run");
  el("thought-rest").onclick = () => act(snapshot?.scheduler?.idle?.active ? "idle_wake" : "idle_rest");
  el("thought-stop").onclick = () => act("pause");
  el("thought-migration-prepare").onclick = () => act("migration_prepare");
  el("thought-migration-apply").onclick = () => {
    if (!snapshot) return;
    const stored = snapshot.migration_plan;
    if (!stored) { status("请先准备迁移计划，核对后再执行。", true); return; }
    act("migration_apply", {identity: stored.identity});
  };
  el("thought-wake").onclick = () => {
    if (!snapshot) return;
    if (dirty && draftRevision !== snapshot.revision) {
      status("设置已在别处更新，请先核对或放弃旧草稿，再切换状态。", true); return;
    }
    const on = snapshot.settings.wake_state === "awake" && snapshot.settings.allow_paid;
    act("settings", {revision:snapshot.revision, settings: snapshot.settings.test_controls_only
      ? {wake_state:on ? "sleeping" : "awake", allow_paid:!on}
      : {wake_state:snapshot.settings.wake_state === "awake" ? "sleeping" : "awake"}}, false, true);
  };
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
