/* GUI-only, read-on-demand work records. Opening this panel never dispatches work. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("worker-tasks"), app = $("app");
  const activeStates = new Set(["queued", "starting", "preparing", "running", "working", "collecting", "stopping"]);
  const endedStates = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
  const managementNames = { archive: "归档", restore: "恢复", delete: "删除" };
  const states = { unverified: "接口未核实", unavailable: "未接入", unauthorized: "未授权", disabled: "未授权",
    ready: "可用", connected: "已配对", disconnected: "未配对", connecting: "正在配对", paused: "已暂停", draining: "正在断开", uncertain: "连接待确认", enabled: "已授权", blocked_permission_profile: "连接与权限待核实", queued: "排队", starting: "准备", preparing: "准备", running: "工作中", working: "工作中", collecting: "收取产物", waiting: "等待补充", waiting_input: "等待补充",
    stopping: "正在停止", completed: "已结束", succeeded: "已结束", failed: "失败", interrupted: "中断", unknown: "状态未知", cancelled: "已停止", stopped: "已停止", revoked: "正文已撤销" };
  const eventNames = { task_accepted: "宿主已接收任务", run_started: "开始执行", alice_message_queued: "任务消息已登记，等待发送",
    alice_message_sent: "Alice → DSH", agent_message_delta: "DSH → Alice · 公开回复", agent_message_completed: "DSH → Alice · 公开回复",
    agent_question: "DSH → Alice · 等待补充", tool_started: "工具开始", tool_completed: "工具结束", tool_failed: "工具失败",
    artifact_reported: "DSH 报告产物", artifact_accepted: "宿主已收取产物", usage_updated: "用量更新",
    cancel_requested: "已请求停止，等待确认", run_stopped: "运行停止", run_failed: "运行失败", connection_lost: "连接中断，执行状态待确认",
    run_completed: "本轮执行已结束", content_revoked: "任务正文已撤销", review_recorded: "人工核对记录", human_reviewed: "人工核对记录" };
  let opened = false, settingsOpened = false, stopped = false, connected = false, timer = null, polling = false;
  let epoch = null, registry = null, nextBefore = null, selection = null, selectedTask = null, viewGeneration = 0;
  let eventCursor = 0, eventMore = false, listBusy = false, actionBusy = false, policyDirty = false;
  let archivedView = false, listGeneration = 0, listDepth = 40, management = null, taskSettingsDirty = false;
  let taskSignature = "", artifactSignature = "", following = true, savedListScroll = 0;
  let controlGeneration = 0;
  const tasks = new Map(), seen = new Map(), eventNodes = new Map(), messageNodes = new Map();
  const controllers = new Set();
  const node = (tag, value, cls) => { const n = document.createElement(tag); if (value !== undefined) n.textContent = String(value); if (cls) n.className = cls; return n; };
  const show = (id, yes) => $(id).classList.toggle("hidden", !yes);
  const text = (id, value) => { if ($(id).textContent !== String(value)) $(id).textContent = String(value); };
  const time = (value) => value ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString("zh-CN", { hour12: false }) : "未记录";
  const state = (value) => states[value] || "状态待确认";
  const visible = () => !stopped && !document.hidden && (settingsOpened || !app.classList.contains("hidden"));
  const current = (id, generation, session) => opened && !$("workers-detail").classList.contains("hidden") && selection === id && generation === viewGeneration && session === epoch;
  const id = () => crypto.randomUUID();
  function status(message, error = false) {
    for (const name of ["workers-status", "workers-settings-status"]) {
      text(name, message); $(name).dataset.error = String(error);
    }
  }
  function invalidateChatSources() { window.dispatchEvent(new Event("alice:sources-changed")); }
  function resetList() {
    listGeneration++; listDepth = 40; nextBefore = null; savedListScroll = 0;
    tasks.clear(); $("workers-list").replaceChildren(); renderList();
  }
  function closeManagement(returnFocus = false) {
    const action = management?.action; management = null; show("workers-management-confirm", false);
    if (returnFocus && selectedTask) $(selectedTask.management_pending ? "workers-management-retry" : action === "delete" ? "workers-delete" : "workers-archive").focus({ preventScroll: true });
  }
  function hideTaskBody(message) {
    eventCursor = 0; eventMore = false; artifactSignature = "";
    eventNodes.clear(); messageNodes.clear();
    $("workers-events").replaceChildren(node("p", message, "worker-hint")); $("workers-artifacts").replaceChildren();
    show("workers-events-more", false); show("workers-events-restart", false); show("workers-latest", false);
  }
  async function request(path, payload) {
    const controller = new AbortController(); controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { method: payload === undefined ? "GET" : "POST", cache: "no-store", signal: controller.signal,
        headers: { "X-Alice-Intent": "workers", ...(payload === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const e = new Error([404, 405].includes(response.status) && !data.code ? "当前服务尚未加载协作接口；应用更新并重启后才能读取。" : (data.error || data.message || "暂时无法读取协作记录"));
        e.code = data.code; throw e;
      }
      return data;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  function clearDetail() {
    closeManagement();
    viewGeneration++; selectedTask = null; eventCursor = 0; eventMore = false; following = true;
    taskSignature = ""; artifactSignature = ""; eventNodes.clear(); messageNodes.clear();
    $("workers-events").replaceChildren(); $("workers-artifacts").replaceChildren();
    for (const name of ["workers-task-title", "workers-task-meta", "workers-tool-note", "workers-review-state", "workers-management-hint", "workers-management-result"]) text(name, "");
    for (const name of ["workers-cancel", "workers-supplement", "workers-review", "workers-revoke", "workers-archive", "workers-delete", "workers-management-retry"]) $(name).disabled = true;
    show("workers-management-retry", false);
    show("workers-events-more", false); show("workers-events-restart", false); show("workers-latest", false);
  }
  function acceptEpoch(next) {
    if (!next) throw new Error("服务未返回有效会话身份");
    if (epoch && epoch !== next) {
      seen.clear(); clearDetail(); selection = null; archivedView = false; resetList();
      show("workers-detail", false); show("workers-list-view", true);
      policyDirty = false; $("workers-token").value = ""; $("workers-advanced").open = false; $("workers-native-ack").checked = false;
      taskSettingsDirty = false;
      text("workers-pairing-result", ""); text("workers-policy-result", ""); text("workers-task-settings-result", "");
      status("会话已切换，旧任务正文已从本页清除。");
      invalidateChatSources();
    }
    epoch = next;
  }
  function setRegistry(data) {
    if (registry && (registry.revision !== data.revision || registry.policy?.enabled && !data.policy?.enabled)) {
      // Policy changes invalidate bodies already rendered or waiting in a fetch.
      clearDetail(); resetList(); policyDirty = false;
      invalidateChatSources();
    }
    if (registry?.task_management?.revision !== data.task_management?.revision) taskSettingsDirty = false;
    registry = data;
    const worker = data.collaborators?.find((row) => row.name === "dsh-agent");
    text("workers-connection", worker ? `DSH · ${state(worker.state)}${worker.reason ? `：${worker.reason}` : ""}` : "宿主未返回 DSH 注册信息，暂不可启用。");
    const paired = data.connection?.connected === true;
    text("workers-capabilities", paired ? (data.policy?.enabled ? "已配对并授权。请在主聊天描述任务，由 Alice 派工；关闭设置不会中断任务。" : "已配对，尚未授权派工。请设置工作目录、模式和执行上限。")
      : "尚未配对。请在下方选择连接方式；同步记录仅查看已有状态。");
    if (paired) $("workers-token").value = "";
    const credential = data.credential;
    const credentialState = credential?.state;
    const credentialMessages = {
      ready: "本机已保存加密令牌，可直接连接；不会因再次连接而更换令牌。",
      missing: "尚未保存令牌。点击下方按钮后，Alice 才会生成令牌并尝试连接。",
      unavailable: "本机令牌暂不可用，未自动生成或覆盖。断开并关闭授权后，可在高级设置尝试更换。",
      unsupported: "此系统不支持 Alice 管理令牌；可在高级设置使用手动令牌连接。"
    };
    text("workers-credential-state", credentialMessages[credentialState] || "当前服务尚未提供本机令牌状态，请重载 Alice 后同步。");
    $("workers-credential-state").dataset.error = String(!["ready", "missing"].includes(credentialState));
    const manual = worker?.manual_setup;
    show("workers-manual-setup", !!manual);
    text("workers-manual-title", manual?.title || "");
    const steps = Array.isArray(manual?.steps) ? manual.steps.filter((step) => typeof step === "string").slice(0, 6) : [];
    const signature = JSON.stringify(steps);
    if ($("workers-manual-steps").dataset.signature !== signature) {
      $("workers-manual-steps").replaceChildren(...steps.map((step) => node("li", step)));
      $("workers-manual-steps").dataset.signature = signature;
    }
    text("workers-manual-note", manual?.note || "");
    if (!policyDirty) {
      $("workers-enabled").checked = !!data.policy?.enabled;
      $("workers-max-runs").value = data.policy?.max_runs ?? 0;
      $("workers-max-seconds").value = data.policy?.max_seconds ?? 600;
      $("workers-workspace").value = data.policy?.workspace_root || "";
      $("workers-mode").value = ["read-only", "workspace-write"].includes(data.policy?.permission_mode) ? data.policy.permission_mode : "read-only";
      $("workers-native-ack").checked = data.policy?.enabled === true && data.policy?.native_permissions_ack === true;
    }
    const used = Number.isSafeInteger(data.policy?.runs_used) ? data.policy.runs_used : "未知";
    const remaining = Number.isSafeInteger(data.policy?.remaining_runs) ? data.policy.remaining_runs : "未知";
    text("workers-budget-state", `本次授权已预留 ${used} 次，剩余 ${remaining} 次。新任务和追问均计次，取消或拒绝不退还；刷新和重新配对不增加额度。`);
    const taskSettings = data.task_management;
    if (!taskSettingsDirty) {
      $("workers-sync-archive").checked = taskSettings?.sync_archive === true;
      $("workers-sync-delete").checked = taskSettings?.sync_delete === true && taskSettings?.capabilities?.delete === true;
    }
    const canSyncArchive = taskSettings?.capabilities?.archive === true && taskSettings?.capabilities?.restore === true;
    text("workers-sync-archive-hint", !Number.isSafeInteger(taskSettings?.revision)
      ? "当前服务尚未提供任务管理设置，请重载 Alice 后刷新。"
      : !paired ? "可先保存同步选择。当前尚未连接 DSH；实际同步归档或恢复前，请在上方使用已保存令牌连接。"
      : canSyncArchive ? "开启后，归档和恢复会同步处理此任务对应的 DSH 专属会话；保存设置不会立即整理任务。"
      : "可先保存同步选择。当前连接未报告归档能力；实际同步前，请重载 0.4.0 或更新版插件并重新连接。");
    text("workers-sync-delete-hint", taskSettings?.capabilities?.delete === true ? "开启后，删除任务也会永久删除对应 DSH 专属会话，无法恢复。"
      : taskSettings?.sync_delete === true ? "已保存的同步删除选择目前不可用；请保存为关闭。当前 DSH 版本不支持同步删除，Alice 记录仍可删除。"
      : "当前 DSH 版本不支持同步删除；Alice 记录仍可删除。");
    renderList();
    updateControls();
  }
  function updateControls() {
    const paired = registry?.connection?.connected === true;
    const hasConnection = registry?.connection?.has_connection === true || paired;
    const worker = registry?.collaborators?.find((row) => row.name === "dsh-agent");
    const canUse = paired && worker?.available === true;
    const credential = registry?.credential;
    const managedAvailable = credential?.supported === true && ["ready", "missing"].includes(credential.state);
    $("workers-managed-connect").textContent = credential?.configured === true ? "使用已保存令牌连接" : "生成令牌并连接";
    $("workers-managed-connect").disabled = actionBusy || !epoch || hasConnection || !managedAvailable;
    $("workers-rotate-token").disabled = actionBusy || !epoch || hasConnection || registry?.policy?.enabled === true
      || credential?.supported !== true || credential?.configured !== true;
    $("workers-token").disabled = actionBusy || !epoch || hasConnection;
    $("workers-connect").disabled = actionBusy || !epoch || hasConnection || !$("workers-token").value.trim();
    $("workers-disconnect").disabled = actionBusy || !epoch || (!hasConnection && !registry?.policy?.enabled);
    for (const name of ["workers-max-runs", "workers-max-seconds", "workers-workspace", "workers-mode", "workers-native-ack"]) $(name).disabled = actionBusy || !canUse;
    $("workers-enabled").disabled = actionBusy || !epoch || !canUse && !registry?.policy?.enabled;
    $("workers-policy-save").disabled = actionBusy || !epoch || !canUse && !registry?.policy?.enabled;
    $("workers-policy-save").textContent = $("workers-enabled").checked ? "保存并启用授权" : "保存为关闭";
    const taskSettings = registry?.task_management;
    const settingsKnown = Number.isSafeInteger(taskSettings?.revision);
    $("workers-sync-archive").disabled = actionBusy || !epoch || !settingsKnown;
    $("workers-sync-delete").disabled = actionBusy || !epoch || !settingsKnown || taskSettings?.capabilities?.delete !== true;
    $("workers-task-settings-save").disabled = actionBusy || !epoch || !settingsKnown;
    for (const name of ["workers-unarchived", "workers-archived"]) $(name).disabled = actionBusy || !epoch;
    $("workers-back").disabled = actionBusy;
    updateTaskControls();
    updateManagementControls();
  }
  function renderList() {
    const rows = [...tasks.values()].filter((task) => (task.archived === true) === archivedView)
      .sort((a, b) => Number(activeStates.has(b.state)) - Number(activeStates.has(a.state)) || Number(b.updated_at || 0) - Number(a.updated_at || 0));
    const shown = new Set(rows.map((task) => task.task_id));
    for (const child of [...$("workers-list").children]) if (!shown.has(child.dataset.taskId)) child.remove();
    $("workers-unarchived").setAttribute("aria-pressed", String(!archivedView));
    $("workers-archived").setAttribute("aria-pressed", String(archivedView));
    text("workers-empty", archivedView ? "还没有已归档任务。归档后的任务保留在这里，可查看、恢复或删除。"
      : registry?.connection?.connected && registry?.policy?.enabled ? "还没有未归档任务。在主聊天告诉 Alice 要做什么；也可以切换到“已归档”查看旧记录。"
      : "还没有未归档任务。请在“设置 → DSH”连接并授权任务范围，然后在主聊天描述目标。");
    let unread = 0, running = 0;
    for (const [position, task] of rows.entries()) {
      const hasUnread = !archivedView && Number(task.last_sequence || 0) > (seen.get(task.task_id) || 0);
      if (hasUnread) unread++;
      if (activeStates.has(task.state)) running++;
      let button = [...$("workers-list").children].find((child) => child.dataset.taskId === task.task_id);
      if (!button) {
        button = node("button", undefined, "worker-task"); button.type = "button"; button.dataset.taskId = task.task_id;
        button.append(node("strong", "", "worker-task-title"), node("span", "", "worker-task-state"), node("span", "", "worker-task-info"));
        button.onclick = () => select(task.task_id);
      }
      button.disabled = actionBusy;
      button.dataset.unread = String(hasUnread); button.setAttribute("aria-current", String(selection === task.task_id));
      button.children[0].textContent = `DSH · ${task.title || "协作任务"}`;
      const pending = managementNames[task.management_pending];
      button.children[1].textContent = `${state(task.state)}${pending ? ` · ${pending}待确认` : ""}${hasUnread ? " · 未读" : ""}`;
      button.children[2].textContent = `最近活动 ${time(task.updated_at)}`;
      if ($("workers-list").children[position] !== button) {
        const focused = document.activeElement === button;
        $("workers-list").insertBefore(button, $("workers-list").children[position] || null);
        if (focused) button.focus({ preventScroll: true });
      }
    }
    text("workers-count", archivedView ? "已归档" : [running ? `${running} 运行` : "", unread ? `${unread} 未读` : ""].filter(Boolean).join(" · "));
    show("workers-empty", rows.length === 0); show("workers-more", nextBefore !== null);
  }
  async function syncList(older = false) {
    if (listBusy || actionBusy) return;
    listBusy = true; $("workers-more").disabled = true;
    const session = epoch, generation = controlGeneration, listVersion = listGeneration, archived = archivedView;
    const wanted = listDepth + (older ? 40 : 0), incoming = new Map();
    const stale = () => session !== epoch || generation !== controlGeneration || listVersion !== listGeneration || archived !== archivedView;
    try {
      // Re-read the visible prefix so removed or moved rows cannot linger in a
      // previously loaded page. No task bodies are fetched by list refreshes.
      let before = 0, next = null;
      while (before < wanted) {
        const params = new URLSearchParams({ limit: String(Math.min(100, wanted - before)), archived: archived ? "1" : "0" });
        if (before) params.set("before", String(before));
        const data = await request(`/api/worker-tasks?${params}`);
        if (stale() || data.session_epoch !== epoch) return;
        for (const row of data.tasks || []) if ((row.archived === true) === archived) incoming.set(row.task_id, row);
        next = Number.isSafeInteger(data.next_before) && data.next_before > before ? data.next_before : null;
        if (next === null) break;
        before = next;
      }
      let newlyRevoked = false;
      for (const row of incoming.values()) {
        const previous = tasks.get(row.task_id);
        if (previous && previous.content_revoked !== true && row.content_revoked === true) newlyRevoked = true;
        if (row.task_id === selection && row.content_revoked === true) {
          // A closed panel must not retain a revoked body until it is reopened.
          if (selectedTask?.content_revoked !== true) viewGeneration++;
          renderDetail({ task: row, artifacts: [] });
        }
      }
      tasks.clear(); for (const [key, row] of incoming) tasks.set(key, row);
      if (newlyRevoked) invalidateChatSources();
      listDepth = wanted; nextBefore = next; renderList();
    } finally {
      listBusy = false; $("workers-more").disabled = false;
      if (stale()) schedule(0);
    }
  }
  function selectArchiveView(archived) {
    if (actionBusy || archivedView === archived) return;
    controlGeneration++; archivedView = archived; selection = null; clearDetail(); resetList();
    show("workers-detail", false); show("workers-list-view", true);
    schedule(0);
  }
  function manageable(task) {
    return !!task && endedStates.has(task.state) && task.agent_idle === true && Number.isSafeInteger(task.version);
  }
  function updateTaskControls() {
    const task = selectedTask;
    if (!task) {
      for (const name of ["workers-cancel", "workers-supplement", "workers-review", "workers-revoke"]) $(name).disabled = true;
      return;
    }
    const revoked = task.content_revoked === true || task.state === "revoked", pending = !!task.management_pending;
    $("workers-cancel").disabled = actionBusy || pending || !(activeStates.has(task.state) || ["unknown", "waiting"].includes(task.state)) || task.state === "stopping" || !!task.cancel_requested && task.state !== "unknown";
    $("workers-cancel").textContent = task.state === "unknown" && task.cancel_requested ? "重试停止" : "请求停止";
    $("workers-supplement").disabled = actionBusy || pending || task.archived === true || task.session_closed === true || revoked || registry?.policy?.enabled !== true || registry?.connection?.connected !== true;
    $("workers-review").disabled = actionBusy || pending || revoked || !["completed", "succeeded"].includes(task.state);
    $("workers-revoke").disabled = actionBusy || pending || revoked;
  }
  function updateManagementControls() {
    const task = selectedTask, pending = task?.management_pending;
    const settingsKnown = Number.isSafeInteger(registry?.task_management?.revision);
    const allowed = !actionBusy && !!epoch && settingsKnown && manageable(task);
    $("workers-archive").textContent = task?.archived ? "恢复任务" : "归档任务";
    $("workers-archive").disabled = !allowed || !!pending;
    $("workers-delete").disabled = !allowed || !!pending;
    show("workers-management-retry", !!managementNames[pending]);
    $("workers-management-retry").textContent = `重新确认${managementNames[pending] || "待处理操作"}`;
    $("workers-management-retry").disabled = !allowed || !managementNames[pending];
    text("workers-management-hint", !settingsKnown ? "当前服务尚未提供任务管理设置，请更新并重载后再试。"
      : pending ? `${managementNames[pending] || "管理操作"}结果待确认。仅可按原来的${task.management_sync_dsh ? "同步 DSH" : "只处理 Alice"}选择明确重试，不会自动重发。`
      : !manageable(task) ? "仅已结束且 DSH 会话已空闲的任务可以归档、恢复或删除。正文已撤销也可清理。"
      : task.archived ? "已归档，正文保留；恢复只回列表与历史，不会重新激活已关闭的 DSH 会话，也不会还原已撤销正文。"
      : task.session_closed ? "DSH 专属会话已关闭，当前任务不能继续派工；记录仍可查看、归档或删除。"
      : "归档会保留正文并暂停此任务的后续派工；删除无法恢复。同步范围在“设置 → DSH”中选择。");
    if (!management) return;
    const sameTask = task?.task_id === management.taskId && task?.version === management.version;
    const sameSettings = registry?.task_management?.revision === management.settingsRevision;
    const samePending = (pending || null) === management.pending;
    const remoteReady = !management.syncDsh || registry?.connection?.connected === true
      && registry?.task_management?.capabilities?.[management.action] === true;
    const warning = !sameTask || !sameSettings || !samePending ? "任务或同步设置已变化，请返回详情后重新确认。"
      : !manageable(task) ? "任务尚未结束或会话未确认空闲，当前不能执行此操作。"
      : !remoteReady ? (management.pending ? "请先恢复 DSH 连接，再按原同步范围重新确认；此项待确认操作不能改为只处理 Alice。"
        : "请先在“设置 → DSH”连接支持此操作的版本，或关闭对应同步选项后重新确认。") : "";
    text("workers-management-warning", warning);
    $("workers-management-submit").disabled = actionBusy || !sameTask || !sameSettings || !samePending || !manageable(task) || !remoteReady;
    $("workers-management-back").disabled = actionBusy;
  }
  function beginManagement(action) {
    const task = selectedTask, settings = registry?.task_management;
    if (actionBusy || !managementNames[action] || !manageable(task) || !Number.isSafeInteger(settings?.revision)) return;
    if (task.management_pending && task.management_pending !== action) return;
    const syncDsh = task.management_pending ? task.management_sync_dsh === true
      : (action === "delete" ? settings.sync_delete : settings.sync_archive) === true;
    management = { action, taskId: task.task_id, version: task.version, settingsRevision: settings.revision,
      syncDsh, pending: task.management_pending || null };
    const descriptions = {
      archive: "将任务移入 Alice 的“已归档”列表。正文保留，恢复前不能继续派工。",
      restore: "将任务移回“进行中与历史”列表。恢复不会重新执行任务、激活已关闭的 DSH 会话、恢复已撤销正文或授予新权限。",
      delete: "删除 Alice 的任务记录并撤销相关正文，无法恢复。工作目录中的文件不受此操作影响。"
    };
    text("workers-management-title", `${management.pending ? "重新确认" : "确认"}${managementNames[action]}任务`);
    text("workers-management-description", `任务 ${task.task_id}：${descriptions[action]}`);
    text("workers-management-scope", syncDsh
      ? `${action === "delete" ? "本次还会永久删除 DSH 对应专属会话，无法恢复。" : `本次还会同步${managementNames[action]} DSH 对应专属会话。`}${management.pending ? "沿用原待确认操作的选择。" : "依据已保存的同步设置。"}`
      : `本次只处理 Alice，DSH 会话保持不变。${management.pending ? "沿用原待确认操作的选择。" : "同步选项可在“设置 → DSH”中更改。"}`);
    text("workers-management-result", "");
    const submit = $("workers-management-submit");
    submit.textContent = `${management.pending ? "确认重试" : "确认"}${managementNames[action]}${syncDsh ? "（含 DSH）" : "（仅 Alice）"}`;
    submit.classList.toggle("danger", action === "delete"); submit.classList.toggle("primary", action !== "delete");
    show("workers-management-confirm", true); updateManagementControls();
    $("workers-management-title").focus({ preventScroll: true });
    $("workers-management-confirm").scrollIntoView({ block: "nearest" });
  }
  async function manageTask() {
    if (!management || actionBusy || $("workers-management-submit").disabled) return;
    const choice = { ...management }, session = epoch;
    actionBusy = true; controlGeneration++; viewGeneration++;
    const generation = controlGeneration;
    if (choice.action === "delete") {
      hideTaskBody("删除请求已提交，正文已从本页清除；操作结果仍待确认。"); invalidateChatSources();
    }
    updateControls(); renderList();
    text("workers-management-result", `正在${managementNames[choice.action]}，结果尚未确认…`);
    $("workers-management-result").dataset.error = "false";
    try {
      const data = await request(`/api/worker-tasks/${choice.action}`, { session_epoch: session, task_id: choice.taskId,
        request_id: id(), revision: choice.version, settings_revision: choice.settingsRevision });
      if (session !== epoch || generation !== controlGeneration || data.session_epoch !== session || data.task_id !== choice.taskId) return;
      closeManagement();
      if (data.completed === true && !data.management_pending) {
        if (choice.syncDsh && data.synced_dsh !== true || choice.action === "delete" && data.deleted !== true
            || choice.action === "archive" && data.archived !== true || choice.action === "restore" && data.archived !== false) {
          throw new Error("宿主返回的管理结果不完整，需要重新核对状态");
        }
        if (choice.action === "delete") { seen.delete(choice.taskId); invalidateChatSources(); }
        else if (selectedTask) seen.set(choice.taskId, Number(selectedTask.last_sequence) || 0);
        if (choice.action === "restore") archivedView = false;
        selection = null; clearDetail(); resetList();
        show("workers-detail", false); show("workers-list-view", true);
        status(`已${managementNames[choice.action]} Alice 任务${data.synced_dsh === true ? `，并已同步${managementNames[choice.action]} DSH 专属会话。` : "；DSH 会话未改动。"}`);
        if (opened) $(archivedView ? "workers-archived" : "workers-unarchived").focus({ preventScroll: true });
      } else {
        if (selectedTask?.task_id === choice.taskId) {
          selectedTask = { ...selectedTask, archived: data.archived === true, version: data.version ?? selectedTask.version,
            management_pending: data.management_pending || null, management_sync_dsh: data.management_sync_dsh === true };
          if (tasks.has(choice.taskId)) tasks.set(choice.taskId, selectedTask);
        }
        const message = data.management_pending ? `${managementNames[choice.action]}尚未确认，Alice 记录未移动。恢复 DSH 连接后，可按原选择明确重试。`
          : `${managementNames[choice.action]}未完成，Alice 记录未移动。请检查 DSH 连接和同步设置，再重新确认；没有自动重试。`;
        text("workers-management-result", message); $("workers-management-result").dataset.error = "true";
        status(message, true);
      }
    } catch (e) {
      if (session !== epoch || generation !== controlGeneration) return;
      closeManagement();
      if (choice.action === "delete") {
        selection = null; clearDetail(); resetList();
        show("workers-detail", false); show("workers-list-view", true); invalidateChatSources();
      }
      const message = `操作结果未确认：${e.message}。请同步状态后重新确认；没有自动重试。`;
      text("workers-management-result", message); $("workers-management-result").dataset.error = "true"; status(message, true);
    } finally { actionBusy = false; updateControls(); renderList(); schedule(0); }
  }
  async function saveTaskSettings() {
    if (actionBusy || !epoch || $("workers-task-settings-save").disabled) return;
    const session = epoch, settings = registry.task_management;
    const archive = $("workers-sync-archive").checked, deletion = $("workers-sync-delete").checked;
    const result = (message, error) => { text("workers-task-settings-result", message); $("workers-task-settings-result").dataset.error = String(error); };
    if (deletion && settings.capabilities?.delete !== true) { result("当前 DSH 版本不支持同步删除。", true); return; }
    actionBusy = true; controlGeneration++; viewGeneration++;
    const generation = controlGeneration;
    updateControls(); renderList(); result("正在保存任务管理同步设置…", false);
    try {
      const data = await request("/api/worker-task-settings", { session_epoch: session, request_id: id(), revision: settings.revision,
        sync_archive: archive, sync_delete: deletion });
      if (session !== epoch || generation !== controlGeneration || data.session_epoch !== session) return;
      taskSettingsDirty = false; setRegistry(data);
      const ready = data.connection?.connected === true && data.task_management?.capabilities?.archive === true
        && data.task_management?.capabilities?.restore === true;
      result(archive && !ready ? "同步选择已保存；实际归档或恢复前还需连接支持此功能的 DSH。派工授权和执行次数未改变。"
        : "同步设置已保存；派工授权、执行次数和时限未改变。", false);
    } catch (e) {
      if (session === epoch && generation === controlGeneration) result(`保存未确认：${e.message}。当前选择保留，请同步状态后再确认。`, true);
    } finally { actionBusy = false; updateControls(); renderList(); schedule(0); }
  }
  function eventText(event) {
    const payload = event.payload || {};
    if (typeof payload === "string") return payload;
    // Never dump arbitrary adapter objects, logs, headers, paths or environment values.
    for (const key of ["text", "message", "summary", "reason"]) if (typeof payload[key] === "string") return payload[key];
    if (typeof payload.tool_name === "string") return `工具：${payload.tool_name}`;
    return "";
  }
  function bodyText(entry, value) {
    entry.body.textContent = value;
    const long = value.length > 900;
    entry.fold.classList.toggle("hidden", !long); entry.body.hidden = false;
    if (long && entry.body.parentNode !== entry.fold) entry.fold.appendChild(entry.body);
    else if (!long && entry.body.parentNode !== entry.root) entry.root.insertBefore(entry.body, entry.fold);
  }
  function appendEvent(event) {
    if (!Number.isSafeInteger(event.sequence) || event.sequence <= eventCursor || eventNodes.has(event.event_id)) return;
    const actor = event.actor === "alice" ? "alice" : ["dsh", "agent", "dsh-agent"].includes(event.actor) ? "dsh" : "host";
    const isMessage = ["agent_message_delta", "agent_message_completed"].includes(event.type);
    const messageKey = isMessage && event.message_id ? `${actor}:${event.message_id}` : null;
    let entry = messageKey && messageNodes.get(messageKey);
    if (!entry) {
      const root = node("article", undefined, "worker-event"); root.dataset.actor = actor;
      const head = node("div", "", "worker-event-head"), body = node("p", "", "worker-event-text"), fold = node("details");
      fold.appendChild(node("summary", "展开长消息"));
      const meta = node("div", "", "worker-event-meta"); root.append(head, body, fold, meta);
      entry = { root, head, body, fold, meta, content: "", offset: 0 }; $("workers-events").appendChild(root);
      if (messageKey) messageNodes.set(messageKey, entry);
    }
    const content = eventText(event);
    if (event.type === "agent_message_delta") {
      const offset = Number(event.payload?.offset);
      if (Number.isSafeInteger(offset) && offset >= 0) {
        if (offset === entry.content.length) entry.content += content;
        else if (offset > entry.content.length) entry.content += "\n[缺失片段，等待完整消息]\n" + content;
      } else entry.content += content;
    } else entry.content = content;
    entry.head.textContent = eventNames[event.type] || "公开工作事件";
    entry.meta.textContent = `#${event.sequence} · ${time(event.observed_at)} · ${actor === "dsh" ? "DSH 报告，未经宿主事实复核" : "宿主记录"}${event.truncated ? " · 片段已截断" : ""}`;
    // The host bounds messages too; keep a second client bound for an open tab.
    if (entry.content.length > 64000) entry.content = entry.content.slice(0, 64000) + "\n[本页消息长度已达上限]";
    bodyText(entry, entry.content); eventNodes.set(event.event_id, entry); eventCursor = event.sequence;
    while (eventNodes.size > 600) eventNodes.delete(eventNodes.keys().next().value);
    while ($("workers-events").children.length > 300) {
      const oldest = $("workers-events").firstElementChild; oldest.remove();
      for (const [key, value] of messageNodes) if (value.root === oldest) messageNodes.delete(key);
      for (const [key, value] of eventNodes) if (value.root === oldest) eventNodes.delete(key);
      show("workers-events-restart", true);
    }
  }
  function renderDetail(data) {
    const task = data.task;
    if (selectedTask?.task_id === task.task_id && selectedTask.content_revoked !== true && task.content_revoked === true) invalidateChatSources();
    selectedTask = task;
    const signature = JSON.stringify(task);
    if (taskSignature !== signature) {
      taskSignature = signature;
      text("workers-task-title", task.title || "协作任务");
      text("workers-task-meta", `${state(task.state)}${task.archived ? " · 已归档" : ""}${task.session_closed ? " · DSH 会话已关闭" : ""} · 最后更新 ${time(task.updated_at)} · ${task.task_id}`);
      const cap = registry?.collaborators?.find((row) => row.name === "dsh-agent")?.capabilities;
      text("workers-tool-note", data.tool_details_available === true || cap?.tool_details === true || cap?.tool_events === true ? "工具事件来自 DSH 的公开报告。" : "此接口未提供工具详情。");
      const reported = task.usage;
      const cost = Number.isSafeInteger(reported?.reported_input_tokens) && Number.isSafeInteger(reported?.reported_output_tokens)
        ? `DSH 根任务累计自报输入 ${reported.reported_input_tokens}、输出 ${reported.reported_output_tokens} token；完整用量及费用仍未知。`
        : "用量/费用未知，不记为零。";
      const review = task.review === "accepted" || task.review === "passed" ? "已记录人工核对通过。" : "尚未人工核对；运行结束不等于产物通过验收。";
      const stopNote = task.cancel_requested && !task.termination_confirmed
        ? (task.agent_idle ? " DSH 会话已空闲；外部进程树终止未确认。" : " 停止尚未确认。") : "";
      text("workers-review-state", `${review} ${cost}${stopNote}`);
    }
    const revoked = task.content_revoked === true || task.state === "revoked";
    const deleting = task.management_pending === "delete";
    if (revoked || deleting) hideTaskBody(revoked ? "正文已撤销，仅保留任务状态事实。" : "删除结果待确认，正文暂不在本页展示。请按原选择明确重试。");
    updateTaskControls();
    updateManagementControls();
    const artifacts = revoked || deleting ? [] : data.artifacts || [];
    const signatureArtifacts = JSON.stringify(artifacts);
    if (signatureArtifacts !== artifactSignature) {
      artifactSignature = signatureArtifacts; $("workers-artifacts").replaceChildren();
      for (const artifact of artifacts) {
        const row = node("div", undefined, "worker-artifact"); row.appendChild(node("span", artifact.display_name || artifact.name || "任务产物"));
        if (artifact.artifact_id && artifact.downloadable === true) {
          const button = node("button", "下载", "link"); button.type = "button";
          button.onclick = () => download(task.task_id, artifact.artifact_id, artifact.display_name || artifact.name); row.appendChild(button);
        } else row.appendChild(node("span", "待宿主收取", "worker-hint"));
        $("workers-artifacts").appendChild(row);
      }
    }
    return !revoked && !deleting;
  }
  async function syncDetail(explicit = false) {
    if (actionBusy || !opened || !selection || $("workers-detail").classList.contains("hidden")) return;
    const selected = selection, generation = viewGeneration, session = epoch;
    let data;
    try { data = await request("/api/worker-tasks/detail", { session_epoch: session, task_id: selected }); }
    catch (e) {
      if (e.code === "not_found" && current(selected, generation, session)) {
        controlGeneration++; seen.delete(selected); selection = null; clearDetail(); resetList();
        show("workers-detail", false); show("workers-list-view", true); invalidateChatSources();
        status("任务已不在当前记录中，列表正在重新同步。"); return;
      }
      if (["not_found", "session_changed", "epoch_changed"].includes(e.code) && current(selected, generation, session)) clearDetail();
      throw e;
    }
    if (!current(selected, generation, session) || data.task?.task_id !== selected || data.session_epoch && data.session_epoch !== session) return;
    if (!renderDetail(data)) return;
    // While reading earlier events, do not evict them through automatic paging.
    if (!explicit && !following && $("workers-events").children.length >= 300) return;
    let response;
    try {
      response = await request("/api/worker-tasks/events", { session_epoch: session, task_id: selected, after_cursor: eventCursor, limit: 100 });
    } catch (e) {
      if (e.code === "resync_required" && current(selected, generation, session)) { clearDetail(); status("工作记录游标已过期，正在重新同步。", true); return; }
      throw e;
    }
    if (!current(selected, generation, session) || response.session_epoch !== session || response.task_id !== selected) return;
    if (response.resync_required) { clearDetail(); status("工作记录游标已过期，正在重新同步。", true); return; }
    if (response.content_revoked) {
      clearDetail(); renderDetail({ task: { ...data.task, content_revoked: true }, artifacts: [] });
      invalidateChatSources(); return;
    }
    const scroll = $("workers-events"), atEnd = following;
    for (const event of response.events || []) appendEvent(event);
    eventCursor = Math.max(eventCursor, Number(response.cursor) || 0); eventMore = !!response.has_more;
    show("workers-events-more", eventMore);
    if (atEnd) scroll.scrollTop = scroll.scrollHeight;
    show("workers-latest", !atEnd && scroll.scrollHeight > scroll.clientHeight);
    if (atEnd && !eventMore) seen.set(selected, eventCursor);
    renderList();
  }
  function schedule(delay) {
    clearTimeout(timer);
    if (!visible()) return;
    const active = [...tasks.values()].some((task) => activeStates.has(task.state));
    timer = setTimeout(poll, delay ?? (opened && selection || active ? 1800 : opened || settingsOpened ? 5000 : 10000));
  }
  async function poll() {
    clearTimeout(timer);
    if (polling || actionBusy || !visible()) return;
    polling = true;
    const generation = controlGeneration;
    try {
      const data = await request("/api/collaborators");
      if (!visible() || generation !== controlGeneration) return;
      acceptEpoch(data.session_epoch); setRegistry(data); connected = true;
      await syncList();
      if (generation !== controlGeneration) return;
      if (opened && selection) await syncDetail();
      if (generation !== controlGeneration) return;
      const worker = registry.collaborators?.find((row) => row.name === "dsh-agent");
      status(worker && worker.state !== "ready" ? (worker.reason || `DSH · ${state(worker.state)}`) : `已同步 · ${time(Date.now() / 1000)}`);
    } catch (e) {
      if (!stopped) { connected = false; status(`连接暂不可用；执行状态未确认。${e.message}`, true); }
    } finally { polling = false; schedule(connected ? undefined : 10000); }
  }
  function layout() {
    const nav = $("app-nav"), style = getComputedStyle(app);
    const available = app.clientWidth - (nav.getClientRects().length && getComputedStyle(nav).position !== "fixed" ? nav.offsetWidth : 0)
      - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 2 * (parseFloat(style.gap) || 0);
    app.classList.toggle("worker-narrow", available < 900);
  }
  function open() {
    window.AliceObserver?.close(); window.AliceEvaluations?.close(); opened = true;
    panel.classList.remove("hidden"); app.classList.add("workers-open"); $("workers-btn").setAttribute("aria-expanded", "true"); layout();
    $("workers-list-view").scrollTop = savedListScroll; $("workers-close").focus({ preventScroll: true }); poll();
  }
  function close(returnFocus = true) {
    if (!opened) return;
    closeManagement();
    opened = false; viewGeneration++; savedListScroll = $("workers-list-view").scrollTop;
    panel.classList.add("hidden"); app.classList.remove("workers-open"); $("workers-btn").setAttribute("aria-expanded", "false");
    if (returnFocus && !app.inert && !app.classList.contains("nav-open")) $("input").focus({ preventScroll: true });
    schedule();
  }
  function setSettingsVisible(value) {
    settingsOpened = value === true && !$("settings-modal").classList.contains("hidden")
      && !$("workers-config").classList.contains("hidden");
    if (!settingsOpened) {
      $("workers-token").value = ""; $("workers-advanced").open = false; updateControls();
      schedule(); return;
    }
    status("正在同步 DSH 连接与授权状态…");
    poll();
  }
  async function select(taskId) {
    if (actionBusy) return;
    savedListScroll = $("workers-list-view").scrollTop;
    if (selection !== taskId) { clearDetail(); selection = taskId; }
    show("workers-list-view", false); show("workers-detail", true);
    $("workers-back").focus({ preventScroll: true });
    try { await syncDetail(); } catch (e) { status(e.message, true); }
  }
  async function mutate(action, extra = {}) {
    if (actionBusy || !epoch) return;
    actionBusy = true; controlGeneration++; viewGeneration++; updateControls();
    const selected = selection, session = epoch;
    for (const name of ["workers-cancel", "workers-supplement", "workers-review", "workers-revoke"]) $(name).disabled = true;
    try {
      const body = { session_epoch: session, request_id: id(), ...(selected ? { task_id: selected, revision: selectedTask?.version } : {}), ...extra };
      await request(`/api/worker-tasks/${action}`, body);
      if (action === "revoke" && session === epoch) {
        if (selected === selection) clearDetail();
        invalidateChatSources();
      }
      status(action === "cancel" ? "停止请求已提交；以宿主返回的停止确认状态为准。" : "已保存，正在同步。");
    } catch (e) { status(`操作未确认：${e.message}；没有自动重试。`, true); }
    finally { actionBusy = false; updateControls(); schedule(0); }
  }
  async function configure(action, extra = {}) {
    if (actionBusy || !epoch) return;
    actionBusy = true; controlGeneration++; viewGeneration++; updateControls();
    const session = epoch, generation = controlGeneration;
    const resultId = action === "policy" ? "workers-policy-result" : "workers-pairing-result";
    const pairing = action === "connect" || action === "connect_managed";
    text(resultId, pairing ? "正在连接本机 DSH…" : action === "rotate_token" ? "正在更换本机保存的令牌…"
      : action === "disconnect" ? "正在停止自有运行并断开…" : "正在保存授权…");
    $(resultId).dataset.error = "false";
    const payload = { session_epoch: session, request_id: id(), ...(action === "policy" ? {} : { action }), ...extra };
    try {
      const data = await request("/api/collaborators", payload);
      if (session !== epoch || generation !== controlGeneration || data.session_epoch !== session) return;
      policyDirty = false; setRegistry(data);
      const setup = typeof data.setup_message === "string" ? data.setup_message.slice(0, 1000) : "";
      text(resultId, pairing ? (data.connection?.connected ? "已配对。请继续检查并保存任务授权。"
        : action === "connect_managed" ? setup || (data.credential?.configured === true
          ? "令牌已保存，DSH NEXT 需加载新版插件后再次连接。" : "连接尚未完成，请查看上方令牌与连接状态。")
          : "配对尚未完成，请查看上方连接原因。")
        : action === "rotate_token" ? setup || "新令牌已保存，旧令牌已失效。请重新连接；派工授权仍关闭。"
        : action === "disconnect" ? "已断开并撤销授权，已保存令牌仍保留；任务停止是否确认请查看任务状态。"
        : data.policy_unchanged === true ? "授权设置未变化，已有任务记录与剩余额度保持不变。"
        : data.policy?.enabled ? "授权已保存。现在可以在主聊天向 Alice 描述任务。" : "授权已关闭；重新启用时需要再次确认任务范围。");
    } catch (e) {
      if (session !== epoch || generation !== controlGeneration) return;
      // A pairing error must never echo a token, even if an upstream error does.
      const message = action === "connect_managed" ? "连接未确认。请同步令牌状态，并确认 DSH NEXT 已加载 0.4 版插件且启用执行许可，再手动连接；没有自动重试。"
        : action === "rotate_token" ? "更换令牌未确认。请同步本机令牌状态；没有自动重试。"
        : action === "connect" ? "配对未确认。请核对桥已启用、执行许可已开启及令牌一致，再查看连接状态；没有自动重试。"
        : `操作未确认：${e.message}；没有自动重试。`;
      text(resultId, message); $(resultId).dataset.error = "true";
    } finally {
      if ("token" in payload) payload.token = "";
      if ("token" in extra) extra.token = "";
      $("workers-token").value = "";
      actionBusy = false; updateControls(); schedule(0);
    }
  }
  async function download(taskId, artifactId, name) {
    const session = epoch;
    try {
      const response = await fetch("/api/worker-artifacts/download", { method: "POST", headers: { "X-Alice-Intent": "workers", "Content-Type": "application/json" },
        body: JSON.stringify({ session_epoch: session, task_id: taskId, artifact_id: artifactId }), cache: "no-store" });
      if (!response.ok) throw new Error("产物尚不可下载或授权已经变化");
      const blob = await response.blob(); if (epoch !== session) return;
      const url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = (name || "artifact").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_");
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { status(e.message, true); }
  }
  $("workers-btn").onclick = () => opened ? close() : open();
  $("workers-close").onclick = () => close();
  $("workers-refresh").onclick = poll;
  $("workers-settings-refresh").onclick = poll;
  $("workers-unarchived").onclick = () => selectArchiveView(false);
  $("workers-archived").onclick = () => selectArchiveView(true);
  $("workers-more").onclick = () => syncList(true).catch((e) => status(e.message, true));
  $("workers-events-more").onclick = () => syncDetail(true).catch((e) => status(e.message, true));
  $("workers-events-restart").onclick = () => { clearDetail(); following = false; syncDetail(true).catch((e) => status(e.message, true)); };
  $("workers-back").onclick = () => {
    if (actionBusy) return;
    closeManagement();
    viewGeneration++; show("workers-detail", false); show("workers-list-view", true);
    $("workers-list-view").scrollTop = savedListScroll;
    [...$("workers-list").children].find((child) => child.dataset.taskId === selection)?.focus({ preventScroll: true });
  };
  $("workers-events").onscroll = () => {
    const el = $("workers-events"); following = el.scrollHeight - el.scrollTop - el.clientHeight < 35;
    show("workers-latest", !following);
  };
  $("workers-latest").onclick = () => { following = true; $("workers-events").scrollTop = $("workers-events").scrollHeight; show("workers-latest", false); };
  $("workers-supplement").onclick = () => {
    if (!selection || $("workers-supplement").disabled) return;
    const input = $("input"), reference = `关于协作任务 ${selection}，请补充：`;
    input.value += `${input.value ? "\n" : ""}${reference}`; input.dispatchEvent(new Event("input", { bubbles: true }));
    close(false); input.focus(); input.setSelectionRange(input.value.length, input.value.length);
  };
  $("workers-cancel").onclick = () => mutate("cancel");
  $("workers-review").onclick = () => { if (confirm("已亲自检查任务结果，并确认符合本次要求？这只记录人工结论，不执行任何任务。")) mutate("review", { review: "accepted" }); };
  $("workers-revoke").onclick = () => { if (confirm("撤销本任务正文及派生内容的使用资格？保留任务状态事实；已发生的外部动作不会被撤回。")) mutate("revoke"); };
  $("workers-archive").onclick = () => beginManagement(selectedTask?.archived ? "restore" : "archive");
  $("workers-delete").onclick = () => beginManagement("delete");
  $("workers-management-retry").onclick = () => beginManagement(selectedTask?.management_pending);
  $("workers-management-back").onclick = () => closeManagement(true);
  $("workers-management-confirm").onsubmit = (event) => { event.preventDefault(); manageTask(); };
  $("workers-task-settings").oninput = () => { taskSettingsDirty = true; updateControls(); };
  $("workers-task-settings").onsubmit = (event) => { event.preventDefault(); saveTaskSettings(); };
  $("workers-token").oninput = updateControls;
  $("workers-advanced").ontoggle = () => { if (!$("workers-advanced").open) { $("workers-token").value = ""; updateControls(); } };
  $("workers-managed-connect").onclick = () => { if (!$("workers-managed-connect").disabled) configure("connect_managed"); };
  $("workers-rotate-token").onclick = () => {
    if (!$("workers-rotate-token").disabled && confirm("更换同一 Windows 用户共用的桥接令牌？旧令牌会失效，其他 Alice 实例的旧连接也需要重新连接。DSH NEXT 读取新令牌后才能再次连接；此操作不会启用派工授权。")) configure("rotate_token");
  };
  $("workers-pairing").onsubmit = (event) => {
    event.preventDefault();
    if ($("workers-connect").disabled) return;
    const token = $("workers-token").value.trim(); $("workers-token").value = "";
    configure("connect", { token });
  };
  $("workers-disconnect").onclick = () => configure("disconnect");
  $("workers-policy").oninput = (event) => {
    policyDirty = true;
    if (!["workers-native-ack", "workers-enabled"].includes(event.target.id)) $("workers-native-ack").checked = false;
    if (event.target.id === "workers-enabled" && !event.target.checked) $("workers-native-ack").checked = false;
    updateControls();
  };
  $("workers-policy").onsubmit = (event) => {
    event.preventDefault();
    if ($("workers-policy-save").disabled) return;
    const enabled = $("workers-enabled").checked, max = Number($("workers-max-runs").value);
    const seconds = Number($("workers-max-seconds").value), workspace = $("workers-workspace").value.trim();
    const mode = $("workers-mode").value, acknowledged = $("workers-native-ack").checked;
    const invalid = (message) => { text("workers-policy-result", message); $("workers-policy-result").dataset.error = "true"; };
    if (!Number.isSafeInteger(max) || max < 0 || max > 100) { invalid("执行次数须为 0–100 的整数。"); return; }
    if (!Number.isSafeInteger(seconds) || seconds < 30 || seconds > 1800) { invalid("每次执行时限须为 30–1800 秒。"); return; }
    if (enabled && (!/^[a-z]:[\\/]/i.test(workspace) || /[\x00-\x1f]/.test(workspace))) { invalid("请填写明确的本机绝对目录，例如 D:\\AliceTasks。"); return; }
    if (!["read-only", "workspace-write"].includes(mode)) { invalid("请选择仅可查看或工作区内修改。"); return; }
    if (enabled && !acknowledged) { invalid("请先确认所选目录、模型外发及 DSH 原生权限边界。"); return; }
    if (enabled && (registry?.connection?.connected !== true || max < 1)) { invalid("启用前须配对 DSH，并至少授权 1 次执行。"); return; }
    configure("policy", { revision: registry.revision, enabled, max_runs: max, max_seconds: seconds,
      workspace_root: workspace, permission_mode: mode, native_permissions_ack: acknowledged });
  };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && opened && !app.inert && !app.classList.contains("nav-open") && (panel.contains(document.activeElement) || app.classList.contains("worker-narrow"))) {
      event.preventDefault(); if (management && !actionBusy) closeManagement(true); else close();
    }
  });
  document.addEventListener("visibilitychange", () => visible() ? poll() : clearTimeout(timer));
  new ResizeObserver(layout).observe(app);
  new MutationObserver(() => { if (visible()) schedule(0); else clearTimeout(timer); }).observe(app, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("pagehide", () => { $("workers-token").value = ""; clearTimeout(timer); controllers.forEach((controller) => controller.abort()); });
  window.AliceWorkers = { close, setSettingsVisible, shutdown() {
    stopped = true; setSettingsVisible(false); close(false); clearTimeout(timer); controllers.forEach((controller) => controller.abort());
  } };
  schedule(0);
})();
