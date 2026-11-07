/* 上下文观测插件：请求列表 → 调用过程 → 最终输入；只读，轮询仅在页面可见时进行。
   展示形态参考 dsh-context：分类颜色编码、组成堆叠条、逐调用趋势、按模型读取
   顺序的 DNA 色带与树状浏览器、调用间差异徽标。数据只来自公开投影；字符估算
   占比与供应商 usage 分列展示，不混用。 */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const time = (ts) => ts ? new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false }) : "未记录";
  const short = (v) => String(v ?? "?").slice(0, 8);
  const kfmt = (n) => n == null ? "?" : n >= 10000 ? (n / 1000).toFixed(1) + "k" : String(n);
  const purposeLabels = { main: "主对话", main_stream: "主对话·流式", main_fallback: "主对话·回落",
    recap_generate: "前情生成", recap_review: "前情审查", rewrite_review: "改写审查", recap_reply_review: "前情回复审查" };
  const stateLabels = { entered: "已进入调用", ok: "收到响应", error: "失败", cancelled: "已取消", stream_interrupted: "流中断" };
  const node = (tag, value, cls) => {
    const n = document.createElement(tag);
    if (value !== undefined && value !== null) n.textContent = value;
    if (cls) n.className = cls;
    return n;
  };

  // 分类颜色编码（对应核心 manifest 的块 kind；dsh-context 六类的 Alice 对应物）
  const KINDS = [
    { key: "stable", label: "稳定提示（宪法·人格·表达·能力说明）", cls: "k-stable" },
    { key: "index", label: "记忆索引（候选目录）", cls: "k-index" },
    { key: "background", label: "背景材料（来源·前情·见闻观点）", cls: "k-background" },
    { key: "history", label: "历史原文", cls: "k-history" },
    { key: "tool_result", label: "工具回执", cls: "k-tool" },
    { key: "assistant", label: "模型消息（工具调用等）", cls: "k-assistant" },
    { key: "user_new", label: "用户消息（未归类）", cls: "k-user" },
  ];
  const KIND_BY_KEY = new Map(KINDS.map((k) => [k.key, k]));
  const roleKind = (role) => role === "tool" ? "tool_result" : role === "assistant" ? "assistant"
    : role === "user" ? "user_new" : "stable";

  const state = { requestId: null, callId: null, capture: { metadata: false, body: false }, disabled: false };

  function status(message, kind = "") {
    const n = el("ci-status");
    n.textContent = message;
    n.className = "ci-status" + (kind ? ` ${kind}` : "");
  }

  function clearBodies(root) {
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

  // ---- 片段计算：块绑定 + 未绑定消息按角色归类 → 模型读取顺序的着色片段 ----

  function computeSegments(request, call) {
    const blocks = request.blocks || [];
    const segments = [];
    for (const b of call.bindings || []) {
      const block = blocks[b.block_seq];
      if (!block) continue;
      segments.push({ key: block.kind, label: block.name, kindLabel: (KIND_BY_KEY.get(block.kind) || {}).label || block.kind,
        chars: Math.max(0, (b.end ?? 0) - (b.start ?? 0)) || block.chars || 0, message_seq: b.message_seq,
        start: b.start ?? 0, content_hash: block.content_hash, bodyState: block.body_state, isBlock: true });
    }
    const boundMessages = new Set((call.bindings || []).map((b) => b.message_seq));
    for (const m of (call.messages && call.messages.order) || []) {
      if (boundMessages.has(m.seq)) continue;
      segments.push({ key: roleKind(m.role), label: `消息 #${m.seq}（${m.role}）`,
        kindLabel: (KIND_BY_KEY.get(roleKind(m.role)) || {}).label || m.role, chars: m.chars || 0,
        message_seq: m.seq, start: 0, content_hash: m.content_hash, bodyState: m.body, isBlock: false });
    }
    segments.sort((a, b) => (a.message_seq - b.message_seq) || (a.start - b.start));
    const totals = {};
    let total = 0;
    for (const s of segments) { totals[s.key] = (totals[s.key] || 0) + s.chars; total += s.chars; }
    return { segments, totals, total };
  }

  function band(segments, total) {
    const bandEl = node("div", null, "ci-band");
    if (!total) { bandEl.append(node("span", "（空输入）", "ci-hint")); return bandEl; }
    for (const s of segments) {
      const seg = node("span", null, `ci-seg ${(KIND_BY_KEY.get(s.key) || {}).cls || "k-other"}`);
      seg.style.width = `${Math.max(0.4, (s.chars / total) * 100)}%`;
      seg.title = `#${s.isBlock ? "块" : "消息"} ${s.label} · ${s.kindLabel} · ${s.chars} 字`;
      bandEl.append(seg);
    }
    return bandEl;
  }

  function legend(totals, total) {
    const wrap = node("div", null, "ci-legend");
    for (const kind of KINDS) {
      if (!totals[kind.key]) continue;
      const chip = node("span", null, "ci-chip");
      chip.append(node("span", null, `ci-swatch ${kind.cls}`),
        node("span", `${kind.label.split("（")[0]} ${kfmt(totals[kind.key])}字 · ${Math.round((totals[kind.key] / total) * 100)}%`));
      wrap.append(chip);
    }
    return wrap;
  }

  function deltaBadge(previous, call) {
    if (!previous || !previous.messages || !call.messages) return null;
    const dCount = call.messages.count - previous.messages.count;
    const dChars = call.messages.total_chars - previous.messages.total_chars;
    const parts = [];
    if (dCount) parts.push(`${dCount > 0 ? "+" : ""}${dCount} 条消息`);
    if (dChars) parts.push(`${dChars > 0 ? "+" : "−"}${kfmt(Math.abs(dChars))} 字`);
    if (!parts.length) return node("span", "与上次输入一致", "ci-hint");
    return node("span", `vs 上次：${parts.join(" · ")}`, "ci-delta");
  }

  async function renderAll() {
    const d = await window.ContextInspectorClient.get("requests", { limit: "12" });
    if (!d || d.status === "disabled") return;
    const requests = d.requests || [];
    renderRequests(requests);
    let selected = requests.find((r) => r.request_id === state.requestId);
    if (state.requestId && !selected) {
      const single = await window.ContextInspectorClient.get("request", { request_id: state.requestId });
      if (single && single.requests && single.requests.length) selected = single.requests[0];
    }
    renderCalls(selected);
    const call = (selected ? selected.calls : []).find((c) => c.call_id === state.callId);
    renderDetail(selected, call);
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
      if (request.blocks && request.blocks.length) {
        const totals = {};
        let total = 0;
        for (const b of request.blocks) { totals[b.kind] = (totals[b.kind] || 0) + (b.chars || 0); total += b.chars || 0; }
        card.append(band(Object.keys(totals).map((k) => ({ key: k, chars: totals[k], label: k, kindLabel: k })), total));
      }
      card.addEventListener("click", () => { state.requestId = request.request_id; state.callId = null; renderAll(); });
      list.append(card);
    }
  }

  function renderCalls(request) {
    const list = el("ci-calls");
    list.replaceChildren();
    if (!request) { list.append(node("p", "先选择左侧请求。", "ci-hint")); return; }
    let previous = null;
    for (const call of request.calls) {
      const last = call.attempts[call.attempts.length - 1] || {};
      const card = document.createElement("button");
      card.type = "button";
      card.className = "ci-item" + (call.call_id === state.callId ? " active" : "");
      card.append(node("div", `${purposeLabels[call.purpose] || call.purpose || "调用"} · 轮${call.tool_round ?? 0}`));
      const line = node("div", null, "ci-hint");
      line.textContent = `${stateLabels[last.status] || last.status || "无尝试"}`
        + (last.duration_ms != null ? ` · ${last.duration_ms}ms` : "")
        + (last.usage ? ` · usage 入${last.usage.prompt ?? "?"}/出${last.usage.completion ?? "?"} tok` : "");
      card.append(line);
      const seg = computeSegments(request, call);
      card.append(band(seg.segments, seg.total));
      card.append(node("div", `合计 ${kfmt(seg.total)} 字 · ${seg.segments.length} 段`, "ci-hint"));
      const badge = deltaBadge(previous, call);
      if (badge) card.append(badge);
      card.addEventListener("click", () => { state.callId = call.call_id; renderCalls(request); renderDetail(request, call); });
      list.append(card);
      previous = call;
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
    if (!call || !request) { detail.append(node("p", "先选择中间一次调用。", "ci-hint")); return; }

    const seg = computeSegments(request, call);
    detail.append(node("h3", "组成（按模型读取顺序 · 字符估算占比）"));
    detail.append(band(seg.segments, seg.total));
    detail.append(legend(seg.totals, seg.total));
    detail.append(node("p", `合计 ${kfmt(seg.total)} 字 · 使用说明与人格源材料分别计块；估算字符与供应商 usage 分列，不混用。`, "ci-hint"));

    detail.append(node("h3", "DNA · 模型读取顺序逐项"));
    const dna = node("div", null, "ci-dna");
    let position = 0;
    for (const s of seg.segments) {
      position += 1;
      const row = node("div", null, "ci-dna-row");
      row.append(node("span", String(position), `ci-pos ${(KIND_BY_KEY.get(s.key) || {}).cls || "k-other"}`));
      const mid = node("div", null, "ci-dna-mid");
      mid.append(node("p", `${s.isBlock ? "块" : "消息"} ${s.label} · ${s.kindLabel} · ${s.chars} 字 · 位于消息 #${s.message_seq}`));
      mid.append(bodyButton(request.request_id, s.content_hash, s.bodyState));
      row.append(mid);
      dna.append(row);
    }
    detail.append(dna);

    const params = call.parameters || {};
    const covered = Object.entries(params.covered || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" · ");
    const adv = document.createElement("details");
    adv.append(node("summary", "调用参数与工具定义"));
    adv.append(node("p", `参数：${covered || "未记录"}`
      + ((params.uncovered || []).length ? ` · 未覆盖：${params.uncovered.join("、")}` : ""), "ci-hint"));
    if (call.tools && call.tools.length) adv.append(node("p", `工具：${call.tools.join("、")}`, "ci-hint"));
    adv.append(node("p", `输入整体 hash：${call.messages_hash || "未记录"} · 调用 ${short(call.call_id)}…`, "ci-hint"));
    detail.append(adv);

    // 树状浏览器：装配块按类别折叠；未入选候选按类别归并展示
    const blocks = request.blocks || [];
    if (blocks.length) {
      detail.append(node("h3", "上下文浏览器"));
      const byKind = new Map();
      for (const block of blocks) {
        if (!byKind.has(block.kind)) byKind.set(block.kind, []);
        byKind.get(block.kind).push(block);
      }
      const selectedSources = new Set((call.bindings || []).map((b) => b.block_seq));
      for (const [kind, items] of byKind) {
        const kindInfo = KIND_BY_KEY.get(kind) || { label: kind, cls: "k-other" };
        const group = document.createElement("details");
        group.append(node("summary", `${kindInfo.label} · ${items.length} 块`));
        for (const block of items) {
          const included = selectedSources.has(block.seq);
          const row = node("p", `#${block.seq} ${block.name} · ${block.chars ?? "?"} 字 · ${included ? "已进入本次调用" : "未进入本次调用"}`, "ci-hint");
          row.append(bodyButton(request.request_id, block.content_hash, block.body_state));
          group.append(row);
        }
        detail.append(group);
      }
      const selections = request.selections || [];
      if (selections.length) {
        const sel = document.createElement("details");
        sel.append(node("summary", `选择决策 ${selections.length} 条（含未入选原因）`));
        for (const s of selections.slice(0, 60)) {
          sel.append(node("p", `${s.source_id} · ${s.included ? "入选" : "未入选"} · ${s.reason}`, "ci-hint"));
        }
        detail.append(sel);
      }
    }
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
