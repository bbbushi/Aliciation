/* User-approved content discovery. No browser persistence, automatic POST, or message sending. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("discovery"), app = $("app"), trigger = $("discovery-btn");
  if (!panel || !app || !trigger) return;
  panel.innerHTML = `
    <div class="discovery-header">
      <div><span class="discovery-kicker">ALICE / DISCOVER</span><h1 id="discovery-title">内容发现</h1>
        <p class="discovery-intro">告诉 Alice 可以去哪里、围绕什么找。找到的材料和暂时看法，留到你们想聊的时候。</p></div>
      <button id="discovery-close" class="card-x" type="button" aria-label="回到对话">×</button>
    </div>
    <div class="discovery-scroll">
      <p id="discovery-status" class="discovery-status" role="status" aria-live="polite">打开后读取设置和资料。</p>
      <p id="discovery-sharing-warning" class="discovery-status hidden" data-kind="waiting" role="status" aria-live="polite"></p>
      <div class="discovery-overview"><p id="discovery-budget">尚未读取今日使用情况。</p><div class="discovery-actions">
        <button id="discovery-run" class="btn primary" type="button" data-write>现在找一轮</button>
        <button id="discovery-cancel" class="btn ghost hidden" type="button" data-write>停止这次寻找</button>
        <button id="discovery-refresh" class="btn ghost" type="button">刷新</button>
      </div></div>
      <div id="discovery-bound" class="discovery-bound hidden"><span id="discovery-bound-text"></span>
        <button id="discovery-go-chat" class="link" type="button">回到对话</button>
        <button id="discovery-undiscuss" class="link" type="button" data-write>取消引用</button>
      </div>
      <div class="discovery-grid">
        <div class="discovery-controls">
          <details class="discovery-box" open><summary>开放的平台</summary>
            <p class="discovery-hint">只在你开放的平台寻找公开内容。评论流、私信、观看历史与账号操作不随开关开放。</p>
            <div id="discovery-platforms"><p class="discovery-hint">正在等待读取…</p></div>
          </details>
          <details class="discovery-box" open><summary>可用的兴趣</summary>
            <p class="discovery-hint">兴趣概要会用于生成搜索词。写主题和内容方向即可，不必填写私人经历。</p>
            <div id="discovery-interests"></div>
            <form id="discovery-interest-form" class="discovery-form">
              <label class="discovery-field">兴趣主题<input id="discovery-interest-topic" type="text" maxlength="80" placeholder="例如：独立游戏的叙事设计" required></label>
              <label class="discovery-field">可以用于寻找内容的概要<textarea id="discovery-interest-summary" rows="2" maxlength="240" placeholder="想了解哪些作品、做法或不同看法" required></textarea></label>
              <label class="discovery-check"><input id="discovery-interest-external" type="checkbox"><span>允许把这份概要用于获准平台和搜索服务的搜索词</span></label>
              <label class="discovery-check"><input id="discovery-interest-enabled" type="checkbox" checked><span>围绕这个兴趣寻找内容</span></label>
              <div class="discovery-actions"><button id="discovery-interest-save" class="btn ghost" type="submit" data-write>添加兴趣</button>
                <button id="discovery-interest-reset" class="link hidden" type="button">取消编辑</button></div>
              <p id="discovery-interest-draft" class="discovery-draft hidden"></p>
            </form>
          </details>
          <details id="discovery-settings-box" class="discovery-box"><summary>活动时间、额度与搜索方式</summary>
            <form id="discovery-settings-form" class="discovery-form">
              <label class="discovery-check"><input id="discovery-enabled" type="checkbox"><span>开启自主内容发现</span></label>
              <p class="discovery-hint">Alice 运行且聊天空闲时，在所选时段内寻找内容。关闭页面不会停止已开启的活动。</p>
              <div class="discovery-pair"><label class="discovery-field">开始时间（北京时间）<input id="discovery-start" type="time" required></label>
                <label class="discovery-field">结束时间（北京时间）<input id="discovery-end" type="time" required></label></div>
              <p class="discovery-hint">开始与结束时间相同表示全天。</p>
              <fieldset class="discovery-policy-section"><legend>每日额度</legend>
                <p class="discovery-hint">0 表示关闭对应行为；勾选“不限”表示不设置该项额度。所有开放平台共用这些设置。</p>
                <div id="discovery-daily-limits" class="discovery-policy-grid"></div>
              </fieldset>
              <label class="discovery-check"><input id="discovery-paid" type="checkbox"><span>允许按以上额度使用已配置的模型；请求数不是金额上限，已发出但取消的请求也可能计费</span></label>
              <label class="discovery-check"><input id="discovery-chat-interests" type="checkbox"><span>允许从聊天中明确表达的普通兴趣整理候选，供我确认</span></label>
              <label class="discovery-check"><input id="discovery-share-qq" type="checkbox"><span>允许在既有本人 QQ 聊天中使用符合范围的资料，不主动发送</span></label>
              <label class="discovery-check" title="当前仅维基百科材料符合正式见闻保存条件；条数与证据保留时间由这里的设置决定，其他平台仍作临时资料。"><input id="discovery-save-memories" type="checkbox"><span>允许把符合来源条件的见闻保存为记忆</span></label>
              <details class="discovery-policy-section"><summary>活动节奏与库存目标</summary><div id="discovery-rhythm-settings" class="discovery-policy-grid"></div></details>
              <details class="discovery-policy-section"><summary>资料、兴趣与对话容量</summary>
                <p class="discovery-hint">容量设为 0 表示不允许对应内容，勾选“不限”则不设该项容量。</p>
                <p id="discovery-storage-usage" class="discovery-hint">尚未读取来源内容池用量。</p>
                <div id="discovery-capacity-settings" class="discovery-policy-grid"></div>
              </details>
              <details class="discovery-policy-section"><summary>资料保留、冷却与讨论时间</summary>
                <p class="discovery-hint">各项时间须大于 0。实际保留时间也受来源的使用许可约束；修改设置不会自动恢复已失效的资料。</p>
                <div id="discovery-retention-settings" class="discovery-policy-grid"></div>
              </details>
              <details class="discovery-policy-section"><summary>高级：每次任务的工作量与等待时间</summary>
                <p class="discovery-hint">这里决定一次任务取多少材料、等多久，以及单次响应可读取的大小；不会增加每日额度。结果数量还受平台单页能力限制。</p>
                <div id="discovery-execution-settings" class="discovery-policy-grid"></div>
              </details>
              <label class="discovery-field">内容发现网络路由<select id="discovery-network-mode" aria-describedby="discovery-network-hint" required disabled><option value="" disabled>等待服务提供设置</option><option value="system">跟随本机系统 HTTP 代理</option><option value="direct">直接连接</option></select></label>
              <p id="discovery-network-hint" class="discovery-hint">当前服务尚未提供网络路由设置。</p>
              <label class="discovery-field">B站访问方式<select id="discovery-bilibili-access" disabled><option value="native">B站原生入口</option><option value="third_party">第三方搜索服务</option></select></label>
              <p id="discovery-bilibili-hint" class="discovery-hint"></p>
              <label class="discovery-field">贴吧访问方式<select id="discovery-tieba-access" aria-describedby="discovery-tieba-hint" required disabled><option value="" disabled>等待服务提供设置</option><option value="native">贴吧原生入口</option><option value="third_party">第三方搜索服务</option></select></label>
              <p id="discovery-tieba-hint" class="discovery-hint"></p>
              <label class="discovery-field">第三方搜索服务<select id="discovery-provider"><option value="native">不使用第三方搜索服务</option><option value="bing_rss">Bing RSS（免费，无需密钥）</option><option value="brave">Brave 搜索服务</option><option value="searxng">SearXNG 搜索服务</option></select></label>
              <p id="discovery-provider-hint" class="discovery-hint"></p>
              <label id="discovery-endpoint-field" class="discovery-field hidden">搜索服务地址<input id="discovery-endpoint" type="url" maxlength="1024" autocomplete="off" spellcheck="false" placeholder="https://搜索服务地址"></label>
              <label id="discovery-engines-field" class="discovery-field hidden">使用的搜索引擎（英文逗号分隔）<input id="discovery-engines" type="text" maxlength="8192" autocomplete="off" spellcheck="false" placeholder="duckduckgo"></label>
              <label id="discovery-key-field" class="discovery-field hidden">搜索服务密钥<input id="discovery-key" type="password" maxlength="512" autocomplete="new-password" placeholder="留空保留现有密钥"></label>
              <p id="discovery-key-state" class="discovery-hint hidden"></p>
              <label id="discovery-clear-key-field" class="discovery-check hidden"><input id="discovery-clear-key" type="checkbox"><span>移除已保存的搜索服务密钥</span></label>
              <p id="discovery-settings-draft" class="discovery-draft hidden"></p>
              <div class="discovery-actions"><button id="discovery-settings-save" class="btn primary" type="submit" data-write>保存设置</button>
                <button id="discovery-settings-reset" class="link" type="button">重新载入设置</button></div>
            </form>
          </details>
        </div>
        <section class="discovery-results" aria-labelledby="discovery-results-title">
          <div class="discovery-results-head"><h2 id="discovery-results-title">找到的资料</h2><p id="discovery-card-count" class="discovery-hint"></p></div>
          <p class="discovery-hint">原文说法与 Alice 的暂时判断分开保留。资料会到期；没有新发现时，也可以什么都不带回。</p>
          <div id="discovery-cards"><p class="discovery-empty">开放平台并添加兴趣后，这里会出现可继续聊的材料。</p></div>
          <details class="discovery-task"><summary>最近的寻找活动</summary><p id="discovery-task" class="discovery-hint">尚未读取。</p><ol id="discovery-recent"></ol></details>
        </section>
      </div>
    </div>`;

  // This is a notice beside the existing composer, not a second chat or send path.
  const composerNotice = document.createElement("div");
  composerNotice.className = "discovery-composer-note hidden";
  composerNotice.setAttribute("role", "status");
  const composerText = document.createElement("span");
  const composerCancel = document.createElement("button");
  composerCancel.type = "button"; composerCancel.className = "link"; composerCancel.textContent = "取消资料引用";
  composerNotice.append(composerText, composerCancel);
  $("chat").querySelector("footer").prepend(composerNotice);

  let opened = false, working = false, generation = 0, timer = null, controller = null;
  let snapshot = null, dataset = null, fresh = false, settingsDirty = false, settingsRevision = null;
  let interestDirty = false, interestRevision = null, editingInterest = null, stopped = false;
  let settingsEdit = 0, interestEdit = 0;
  let platformSignature = "", interestSignature = "", cardSignature = "";
  let pendingDiscussion = null, discussionTimer = null;
  const activeStates = new Set(["queued", "starting", "running", "planning", "searching", "reading", "synthesizing", "committing", "cancelling", "draining"]);
  const taskLabels = { idle: "等待下一次合格活动", queued: "等待开始", starting: "正在准备", running: "正在寻找", plan: "正在选择角度", search: "正在寻找材料", read: "正在读取内容", interpret: "正在整理材料与看法", bili_search: "正在准备 B站搜索", bili_read: "正在准备读取视频信息", bili_http: "正在访问 B站公开接口", planning: "正在选择角度", searching: "正在寻找材料", reading: "正在读取内容", synthesizing: "正在整理材料与看法", committing: "正在保存资料", completed: "本次寻找已完成", succeeded: "本次寻找已完成", skipped: "本次未继续寻找", cancelled: "本次已停止", cancelling: "正在停止", draining: "正在收回本次任务", failed: "本次寻找未完成", unavailable: "发现服务暂不可用", interrupted: "本次寻找已中断" };
  const biliStages = { device: "匿名设备准备", wbi_keys: "搜索参数准备", search: "视频搜索", video_info: "视频信息读取" };
  const reasons = { disabled: "自主发现尚未开启", outside_window: "当前不在活动时段", foreground_busy: "先等当前聊天结束", stock_sufficient: "已有足够的新资料", no_new_material: "本轮没有新增资料", search_no_candidates: "搜索已返回，但没有可读取的页面或帖子候选，尚未进入正文阅读", no_selected_material: "已读取材料，本轮未选入资料卡", quota_exhausted: "今日额度已经用完", source_revoked: "来源许可已关闭", cancelled: "本次任务已停止", no_platforms: "还没有开放平台", no_interests: "还没有可用于寻找内容的兴趣", no_credentials: "所选服务还没有配置可用凭据", timeout: "本次寻找达到时间限制" };
  const policyFields = [
    { key: "daily_queries", label: "每日查询次数", group: "daily", integer: true, nullable: true },
    { key: "daily_models", label: "后台每日模型请求", group: "daily", integer: true, nullable: true },
    { key: "daily_network", label: "每日网络尝试", group: "daily", integer: true, nullable: true },
    { key: "daily_response_mib", label: "每日响应正文（MiB）", group: "daily", nullable: true },
    { key: "daily_shares", label: "每日自动带出资料次数", group: "daily", integer: true, nullable: true },
    { key: "daily_memory_writes", label: "每日正式见闻条数", group: "daily", integer: true, nullable: true },
    { key: "activity_interval_minutes", label: "两次活动间隔（分钟）", group: "rhythm", note: "0 表示不设最小间隔。" },
    { key: "share_interval_minutes", label: "两次自动分享间隔（分钟）", group: "rhythm", note: "0 表示不设最小间隔。" },
    { key: "tick_minutes", label: "检查活动条件的间隔（分钟）", group: "rhythm", positive: true, note: "须大于 0；检查本身不调用模型。" },
    { key: "stock_target", label: "新资料库存目标（份）", group: "rhythm", integer: true, nullable: true, unlimitedLabel: "不按库存暂停", note: "0 表示不自动补充；不按库存暂停时仍遵守时段与其他额度。" },
    { key: "pool_capacity", label: "候选资料数量", group: "capacity", integer: true, nullable: true },
    { key: "source_pool_bytes", label: "来源内容池（MiB）", group: "capacity", nullable: true, scale: 1048576, note: "包含临时来源与外部正式见闻的来源内容。" },
    { key: "interest_capacity", label: "兴趣总数量", group: "capacity", integer: true, nullable: true },
    { key: "active_interest_capacity", label: "同时启用的兴趣数量", group: "capacity", integer: true, nullable: true },
    { key: "chat_material_limit", label: "每轮可带入的新资料数量", group: "capacity", integer: true, nullable: true },
    { key: "chat_dependency_limit", label: "每轮来源依赖数量", group: "capacity", integer: true, nullable: true },
    { key: "external_recall_limit", label: "每次外部回忆的条目数量", group: "capacity", integer: true, nullable: true },
    { key: "candidate_ttl_hours", label: "候选资料保留（小时）", group: "retention", positive: true },
    { key: "source_ttl_hours", label: "临时来源保留（小时）", group: "retention", positive: true },
    { key: "memory_ttl_hours", label: "正式见闻证据保留（小时）", group: "retention", positive: true },
    { key: "seen_ttl_days", label: "重复内容排除期（天）", group: "retention", positive: true },
    { key: "block_ttl_days", label: "停用来源保留（天）", group: "retention", positive: true },
    { key: "less_share_days", label: "少分享的持续时间（天）", group: "retention", positive: true },
    { key: "discussion_minutes", label: "本轮讨论选择有效期（分钟）", group: "retention", positive: true },
    { key: "search_results", label: "每次查询的首页结果数量", group: "execution", integer: true, positive: true, note: "平台实际可返回的单页结果可能更少。" },
    { key: "read_candidates", label: "每次尝试读取的候选数量", group: "execution", integer: true, nullable: true, note: "0 不读取；不限则尝试所有候选，仍受时段、期限与其他额度约束。" },
    { key: "planning_interests", label: "模型选题时使用的兴趣数量", group: "execution", integer: true, nullable: true, note: "0 跳过模型选题；不限则可使用全部已启用并允许外发的兴趣。" },
    { key: "activity_timeout_seconds", label: "整次活动等待期限（秒）", group: "execution", positive: true },
    { key: "search_timeout_seconds", label: "每次查询等待期限（秒）", group: "execution", positive: true },
    { key: "read_timeout_seconds", label: "每份内容读取期限（秒）", group: "execution", positive: true },
    { key: "model_timeout_seconds", label: "每次模型请求期限（秒）", group: "execution", positive: true },
    { key: "search_response_bytes", label: "单次查询响应（MiB）", group: "execution", positive: true, scale: 1048576 },
    { key: "read_response_bytes", label: "单份内容响应（MiB）", group: "execution", positive: true, scale: 1048576 },
    { key: "model_response_bytes", label: "单次模型响应（MiB）", group: "execution", positive: true, scale: 1048576 },
  ];
  const policyControls = new Map();
  const has = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const text = (id, value) => { $(id).textContent = value == null ? "" : String(value); };
  function node(tag, value, cls) { const n = document.createElement(tag); if (value != null) n.textContent = String(value); if (cls) n.className = cls; return n; }
  function list(value) { return Array.isArray(value) ? value : []; }
  function words(value) { return Array.isArray(value) ? value.map(String).join("、") : typeof value === "string" ? value : ""; }
  function time(value) { const n = typeof value === "number" ? value * 1000 : Date.parse(value); return Number.isFinite(n) ? n : 0; }
  function stamp(value) { const n = time(value); return n ? new Date(n).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "未记录"; }
  function expired(card) { return !!time(card.expires_at) && time(card.expires_at) <= Date.now(); }
  function stateLabel(value) { return taskLabels[value] || "正在更新活动状态"; }
  function searchSummaryLabel(value) {
    if (!value || value.kind !== "search_results" || value.stage !== "search") return "";
    const platforms = { wikipedia: "维基百科", bilibili: "B站", zhihu: "知乎", tieba: "贴吧" };
    const providers = { native: "站内搜索", bing_rss: "Bing RSS", brave: "Brave", searxng: "SearXNG" };
    const counts = ["received", "accepted", "returned", "rejected", "duplicates", "unsupported"];
    if (!has(platforms, value.platform) || !has(providers, value.provider)
        || counts.some((key) => !Number.isSafeInteger(value[key]) || value[key] < 0)
        || value.received !== value.accepted + value.rejected + value.duplicates + value.unsupported
        || value.returned > value.accepted) return "";
    const prefix = `${platforms[value.platform]} · ${providers[value.provider]}`;
    if (value.received === 0) return `${prefix}：搜索服务返回 0 条结果，尚无页面或帖子候选`;
    const parts = [value.accepted === 0
      ? `${prefix}：收到 ${value.received} 条结果，来源范围与类型筛选后为 0 条候选`
      : `${prefix}：收到 ${value.received} 条结果，符合范围 ${value.accepted} 条，本轮候选 ${value.returned} 条`];
    if (value.rejected) parts.push(`地址或必要字段不符合读取范围 ${value.rejected} 条`);
    if (value.unsupported) parts.push(`当前不支持的结果类型 ${value.unsupported} 条`);
    if (value.duplicates) parts.push(`重复结果 ${value.duplicates} 条`);
    return parts.join(" · ");
  }
  function diagnosticLabel(value) {
    if (!value || typeof value !== "object" || typeof value.stage !== "string") return "";
    if (value.kind === "search_results") return searchSummaryLabel(value);
    if (has(value, "kind")) return "";
    const isTieba = value.platform === "tieba";
    const stages = isTieba ? { search: "搜索公开主帖", read: "读取公开主帖" } : biliStages;
    if (!Object.prototype.hasOwnProperty.call(stages, value.stage)) return "";
    const parts = [`${isTieba ? "贴吧" : "B站"}阶段：${stages[value.stage]}`];
    if (has(value, "api_code")) parts.push(Number.isInteger(value.api_code) && value.api_code >= -2147483648 && value.api_code <= 2147483647 ? `API 返回码 ${value.api_code}` : "API 返回码未取得");
    if (has(value, "verification_required")) parts.push(value.verification_required === true ? "观察到额外验证信号" : value.verification_required === false ? "未观察到额外验证信号" : "验证信号未取得");
    return parts.join(" · ");
  }
  function status(message, kind = "") { text("discovery-status", message); $("discovery-status").dataset.kind = kind; }
  function button(label, callback, cls = "btn ghost") {
    const b = node("button", label, cls); b.type = "button"; b.dataset.write = "";
    b.addEventListener("click", callback); return b;
  }
  function check(label, checked) {
    const row = node("label", null, "discovery-check"), input = node("input"); input.type = "checkbox"; input.checked = !!checked;
    row.append(input, node("span", label)); return { row, input };
  }
  function safeLink(url, label) {
    try {
      const parsed = new URL(url); if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
      const a = node("a", label); a.href = parsed.href; a.target = "_blank"; a.rel = "noopener noreferrer"; a.referrerPolicy = "no-referrer"; return a;
    } catch { return null; }
  }
  function mountPolicyFields() {
    const groups = { daily: "discovery-daily-limits", rhythm: "discovery-rhythm-settings", capacity: "discovery-capacity-settings", retention: "discovery-retention-settings", execution: "discovery-execution-settings" };
    for (const field of policyFields) {
      const wrapper = node("div", null, "discovery-policy-field"), label = node("label", field.label, "discovery-policy-label");
      const input = node("input"), row = node("div", null, "discovery-limit-input");
      input.id = `discovery-policy-${field.key}`; input.type = "number"; input.min = "0";
      input.step = field.integer ? "1" : "any"; input.disabled = true; input.required = true;
      label.htmlFor = input.id; row.append(input);
      const unlimited = field.nullable ? check(field.unlimitedLabel || "不限", false) : null;
      if (unlimited) {
        unlimited.input.disabled = true; unlimited.input.setAttribute("aria-label", `${field.label}：${field.unlimitedLabel || "不限"}`);
        row.append(unlimited.row);
      }
      const note = node("p", field.note || "", "discovery-hint discovery-policy-note");
      note.id = `${input.id}-hint`; input.setAttribute("aria-describedby", note.id); note.classList.toggle("hidden", !field.note);
      wrapper.append(label, row, note); $(groups[field.group]).append(wrapper);
      policyControls.set(field.key, { field, input, unlimited: unlimited?.input || null, note, supported: false });
    }
  }
  function updatePolicyControls() {
    for (const control of policyControls.values()) {
      control.input.disabled = !control.supported || !!control.unlimited?.checked;
      if (control.unlimited) control.unlimited.disabled = !control.supported;
    }
  }
  function fillPolicySettings(settings) {
    for (const control of policyControls.values()) {
      const { field, input, unlimited, note } = control;
      control.supported = has(settings, field.key);
      const value = settings[field.key];
      input.value = control.supported && Number.isFinite(value) ? String(value / (field.scale || 1)) : "";
      if (unlimited) unlimited.checked = control.supported && value === null;
      note.textContent = control.supported ? field.note || "" : "当前服务尚未提供此设置。";
      note.classList.toggle("hidden", !note.textContent);
    }
    updatePolicyControls();
  }
  function readPolicySettings() {
    const values = {};
    for (const { field, input, unlimited, supported } of policyControls.values()) {
      if (!supported) continue;
      if (unlimited?.checked) { values[field.key] = null; continue; }
      const raw = input.value.trim(), value = Number(raw);
      if (!raw || !Number.isFinite(value) || value < 0 || field.positive && value === 0
          || field.integer && !Number.isSafeInteger(value)) {
        revealSetting(input); input.focus();
        throw new Error(`${field.label}须填写${field.positive ? "大于 0 的数值" : field.integer ? "可准确表示的非负整数" : "非负数值"}${field.nullable ? "，也可选择“不限”" : ""}。`);
      }
      if (field.scale) {
        const bytes = Math.round(value * field.scale);
        if (!Number.isSafeInteger(bytes) || value > 0 && bytes === 0) {
          revealSetting(input); input.focus();
          throw new Error(`${field.label}须能换算为可准确表示的整数个字节${field.nullable ? "，也可设置 0 或选择不限" : ""}。`);
        }
        values[field.key] = bytes;
      } else values[field.key] = value;
    }
    return values;
  }
  function revealSetting(input) {
    for (let parent = input.parentElement; parent && parent !== panel; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  }
  function quota(value) { return value === null ? "不限" : Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : "未提供"; }
  mountPolicyFields();
  function currentRevision() { return snapshot?.settings?.revision; }
  function settingsConflict() { return settingsDirty && settingsRevision !== currentRevision(); }
  function updateControls() {
    const ready = fresh && !!dataset && Number.isInteger(currentRevision()) && !working && !stopped;
    panel.querySelectorAll("[data-write]").forEach((n) => { n.disabled = !ready || n.dataset.ineligible === "true"; });
    $("discovery-refresh").disabled = working || stopped;
    $("discovery-settings-reset").disabled = working || !snapshot;
    $("discovery-interest-reset").disabled = working;
    $("discovery-settings-save").disabled = !ready || settingsConflict();
    const task = snapshot?.task || {}, active = activeStates.has(task.state);
    $("discovery-run").disabled = !ready || active || !snapshot?.settings?.enabled;
    $("discovery-cancel").classList.toggle("hidden", !active);
    $("discovery-cancel").disabled = !ready || !task.id || ["draining", "cancelling"].includes(task.state);
    composerCancel.disabled = working || stopped || $("input").disabled;
    const dirtyNote = $("discovery-settings-draft");
    dirtyNote.classList.toggle("hidden", !settingsDirty);
    dirtyNote.textContent = settingsConflict() ? "设置已在别处更新。你的草稿仍在；请先重新载入，再保存。" : "有尚未保存的设置。";
    $("discovery-interest-draft").classList.toggle("hidden", !interestDirty);
    text("discovery-interest-draft", interestDirty && interestRevision !== currentRevision() ? "设置已更新。请取消编辑并重新选择兴趣，避免覆盖新内容。" : "兴趣修改尚未保存。保存不会直接发送聊天消息。");
  }
  function providerFields() {
    const provider = $("discovery-provider").value, keySupported = ["brave", "searxng"].includes(provider);
    const networkSupported = has(snapshot?.settings, "network_mode") && ["system", "direct"].includes(snapshot.settings.network_mode);
    $("discovery-network-mode").disabled = !networkSupported;
    if (!networkSupported) $("discovery-network-mode").value = "";
    text("discovery-network-hint", networkSupported
      ? "仅影响公开平台、Sogou、Bing RSS 与 Brave 请求。本机 HTTP 代理由系统配置；不支持远程代理、SOCKS 或 PAC 自动脚本。模型与自定义 SearXNG 保持原路由。这是连接方式，不是第三方搜索通道。"
      : "当前服务尚未提供可用的网络路由设置；此项不会提交。模型与自定义 SearXNG 保持原路由。");
    $("discovery-endpoint-field").classList.toggle("hidden", provider !== "searxng");
    $("discovery-engines-field").classList.toggle("hidden", provider !== "searxng");
    $("discovery-key-field").classList.toggle("hidden", !keySupported);
    $("discovery-key-state").classList.toggle("hidden", !keySupported);
    $("discovery-clear-key-field").classList.toggle("hidden", !keySupported);
    $("discovery-endpoint").required = provider === "searxng";
    $("discovery-engines").required = provider === "searxng";
    $("discovery-endpoint").disabled = provider !== "searxng";
    $("discovery-engines").disabled = provider !== "searxng";
    $("discovery-key").disabled = !keySupported;
    $("discovery-clear-key").disabled = !keySupported;
    text("discovery-provider-hint", provider === "native" ? "使用已接入的平台原生入口，不把搜索词交给第三方搜索服务。各平台仍须单独开放。"
      : provider === "bing_rss" ? "使用此通道的平台会把搜索词交给 Bing。公开 RSS 免费且无需密钥、服务地址或引擎配置；不保证返回数量或始终可达。请在各平台单独允许第三方搜索。"
      : provider === "brave" ? "使用此通道的平台会把搜索词交给 Brave。请在各平台单独允许此通道，并提供自己的服务密钥；具体费用以服务账单为准。"
      : "使用此通道的平台会把搜索词交给你填写的 SearXNG 服务。请确认服务地址及使用许可，并在各平台单独允许此通道。");
    text("discovery-bilibili-hint", !has(snapshot?.settings, "bilibili_access") ? "读取支持此设置的服务后可选择 B站访问方式。"
      : $("discovery-bilibili-access").value === "native" ? "原生入口可搜索公开视频、读取视频信息和简介，无需第三方搜索服务。字幕尚未开放，也不表示已观看视频。"
      : "搜索词会交给下面选择的第三方服务；保存后需在 B站平台卡中单独允许该通道。读取范围仍以实际取得的视频信息与简介为准。");
    const tiebaSupported = has(snapshot?.settings, "tieba_access") && ["native", "third_party"].includes(snapshot.settings.tieba_access);
    $("discovery-tieba-access").disabled = !tiebaSupported;
    if (!tiebaSupported) $("discovery-tieba-access").value = "";
    text("discovery-tieba-hint", !tiebaSupported ? "当前服务尚未提供贴吧访问方式；此项不会提交。"
      : $("discovery-tieba-access").value === "native" ? "贴吧原生入口无需第三方搜索服务，只读公开主帖，不采集回复或楼中楼。实际可读性以本次结果为准。"
      : "搜索词会交给下面选择的第三方服务；保存后需在贴吧平台卡中单独允许该通道。读取仍限公开主帖，不采集回复或楼中楼。");
    text("discovery-key-state", snapshot?.settings?.provider_key_present ? "已保存密钥；内容不会回显。" : "尚未保存搜索服务密钥。SearXNG 是否需要密钥取决于该服务。");
  }
  function fillSettings(force = false) {
    const s = snapshot?.settings; if (!s || settingsDirty && !force) return;
    settingsDirty = false; settingsRevision = s.revision;
    $("discovery-enabled").checked = !!s.enabled;
    $("discovery-start").value = s.window_start || ""; $("discovery-end").value = s.window_end || "";
    fillPolicySettings(s);
    $("discovery-paid").checked = !!s.allow_paid;
    $("discovery-chat-interests").checked = !!s.allow_chat_interests;
    $("discovery-share-qq").checked = !!s.share_in_qq; $("discovery-save-memories").checked = !!s.save_memories;
    $("discovery-provider").value = ["native", "bing_rss", "brave", "searxng"].includes(s.provider_kind) ? s.provider_kind : "native";
    $("discovery-network-mode").value = ["system", "direct"].includes(s.network_mode) ? s.network_mode : "";
    $("discovery-bilibili-access").disabled = !has(s, "bilibili_access");
    $("discovery-bilibili-access").value = s.bilibili_access === "third_party" ? "third_party" : "native";
    $("discovery-tieba-access").value = ["native", "third_party"].includes(s.tieba_access) ? s.tieba_access : "";
    $("discovery-endpoint").value = s.provider_endpoint || ""; $("discovery-key").value = ""; $("discovery-clear-key").checked = false;
    $("discovery-engines").value = s.provider_engines || "duckduckgo";
    providerFields();
  }
  function renderPlatforms() {
    const rows = list(snapshot.platforms), signature = JSON.stringify([rows, snapshot.settings.bilibili_access, snapshot.settings.tieba_access]);
    if (signature === platformSignature) return; platformSignature = signature;
    const target = $("discovery-platforms"); target.replaceChildren();
    if (!rows.length) { target.append(node("p", "暂时没有可配置的平台。", "discovery-hint")); return; }
    for (const p of rows) {
      const biliNative = p.id === "bilibili" && snapshot.settings.bilibili_access === "native";
      const tiebaNative = p.id === "tieba" && snapshot.settings.tieba_access === "native";
      const needsThirdParty = typeof p.requires_third_party === "boolean" ? p.requires_third_party : !biliNative && !tiebaNative && (p.id !== "wikipedia" || !!p.third_party_notice);
      const card = node("form", null, "discovery-platform"), head = node("div", null, "discovery-item-head");
      head.append(node("h3", p.name || p.label || "内容平台"), node("span", p.enabled ? "已开放" : p.available ? "可开放" : "暂不可用", `discovery-pill${p.enabled ? "" : " muted"}`));
      card.append(head, node("p", p.description || "仅使用获准的公开内容。", "discovery-hint"));
      if (words(p.readable_types)) card.append(node("p", `可读范围：${words(p.readable_types)}`, "discovery-hint"));
      if (biliNative) card.append(node("p", "B站原生访问无需第三方搜索服务。可搜索公开视频，读取视频信息与简介；目前不包含字幕或视频画面。", "discovery-hint"));
      if (p.id === "tieba") card.append(node("p", "贴吧只读公开主帖，不采集回复或楼中楼；是否能取得内容以实际结果为准。", "discovery-hint"));
      if (p.discovery_method) card.append(node("p", `发现方式：${({ native: "平台公开入口", bing_rss: "Bing RSS（免费，无需密钥）", brave: "Brave 搜索服务", searxng: "SearXNG 搜索服务" })[p.discovery_method] || words(p.discovery_method)}`, "discovery-hint"));
      if (p.reason) card.append(node("p", p.reason, "discovery-hint"));
      const enabled = check("允许在此平台发现和读取内容", p.enabled); enabled.input.disabled = !p.available && !p.enabled;
      const third = check("允许所选第三方搜索服务寻找此平台的内容", p.allow_third_party);
      card.append(enabled.row);
      if (needsThirdParty && p.third_party_notice) card.append(node("p", p.third_party_notice, "discovery-hint"));
      if (needsThirdParty) card.append(third.row);
      const actions = node("div", null, "discovery-actions");
      const save = button("保存平台选择", () => {}); save.type = "submit";
      actions.append(save, button("清除此平台资料", () => {
        if (window.confirm(`清除${p.name || p.label || "此平台"}的资料及依赖判断？相关引用会失效；这不会关闭平台。`)) mutate("clear", { platform_id: p.id }, "正在清除平台资料…", "平台资料已清除，相关引用不再用于聊天。");
      }, "link"));
      if (["bilibili", "tieba"].includes(p.id)) {
        const accessId = `discovery-${p.id}-access`;
        const chooseAccess = node("button", `设置${p.id === "bilibili" ? " B站" : "贴吧"}访问方式`, "link"); chooseAccess.type = "button";
        chooseAccess.addEventListener("click", () => { $("discovery-settings-box").open = true; $(accessId).focus(); $(accessId).scrollIntoView({ block: "nearest" }); });
        actions.append(chooseAccess);
      }
      card.append(actions);
      card.addEventListener("submit", (event) => {
        event.preventDefault(); mutate("platform", { id: p.id, enabled: enabled.input.checked, allow_third_party: third.input.checked }, "正在保存平台选择…", "平台选择已保存；关闭的平台不再继续寻找或自动分享新资料。");
      });
      target.append(card);
    }
  }
  function resetInterest() {
    editingInterest = null; interestDirty = false; interestRevision = currentRevision();
    $("discovery-interest-form").reset(); $("discovery-interest-enabled").checked = true;
    text("discovery-interest-save", "添加兴趣"); $("discovery-interest-reset").classList.add("hidden"); updateControls();
  }
  function editInterest(item) {
    if (working) return;
    editingInterest = item.id; interestDirty = true; interestRevision = currentRevision();
    $("discovery-interest-topic").value = item.topic || ""; $("discovery-interest-summary").value = item.summary || item.topic || "";
    $("discovery-interest-external").checked = !!item.external_allowed; $("discovery-interest-enabled").checked = !!item.enabled;
    text("discovery-interest-save", "保存兴趣"); $("discovery-interest-reset").classList.remove("hidden"); updateControls();
    $("discovery-interest-topic").focus();
  }
  function renderInterests() {
    const rows = list(snapshot.interests), signature = JSON.stringify(rows);
    if (signature === interestSignature) return; interestSignature = signature;
    const target = $("discovery-interests"); target.replaceChildren();
    if (!rows.length) target.append(node("p", "还没有兴趣。先添加一个想一起了解的主题。", "discovery-hint"));
    for (const item of rows) {
      const card = node("article", null, "discovery-interest"), head = node("div", null, "discovery-item-head");
      head.append(node("h3", item.topic || "未命名兴趣"), node("span", !item.external_allowed ? "等待允许外发" : item.enabled ? "正在使用" : "已停用", `discovery-pill${item.enabled && item.external_allowed ? "" : " muted"}`));
      card.append(head, node("p", item.summary || "尚无兴趣概要。", "discovery-hint"));
      card.append(node("p", item.origin === "conversation_candidate" ? item.external_allowed ? "来自聊天中的兴趣，已允许用于搜索。" : "来自聊天中的兴趣候选，可修改后确认。" : "由你明确提供。", "discovery-hint"));
      const actions = node("div", null, "discovery-actions");
      actions.append(button("修改", () => editInterest(item)), button(item.enabled ? "停用兴趣" : "启用兴趣", () => {
        if (!item.external_allowed && !item.enabled) { editInterest(item); status("请先核对概要并明确允许用于搜索。", "waiting"); return; }
        mutate("interest", { id: item.id, topic: item.topic, summary: item.summary, enabled: !item.enabled, external_allowed: !!item.external_allowed }, "正在更新兴趣…", item.enabled ? "已停用这个兴趣；旧的待用推荐和在途结果将失效。" : "已启用这个兴趣。");
      }), button("删除", () => {
        if (window.confirm(`删除“${item.topic || "此兴趣"}”并停止使用其待用推荐？`)) mutate("interest_delete", { id: item.id }, "正在删除兴趣…", "兴趣已删除，不会从旧资料自动恢复。", { after: () => { if (editingInterest === item.id) resetInterest(); } });
      }, "link"));
      card.append(actions); target.append(card);
    }
  }
  function cardPart(card, title, value, cls = "discovery-source-detail") {
    if (!words(value)) return;
    const block = node("div", null, cls); block.append(node("h4", title), node("p", words(value))); card.append(block);
  }
  function renderCards() {
    const all = list(snapshot.cards), rows = all.filter((c) => !expired(c)), signature = JSON.stringify(rows);
    text("discovery-card-count", `${rows.length} 份当前资料`);
    if (signature === cardSignature) return; cardSignature = signature;
    const target = $("discovery-cards"); target.replaceChildren();
    if (!rows.length) {
      target.append(node("p", snapshot.settings.enabled ? "暂时没有可用的新资料。Alice 会在获准时段和额度内寻找，也可以跳过没有新内容的一轮。" : "先开放一个平台、添加可用于搜索的兴趣，再在活动设置中开启发现。打开页面本身不会调用模型。", "discovery-empty"));
      return;
    }
    for (const item of rows) {
      const card = node("article", null, "discovery-card");
      const platform = list(snapshot.platforms).find((p) => p.id === item.platform || p.id === item.platform_id);
      const heading = node("h3"), title = item.title || "一份可聊的材料";
      heading.append(safeLink(item.url || item.source_url, title) || node("span", title));
      card.append(node("span", platform?.name || platform?.label || item.platform_name || "公开内容", "discovery-pill"), heading);
      if ((item.platform || item.platform_id) === "wikipedia") {
        const licence = node("p", null, "discovery-card-meta");
        licence.append(document.createTextNode("维基百科节选 · "), safeLink("https://creativecommons.org/licenses/by-sa/4.0/", "CC BY-SA 4.0"), document.createTextNode("；概括与解读由 Alice 生成"));
        card.append(licence);
      }
      card.append(node("p", `${item.created_at ? `找到于 ${stamp(item.created_at)}` : "发现时间未记录"}${item.expires_at ? ` · 保留至 ${stamp(item.expires_at)}` : ""}`, "discovery-card-meta"));
      if (item.share_state) card.append(node("p", item.share_state, "discovery-card-meta"));
      cardPart(card, "实际读到的内容", item.summary || item.content);
      cardPart(card, "为什么值得聊", item.reason || item.recommendation_reason);
      cardPart(card, "Alice 的暂时判断", item.interpretation, "discovery-interpretation");
      cardPart(card, "还不能确定", item.uncertainty || item.open_question);
      cardPart(card, "可以继续聊", item.question);
      const detail = node("details"); detail.append(node("summary", "来源、阅读范围与作者说法"));
      cardPart(detail, "阅读范围", item.coverage || "未提供完整阅读范围，不视为读完原文或看过视频。");
      cardPart(detail, "作者的说法", item.author_view);
      cardPart(detail, "取得的依据", item.evidence_excerpt || item.excerpt);
      const source = safeLink(item.url || item.source_url, "打开原始来源 ↗");
      detail.append(source || node("p", "当前没有可打开的来源链接。", "discovery-hint")); card.append(detail);
      const actions = node("div", null, "discovery-actions");
      const discuss = button("本轮讨论", () => discussCard(item), "btn primary");
      const interest = list(snapshot.interests).find((i) => i.id === item.interest_id);
      if (item.can_discuss === false || interest && (!interest.enabled || !interest.external_allowed)) discuss.dataset.ineligible = "true";
      actions.append(discuss, button("继续找这类", () => feedback(item, "more")), button("少分享", () => feedback(item, "less")));
      card.append(actions);
      const secondary = node("div", null, "discovery-actions discovery-secondary");
      secondary.append(button("停用相关兴趣", () => feedback(item, "stop_interest"), "link"), button("不再看此来源", () => feedback(item, "stop_source"), "link"), button("删除资料", () => {
        if (window.confirm("删除这份资料和依赖它的暂时判断？相关聊天引用将失效。")) mutate("delete", { card_id: item.id }, "正在删除资料…", "资料已删除，相关判断与引用不再使用。");
      }, "link"));
      card.append(secondary); target.append(card);
    }
  }
  function updateDiscussion(data) {
    const bound = data.discussion || data.discuss || null;
    const id = data.discuss_card_id || bound?.card_id || null;
    pendingDiscussion = id ? { id, title: bound?.title || list(data.cards).find((c) => c.id === id)?.title || "所选资料", expires_at: data.discuss_expires_at || bound?.expires_at || null } : null;
    scheduleDiscussionExpiry();
    paintDiscussion();
  }
  function scheduleDiscussionExpiry() {
    clearTimeout(discussionTimer);
    if (!pendingDiscussion || !time(pendingDiscussion.expires_at)) return;
    const remaining = time(pendingDiscussion.expires_at) - Date.now();
    if (remaining <= 0) { pendingDiscussion = null; paintDiscussion(); }
    else discussionTimer = setTimeout(scheduleDiscussionExpiry, Math.min(remaining, 2147483647));
  }
  function paintDiscussion() {
    $("discovery-bound").classList.toggle("hidden", !pendingDiscussion);
    composerNotice.classList.toggle("hidden", !pendingDiscussion);
    const validity = pendingDiscussion && time(pendingDiscussion.expires_at) ? `有效至 ${stamp(pendingDiscussion.expires_at)}` : "有效期以当前设置及来源期限为准";
    const message = pendingDiscussion ? `下一轮网页聊天可引用：${pendingDiscussion.title}。${validity}，发送时仍会核对资料。` : "";
    text("discovery-bound-text", message); composerText.textContent = message;
  }
  function clearIdentity() {
    snapshot = null; dataset = null; fresh = false; settingsDirty = false; settingsRevision = null;
    interestDirty = false; pendingDiscussion = null; clearTimeout(discussionTimer);
    platformSignature = interestSignature = cardSignature = "";
    $("discovery-settings-form").reset(); $("discovery-key").value = ""; fillPolicySettings({}); $("discovery-bilibili-access").disabled = true; resetInterest();
    $("discovery-platforms").replaceChildren(node("p", "等待当前会话的平台设置。", "discovery-hint"));
    $("discovery-interests").replaceChildren(); $("discovery-cards").replaceChildren(); $("discovery-recent").replaceChildren();
    text("discovery-budget", "尚未读取今日使用情况。"); text("discovery-card-count", ""); text("discovery-task", "尚未读取。");
    text("discovery-storage-usage", "尚未读取来源内容池用量。");
    text("discovery-sharing-warning", ""); $("discovery-sharing-warning").classList.add("hidden");
    providerFields(); paintDiscussion();
  }
  function adopt(data) {
    if (!data.settings || !Number.isInteger(data.settings.revision) || !data.dataset_id) throw new Error("内容发现响应不完整，请刷新确认。");
    if (dataset && dataset !== data.dataset_id) {
      clearIdentity();
    }
    dataset = data.dataset_id; snapshot = data; fresh = true;
    const sharingWarning = typeof data.sharing_warning === "string" ? data.sharing_warning : "";
    text("discovery-sharing-warning", sharingWarning); $("discovery-sharing-warning").classList.toggle("hidden", !sharingWarning);
    if (!interestDirty) interestRevision = currentRevision();
    fillSettings(); providerFields(); renderPlatforms(); renderInterests(); renderCards(); updateDiscussion(data);
    const budget = data.budget || {}, s = data.settings;
    const used = (value) => Number.isFinite(value) ? quota(value) : "未知";
    const limit = (budgetKey, settingsKey) => quota(has(budget, budgetKey) ? budget[budgetKey] : s[settingsKey]);
    const responseLimit = has(budget, "bytes_limit") ? budget.bytes_limit === null ? null : budget.bytes_limit / 1048576 : s.daily_response_mib;
    const responseUsed = Number.isFinite(budget.bytes_used) ? budget.bytes_used / 1048576 : undefined;
    text("discovery-budget", [
      `今日查询 ${used(budget.queries_used)} / ${limit("queries_limit", "daily_queries")}`,
      `后台模型 ${used(budget.models_used)} / ${limit("models_limit", "daily_models")}`,
      `网络尝试 ${used(budget.network_used)} / ${limit("network_limit", "daily_network")}`,
      `响应正文 ${used(responseUsed)} / ${quota(responseLimit)} MiB`,
      `自动分享 ${used(budget.shares_used)} / ${limit("shares_limit", "daily_shares")}`,
      `正式见闻 ${used(budget.memory_writes_used)} / ${limit("memory_writes_limit", "daily_memory_writes")}`,
    ].join(" · "));
    const storage = data.storage || {}, stored = storage.used_bytes;
    const storageLimit = has(storage, "limit_bytes") ? storage.limit_bytes : s.source_pool_bytes;
    const storageAllowance = storageLimit === null ? "不限" : `${quota(Number.isFinite(storageLimit) ? storageLimit / 1048576 : undefined)} MiB`;
    text("discovery-storage-usage", `来源内容池：${used(Number.isFinite(stored) ? stored / 1048576 : undefined)} MiB / ${storageAllowance}。`
      + (Number.isFinite(stored) && Number.isFinite(storageLimit) && stored > storageLimit ? "已有用量超过当前容量，暂停新增；仍合法的历史不会因此删除。" : "调低容量只限制新增，不删除仍合法的历史。"));
    const task = data.task || {}, label = stateLabel(task.state === "running" && taskLabels[task.phase] ? task.phase : task.state || "idle");
    const reason = reasons[task.reason] || task.reason_message || "";
    const searchSummary = searchSummaryLabel(task.search_summary) || searchSummaryLabel(task.diagnostic);
    const diagnostic = [searchSummary, task.diagnostic?.kind === "search_results" ? "" : diagnosticLabel(task.diagnostic)].filter(Boolean).join("。");
    text("discovery-task", `${label}${reason ? `：${reason}` : ""}${diagnostic ? `。${diagnostic}` : ""}${task.started_at ? `。开始于 ${stamp(task.started_at)}` : ""}`);
    const recent = $("discovery-recent"); recent.replaceChildren();
    for (const row of list(data.recent || data.tasks).slice(0, 10)) recent.append(node("li", `${stamp(row.started_at || row.created_at)} · ${stateLabel(row.state)}${reasons[row.reason] ? ` · ${reasons[row.reason]}` : ""}`));
    status(s.enabled ? `${label}${reason ? ` · ${reason}` : ""}${diagnostic ? ` · ${diagnostic}` : ""}` : "自主内容发现已关闭。设置开放的平台与兴趣后，可以按你的节奏开启。");
    updateControls();
  }
  function unavailable(message) {
    fresh = false; cardSignature = "";
    $("discovery-cards").replaceChildren(node("p", "暂时不能确认资料是否仍可使用，请刷新后查看。你的设置草稿会保留。", "discovery-empty"));
    text("discovery-card-count", "状态尚未确认"); status(message, "error"); updateControls();
  }
  async function request(method, body, epoch) {
    const own = new AbortController(); controller = own;
    const deadline = setTimeout(() => own.abort(), 15000);
    try {
      const response = await fetch("/api/discovery", { method, headers: { "X-Alice-Intent": "discovery", ...(method === "POST" ? { "Content-Type": "application/json" } : {}) }, cache: "no-store", signal: own.signal, ...(method === "POST" ? { body: JSON.stringify(body) } : {}) });
      const data = await response.json().catch(() => ({}));
      if (epoch !== generation || stopped) return null;
      if (["revision_conflict", "session_changed"].includes(data.code) || ["conflict", "revision_conflict", "reset"].includes(data.status)) {
        const error = new Error("设置或会话已更新，本次操作未能确认。请刷新后重新核对；你的编辑草稿不会自动覆盖新设置。"); error.conflict = true; throw error;
      }
      if (!response.ok || data.status !== "ok") {
        throw new Error(response.status === 404 ? "当前服务尚未接入内容发现。应用更新并重启服务后可用；本页不会自动重试写操作。"
          : data.status === "busy" || data.code === "busy" ? "Alice 正忙，稍后会刷新状态。"
          : response.status === 503 ? "服务正在退出或暂不可用。"
          : data.error || data.message || "操作没有完成，请刷新确认状态。");
      }
      return data.snapshot || data;
    } finally { clearTimeout(deadline); if (controller === own) controller = null; }
  }
  function schedule() {
    clearTimeout(timer);
    if (opened && !stopped && !document.hidden) timer = setTimeout(() => refresh(), 7000);
  }
  async function refresh() {
    if (!opened || working || stopped || document.hidden) { schedule(); return; }
    const epoch = generation; working = true; updateControls();
    try { const data = await request("GET", null, epoch); if (data) adopt(data); }
    catch (error) { if (epoch === generation) unavailable(error.name === "AbortError" ? "读取超时，暂时保留设置草稿；没有发起新的寻找任务。" : error.message); }
    finally { if (epoch === generation) { working = false; updateControls(); schedule(); } }
  }
  async function mutate(action, payload, progress, success, options = {}) {
    if (working || stopped || !dataset || !fresh) return false;
    const epoch = generation, editAtSend = settingsEdit; working = true; clearTimeout(timer); updateControls(); status(progress);
    try {
      const data = await request("POST", { action, dataset_id: dataset, revision: options.revision ?? currentRevision(), ...payload }, epoch);
      if (!data) return false;
      if (action === "settings" && editAtSend === settingsEdit) { settingsDirty = false; $("discovery-key").value = ""; }
      adopt(data); if (options.after) options.after(data); status(success); return true;
    } catch (error) {
      if (epoch === generation) unavailable(error.name === "AbortError" ? "连接中断，操作结果尚未确认。请刷新检查；本页不会自动重发或再找一轮。" : error.message);
      return false;
    } finally {
      // A timed-out mutation may already have revoked the source on the server.
      if (["settings", "delete", "clear"].includes(action)) window.dispatchEvent(new Event("alice:sources-changed"));
      if (epoch === generation) { working = false; updateControls(); schedule(); }
    }
  }
  function feedback(item, value) {
    const messages = { more: "已提高这类内容的发现优先级，仍遵守活动时段与额度。", less: "会少分享这类资料；这不等于停止寻找。", stop_interest: "相关兴趣已停用，旧的待用推荐不再自动使用。", stop_source: "此来源地址已停用；同一平台的其他获准来源不受影响。" };
    mutate("feedback", { card_id: item.id, value }, "正在保存你的反馈…", messages[value]);
  }
  function goChat() {
    close(false); $("chat-nav").click();
    $("input").dispatchEvent(new Event("input", { bubbles: true }));
    $("input").focus();
  }
  async function discussCard(item) {
    const ok = await mutate("discuss", { card_id: item.id }, "正在为下一轮准备资料…", "已为下一轮准备资料，仍由你编辑并发送消息。");
    if (!ok) return;
    const input = $("input");
    if (!input.value.trim()) {
      input.value = `我们聊聊《${item.title || "这份资料"}》吧。${words(item.question || item.open_question) || "你怎样理解这里的说法，还有哪些地方值得怀疑或继续了解？"}`;
    }
    goChat();
  }
  async function removeDiscussion() {
    if (working || stopped) return;
    // Refresh the snapshot before a composer action, because the panel may have been closed.
    if (!opened) {
      const epoch = generation; working = true; updateControls();
      try { const data = await request("GET", null, epoch); if (!data) return; adopt(data); }
      catch (error) { open(); status("尚未确认当前引用，请刷新后取消。", "waiting"); return; }
      finally { if (epoch === generation) { working = false; updateControls(); } }
    }
    await mutate("undiscuss", {}, "正在取消引用…", "已取消资料引用。输入框中的草稿保留，可自行编辑。");
  }
  async function refreshDiscussion() {
    if (opened) { refresh(); return; }
    if (!pendingDiscussion || working || stopped || document.hidden) return;
    const epoch = generation; working = true; updateControls();
    try {
      const data = await request("GET", null, epoch);
      if (data) {
        if (dataset && data.dataset_id !== dataset) { pendingDiscussion = null; paintDiscussion(); }
        else updateDiscussion(data);
      }
    } catch {
      if (pendingDiscussion) composerText.textContent = "所选资料的使用状态暂未确认；可打开内容发现刷新查看。不会自动重发消息。";
    } finally { if (epoch === generation) { working = false; updateControls(); } }
  }
  function open() {
    if (stopped) return;
    window.AliceObserver?.close(); window.AliceEvaluations?.close();
    opened = true; generation++; working = false; controller?.abort();
    panel.classList.remove("hidden"); app.classList.add("discovering"); trigger.setAttribute("aria-expanded", "true"); trigger.setAttribute("aria-current", "page");
    $("chat-nav").classList.remove("selected"); $("chat-nav").removeAttribute("aria-current");
    if (app.classList.contains("nav-open")) $("nav-close").click();
    panel.inert = false; $("discovery-close").focus(); status("正在核对当前设置与资料…", "waiting"); refresh();
  }
  function close(returnFocus = true) {
    if (!opened) return;
    opened = false; generation++; working = false; fresh = false; clearTimeout(timer); controller?.abort(); controller = null;
    $("discovery-key").value = "";
    panel.classList.add("hidden"); app.classList.remove("discovering"); trigger.setAttribute("aria-expanded", "false"); trigger.removeAttribute("aria-current");
    $("chat-nav").classList.add("selected"); $("chat-nav").setAttribute("aria-current", "page");
    cardSignature = ""; $("discovery-cards").replaceChildren();
    if (returnFocus && !app.inert) trigger.focus();
  }

  trigger.addEventListener("click", () => opened ? close() : open());
  $("discovery-close").addEventListener("click", goChat);
  for (const id of ["chat-nav", "observer-btn", "evaluations-btn"]) $(id).addEventListener("click", () => close(false), true);
  $("discovery-refresh").addEventListener("click", refresh);
  $("discovery-run").addEventListener("click", () => mutate("run", {}, "正在请求寻找内容…", "寻找请求已提交，实际是否开始取决于当前时段、许可与额度。"));
  $("discovery-cancel").addEventListener("click", () => mutate("cancel", { task_id: snapshot?.task?.id }, "正在请求停止…", "已请求停止；收回任务前不会开始另一轮。已发出的请求仍可能计费。"));
  $("discovery-go-chat").addEventListener("click", goChat);
  $("discovery-undiscuss").addEventListener("click", removeDiscussion); composerCancel.addEventListener("click", removeDiscussion);
  $("discovery-settings-form").addEventListener("input", () => { settingsEdit++; settingsDirty = true; updatePolicyControls(); providerFields(); updateControls(); });
  $("discovery-settings-form").addEventListener("invalid", (event) => revealSetting(event.target), true);
  $("discovery-provider").addEventListener("change", providerFields);
  $("discovery-bilibili-access").addEventListener("change", providerFields);
  $("discovery-tieba-access").addEventListener("change", providerFields);
  $("discovery-settings-reset").addEventListener("click", () => { fillSettings(true); updateControls(); });
  $("discovery-settings-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (settingsConflict()) return;
    let policy;
    try { policy = readPolicySettings(); } catch (error) { status(error.message, "waiting"); return; }
    const settings = { ...policy, enabled: $("discovery-enabled").checked, window_start: $("discovery-start").value, window_end: $("discovery-end").value, allow_paid: $("discovery-paid").checked, allow_chat_interests: $("discovery-chat-interests").checked, share_in_qq: $("discovery-share-qq").checked, save_memories: $("discovery-save-memories").checked, provider_kind: $("discovery-provider").value };
    if (has(snapshot?.settings, "network_mode") && ["system", "direct"].includes(snapshot.settings.network_mode)) {
      const networkMode = $("discovery-network-mode").value;
      if (!["system", "direct"].includes(networkMode)) { status("请选择服务支持的内容发现网络路由。", "waiting"); return; }
      settings.network_mode = networkMode;
    }
    if (has(snapshot?.settings, "bilibili_access")) settings.bilibili_access = $("discovery-bilibili-access").value;
    if (has(snapshot?.settings, "tieba_access") && ["native", "third_party"].includes(snapshot.settings.tieba_access)) {
      const tiebaAccess = $("discovery-tieba-access").value;
      if (!["native", "third_party"].includes(tiebaAccess)) { status("请选择服务支持的贴吧访问方式。", "waiting"); return; }
      settings.tieba_access = tiebaAccess;
    }
    if (settings.provider_kind === "searxng") {
      const engines = $("discovery-engines").value.split(",").map((name) => name.trim()).filter(Boolean);
      if (!engines.length || new Set(engines).size !== engines.length || engines.some((name) => !/^[a-zA-Z0-9_-]{1,40}$/.test(name))) { status("请填写不同的搜索引擎名称，用英文逗号分隔；每个名称使用字母、数字、下划线或连字符（不超过 40 字符）。", "waiting"); return; }
      settings.provider_endpoint = $("discovery-endpoint").value.trim();
      settings.provider_engines = engines.join(",");
    }
    if (settings.enabled && settings.daily_models !== 0 && !settings.allow_paid) { status("开启自主发现前，请明确是否允许模型费用；模型额度选择不限也需要费用许可。", "waiting"); return; }
    if (["brave", "searxng"].includes(settings.provider_kind)) {
      if ($("discovery-key").value.trim()) settings.provider_key = $("discovery-key").value.trim();
      if ($("discovery-clear-key").checked) settings.clear_provider_key = true;
    }
    if (settings.provider_key && settings.clear_provider_key) { status("填写新密钥和移除密钥只能选择一项。", "waiting"); return; }
    mutate("settings", { settings }, "正在保存发现设置…", "设置已保存。自主寻找会遵守所选平台、兴趣、时段与额度。", { revision: settingsRevision });
  });
  $("discovery-interest-form").addEventListener("input", () => { interestEdit++; if (!interestDirty) interestRevision = currentRevision(); interestDirty = true; $("discovery-interest-reset").classList.remove("hidden"); updateControls(); });
  $("discovery-interest-reset").addEventListener("click", resetInterest);
  $("discovery-interest-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (interestRevision !== currentRevision()) { status("兴趣编辑期间设置已更新，请取消编辑并重新选择。", "waiting"); return; }
    const payload = { topic: $("discovery-interest-topic").value.trim(), summary: $("discovery-interest-summary").value.trim(), enabled: $("discovery-interest-enabled").checked, external_allowed: $("discovery-interest-external").checked };
    if (editingInterest) payload.id = editingInterest;
    if (!payload.topic || !payload.summary) { status("请填写兴趣主题和可以用于搜索的概要。", "waiting"); return; }
    if (payload.enabled && !payload.external_allowed) { status("启用这个兴趣前，请先允许将概要用于搜索；也可以先关闭寻找，保留为未启用的兴趣。", "waiting"); return; }
    const editAtSend = interestEdit;
    mutate("interest", payload, "正在保存兴趣…", payload.enabled ? "兴趣已保存，Alice 可以在你开放的范围内自行选择搜索角度。" : "兴趣已保存并停用，不会用于寻找内容。", { revision: interestRevision, after: () => { if (editAtSend === interestEdit) resetInterest(); } });
  });
  document.addEventListener("visibilitychange", () => { clearTimeout(timer); if (!document.hidden) { if (opened) refresh(); else refreshDiscussion(); } });
  new MutationObserver(() => {
    updateControls();
    if (!$("input").disabled) refreshDiscussion();
    else if (pendingDiscussion) composerText.textContent = "正在确认本轮使用的资料；实际采用情况以本轮回复为准。";
  }).observe($("input"), { attributes: true, attributeFilter: ["disabled"] });
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape" && !app.inert) { event.preventDefault(); goChat(); } });
  new MutationObserver(() => {
    panel.inert = app.classList.contains("nav-open");
    if (app.classList.contains("hidden")) { close(false); clearIdentity(); }
  }).observe(app, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(() => {
    if (!$("shutdown-screen").classList.contains("hidden")) { close(false); stopped = true; pendingDiscussion = null; paintDiscussion(); clearTimeout(discussionTimer); }
    else stopped = false;
  }).observe($("shutdown-screen"), { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("pagehide", () => { close(false); stopped = true; controller?.abort(); clearTimeout(discussionTimer); $("discovery-key").value = ""; });
  window.AliceDiscovery = { close, refreshAfterTurn: () => { if (opened) refresh(); }, shutdown: () => { close(false); stopped = true; } };
  updateControls(); providerFields();
})();
