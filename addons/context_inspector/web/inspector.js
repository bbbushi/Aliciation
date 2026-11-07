/* 上下文观测插件：请求列表 → 调用过程 → 最终输入；只读，轮询仅在页面可见时进行。 */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const time = (ts) => ts ? new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false }) : "未记录";
  const short = (v) => String(v ?? "?").slice(0, 8);
  const purposeLabels = { main: "主对话", main_stream: "主对话·流式", main_fallback: "主对话·回落",
    recap_generate: "前情生成", recap_review: "前情审查", rewrite_review: "改写审查", recap_reply_review: "前情回复审查" };
  const stateLabels = { entered: "已进入调用", ok: "收到响应", error: "失败", cancelled: "已取消", stream_interrupted: "流中断" };
  const node = (tag, value, cls) => {
    const n = document.createElement(tag);
    if (value !== undefined && value !== null) n.textContent = value;
    if (cls) n.className = cls;
    return n;
  };

  const state = { requestId: null, callId: null, capture: { metadata: false, body: false }, disabled: false };

  function status(message, kind = "") {
    const n = el("ci-status");
    n.textContent = message;
    n.className = "ci-status" + (kind ? ` ${kind}` : "");
  }

  function clearBodies(root) {
    // scope 变化或禁用时清掉已展示正文与旧引用（插件设计 §6）。
    root.querySelectorAll("pre.ci-body").forEach((n) => n.remove());
  }

  function bodyButton(requestId, contentHash, bodyState) {
    if (!contentHash || bodyState === "none") return node("span", "（未留存正文）", "ci-hint");
    if (bodyState && bodyState !== "stored" && bodyState !== "head" && bodyState !== "on_disk") {
      return node("span", `（正文：${bodyState}）`, "ci-hint");
    }
    const wrap = document.createElement("span");
    const button = node("button", "看留存正文", "ci-btn");
    button.type = "button";
    const pre = node("pre", null, "ci-body");
    pre.hidden = true;
    button.addEventListener("click", async () => {
      if (!pre.hidden) { pre.hidden = true; return; }
      if (!state.capture.body) {
        pre.textContent = "正文采集未开启（context.observer_body_capture）；该调用只有元数据。";
        pre.hidden = false;
        return;
      }
      button.disabled = true;
      const d = await window.ContextInspectorClient.get("body", { request_id: requestId, hash: contentHash });
      button.disabled = false;
      pre.textContent = !d || d.status === "disabled" ? "插件已禁用；正文读取停止。"
        : d.stored ? (d.text || "（空）") + (d.truncated ? "\n…（已截断）" : "")
        : `未留存正文（${d.reason || "not_retained"}）；不能用当前数据补画历史。`;
      pre.hidden = false;
    });
    wrap.append(button, pre);
    return wrap;
  }

  async function loadStatus() {
    const d = await window.ContextInspectorClient.get("status");
    if (d.status === "disabled" || (d.plugin && d.plugin.enabled === false)) {
      state.disabled = true;
      status("插件未启用（[plugins] context_inspector_enabled）。聊天照常运行；本页不再轮询。", "warn");
      el("ci-requests").replaceChildren(node("p", "插件未启用。", "ci-hint"));
      return false;
    }
    if (d.status !== "ok" || !d.plugin) { status("插件状态读取未完成；稍后重试。", "warn"); return false; }
    state.capture = d.plugin.capture || state.capture;
    const quota = d.plugin.quota;
    status(`采集：元数据 ${state.capture.metadata ? "开" : "关"} · 正文 ${state.capture.body ? "开" : "关"}`
      + (quota ? ` · 正文库 ${(quota.body_dir_bytes / 1048576).toFixed(1)} / ${(quota.limit_bytes / 1048576).toFixed(0)} MiB · 保留 ${quota.retention_days} 天` : "")
      + ` · 协议 ${d.plugin.protocol_version}`);
    return true;
  }

  function renderRequests(requests) {
    const list = el("ci-requests");
    list.replaceChildren();
    if (!requests.length) { list.append(node("p", "暂无请求轨迹；进行一次对话后出现。", "ci-hint")); return; }
    for (const request of requests) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "ci-item" + (request.request_id === state.requestId ? " active" : "");
      card.append(node("div", `${time(request.first_event_at)} · ${request.calls.length} 次调用 · ${request.finished ? "已结束" : "进行中"}`));
      card.append(node("div", `请求 ${short(request.request_id)}…${request.assistant_turn_id ? ` · 回复 #${request.assistant_turn_id}` : ""}`, "ci-hint"));
      card.addEventListener("click", () => { state.requestId = request.request_id; state.callId = null; renderAll(); });
      list.append(card);
    }
  }

  function renderCalls(request) {
    const list = el("ci-calls");
    list.replaceChildren();
    if (!request) { list.append(node("p", "先选择左侧请求。", "ci-hint")); return; }
    for (const call of request.calls) {
      const last = call.attempts[call.attempts.length - 1] || {};
      const card = document.createElement("button");
      card.type = "button";
      card.className = "ci-item" + (call.call_id === state.callId ? " active" : "");
      card.append(node("div", `${purposeLabels[call.purpose] || call.purpose || "调用"} · 轮${call.tool_round ?? 0}`));
      card.append(node("div", `${stateLabels[last.status] || last.status || "无尝试"}`
        + (last.duration_ms != null ? ` · ${last.duration_ms}ms` : "")
        + (last.usage ? ` · 入${last.usage.prompt ?? "?"}/出${last.usage.completion ?? "?"} tok` : ""), "ci-hint"));
      card.addEventListener("click", () => { state.callId = call.call_id; renderDetail(request, call); });
      list.append(card);
    }
    if (request.reply_postprocess) {
      list.append(node("p", `回复处理：${request.reply_postprocess.kind} · ${JSON.stringify(request.reply_postprocess.detail || {})}`, "ci-hint"));
    }
    if (request.gaps && request.gaps.length) {
      list.append(node("p", `观测缺口 ${request.gaps.length} 条；缺口处证据缺失，不伪造位置。`, "ci-hint"));
    }
  }

  function renderDetail(request, call) {
    const detail = el("ci-detail");
    clearBodies(detail);
    detail.replaceChildren();
    if (!call) { detail.append(node("p", "先选择中间一次调用。", "ci-hint")); return; }
    const params = call.parameters || {};
    const covered = Object.entries(params.covered || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" · ");
    detail.append(node("p", `参数：${covered || "未记录"}`
      + ((params.uncovered || []).length ? ` · 未覆盖：${params.uncovered.join("、")}` : ""), "ci-hint"));
    if (call.tools && call.tools.length) detail.append(node("p", `工具定义：${call.tools.join("、")}`, "ci-hint"));

    detail.append(node("h3", "最终输入顺序"));
    const order = (call.messages && call.messages.order) || [];
    const bindingsByMessage = new Map();
    for (const b of call.bindings || []) {
      if (!bindingsByMessage.has(b.message_seq)) bindingsByMessage.set(b.message_seq, []);
      bindingsByMessage.get(b.message_seq).push(b);
    }
    for (const entry of order) {
      const row = node("div", null, "ci-msg");
      row.append(node("p", `${entry.seq} ${entry.role}${entry.name ? ` (${entry.name})` : ""}`
        + `${entry.tool_call_id ? ` · tool:${short(entry.tool_call_id)}` : ""} · ${entry.chars ?? "?"} 字`));
      const bound = bindingsByMessage.get(entry.seq);
      if (bound) {
        for (const b of bound) row.append(node("p", `↳ 块 #${b.block_seq} ${b.name} [${b.kind}] 字符 ${b.start}–${b.end}`, "ci-hint"));
      }
      row.append(bodyButton(request.request_id, entry.content_hash, entry.body));
      detail.append(row);
    }

    const blocks = request.blocks || [];
    if (blocks.length) {
      detail.append(node("h3", `装配块 ${blocks.length} · 未入选 ${(request.selections || []).filter((s) => !s.included).length} 条`));
      for (const block of blocks) {
        const row = node("p", `#${block.seq} [${block.kind}] ${block.name} · ${block.chars ?? "?"} 字`, "ci-hint");
        row.append(bodyButton(request.request_id, block.content_hash, block.body_state));
        detail.append(row);
      }
      const excluded = (request.selections || []).filter((s) => !s.included).slice(0, 12);
      if (excluded.length) {
        detail.append(node("p", "未入选：" + excluded.map((s) => `${s.source_id}（${s.reason}）`).join("、"), "ci-hint"));
      }
    }
    detail.append(node("p", `输入整体 hash：${call.messages_hash || "未记录"} · 调用 ${short(call.call_id)}…`, "ci-hint"));
  }

  async function renderAll() {
    const d = await window.ContextInspectorClient.get("requests", { limit: "12" });
    if (!d || d.status === "disabled") return;
    const requests = d.requests || [];
    renderRequests(requests);
    const selected = requests.find((r) => r.request_id === state.requestId);
    if (state.requestId && !selected) {
      const single = await window.ContextInspectorClient.get("request", { request_id: state.requestId });
      if (single && single.requests && single.requests.length) { renderCalls(single.requests[0]); return; }
    }
    renderCalls(selected);
    const call = (selected ? selected.calls : []).find((c) => c.call_id === state.callId);
    renderDetail(selected, call);
  }

  let timer = null;
  async function tick() {
    if (document.visibilityState !== "visible") return;  // 页面不可见不轮询
    const ok = await loadStatus();
    if (state.disabled) { clearInterval(timer); timer = null; return; }
    if (ok) await renderAll();
  }

  async function init() {
    const params = new URLSearchParams(location.search);
    const rid = params.get("request_id");
    if (rid && /^[0-9a-f]{16,64}$/.test(rid)) state.requestId = rid;  // 从回复入口携带
    const ok = await loadStatus();
    if (state.disabled) return;
    if (ok) await renderAll();
    timer = setInterval(tick, 2500);
  }

  init();
})();
