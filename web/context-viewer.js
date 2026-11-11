/* Shared read-only context window. Content is always rendered as plain text. */
"use strict";
(() => {
  let current = null;
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const reason = value => ({historical_not_captured: "历史记录未采集", none: "当轮未采集",
    capture_disabled: "当轮采集已关闭", rolling_window_evicted: "已移出最近10请求窗口",
    retention_expired: "已超过7天留存期", not_retained: "正文未留存或已清理",
    too_large: "超过单条采集上限", body_budget_exceeded: "正文库容量不足",
    view_budget_exceeded: "超过本次查看容量", write_failed: "采集写入失败",
    capture_index_unavailable: "留存索引不可用", snapshot_unreadable: "快照无法解析",
    hash_mismatch: "正文与记录摘要不一致"})[value] || value || "未记录";
  const pretty = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  function close() {
    if (!current) return;
    const old = current;
    current = null;
    old.controller?.abort();
    if (old.dialog.open) old.dialog.close();
    old.dialog.remove();
    old.rows.length = 0;
    old.payload = null;
    if (old.opener?.isConnected) old.opener.focus();
  }
  function open(options) {
    close();
    const calls = options.calls || [];
    if (!calls.length) return;
    const dialog = node("dialog", null, "acv-dialog");
    dialog.setAttribute("aria-label", "完整上下文");
    const head = node("header", null, "acv-head");
    const title = node("h2", "完整上下文");
    const closeButton = node("button", "关闭");
    closeButton.type = "button";
    closeButton.onclick = close;
    head.append(title, closeButton);
    const toolbar = node("div", null, "acv-toolbar");
    const selectLabel = node("label", "模型调用 ");
    const select = document.createElement("select");
    for (const [index, call] of calls.entries()) {
      const item = node("option", `${index + 1}. ${call.purpose || "调用"} · 工具轮 ${call.tool_round ?? 0}`);
      item.value = call.call_id;
      select.append(item);
    }
    select.value = calls.some(call => call.call_id === options.callId) ? options.callId : calls[calls.length - 1].call_id;
    selectLabel.append(select);
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索消息、工具定义或参数…";
    search.setAttribute("aria-label", "搜索完整上下文");
    const reload = node("button", "重新读取"), copy = node("button", "复制上下文");
    const searchCount = node("span", "", "acv-search-count");
    searchCount.setAttribute("role", "status");
    reload.type = copy.type = "button";
    toolbar.append(selectLabel, search, reload, copy, searchCount);
    const note = node("p", "正在读取…", "acv-note");
    note.setAttribute("role", "status");
    const layout = node("div", null, "acv-layout");
    const toc = node("nav", null, "acv-toc");
    toc.setAttribute("aria-label", "上下文目录");
    const body = node("div", null, "acv-body");
    layout.append(toc, body);
    dialog.append(head, toolbar, note, layout);
    const state = {dialog, rows: [], controller: null, payload: null, revision: 0, opener: document.activeElement};
    current = state;
    dialog.addEventListener("close", () => { if (current === state) close(); });
    dialog.addEventListener("cancel", e => { e.preventDefault(); close(); });
    function row(label, content, warning = false) {
      const section = node("section", null, "acv-message" + (warning ? " acv-missing" : ""));
      const heading = node("h3", label);
      const pre = node("pre", content);
      section.append(heading, pre);
      const jump = node("button", label);
      jump.type = "button";
      jump.onclick = () => section.scrollIntoView({block: "start"});
      toc.append(jump);
      body.append(section);
      state.rows.push({section, jump, text: (label + "\n" + content).toLocaleLowerCase()});
    }
    function render(data) {
      body.replaceChildren(); toc.replaceChildren(); state.rows.length = 0;
      const exact = data.complete && data.input;
      const messages = exact ? data.input.messages : data.messages || [];
      note.textContent = (exact ? "完整输入快照" : "部分留存 · " + reason(data.snapshot_reason))
        + ` · ${messages.length} 条消息 · 请求 ${options.requestId} · 调用 ${select.value}`;
      note.classList.toggle("acv-warning", !exact);
      for (const [index, message] of messages.entries()) {
        if (!message || typeof message !== "object") { row(`#${index} 未识别消息`, pretty(message), true); continue; }
        const label = `#${index} ${message.role || "未知角色"}${message.name ? " · " + message.name : ""}`;
        if (!exact && !message.retained) row(label, `【未留存正文：${reason(message.reason)}】`, true);
        else row(label, (message.content == null ? "（无文本正文）" : pretty(message.content))
          + (message.truncated ? "\n【正文已截断】" : ""), !!message.truncated);
        if (exact) {
          const envelope = Object.fromEntries(Object.entries(message).filter(([key]) => !["role", "content"].includes(key)));
          if (Object.keys(envelope).length) row(`#${index} 消息附加字段 / 工具调用`, pretty(envelope));
        } else if (message.tool_call_count) {
          row(`#${index} 工具调用参数`, `旧记录仅保存调用数量：${message.tool_call_count}；参数未留存。`, true);
        }
      }
      row("SDK 工具定义", exact ? pretty(data.input.tools || [])
        : "【完整定义未留存】\n当时记录的工具名：" + pretty(data.call?.tools || []), !exact);
      row("模型参数", pretty(exact ? data.input.parameters : data.call?.parameters || {}));
      row("记录范围", data.notice + "\n未知参数仅列名称，未留存其值。" + (!exact ? "\n旧记录按已留存内容展示，缺失字段保持未知。" : ""));
      search.value = ""; searchCount.textContent = ""; body.scrollTop = 0;
      copy.disabled = false;
    }
    async function load() {
      state.controller?.abort();
      state.controller = new AbortController();
      const controller = state.controller, revision = ++state.revision;
      const timeout = setTimeout(() => controller.abort(), 20000);
      body.replaceChildren(); toc.replaceChildren(); state.rows.length = 0; state.payload = null;
      copy.disabled = true; copy.textContent = "复制上下文";
      note.textContent = "正在读取这次调用的已留存输入…";
      try {
        const params = new URLSearchParams({request_id: options.requestId, call_id: select.value});
        if (options.scope) params.set("scope", options.scope);
        const response = await fetch(`${options.endpoint}?${params}`, {headers: {"X-Alice-Intent": "observer"},
          cache: "no-store", signal: controller.signal});
        const data = await response.json();
        if (current !== state || revision !== state.revision) return;
        if (data.status === "reset" || options.scope && data.scope && data.scope !== options.scope) {
          close(); return;
        }
        if (!response.ok || data.status !== "ok") {
          throw new Error(data.status === "busy" ? "会话忙碌，请稍后重新读取。"
            : data.status === "disabled" ? "观测插件已禁用。"
            : data.error || "读取未完成，请确认服务已更新后重试。");
        }
        state.payload = data; render(data);
      } catch (error) {
        if (current === state && revision === state.revision) {
          note.textContent = error.name === "AbortError" ? "读取超时，请重新读取。" : error.message;
          note.classList.add("acv-warning");
        }
      } finally { clearTimeout(timeout); }
    }
    select.onchange = reload.onclick = load;
    search.oninput = () => {
      const q = search.value.trim().toLocaleLowerCase();
      let count = 0;
      for (const item of state.rows) {
        const matches = !q || item.text.includes(q);
        item.section.hidden = item.jump.hidden = !matches;
        if (matches) count++;
      }
      search.title = q ? `匹配 ${count} 段` : "显示全部内容";
      searchCount.textContent = q ? `匹配 ${count} 段` : "";
    };
    copy.onclick = async () => {
      if (!state.payload) return;
      const data = state.payload;
      const text = data.complete ? pretty(data.input)
        : "【上下文仅部分留存】\n" + pretty({request_id: data.request_id, call_id: data.call_id,
          snapshot_reason: data.snapshot_reason, messages: data.messages, recorded_metadata: data.call});
      try { await navigator.clipboard.writeText(text); if (current === state) copy.textContent = "已复制"; }
      catch (_) { if (current === state) note.textContent = "复制未成功，可在正文中选择文本复制。"; }
    };
    document.body.append(dialog);
    dialog.showModal();
    load();
  }
  window.addEventListener("pagehide", close);
  window.addEventListener("alice-observer-scope-changed", close);
  window.AliceContextViewer = {open, close};
})();
