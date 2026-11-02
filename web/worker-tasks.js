/* GUI-only, read-on-demand work records. Opening this panel never dispatches work. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("worker-tasks"), app = $("app");
  const activeStates = new Set(["queued", "starting", "preparing", "running", "working", "collecting", "stopping"]);
  const endedStates = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
  const managementNames = { archive: "归档", restore: "恢复", delete: "删除" };
  const states = { unverified: "接口未核实", unavailable: "未接入", unauthorized: "未授权", disabled: "未授权",
    ready: "可用", connected: "已连接", disconnected: "未连接", connecting: "正在连接", paused: "已暂停", draining: "正在断开", uncertain: "连接待确认", enabled: "已授权", blocked_permission_profile: "连接与权限待核实", queued: "排队", starting: "准备", preparing: "准备", running: "工作中", working: "工作中", collecting: "收取产物", waiting: "等待补充", waiting_input: "等待补充",
    stopping: "正在停止", completed: "已结束", succeeded: "已结束", failed: "失败", interrupted: "中断", unknown: "状态未知", cancelled: "已停止", stopped: "已停止", revoked: "正文已撤销" };
  const eventNames = { task_accepted: "宿主已接收任务", run_started: "开始执行", alice_message_queued: "任务消息已登记，等待发送",
    alice_message_sent: "Alice → DSH", agent_message_delta: "DSH → Alice · 公开回复", agent_message_completed: "DSH → Alice · 公开回复",
    agent_question: "DSH → Alice · 等待补充", tool_started: "工具开始", tool_completed: "工具结束", tool_failed: "工具失败",
    artifact_reported: "DSH 报告产物", artifact_accepted: "宿主已收取产物", usage_updated: "用量更新",
    cancel_requested: "已请求停止，等待确认", run_stopped: "运行停止", run_failed: "运行失败", connection_lost: "连接中断，执行状态待确认",
    run_completed: "本轮执行已结束", content_revoked: "任务正文已撤销", review_recorded: "人工核对记录", human_reviewed: "人工核对记录" };
  let opened = false, closing = false, closeFocus = false, settingsOpened = false, stopped = false, connected = false, timer = null, polling = false;
  let epoch = null, registry = null, nextBefore = null, selection = null, selectedTask = null, viewGeneration = 0;
  let eventCursor = 0, eventMore = false, listBusy = false, actionBusy = false, policyDirty = false;
  let archivedView = false, listGeneration = 0, listDepth = 40, management = null, taskSettingsDirty = false;
  let taskSignature = "", artifactSignature = "", following = true, savedListScroll = 0;
  let controlGeneration = 0, dispatchWatchUntil = 0;
  const tasks = new Map(), seen = new Map(), eventNodes = new Map(), messageNodes = new Map();
  const controllers = new Set();
  const readPrefix = "alice.collaborator-read.v1.dsh-agent.";
  const readIdentity = /^[A-Za-z0-9_-]{16,128}$/;
  function readKey(taskId) {
    return typeof epoch === "string" && readIdentity.test(epoch) && typeof taskId === "string" && readIdentity.test(taskId)
      ? `${readPrefix}${epoch}.${taskId}` : null;
  }
  function readValue(value) {
    // -1 is a deletion marker: another tab's late response cannot revive it.
    if (value === "-1") return -1;
    if (typeof value !== "string" || !/^(0|[1-9][0-9]{0,15})$/.test(value)) return 0;
    const sequence = Number(value);
    return Number.isSafeInteger(sequence) ? sequence : 0;
  }
  function savedRead(taskId) {
    const key = readKey(taskId);
    try { return key ? readValue(localStorage.getItem(key)) : 0; }
    catch { return 0; } // Reading still works when browser storage is unavailable.
  }
  function readThrough(taskId) {
    if (!seen.has(taskId)) seen.set(taskId, savedRead(taskId));
    return Math.max(0, seen.get(taskId) || 0);
  }
  function markSeen(taskId, sequence) {
    const key = readKey(taskId);
    if (!key || !Number.isSafeInteger(sequence) || sequence < 0) return;
    const saved = savedRead(taskId), previous = seen.get(taskId) || 0;
    if (saved === -1 || previous === -1) { seen.set(taskId, -1); return; }
    const next = Math.max(saved, previous, sequence);
    seen.set(taskId, next);
    if (next > saved) {
      try { localStorage.setItem(key, String(next)); } catch { /* Keep this page's read position. */ }
    }
  }
  function forgetSeen(taskId) {
    const key = readKey(taskId);
    seen.set(taskId, -1);
    try { if (key) localStorage.setItem(key, "-1"); } catch { /* No task text is stored here. */ }
  }
  function canReadSelected(taskId) {
    return opened && selection === taskId && !stopped && !document.hidden && !settingsOpened
      && !app.inert && !panel.inert && !app.classList.contains("hidden") && !app.classList.contains("nav-open")
      && !$("workers-detail").classList.contains("hidden") && panel.getClientRects().length > 0;
  }
  function markVisibleRead() {
    if (!canReadSelected(selection) || !following || eventMore || $("workers-history").open
        || !selectedTask || eventCursor < Number(selectedTask.last_sequence || 0)) return;
    markSeen(selection, eventCursor); renderList();
  }
  window.addEventListener("storage", (event) => {
    if (!epoch) return;
    try { if (event.storageArea !== localStorage) return; } catch { return; }
    if (event.key === null) { seen.clear(); renderList(); return; }
    const prefix = `${readPrefix}${epoch}.`;
    if (!event.key.startsWith(prefix)) return;
    const taskId = event.key.slice(prefix.length);
    if (!readIdentity.test(taskId)) return;
    if (event.newValue === null) seen.delete(taskId);
    else if (readValue(event.newValue) === -1 || seen.get(taskId) === -1) forgetSeen(taskId);
    else markSeen(taskId, readValue(event.newValue));
    renderList();
  });
  const node = (tag, value, cls) => { const n = document.createElement(tag); if (value !== undefined) n.textContent = String(value); if (cls) n.className = cls; return n; };
  const show = (id, yes) => $(id).classList.toggle("hidden", !yes);
  const text = (id, value) => { if ($(id).textContent !== String(value)) $(id).textContent = String(value); };
  const time = (value) => value ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString("zh-CN", { hour12: false }) : "未记录";
  const state = (value) => states[value] || "状态待确认";
  const visible = () => !stopped && !document.hidden && (settingsOpened || !app.classList.contains("hidden"));
  const current = (id, generation, session) => opened && !$("workers-detail").classList.contains("hidden") && selection === id && generation === viewGeneration && session === epoch;
  const id = () => crypto.randomUUID();
  function taskContentState(task = {}) {
    const reasons = {
      revoked: ["正文已撤销", "任务正文已撤销，仅保留任务状态事实。"],
      deleted: ["任务已删除", "任务已删除，正文不再可用。"],
      expired: ["资料已过期", "任务资料已过期，正文不再可用。"],
      policy_changed: ["授权范围已变更", "任务正文所属的授权范围已变更，旧正文不再可用。"],
      authorization_disabled: ["待恢复授权", "授权已关闭，正文暂不可用；恢复原授权后可重新读取仍有效的资料。"]
    };
    const reason = typeof task.content_unavailable_reason === "string" ? task.content_unavailable_reason
      : task.state === "revoked" ? "revoked" : null;
    const unavailable = !!reason || task.content_available === false
      || task.content_available !== true && task.content_revoked === true;
    const description = reasons[reason] || ["正文暂不可用", "正文当前不可用，请同步授权状态后再查看。"];
    return { available: !unavailable, reason, label: unavailable ? description[0] : "", message: description[1] };
  }
  function status(message, error = false) {
    for (const name of ["workers-status", "workers-settings-status"]) {
      text(name, message); $(name).dataset.error = String(error); $(name).title = message;
    }
  }
  function invalidateChatSources() { window.dispatchEvent(new Event("alice:sources-changed")); }
  function avatarSizeHint(button) {
    return `双击${button.dataset.size === "small" ? "放大" : "缩小"}头像`;
  }
  function setupAvatarDrag(button) {
    const area = button.closest(".chat-conversation");
    const art = button.querySelector(".collaborator-avatar-art");
    const decoration = button.querySelector(".collaborator-avatar-frame");
    const visualParts = [art, $("workers-avatar-status"), $("workers-count")];
    const portraits = [...button.querySelectorAll(".collaborator-avatar-image")];
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const storageKey = `alice.collaborator-position.v1.${button.dataset.worker}`;
    const clamp = (n) => Math.max(0, Math.min(1, n));
    const doubleClickMs = 500;
    let position = { x: 1, y: 1 }, drag = null, suppressClick = false, frame = null;
    let clickTimer = null, pendingClick = null, workBaseline = null;
    let sizeAnimations = [], areaWidth = area.clientWidth, areaHeight = area.clientHeight;
    const interactive = () => !stopped && !document.hidden && !button.closest("[inert]")
      && !app.classList.contains("hidden") && !app.classList.contains("nav-open")
      && button.getClientRects().length > 0 && getComputedStyle(button).visibility !== "hidden";
    function clearClick() {
      clearTimeout(clickTimer); clickTimer = null; pendingClick = null;
    }
    function activate() { if (interactive()) opened ? close() : open(); }
    function stopSizeMotion() {
      const animations = sizeAnimations;
      sizeAnimations = [];
      for (const animation of animations) { animation.onfinish = null; animation.cancel(); }
      delete button.dataset.sizeHover;
    }
    function cancelInteraction() { finish(true); stopSizeMotion(); }
    function setSize(size) {
      if (button.dataset.size === size) return;
      if (closing) windowFrame.completeTransition();
      // Capture an in-flight transition before cancelling it, so reversing the
      // size change starts at the current visual position and opacity.
      const before = button.getBoundingClientRect();
      const beforeFrame = decoration.getBoundingClientRect();
      const beforeParts = visualParts.map((part) => part.getBoundingClientRect());
      const opacity = portraits.map((image) => getComputedStyle(image).opacity);
      const hovered = button.dataset.sizeHover ?? String(button.matches(":hover"));
      stopSizeMotion();
      clearClick(); finish(true);
      button.dataset.size = size;
      text("workers-avatar-help", `拖动调整位置；单击或 Enter 打开会话；${avatarSizeHint(button)}。`);
      renderList(); place();
      const after = button.getBoundingClientRect();
      const afterFrame = decoration.getBoundingClientRect();
      const afterParts = visualParts.map((part) => part.getBoundingClientRect());
      if (reducedMotion.matches || typeof button.animate !== "function" || !interactive()
          || !before.width || !before.height || !after.width || !after.height) return;
      // Commit button layout once. Only the absolute decorative frame changes
      // dimensions during motion, so drag bounds and ResizeObserver stay stable.
      const timing = { duration: 240, easing: "cubic-bezier(.22, 1, .36, 1)" };
      button.dataset.sizeHover = hovered;
      const dx = before.left - after.left, dy = before.top - after.top;
      const animations = [button.animate([
        { transform: `translate(${dx}px, ${dy}px)` },
        { transform: "none" },
      ], timing)];
      animations.push(decoration.animate([
        { width: `${beforeFrame.width}px`, height: `${beforeFrame.height}px`,
          transform: `translate(${beforeFrame.left - afterFrame.left - dx}px, ${beforeFrame.top - afterFrame.top - dy}px)` },
        { width: `${afterFrame.width}px`, height: `${afterFrame.height}px`, transform: "none" },
      ], timing));
      // Scale only the square portrait, uniformly. Labels and badges move
      // between their old/new positions without stretching their text.
      visualParts.forEach((part, index) => {
        const from = beforeParts[index], to = afterParts[index];
        if (!from.width || !from.height || !to.width || !to.height) return;
        const scale = part === art ? from.width / to.width : 1;
        animations.push(part.animate([
          { transform: `translate(${from.left - to.left - dx}px, ${from.top - to.top - dy}px) scale(${scale})` },
          { transform: "none" },
        ], timing));
      });
      portraits.forEach((image, index) => {
        animations.push(image.animate([{ opacity: opacity[index] }, { opacity: getComputedStyle(image).opacity }], timing));
      });
      sizeAnimations = animations;
      animations[0].onfinish = () => { if (sizeAnimations === animations) stopSizeMotion(); };
    }
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        position = { x: clamp(saved.x), y: clamp(saved.y) };
      }
    } catch { /* Position storage is optional, including in private browsing. */ }
    function bounds() {
      const width = area.clientWidth, height = area.clientHeight;
      if (!width || !height || !button.offsetWidth || !button.offsetHeight) return null;
      if (width < button.offsetWidth || height < button.offsetHeight) return null;
      const insetX = Math.min(12, Math.max(0, (width - button.offsetWidth) / 2));
      const insetY = Math.min(12, Math.max(0, (height - button.offsetHeight) / 2));
      return { left: insetX, top: insetY,
        width: Math.max(0, width - button.offsetWidth - insetX * 2),
        height: Math.max(0, height - button.offsetHeight - insetY * 2) };
    }
    function place() {
      const box = bounds();
      // A collapsed conversation must not leave the avatar over the composer.
      // Visibility preserves its size, so the next resize can restore it.
      button.style.visibility = box && !opened && !closing ? "" : "hidden";
      if (!box) return;
      button.style.left = `${box.left + position.x * box.width}px`;
      button.style.top = `${box.top + position.y * box.height}px`;
      button.style.right = "auto"; button.style.bottom = "auto";
    }
    function move(left, top, box) {
      position = { x: box.width ? clamp((left - box.left) / box.width) : position.x,
        y: box.height ? clamp((top - box.top) / box.height) : position.y };
      place();
    }
    function save() {
      try { localStorage.setItem(storageKey, JSON.stringify(position)); } catch { /* Drag still works. */ }
    }
    function finish(cancelled = false) {
      if (cancelled) clearClick();
      if (!drag) return;
      const ended = drag; drag = null;
      button.dataset.dragging = "false";
      if (cancelled) suppressClick = true;
      if (ended.moved) {
        clearClick();
        suppressClick = true;
        if (cancelled) position = ended.position;
        else save();
      }
      if (button.hasPointerCapture(ended.pointerId)) button.releasePointerCapture(ended.pointerId);
      place();
    }
    button.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !event.isPrimary || drag || button.closest("[inert]")) return;
      const box = bounds();
      if (!box) return;
      // Hold a pending single click while a possible second press is down.
      clearTimeout(clickTimer);
      suppressClick = false;
      drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY,
        left: box.left + position.x * box.width, top: box.top + position.y * box.height,
        position: { ...position }, moved: false, box };
      try { button.setPointerCapture(event.pointerId); } catch { drag = null; }
    });
    button.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (button.closest("[inert]") || !bounds()) { finish(true); return; }
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 6) return;
      if (!drag.moved && sizeAnimations.length) {
        // A press may become another double click. Finish motion only once a
        // real drag starts, taking over its current position without a jump.
        const current = button.getBoundingClientRect();
        stopSizeMotion();
        const settled = button.getBoundingClientRect();
        drag.left += current.left - settled.left; drag.top += current.top - settled.top;
        drag.x = event.clientX; drag.y = event.clientY;
      }
      clearClick();
      drag.moved = true; button.dataset.dragging = "true";
      event.preventDefault();
      move(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y, drag.box);
    });
    button.addEventListener("pointerup", (event) => {
      if (drag && event.pointerId === drag.pointerId) finish();
    });
    for (const name of ["pointercancel", "lostpointercapture"]) {
      button.addEventListener(name, (event) => { if (drag?.pointerId === event.pointerId) finish(true); });
    }
    button.addEventListener("click", (event) => {
      if (suppressClick && (event.detail !== 0 || event.pointerType)) {
        suppressClick = false; clearClick(); event.preventDefault(); event.stopImmediatePropagation(); return;
      }
      if (!interactive()) { clearClick(); return; }
      if (event.detail === 0 && !event.pointerType) { clearClick(); activate(); return; }
      const now = performance.now(), pointerType = event.pointerType || "mouse";
      if (pendingClick && now - pendingClick.at <= doubleClickMs && pendingClick.pointerType === pointerType
          && Math.hypot(event.clientX - pendingClick.x, event.clientY - pendingClick.y) <= 12) {
        event.preventDefault();
        setSize(button.dataset.size === "small" ? "large" : "small"); return;
      }
      clearClick();
      pendingClick = { at: now, x: event.clientX, y: event.clientY, pointerType };
      // Delay only pointer activation: a double click changes size without
      // opening the conversation. The same gesture also supports touch taps.
      clickTimer = setTimeout(() => { clearClick(); if (!drag) activate(); }, doubleClickMs);
    }, true);
    button.addEventListener("dblclick", (event) => event.preventDefault());
    button.addEventListener("dragstart", (event) => event.preventDefault());
    button.addEventListener("keydown", (event) => {
      if (event.key === "Escape") clearClick();
      if (event.key === "Escape" && drag) { event.preventDefault(); event.stopPropagation(); finish(true); return; }
    });
    const resize = new ResizeObserver(() => {
      if (area.clientWidth !== areaWidth || area.clientHeight !== areaHeight) {
        areaWidth = area.clientWidth; areaHeight = area.clientHeight; stopSizeMotion();
      }
      if (drag) finish(true);
      cancelAnimationFrame(frame); frame = requestAnimationFrame(place);
    });
    resize.observe(area); resize.observe(button);
    window.addEventListener("blur", cancelInteraction);
    document.addEventListener("visibilitychange", () => { if (document.hidden) cancelInteraction(); });
    window.addEventListener("pagehide", () => { cancelInteraction(); cancelAnimationFrame(frame); resize.disconnect(); });
    window.addEventListener("pageshow", () => { resize.observe(area); resize.observe(button); place(); });
    reducedMotion.addEventListener("change", () => { if (reducedMotion.matches) stopSizeMotion(); });
    place();
    return {
      cancel: cancelInteraction,
      layout: place,
      targetRect() { return bounds() && button.getClientRects().length ? art.getBoundingClientRect() : null; },
      syncWork(data) {
        const used = data.policy?.runs_used;
        if (!Number.isSafeInteger(used) || used < 0 || !Number.isSafeInteger(data.revision)) return;
        if (!workBaseline || workBaseline.epoch !== data.session_epoch || workBaseline.revision !== data.revision) {
          workBaseline = { epoch: data.session_epoch, revision: data.revision, used }; return;
        }
        if (used > workBaseline.used) {
          workBaseline.used = used;
          setSize("large");
          return true;
        }
      },
    };
  }
  function renderAvatar(running = 0, unread = 0) {
    const worker = registry?.collaborators?.find((row) => row.name === "dsh-agent");
    // Registration controls which tools are represented; a temporary disconnect
    // must not remove the entry needed to read that tool's existing sessions.
    show("collaborator-dock", !!worker);
    const button = $("workers-btn");
    const paired = registry?.connection?.connected === true && worker?.available === true;
    const statusLabel = !connected ? "状态待同步" : !paired ? "未连接" : running ? "工作中" : "已连接";
    button.dataset.state = !connected ? "unknown" : !paired ? "disconnected" : running ? "working" : "connected";
    panel.dataset.state = button.dataset.state;
    const portrait = $("workers-window-portrait"), source = button.querySelector(button.dataset.size === "small"
      ? ".collaborator-avatar-small" : ".collaborator-avatar-large").src;
    if (portrait.src !== source) portrait.src = source;
    button.dataset.unread = String(unread > 0);
    text("workers-avatar-status", statusLabel);
    text("workers-count", unread ? String(Math.min(unread, 99)) + (unread > 99 ? "+" : "") : "");
    const details = [statusLabel, running ? `${running} 个任务运行中` : "", unread ? `${unread} 个任务未读` : ""].filter(Boolean).join("，");
    button.setAttribute("aria-label", `${opened ? "收起" : "打开"} DSH 会话，${details}`);
    button.title = `DSH · ${details}。单击查看会话，${avatarSizeHint(button)}，拖动调整位置。`;
  }
  function setupWorkerWindow() {
    const handle = $("workers-window-handle"), dockButton = $("workers-window-dock");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const storageKey = "alice.collaborator-window.v1.dsh-agent";
    const clamp = (n) => Math.max(0, Math.min(1, n));
    let mode = "floating", position = { x: 1, y: 0.08 }, drag = null, suppressClick = false;
    let expansion = [], expansionArea = null, collapseDone = null;
    const timing = { duration: 420, easing: "cubic-bezier(.4, 0, .2, 1)", fill: "both" };
    function stopExpansion() {
      const animations = expansion, done = collapseDone;
      expansion = []; collapseDone = null;
      for (const animation of animations) { animation.onfinish = null; animation.cancel(); }
      if (done) done();
    }
    function foldedFrame(avatar, portrait, frame) {
      const left = Math.max(0, portrait.left - frame.left - 5);
      const top = Math.max(0, portrait.top - frame.top - 5);
      const right = Math.max(0, frame.right - (portrait.left + avatar.width + 5));
      const bottom = Math.max(0, frame.bottom - (portrait.top + avatar.height + 5));
      return { transform: `translate(${avatar.left - portrait.left}px, ${avatar.top - portrait.top}px)`,
        clipPath: `inset(${top}px ${right}px ${bottom}px ${left}px round 10px)` };
    }
    const expandedFrame = { transform: "none", clipPath: "inset(0 0 0 0 round 12px)" };
    function animateWindow(avatar, portrait, frame, collapsing = false, from = null) {
      const folded = foldedFrame(avatar, portrait, frame);
      const animations = [panel.animate(collapsing ? [from?.panel || expandedFrame, folded] : [folded, expandedFrame], timing)];
      // Desktop and window portraits share their size; only compact mode needs
      // a size transition. Both directions use the same geometry and timing.
      if (Math.abs(avatar.width - portrait.width) > .5 || from?.portrait && from.portrait !== "none") {
        const small = { transform: `scale(${avatar.width / portrait.width})` }, large = { transform: "none" };
        animations.push($("workers-window-portrait").animate(collapsing ? [{ transform: from?.portrait || "none" }, small] : [small, large], timing));
      }
      expansionArea = area(); expansion = animations;
      animations[0].onfinish = () => { if (expansion === animations) stopExpansion(); };
    }
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) position = { x: clamp(saved.x), y: clamp(saved.y) };
    } catch { /* Window placement is optional, local-only presentation state. */ }
    function area() {
      const style = getComputedStyle(app), nav = $("app-nav");
      const gap = parseFloat(style.gap) || 0;
      const left = (parseFloat(style.paddingLeft) || 0)
        + (nav.getClientRects().length && getComputedStyle(nav).position !== "fixed" ? nav.offsetWidth + gap : 0);
      const top = parseFloat(style.paddingTop) || 0;
      return { left, top,
        width: Math.max(0, app.clientWidth - left - (parseFloat(style.paddingRight) || 0)),
        height: Math.max(0, app.clientHeight - top - (parseFloat(style.paddingBottom) || 0)) };
    }
    const dockWidth = () => Math.min(780, Math.max(620, window.innerWidth * 0.44));
    function canDock() { const box = area(); return box.width - dockWidth() >= 460 && box.height >= 320; }
    function save() {
      try { localStorage.setItem(storageKey, JSON.stringify(position)); } catch { /* Still movable. */ }
    }
    function render() {
      if (closing) return;
      if (mode === "docked" && !canDock()) mode = "floating";
      panel.dataset.presentation = mode;
      app.classList.toggle("workers-docked", opened && mode === "docked");
      dockButton.textContent = mode === "docked" ? "恢复浮窗" : "固定右侧";
      dockButton.disabled = mode !== "docked" && !canDock();
      dockButton.title = dockButton.disabled ? "窗口较窄，保留浮窗以免挤压主聊天" : mode === "docked" ? "移出侧栏，恢复浮动窗口" : "将此窗口固定在右侧";
      if (!opened) return;
      if (mode === "docked") {
        for (const prop of ["left", "top", "right", "bottom", "width", "height", "maxWidth", "maxHeight"]) panel.style[prop] = "";
        return;
      }
      const box = area();
      if (!box.width || !box.height) return;
      const width = Math.min(960, box.width), height = Math.min(680, box.height);
      Object.assign(panel.style, { width: `${width}px`, height: `${height}px`, maxWidth: `${box.width}px`, maxHeight: `${box.height}px`,
        right: "auto", bottom: "auto", left: `${box.left + position.x * (box.width - width)}px`,
        top: `${box.top + position.y * (box.height - height)}px` });
    }
    function atRightEdge(event) {
      const box = area(), rect = app.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      return canDock() && x >= box.left + box.width - 32 && x <= app.clientWidth
        && y >= box.top && y <= box.top + box.height;
    }
    function preview(yes) {
      const box = area(), hint = $("workers-dock-preview");
      if (yes) Object.assign(hint.style, { top: `${box.top}px`, right: `${app.clientWidth - box.left - box.width}px`,
        height: `${box.height}px`, width: `${dockWidth()}px` });
      show("workers-dock-preview", yes);
    }
    function finish(cancelled = false, event = null) {
      if (!drag) return;
      const ended = drag; drag = null;
      panel.dataset.dragging = "false"; preview(false);
      if (ended.moved) {
        suppressClick = true;
        if (cancelled) { mode = ended.mode; position = ended.position; }
        else {
          mode = event && atRightEdge(event) ? "docked" : "floating";
          save();
        }
      }
      if (handle.hasPointerCapture(ended.pointerId)) handle.releasePointerCapture(ended.pointerId);
      render();
    }
    handle.addEventListener("pointerdown", (event) => {
      if (!opened || drag || event.button !== 0 || !event.isPrimary || panel.closest("[inert]")) return;
      // A cancelled gesture may have no trailing click. A new press starts fresh,
      // including a press on the close/dock controls inside the drag handle.
      suppressClick = false;
      if (event.target.closest("button,a,input,select,textarea")) return;
      stopExpansion();
      const rect = panel.getBoundingClientRect();
      drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, moved: false,
        mode, position: { ...position }, anchorX: clamp((event.clientX - rect.left) / rect.width),
        anchorY: event.clientY - rect.top };
      try { handle.setPointerCapture(event.pointerId); event.preventDefault(); } catch { drag = null; }
    });
    handle.addEventListener("pointermove", (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      if (!opened || panel.closest("[inert]") || !panel.getClientRects().length) { finish(true); return; }
      if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 6) return;
      drag.moved = true; mode = "floating"; render(); panel.dataset.dragging = "true";
      const box = area(), rect = app.getBoundingClientRect();
      const width = Math.min(960, box.width), height = Math.min(680, box.height);
      const left = event.clientX - rect.left - drag.anchorX * width;
      const top = event.clientY - rect.top - Math.min(drag.anchorY, Math.max(0, height - 36));
      position = { x: box.width > width ? clamp((left - box.left) / (box.width - width)) : position.x,
        y: box.height > height ? clamp((top - box.top) / (box.height - height)) : position.y };
      event.preventDefault(); render(); preview(atRightEdge(event));
    });
    handle.addEventListener("pointerup", (event) => {
      if (drag?.pointerId === event.pointerId) finish(!!panel.closest("[inert]") || !opened, event);
    });
    for (const name of ["pointercancel", "lostpointercapture"]) {
      handle.addEventListener(name, (event) => { if (drag?.pointerId === event.pointerId) finish(true); });
    }
    handle.addEventListener("click", (event) => {
      if (suppressClick && (event.detail !== 0 || event.pointerType)) {
        suppressClick = false; event.preventDefault(); event.stopImmediatePropagation();
      }
    }, true);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drag) { event.preventDefault(); event.stopImmediatePropagation(); finish(true); }
    }, true);
    dockButton.onclick = () => {
      if (!opened || dockButton.disabled) return;
      stopExpansion(); finish(true); mode = mode === "docked" ? "floating" : "docked"; render();
    };
    window.addEventListener("blur", () => { stopExpansion(); finish(true); });
    document.addEventListener("visibilitychange", () => { if (document.hidden) { stopExpansion(); finish(true); } });
    window.addEventListener("pagehide", () => { stopExpansion(); finish(true); });
    window.addEventListener("pageshow", render);
    reducedMotion.addEventListener("change", () => { if (reducedMotion.matches) stopExpansion(); });
    return {
      completeTransition: stopExpansion,
      open(origin) {
        stopExpansion(); finish(true); mode = "floating";
        $("workers-list-view").closest(".worker-workspace").scrollLeft = 0;
        const box = area(), rect = app.getBoundingClientRect();
        const width = Math.min(960, box.width), height = Math.min(680, box.height);
        if (origin?.width && origin?.height) {
          // Keep the expanded workspace near its avatar, clamped to the main
          // content area. The desktop avatar retains its own saved position.
          position = { x: box.width > width ? clamp((origin.left - rect.left - box.left - 12) / (box.width - width)) : 0,
            y: box.height > height ? clamp((origin.top - rect.top - box.top - 12) / (box.height - height)) : 0 };
        }
        render();
        if (!origin?.width || !origin?.height || reducedMotion.matches || typeof panel.animate !== "function" || document.hidden) return;
        const target = $("workers-window-portrait").getBoundingClientRect(), frame = panel.getBoundingClientRect();
        if (!target.width || !target.height || !frame.width || !frame.height) return;
        animateWindow(origin, target, frame);
      },
      close(done, animate = true) {
        const style = getComputedStyle(panel);
        const from = expansion.length ? { panel: { transform: style.transform, clipPath: style.clipPath },
          portrait: getComputedStyle($("workers-window-portrait")).transform } : null;
        stopExpansion(); finish(true); preview(false);
        const frame = panel.getBoundingClientRect(), workspace = $("workers-list-view").closest(".worker-workspace");
        const portrait = $("workers-window-portrait").getBoundingClientRect();
        // Freeze a docked window before releasing its flex slot, so the main
        // chat can settle and supply the avatar's final return position.
        const rect = app.getBoundingClientRect();
        panel.dataset.presentation = "floating";
        Object.assign(panel.style, { left: `${frame.left - rect.left}px`, top: `${frame.top - rect.top}px`,
          width: `${frame.width}px`, height: `${frame.height}px`, maxWidth: "none", maxHeight: "none", right: "auto", bottom: "auto" });
        app.classList.remove("workers-docked"); avatarControl.layout();
        const target = avatarControl.targetRect();
        collapseDone = done;
        if (!animate || reducedMotion.matches || typeof panel.animate !== "function" || document.hidden
            || !target?.width || !target?.height || !frame.width || !frame.height || !portrait.width) { stopExpansion(); return; }
        if (workspace.scrollLeft > 0) {
          // On a narrow scrolled workspace the portrait is off-screen; fade
          // the window rather than moving a clipped portrait across the chat.
          const animations = [panel.animate([{ opacity: 1 }, { opacity: 0 }], timing)];
          expansionArea = area(); expansion = animations;
          animations[0].onfinish = () => { if (expansion === animations) stopExpansion(); };
          return;
        }
        animateWindow(target, portrait, frame, true, from);
      },
      layout() {
        if (expansion.length && expansionArea) {
          const box = area();
          if (["left", "top", "width", "height"].some((key) => box[key] !== expansionArea[key])) stopExpansion();
        }
        if (!drag) render();
      }
    };
  }
  function showTaskDetail(yes) {
    show("workers-list-view", true);
    show("workers-detail", yes);
    show("workers-chat-empty", !yes);
  }
  function resetList() {
    listGeneration++; listDepth = 40; nextBefore = null; savedListScroll = 0;
    tasks.clear(); $("workers-list").replaceChildren(); renderList();
  }
  function closeManagement(returnFocus = false) {
    const action = management?.action; management = null; show("workers-management-confirm", false);
    if (returnFocus && selectedTask) $(selectedTask.management_pending ? "workers-management-retry" : action === "delete" ? "workers-delete" : "workers-archive").focus({ preventScroll: true });
  }
  function clearEventView(message = "") {
    eventNodes.clear(); messageNodes.clear();
    $("workers-messages").replaceChildren(...(message ? [node("p", message, "worker-hint")] : []));
    $("workers-history-events").replaceChildren();
    $("workers-history").open = false; show("workers-history", false);
    text("workers-history-summary", "工具与执行记录");
    text("workers-tool-note", "");
  }
  function hideTaskBody(message) {
    eventCursor = 0; eventMore = false; artifactSignature = "";
    clearEventView(message); $("workers-artifacts").replaceChildren();
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
    $("workers-task-menu").open = false;
    viewGeneration++; selectedTask = null; eventCursor = 0; eventMore = false; following = true;
    taskSignature = ""; artifactSignature = ""; clearEventView();
    $("workers-artifacts").replaceChildren();
    for (const name of ["workers-task-title", "workers-task-state", "workers-task-meta", "workers-tool-note", "workers-review-state", "workers-management-hint", "workers-management-result"]) text(name, "");
    for (const name of ["workers-cancel", "workers-supplement", "workers-review", "workers-revoke", "workers-archive", "workers-delete", "workers-management-retry"]) $(name).disabled = true;
    show("workers-management-retry", false);
    show("workers-events-more", false); show("workers-events-restart", false); show("workers-latest", false);
  }
  function acceptEpoch(next) {
    if (typeof next !== "string" || !readIdentity.test(next)) throw new Error("服务未返回有效会话身份");
    if (epoch && epoch !== next) {
      seen.clear(); clearDetail(); selection = null; archivedView = false; resetList();
      showTaskDetail(false);
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
    if (avatarControl.syncWork(data)) dispatchWatchUntil = 0;
    const worker = data.collaborators?.find((row) => row.name === "dsh-agent");
    text("workers-connection", worker ? `DSH · ${state(worker.state)}${worker.reason ? `：${worker.reason}` : ""}` : "宿主未返回 DSH 注册信息，暂不可启用。");
    const paired = data.connection?.connected === true;
    text("workers-capabilities", paired ? (data.policy?.enabled ? "已连接，现有授权和剩余额度继续有效。请在主聊天描述任务，由 Alice 派工。" : "已连接，派工授权尚未开启。可核对原范围后恢复授权，或设置新的任务范围。")
      : data.policy?.enabled ? "连接已断开，授权和剩余额度仍保留，仍有效的任务正文可以查看。请使用已保存令牌重新连接后继续派工。"
      : "当前未连接，派工授权尚未开启。请连接后核对任务范围；同步状态只读取已有记录。");
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
      $("workers-native-ack").checked = data.policy?.native_permissions_ack === true;
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
    $("workers-disconnect").disabled = actionBusy || !epoch || !hasConnection;
    for (const name of ["workers-max-runs", "workers-max-seconds", "workers-workspace", "workers-mode", "workers-native-ack"]) $(name).disabled = actionBusy || !canUse;
    $("workers-enabled").disabled = actionBusy || !epoch || !canUse && !registry?.policy?.enabled;
    $("workers-policy-save").disabled = actionBusy || !epoch || !canUse && !registry?.policy?.enabled;
    $("workers-policy-save").textContent = $("workers-enabled").checked ? registry?.policy?.enabled ? "保存授权设置" : "保存并开启授权" : "保存为关闭授权";
    const taskSettings = registry?.task_management;
    const settingsKnown = Number.isSafeInteger(taskSettings?.revision);
    $("workers-sync-archive").disabled = actionBusy || !epoch || !settingsKnown;
    $("workers-sync-delete").disabled = actionBusy || !epoch || !settingsKnown || taskSettings?.capabilities?.delete !== true;
    $("workers-task-settings-save").disabled = actionBusy || !epoch || !settingsKnown;
    for (const name of ["workers-unarchived", "workers-archived"]) $(name).disabled = actionBusy || !epoch;
    updateTaskControls();
    updateManagementControls();
  }
  function renderList() {
    const rows = [...tasks.values()].filter((task) => (task.archived === true) === archivedView)
      .sort((a, b) => Number(activeStates.has(b.state)) - Number(activeStates.has(a.state)) || Number(b.updated_at || 0) - Number(a.updated_at || 0));
    // A main-chat reference can target an older conversation beyond the loaded
    // page. Keep that explicitly selected conversation visible in the sidebar.
    if (selectedTask?.task_id === selection && (selectedTask.archived === true) === archivedView
        && !rows.some((task) => task.task_id === selection)) rows.unshift(selectedTask);
    const shown = new Set(rows.map((task) => task.task_id));
    for (const child of [...$("workers-list").children]) if (!shown.has(child.dataset.taskId)) child.remove();
    $("workers-unarchived").setAttribute("aria-pressed", String(!archivedView));
    $("workers-archived").setAttribute("aria-pressed", String(archivedView));
    text("workers-empty", archivedView ? "还没有已归档任务。归档后的任务保留在这里，可查看、恢复或删除。"
      : registry?.connection?.connected && registry?.policy?.enabled ? "还没有未归档任务。在主聊天告诉 Alice 要做什么；也可以切换到“已归档”查看旧记录。"
      : "还没有未归档任务。请在“设置 → DSH”连接并授权任务范围，然后在主聊天描述目标。");
    let unread = 0, running = 0;
    for (const [position, task] of rows.entries()) {
      const hasUnread = !archivedView && Number(task.last_sequence || 0) > readThrough(task.task_id);
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
      button.children[0].textContent = task.title || "协作任务";
      const pending = managementNames[task.management_pending];
      const content = taskContentState(task);
      button.children[1].textContent = `${state(task.state)}${content.label ? ` · ${content.label}` : ""}${pending ? ` · ${pending}待确认` : ""}${hasUnread ? " · 未读" : ""}`;
      button.children[2].textContent = `最近活动 ${time(task.updated_at)}`;
      if ($("workers-list").children[position] !== button) {
        const focused = document.activeElement === button;
        $("workers-list").insertBefore(button, $("workers-list").children[position] || null);
        if (focused) button.focus({ preventScroll: true });
      }
    }
    renderAvatar(running, unread);
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
      let newlyUnavailable = false;
      for (const row of incoming.values()) {
        const previous = tasks.get(row.task_id);
        const unavailable = !taskContentState(row).available;
        if (previous && taskContentState(previous).available && unavailable) newlyUnavailable = true;
        if (row.task_id === selection && unavailable) {
          // A closed panel must not retain a body whose permission has changed.
          if (taskContentState(selectedTask || {}).available) viewGeneration++;
          renderDetail({ task: row, artifacts: [] });
        }
      }
      tasks.clear(); for (const [key, row] of incoming) tasks.set(key, row);
      if (newlyUnavailable) invalidateChatSources();
      listDepth = wanted; nextBefore = next; renderList();
    } finally {
      listBusy = false; $("workers-more").disabled = false;
      if (stale()) schedule(0);
    }
  }
  function selectArchiveView(archived) {
    if (actionBusy || archivedView === archived) return;
    controlGeneration++; archivedView = archived; selection = null; clearDetail(); resetList();
    showTaskDetail(false);
    schedule(0);
  }
  function manageable(task) {
    return !!task && endedStates.has(task.state) && task.agent_idle === true && Number.isSafeInteger(task.version);
  }
  function updateTaskControls() {
    const task = selectedTask;
    show("workers-cancel", !!task && (activeStates.has(task.state) || ["unknown", "waiting"].includes(task.state)));
    if (!task) {
      for (const name of ["workers-cancel", "workers-supplement", "workers-review", "workers-revoke"]) $(name).disabled = true;
      return;
    }
    const content = taskContentState(task), pending = !!task.management_pending;
    $("workers-cancel").disabled = actionBusy || pending || !(activeStates.has(task.state) || ["unknown", "waiting"].includes(task.state)) || task.state === "stopping" || !!task.cancel_requested && task.state !== "unknown";
    $("workers-cancel").textContent = task.state === "unknown" && task.cancel_requested ? "重试停止" : "请求停止";
    $("workers-supplement").disabled = actionBusy || pending || task.archived === true || task.session_closed === true || !content.available || registry?.policy?.enabled !== true || registry?.connection?.connected !== true;
    $("workers-review").disabled = actionBusy || pending || !content.available || !["completed", "succeeded"].includes(task.state);
    $("workers-revoke").disabled = actionBusy || pending || !content.available;
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
    $("workers-task-menu").open = true;
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
        if (choice.action === "delete") { forgetSeen(choice.taskId); invalidateChatSources(); }
        // Archive/restore preserves the position actually read, not the latest sequence.
        if (choice.action === "restore") archivedView = false;
        selection = null; clearDetail(); resetList();
        showTaskDetail(false);
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
        showTaskDetail(false); invalidateChatSources();
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
  function renderWorkerMarkdown(target, source) {
    // Build only known DOM nodes. Source never becomes HTML, CSS or an image
    // request. Complex input falls back to bounded literal text, without loss.
    const raw = typeof source === "string" ? source : "";
    const textSource = raw.slice(0, 65536) + (raw.length > 65536 ? "\n[排版文本已达上限]" : "");
    const limit = {}, budget = { nodes: 2400, steps: 16000 };
    const step = (count = 1) => { if ((budget.steps -= count) < 0) throw limit; };
    const element = (tag, value) => {
      if (--budget.nodes < 0) throw limit;
      const result = document.createElement(tag);
      if (value !== undefined) {
        if (--budget.nodes < 0) throw limit;
        result.textContent = value;
      }
      return result;
    };
    const plain = (parent, value) => {
      if (!value) return;
      if (--budget.nodes < 0) throw limit;
      parent.appendChild(document.createTextNode(value));
    };
    const literal = (parent, value) => {
      const pre = element("pre", value); pre.className = "worker-markdown-literal"; parent.appendChild(pre);
    };
    function safeLink(value) {
      if (value.length > 2048 || !/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f\\]/.test(value)) return null;
      try {
        const url = new URL(value), host = url.hostname.toLowerCase().replace(/\.+$/, "");
        if (!["http:", "https:"].includes(url.protocol) || !host || url.username || url.password
            || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")
            || !host.includes(".") || host.startsWith("[")) return null;
        if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
          const [a, b] = host.split(".").map(Number);
          if (a === 0 || a === 10 || a === 127 || a >= 224 || a === 169 && b === 254
              || a === 172 && b >= 16 && b <= 31 || a === 192 && b === 168 || a === 100 && b >= 64 && b <= 127) return null;
        }
        return url.href;
      } catch { return null; }
    }
    function linkAt(value, start, image) {
      const labelStart = start + (image ? 2 : 1);
      let close = labelStart;
      for (; close < value.length && close - labelStart <= 1024; close++) {
        step();
        if (value[close] === "\n") return null;
        if (value[close] === "\\") { close++; continue; }
        if (value[close] === "]") break;
      }
      if (value[close] !== "]" || value[close + 1] !== "(") return null;
      let end = close + 2, depth = 1;
      for (; end < value.length && end - close <= 4096; end++) {
        step();
        if (value[end] === "\n") return null;
        if (value[end] === "\\") { end++; continue; }
        if (value[end] === "(" && ++depth > 4) return null;
        if (value[end] === ")" && --depth === 0) break;
      }
      if (depth !== 0) return null;
      const destination = value.slice(close + 2, end).trim();
      const match = /^(?:<([^<>\n]+)>|(\S+?))(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'))?$/.exec(destination);
      return { end: end + 1, label: value.slice(labelStart, close),
        href: match && safeLink(match[1] || match[2]), literal: value.slice(start, end + 1) };
    }
    function inline(parent, value, depth = 0, allowLinks = true) {
      if (depth >= 6 || value.length > 8000) { plain(parent, value); return; }
      const tokens = /\\\n|\n|\\[\\`*_\[\]{}()#+.!>|~-]|`+|!\[|\[|<https?:\/\/|\*\*\*|___|\*\*|__|~~|\*|_/gi;
      let cursor = 0, match;
      while ((match = tokens.exec(value))) {
        step();
        const token = match[0], start = match.index;
        if (token.endsWith("\n")) {
          if (token === "\n" && value.slice(Math.max(cursor, start - 2), start) !== "  ") continue;
          plain(parent, value.slice(cursor, token === "\n" ? start - 2 : start));
          parent.appendChild(element("br")); cursor = tokens.lastIndex; continue;
        }
        if (token[0] === "\\") {
          plain(parent, value.slice(cursor, start)); plain(parent, token.slice(1)); cursor = tokens.lastIndex; continue;
        }
        if (token === "[" || token === "![") {
          const link = linkAt(value, start, token === "![");
          if (!link) continue;
          plain(parent, value.slice(cursor, start));
          if (allowLinks && token === "[" && link.href) {
            const anchor = element("a"); anchor.href = link.href; anchor.target = "_blank";
            anchor.rel = "noopener noreferrer"; anchor.referrerPolicy = "no-referrer";
            inline(anchor, link.label || link.href, depth + 1, false); parent.appendChild(anchor);
          } else plain(parent, link.literal);
          cursor = link.end; tokens.lastIndex = cursor; continue;
        }
        if (token[0] === "<") {
          const end = value.indexOf(">", tokens.lastIndex);
          if (end < 0 || end - start > 2048) continue;
          const href = allowLinks && safeLink(value.slice(start + 1, end));
          if (!href) continue;
          plain(parent, value.slice(cursor, start));
          const anchor = element("a", value.slice(start + 1, end)); anchor.href = href; anchor.target = "_blank";
          anchor.rel = "noopener noreferrer"; anchor.referrerPolicy = "no-referrer"; parent.appendChild(anchor);
          cursor = end + 1; tokens.lastIndex = cursor; continue;
        }
        const from = start + token.length, end = value.indexOf(token, from), code = token[0] === "`";
        if (end <= from) continue;
        const content = value.slice(from, end);
        if (!code && (!content.trim() || token[0] === "_" && /[\p{L}\p{N}]/u.test(value[start - 1] || ""))) continue;
        if (code && (value[end - 1] === "`" || value[end + token.length] === "`")) continue;
        plain(parent, value.slice(cursor, start));
        const format = element(code ? "code" : token === "~~" ? "del" : token.length >= 2 ? "strong" : "em");
        if (code) plain(format, content);
        else if (token.length === 3) {
          const emphasis = element("em"); inline(emphasis, content, depth + 1, allowLinks); format.appendChild(emphasis);
        } else inline(format, content, depth + 1, allowLinks);
        parent.appendChild(format); cursor = end + token.length; tokens.lastIndex = cursor;
      }
      plain(parent, value.slice(cursor));
    }
    const fence = (line) => {
      const match = /^ {0,3}(`{3,}|~{3,})([^\n]*)$/.exec(line);
      return match && !(match[1][0] === "`" && match[2].includes("`")) ? match[1] : null;
    };
    const heading = (line) => /^ {0,3}(#{1,6})(?:[ \t]+([^\n]*)|[ \t]*)$/.exec(line);
    const rule = (line) => /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/.test(line);
    const listItem = (line) => {
      const match = /^( {0,3})([-+*]|\d{1,9}[.)])[ \t]+([^\n]*)$/.exec(line);
      return match ? { indent: match[1].length, ordered: /^\d/.test(match[2]), number: parseInt(match[2], 10), body: match[3] } : null;
    };
    const cells = (line) => {
      if (line.length > 8192 || !line.includes("|")) return null;
      const values = [], input = line.trim();
      let value = "", codeRun = 0, separators = 0;
      for (let i = 0; i < input.length; i++) {
        step();
        if (input[i] === "\\" && i + 1 < input.length) { value += input[i] + input[++i]; continue; }
        if (input[i] === "`") {
          let count = 1;
          while (input[i + count] === "`") { step(); count++; }
          if (!codeRun) codeRun = count;
          else if (codeRun === count) codeRun = 0;
          value += "`".repeat(count); i += count - 1; continue;
        }
        if (input[i] === "|" && !codeRun) {
          values.push(value.trim()); value = ""; separators++;
          if (values.length > 17) return null;
        } else value += input[i];
      }
      if (!separators) return null;
      values.push(value.trim());
      if (values[0] === "" && input.startsWith("|")) values.shift();
      if (values[values.length - 1] === "" && input.endsWith("|")) values.pop();
      return values.length && values.length <= 16 ? values : null;
    };
    const tableStart = (lines, i) => {
      const head = cells(lines[i]), divider = i + 1 < lines.length && cells(lines[i + 1]);
      return head && divider && head.length === divider.length && divider.every((value) => /^:?-{3,}:?$/.test(value)) ? head : null;
    };
    function blocks(lines, parent, depth = 0) {
      if (depth >= 6) { literal(parent, lines.join("\n")); return; }
      let i = 0;
      while (i < lines.length) {
        step();
        if (!lines[i].trim()) { i++; continue; }
        const mark = fence(lines[i]);
        if (mark) {
          const content = []; i++;
          while (i < lines.length) {
            step();
            const closing = /^ {0,3}(`+|~+)[ \t]*$/.exec(lines[i]);
            if (closing && closing[1][0] === mark[0] && closing[1].length >= mark.length) { i++; break; }
            content.push(lines[i++]);
          }
          const pre = element("pre"); pre.className = "worker-markdown-code";
          pre.appendChild(element("code", content.join("\n"))); parent.appendChild(pre); continue;
        }
        const title = heading(lines[i]);
        if (title) {
          const h = element(`h${title[1].length}`);
          inline(h, (title[2] || "").replace(/[ \t]+#+[ \t]*$/, "").trim()); parent.appendChild(h); i++; continue;
        }
        if (rule(lines[i])) { parent.appendChild(element("hr")); i++; continue; }
        const header = tableStart(lines, i);
        if (header) {
          const wrap = element("div"); wrap.className = "worker-markdown-table"; wrap.tabIndex = 0;
          const table = element("table"), head = element("thead"), row = element("tr"), body = element("tbody");
          for (const value of header) { const cell = element("th"); cell.scope = "col"; inline(cell, value); row.appendChild(cell); }
          head.appendChild(row); table.appendChild(head); i += 2;
          let rows = 0, values;
          while (i < lines.length && (values = cells(lines[i]))) {
            step(); if (++rows > 100) throw limit;
            const tr = element("tr");
            if (values.length !== header.length) {
              const td = element("td", lines[i]); td.colSpan = header.length; tr.appendChild(td);
            } else for (const value of values) { const td = element("td"); inline(td, value); tr.appendChild(td); }
            body.appendChild(tr); i++;
          }
          table.appendChild(body); wrap.appendChild(table); parent.appendChild(wrap); continue;
        }
        if (/^ {0,3}>/.test(lines[i])) {
          const quoted = [];
          while (i < lines.length && /^ {0,3}>/.test(lines[i])) { step(); quoted.push(lines[i++].replace(/^ {0,3}> ?/, "")); }
          const quote = element("blockquote"); blocks(quoted, quote, depth + 1); parent.appendChild(quote); continue;
        }
        const first = listItem(lines[i]);
        if (first) {
          const list = element(first.ordered ? "ol" : "ul"); if (first.ordered) list.start = first.number;
          while (i < lines.length) {
            step(); const item = listItem(lines[i]);
            if (!item || item.ordered !== first.ordered || item.indent !== first.indent) break;
            const li = element("li"); inline(li, item.body); i++;
            const nested = [], prefix = " ".repeat(first.indent + 2);
            while (i < lines.length && lines[i].trim() && lines[i].startsWith(prefix)) { step(); nested.push(lines[i++].slice(prefix.length)); }
            if (nested.length) blocks(nested, li, depth + 1);
            list.appendChild(li);
          }
          parent.appendChild(list); continue;
        }
        const paragraph = [lines[i++]];
        while (i < lines.length && lines[i].trim() && !fence(lines[i]) && !heading(lines[i]) && !rule(lines[i])
            && !listItem(lines[i]) && !/^ {0,3}>/.test(lines[i]) && !tableStart(lines, i)) { step(); paragraph.push(lines[i++]); }
        const p = element("p"); inline(p, paragraph.join("\n")); parent.appendChild(p);
      }
    }
    target.classList.add("worker-markdown");
    const content = document.createDocumentFragment(), lines = textSource.replace(/\r\n?/g, "\n").split("\n");
    try {
      if (lines.length > 4000) throw limit;
      blocks(lines, content); target.replaceChildren(content);
    } catch {
      const pre = document.createElement("pre"); pre.className = "worker-markdown-literal"; pre.textContent = textSource;
      target.replaceChildren(pre);
    }
  }
  function bodyText(entry, value) {
    if (entry.markdown) renderWorkerMarkdown(entry.body, value);
    else entry.body.textContent = value;
    const long = !entry.markdown && value.length > 900;
    entry.fold.classList.toggle("hidden", !long); entry.body.hidden = false;
    if (long && entry.body.parentNode !== entry.fold) entry.fold.appendChild(entry.body);
    else if (!long && entry.body.parentNode !== entry.root) entry.root.insertBefore(entry.body, entry.fold);
  }
  function appendEvent(event) {
    if (!Number.isSafeInteger(event.sequence) || event.sequence <= eventCursor || eventNodes.has(event.event_id)) return;
    const actor = event.actor === "alice" ? "alice" : ["dsh", "agent", "dsh-agent"].includes(event.actor) ? "dsh" : "host";
    const isMessage = ["agent_message_delta", "agent_message_completed"].includes(event.type);
    const isConversation = isMessage || ["alice_message_queued", "agent_question"].includes(event.type);
    const target = $(isConversation ? "workers-messages" : "workers-history-events");
    const messageKey = (isMessage || event.type === "alice_message_queued") && event.message_id ? `${actor}:${event.message_id}` : null;
    let entry = messageKey && messageNodes.get(messageKey);
    if (!entry) {
      const root = node("article", undefined, "worker-event"); root.dataset.actor = actor;
      const head = node("div", "", "worker-event-head"), body = node("div", "", "worker-event-text"), fold = node("details");
      fold.appendChild(node("summary", "展开长消息"));
      const meta = node("div", "", "worker-event-meta"); root.append(head, body, fold, meta);
      entry = { root, head, body, fold, meta, content: "", offset: 0, markdown: isConversation }; target.appendChild(root);
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
    entry.truncated = event.truncated === true;
    entry.head.textContent = isConversation
      ? event.type === "alice_message_queued" ? "Alice → DSH" : event.type === "agent_question" ? "DSH → Alice · 等待补充" : "DSH → Alice"
      : eventNames[event.type] || "公开工作事件";
    const truncation = entry.truncated ? " · 片段已截断" : "";
    entry.meta.textContent = isConversation
      ? `${time(event.observed_at)}${event.type === "alice_message_queued" ? " · 已登记" : ""}${truncation}`
      : `#${event.sequence} · ${time(event.observed_at)} · ${actor === "dsh" ? "DSH 报告，未经宿主事实复核" : "宿主记录"}${truncation}`;
    entry.root.dataset.attention = String(["tool_failed", "run_failed", "connection_lost"].includes(event.type));
    if (event.type === "alice_message_sent") {
      // This is a host receipt, not a second message or a replacement for the brief.
      const sent = messageNodes.get(`alice:${event.message_id}`);
      if (sent) sent.meta.textContent = `${time(event.observed_at)} · 已送达${sent.truncated ? " · 片段已截断" : ""}`;
    }
    // The host bounds messages too; keep a second client bound for an open tab.
    if (entry.content.length > 64000) entry.content = entry.content.slice(0, 64000) + "\n[本页消息长度已达上限]";
    bodyText(entry, entry.content); eventNodes.set(event.event_id, entry); eventCursor = event.sequence;
    while (eventNodes.size > 600) eventNodes.delete(eventNodes.keys().next().value);
    // Bound both streams independently, so a tool-heavy run cannot evict the conversation.
    while (target.children.length > 300) {
      const oldest = target.firstElementChild; oldest.remove();
      for (const [key, value] of messageNodes) if (value.root === oldest) messageNodes.delete(key);
      for (const [key, value] of eventNodes) if (value.root === oldest) eventNodes.delete(key);
      show("workers-events-restart", true);
    }
  }
  function renderHistorySummary() {
    const records = $("workers-history-events"), count = records.children.length;
    const attention = records.querySelectorAll('[data-attention="true"]').length;
    text("workers-history-summary", `工具与执行记录 · ${count} 条${attention ? ` · ${attention} 条异常记录` : ""}`);
    show("workers-history", count > 0);
  }
  function renderDetail(data) {
    const task = data.task;
    const content = taskContentState(task);
    if (selectedTask?.task_id === task.task_id && taskContentState(selectedTask).available && !content.available) invalidateChatSources();
    if (selectedTask?.task_id === task.task_id && !taskContentState(selectedTask).available && content.available) clearEventView();
    selectedTask = task;
    const signature = JSON.stringify(task);
    if (taskSignature !== signature) {
      taskSignature = signature;
      text("workers-task-title", task.title || "协作任务");
      $("workers-task-title").title = task.title || "协作任务";
      text("workers-task-state", `${managementNames[task.management_pending] ? `${managementNames[task.management_pending]}待确认` : state(task.state)}${content.label ? ` · ${content.label}` : ""}${task.archived ? " · 已归档" : ""}${task.session_closed ? " · 会话已关闭" : ""}`);
      $("workers-task-state").title = $("workers-task-state").textContent;
      text("workers-task-meta", `${state(task.state)}${content.label ? ` · ${content.label}` : ""}${task.archived ? " · 已归档" : ""}${task.session_closed ? " · DSH 会话已关闭" : ""} · 最后更新 ${time(task.updated_at)} · ${task.task_id}`);
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
    const deleting = task.management_pending === "delete";
    if (!content.available || deleting) hideTaskBody(!content.available ? content.message : "删除结果待确认，正文暂不在本页展示。请按原选择明确重试。");
    updateTaskControls();
    updateManagementControls();
    const artifacts = !content.available || deleting ? [] : data.artifacts || [];
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
    return content.available && !deleting;
  }
  async function syncDetail(explicit = false) {
    if (actionBusy || !opened || !selection || $("workers-detail").classList.contains("hidden")) return;
    const selected = selection, generation = viewGeneration, session = epoch;
    let data;
    try { data = await request("/api/worker-tasks/detail", { session_epoch: session, task_id: selected }); }
    catch (e) {
      if (e.code === "not_found" && current(selected, generation, session)) {
        controlGeneration++; forgetSeen(selected); selection = null; clearDetail(); resetList();
        showTaskDetail(false); invalidateChatSources();
        status("任务已不在当前记录中，列表正在重新同步。"); return;
      }
      if (["not_found", "session_changed", "epoch_changed"].includes(e.code) && current(selected, generation, session)) clearDetail();
      throw e;
    }
    if (!current(selected, generation, session) || data.task?.task_id !== selected || data.session_epoch && data.session_epoch !== session) return;
    if ((data.task.archived === true) !== archivedView) {
      // Align the sidebar for references into the other archive group without
      // clearing the selected conversation or invalidating this detail read.
      archivedView = data.task.archived === true; resetList(); schedule(0);
    }
    if (!renderDetail(data)) {
      // An unavailable body still has a readable status; do not leave its
      // already-seen revocation/expiry notice permanently marked as unread.
      if (!taskContentState(data.task).available && canReadSelected(selected)) {
        markSeen(selected, data.task.last_sequence); renderList();
      }
      return;
    }
    // Reading tool records must not freeze the shared cursor or hide a later reply.
    if (!explicit && !following && !$("workers-history").open && $("workers-messages").children.length >= 300) {
      show("workers-events-more", true); return;
    }
    let response;
    try {
      response = await request("/api/worker-tasks/events", { session_epoch: session, task_id: selected, after_cursor: eventCursor, limit: 100 });
    } catch (e) {
      if (e.code === "resync_required" && current(selected, generation, session)) { clearDetail(); status("工作记录游标已过期，正在重新同步。", true); return; }
      throw e;
    }
    if (!current(selected, generation, session) || response.session_epoch !== session || response.task_id !== selected) return;
    if (response.resync_required) { clearDetail(); status("工作记录游标已过期，正在重新同步。", true); return; }
    if (!taskContentState(response).available) {
      renderDetail({ task: { ...data.task, content_available: false, content_revoked: response.content_revoked === true,
        content_unavailable_reason: response.content_unavailable_reason || null }, artifacts: [] });
      invalidateChatSources(); return;
    }
    const scroll = $("workers-events"), history = $("workers-history"), records = $("workers-history-events");
    const atEnd = following && !history.open, historyTop = history.getBoundingClientRect().top;
    const recordAnchor = history.open && [...records.children].find((row) => row.getBoundingClientRect().bottom > records.getBoundingClientRect().top);
    const anchorTop = recordAnchor ? recordAnchor.getBoundingClientRect().top - records.getBoundingClientRect().top : 0;
    for (const event of response.events || []) appendEvent(event);
    renderHistorySummary();
    eventCursor = Math.max(eventCursor, Number(response.cursor) || 0); eventMore = !!response.has_more;
    show("workers-events-more", eventMore);
    if (atEnd) scroll.scrollTop = scroll.scrollHeight;
    else if (history.open) {
      scroll.scrollTop += history.getBoundingClientRect().top - historyTop;
      if (recordAnchor?.isConnected) records.scrollTop += recordAnchor.getBoundingClientRect().top - records.getBoundingClientRect().top - anchorTop;
      else if (recordAnchor) records.scrollTop = 0;
    }
    show("workers-latest", !atEnd && scroll.scrollHeight > scroll.clientHeight);
    if (atEnd && !eventMore && canReadSelected(selected)) markSeen(selected, eventCursor);
    renderList();
  }
  function schedule(delay) {
    clearTimeout(timer);
    if (!visible()) return;
    const active = [...tasks.values()].some((task) => activeStates.has(task.state));
    timer = setTimeout(poll, delay ?? (Date.now() < dispatchWatchUntil ? 1000
      : opened && selection || active ? 1800 : opened || settingsOpened ? 5000 : 10000));
  }
  async function poll() {
    clearTimeout(timer);
    if (polling || actionBusy || !visible()) return;
    polling = true;
    const generation = controlGeneration;
    try {
      const data = await request("/api/collaborators");
      if (!visible() || generation !== controlGeneration) return;
      connected = true; acceptEpoch(data.session_epoch); setRegistry(data);
      await syncList();
      if (generation !== controlGeneration) return;
      if (opened && selection) await syncDetail();
      if (generation !== controlGeneration) return;
      const worker = registry.collaborators?.find((row) => row.name === "dsh-agent");
      status(worker && worker.state !== "ready" ? (worker.reason || `DSH · ${state(worker.state)}`) : `已同步 · ${time(Date.now() / 1000)}`);
    } catch (e) {
      if (!stopped) { connected = false; renderList(); status(`连接暂不可用；执行状态未确认。${e.message}`, true); }
    } finally { polling = false; schedule(connected ? undefined : 10000); }
  }
  function layout() {
    windowFrame.layout();
  }
  function open() {
    if (opened) return;
    closeFocus = false;
    windowFrame.completeTransition();
    avatarControl.cancel();
    const avatar = $("workers-btn");
    const origin = getComputedStyle(avatar).visibility === "hidden" ? null
      : avatar.querySelector(".collaborator-avatar-art").getBoundingClientRect();
    window.AliceObserver?.close(); window.AliceEvaluations?.close(); opened = true;
    panel.classList.remove("hidden"); app.classList.add("workers-open"); avatar.setAttribute("aria-expanded", "true");
    renderList(); windowFrame.open(origin); avatarControl.layout();
    $("workers-list-view").scrollTop = savedListScroll;
    $("workers-window-avatar").focus({ preventScroll: true }); poll();
  }
  function close(returnFocus = true) {
    avatarControl.cancel();
    if (closing) { if (!returnFocus) { closeFocus = false; windowFrame.completeTransition(); } return; }
    if (!opened) return;
    closeManagement(); $("workers-task-menu").open = false;
    const priorInert = panel.inert;
    opened = false; closing = true; closeFocus = returnFocus; viewGeneration++; savedListScroll = $("workers-list-view").scrollTop;
    panel.inert = true; panel.dataset.transition = "closing";
    $("workers-btn").setAttribute("aria-expanded", "false");
    const complete = () => {
      if (!closing) return;
      closing = false; delete panel.dataset.transition;
      panel.classList.add("hidden"); app.classList.remove("workers-open");
      panel.inert = priorInert || app.classList.contains("nav-open");
      renderList(); avatarControl.layout();
      if (closeFocus && !stopped && !document.hidden && document.hasFocus() && !settingsOpened && !app.inert && !app.classList.contains("nav-open")
          && (document.activeElement === document.body || panel.contains(document.activeElement))) {
        const avatar = $("workers-btn");
        (avatar.getClientRects().length && getComputedStyle(avatar).visibility !== "hidden" ? avatar : $("input")).focus({ preventScroll: true });
      }
    };
    windowFrame.close(complete, returnFocus && !stopped && !app.inert && !panel.closest(".nav-open"));
    schedule();
  }
  function setSettingsVisible(value) {
    if (value) { avatarControl.cancel(); if (closing) windowFrame.completeTransition(); }
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
    showTaskDetail(true); renderList();
    if (!selectedTask) {
      text("workers-task-title", tasks.get(taskId)?.title || "正在读取会话…");
      clearEventView("正在读取会话…");
    }
    const generation = viewGeneration, session = epoch;
    try { await syncDetail(); }
    catch (e) { if (current(taskId, generation, session)) status(e.message, true); }
    if (current(taskId, generation, session)) {
      renderList();
      const item = [...$("workers-list").children].find((child) => child.dataset.taskId === taskId);
      const scroll = $("workers-list-view");
      if (item) {
        const itemRect = item.getBoundingClientRect(), viewport = scroll.getBoundingClientRect();
        const top = viewport.top + $("workers-unarchived").parentElement.offsetHeight + 10;
        if (itemRect.top < top) scroll.scrollTop += itemRect.top - top;
        else if (itemRect.bottom > viewport.bottom) scroll.scrollTop += itemRect.bottom - viewport.bottom;
      }
    }
  }
  async function openTask(taskId) {
    if (stopped || app.inert || app.classList.contains("hidden") || app.classList.contains("nav-open")
        || actionBusy || typeof taskId !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(taskId)) return;
    avatarControl.cancel();
    if (!opened) open();
    const generation = viewGeneration;
    try {
      if (!epoch) {
        const data = await request("/api/collaborators");
        if (!opened || !visible() || app.inert || actionBusy || generation !== viewGeneration) return;
        connected = true; acceptEpoch(data.session_epoch); setRegistry(data);
      }
      if (opened && !app.inert && !actionBusy) await select(taskId);
    } catch (e) { if (opened) status(e.message, true); }
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
      text(resultId, pairing ? (data.connection?.connected ? data.policy?.enabled ? "连接已恢复，原授权和剩余额度保持不变，无需重新保存授权。" : "已连接。请核对并开启任务授权。"
        : action === "connect_managed" ? (data.credential?.configured === true
          ? "令牌已保存，连接尚未确认。请查看上方连接状态，确认 DSH NEXT 正在运行后再次连接。" : "连接尚未完成，请查看上方令牌与连接状态。")
          : "配对尚未完成，请查看上方连接原因。")
        : action === "rotate_token" ? setup || "新令牌已保存，旧令牌已失效。请重新连接；派工授权仍关闭。"
        : action === "disconnect" ? "连接已断开，原授权、剩余额度和仍有效的任务正文保留。再次连接后可继续使用；运行停止是否确认请查看任务状态。"
        : data.policy_unchanged === true ? "授权设置未变化，已有任务记录与剩余额度保持不变。"
        : data.policy_resumed === true ? "原授权已恢复，原执行次数与剩余额度保留；仍有效且未被撤销的任务正文可以重新查看。"
        : data.policy?.enabled ? "授权已保存。现在可以在主聊天向 Alice 描述任务。" : "授权已关闭，旧任务正文及派生内容已清除，无法仅靠重新开启授权恢复。");
    } catch (e) {
      if (session !== epoch || generation !== controlGeneration) return;
      // A pairing error must never echo a token, even if an upstream error does.
      const message = action === "connect_managed" ? "连接未确认。连接失败本身不会撤销原授权或任务正文；请同步连接与令牌状态后再手动连接，没有自动重试。"
        : action === "rotate_token" ? "更换令牌未确认。请同步本机令牌状态；没有自动重试。"
        : action === "connect" ? "配对未确认。请查看上方连接状态并核对所用桥接令牌；原授权不会因本次连接失败自动撤销，没有自动重试。"
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
  const windowFrame = setupWorkerWindow();
  const avatarControl = setupAvatarDrag($("workers-btn"));
  $("workers-close").onclick = () => close();
  $("workers-window-avatar").onclick = () => close();
  $("workers-refresh").onclick = poll;
  $("workers-settings-refresh").onclick = poll;
  $("workers-unarchived").onclick = () => selectArchiveView(false);
  $("workers-archived").onclick = () => selectArchiveView(true);
  $("workers-more").onclick = () => syncList(true).catch((e) => status(e.message, true));
  $("workers-events-more").onclick = () => syncDetail(true).catch((e) => status(e.message, true));
  $("workers-events-restart").onclick = () => { clearDetail(); following = false; syncDetail(true).catch((e) => status(e.message, true)); };
  $("workers-events").onscroll = () => {
    const el = $("workers-events"); following = el.scrollHeight - el.scrollTop - el.clientHeight < 35;
    show("workers-latest", !following);
    markVisibleRead();
  };
  $("workers-latest").onclick = () => {
    $("workers-history").open = false; following = true;
    $("workers-events").scrollTop = $("workers-events").scrollHeight; show("workers-latest", false); schedule(0);
    markVisibleRead();
  };
  $("workers-supplement").onclick = () => {
    if (!selection || $("workers-supplement").disabled) return;
    const accepted = window.AliceComposer?.setWorkerReference({
      task_id: selection, title: selectedTask?.title || "DSH 协作会话", session_epoch: epoch,
    });
    if (!accepted) {
      status("暂时无法更换任务引用，请先处理主聊天中的发送状态。", true); return;
    }
    const input = $("input");
    close(false); input.focus(); input.setSelectionRange(input.value.length, input.value.length);
  };
  $("workers-cancel").onclick = () => mutate("cancel");
  $("workers-review").onclick = () => { if (confirm("已亲自检查任务结果，并确认符合本次要求？这只记录人工结论，不执行任何任务。")) mutate("review", { review: "accepted" }); };
  $("workers-revoke").onclick = () => { if (confirm("撤销本任务正文及派生内容的使用资格？保留任务状态事实；已发生的外部动作不会被撤回。")) mutate("revoke"); };
  $("workers-archive").onclick = () => beginManagement(selectedTask?.archived ? "restore" : "archive");
  $("workers-delete").onclick = () => beginManagement("delete");
  $("workers-management-retry").onclick = () => beginManagement(selectedTask?.management_pending);
  $("workers-management-back").onclick = () => closeManagement(true);
  $("workers-task-menu").addEventListener("toggle", () => {
    if (!$("workers-task-menu").open && management && !actionBusy) closeManagement();
  });
  document.addEventListener("pointerdown", (event) => {
    if ($("workers-task-menu").open && !actionBusy && !$("workers-task-menu").contains(event.target)) {
      closeManagement(); $("workers-task-menu").open = false;
    }
  });
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
    updateControls();
  };
  $("workers-policy").onsubmit = (event) => {
    event.preventDefault();
    if ($("workers-policy-save").disabled) return;
    const enabled = $("workers-enabled").checked, max = Number($("workers-max-runs").value);
    const seconds = Number($("workers-max-seconds").value), workspace = $("workers-workspace").value.trim();
    const mode = $("workers-mode").value, acknowledged = $("workers-native-ack").checked;
    const previous = registry.policy || {};
    const sameGrant = max === previous.max_runs && seconds === previous.max_seconds
      && workspace === (previous.workspace_root || "") && mode === previous.permission_mode
      && acknowledged === (previous.native_permissions_ack === true);
    const invalid = (message) => { text("workers-policy-result", message); $("workers-policy-result").dataset.error = "true"; };
    if (!Number.isSafeInteger(max) || max < 0 || max > 100) { invalid("执行次数须为 0–100 的整数。"); return; }
    if (!Number.isSafeInteger(seconds) || seconds < 30 || seconds > 1800) { invalid("每次执行时限须为 30–1800 秒。"); return; }
    if (enabled && (!/^[a-z]:[\\/]/i.test(workspace) || /[\x00-\x1f]/.test(workspace))) { invalid("请填写明确的本机绝对目录，例如 D:\\AliceTasks。"); return; }
    if (!["read-only", "workspace-write"].includes(mode)) { invalid("请选择仅可查看或工作区内修改。"); return; }
    if (enabled && !acknowledged) { invalid("请先确认所选目录、模型外发及 DSH 原生权限边界。"); return; }
    if (enabled && (max < 1 || (!sameGrant || previous.enabled !== true) && registry?.connection?.connected !== true)) { invalid("开启或修改授权前须连接 DSH，并至少授权 1 次执行。"); return; }
    const closesGrant = previous.enabled === true && !enabled;
    const replacesGrant = !!previous.workspace_root && previous.max_runs > 0 && !sameGrant;
    if ((closesGrant || replacesGrant) && !confirm(closesGrant
      ? "关闭授权将清除旧任务正文及派生内容，无法仅靠重新开启授权恢复，并停止新的派工。已发生的外部动作不会撤回。确定关闭授权？"
      : "更改已有授权参数将清除旧任务正文及派生内容，无法仅靠重新开启或改回原参数恢复。新授权另计执行次数。确定保存新的授权范围？")) return;
    configure("policy", { revision: registry.revision, enabled, max_runs: max, max_seconds: seconds,
      workspace_root: workspace, permission_mode: mode, native_permissions_ack: acknowledged });
  };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && opened && !app.inert && !app.classList.contains("nav-open") && panel.contains(document.activeElement)) {
      event.preventDefault();
      if (management && !actionBusy) closeManagement(true);
      else if ($("workers-task-menu").open) { $("workers-task-menu").open = false; $("workers-task-menu-toggle").focus({ preventScroll: true }); }
      else close();
    }
  });
  document.addEventListener("visibilitychange", () => visible() ? poll() : clearTimeout(timer));
  new ResizeObserver(layout).observe(app);
  new MutationObserver(() => {
    if (closing && (app.inert || app.classList.contains("hidden") || app.classList.contains("nav-open"))) windowFrame.completeTransition();
    if (visible()) schedule(0); else clearTimeout(timer);
  }).observe(app, { attributes: true, attributeFilter: ["class", "inert"] });
  window.addEventListener("pagehide", () => { $("workers-token").value = ""; clearTimeout(timer); controllers.forEach((controller) => controller.abort()); });
  window.AliceWorkers = { close, openTask, setSettingsVisible,
    noteTool(name) {
      if (!["dispatch_worker", "send_worker_message"].includes(name)) return;
      // A tool announcement is only intent: refresh promptly, but expand only
      // when the host confirms that its execution counter actually increased.
      dispatchWatchUntil = Date.now() + 30000; schedule(0);
    },
    refreshAfterTurn() { schedule(0); },
    shutdown() {
    stopped = true; avatarControl.cancel(); setSettingsVisible(false); close(false); clearTimeout(timer); controllers.forEach((controller) => controller.abort());
  } };
  schedule(0);
})();
