/* 上下文观测插件 · 仪表板（复刻 dsh-context 形态：KPI 行/组成卡/趋势卡/DNA/浏览器）。
   只读；轮询仅页面可见时。数据全部来自公开投影；字符估算与供应商 usage 分列。 */
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

  const KINDS = [
    { key: "stable", label: "稳定提示", cls: "k-stable" },
    { key: "index", label: "记忆索引", cls: "k-index" },
    { key: "background", label: "背景材料", cls: "k-background" },
    { key: "history", label: "历史原文", cls: "k-history" },
    { key: "tool_result", label: "工具回执", cls: "k-tool" },
    { key: "assistant", label: "模型消息", cls: "k-assistant" },
    { key: "user_new", label: "用户消息", cls: "k-user" },
  ];
  const KIND_BY_KEY = new Map(KINDS.map((k) => [k.key, k]));
  const roleKind = (role) => role === "tool" ? "tool_result" : role === "assistant" ? "assistant"
    : role === "user" ? "user_new" : "stable";

  const state = { requests: [], requestId: null, callId: null, mode: "total",
    capture: {}, canReadBody: false, fullCall: false };

  function status(message, kind = "") {
    const n = el("ci-status");
    n.textContent = message;
    n.className = "ci-status" + (kind ? ` ${kind}` : "");
  }

  function clearBodies(root) {
    root.querySelectorAll("pre.ci-body").forEach((n) => n.remove());
  }

  const bodyReason = reason => ({none: "当轮未采集", historical_not_captured: "历史请求未采集正文",
    capture_disabled: "当轮正文采集已关闭", rolling_window_evicted: "已移出最近10请求窗口",
    retention_expired: "已超过7天留存期", body_budget_exceeded: "正文库容量不足",
    too_large: "正文超过单条采集上限", write_failed: "正文写入失败",
    capture_index_unavailable: "正文留存索引不可用", not_retained: "正文文件未留存或已清理"})[reason] || reason || "原因未记录";

  function bodyButton(requestId, contentHash, bodyState) {
    if (!contentHash || bodyState === "none") return node("span", "（未留存正文）", "ci-hint");
    if (bodyState && bodyState !== "stored" && bodyState !== "head" && bodyState !== "on_disk") {
      return node("span", `（未留存正文：${bodyReason(bodyState)}）`, "ci-hint");
    }
    if (!state.canReadBody) return node("span", "（已留存 · 未授权正文）", "ci-hint");
    const wrap = document.createElement("span");
    const button = node("button", "正文", "ci-btn");
    button.type = "button";
    const pre = node("pre", null, "ci-body");
    pre.hidden = true;
    button.addEventListener("click", async () => {
      if (!pre.hidden) { pre.hidden = true; return; }
      button.disabled = true;
      try {
        const data = await window.ContextInspectorClient.get("body", {request_id: requestId, hash: contentHash});
        pre.textContent = data.stored ? (data.text || "（空）") + (data.truncated ? "\n…（已截断）" : "")
          : `未留存正文（${bodyReason(data.reason)}）`;
      } catch (error) {
        pre.textContent = error.message;
      } finally {
        button.disabled = false;
        pre.hidden = false;
      }
    });
    wrap.append(button, pre);
    return wrap;
  }

  // ---- 片段与统计 ----

  function segmentsOf(request, call) {
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
      seg.title = `${s.label} · ${s.kindLabel} · ${s.chars} 字`;
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
        node("span", `${kind.label} ${kfmt(totals[kind.key])}字 · ${Math.round((totals[kind.key] / total) * 100)}%`));
      wrap.append(chip);
    }
    if (!total) wrap.append(node("span", "（无内容）", "ci-hint"));
    return wrap;
  }

  // ---- 视图 ----

  function renderRequestBar(requests) {
    const bar = el("ci-requestbar");
    bar.replaceChildren();
    for (const request of requests) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "ci-req" + (request.request_id === state.requestId ? " active" : "");
      card.append(node("div", `${time(request.first_event_at)}`, "t"));
      card.append(node("div", `${request.calls.length} 次调用${request.assistant_turn_id ? ` · 回复#${request.assistant_turn_id}` : ""} · ${request.finished ? "已结束" : "进行中"}`, "s"));
      card.addEventListener("click", () => {
        state.requestId = request.request_id;
        state.callId = request.calls.length ? request.calls[request.calls.length - 1].call_id : null;
        renderAll();
      });
      bar.append(card);
    }
  }

  function renderKPIs(request, call) {
    const kpis = el("ci-kpis");
    kpis.replaceChildren();
    if (!request) return;
    const last = call ? (call.attempts[call.attempts.length - 1] || {}) : {};
    const usage = last.usage || null;
    const kpi = (k, v, s) => { const c = node("div", null, "ci-kpi"); c.append(node("div", k, "k"), node("div", v, "v")); if (s) c.append(node("div", s, "s")); return c; };
    kpis.append(kpi("模型调用", `${request.calls.length} 次`, `请求 ${short(request.request_id)}…`));
    kpis.append(kpi("当前调用输入", call ? `${kfmt((call.messages || {}).total_chars)} 字` : "—",
      call ? `${(call.messages || {}).count} 条消息 · ${purposeLabels[call.purpose] || call.purpose} · 轮${call.tool_round ?? 0}` : undefined));
    kpis.append(kpi("供应商 usage", usage ? `入${usage.prompt ?? "?"} / 出${usage.completion ?? "?"} tok` : "未知",
      usage ? `${stateLabels[last.status] || last.status} · ${last.duration_ms ?? "?"}ms` : "未知不填零"));
    kpis.append(kpi("装配块", `${(request.blocks || []).length} 块`,
      `选择决策 ${(request.selections || []).length} 条`));
    const continued = (request.blocks || []).filter((b) => b.name === "source_continuation");
    if (continued.length) {
      kpis.append(kpi("历史接续来源", `${continued.length} 段`,
        `共 ${kfmt(continued.reduce((n, b) => n + (b.chars || 0), 0))} 字 · 随近期历史恢复，非本轮主动共享`));
    }
    kpis.append(kpi("状态", request.finished ? "已结束" : "进行中",
      request.assistant_turn_id ? `回复 #${request.assistant_turn_id}` : (request.delivery || "未落库")));
    const format = window.AliceContextViewer.replySectionsSummary(request.reply_sections);
    if (format) {
      const reply = kpi("回复三栏", request.reply_sections.status === "accepted" ? "格式通过"
        : request.reply_sections.status === "rejected" ? "格式未通过" : "未校验", format);
      if (state.canReadBody && request.reply_sections.cache_status === "stored") {
        const cached = node("button", "查看缓存注释", "ci-btn");
        cached.type = "button";
        cached.onclick = () => window.AliceContextViewer.openReplyCache({
          requestId: request.request_id, scope: window.ContextInspectorClient.getScope()
        });
        reply.append(cached);
      }
      kpis.append(reply);
    }
  }

  function renderComposition(request, call) {
    const body = el("ci-composition");
    clearBodies(body);
    body.replaceChildren();
    if (!call) { body.append(node("p", "先在趋势图选择一次调用。", "ci-hint")); return; }
    const seg = segmentsOf(request, call);
    body.append(band(seg.segments, seg.total));
    body.append(legend(seg.totals, seg.total));
    const last = call.attempts[call.attempts.length - 1] || {};
    body.append(node("p", `合计 ${kfmt(seg.total)} 字 · ${seg.segments.length} 段 · 状态 ${stateLabels[last.status] || last.status || "无尝试"}`
      + (last.usage ? ` · usage 入${last.usage.prompt ?? "?"}/出${last.usage.completion ?? "?"} tok（另列）` : ""), "ci-hint"));
  }

  function renderTrend(request) {
    const body = el("ci-trend");
    body.replaceChildren();
    if (!request) { body.append(node("p", "先选择请求。", "ci-hint")); return; }
    const calls = request.calls;
    const stats = calls.map((c) => {
      const seg = segmentsOf(request, c);
      const prev = calls[calls.indexOf(c) - 1] || null;
      let delta = null;
      if (prev && prev.messages && c.messages) {
        delta = { count: c.messages.count - prev.messages.count,
                  chars: c.messages.total_chars - prev.messages.total_chars,
                  byKind: {} };
        const prevSeg = segmentsOf(request, prev);
        for (const kind of KINDS) delta.byKind[kind.key] = (seg.totals[kind.key] || 0) - (prevSeg.totals[kind.key] || 0);
      }
      return { call: c, seg, delta };
    });
    const maxTotal = Math.max(1, ...stats.map((s) => s.seg.total));
    const maxDelta = Math.max(1, ...stats.map((s) => s.delta ? Math.max(...Object.values(s.delta.byKind).map(Math.abs)) : 0));
    const chart = node("div", null, "ci-chart");
    stats.forEach(({ call, seg, delta }, index) => {
      const wrap = node("div", null, "ci-colwrap" + (call.call_id === state.callId ? " active" : ""));
      const col = node("div", null, "ci-col");
      const reference = state.mode === "delta" ? (delta ? delta.byKind : null) : seg.totals;
      const scale = state.mode === "delta" ? maxDelta : maxTotal;
      const order = [...KINDS].reverse();
      for (const kind of order) {
        let value = reference ? (reference[kind.key] || 0) : 0;
        if (state.mode === "delta") {
          const negative = value < 0;
          value = Math.abs(value);
          if (!value) continue;
          const segEl = node("span", null, `ci-seg ${negative ? "k-neg" : (KIND_BY_KEY.get(kind.key) || {}).cls || "k-other"}`);
          segEl.style.height = `${Math.max(2, (value / scale) * 100)}%`;
          segEl.title = `${kind.label} ${negative ? "−" : "+"}${kfmt(value)} 字 vs 上一次`;
          col.append(segEl);
        } else {
          if (!value) continue;
          const segEl = node("span", null, `ci-seg ${(KIND_BY_KEY.get(kind.key) || {}).cls || "k-other"}`);
          segEl.style.height = `${(value / scale) * 100}%`;
          segEl.title = `${kind.label} ${kfmt(value)} 字`;
          col.append(segEl);
        }
      }
      wrap.append(col);
      const label = node("div", null, "lbl");
      label.append(node("div", `${purposeLabels[call.purpose] || call.purpose}`));
      label.append(node("div", `轮${call.tool_round ?? 0} · ${kfmt(seg.total)}字`));
      if (delta) {
        const sign = delta.chars > 0 ? "+" : delta.chars < 0 ? "−" : "";
        if (delta.chars || delta.count) {
          label.append(node("span", `${sign}${kfmt(Math.abs(delta.chars))}字`, "ci-deltabadge"));
        }
      }
      wrap.append(label);
      wrap.addEventListener("click", () => {
        state.callId = call.call_id;
        state.requestId = request.request_id;
        renderAll();
      });
      chart.append(wrap);
    });
    body.append(chart);
    if (state.mode === "delta") {
      body.append(node("p", "Delta 模式：与同请求上一次调用比较；斜线纹=该类减少。", "ci-hint"));
    }
  }

  function renderDNA(request, call) {
    const body = el("ci-dna");
    clearBodies(body);
    body.replaceChildren();
    if (!call) { body.append(node("p", "选择一次调用。", "ci-hint")); return; }
    const seg = segmentsOf(request, call);
    const dna = node("div", null, "ci-dna");
    let position = 0;
    const maxChars = Math.max(1, ...seg.segments.map((s) => s.chars));
    for (const s of seg.segments) {
      position += 1;
      const row = node("div", null, "ci-dna-row");
      row.append(node("span", String(position), `ci-pos ${(KIND_BY_KEY.get(s.key) || {}).cls || "k-other"}`));
      const mid = node("div", null, "ci-dna-mid");
      mid.append(node("p", `${s.label} · ${s.kindLabel} · ${s.chars} 字 · 消息 #${s.message_seq}`));
      const track = node("div", null, "ci-dna-track");
      const bar = node("div", null, `ci-dna-bar ${(KIND_BY_KEY.get(s.key) || {}).cls || "k-other"}`);
      bar.style.width = `${Math.max(2, (s.chars / maxChars) * 100)}%`;
      track.append(bar);
      mid.append(track);
      mid.append(bodyButton(request.request_id, s.content_hash, s.bodyState));
      row.append(mid);
      dna.append(row);
    }
    if (!position) dna.append(node("p", "（空输入）", "ci-hint"));
    body.append(dna);
  }

  function renderBrowser(request, call) {
    const body = el("ci-browser");
    clearBodies(body);
    body.replaceChildren();
    if (!request) { body.append(node("p", "选择一次调用。", "ci-hint")); return; }
    const tree = node("div", null, "ci-tree");
    const selectedSources = new Set(((call && call.bindings) || []).map((b) => b.block_seq));
    const byKind = new Map();
    for (const block of request.blocks || []) {
      if (!byKind.has(block.kind)) byKind.set(block.kind, []);
      byKind.get(block.kind).push(block);
    }
    for (const [kind, items] of byKind) {
      const kindInfo = KIND_BY_KEY.get(kind) || { label: kind, cls: "k-other" };
      const group = document.createElement("details");
      if (call) group.open = items.some((b) => selectedSources.has(b.seq));
      const inCall = items.filter((b) => selectedSources.has(b.seq)).length;
      const summary = node("summary", null);
      summary.append(node("span", `${kindInfo.label} `),
        node("span", `· ${items.length} 块${call ? ` · ${inCall} 块在当前调用` : ""}`, "cnt"));
      group.append(summary);
      for (const block of items) {
        const included = selectedSources.has(block.seq);
        const row = node("p", `#${block.seq} ${block.name} · ${block.chars ?? "?"} 字 · ${call ? (included ? "已进入本次调用" : "未进入本次调用") : "待选择调用"}`, "ci-hint");
        row.append(bodyButton(request.request_id, block.content_hash, block.body_state));
        group.append(row);
      }
      tree.append(group);
    }
    const selections = request.selections || [];
    if (selections.length) {
      const sel = document.createElement("details");
      const selSummary = node("summary", null);
      selSummary.append(node("span", `选择决策 `), node("span", `· ${selections.length} 条`, "cnt"));
      sel.append(selSummary);
      for (const s of selections.slice(0, 60)) {
        sel.append(node("p", `${s.source_id} · ${s.included ? "入选" : "未入选"} · ${s.reason}`, "ci-hint"));
      }
      tree.append(sel);
    }
    if (request.gaps && request.gaps.length) {
      tree.append(node("p", `观测缺口 ${request.gaps.length} 条；缺口处证据缺失，不伪造位置。`, "ci-hint"));
    }
    body.append(tree);
  }

  function renderAll() {
    const requests = state.requests;
    renderRequestBar(requests);
    const request = requests.find((r) => r.request_id === state.requestId) || null;
    renderKPIs(request, null);
    const call = (request ? request.calls : []).find((c) => c.call_id === state.callId) || null;
    const full = el("ci-full-context");
    full.disabled = !state.canReadBody || !state.fullCall || !request?.calls.length;
    full.title = !state.canReadBody ? "需在 Alice 授予正文后重新连接" :
      !state.fullCall ? "完整上下文需要新版 Alice" : "";
    renderKPIs(request, call);
    renderComposition(request, call);
    renderTrend(request);
    renderDNA(request, call);
    renderBrowser(request, call);
  }

  let lastSignature = "", refreshing = false;
  async function tick() {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try {
      const info = await window.ContextInspectorClient.get("status");
      state.canReadBody = info.can_read_body === true;
      state.fullCall = info.full_call !== false;
      state.capture = info.capture || {};
      const quota = info.quota;
      const label = value => value == null ? "未知" : value ? "开" : "关";
      status(`采集：元数据 ${label(state.capture.metadata)} · 正文 ${label(state.capture.body)}`
        + ` · 读取：${state.canReadBody ? "含正文" : "仅元数据"}`
        + (quota ? ` · 正文库 ${(quota.body_dir_bytes / 1048576).toFixed(1)} / ${(quota.limit_bytes / 1048576).toFixed(0)} MiB`
          + (Number.isFinite(quota.retention_days) ? ` · 保留 ${quota.retention_days} 天` : "") : "")
        + (quota?.requests ? ` · 滚动留存 ${quota.requests.used ?? "?"}/${quota.requests.limit} 个请求` : "")
        + (state.canReadBody && !state.fullCall ? " · 完整上下文需要新版 Alice" : ""));
      const data = await window.ContextInspectorClient.get("requests");
      state.requests = data.requests || [];
      const selected = state.requests.find(item => item.request_id === state.requestId) || state.requests[0];
      state.requestId = selected?.request_id ?? null;
      if (!selected?.calls.some(call => call.call_id === state.callId))
        state.callId = selected?.calls.at(-1)?.call_id ?? null;
      const signature = JSON.stringify([state.canReadBody, state.fullCall, data.scope,
        state.requests.map(r => [r.request_id, r.event_count, r.finished, r.calls.length])]);
      if (signature !== lastSignature) {
        lastSignature = signature;
        renderAll();
        if (!state.requests.length) el("ci-requestbar").append(node("p", "暂无上下文请求。", "ci-hint"));
      }
    } catch (error) {
      status(error.message, "warn");
    } finally {
      refreshing = false;
    }
  }

  function setMode(mode) {
    state.mode = mode;
    el("ci-mode-total").classList.toggle("active", mode === "total");
    el("ci-mode-delta").classList.toggle("active", mode === "delta");
    const request = state.requests.find((r) => r.request_id === state.requestId) || null;
    renderTrend(request);
  }

  el("ci-mode-total").addEventListener("click", () => setMode("total"));
  el("ci-full-context").addEventListener("click", () => {
    const request = state.requests.find(r => r.request_id === state.requestId);
    if (!request) return;
    window.AliceContextViewer.open({requestId: request.request_id, calls: request.calls,
      callId: state.callId, scope: window.ContextInspectorClient.getScope()});
  });
  el("ci-mode-delta").addEventListener("click", () => setMode("delta"));

  el("ci-refresh").addEventListener("click", tick);
  window.addEventListener("alice-observer-scope-changed", () => {
    state.requests = [];state.requestId = state.callId = null;lastSignature = "";
    renderAll();
  });
  const timer = setInterval(tick, 3500);
  window.addEventListener("pagehide", () => clearInterval(timer), {once:true});
  tick();
})();
