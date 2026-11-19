"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const box = $("messages"), input = $("input");
  const invoke = (action, payload = null) => window.__TAURI_INTERNALS__.invoke("bubble_action", {action, payload});
  let host = {}, epoch = null, cursor = 0, initial = false, syncing = false, sending = false;
  let ready = false, activity = null, lastSync = 0, lastState = 0, composing = false;
  let uncertain = null, live = null, draftVersion = 0, epochWarning = false;
  let followLatest = true, followFrame = null, scrollIntentUntil = 0, scrollPointer = false, touchY = null;
  let sourceTimer = null;
  let syncController = null;
  const turns = new Map();
  const views = new Map();
  const motion = new window.AliceDesktopSentenceMotion();
  const speech = new window.AliceDesktopSpeech((reveal) => render(reveal));
  const setText = (node, text) => {if (node.textContent !== text) node.textContent = text;};
  const say = (text = "") => setText($("notice"), text);
  function settleRequest() {
    if (uncertain?.restoredVersion === draftVersion && input.value.trim() === uncertain.text) {
      input.value = ""; draftVersion++;
    }
    uncertain = null;
  }
  const following = () => box.scrollHeight - box.scrollTop - box.clientHeight <= 4;
  function queueFollow() {
    if (!followLatest || followFrame !== null) return;
    followFrame = requestAnimationFrame(() => {
      followFrame = null;
      if (!followLatest) return;
      box.scrollTop = box.scrollHeight;
      if (host.expanded && document.hasFocus()) invoke("read").catch(() => {});
    });
  }
  function bottom() {
    followLatest = true; $("latest").hidden = true;
    box.scrollTop = box.scrollHeight; queueFollow();
  }
  function pauseFollowing() {
    followLatest = false; $("latest").hidden = false;
    if (followFrame !== null) {cancelAnimationFrame(followFrame);followFrame = null;}
  }
  function buttons() {
    // Quiet GET polling is not a user operation and must not pulse the composer.
    $("send").disabled = !ready || !host.available || !host.enabled || sending
      || !epoch || !!epochWarning || !!uncertain || activity?.status !== "ready";
    input.disabled = sending || !host.available || !host.enabled;
    $("recovery").hidden = !uncertain;
    $("new-request").hidden = !uncertain?.canStartNew;
    $("check").disabled = sending || syncing || activity?.status === "busy";
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, headers:{"X-Alice-Intent":"gui", ...options.headers}});
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw Object.assign(new Error(detail.error || `连接未完成（${response.status}）`), {code:detail.code, status:response.status});
    }
    return response;
  }
  const eligible = (turn) => ["gui", "local"].includes(turn.channel) && !turn.proactive && !turn.worker_report;
  const sourceReadable = (turn) => !turn.source_bound || (!turn.worker_bound && !turn.source_stale
    && turn.source_available === true && Number(turn.source_expires_at) * 1000 > Date.now());
  const display = (turn) => turn.worker_bound ? "（这条消息包含协作资料，请在主界面查看。）"
    : !sourceReadable(turn) ? (turn.source_stale ? "（资料状态待同步，暂不展示正文。）" : "（资料已到期或不可用，正文已隐藏。）")
    : turn.content || "（没有正文）";
  const turnKey = (turn) => `${epoch}:${turn.request_id || `turn-${turn.id}`}:${turn.role}`;
  const liveKey = () => `${epoch}:${live.requestId}:assistant`;
  function reconcile(parent, nodes) {
    nodes.forEach((node, index) => {if (parent.childNodes[index] !== node) parent.insertBefore(node, parent.childNodes[index] || null);});
    while (parent.childNodes.length > nodes.length) parent.lastChild.remove();
  }
  function turnView(key, role, parts, pending = false) {
    let node = views.get(key);
    if (!node) {node = document.createElement("div"); views.set(key,node);}
    if (role === "user") {
      const name = `message user${pending ? " pending" : ""}`;
      if (node.className !== name) node.className = name;
      if (node.textContent !== parts[0]) node.textContent = parts[0];
    } else {
      if (node.className !== "assistant-turn") node.className = "assistant-turn";
      const content = parts.length ? parts : ["…"];
      const children = content.map((text, index) => {
        const child = node.children[index] || document.createElement("div");
        const name = `message assistant${parts.length ? "" : " pending"}`;
        if (child.className !== name) child.className = name;
        if (child.textContent !== text) child.textContent = text;
        return child;
      });
      reconcile(node,children);
    }
    return node;
  }
  function mergeTurn(turn, {immediate = false} = {}) {
    turn = {...turn, content:sourceReadable(turn) && !turn.worker_bound ? turn.content : ""};
    turns.set(turn.id, turn);
    if (turn.role === "assistant" && eligible(turn)) {
      const masked = !sourceReadable(turn) || !!turn.worker_bound;
      // A source placeholder discards body text but preserves only the reveal
      // count. Revalidating old turns must not replay them ahead of a new reply.
      speech.set(turnKey(turn), display(turn), {final:true, masked,
        immediate:immediate || masked || !!host.main_focused});
    }
  }
  function refreshSourceExpiry({invalidate = false} = {}) {
    clearTimeout(sourceTimer); sourceTimer = null;
    let next = Infinity, changed = false;
    for (const turn of turns.values()) {
      if (!turn.source_bound || turn.worker_bound || !eligible(turn)) continue;
      const deadline = Number(turn.source_expires_at) * 1000;
      if (invalidate || !sourceReadable(turn)) {
        if (turn.content || (invalidate && !turn.source_stale)) {
          mergeTurn({...turn, content:"", source_stale:invalidate || turn.source_stale}, {immediate:true});
          changed = true;
        }
      } else next = Math.min(next, deadline);
    }
    if (changed) {motion.clear();render();}
    if (Number.isFinite(next)) sourceTimer = setTimeout(refreshSourceExpiry, Math.max(20, Math.min(2147483647, next - Date.now() + 20)));
  }
  function render(reveal = null) {
    const oldTop = box.scrollTop;
    const used = new Set();
    const nodes = [...turns.values()].sort((a,b) => a.id - b.id).filter(eligible).slice(-60).map((turn) => {
      const key = turnKey(turn);used.add(key);
      return turnView(key, turn.role, turn.role === "user" ? [display(turn)] : speech.visible(key));
    });
    if (live) {
      if (!live.userId || !turns.has(live.userId)) {
        const key = `${epoch}:${live.requestId}:user`;used.add(key);
        nodes.push(turnView(key, "user", [live.text], true));
      }
      if (!live.assistantId || !turns.has(live.assistantId)) {
        const key = liveKey();used.add(key);
        nodes.push(turnView(key, "assistant", speech.visible(key)));
      }
    }
    if (!nodes.length) {const empty = document.createElement("p");empty.className = "empty";empty.textContent = "在这里，接着聊。";nodes.push(empty);}
    reconcile(box,nodes);
    for (const key of views.keys()) if (!used.has(key)) views.delete(key);
    if (followLatest) bottom(); else {box.scrollTop = oldTop; $("latest").hidden = false;}
    const sentence = reveal && views.get(reveal.key)?.children[reveal.index];
    if (sentence && host.expanded) motion.enter(sentence);
  }
  function reset(nextEpoch) {
    const changed = epoch && nextEpoch !== epoch;
    epoch = nextEpoch || null; cursor = 0; initial = false; turns.clear(); live = null;speech.clear();motion.clear();views.clear();bottom();
    clearTimeout(sourceTimer); sourceTimer = null;
    if (changed) {
      uncertain = null; epochWarning = true;
      say("会话已更换，草稿保留。请先打开主界面核对当前会话，再回到这里发送。");
    }
    render();
  }
  async function sync(force = false) {
    refreshSourceExpiry();
    const interval = host.expanded ? 1500 : host.background ? 4000 : 15000;
    if (syncing || sending || !host.enabled || !host.available || (!force && Date.now() - lastSync < interval)) return;
    syncing = true; lastSync = Date.now(); buttons();
    const controller = new AbortController();
    syncController = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    // This deadline covers headers and JSON bodies, only for read-only sync.
    // A chat stream itself has no such deadline and is never resent here.
    const read = async (path) => {
      const value = await (await api(path, {signal:controller.signal})).json();
      if (syncController !== controller) throw new DOMException("同步已让位于发送", "AbortError");
      return value;
    };
    try {
      activity = await read("/api/chat/activity");
      setText($("connection"), activity.status === "busy" ? "Alice 正在处理消息…" : activity.status === "ready" ? "同一段对话 · 已连接" : "Alice 正在准备或退出");
      if (epoch && activity.session_epoch !== epoch) reset(activity.session_epoch);
      if (activity.status !== "ready") return;
      if (!ready || Date.now() - lastState > 30000) {
        const state = await read("/api/state");
        ready = state.model_ready === true && !state.needs_seed; lastState = Date.now();
        if (!ready) say("请先在主界面配置模型 API 并安装人格。");
      }
      for (let page = 0; page < 3; page++) {
        const params = new URLSearchParams({limit:"60"});
        if (initial) params.set("after_id", String(cursor));
        if (epoch) params.set("session_epoch", epoch);
        const sourceIds = [...turns.values()].filter((turn) => turn.source_bound && eligible(turn)).map((turn) => turn.id);
        if (sourceIds.length) params.set("source_turn_ids", sourceIds.slice(-100).join(","));
        const history = await read(`/api/history?${params}`);
        if (epoch && history.session_epoch !== epoch) {reset(history.session_epoch); break;}
        epoch = history.session_epoch;
        const baseline = !initial;
        for (const turn of history.turns || []) {
          if (!Number.isSafeInteger(turn.id)) continue;
          const fresh = turn.id > cursor;
          // Use only the server's GUI projection, never retrieve raw source packets.
          mergeTurn(turn,{immediate:baseline});
          if (live?.requestId === turn.request_id) {
            if (turn.role === "user") live.userId = turn.id;
            if (turn.role === "assistant") live.assistantId = turn.id;
          }
          if (!baseline && fresh && turn.role === "assistant" && eligible(turn) && !turn.source_bound && host.background && !host.expanded) {
            await invoke("notice", {epoch, id:turn.id, text:turn.content || "Alice 有一条新回复"});
            if (syncController !== controller) return;
          }
          if (uncertain?.requestId === turn.request_id && turn.role === "assistant") {
            settleRequest(); live = null; say("原请求的回复已同步，未重复发送。");
          }
        }
        for (const update of history.source_updates || []) {
          const previous = turns.get(update.id);
          if (previous) mergeTurn({...previous, ...update, source_stale:false});
        }
        if (Number.isSafeInteger(history.next_after_id)) cursor = Math.max(cursor, history.next_after_id);
        initial = true;
        for (const id of [...turns.keys()].sort((a,b) => b-a).slice(100)) turns.delete(id);
        const retained = new Set([...turns.values()].filter((turn) => turn.role === "assistant" && eligible(turn)).map(turnKey));
        if (live) retained.add(liveKey());speech.retain(retained);
        if (!history.has_more) break;
      }
      refreshSourceExpiry();
      render();
    } catch (error) {
      if (syncController !== controller) return; // Superseded read: no stale UI/error/source mask.
      if (error.code === "session_changed") reset(null);
      else {
        refreshSourceExpiry({invalidate:true});
        if (error.name === "AbortError") {$("connection").textContent = "同步暂未完成，稍后自动重试";}
        else if (!["busy", "status_unavailable"].includes(error.code)) {$("connection").textContent = "暂时无法连接"; say(error.message);}
      }
    } finally {
      clearTimeout(timeout);
      // A cancelled read must not unlock or overwrite a newer sync owner.
      if (syncController === controller) {syncController = null; syncing = false; buttons();}
    }
  }
  window.bubbleHostState = (state) => {
    const wasExpanded = host.expanded;
    const wasMainFocused = host.main_focused;
    host = state;
    refreshSourceExpiry();
    speech.setActive(!!state.expanded && !!state.available);
    if (!state.expanded || !state.available) motion.clear();
    if (state.main_focused && !wasMainFocused) speech.showAll();
    document.body.classList.toggle("solid", !!state.solid);
    $("pin").setAttribute("aria-pressed", String(!!state.pinned));
    if (!state.available) {$("connection").textContent = "正在准备、维护或退出";}
    // Returning from the main window is the explicit review step after an epoch change.
    if (state.main_focused && epochWarning) epochWarning = false;
    if (state.expanded && !wasExpanded) {sync(true); if (!composing) input.focus({preventScroll:true});}
    else sync();
    buttons();
  };
  async function stream(response, onEvent) {
    if (!response.body) throw new Error("回复数据流不可用");
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "", terminal = false;
    try {
      for (;;) {
        const {done, value} = await reader.read();
        buffer += done ? decoder.decode() : decoder.decode(value, {stream:true});
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0,index); buffer = buffer.slice(index+2);
          const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
          if (!data) continue;
          const event = JSON.parse(data); onEvent(event);
          if (["done","error"].includes(event.type)) terminal = true;
        }
        if (buffer.length > 4 * 1024 * 1024) throw new Error("回复帧过大");
        if (done) break;
      }
      if (!terminal) throw new Error("连接中断，结果尚未确认");
    } finally {reader.releaseLock();}
  }
  async function send(originalText = null) {
    const text = typeof originalText === "string" ? originalText : input.value.trim();
    if (!text || sending || !host.available || activity?.status !== "ready" || !epoch || !ready || epochWarning || uncertain) return;
    if (new TextEncoder().encode(text).length > 12000) {say("消息太长，请缩短后发送（最多 12000 字节）。"); return;}
    // Only abort background GETs. The POST/SSE below has its own request identity
    // and is never aborted or resent by polling.
    const previousSync = syncController;
    syncController = null; syncing = false;
    previousSync?.abort();
    const request = {requestId:crypto.randomUUID().replaceAll("-",""), text, epoch};
    const version = draftVersion;
    uncertain = request; sending = true; buttons(); say("正在提交…");
    try {
      const response = await api("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text:request.text,request_id:request.requestId,session_epoch:request.epoch})});
      live = {requestId:request.requestId,text:request.text,reply:"",userId:null,assistantId:null};
      bottom();
      speech.set(liveKey(), "");
      let terminalError = false;
      await stream(response, (event) => {
        if (event.session_epoch && event.session_epoch !== request.epoch) throw new Error("会话身份已经变化，请同步原请求");
        if (event.type === "start") {
          if (draftVersion === version && input.value.trim() === request.text) {input.value = ""; draftVersion++;}
          say("Alice 正在回复…");
        } else if (event.type === "delta") {
          live.reply += event.text || "";
          speech.set(liveKey(), live.reply, {immediate:!!host.main_focused});
        }
        else if (event.type === "done") {
          live.userId = event.user_turn_id;live.assistantId = event.assistant_turn_id;
          // Finalize already-streamed text in the SAME paced queue. Otherwise
          // an unpunctuated tail waits for history, which may be busy/offline.
          // Source/worker projection is still refreshed from canonical history.
          if (live.reply) speech.set(liveKey(), live.reply, {final:true, immediate:!!host.main_focused});
          else if (!event.assistant_turn_id) speech.set(liveKey(), event.reply || "（沉默）", {final:true});
          uncertain = null;say(!live.reply && event.assistant_turn_id ? "回复已保存，正在同步正文…" : "");
        } else if (event.type === "error") {
          terminalError = true; say(event.message || "这轮未完成，请先核对记录。");
        } else if (event.type === "search") {
          if (event.status !== "not_used" && event.phase !== "idle") say(event.phase === "completed"
            ? (event.message || "查找结束，Alice 正在整理回答…")
            : ({queued:"正在准备查找网页…", search:"正在搜索网页…", read:"正在读取网页正文…"}[event.phase] || "正在查找资料…"));
        } else if (event.type === "reply_saved") say("回复已保存，正在完成整理…");
        render();
      });
      if (!terminalError && !host.expanded) {
        // Notifications use the final, privacy-filtered history projection in sync().
        lastSync = 0;
      }
    } catch (error) {
      if (error.code === "session_changed") {reset(null); say("会话已更换，请打开主界面核对；草稿保留。");}
      else if (["busy","draining","not_ready","invalid_request","gui_only"].includes(error.code) || error.status === 400) {
        uncertain = null; live = null; say(error.message);
      } else {
        if (["request_failed","request_interrupted"].includes(error.code)) request.canStartNew = true;
        say("结果尚未确认。先同步记录；“同步原请求”沿用同一请求编号，不自动重跑。");
      }
      if (!input.value && !composing) {input.value = request.text;draftVersion++;request.restoredVersion = draftVersion;}
    } finally {
      sending = false; await sync(true);
      if (!uncertain && live?.assistantId && turns.has(live.assistantId)) {live = null;render();}
      buttons();
    }
  }
  $("composer").addEventListener("submit", (event) => {event.preventDefault();send();});
  $("chat-drag-handle").addEventListener("mousedown", (event) => {
    if (event.button !== 0 || event.target.closest("button, input, textarea, a, dialog")) return;
    event.preventDefault();
    invoke("drag").catch((error) => say(String(error)));
  });
  input.addEventListener("input", () => {draftVersion++;});
  input.addEventListener("compositionstart", () => {composing = true;});
  input.addEventListener("compositionend", () => {composing = false;});
  document.addEventListener("keydown", (event) => {
    if (event.isComposing || composing || event.keyCode === 229) return;
    if (event.key === "Escape" && !$("confirm-new").open) {event.preventDefault();invoke("collapse").catch(() => {});}
    if (event.target === input && event.key === "Enter" && !event.shiftKey) {event.preventDefault();send();}
  });
  $("latest").onclick = bottom;
  // Only deliberate scrolling changes follow mode; content growth and viewport changes do not.
  box.addEventListener("wheel", (event) => {
    scrollIntentUntil = performance.now() + 1200;
    if (event.deltaY < 0) pauseFollowing();
  }, {passive:true});
  box.addEventListener("touchstart", (event) => {touchY = event.touches[0]?.clientY ?? null;}, {passive:true});
  box.addEventListener("touchmove", (event) => {
    const next = event.touches[0]?.clientY;
    scrollIntentUntil = performance.now() + 1200;
    if (touchY !== null && next > touchY) pauseFollowing();
    touchY = next ?? null;
  }, {passive:true});
  box.addEventListener("pointerdown", () => {scrollPointer = true;});
  window.addEventListener("pointerup", () => {scrollPointer = false;});
  window.addEventListener("pointercancel", () => {scrollPointer = false;});
  box.addEventListener("keydown", (event) => {
    if (!["ArrowUp","ArrowDown","PageUp","PageDown","Home","End"," "].includes(event.key)) return;
    scrollIntentUntil = performance.now() + 1200;
    if (["ArrowUp","PageUp","Home"].includes(event.key) || (event.key === " " && event.shiftKey)) pauseFollowing();
  });
  document.addEventListener("selectionchange", () => {
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && box.contains(selection.anchorNode)) pauseFollowing();
  });
  box.addEventListener("scroll", () => {
    if (!scrollPointer && performance.now() >= scrollIntentUntil) return;
    if (following()) bottom(); else pauseFollowing();
  }, {passive:true});
  // Notices, recovery controls and window resizing can all change the visible transcript height.
  new ResizeObserver(queueFollow).observe(box);
  $("main").onclick = () => invoke("main").catch((error) => say(String(error)));
  $("collapse").onclick = () => invoke("collapse").catch(() => {});
  $("settings").onclick = () => invoke("settings").catch((error) => say(String(error)));
  $("pin").onclick = () => invoke("pin", {pinned:!host.pinned}).then((state) => {host.pinned = state.pinned; $("pin").setAttribute("aria-pressed", String(state.pinned));}).catch((error) => say(String(error)));
  $("check").onclick = async () => {
    await sync(true);
    const request = uncertain;
    if (!request || sending || syncing) return;
    $("check").disabled = true;
    try {
      const status = await (await api("/api/chat/request/status", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({text:request.text, request_id:request.requestId, session_epoch:request.epoch})})).json();
      if (uncertain !== request) return;
      if (status.assistant_turn_id) {
        const history = await (await api(`/api/history?${new URLSearchParams({after_id:String(status.assistant_turn_id-1),limit:"1",session_epoch:request.epoch})}`)).json();
        if (history.session_epoch !== epoch || uncertain !== request) return;
        for (const turn of history.turns || []) mergeTurn(turn);
        refreshSourceExpiry();
        settleRequest(); live = null; render(); say("原请求的回复已同步，未重复发送。");
      } else {
        request.canStartNew = ["not_found","failed","interrupted"].includes(status.status);
        say(request.canStartNew ? "原请求无可恢复的回复。请核对记录后，再确认是否作为新请求发送。" : "原请求仍在处理；稍后再同步，不会自动重发。");
      }
    } catch (error) {say(error.message || "暂时无法核对原请求，请稍后再试。");}
    finally {buttons();}
  };
  $("new-request").onclick = () => $("confirm-new").showModal();
  $("cancel-new").onclick = () => $("confirm-new").close();
  $("accept-new").onclick = () => {
    $("confirm-new").close();
    if (!uncertain?.canStartNew || sending) return;
    const text = uncertain.text; uncertain = null; live = null; buttons(); send(text);
  };
  invoke("status").then(window.bubbleHostState).catch(() => {say("请从 Alice 桌面气泡入口打开此页面。"); input.disabled = true;});
})();
