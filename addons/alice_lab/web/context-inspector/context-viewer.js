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

  function replySectionsSummary(audit) {
    if (!audit) return "";
    const reasons = {
      output_empty: "没有收到正文", output_wrong_type: "正文类型错误",
      output_too_large: "正文超过校验上限", output_invalid_unicode: "正文编码无效",
      markdown_fence: "返回了 Markdown 代码块", not_json: "返回内容不是 JSON 对象",
      json_syntax: "JSON 语法错误", duplicate_key: "JSON 有重复字段",
      json_value_limit: "JSON 超出解析限制", root_not_object: "JSON 顶层不是对象",
      field_mismatch: "三栏字段不匹配", answer_empty: "正面回答为空",
      answer_wrong_type: "正面回答不是文本", answer_too_large: "正面回答超长",
      answer_invalid_unicode: "正面回答编码无效"
    };
    const stages = {answer: "已交付正文", format_fallback: "已发出格式失败提示", other: "已交付其他回复"};
    const parts = [audit.status === "accepted" ? "格式通过" : audit.status === "rejected"
      ? reasons[audit.reason] || "格式校验失败" : "本轮未进入格式校验"];
    if (audit.format === "plain_text") parts.push("普通正文");
    if (audit.format === "marked_text") parts.push("检测到旁栏标记");
    if (audit.format === "annotated_text") {
      parts.push(`注释 过程 ${audit.process_comments ?? 0} 条、反省 ${audit.reflection_comments ?? 0} 条`);
      if (audit.empty_markers) parts.push(`已略去 ${audit.empty_markers} 个空标记`);
      const cache = {stored: "注释已入缓存", too_large: "注释超出单条缓存上限",
        ineligible: "本轮注释不满足缓存条件", unavailable: "本轮注释未能写入缓存"};
      if (cache[audit.cache_status]) parts.push(cache[audit.cache_status]);
    }
    if (audit.missing_fields?.length) parts.push(`缺少 ${audit.missing_fields.join("、")}`);
    if (audit.extra_field_count) parts.push(`多出 ${audit.extra_field_count} 个字段`);
    if (audit.json_line && audit.json_column) parts.push(`第 ${audit.json_line} 行第 ${audit.json_column} 列`);
    if (Number.isInteger(audit.output_chars)) parts.push(`模型正文 ${audit.output_chars} 字符`);
    if (audit.finish_reason) {
      const finish = {stop: "正常结束", length: "达到输出长度上限", tool_calls: "发起工具调用",
        function_call: "发起函数调用", content_filter: "内容过滤",
        insufficient_system_resource: "供应商资源不足", other: "其他"};
      parts.push(`模型结束原因为${finish[audit.finish_reason] || "未识别"}`);
    }
    if (audit.status === "accepted") {
      if (audit.process_status === "invalid_list") parts.push("过程栏格式错误，已丢弃");
      if (audit.process_status === "duplicate_marker") parts.push("过程标记重复，已丢弃过程栏");
      if (audit.process_discarded) parts.push(`已丢弃 ${audit.process_discarded} 条过程`);
      if (audit.reflection_status === "duplicate_marker") parts.push("反省标记重复，已丢弃反省栏");
      else if (audit.reflection_status && !["empty", "accepted"].includes(audit.reflection_status)) parts.push("反省栏无效，已丢弃");
    }
    if (audit.inline_markers) parts.push(`${audit.inline_markers} 个标记带同行内容，已按旁栏处理`);
    const review = audit.annotation_review;
    if (review) {
      const states = {off: "复读关闭", applied: "复读已调整", unchanged: "复读后沿用初稿",
        fallback: "复读失败，沿用初稿", skipped_draft: "初稿不适用复读",
        skipped_budget: "复读超出预算，沿用初稿", not_evaluated: "未进入复读",
        overridden: "复读后由交付检查替换"};
      if (states[review.status]) parts.push(states[review.status]);
      if (Number.isInteger(review.elapsed_ms)) parts.push(`复读 ${review.elapsed_ms} ms`);
      const labels = {answer: "正文", task_process: "过程", reflection: "反省"};
      for (const edit of review.changes || []) {
        parts.push(`#${edit.id} ${labels[edit.from]} → ${labels[edit.to]}`);
      }
    }
    if (Number.isInteger(audit.first_content_ms)) parts.push(`正文就绪 ${audit.first_content_ms} ms`);
    if (stages[audit.delivery]) parts.push(stages[audit.delivery]);
    return parts.join(" · ");
  }
  function close() {
    if (!current) return;
    const old = current;
    current = null;
    old.controller?.abort();
    clearTimeout(old.expiryTimer);
    if (old.dialog) {                       /* 弹窗态（缓存注释窗仍用）；内联态无 dialog，由页面区接管 */
      if (old.dialog.open) old.dialog.close();
      old.dialog.remove();
    }
    if (old.rows) old.rows.length = 0;
    old.payload = null;
    if (old.opener?.isConnected) old.opener.focus();
  }

  async function openReplyCache({requestId, scope}) {
    if (!requestId || !scope) return;
    close();
    const dialog = node("dialog", null, "acv-dialog acv-cache-dialog");
    dialog.setAttribute("aria-label", "本轮缓存注释");
    const head = node("header", null, "acv-head");
    const dismiss = node("button", "关闭");
    dismiss.type = "button";
    dismiss.onclick = close;
    head.append(node("h2", "本轮缓存注释"), dismiss);
    const note = node("p", "正在读取…", "acv-note");
    note.setAttribute("role", "status");
    const body = node("div", null, "acv-body");
    body.hidden = true;
    dialog.append(head, note, body);
    const controller = new AbortController();
    const state = {dialog, rows: [], controller, opener: document.activeElement, kind: "reply_cache"};
    current = state;
    dialog.addEventListener("close", () => { if (current === state) close(); });
    document.body.append(dialog);
    dialog.showModal();

    const unavailable = {
      reset: "会话已更换，请从当前请求打开。",
      busy: "会话忙碌，请稍后重新打开。",
      disabled: "上下文观测器已关闭。"
    };
    const startedAt = performance.now();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const data = await window.ContextInspectorClient.get("reply-cache", {request_id: requestId}, controller.signal);
      if (current !== state) return;
      if (data.status !== "ok" || data.scope !== scope) {
        note.textContent = unavailable[data.status] || "读取未完成，请重新打开。";
        return;
      }
      const left = data.remaining_ms - (performance.now() - startedAt);
      if (!data.cached || !Number.isFinite(left) || left <= 0) {
        note.textContent = "这轮的注释已不在可读取的缓存中。";
        return;
      }
      for (const [name, content] of [["过程", data.process?.join("\n\n")], ["反省", data.reflection]]) {
        if (!content) continue;
        const section = node("section", null, "acv-message");
        section.append(node("h3", name), node("pre", content));
        body.append(section);
      }
      note.textContent = "这是 Alice 当时标出的注释。实际操作以工具回执为准。";
      body.hidden = false;
      state.expiryTimer = setTimeout(() => {
        body.replaceChildren();
        body.hidden = true;
        note.textContent = "这轮的注释已到期。";
      }, left);
    } catch (_) {
      if (current === state) note.textContent = "读取未完成，请重新打开。";
    } finally {
      clearTimeout(timeout);
    }
  }

  /* 05-21：完整上下文不再弹独立窗——内联挂载进观测页折叠区（options.container 必填）。
     工具条/目录/正文渲染与原弹窗一致；scope 失配改为就地提示而非关闭。 */
  function open(options) {
    close();
    const container = options.container;
    if (!container) return;
    const calls = options.calls || [];
    if (!calls.length) {
      container.replaceChildren(node("p", "本请求暂无模型调用。", "ci-hint"));
      return;
    }
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
    const frame = node("div", null, "acv-inline");
    frame.append(toolbar, note, layout);
    container.replaceChildren(frame);
    const state = {rows: [], controller: null, payload: null, revision: 0,
                   requestId: options.requestId, scope: options.scope};
    current = state;
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
        const data = await window.ContextInspectorClient.get("call",
          {request_id: options.requestId, call_id: select.value}, controller.signal);
        if (current !== state || revision !== state.revision) return;
        if (data.status === "reset" || options.scope && data.scope && data.scope !== options.scope) {
          note.textContent = "会话已更换，请重新展开本节读取。";
          note.classList.add("acv-warning");
          return;
        }
        if (data.status !== "ok") {
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
    load();
  }
  window.addEventListener("pagehide", close);
  window.addEventListener("alice-observer-scope-changed", close);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && current?.kind === "reply_cache") close();
  });
  window.AliceContextViewer = {open, close, openReplyCache, replySectionsSummary};
})();
