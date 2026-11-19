/* User-approved content discovery. No browser persistence, automatic POST, or message sending. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("discovery"), app = $("app"), trigger = $("discovery-btn"), settingsPanel = $("discovery-settings");
  if (!panel || !app || !trigger || !settingsPanel) return;
  panel.innerHTML = `
    <div class="discovery-header">
      <div><span class="discovery-kicker">ALICE / DISCOVER</span><h1 id="discovery-title">内容发现</h1>
        <p class="discovery-intro">找点你感兴趣的，留着一起聊。</p></div>
      <button id="discovery-close" class="card-x" type="button" aria-label="回到对话">×</button>
    </div>
    <div class="discovery-scroll">
      <p id="discovery-status" class="discovery-status" role="status" aria-live="polite">打开后读取设置和资料。</p>
      <p id="discovery-sharing-warning" class="discovery-status hidden" data-kind="waiting" role="status" aria-live="polite"></p>
      <details id="discovery-learning-preview"><summary>见闻维护预览</summary><p>高价值优先保留，低价值优先遗忘；保留仍受来源期限限制。预览随新材料与实际使用更新。</p><div id="discovery-learning-items"></div></details>
      <div class="discovery-overview"><div class="discovery-overview-copy"><p id="discovery-context">正在读取寻找范围…</p><p id="discovery-tieba-scope" class="discovery-hint hidden"></p><p id="discovery-mode" class="discovery-hint"></p><button id="discovery-toggle-enabled" class="link" type="button" data-write>开启发现</button></div><div class="discovery-actions">
        <button id="discovery-run" class="btn primary" type="button" data-write>现在找一轮</button>
        <button id="discovery-cancel" class="btn ghost hidden" type="button" data-write>停止这次寻找</button>
        <button id="discovery-preferences-toggle" class="btn ghost" type="button" aria-expanded="false" aria-controls="settings-modal" aria-haspopup="dialog">平台与兴趣</button>
        <button id="discovery-settings-open" class="link" type="button">联网搜索设置</button>
      </div></div>
      <div id="discovery-next" class="discovery-next hidden"><p id="discovery-next-text"></p><button id="discovery-next-action" class="btn ghost" type="button"></button></div>
      <div id="discovery-bound" class="discovery-bound hidden"><span id="discovery-bound-text"></span>
        <button id="discovery-go-chat" class="link" type="button">回到对话</button>
        <button id="discovery-undiscuss" class="link" type="button" data-write>取消引用</button>
      </div>
      <div class="discovery-grid">
        <div id="discovery-preferences" class="discovery-controls hidden">
          <div class="discovery-preferences-head"><h2 id="discovery-settings-scope-title">平台与兴趣</h2><button id="discovery-preferences-close" class="link" type="button">返回使用与安排</button></div>
          <p class="discovery-hint discovery-preferences-note">平台操作即时保存。兴趣用自己的保存按钮，切换分类会保留草稿。</p>
          <details id="discovery-platforms-box" class="discovery-box" open><summary>平台访问</summary>
            <p id="discovery-settings-platforms" class="discovery-hint">尚未读取平台状态。</p>
            <p class="discovery-hint">仅在开放的平台查找可读的公开内容。</p>
            <div id="discovery-platforms"><p class="discovery-hint">正在等待读取…</p></div>
          </details>
          <details id="discovery-interests-box" class="discovery-box" open><summary>兴趣主题</summary>
            <form id="discovery-interest-form" class="discovery-form">
              <label class="discovery-field">想了解什么<input id="discovery-interest-topic" type="text" maxlength="80" placeholder="例如：游戏、手柄、独立游戏叙事" required></label>
              <label class="discovery-check"><input id="discovery-interest-external" type="checkbox"><span>允许用这个主题及补充方向，在已开放的平台和搜索服务中搜索</span></label>
              <details id="discovery-interest-forums-box" class="discovery-setting-fold"><summary>优先在哪些吧找（可选）</summary>
                <div class="discovery-scope-fields">
                  <label class="discovery-field">优先吧名<textarea id="discovery-interest-forums" rows="3" placeholder="每行一个准确吧名；留空不设优先吧" aria-describedby="discovery-interest-forums-hint" disabled></textarea></label>
                  <p id="discovery-interest-forums-hint" class="discovery-hint">先尝试这些吧的候选，没有合适内容时可以找其他吧；当前不是完整吧内检索。每行填写一个与平台显示一致的吧名，保留末尾的“吧”和名字中的逗号。</p>
                  <div class="discovery-actions"><button id="discovery-forum-check" class="btn ghost" type="button" data-write>检测吧名</button></div>
                  <p class="discovery-hint">检测只访问贴吧公开入口，不调用模型、不保存兴趣，也不会开启自动寻找；会计入网络额度。新增的吧名需检测确认有公开帖子后再保存。</p>
                  <div id="discovery-forum-check-results" class="discovery-forum-check-results" role="status" aria-live="polite"></div>
                </div>
              </details>
              <details id="discovery-interest-options" class="discovery-setting-fold"><summary>补充方向与启用状态（可选）</summary>
                <label class="discovery-field">补充方向<textarea id="discovery-interest-summary" rows="2" maxlength="240" placeholder="留空就按上面的主题寻找"></textarea></label>
                <label class="discovery-check"><input id="discovery-interest-enabled" type="checkbox" checked><span>启用这个兴趣</span></label>
              </details>
              <div class="discovery-actions"><button id="discovery-interest-save" class="btn primary" type="submit" data-write>添加兴趣</button>
                <button id="discovery-interest-reset" class="link hidden" type="button">取消编辑</button></div>
              <p id="discovery-interest-draft" class="discovery-draft hidden"></p>
            </form>
            <div id="discovery-interests"></div>
          </details>
          <div id="discovery-settings-box">
            <form id="discovery-settings-form" class="discovery-form">
              <label class="discovery-switch"><input id="discovery-enabled" type="checkbox" role="switch"><span>允许内容访问</span><span class="discovery-switch-track" aria-hidden="true"></span></label>
              <div id="discovery-chat-options" class="discovery-setting-group">
                <label class="discovery-switch"><input id="discovery-chat-search" type="checkbox" role="switch"><span>聊天时按需查找<small>允许 Alice 为回答查找相关资料。</small></span><span class="discovery-switch-track" aria-hidden="true"></span></label>
                <details class="discovery-setting-fold"><summary>聊天查找的工作量与等待时间</summary><div id="discovery-chat-limits" class="discovery-policy-grid"></div></details>
              </div>
              <div id="discovery-activity-options" class="discovery-setting-group">
                <label class="discovery-switch"><input id="discovery-background" type="checkbox" role="switch"><span>空闲时按兴趣自动寻找<small>Alice 保持运行时，关闭网页后也会继续。</small></span><span class="discovery-switch-track" aria-hidden="true"></span></label>
                <fieldset class="discovery-time-fields">
                  <legend>运行时段 <span>北京时间</span></legend>
                  <div class="discovery-pair"><label class="discovery-field">开始时间<input id="discovery-start" type="time" required></label>
                    <label class="discovery-field">结束时间<input id="discovery-end" type="time" required></label></div>
                  <p class="discovery-hint">起止时间相同表示全天。</p>
                </fieldset>
                <details class="discovery-setting-fold"><summary>活动节奏与库存目标</summary><div id="discovery-rhythm-settings" class="discovery-policy-grid"></div></details>
              </div>
              <label class="discovery-switch discovery-setting-group"><input id="discovery-paid" type="checkbox" role="switch"><span>允许后台模型选题与整理<small>使用已配置的模型，可能产生费用，受每日请求额度约束。</small></span><span class="discovery-switch-track" aria-hidden="true"></span></label>
              <details id="discovery-tieba-scope-box" class="discovery-policy-section"><summary>贴吧回帖阅读</summary>
                <div class="discovery-scope-fields">
                  <label class="discovery-switch"><input id="discovery-tieba-read-replies" type="checkbox" role="switch" disabled><span>按楼层翻页读取公开回帖</span><span class="discovery-switch-track" aria-hidden="true"></span></label>
                  <label class="discovery-switch"><input id="discovery-tieba-read-nested" type="checkbox" role="switch" disabled><span>读取附带的楼中楼节选</span><span class="discovery-switch-track" aria-hidden="true"></span></label>
                  <div class="discovery-pair">
                    <label class="discovery-field">每帖回复总量（含楼中楼）<input id="discovery-tieba-reply-limit" type="number" min="1" step="1" aria-describedby="discovery-tieba-replies-hint" disabled></label>
                    <label class="discovery-field">每帖文字额度（字符）<input id="discovery-tieba-text-limit" type="number" min="80" step="1" aria-describedby="discovery-tieba-replies-hint" disabled></label>
                  </div>
                  <details class="discovery-setting-fold"><summary>阅读范围与额度说明</summary><p id="discovery-tieba-replies-hint" class="discovery-hint">建议从 200 条回帖和 30,000 字符开始。主帖不占回帖条数，楼中楼只读取所附节选。按楼层读取文字，达到条数、文字或网络额度即停止。额度越高，耗时和上下文用量可能越多。</p></details>
                  <p id="discovery-tieba-scope-support" class="discovery-hint">等待当前服务提供回帖设置。</p>
                  <p class="discovery-hint discovery-scope-notice">保存范围后，不再符合范围的旧资料及相关引用可能停用；已经失效的资料不会因重新放宽范围而恢复。</p>
                </div>
              </details>
              <details class="discovery-policy-section discovery-daily-section"><summary>每日额度</summary>
                <p class="discovery-hint">所有开放平台共用这些额度。</p>
                <div id="discovery-daily-limits" class="discovery-daily-layout"></div>
                <p class="discovery-hint">模型额度按请求次数计算。已发出但取消的请求也可能计费。</p>
              </details>
              <div id="discovery-interest-learning" class="discovery-setting-group discovery-settings-interest-learning">
                <label class="discovery-switch"><input id="discovery-chat-interests" type="checkbox" role="switch"><span>从聊天中整理兴趣<small>依据你的表达整理，可查看、纠正或停用。此项随底部设置保存。</small></span><span class="discovery-switch-track" aria-hidden="true"></span></label>
              </div>
              <div id="discovery-memory-options" class="discovery-setting-group">
                <label class="discovery-switch"><input id="discovery-remember-observations" type="checkbox" role="switch"><span>保存带出处的见闻<small>择要保留，按价值与实际使用复核。</small></span><span class="discovery-switch-track" aria-hidden="true"></span></label>
                <label class="discovery-switch"><input id="discovery-share-qq" type="checkbox" role="switch"><span>本人 QQ 私聊可查找和使用资料<small>仅用于对话回复。查找还需开启聊天查找，与网页共用平台和额度。</small></span><span class="discovery-switch-track" aria-hidden="true"></span></label>
              </div>
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
              <div id="discovery-network-fields" class="discovery-setting-stack">
                <div class="discovery-setting-group">
                  <div class="discovery-setting-row">
                    <div><label for="discovery-network-mode">网络路由</label><p id="discovery-network-hint" class="discovery-hint">当前服务尚未提供网络路由设置。</p></div>
                    <select id="discovery-network-mode" aria-describedby="discovery-network-hint" required disabled><option value="" disabled>等待服务提供设置</option><option value="system">跟随系统 HTTP 代理</option><option value="direct">直接连接</option></select>
                  </div>
                  <details class="discovery-setting-fold"><summary>路由适用范围</summary><p class="discovery-hint">跟随系统时使用本机配置的 HTTP 代理。模型和自定义 SearXNG 沿用各自的路由。</p></details>
                </div>
                <div class="discovery-setting-group discovery-route-group">
                  <h4>平台访问方式</h4>
                  <div class="discovery-setting-row">
                    <div><label for="discovery-bilibili-access">B站</label><p id="discovery-bilibili-hint" class="discovery-hint"></p></div>
                    <select id="discovery-bilibili-access" aria-describedby="discovery-bilibili-hint" disabled><option value="native">B站原生入口</option><option value="browser">独立本机浏览器</option><option value="third_party">第三方搜索服务</option></select>
                  </div>
                  <div class="discovery-setting-row">
                    <div><label for="discovery-zhihu-access">知乎</label><p id="discovery-zhihu-hint" class="discovery-hint"></p></div>
                    <select id="discovery-zhihu-access" aria-describedby="discovery-zhihu-hint" disabled><option value="browser">独立本机浏览器</option><option value="third_party">第三方搜索服务</option></select>
                  </div>
                  <div class="discovery-setting-row">
                    <div><label for="discovery-tieba-access">贴吧</label><p id="discovery-tieba-hint" class="discovery-hint"></p></div>
                    <select id="discovery-tieba-access" aria-describedby="discovery-tieba-hint" required disabled><option value="" disabled>等待服务提供设置</option><option value="native">贴吧原生入口</option><option value="third_party">第三方搜索服务</option></select>
                  </div>
                </div>
                <div class="discovery-setting-group">
                  <div class="discovery-setting-row">
                    <div><label for="discovery-provider">第三方搜索服务</label><p id="discovery-provider-hint" class="discovery-hint"></p></div>
                    <select id="discovery-provider" aria-describedby="discovery-provider-hint"><option value="native">不使用第三方搜索服务</option><option value="bing_rss">Bing RSS（免费，无需密钥）</option><option value="brave">Brave 搜索服务</option><option value="searxng">SearXNG 搜索服务</option></select>
                  </div>
                  <div class="discovery-provider-config">
                    <label id="discovery-endpoint-field" class="discovery-field hidden">服务地址<input id="discovery-endpoint" type="url" maxlength="1024" autocomplete="off" spellcheck="false" placeholder="https://搜索服务地址"></label>
                    <label id="discovery-engines-field" class="discovery-field hidden">搜索引擎（英文逗号分隔）<input id="discovery-engines" type="text" maxlength="8192" autocomplete="off" spellcheck="false" placeholder="duckduckgo"></label>
                    <label id="discovery-key-field" class="discovery-field hidden">搜索服务密钥<input id="discovery-key" type="password" maxlength="512" autocomplete="new-password" placeholder="留空保留现有密钥" aria-describedby="discovery-key-state"></label>
                    <p id="discovery-key-state" class="discovery-hint hidden"></p>
                    <label id="discovery-clear-key-field" class="discovery-check hidden"><input id="discovery-clear-key" type="checkbox"><span>移除已保存的搜索服务密钥</span></label>
                  </div>
                </div>
              </div>
              <p id="discovery-settings-draft" class="discovery-draft hidden"></p>
              <div class="discovery-actions"><button id="discovery-settings-save" class="btn primary" type="submit" data-write>保存设置</button>
                <button id="discovery-settings-reset" class="link" type="button">重新载入设置</button></div>
            </form>
          </div>
        </div>
        <section class="discovery-results" aria-labelledby="discovery-results-title">
          <div class="discovery-results-head"><h2 id="discovery-results-title">找到的资料</h2><p id="discovery-card-count" class="discovery-hint"></p></div>
          <p class="discovery-hint">原文说法与 Alice 的暂时判断分开保留。资料会到期；没有新发现时，也可以什么都不带回。</p>
          <div id="discovery-cards"><p class="discovery-empty">开放平台并添加兴趣后，这里会出现可继续聊的材料。</p></div>
          <details class="discovery-task"><summary>活动记录与用量</summary><p id="discovery-task" class="discovery-hint">尚未读取。</p><p id="discovery-budget" class="discovery-hint">尚未读取今日使用情况。</p><button id="discovery-refresh" class="btn ghost" type="button">刷新状态</button><ol id="discovery-recent"></ol></details>
        </section>
      </div>
    </div>`;
  // Keep original controls and form owners; the interest editor saves separately.
  settingsPanel.append($("discovery-settings-box"));
  buildSettingsLayout();

  function buildSettingsLayout() {
    const box = $("discovery-settings-box"), form = $("discovery-settings-form");
    // Native form ownership lets settings span panes without nesting the interest form.
    form.querySelectorAll("input, select, textarea, button").forEach((control) => control.setAttribute("form", form.id));
    const label = (id) => $(id).closest("label");
    const policy = (id) => $(id).closest("details");
    const master = label("discovery-enabled");
    const draft = $("discovery-settings-draft"), actions = $("discovery-settings-save").closest(".discovery-actions");
    const workspace = node("div", null, "discovery-settings-workspace");
    workspace.innerHTML = `
      <nav class="discovery-settings-nav" aria-label="联网搜索分类">
        <button type="button" data-discovery-settings-target="use" aria-controls="discovery-settings-use" aria-current="page">使用与安排<small>聊天、自动寻找与时段</small></button>
        <button type="button" data-discovery-settings-target="scope" aria-controls="discovery-settings-scope">平台与兴趣<small>平台范围与兴趣来源</small></button>
        <button type="button" data-discovery-settings-target="budget" aria-controls="discovery-settings-budget">费用与额度<small>模型许可与每日上限</small></button>
        <button type="button" data-discovery-settings-target="memory" aria-controls="discovery-settings-memory">资料与使用<small>保存、QQ 与保留时间</small></button>
        <button type="button" data-discovery-settings-target="network" aria-controls="discovery-settings-network">网络与访问<small>搜索服务与平台通道</small></button>
      </nav>
      <div class="discovery-settings-panes">
        <section id="discovery-settings-use" class="discovery-settings-pane" data-discovery-settings-pane="use" aria-labelledby="discovery-settings-use-title"><h3 id="discovery-settings-use-title">使用与安排</h3></section>
        <section id="discovery-settings-scope" class="discovery-settings-pane hidden" data-discovery-settings-pane="scope" aria-labelledby="discovery-settings-scope-title"></section>
        <section id="discovery-settings-budget" class="discovery-settings-pane hidden" data-discovery-settings-pane="budget" aria-labelledby="discovery-settings-budget-title"><h3 id="discovery-settings-budget-title">费用与额度</h3></section>
        <section id="discovery-settings-memory" class="discovery-settings-pane hidden" data-discovery-settings-pane="memory" aria-labelledby="discovery-settings-memory-title"><h3 id="discovery-settings-memory-title">资料与使用</h3></section>
        <section id="discovery-settings-network" class="discovery-settings-pane hidden" data-discovery-settings-pane="network" aria-labelledby="discovery-settings-network-title"><h3 id="discovery-settings-network-title">网络与访问</h3></section>
      </div>`;
    const pane = (name) => workspace.querySelector(`[data-discovery-settings-pane="${name}"]`);
    const cost = node("div", null, "discovery-settings-cost");
    const costText = node("span", "尚未读取后台模型使用许可。"); costText.id = "discovery-settings-cost";
    const costLink = node("button", "调整费用与额度", "link"); costLink.type = "button"; costLink.dataset.discoverySettingsTarget = "budget";
    cost.append(costText, costLink);
    pane("use").append($("discovery-chat-options"), $("discovery-activity-options"));
    const preferences = $("discovery-preferences");
    preferences.classList.remove("hidden");
    preferences.append($("discovery-interest-learning"));
    pane("scope").append(preferences);
    const daily = policy("discovery-daily-limits"); daily.open = true;
    pane("budget").append(label("discovery-paid"), daily);
    pane("memory").append($("discovery-memory-options"), policy("discovery-capacity-settings"), policy("discovery-retention-settings"));
    pane("network").append($("discovery-network-fields"), $("discovery-tieba-scope-box"), policy("discovery-execution-settings"));
    const savebar = node("div", null, "discovery-settings-savebar");
    const saveNote = node("p", "关闭不保存 · 普通草稿本页保留", "discovery-hint");
    savebar.append($("discovery-settings-status"), draft, saveNote, actions);
    master.after(cost);
    box.append(workspace, savebar);
  }

  // This is a notice beside the existing composer, not a second chat or send path.
  const composerNotice = document.createElement("div");
  composerNotice.className = "discovery-composer-note hidden";
  composerNotice.setAttribute("role", "status");
  const composerText = document.createElement("span");
  const composerCancel = document.createElement("button");
  composerCancel.type = "button"; composerCancel.className = "link"; composerCancel.textContent = "取消资料引用";
  composerNotice.append(composerText, composerCancel);
  $("chat").querySelector("footer").prepend(composerNotice);

  let opened = false, settingsOpened = false, working = false, generation = 0, timer = null, controller = null;
  let snapshot = null, dataset = null, fresh = false, settingsDirty = false, settingsRevision = null;
  let interestDirty = false, interestRevision = null, editingInterest = null, stopped = false;
  let settingsEdit = 0, interestEdit = 0, settingsSection = "use";
  let editingPreferredForums = [], forumCheckSignature = "";
  let platformSignature = "", interestSignature = "", cardSignature = "";
  let pendingDiscussion = null, discussionTimer = null;
  const activeStates = new Set(["queued", "starting", "running", "planning", "searching", "reading", "synthesizing", "committing", "cancelling", "draining"]);
  const taskLabels = { idle: "等待下一次合格活动", queued: "等待开始", starting: "正在准备", running: "正在寻找", plan: "正在选择角度", search: "正在寻找材料", read: "正在读取内容", interpret: "正在整理材料与看法", bili_search: "正在准备 B站搜索", bili_read: "正在准备读取视频信息", bili_http: "正在访问 B站公开接口", planning: "正在选择角度", searching: "正在寻找材料", reading: "正在读取内容", synthesizing: "正在整理材料与看法", committing: "正在保存资料", completed: "本次寻找已完成", succeeded: "本次寻找已完成", skipped: "本次未继续寻找", cancelled: "本次已停止", cancelling: "正在停止", draining: "正在收回本次任务", failed: "本次寻找未完成", unavailable: "发现服务暂不可用", interrupted: "本次寻找已中断" };
  const biliStages = { device: "匿名设备准备", wbi_keys: "搜索参数准备", search: "视频搜索", video_info: "视频信息读取" };
  taskLabels.tieba_forum_check = "正在检测吧名";
  taskLabels.tieba_page = "正在读取贴吧楼层与回复";
  taskLabels.reflect = "正在消化见闻与修订看法";
  taskLabels.browser_search = "正在通过独立浏览器搜索";
  taskLabels.browser_read = "正在读取指定页面正文";
  taskLabels.browser_resource = "正在加载获准的浏览器资源";
  const reasons = { disabled: "自主发现尚未开启", outside_window: "当前不在活动时段", foreground_busy: "先等当前聊天结束", stock_sufficient: "已有足够的新资料", no_new_material: "本轮没有新增资料", search_no_candidates: "搜索已返回，但没有可读取的页面或帖子候选，尚未进入正文阅读", no_selected_material: "已读取材料，本轮未选入资料卡", quota_exhausted: "今日额度已经用完", source_revoked: "来源许可已关闭", cancelled: "本次任务已停止", no_platforms: "还没有开放平台", no_interests: "还没有可用于寻找内容的兴趣", no_credentials: "所选服务还没有配置可用凭据", timeout: "本次寻找达到时间限制" };
  const policyFields = [
    { key: "daily_queries", label: "每日查询次数", group: "daily", integer: true, nullable: true, shortLabel: "查询次数", unit: "次" },
    { key: "daily_models", label: "后台每日模型请求", group: "daily", integer: true, nullable: true, shortLabel: "后台模型请求", unit: "次" },
    { key: "daily_network", label: "每日网络尝试", group: "daily", integer: true, nullable: true, shortLabel: "网络尝试", unit: "次" },
    { key: "daily_response_mib", label: "每日响应正文（MiB）", group: "daily", nullable: true, shortLabel: "响应正文", unit: "MiB" },
    { key: "daily_shares", label: "每日自动带出资料次数", group: "daily", integer: true, nullable: true, shortLabel: "自动带出资料", unit: "次" },
    { key: "daily_memory_writes", label: "每日正式见闻条数", group: "daily", integer: true, nullable: true, shortLabel: "正式见闻", unit: "条" },
    { key: "chat_search_calls", label: "每轮对话搜索调用次数", group: "execution", integer: true, nullable: true, note: "相同请求复用已取得的结果，不重复访问。" },
    { key: "chat_search_results", label: "每次对话搜索返回材料", group: "execution", integer: true, nullable: true, note: "同时受本轮资料和来源依赖数量约束。" },
    { key: "tool_round_limit", label: "每轮聊天工具交互轮数", group: "execution", integer: true, nullable: true, note: "用完后不再执行工具，再用一次无工具答复交代实际结果。0 表示只答复、不执行工具。" },
    { key: "chat_search_timeout_seconds", label: "每次对话搜索等待期限（秒）", group: "execution", positive: true },
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
    { key: "observation_ttl_hours", label: "新见闻最长保留（小时）", group: "retention", integer: true, nullable: true, unlimitedLabel: "只按普通遗忘和来源许可", note: "0 暂停新增；修改不延长旧记忆。即使不设期限，普通遗忘与清除来源仍会生效。" },
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
    { key: "browser_resource_bytes", label: "浏览器单个资源响应（MiB）", group: "execution", positive: true, scale: 1048576 },
    { key: "read_response_bytes", label: "单份内容响应（MiB）", group: "execution", positive: true, scale: 1048576 },
    { key: "model_response_bytes", label: "单次模型响应（MiB）", group: "execution", positive: true, scale: 1048576 },
  ];
  const policyControls = new Map();
  let selectedDailyQuota = "daily_models";
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
    const providers = { native: "站内搜索", browser: "独立浏览器", bing_rss: "Bing RSS", brave: "Brave", searxng: "SearXNG" };
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
    if (value.rejected) parts.push(`地址、吧归属或必要字段不符合读取范围 ${value.rejected} 条`);
    if (value.unsupported) parts.push(`当前不支持的结果类型 ${value.unsupported} 条`);
    if (value.duplicates) parts.push(`重复结果 ${value.duplicates} 条`);
    return parts.join(" · ");
  }
  function diagnosticLabel(value) {
    if (!value || typeof value !== "object" || typeof value.stage !== "string") return "";
    if (value.kind === "search_results") return searchSummaryLabel(value);
    if (has(value, "kind")) return "";
    const isTieba = value.platform === "tieba";
    const stages = isTieba ? { search: "搜索公开主帖", read: "读取获准帖子文字" } : biliStages;
    if (!Object.prototype.hasOwnProperty.call(stages, value.stage)) return "";
    const parts = [`${isTieba ? "贴吧" : "B站"}阶段：${stages[value.stage]}`];
    if (has(value, "api_code")) parts.push(Number.isInteger(value.api_code) && value.api_code >= -2147483648 && value.api_code <= 2147483647 ? `API 返回码 ${value.api_code}` : "API 返回码未取得");
    if (has(value, "verification_required")) parts.push(value.verification_required === true ? "观察到额外验证信号" : value.verification_required === false ? "未观察到额外验证信号" : "验证信号未取得");
    return parts.join(" · ");
  }
  function status(message, kind = "") {
    for (const id of ["discovery-status", "discovery-settings-status"]) { text(id, message); $(id).dataset.kind = kind; }
  }
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
      input.setAttribute("form", "discovery-settings-form");
      input.step = field.integer ? "1" : "any"; input.disabled = true; input.required = true;
      label.htmlFor = input.id; row.append(input);
      const unlimited = field.nullable ? check(field.unlimitedLabel || "不限", false) : null;
      if (unlimited) {
        unlimited.input.setAttribute("form", "discovery-settings-form");
        unlimited.input.disabled = true; unlimited.input.setAttribute("aria-label", `${field.label}：${field.unlimitedLabel || "不限"}`);
        row.append(unlimited.row);
      }
      const note = node("p", field.note || "", "discovery-hint discovery-policy-note");
      note.id = `${input.id}-hint`; input.setAttribute("aria-describedby", note.id); note.classList.toggle("hidden", !field.note);
      const chatField = ["chat_search_calls", "chat_search_results", "tool_round_limit", "chat_search_timeout_seconds"].includes(field.key);
      wrapper.append(label, row, note); $(chatField ? "discovery-chat-limits" : groups[field.group]).append(wrapper);
      policyControls.set(field.key, { field, input, unlimited: unlimited?.input || null, note, supported: false });
    }
  }
  function mountDailyQuotaEditor() {
    const layout = $("discovery-daily-limits");
    const list = node("div", null, "discovery-daily-list");
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", "选择要编辑的额度");
    const editor = node("section", null, "discovery-daily-editor");
    editor.id = "discovery-daily-editor";
    editor.setAttribute("aria-labelledby", "discovery-daily-title");
    editor.innerHTML = `
      <h4 id="discovery-daily-title"></h4>
      <fieldset id="discovery-daily-modes">
        <legend>限额方式</legend>
        <div class="discovery-daily-choices">
          <label class="discovery-daily-choice"><input class="sr-only" type="radio" name="discovery-daily-mode" value="limit"><span>设上限</span></label>
          <label class="discovery-daily-choice"><input class="sr-only" type="radio" name="discovery-daily-mode" value="unlimited"><span>不限</span></label>
          <label class="discovery-daily-choice"><input class="sr-only" type="radio" name="discovery-daily-mode" value="off"><span>关闭</span></label>
        </div>
      </fieldset>
      <p id="discovery-daily-mode-note" class="discovery-hint"></p>`;
    layout.prepend(list, editor);
    const modeNote = $("discovery-daily-mode-note");

    for (const control of policyControls.values()) {
      const { field, input, unlimited } = control;
      if (field.group !== "daily") continue;
      const button = node("button", null, "discovery-daily-item");
      button.type = "button";
      button.setAttribute("aria-controls", editor.id);
      const amount = node("span", null, "discovery-daily-amount");
      button.append(node("span", field.shortLabel), amount);
      button.addEventListener("click", () => selectDailyQuota(control));
      list.append(button);

      // The existing number and unlimited checkbox remain the form's source of truth.
      const valueField = input.closest(".discovery-policy-field");
      valueField.classList.add("discovery-daily-value");
      valueField.querySelector("label").textContent = "每日上限";
      input.dataset.discoveryDailyQuota = field.key;
      input.inputMode = field.integer ? "numeric" : "decimal";
      input.setAttribute("aria-label", `${field.shortLabel}每日上限（${field.unit}）`);
      unlimited.closest("label").classList.add("hidden");
      input.after(node("span", field.unit, "discovery-daily-unit"));
      modeNote.before(valueField);
      control.daily = { button, amount, valueField, lastLimit: "" };
      input.addEventListener("blur", () => {
        if (field.key === selectedDailyQuota && dailyQuotaMode(control) === "off") renderDailyQuotaEditor();
      });
    }

    $("discovery-daily-modes").addEventListener("change", (event) => {
      const control = policyControls.get(selectedDailyQuota);
      if (!control.supported || !event.target.checked) return;
      const mode = event.target.value;
      control.unlimited.checked = mode === "unlimited";
      if (mode === "off") control.input.value = "0";
      if (mode === "limit" && control.input.valueAsNumber === 0) control.input.value = control.daily.lastLimit;
      recordSettingsEdit();
      renderDailyQuotaEditor();
    });
    renderDailyQuotaEditor();
  }
  function dailyQuotaMode(control) {
    if (control.unlimited.checked) return "unlimited";
    return control.input.valueAsNumber === 0 ? "off" : "limit";
  }
  function selectDailyQuota(control) {
    selectedDailyQuota = control.field.key;
    renderDailyQuotaEditor();
  }
  function renderDailyQuotaSummary(control) {
    const { daily, field, input, supported } = control;
    const mode = dailyQuotaMode(control);
    const value = input.valueAsNumber;
    if (!supported) daily.amount.textContent = snapshot ? "未提供" : "待读取";
    else if (mode === "unlimited") daily.amount.textContent = "不限";
    else if (mode === "off") daily.amount.textContent = "关闭";
    else if (!input.value) daily.amount.textContent = "待填写";
    else if (!input.validity.valid || field.integer && !Number.isSafeInteger(value)) daily.amount.textContent = "待校验";
    else daily.amount.textContent = `${Number.isInteger(value) ? quota(value) : input.value} ${field.unit}`;
  }
  function renderDailyQuotaEditor() {
    const selected = policyControls.get(selectedDailyQuota);
    const mode = dailyQuotaMode(selected);
    text("discovery-daily-title", selected.field.shortLabel);
    const modes = $("discovery-daily-modes");
    modes.classList.toggle("hidden", !selected.supported);
    for (const radio of modes.querySelectorAll("input")) radio.checked = radio.value === mode;
    const note = $("discovery-daily-mode-note");
    note.textContent = !selected.supported ? (snapshot ? "当前服务尚未提供此设置。" : "等待读取额度。")
      : mode === "unlimited" ? "该项不设每日上限。"
      : mode === "off" ? "此项额度为 0，暂停对应行为。" : "";
    note.classList.toggle("hidden", !note.textContent);
    for (const control of policyControls.values()) {
      if (!control.daily) continue;
      const active = control === selected;
      control.daily.button.setAttribute("aria-pressed", String(active));
      control.daily.valueField.classList.toggle("hidden", !active || !control.supported || mode !== "limit");
      renderDailyQuotaSummary(control);
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
      if (control.daily) control.daily.lastLimit = input.valueAsNumber > 0 ? input.value : "";
      if (unlimited) unlimited.checked = control.supported && value === null;
      note.textContent = control.supported ? field.note || "" : "当前服务尚未提供此设置。";
      note.classList.toggle("hidden", !note.textContent);
    }
    updatePolicyControls();
    renderDailyQuotaEditor();
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
    if (settingsPanel.contains(input)) showSettings(input.id || "discovery-settings-title");
    else showPreferences(true);
    for (let parent = input.parentElement; parent && parent !== panel && parent !== settingsPanel; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  }
  function quota(value) { return value === null ? "不限" : Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : "未提供"; }
  mountPolicyFields();
  mountDailyQuotaEditor();
  function currentRevision() { return snapshot?.settings?.revision; }
  function settingsConflict() { return settingsDirty && settingsRevision !== currentRevision(); }
  function selectSettingsSection(section, resetScroll = false) {
    if (!["use", "scope", "budget", "memory", "network"].includes(section)) section = "use";
    settingsSection = section;
    settingsPanel.querySelectorAll("[data-discovery-settings-pane]").forEach((pane) => pane.classList.toggle("hidden", pane.dataset.discoverySettingsPane !== section));
    settingsPanel.querySelectorAll(".discovery-settings-nav [data-discovery-settings-target]").forEach((button) => {
      if (button.dataset.discoverySettingsTarget === section) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    $("discovery-preferences-toggle").setAttribute("aria-expanded", String(settingsOpened && section === "scope"));
    if (resetScroll) {
      settingsPanel.closest(".modal-body").scrollTop = 0;
      settingsPanel.querySelector(".discovery-settings-panes").scrollTop = 0;
    }
  }
  function focusSetting(field) {
    const pane = field.closest("[data-discovery-settings-pane]");
    if (pane) selectSettingsSection(pane.dataset.discoverySettingsPane);
    for (let parent = field; parent && parent !== panel && parent !== settingsPanel; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    let target = field.id === "discovery-settings-box" ? $("discovery-settings-title")
      : field.tagName === "DETAILS" ? field.querySelector("summary") : field;
    const daily = policyControls.get(field.dataset.discoveryDailyQuota);
    if (daily) {
      selectDailyQuota(daily);
      if (!daily.supported) target = daily.daily.button;
      else if (dailyQuotaMode(daily) !== "limit") target = $("discovery-daily-modes").querySelector(":checked");
    }
    requestAnimationFrame(() => {
      target.focus({ preventScroll: true });
      (target.closest(".discovery-daily-choice") || target).scrollIntoView({ block: "nearest" });
    });
  }
  function showSettings(target = "discovery-settings-title") {
    if (!settingsOpened) window.AliceUI?.openSettings("discovery");
    const field = $(target); if (field) focusSetting(field);
  }
  function showPreferences(show, target = null) {
    if (show) { showSettings(target || "discovery-platforms-box"); return; }
    const restoreFocus = $("discovery-preferences").contains(document.activeElement) && settingsOpened;
    if (settingsSection === "scope") selectSettingsSection("use");
    if (restoreFocus) settingsPanel.querySelector('.discovery-settings-nav [data-discovery-settings-target="use"]').focus();
  }
  function resolveDrafts() {
    if (!settingsDirty && !interestDirty) return true;
    showPreferences(true, settingsDirty ? "discovery-settings-box" : "discovery-interest-topic");
    status("先保存或取消当前编辑，再更改寻找开关。草稿已保留。", "waiting");
    return false;
  }
  function setupIssue() {
    if (!snapshot) return null;
    const s = snapshot.settings, budget = snapshot.budget || {};
    if (settingsDirty) return { message: "活动设置尚未保存。", label: "继续编辑", target: "discovery-settings-box" };
    if (interestDirty) return { message: "兴趣尚未保存。", label: "继续编辑", target: "discovery-interest-topic" };
    if (!list(snapshot.platforms).some((p) => p.enabled && p.available && (!p.requires_third_party || p.allow_third_party)))
      return { message: "先选一个平台，告诉 Alice 去哪里找。", label: "选择平台", target: "discovery-platforms-box" };
    if (!list(snapshot.interests).some((i) => i.enabled && i.external_allowed))
      return { message: "添加一个兴趣，Alice 就知道找什么了。", label: "添加兴趣", target: "discovery-interest-topic" };
    if (!s.enabled) return { message: "寻找已暂停，已找到的资料仍可查看。", label: "开启发现", enable: true };
    if ([s.pool_capacity, s.source_pool_bytes, s.read_candidates, s.active_interest_capacity].some((value) => value === 0))
      return { message: "资料容量、读取数或启用兴趣数量设为 0，当前无法寻找。", label: "调整设置", target: "discovery-settings-box" };
    const exhausted = ["queries", "network", "bytes"].find((key) => Number.isFinite(budget[`${key}_limit`]) && budget[`${key}_used`] >= budget[`${key}_limit`]);
    if (exhausted) return { message: "今日寻找额度已用完，可以等下次额度恢复，或自行调整。", label: "查看额度", target: `discovery-policy-${({ queries: "daily_queries", network: "daily_network", bytes: "daily_response_mib" })[exhausted]}` };
    return null;
  }
  function toggleEnabled() {
    if (!fresh || working || !snapshot || !resolveDrafts()) return;
    const s = snapshot.settings;
    if (!s.enabled && s.daily_models !== 0 && !s.allow_paid) {
      showPreferences(true, "discovery-paid");
      status("请确认模型使用许可，或在每日额度中关闭后台模型请求，再开启发现。", "waiting");
      return;
    }
    mutate("settings", { settings: { enabled: !s.enabled } }, "正在更新寻找状态…", s.enabled ? "已暂停发现。已找到的资料仍可查看。" : "已开启发现，将按已保存的平台、兴趣、时段与额度寻找。");
  }
  function renderOverview() {
    const s = snapshot?.settings;
    if (!s) return;
    document.querySelector(".discovery-overview")?.setAttribute("data-enabled", s.enabled ? "on" : "off");
    const preview = $("discovery-learning-items");
    preview.replaceChildren();
    for (const item of list(snapshot.learning_preview)) {
      const action = {keep:'继续保留',warehouse:'移入仓库',promote:'保留到长期层',expire:'将遗忘',dependent:'随必要依据遗忘'}[item.retention_action] || '等待复核';
      const row = node('p', `${item.title} · ${action} · 建议保留${item.compression}`);
      row.append(node('span', ` · ${item.retention_reason || item.reason || '尚未记录维护理由'}`));
      if (item.reason) row.append(node('span', ` · 价值依据：${item.reason}`));
      row.append(button('查看依据', () => window.AliceChatSearch?.showMemory(item.memory_id, snapshot.dataset_id), 'link'));
      preview.append(row);
    }
    if (!preview.children.length) preview.append(node('p','暂无已分级见闻。'));
    const platforms = list(snapshot.platforms).filter((p) => p.enabled).map((p) => p.name || p.label);
    text("discovery-settings-platforms", `已开放平台：${platforms.length ? platforms.join("、") : "暂无"}。`);
    const interests = list(snapshot.interests).filter((i) => i.enabled && i.external_allowed).map((i) => i.topic);
    const names = (rows, empty) => rows.length ? rows.slice(0, 3).join("、") + (rows.length > 3 ? ` 等 ${rows.length} 项` : "") : empty;
    text("discovery-context", `${names(platforms, "未选平台")} · ${names(interests, "未添加兴趣")}`);
    const tiebaEnabled = list(snapshot.platforms).some((p) => p.id === "tieba" && p.enabled);
    text("discovery-tieba-scope", tiebaEnabled ? tiebaScopeLabel(s) : "");
    $("discovery-tieba-scope").classList.toggle("hidden", !tiebaEnabled);
    text("discovery-mode", s.enabled ? `${s.background_enabled ? `自动寻找已开启 · ${s.window_start === s.window_end ? "全天" : `${s.window_start}–${s.window_end}（北京时间）`}` : "自动寻找已关闭"} · 聊天查找${s.chat_search_enabled ? "已开启" : "已关闭"} · 手动找一轮不限时段` : "内容访问已暂停");
    text("discovery-toggle-enabled", s.enabled ? "暂停发现" : "开启发现");
  }
  function updateControls() {
    const ready = fresh && !!dataset && Number.isInteger(currentRevision()) && !working && !stopped;
    for (const root of [panel, settingsPanel]) root.querySelectorAll("[data-write]").forEach((n) => { n.disabled = !ready || n.dataset.ineligible === "true"; });
    $("discovery-refresh").disabled = working || stopped;
    $("discovery-settings-refresh").disabled = working || stopped;
    $("discovery-settings-reset").disabled = working || !snapshot;
    $("discovery-interest-reset").disabled = working;
    const forumCheck = snapshot?.tieba_forum_check;
    const checking = forumCheck && activeStates.has(forumCheck.state);
    $("discovery-interest-forums").disabled = !has(snapshot, "tieba_forum_check");
    $("discovery-forum-check").disabled = !ready || !has(snapshot, "tieba_forum_check") || checking || !preferredForumNames().length;
    $("discovery-forum-check").textContent = checking ? "正在检测…" : "检测吧名";
    renderForumCheck();
    $("discovery-settings-save").disabled = !ready || settingsConflict();
    const task = snapshot?.task || {}, active = activeStates.has(task.state);
    const issue = setupIssue();
    $("discovery-run").disabled = !ready || active || !!issue;
    $("discovery-run").textContent = active ? "正在寻找…" : "现在找一轮";
    $("discovery-run").title = issue?.message || "按已保存的兴趣和平台寻找，不修改任何额度";
    const reconnect = !fresh && !working && !stopped;
    $("discovery-next").classList.toggle("hidden", !reconnect && (!issue || !fresh));
    text("discovery-next-text", reconnect ? "尚未取得最新状态，重新读取后可以继续操作。" : issue?.message || "");
    text("discovery-next-action", reconnect ? "重新读取" : issue?.label || "");
    $("discovery-next-action").disabled = reconnect ? false : !ready;
    text("discovery-preferences-toggle", interestDirty ? "平台与兴趣 · 未保存" : "平台与兴趣");
    $("discovery-preferences-toggle").setAttribute("aria-expanded", String(settingsOpened && settingsSection === "scope"));
    text("discovery-settings-open", settingsDirty ? "联网搜索设置 · 未保存" : "联网搜索设置");
    $("discovery-cancel").classList.toggle("hidden", !active);
    $("discovery-cancel").disabled = !ready || !task.id || ["draining", "cancelling"].includes(task.state);
    composerCancel.disabled = working || stopped || $("input").disabled;
    const dirtyNote = $("discovery-settings-draft");
    dirtyNote.classList.toggle("hidden", !settingsDirty);
    dirtyNote.textContent = settingsConflict() ? "设置已在别处更新。你的草稿仍在；请先重新载入，再保存。" : "有尚未保存的设置。";
    $("discovery-interest-draft").classList.toggle("hidden", !interestDirty);
    text("discovery-interest-draft", interestDirty && interestRevision !== currentRevision() ? "设置已更新。请取消编辑并重新选择兴趣，避免覆盖新内容。" : "兴趣修改尚未保存。保存不会直接发送聊天消息。");
    const saved = snapshot?.settings, cost = $("discovery-settings-cost"), current = fresh ? "当前生效" : "上次读取";
    cost.dataset.paid = String(!!saved?.allow_paid);
    cost.textContent = !saved ? "尚未读取后台模型使用许可。"
      : !saved.allow_paid ? `${current}：后台模型未授权。`
      : !has(saved, "daily_models") ? `${current}：后台模型已授权，每日额度尚未提供。`
      : saved.daily_models === 0 ? `${current}：后台模型已授权，每日额度为 0（暂停模型）。`
      : `${current}：后台模型已授权 · 每日请求${saved.daily_models === null ? "不限" : `最多 ${quota(saved.daily_models)} 次`}（不是金额上限）。`;
  }
  function tiebaScopeSupport() {
    const s = snapshot?.settings || {};
    return {
      replies: typeof s.tieba_read_replies === "boolean",
      limit: Number.isSafeInteger(s.tieba_reply_limit) && s.tieba_reply_limit > 0,
      nested: typeof s.tieba_read_nested === "boolean",
      textLimit: Number.isSafeInteger(s.tieba_text_limit) && s.tieba_text_limit >= 80,
    };
  }
  function tiebaScopeLabel(settings) {
    const support = tiebaScopeSupport();
    const reading = support.replies ? settings.tieba_read_replies
      ? support.limit ? `主帖 + ${support.nested ? "分页回帖" : "首页回帖"}（合计最多 ${quota(settings.tieba_reply_limit)} 条${support.nested && settings.tieba_read_nested ? "，含楼中楼节选" : ""}）` : "回帖数量待确认"
      : "仅主帖" : "回帖设置未提供";
    return `贴吧阅读：${reading} · 优先吧可按兴趣设置`;
  }
  function tiebaScopeFields() {
    const support = tiebaScopeSupport();
    const repliesSupported = support.replies && support.limit;
    $("discovery-tieba-read-replies").disabled = !repliesSupported;
    const readingReplies = repliesSupported && $("discovery-tieba-read-replies").checked;
    $("discovery-tieba-reply-limit").disabled = !readingReplies;
    $("discovery-tieba-reply-limit").required = readingReplies;
    $("discovery-tieba-read-nested").disabled = !readingReplies || !support.nested;
    $("discovery-tieba-text-limit").disabled = !support.textLimit;
    $("discovery-tieba-text-limit").required = support.textLimit;
    const fullySupported = repliesSupported && support.nested && support.textLimit;
    text("discovery-tieba-scope-support", !fullySupported ? "当前运行服务尚未提供完整的分页与楼中楼设置，请重启服务后使用新功能；不支持的字段不会提交。" : "");
    $("discovery-tieba-scope-support").classList.toggle("hidden", fullySupported);
  }
  function providerFields() {
    tiebaScopeFields();
    const provider = $("discovery-provider").value, keySupported = ["brave", "searxng"].includes(provider);
    const networkSupported = has(snapshot?.settings, "network_mode") && ["system", "direct"].includes(snapshot.settings.network_mode);
    $("discovery-network-mode").disabled = !networkSupported;
    if (!networkSupported) $("discovery-network-mode").value = "";
    text("discovery-network-hint", networkSupported
      ? "适用于公开平台、Bing RSS 和 Brave 的请求。"
      : "当前服务尚未提供网络路由设置，此项不会提交。");
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
    text("discovery-provider-hint", provider === "native" ? "各平台按所选方式访问，仍需单独开放。"
      : provider === "bing_rss" ? "获准的平台将搜索词交给 Bing 的公开 RSS。平台许可在「平台与兴趣」单独设置。"
      : provider === "brave" ? "获准的平台将搜索词交给 Brave。需要服务密钥，费用以账单为准。"
      : "获准的平台将搜索词交给指定的 SearXNG 服务。请核对服务地址与使用许可。");
    text("discovery-bilibili-hint", !has(snapshot?.settings, "bilibili_access") ? "当前服务尚未提供 B站访问方式。"
      : $("discovery-bilibili-access").value === "browser" ? "搜索与读取视频简介。如需登录，请在平台卡打开独立浏览器。"
      : $("discovery-bilibili-access").value === "native" ? "搜索公开视频，读取视频信息和简介。"
      : "由选定服务查找视频链接，读取信息和简介。请在 B站平台卡单独授权。");
    text("discovery-zhihu-hint", $("discovery-zhihu-access").value === "browser"
      ? "在独立浏览器搜索并读取指定回答。需要时请亲自登录。"
      : "通过第三方服务发现链接，再尝试读取指定正文。");
    const tiebaSupported = has(snapshot?.settings, "tieba_access") && ["native", "third_party"].includes(snapshot.settings.tieba_access);
    $("discovery-tieba-access").disabled = !tiebaSupported;
    if (!tiebaSupported) $("discovery-tieba-access").value = "";
    text("discovery-tieba-hint", !tiebaSupported ? "当前服务尚未提供贴吧访问方式，此项不会提交。"
      : $("discovery-tieba-access").value === "native" ? "通过贴吧查找公开帖子。回帖范围在下方设置。"
      : "搜索词交给选定服务，需在贴吧平台卡单独授权。回帖范围在下方设置。");
    text("discovery-key-state", snapshot?.settings?.provider_key_present ? "已保存密钥；内容不会回显。" : "尚未保存搜索服务密钥。SearXNG 是否需要密钥取决于该服务。");
  }
  function fillSettings(force = false) {
    const s = snapshot?.settings; if (!s || settingsDirty && !force) return;
    settingsDirty = false; settingsRevision = s.revision;
    $("discovery-enabled").checked = !!s.enabled;
    $("discovery-background").checked = !!s.background_enabled;
    $("discovery-chat-search").checked = !!s.chat_search_enabled;
    $("discovery-remember-observations").checked = !!s.remember_observations;
    $("discovery-start").value = s.window_start || ""; $("discovery-end").value = s.window_end || "";
    fillPolicySettings(s);
    $("discovery-paid").checked = !!s.allow_paid;
    $("discovery-chat-interests").checked = !!s.allow_chat_interests;
    $("discovery-share-qq").checked = !!s.share_in_qq;
    $("discovery-provider").value = ["native", "bing_rss", "brave", "searxng"].includes(s.provider_kind) ? s.provider_kind : "native";
    $("discovery-network-mode").value = ["system", "direct"].includes(s.network_mode) ? s.network_mode : "";
    $("discovery-bilibili-access").disabled = !has(s, "bilibili_access");
    $("discovery-bilibili-access").value = ["native", "third_party", "browser"].includes(s.bilibili_access) ? s.bilibili_access : "native";
    $("discovery-zhihu-access").disabled = !has(s, "zhihu_access");
    $("discovery-zhihu-access").value = s.zhihu_access === "browser" ? "browser" : "third_party";
    $("discovery-tieba-access").value = ["native", "third_party"].includes(s.tieba_access) ? s.tieba_access : "";
    const tiebaSupport = tiebaScopeSupport();
    $("discovery-tieba-read-replies").checked = tiebaSupport.replies && s.tieba_read_replies;
    $("discovery-tieba-reply-limit").value = tiebaSupport.limit ? String(s.tieba_reply_limit) : "";
    $("discovery-tieba-read-nested").checked = tiebaSupport.nested && s.tieba_read_nested;
    $("discovery-tieba-text-limit").value = tiebaSupport.textLimit ? String(s.tieba_text_limit) : "";
    $("discovery-endpoint").value = s.provider_endpoint || ""; $("discovery-key").value = ""; $("discovery-clear-key").checked = false;
    $("discovery-engines").value = s.provider_engines || "duckduckgo";
    providerFields();
  }
  function renderPlatforms() {
    const rows = list(snapshot.platforms), signature = JSON.stringify([rows, snapshot.settings.bilibili_access, snapshot.settings.zhihu_access, snapshot.settings.tieba_access, snapshot.settings.tieba_read_replies, snapshot.settings.tieba_reply_limit, snapshot.settings.tieba_read_nested, snapshot.settings.tieba_text_limit]);
    if (signature === platformSignature) return; platformSignature = signature;
    const target = $("discovery-platforms"); target.replaceChildren();
    if (!rows.length) { target.append(node("p", "暂时没有可配置的平台。", "discovery-hint")); return; }
    for (const p of [...rows].sort((a, b) => Number(b.enabled) - Number(a.enabled))) {
      const biliNative = p.id === "bilibili" && snapshot.settings.bilibili_access === "native";
      const tiebaNative = p.id === "tieba" && snapshot.settings.tieba_access === "native";
      const needsThirdParty = typeof p.requires_third_party === "boolean" ? p.requires_third_party : !biliNative && !tiebaNative && (p.id !== "wikipedia" || !!p.third_party_notice);
      const browserAccess = !!p.browser && snapshot.settings[`${p.id}_access`] === "browser";
      const card = node("article", null, "discovery-platform"), head = node("div", null, "discovery-item-head");
      const name = p.name || p.label || "内容平台";
      const detail = node("details");
      detail.append(node("summary", p.browser ? "访问、登录与资料" : "访问方式与资料"), node("p", p.description || "仅使用获准的公开内容。", "discovery-hint"));
      const toggle = button(p.enabled ? "停用" : "开启", () => {
        if (!resolveDrafts()) return;
        if (!p.enabled && needsThirdParty && !p.allow_third_party) {
          detail.open = true; third.input.focus();
          status(`先确认${name}的第三方搜索许可，再点“允许并开启”。`, "waiting"); return;
        }
        mutate("platform", { id: p.id, enabled: !p.enabled, allow_third_party: !!p.allow_third_party }, "正在更新平台…", p.enabled ? `已停用${name}。` : `已开启${name}，将用于之后的寻找。`);
      }, "btn ghost discovery-platform-toggle");
      toggle.setAttribute("aria-label", `${toggle.textContent}${name}`);
      if (!p.available && !p.enabled) toggle.dataset.ineligible = "true";
      const title = node("div", null, "discovery-platform-title");
      const state = node("div", p.enabled ? (p.available ? "已开启" : "已开启 · 暂不可用") : p.available ? "未开启" : "暂不可用", "discovery-platform-state");
      state.dataset.active = String(p.enabled && p.available);
      title.append(node("h3", name), state);
      head.append(title, toggle);
      card.append(head);
      if (p.browser) {
        const browserNote = node("p", browserAccess ? `${p.browser.message}。${p.browser.last_message}` : "也可使用独立本机浏览器，需要登录时由你亲自完成。", "discovery-hint");
        (browserAccess ? card : detail).append(browserNote);
        const browserActions = node("div", null, "discovery-actions");
        if (!browserAccess) {
          const useBrowser = button("使用独立浏览器", () => {
            if (!resolveDrafts()) return;
            mutate("settings", { settings: { [`${p.id}_access`]: "browser" } }, "正在设置访问方式…", `${name}已改用独立浏览器，平台开关保持当前选择。`);
          }, "btn ghost");
          if (!p.browser.installed) useBrowser.dataset.ineligible = "true";
          browserActions.append(useBrowser);
        }
        browserActions.append(button("打开独立浏览器 / 登录", () => {
          mutate("browser_open", { platform: p.id }, "正在打开独立浏览器…", "已打开独立窗口。需要时请亲自登录，完成后关闭这个窗口，再点“现在找一轮”。");
        }, "btn ghost"));
        detail.append(browserActions, node("p", "登录保存在本机独立浏览器。完成后关闭该窗口再寻找。后台搜索和阅读遇到验证时会停止。", "discovery-hint"));
      }
      if (p.id === "tieba") {
        detail.append(node("p", tiebaScopeLabel(snapshot.settings), "discovery-hint"));
        const scope = node("button", "设置回帖阅读", "link"); scope.type = "button";
        scope.addEventListener("click", () => showPreferences(true, "discovery-tieba-scope-box"));
        detail.append(scope);
      }
      if (words(p.readable_types)) detail.append(node("p", `可读范围：${words(p.readable_types)}`, "discovery-hint"));
      if (p.discovery_method) detail.append(node("p", `发现方式：${({ native: "平台公开入口", bing_rss: "Bing RSS（免费，无需密钥）", brave: "Brave 搜索服务", searxng: "SearXNG 搜索服务" })[p.discovery_method] || words(p.discovery_method)}`, "discovery-hint"));
      if (p.reason && !browserAccess) (p.available ? detail : card).append(node("p", p.reason, "discovery-hint"));
      const third = check("允许所选第三方搜索服务寻找此平台的内容", p.allow_third_party);
      if (needsThirdParty || p.allow_third_party) {
        if (p.third_party_notice) detail.append(node("p", p.third_party_notice, "discovery-hint"));
        const permission = button("保存搜索许可", () => {
          if (!resolveDrafts()) return;
          if (!third.input.checked && !p.allow_third_party) { status("请勾选许可后再开启；未勾选不会开放第三方搜索。", "waiting"); return; }
          const enabled = needsThirdParty ? third.input.checked && (p.enabled || !p.allow_third_party) : p.enabled;
          mutate("platform", { id: p.id, enabled, allow_third_party: third.input.checked }, "正在保存搜索许可…", enabled ? `${name}搜索许可已保存，平台已开启。` : "搜索许可已保存，平台保持关闭。");
        });
        const labelPermission = () => {
          permission.textContent = !third.input.checked && p.allow_third_party ? needsThirdParty && p.enabled ? "撤销搜索许可并停用" : "撤销搜索许可" : !p.enabled && !p.allow_third_party ? "允许并开启" : "保存搜索许可";
          permission.dataset.ineligible = String(third.input.checked === !!p.allow_third_party || third.input.checked && !p.available);
          updateControls();
        };
        third.input.addEventListener("change", labelPermission);
        detail.append(third.row, permission); labelPermission();
      }
      const actions = node("div", null, "discovery-actions");
      actions.append(button("清除此平台资料", () => {
        if (window.confirm(`清除${p.name || p.label || "此平台"}的资料及依赖判断？相关引用会失效；这不会关闭平台。`)) mutate("clear", { platform_id: p.id }, "正在清除平台资料…", "平台资料已清除，相关引用不再用于聊天。");
      }, "link"));
      if (["bilibili", "zhihu", "tieba"].includes(p.id)) {
        const accessId = `discovery-${p.id}-access`;
        const chooseAccess = node("button", `设置${name}访问方式`, "link"); chooseAccess.type = "button";
        chooseAccess.addEventListener("click", () => showPreferences(true, accessId));
        actions.append(chooseAccess);
      }
      detail.append(actions); card.append(detail);
      target.append(card);
    }
  }
  function canonicalForumName(value) {
    return value.trim().normalize("NFC").replace(/[A-Z]/g, (letter) => letter.toLowerCase());
  }
  function preferredForumNames() {
    return [...new Set($("discovery-interest-forums").value.split(/\r?\n/).map(canonicalForumName).filter(Boolean))];
  }
  function validForumNames(names) {
    return names.every((name) => Array.from(name).length <= 128 && !/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(name));
  }
  function readyForumCheck(name) {
    const check = snapshot?.tieba_forum_check;
    if (!check || check.state !== "completed" || check.revision !== currentRevision()
        || check.revision !== interestRevision || !list(check.forums).includes(name)) return false;
    return list(check.results).some((row) => row.requested === name && row.forum_name === name && row.status === "ready"
      && /^[1-9][0-9]*$/.test(String(row.forum_id)) && Number.isSafeInteger(row.thread_count) && row.thread_count > 0);
  }
  function renderForumCheck() {
    const names = preferredForumNames(), supported = has(snapshot, "tieba_forum_check"), check = snapshot?.tieba_forum_check;
    const signature = JSON.stringify([names, supported, check, currentRevision(), interestRevision, editingPreferredForums]);
    if (signature === forumCheckSignature) return;
    forumCheckSignature = signature;
    const target = $("discovery-forum-check-results"); target.replaceChildren();
    if (!supported) { target.append(node("p", "当前服务尚未提供优先吧检测；此项不可修改，也不会提交。", "discovery-hint")); return; }
    if (!names.length) { target.append(node("p", "未设置优先吧，将按兴趣寻找。", "discovery-hint")); return; }
    const current = check?.state === "completed" && check.revision === currentRevision() && check.revision === interestRevision;
    const active = check && activeStates.has(check.state);
    const labels = { ready: "已确认，取得公开帖子", empty: "已确认吧，本次未取得公开帖子", mismatch: "返回吧名与输入不一致", unverified: "本次未能确认" };
    const rows = node("ul");
    for (const name of names) {
      const row = list(check?.results).find((item) => item.requested === name && list(check.forums).includes(name));
      const line = node("li"), saved = editingPreferredForums.includes(name);
      const pending = row && active && row.status === "unverified" && !time(row.checked_at)
        && ["", "not_checked", "checking"].includes(row.code || "");
      const pendingLabel = row?.code === "checking" || !row?.code && check?.state === "running" ? "正在检测" : "等待检测";
      const label = pending ? pendingLabel : row ? labels[row.status] || "本次未能确认" : saved ? "已保存的优先吧，未修改时无需重新检测" : "尚未检测";
      line.append(node("strong", name), node("p", label, "discovery-hint"));
      if (row) {
        const actual = typeof row.forum_name === "string" ? row.forum_name : "";
        if (!pending) {
          const count = row.status === "unverified" ? "未确认" : Number.isSafeInteger(row.thread_count) ? row.thread_count : "未取得";
          line.append(node("p", `实际吧名：${actual || "未取得"} · 帖子数：${count} · 检测于 ${stamp(row.checked_at)}`, "discovery-hint"));
        }
        const link = safeLink(`https://tieba.baidu.com/f?kw=${encodeURIComponent(actual || name)}`, actual ? "打开返回的吧 ↗" : "打开该吧核对 ↗");
        if (link) line.append(link);
        if (!pending && !current && !saved) line.append(node("p", "此结果尚不能用于保存；需本次检测完成且设置版本一致。", "discovery-hint"));
      }
      rows.append(line);
    }
    target.append(rows);
    const newNames = names.filter((name) => !editingPreferredForums.includes(name));
    const note = active ? "吧名检测正在进行，结果会自动更新；检测期间可以继续编辑草稿。"
      : newNames.some((name) => !readyForumCheck(name)) ? "新增吧名尚未全部确认。请检测并核对返回名称；未取得帖子或名称不一致的项目不能直接保存。"
      : "这些优先吧可以保存。之后没有合适内容时，仍允许寻找其他吧。";
    target.append(node("p", note, "discovery-hint"));
  }
  function resetInterest() {
    editingInterest = null; interestDirty = false; interestRevision = currentRevision();
    editingPreferredForums = [];
    $("discovery-interest-form").reset(); $("discovery-interest-enabled").checked = true;
    $("discovery-interest-options").open = false;
    $("discovery-interest-forums-box").open = false;
    text("discovery-interest-save", "添加兴趣"); $("discovery-interest-reset").classList.add("hidden"); updateControls();
  }
  function editInterest(item) {
    if (working) return;
    editingInterest = item.id; interestDirty = true; interestRevision = currentRevision();
    editingPreferredForums = list(item.preferred_forums).filter((name) => typeof name === "string").map(canonicalForumName);
    $("discovery-interest-forums").value = editingPreferredForums.join("\n");
    $("discovery-interest-forums-box").open = editingPreferredForums.length > 0;
    $("discovery-interest-topic").value = item.topic || ""; $("discovery-interest-summary").value = item.summary === item.topic ? "" : item.summary || "";
    $("discovery-interest-options").open = !!$("discovery-interest-summary").value || !item.enabled;
    $("discovery-interest-external").checked = !!item.external_allowed; $("discovery-interest-enabled").checked = !!item.enabled;
    text("discovery-interest-save", "保存兴趣"); $("discovery-interest-reset").classList.remove("hidden"); updateControls();
    showPreferences(true, "discovery-interest-topic");
  }
  function renderInterests() {
    const rows = list(snapshot.interests), signature = JSON.stringify(rows);
    if (signature === interestSignature) return; interestSignature = signature;
    const target = $("discovery-interests"); target.replaceChildren();
    if (!rows.length) target.append(node("p", "暂无已添加的兴趣。", "discovery-hint"));
    for (const item of rows) {
      const card = node("article", null, "discovery-interest"), head = node("div", null, "discovery-item-head");
      head.append(node("h3", item.topic || "未命名兴趣"), node("span", !item.external_allowed ? "等待允许外发" : item.enabled ? "正在使用" : "已停用", `discovery-pill${item.enabled && item.external_allowed ? "" : " muted"}`));
      card.append(head);
      if (item.summary && item.summary !== item.topic) card.append(node("p", item.summary, "discovery-hint"));
      if (list(item.preferred_forums).length) card.append(node("p", `优先吧：${words(item.preferred_forums)}；无合适内容时可找其他吧。`, "discovery-hint"));
      if (item.origin === 'conversation_inferred') {
        const attitude = {interested:'感兴趣',likes:'喜欢',dislikes:'不喜欢',concerned:'关注',curious:'好奇',neutral:'中立'}[item.attitude] || '待明确';
        const evidence = node("details", null, "discovery-setting-fold");
        evidence.append(node("summary", `聊天依据 · ${attitude}`), node("p", `置信度 ${Math.round((item.confidence || 0) * 100)}% · ${list(item.basis_user_turn_ids).length} 条用户表达`, "discovery-hint"));
        if (item.evidence_reason) evidence.append(node("p", item.evidence_reason, "discovery-hint"));
        for (const quote of list(item.evidence_quotes)) evidence.append(node("p", `表达依据：“${quote}”`, "discovery-hint"));
        card.append(evidence);
      }
      if (item.origin === "conversation_candidate") card.append(node("p", item.external_allowed ? "来自聊天，已允许搜索。" : "来自聊天，确认后才会用于搜索。", "discovery-hint"));
      const actions = node("div", null, "discovery-actions");
      actions.append(button("修改", () => editInterest(item)), button(item.enabled ? "停用兴趣" : "启用兴趣", () => {
        if (!item.external_allowed && !item.enabled) { editInterest(item); status("请先核对概要并明确允许用于搜索。", "waiting"); return; }
        const payload = { id: item.id, topic: item.topic, summary: item.summary, enabled: !item.enabled, external_allowed: !!item.external_allowed };
        if (has(snapshot, "tieba_forum_check")) payload.preferred_forums = list(item.preferred_forums);
        mutate("interest", payload, "正在更新兴趣…", item.enabled ? "已停用这个兴趣；旧的待用推荐和在途结果将失效。" : "已启用这个兴趣。");
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
  function discussUnavailableReason(item, interest, platform) {
    if (typeof item.discuss_unavailable_reason === "string" && item.discuss_unavailable_reason.trim()) return item.discuss_unavailable_reason.trim();
    const known = {
      interest_removed: "关联兴趣已删除，这份资料暂不能用于讨论。",
      interest_disabled: "关联兴趣已停用，这份资料暂不能用于讨论。",
      interest_permission_required: "关联兴趣尚未允许用于搜索，这份资料暂不能用于讨论。",
      interest_changed: "这份资料对应的兴趣已修改，旧资料不再用于讨论。",
      platform_disabled: "来源平台已关闭，这份资料暂不能用于讨论。",
      card_disabled: "这份资料已停用，不再用于讨论。",
    };
    if (has(known, item.unavailable_reason)) return known[item.unavailable_reason];
    if (interest && !interest.enabled) return known.interest_disabled;
    if (interest && !interest.external_allowed) return known.interest_permission_required;
    if (platform && !platform.enabled) return known.platform_disabled;
    return "这份资料已暂停或不再符合当前使用条件，暂不能用于讨论。";
  }
  function renderCards() {
    const all = list(snapshot.cards), rows = all.filter((c) => !expired(c));
    const signature = JSON.stringify([rows,
      list(snapshot.interests).map((item) => [item.id, item.enabled, item.external_allowed]),
      list(snapshot.platforms).map((item) => [item.id, item.enabled])]);
    text("discovery-card-count", `${rows.length} 份当前资料`);
    if (signature === cardSignature) return; cardSignature = signature;
    const target = $("discovery-cards"); target.replaceChildren();
    if (!rows.length) {
      target.append(node("p", "还没有资料。按上方提示准备好后，点“现在找一轮”；找到的内容会自动出现在这里。", "discovery-empty"));
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
      card.append(node("p", [words(item.coverage) || "阅读范围未提供，不视为读完原文。", item.share_state].filter(Boolean).join(" · "), "discovery-card-meta"));
      cardPart(card, "实际读到的内容", item.summary || item.content);
      const actions = node("div", null, "discovery-actions");
      const discuss = button("聊聊这条", () => discussCard(item), "btn primary");
      const interest = list(snapshot.interests).find((i) => i.id === item.interest_id);
      const unavailable = item.can_discuss === false || interest && (!interest.enabled || !interest.external_allowed);
      let unavailableNote = null;
      if (unavailable) {
        discuss.dataset.ineligible = "true";
        const reason = discussUnavailableReason(item, interest, platform);
        discuss.title = reason;
        unavailableNote = node("p", `暂不能讨论：${reason}`, "discovery-unavailable-note");
        unavailableNote.id = `discovery-discuss-reason-${target.children.length}`;
        discuss.setAttribute("aria-describedby", unavailableNote.id);
      } else discuss.title = "准备下一轮讨论草稿，消息由你发送";
      const more = button("多找这类", () => feedback(item, "more")); more.title = "提高这类内容之后的寻找优先级，不会立刻发起搜索";
      actions.append(discuss, more);
      if (item.trigger_kind === "chat") { more.dataset.ineligible = "true"; more.title = "本次查找没有绑定长期兴趣"; }
      const links = list(item.memory_links);
      if (links.length) {
        for (const memory of links) actions.append(button(`查看记忆：${memory.title}`, () => window.AliceChatSearch?.showMemory(memory.id, dataset)));
      } else {
        const remember = button("记住这份见闻", () => mutate("remember_card", { card_id: item.id }, "正在保存短证据与出处…", "已保存为普通见闻记忆，可从资料卡或记忆面板查看。"));
        if (unavailable || !snapshot.settings.remember_observations) { remember.dataset.ineligible = "true"; remember.title = unavailable ? "资料已不可用" : "先在设置中开启择要保存见闻"; }
        actions.append(remember);
      }
      card.append(actions);
      if (unavailableNote) card.append(unavailableNote);
      const detail = node("details"); detail.append(node("summary", "看看 Alice 的想法与原始依据"));
      cardPart(detail, "为什么值得聊", item.reason || item.recommendation_reason);
      cardPart(detail, "Alice 的暂时判断", item.interpretation, "discovery-interpretation");
      cardPart(detail, "还不能确定", item.uncertainty || item.open_question);
      cardPart(detail, "可以继续聊", item.question);
      cardPart(detail, "作者的说法", item.author_view);
      cardPart(detail, "取得的依据", item.evidence_excerpt || item.excerpt);
      cardPart(detail, "找到时间与保留期限", `${item.created_at ? stamp(item.created_at) : "发现时间未记录"}${item.expires_at ? ` · 保留至 ${stamp(item.expires_at)}` : ""}`);
      const source = safeLink(item.url || item.source_url, "打开原始来源 ↗");
      detail.append(source || node("p", "当前没有可打开的来源链接。", "discovery-hint")); card.append(detail);
      const management = node("details"); management.append(node("summary", "少分享、停用或删除"));
      const secondary = node("div", null, "discovery-actions discovery-secondary");
      if (item.interest_id) secondary.append(button("少分享", () => feedback(item, "less"), "link"), button("停用相关兴趣", () => feedback(item, "stop_interest"), "link"));
      secondary.append(button("不再看此来源", () => feedback(item, "stop_source"), "link"), button("删除资料", () => {
        if (window.confirm("删除这份资料及依赖它的见闻和观点？相关聊天引用将失效，独立的用户表达会保留。")) mutate("delete", { card_id: item.id }, "正在删除资料…", "资料已删除，相关判断与引用不再使用。");
      }, "link"));
      management.append(secondary); card.append(management); target.append(card);
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
    text("discovery-tieba-scope", ""); $("discovery-tieba-scope").classList.add("hidden");
    text("discovery-storage-usage", "尚未读取来源内容池用量。");
    text("discovery-sharing-warning", ""); $("discovery-sharing-warning").classList.add("hidden");
    text("discovery-context", "正在读取寻找范围…"); text("discovery-mode", ""); $("discovery-next").classList.add("hidden");
    text("discovery-settings-platforms", "尚未读取平台状态。");
    showPreferences(false);
    providerFields(); paintDiscussion();
  }
  function adopt(data) {
    if (!data.settings || !Number.isInteger(data.settings.revision) || !data.dataset_id) throw new Error("内容发现响应不完整，请刷新确认。");
    if (dataset && dataset !== data.dataset_id) {
      clearIdentity();
    }
    dataset = data.dataset_id; snapshot = data; fresh = true;
    window.dispatchEvent(new CustomEvent("alice:discovery-state", { detail: data }));
    const sharingWarning = typeof data.sharing_warning === "string" ? data.sharing_warning : "";
    text("discovery-sharing-warning", sharingWarning); $("discovery-sharing-warning").classList.toggle("hidden", !sharingWarning);
    if (!interestDirty) interestRevision = currentRevision();
    fillSettings(); providerFields(); renderPlatforms(); renderInterests(); renderCards(); renderOverview(); updateDiscussion(data);
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
    status(s.enabled ? `${label}${reason ? ` · ${reason}` : ""}` : "发现已暂停。你可以继续查看已有资料。", task.state === "failed" ? "error" : "");
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
    if ((opened || settingsOpened) && !stopped && !document.hidden) timer = setTimeout(() => refresh(), 7000);
  }
  async function refresh() {
    if ((!opened && !settingsOpened) || working || stopped || document.hidden) { schedule(); return; }
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
    if (opened || settingsOpened) { refresh(); return; }
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
    opened = false;
    if (!settingsOpened) suspendView();
    panel.classList.add("hidden"); app.classList.remove("discovering"); trigger.setAttribute("aria-expanded", "false"); trigger.removeAttribute("aria-current");
    $("chat-nav").classList.add("selected"); $("chat-nav").setAttribute("aria-current", "page");
    cardSignature = ""; $("discovery-cards").replaceChildren();
    if (returnFocus && !app.inert) trigger.focus();
  }
  function suspendView() {
    generation++; working = false; fresh = false; clearTimeout(timer); controller?.abort(); controller = null;
    $("discovery-key").value = "";
    updateControls();
  }
  function setSettingsVisible(value) {
    const next = !!value && !stopped;
    if (settingsOpened === next) return;
    settingsOpened = next;
    $("discovery-preferences-toggle").setAttribute("aria-expanded", String(next && settingsSection === "scope"));
    if (next) {
      if (!snapshot) status("正在读取联网搜索设置…", "waiting");
      refresh();
    } else {
      $("discovery-key").value = "";
      if (!opened) suspendView();
    }
  }

  trigger.addEventListener("click", () => opened ? close() : open());
  $("discovery-close").addEventListener("click", goChat);
  for (const id of ["chat-nav", "observer-btn", "evaluations-btn"]) $(id).addEventListener("click", () => close(false), true);
  $("discovery-refresh").addEventListener("click", refresh);
  $("discovery-settings-refresh").addEventListener("click", refresh);
  $("discovery-settings-open").addEventListener("click", () => showSettings());
  settingsPanel.querySelectorAll("[data-discovery-settings-target]").forEach((button) => button.addEventListener("click", () => selectSettingsSection(button.dataset.discoverySettingsTarget, true)));
  $("discovery-preferences-toggle").addEventListener("click", () => showPreferences(true));
  $("discovery-preferences-close").addEventListener("click", () => showPreferences(false));
  $("discovery-toggle-enabled").addEventListener("click", toggleEnabled);
  $("discovery-next-action").addEventListener("click", () => { if (!fresh) { refresh(); return; } const issue = setupIssue(); if (issue?.enable) toggleEnabled(); else if (issue?.target) showPreferences(true, issue.target); });
  $("discovery-run").addEventListener("click", () => {
    if (setupIssue()) return;
    mutate("run", {}, "正在请求寻找内容…", "已提交本轮寻找，完成后资料会自动出现在这里。");
  });
  $("discovery-cancel").addEventListener("click", async () => {
    if (snapshot?.task?.parent_request_id) {
      try { await window.AliceChatSearch.stop(snapshot.task.parent_request_id, dataset); status("已请求停止本轮查找，已发生的请求仍计入额度。"); }
      catch (error) { status(error.message, "error"); }
      return;
    }
    mutate("cancel", { task_id: snapshot?.task?.id }, "正在请求停止…", "已请求停止；收回任务前不会开始另一轮。已发出的请求仍可能计费。");
  });
  $("discovery-go-chat").addEventListener("click", goChat);
  $("discovery-undiscuss").addEventListener("click", removeDiscussion); composerCancel.addEventListener("click", removeDiscussion);
  function recordSettingsEdit() {
    settingsEdit++;
    settingsDirty = true;
    updatePolicyControls();
    providerFields();
    updateControls();
  }
  settingsPanel.addEventListener("input", (event) => {
    if (event.target.form !== $("discovery-settings-form")) return;
    const control = policyControls.get(event.target.dataset.discoveryDailyQuota);
    if (control) {
      const { input, field, daily } = control;
      if (input.valueAsNumber > 0 && input.validity.valid && (!field.integer || Number.isSafeInteger(input.valueAsNumber))) {
        daily.lastLimit = input.value;
      }
      renderDailyQuotaSummary(control);
    }
    recordSettingsEdit();
  });
  settingsPanel.addEventListener("invalid", (event) => {
    const form = $("discovery-settings-form");
    if (event.target.form !== form) return;
    // Native validation visits every invalid control. Keep the first one's pane
    // visible instead of switching repeatedly and leaving the first field hidden.
    const first = [...form.elements].find((control) => control.willValidate && !control.validity.valid);
    if (first && event.target !== first) { event.preventDefault(); return; }
    revealSetting(event.target);
  }, true);
  $("discovery-provider").addEventListener("change", providerFields);
  $("discovery-bilibili-access").addEventListener("change", providerFields);
  $("discovery-zhihu-access").addEventListener("change", providerFields);
  $("discovery-tieba-access").addEventListener("change", providerFields);
  $("discovery-settings-reset").addEventListener("click", () => { fillSettings(true); updateControls(); });
  $("discovery-settings-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (settingsConflict()) { showPreferences(true, "discovery-settings-reset"); return; }
    let policy;
    try { policy = readPolicySettings(); } catch (error) { status(error.message, "waiting"); return; }
    const settings = { ...policy, enabled: $("discovery-enabled").checked, background_enabled: $("discovery-background").checked, chat_search_enabled: $("discovery-chat-search").checked, remember_observations: $("discovery-remember-observations").checked, window_start: $("discovery-start").value, window_end: $("discovery-end").value, allow_paid: $("discovery-paid").checked, allow_chat_interests: $("discovery-chat-interests").checked, share_in_qq: $("discovery-share-qq").checked, provider_kind: $("discovery-provider").value };
    if (has(snapshot?.settings, "network_mode") && ["system", "direct"].includes(snapshot.settings.network_mode)) {
      const networkMode = $("discovery-network-mode").value;
      if (!["system", "direct"].includes(networkMode)) { status("请选择服务支持的内容发现网络路由。", "waiting"); return; }
      settings.network_mode = networkMode;
    }
    if (has(snapshot?.settings, "bilibili_access")) settings.bilibili_access = $("discovery-bilibili-access").value;
    if (has(snapshot?.settings, "zhihu_access")) settings.zhihu_access = $("discovery-zhihu-access").value;
    if (has(snapshot?.settings, "tieba_access") && ["native", "third_party"].includes(snapshot.settings.tieba_access)) {
      const tiebaAccess = $("discovery-tieba-access").value;
      if (!["native", "third_party"].includes(tiebaAccess)) { status("请选择服务支持的贴吧访问方式。", "waiting"); return; }
      settings.tieba_access = tiebaAccess;
    }
    const tiebaSupport = tiebaScopeSupport();
    if (tiebaSupport.replies && tiebaSupport.limit) {
      settings.tieba_read_replies = $("discovery-tieba-read-replies").checked;
      if (settings.tieba_read_replies) {
        const raw = $("discovery-tieba-reply-limit").value.trim(), limit = Number(raw);
        if (!raw || !Number.isSafeInteger(limit) || limit <= 0) {
          showPreferences(true, "discovery-tieba-reply-limit");
          status("每帖回复总量须为可准确表示的正整数，楼中楼也计入条数。", "waiting"); return;
        }
        settings.tieba_reply_limit = limit;
      }
    }
    if (tiebaSupport.nested) settings.tieba_read_nested = $("discovery-tieba-read-nested").checked;
    if (tiebaSupport.textLimit) {
      const raw = $("discovery-tieba-text-limit").value.trim(), limit = Number(raw);
      if (!raw || !Number.isSafeInteger(limit) || limit < 80) {
        showPreferences(true, "discovery-tieba-text-limit");
        status("每帖文字额度须为至少 80 的可准确表示整数。", "waiting"); return;
      }
      settings.tieba_text_limit = limit;
    }
    if (settings.provider_kind === "searxng") {
      const engines = $("discovery-engines").value.split(",").map((name) => name.trim()).filter(Boolean);
      if (!engines.length || new Set(engines).size !== engines.length || engines.some((name) => !/^[a-zA-Z0-9_-]{1,40}$/.test(name))) { status("请填写不同的搜索引擎名称，用英文逗号分隔；每个名称使用字母、数字、下划线或连字符（不超过 40 字符）。", "waiting"); return; }
      settings.provider_endpoint = $("discovery-endpoint").value.trim();
      settings.provider_engines = engines.join(",");
    }
    if (settings.enabled && settings.background_enabled && settings.daily_models !== 0 && !settings.allow_paid) { showPreferences(true, "discovery-paid"); status("请允许后台模型使用，或在每日额度中关闭后台模型请求。", "waiting"); return; }
    if (["brave", "searxng"].includes(settings.provider_kind)) {
      if ($("discovery-key").value.trim()) settings.provider_key = $("discovery-key").value.trim();
      if ($("discovery-clear-key").checked) settings.clear_provider_key = true;
    }
    if (settings.provider_key && settings.clear_provider_key) { status("填写新密钥和移除密钥只能选择一项。", "waiting"); return; }
    mutate("settings", { settings }, "正在保存发现设置…", "设置已保存。自主寻找会遵守所选平台、兴趣、时段与额度。", { revision: settingsRevision });
  });
  $("discovery-interest-form").addEventListener("input", () => { interestEdit++; if (!interestDirty) interestRevision = currentRevision(); interestDirty = true; $("discovery-interest-reset").classList.remove("hidden"); updateControls(); });
  $("discovery-interest-form").addEventListener("invalid", (event) => revealSetting(event.target), true);
  $("discovery-interest-reset").addEventListener("click", resetInterest);
  $("discovery-forum-check").addEventListener("click", () => {
    if (!has(snapshot, "tieba_forum_check")) return;
    if (interestRevision !== currentRevision()) { status("设置在编辑期间已更新，请取消并重新编辑兴趣后再检测。草稿仍保留。", "waiting"); return; }
    const forums = preferredForumNames();
    if (!forums.length || !validForumNames(forums)) { status("请每行填写一个有效吧名，不超过 128 字且不含控制字符。", "waiting"); return; }
    mutate("tieba_forum_check", { forums }, "正在提交吧名检测…", "吧名检测已提交，结果会自动更新；兴趣草稿尚未保存。", { revision: interestRevision });
  });
  $("discovery-interest-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (interestRevision !== currentRevision()) { status("兴趣编辑期间设置已更新，请取消编辑并重新选择。", "waiting"); return; }
    const topic = $("discovery-interest-topic").value.trim();
    const payload = { topic, summary: $("discovery-interest-summary").value.trim() || topic, enabled: $("discovery-interest-enabled").checked, external_allowed: $("discovery-interest-external").checked };
    if (editingInterest) payload.id = editingInterest;
    if (!payload.topic) { status("写一个想了解的主题即可。", "waiting"); return; }
    if (payload.enabled && !payload.external_allowed) { showPreferences(true, "discovery-interest-external"); status("请允许用这个兴趣搜索；也可展开启用状态，先保存为停用的兴趣。", "waiting"); return; }
    if (has(snapshot, "tieba_forum_check")) {
      const names = preferredForumNames();
      if (!validForumNames(names)) { showPreferences(true, "discovery-interest-forums"); status("吧名须为不超过 128 字、不含控制字符的准确名称。", "waiting"); return; }
      const unchecked = names.filter((name) => !editingPreferredForums.includes(name) && !readyForumCheck(name));
      if (unchecked.length) {
        showPreferences(true, "discovery-interest-forums");
        status("新增优先吧需要先检测：只有名称一致且取得公开帖子的结果才能保存。未确认的项目请核对、移除或重新检测。", "waiting"); return;
      }
      payload.preferred_forums = names;
    }
    const editAtSend = interestEdit;
    mutate("interest", payload, "正在保存兴趣…", payload.enabled ? "兴趣已保存，Alice 可以在你开放的范围内自行选择搜索角度。" : "兴趣已保存并停用，不会用于寻找内容。", { revision: interestRevision, after: () => { if (editAtSend === interestEdit) resetInterest(); } });
  });
  document.addEventListener("visibilitychange", () => { clearTimeout(timer); if (!document.hidden) { if (opened || settingsOpened) refresh(); else refreshDiscussion(); } });
  new MutationObserver(() => {
    updateControls();
    if (!$("input").disabled) refreshDiscussion();
    else if (pendingDiscussion) composerText.textContent = "正在确认本轮使用的资料；实际采用情况以本轮回复为准。";
  }).observe($("input"), { attributes: true, attributeFilter: ["disabled"] });
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape" && !app.inert) { event.preventDefault(); goChat(); } });
  new MutationObserver(() => {
    panel.inert = app.classList.contains("nav-open");
    if (app.classList.contains("hidden")) { close(false); setSettingsVisible(false); clearIdentity(); }
  }).observe(app, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(() => {
    if (!$("shutdown-screen").classList.contains("hidden")) { close(false); setSettingsVisible(false); stopped = true; pendingDiscussion = null; paintDiscussion(); clearTimeout(discussionTimer); }
    else stopped = false;
  }).observe($("shutdown-screen"), { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("pagehide", () => { close(false); setSettingsVisible(false); stopped = true; controller?.abort(); clearTimeout(discussionTimer); $("discovery-key").value = ""; });
  window.AliceDiscovery = { close, setSettingsVisible, refreshAfterTurn: () => { if (opened || settingsOpened) refresh(); }, shutdown: () => { close(false); setSettingsVisible(false); stopped = true; } };
  updateControls(); providerFields();
})();
