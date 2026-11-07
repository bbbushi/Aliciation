/* Read-only developer observer. No storage, model calls, or conversation writes. */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const layers = { active: "活跃", warehouse: "仓库", longterm: "长期" };
  const labels = {
    id: "稳定 ID", title: "标题", layer: "层级", kind: "类型", created_at: "记录时间 created_at",
    salience: "重要性（存储值）", pressure: "压力标记（存储值）", mention_count: "提及次数", use_count: "使用次数",
    last_used_at: "最近使用", promoted_at: "晋升时间", keywords: "关键词", source_titles: "出处标题（未验证）",
    filename: "快照文件", mtime: "文件修改时间", version: "当前版本计数", seeded_at: "人格安装时间",
    truncated: "元信息有截断", memory_id: "原记忆 ID", expired_at: "实际删除时间", reason: "存储原因",
    origin: "来源资格", scope: "允许使用的渠道", expires_at: "证据期限", source_packet_ids: "来源包 ID",
  };
  const times = new Set(["created_at", "last_used_at", "promoted_at", "seeded_at", "mtime", "expired_at", "expires_at"]);
  let opened = false, stopped = false, running = false, generation = 0;
  let timer = null, controller = null, scope = null, snapshot = null, personaChange = null;
  let pending = { snapshot: false, list: false, detail: false }, changes = [], firstTime = null;
  let listRevision = 0, detailRevision = 0, before = null, ceiling = null, nextBefore = null;
  let listSignature = "", detail = null, nextOffset = null;
  let query = "", interrupted = false;
  let personaFrame = null, personaRenderGeneration = 0, detailReadingSignature = null;
  let personaFolds = new Map();
  const personaStandard = new Map([
    ["底色 · 气质与风格", "（这里写 3~6 条气质与风格要点）"],
    ["生长 · 我的来历", "（尚未消化——导入文档的来历将写在这里）"],
    ["生长 · 自我叙事", "（尚未形成——将随共同经历自然生长）"],
    ["生长 · 我现在的想法", "（尚未形成——将在思辨中从经历里长出观点）"],
    ["生长 · 与用户的关系", "（尚未形成）"],
  ]);
  // Retired by the simplified expression layer; recognize old templates only
  // when present, without reporting the removed section as missing.
  const legacyPersonaTemplates = new Map([
    ["底色 · 情绪词表", "（8~15 个从标签派生的情绪词，【】格式）\n\n（使用注记一行）"],
  ]);
  const view = () => el("obs-view").value;
  const isList = () => ["memories", "tombstones", "skills", "history"].includes(view());
  const time = (ts) => ts ? new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false }) : "未记录";
  const text = (id, value) => { const n = el(id); if (n.textContent !== String(value)) n.textContent = value; };
  function node(tag, value, cls) {
    const n = document.createElement(tag);
    if (value !== undefined) n.textContent = value;
    if (cls) n.className = cls;
    return n;
  }
  function status(message, kind = "") {
    text("obs-status", message);
    el("obs-status").className = "obs-status" + (kind ? ` ${kind}` : "");
  }
  function sampleLabel() {
    text("obs-time", snapshot
      ? `最后有效快照：${time(snapshot.sampled_at)} · ${el("obs-auto").checked ? "自动刷新开启" : "自动刷新暂停"}`
      : "尚无有效快照；打开观察器不会触发记忆提取。");
  }
  function clearDetail() {
    detail = null; nextOffset = null; detailRevision++; detailReadingSignature = null;
    el("obs-detail").classList.add("hidden");
    for (const id of ["obs-detail-title", "obs-detail-time", "obs-detail-meta", "obs-detail-text", "obs-detail-reading"]) text(id, "");
    el("obs-detail-mode").value = "reading";
    el("obs-detail-reading-tools").classList.add("hidden");
    setDetailMode();
    el("obs-detail-next").disabled = true;
  }
  function clearData() {
    snapshot = null; personaChange = null; changes = []; firstTime = null; interrupted = false;
    before = null; ceiling = null; nextBefore = null; listSignature = ""; listRevision++;
    clearDetail();
    for (const id of ["obs-time", "obs-counts", "obs-coverage", "obs-progress", "obs-changes", "obs-list", "obs-list-time",
      "obs-persona-meta", "obs-persona-text", "obs-persona-toc", "obs-persona-sections", "obs-persona-reading-note", "obs-persona-diff-meta", "obs-persona-diff", "obs-persona-compare", "obs-changelog"]) text(id, "");
    personaFrame = null; personaRenderGeneration++; personaFolds.clear();
    el("obs-persona-advanced").open = false; el("obs-persona-source").open = false;
    el("obs-next").disabled = true;
  }
  function schedule() {
    clearTimeout(timer);
    if (!opened || stopped || !el("obs-auto").checked) return;
    timer = setTimeout(() => {
      pending.snapshot = true;
      pump();
      schedule();
    }, 5000);
  }
  function requestSnapshot() {
    if (!opened || stopped) return;
    pending.snapshot = true;
    pump();
  }
  async function request(path, params, epoch, isCurrent = () => true) {
    const url = new URLSearchParams(params);
    if (scope) url.set("scope", scope);
    controller = new AbortController();
    const ownController = controller;
    const timeout = setTimeout(() => ownController.abort(), 12000);
    try {
      const r = await fetch(`/api/observer/${path}?${url}`, {
        headers: { "X-Alice-Intent": "observer" }, cache: "no-store", signal: ownController.signal,
      });
      const d = await r.json().catch(() => ({}));
      if (epoch !== generation || !opened) return null;
      if (!r.ok) {
        if (!isCurrent()) return null;
        const message = r.status === 503 ? "服务正在退出；最后快照已过时，重新连接后再确认。"
          : r.status === 404 ? "观察接口不存在；可能仍在运行旧服务，请应用新代码后重新打开。"
          : `读取失败（HTTP ${r.status}）；最后快照已保留。`;
        throw new Error(message);
      }
      if (d.scope && scope && d.scope !== scope || d.status === "reset") {
        clearData(); scope = d.scope || null;
        pending = { snapshot: true, list: false, detail: false };
        status("服务或会话身份已变化，旧观察记录已清空，正在建立新基线。", "waiting");
        return null;
      }
      if (d.scope) scope = d.scope;
      if (!isCurrent()) return null;
      if (d.status === "busy") {
        status(snapshot ? "会话忙碌，等待锁空闲；继续显示最后有效快照。" : "会话忙碌，等待首次有效快照。", "waiting");
        return null;
      }
      if (d.status === "error") {
        const error = new Error(typeof d.error === "string" ? d.error : "读取未完成；数据状态未知。");
        error.clearDetail = path === "detail";
        throw error;
      }
      if (d.status !== "ok" || !d.scope) throw new Error("观察接口响应不完整；保留最后有效快照。");
      return d;
    } catch (error) {
      if (epoch !== generation || !opened || !isCurrent()) return null;
      // Only this still-selected record may be cleared; old failures cannot erase a new selection.
      if (error.clearDetail) clearDetail();
      throw error;
    } finally {
      clearTimeout(timeout);
      if (controller === ownController) controller = null;
    }
  }
  async function pump() {
    if (!opened || stopped || running) return;
    running = true;
    const epoch = generation;
    try {
      while (opened && epoch === generation && !stopped) {
        if (view() === "traces") {
          await renderTraces(epoch);
          await new Promise((resolve) => setTimeout(resolve, 4000));
          continue;
        }
        if (pending.snapshot) {
          pending.snapshot = false;
          status(snapshot ? "正在读取；最后有效快照仍可查看。" : "正在读取首次快照…");
          const d = await request("snapshot", {}, epoch);
          if (d) {
            acceptSnapshot(d);
            if (isList()) pending.list = true;
            if (detail && (detail.dirty || detail.view === "persona"
              || detail.view === "memories" && d.manifest.items.some((m) => m.id === Number(detail.id)))) pending.detail = true;
          }
        } else if (pending.list && isList()) {
          pending.list = false;
          const revision = listRevision;
          const requestedView = view();
          const params = { view: requestedView };
          if (requestedView !== "history") params.q = query;
          if (requestedView === "memories") {
            params.layer = el("obs-layer").value; params.kind = el("obs-kind").value;
          }
          if (before !== null) { params.before = before; params.ceiling = ceiling; }
          const d = await request("list", params, epoch, () => revision === listRevision && requestedView === view());
          if (d && revision === listRevision && requestedView === view()) renderList(d, requestedView);
        } else if (pending.detail && detail) {
          pending.detail = false;
          const revision = detailRevision, selected = { ...detail };
          const params = { view: selected.view, offset: selected.offset };
          if (selected.id !== undefined) params.id = selected.id;
          const d = await request("detail", params, epoch, () => revision === detailRevision && !!detail);
          if (d && revision === detailRevision && detail) renderDetail(d, selected);
        } else break;
      }
    } catch (e) {
      if (opened && epoch === generation) {
        interrupted = true;
        // Keep newer manual list/detail choices queued; discard only accumulated automatic ticks.
        pending.snapshot = false;
        status(e.name === "AbortError" ? "读取超时或连接中断；最后快照已保留，连接状态未知。" : e.message, "error");
      }
    } finally {
      running = false;
      // A close/reopen can happen before the old fetch settles.
      if (opened && !stopped && (pending.snapshot || pending.list && isList() || pending.detail && detail)) pump();
    }
  }
  function addChange(message, from, to) { changes.push({ message, from, to }); }

  // ---- 调用轨迹（SDK 观测投影）----
  const stateLabels = { entered: "已进入调用", ok: "收到响应", error: "失败", cancelled: "已取消", stream_interrupted: "流中断" };
  const purposeLabels = { main: "主对话", main_stream: "主对话·流式", main_fallback: "主对话·回落",
    recap_generate: "前情生成", recap_review: "前情审查", rewrite_review: "改写审查", recap_reply_review: "前情回复审查" };
  let tracesFetchedAt = 0, tracesSignature = "";

  async function renderTraces(epoch) {
    const now = Date.now();
    if (now - tracesFetchedAt < 3900) { await new Promise((r) => setTimeout(r, 1200)); return; }
    tracesFetchedAt = now;
    const d = await request("requests", { limit: "12" }, epoch);
    if (!d) return;
    const list = el("obs-traces-list");
    const signature = JSON.stringify((d.requests || []).map((r) => [r.request_id, r.event_count, r.finished]));
    if (signature === tracesSignature && list.childElementCount) return;
    tracesSignature = signature;
    list.replaceChildren();
    if (!d.requests || !d.requests.length) {
      list.append(node("p", "暂无请求轨迹；进行一次对话后出现。", "obs-hint"));
      return;
    }
    for (const request of d.requests) renderTraceRequest(list, request);
  }

  function renderTraceRequest(list, request) {
    const card = document.createElement("details");
    const head = node("summary", null, "obs-trace-head");
    const short = String(request.request_id || "?").slice(0, 8);
    head.append(node("strong", `请求 ${short}…`),
      node("span", ` ${request.event_count} 事件 · ${request.calls.length} 次调用 · ${request.finished ? "已结束" : "进行中"}`, "obs-hint"));
    card.append(head);
    const meta = node("p", null, "obs-hint");
    meta.textContent = `时间 ${time(request.first_event_at)} · 渠道 ${request.scope || "?"} · 代际 ${String(request.session_epoch || "?").slice(0, 8)}…`
      + (request.assistant_turn_id ? ` · 回复 #${request.assistant_turn_id}` : "")
      + (request.gaps && request.gaps.length ? ` · 观测缺口 ${request.gaps.length}` : "");
    card.append(meta);
    for (const call of request.calls) renderTraceCall(card, request, call);
    if (request.blocks && request.blocks.length) {
      const blocks = document.createElement("details");
      blocks.append(node("summary", `装配块 ${request.blocks.length} · 选择记录 ${request.selections.length}`));
      const table = node("div");
      for (const block of request.blocks) {
        const row = node("p", `#${block.seq} [${block.kind}] ${block.name} · ${block.chars ?? "?"} 字`, "obs-hint");
        row.append(bodyButton(block.content_hash, block.body_state));
        table.append(row);
      }
      if (request.selections && request.selections.length) {
        const shown = request.selections.filter((s) => !s.included).slice(0, 12);
        table.append(node("p", `未入选 ${request.selections.filter((s) => !s.included).length} 条（示前 12）：`
          + shown.map((s) => `${s.source_id}（${s.reason}）`).join("、"), "obs-hint"));
      }
      blocks.append(table);
      card.append(blocks);
    }
    list.append(card);
  }

  function renderTraceCall(card, request, call) {
    const wrap = document.createElement("details");
    const last = call.attempts[call.attempts.length - 1] || {};
    const head = node("summary", null, "obs-trace-head");
    head.append(node("strong", purposeLabels[call.purpose] || call.purpose || "调用"),
      node("span", ` · 轮${call.tool_round ?? 0} · ${stateLabels[last.status] || last.status || "无尝试"}`
        + (last.duration_ms != null ? ` · ${last.duration_ms}ms` : "")
        + (last.usage ? ` · 入${last.usage.prompt ?? "?"}/出${last.usage.completion ?? "?"}tok` : ""), "obs-hint"));
    wrap.append(head);
    const params = call.parameters || {};
    const covered = Object.entries(params.covered || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" · ");
    wrap.append(node("p", `参数：${covered || "未记录"}`
      + ((params.uncovered || []).length ? ` · 未覆盖：${params.uncovered.join("、")}` : ""), "obs-hint"));
    if (call.tools && call.tools.length) wrap.append(node("p", `工具：${call.tools.join("、")}`, "obs-hint"));
    const messages = (call.messages && call.messages.order) || [];
    const table = node("div");
    for (const entry of messages) {
      const row = node("p", `${entry.seq} ${entry.role} · ${entry.chars ?? "?"} 字`
        + (entry.tool_call_id ? ` · tool:${String(entry.tool_call_id).slice(0, 10)}…` : ""), "obs-hint");
      row.append(bodyButton(entry.content_hash, entry.body));
      table.append(row);
    }
    if (call.bindings && call.bindings.length) {
      const names = call.bindings.map((b) => `${b.name}→消息${b.message_seq}`).slice(0, 16).join("、");
      table.append(node("p", `块绑定：${names}${call.bindings.length > 16 ? " …" : ""}`, "obs-hint"));
    }
    wrap.append(table);
    if (request.reply_postprocess) {
      wrap.append(node("p", `回复处理：${request.reply_postprocess.kind} · ${JSON.stringify(request.reply_postprocess.detail || {})}`, "obs-hint"));
    }
    card.append(wrap);
  }

  function bodyButton(contentHash, bodyState) {
    if (!contentHash || bodyState === "none") return node("span", "（未留存正文）", "obs-hint");
    if (bodyState && bodyState !== "stored" && bodyState !== "head" && bodyState !== "on_disk") {
      return node("span", `（正文：${bodyState}）`, "obs-hint");
    }
    const wrap = document.createElement("span");
    const button = node("button", "看留存正文", "btn ghost");
    button.type = "button";
    const pre = node("pre", null, "obs-text");
    pre.hidden = true;
    button.addEventListener("click", async () => {
      if (!pre.hidden) { pre.hidden = true; return; }
      button.disabled = true;
      const d = await request("body", { hash: contentHash }, generation, () => true).catch(() => null);
      button.disabled = false;
      pre.textContent = !d ? "读取失败；稍后重试。"
        : d.stored ? (d.text || "（空）") + (d.truncated ? "\n…（已截断）" : "")
        : `未留存正文（${d.reason || "not_retained"}）；不能用当前数据补画历史。`;
      pre.hidden = false;
    });
    wrap.append(button, pre);
    return wrap;
  }

  function acceptSnapshot(d) {
    const old = snapshot;
    if (!firstTime) firstTime = d.sampled_at;
    if (old) {
      const prior = new Map(old.manifest.items.map((m) => [m.id, m]));
      const current = new Map(d.manifest.items.map((m) => [m.id, m]));
      const maxID = old.manifest.items.reduce((max, m) => Math.max(max, m.id), 0);
      for (const m of d.manifest.items) {
        const p = prior.get(m.id);
        const identity = `#${m.id} ${m.title}`;
        if (!p) {
          addChange(`${identity}：${m.id > maxID ? "新 ID 首次可见（不代表 created_at 是入库时刻）" : "进入本次跟踪范围，不能判定为新增"}`, old.sampled_at, d.sampled_at);
          continue;
        }
        const fields = [];
        for (const key of ["layer", "kind", "salience", "pressure", "mention_count", "use_count", "last_used_at", "promoted_at", "created_at"]) {
          if (p[key] !== m[key]) fields.push(`${labels[key]} ${format(key, p[key])} → ${format(key, m[key])}`);
        }
        if (p.text_hash !== m.text_hash) fields.push("标题 / 正文 / 关键词指纹变化" + (p.text_truncated || m.text_truncated ? "（仅截取范围与长度）" : ""));
        if (fields.length) addChange(`${identity}：${fields.join("；")}`, old.sampled_at, d.sampled_at);
      }
      for (const p of old.manifest.items) {
        if (!current.has(p.id)) addChange(`#${p.id} ${p.title}：离开存活跟踪范围；可能已删除或移出窗口，删除以墓碑为证。`, old.sampled_at, d.sampled_at);
      }
      if (old.persona.text !== d.persona.text) {
        personaChange = { before: old.persona.text, after: d.persona.text, from: old.sampled_at, to: d.sampled_at,
          truncated: old.persona.truncated || d.persona.truncated };
        addChange("人格原文发生变化（底色 / 生长 / 思想的原文对照见人格页）", old.sampled_at, d.sampled_at);
        renderPersonaDiff();
      }
      if (old.persona.version !== d.persona.version) addChange(`人格版本计数 ${old.persona.version} → ${d.persona.version}`, old.sampled_at, d.sampled_at);
    }
    changes = changes.slice(-100);
    // Newly confirmed absence clears stale memory bodies, including a held older list page.
    const alive = new Set(d.manifest.items.map((m) => m.id));
    const floor = d.manifest.items.reduce((min, m) => Math.min(min, m.id), Infinity);
    const absent = (id) => !alive.has(id) && (d.manifest.complete || id >= floor);
    for (const card of el("obs-list").querySelectorAll("[data-memory-id]")) {
      if (absent(Number(card.dataset.memoryId))) {
        card.replaceChildren(node("p", `#${card.dataset.memoryId} 已不在此次存活记忆范围；旧预览已清除，实际删除记录请查墓碑。`, "obs-hint"));
        delete card.dataset.memoryId; listSignature = "";
      }
    }
    if (detail?.view === "memories" && absent(Number(detail.id))) clearDetail();
    snapshot = d;
    renderOverview(d);
    renderPersona(d);
    renderChanges();
    sampleLabel();
    status(interrupted ? "已重新取得有效快照；断线期间仅能比较两端净差异。" : old ? "快照已更新；未出现差异不代表期间没有短暂变化。" : "已建立观察基线；此前的变化未在本次观察中记录。");
    interrupted = false;
  }
  function format(key, value) {
    if (times.has(key)) return time(value);
    if (value === undefined || value === null) return "未记录";
    if (key === "layer") return layers[value] || value;
    if (key === "kind") return value === "stance" ? "观点" : value === "event" ? "事件" : value;
    if (key === "origin") return ({ conversation: "普通对话", external: "外部见闻", external_derived: "外部派生" })[value] || value;
    if (key === "source_packet_ids") return Array.isArray(value) && value.length ? value.join("、") : "无外部来源包";
    if (key === "scope" && value === "[]") return "沿用普通对话范围";
    return String(value);
  }
  function renderOverview(d) {
    const m = d.memory_counts, p = d.progress, x = p.extraction;
    text("obs-counts", `原始状态 · 存活记忆条数：活跃 ${m.active} · 仓库 ${m.warehouse} · 长期 ${m.longterm}`);
    text("obs-coverage", `差异覆盖 ${d.manifest.items.length} / ${d.manifest.total} 条存活记忆（最近 ID，上限 ${d.manifest.limit}）。${d.manifest.scope_limited ? `其中 ${d.manifest.visible_total} 条目前允许在GUI查看；其他条目不提供正文。` : d.manifest.complete ? "当前全部存活 ID 在范围内。" : "范围不完整；未跟踪条目仍可在记忆页检索。"} ${d.manifest.items.some((r) => r.text_truncated) ? "包含超长字段，仅比较前 16000 字与长度。" : ""}`);
    const container = el("obs-progress"); container.replaceChildren();
    const items = [
      `记忆提取：游标 ${x.cursor} 后待处理 ${x.pending_messages} 条消息；实际检查的前 ${x.batch_messages} 条中有 ${x.batch_user_messages} 条用户消息，阈值 ${x.threshold}。${x.batch_user_messages >= x.threshold ? "当前满足数量条件；不保证提取成功或产出新记忆。" : "当前批次未达到数量门槛。"}`,
      "对话里说“记住了”不等于已入库；候选提取没有持久记录，任务阶段与失败原因未知。退出前另有强制提取。",
    ];
    for (const [key, name] of [["evolution", "人格演化"], ["deliberation", "思想思辨"], ["distillation", "技能提炼"]]) {
      const r = p[key];
      items.push(`${name}：锚点 ${time(r.anchor)} 之后现存${key === "deliberation" ? "事件类" : ""}记忆 ${r.count} / 阈值 ${r.threshold}。${r.count >= r.threshold ? "当前满足数量条件；不保证执行或正文变化。" : "尚未达到数量门槛。"}`);
    }
    items.push(`首次思辨${p.deliberation.uses_evolution_anchor ? "正在退用" : "当前未退用"}演化锚点。服务先演化再思辨，首次思辨进度可能被重置；观察器不改变这个调度。演化最多取 100 条、思辨最多 60 条，旧时间与遗忘也影响计数。`);
    const r = p.maintenance;
    items.push(`分层维护：上次 ${time(r.last_at)}；${r.due ? "已到一小时检查条件" : "未到一小时检查条件"}。服务仅启动 prepare 时检查，聊天 / 观察刷新不运行维护。规则：活跃 ${r.active_days} 天，存活线 ${r.survival_days} 天，提及+使用门槛 ${r.survival_min_uses}，长期容量 ${r.longterm_capacity}；重要性与压力还影响晋升。`);
    items.forEach((v) => container.appendChild(node("p", v, "obs-hint")));
  }
  function renderChanges() {
    const target = el("obs-changes"); target.replaceChildren();
    if (!changes.length) { target.appendChild(node("li", `自 ${time(firstTime)} 尚未观察到净差异。`)); return; }
    [...changes].reverse().forEach((c) => {
      const row = node("li"); row.append(node("div", c.message), node("small", `${time(c.from)} → ${time(c.to)}（观测区间）`));
      target.appendChild(row);
    });
  }
  // A bounded Markdown reader. Source text never becomes HTML, URLs or CSS.
  function mdFence(line) {
    const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!m || m[1][0] === "`" && m[2].includes("`")) return null;
    return { mark: m[1][0], length: m[1].length };
  }
  function mdFenceEnd(line, fence) {
    const m = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
    return !!m && m[1][0] === fence.mark && m[1].length >= fence.length;
  }
  function mdHeading(line) {
    const m = /^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$/.exec(line);
    if (!m) return null;
    let title = (m[2] || "").trimEnd(), end = title.length;
    while (end > 0 && title[end - 1] === "#") end--;
    if (end < title.length && (end === 0 || title[end - 1] === " " || title[end - 1] === "\t")) title = title.slice(0, end).trimEnd();
    return { level: m[1].length, title: title.trimStart() };
  }
  function personaSections(source) {
    // Keep exact source slices for excerpts and source inspection, including line endings.
    const rawLines = source.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g) || [], sections = [];
    let current = { title: null, lines: [], start: 0, bodyStart: 0 }, fence = null, offset = 0;
    const finish = (end) => {
      if (current.lines.some((s) => s.trim()) || current.title !== null) {
        current.raw = source.slice(current.start, end); current.body = source.slice(current.bodyStart, end);
        sections.push(current);
      }
    };
    for (let i = 0; i < rawLines.length; i++) {
      const raw = rawLines[i], line = raw.replace(/[\r\n]+$/, ""), heading = !fence && mdHeading(line);
      if (heading && heading.level === 2) {
        if (sections.length >= 199) {
          finish(offset);
          const remainder = source.slice(offset);
          sections.push({ title: null, lines: remainder.replace(/\r\n?/g, "\n").split("\n"), raw: remainder, body: remainder, literal: true });
          return sections;
        }
        finish(offset);
        current = { title: heading.title, lines: [], start: offset, bodyStart: offset + raw.length };
      } else current.lines.push(line);
      if (fence) { if (mdFenceEnd(line, fence)) fence = null; }
      else fence = mdFence(line);
      offset += raw.length;
    }
    finish(source.length);
    return sections;
  }
  function mdElement(tag, value, budget) {
    budget.nodes--;
    return node(tag, value);
  }
  function mdLiteral(target, source, budget, pre = false) {
    budget.nodes--; budget.fallback = true;
    target.appendChild(node(pre ? "pre" : "span", source, pre ? "obs-md-fallback" : undefined));
  }
  function mdInline(target, source, budget, depth = 0) {
    if (source.length > 8000 || depth >= 4 || budget.nodes < 2 || budget.steps <= 0) {
      mdLiteral(target, source, budget); return;
    }
    const tokens = /\\[\\`*_\[\]{}()#+.!>|~-]|`+|\*\*\*|___|\*\*|__|\*|_/g;
    let cursor = 0, match;
    const plain = (s) => { if (s) { target.appendChild(document.createTextNode(s)); budget.nodes--; } };
    while ((match = tokens.exec(source))) {
      if (--budget.steps <= 0 || budget.nodes < 3) { mdLiteral(target, source.slice(cursor), budget); return; }
      const token = match[0], start = match.index;
      if (token[0] === "\\") {
        plain(source.slice(cursor, start)); plain(token.slice(1)); cursor = tokens.lastIndex; continue;
      }
      const from = start + token.length, end = source.indexOf(token, from);
      if (end < 0 || end === from) continue;
      const contents = source.slice(from, end), code = token[0] === "`";
      if (!code && (!contents.trim() || token[0] === "_" && /[\p{L}\p{N}]/u.test(source[start - 1] || ""))) continue;
      // A longer backtick run is a different delimiter; unknown forms remain source text.
      if (code && (source[end - 1] === "`" || source[end + token.length] === "`")) continue;
      plain(source.slice(cursor, start));
      const formatted = mdElement(code ? "code" : token.length >= 2 ? "strong" : "em", undefined, budget);
      if (code) formatted.textContent = contents;
      else if (token.length === 3) {
        const emphasis = mdElement("em", undefined, budget); mdInline(emphasis, contents, budget, depth + 1); formatted.appendChild(emphasis);
      } else mdInline(formatted, contents, budget, depth + 1);
      target.appendChild(formatted); cursor = end + token.length; tokens.lastIndex = cursor;
    }
    plain(source.slice(cursor));
  }
  function mdTableCells(line) {
    if (!line.includes("|") || /[`\\]/.test(line)) return null;
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((s) => s.trim());
  }
  function mdListItem(line) {
    const m = /^( {0,3})([-+*]|\d{1,9}[.)])[ \t]+(.*)$/.exec(line);
    return m ? { indent: m[1].length, ordered: /^\d/.test(m[2]), number: parseInt(m[2], 10), body: m[3] } : null;
  }
  function mdBlocks(lines, target, budget, depth = 0) {
    let i = 0;
    while (i < lines.length) {
      if (budget.nodes < 8 || budget.steps <= 0 || depth >= 5) { mdLiteral(target, lines.slice(i).join("\n"), budget, true); return; }
      if (!lines[i].trim()) { i++; continue; }
      const fence = mdFence(lines[i]);
      if (fence) {
        const content = []; i++;
        while (i < lines.length && !mdFenceEnd(lines[i], fence)) content.push(lines[i++]);
        if (i < lines.length) i++;
        const pre = mdElement("pre", undefined, budget), code = mdElement("code", content.join("\n"), budget);
        pre.appendChild(code); target.appendChild(pre); continue;
      }
      const heading = mdHeading(lines[i]);
      if (heading) {
        const h = mdElement(`h${Math.min(heading.level + 1, 6)}`, undefined, budget);
        mdInline(h, heading.title, budget); target.appendChild(h); i++; continue;
      }
      const header = mdTableCells(lines[i]), divider = i + 1 < lines.length && mdTableCells(lines[i + 1]);
      if (header && divider && header.length === divider.length && divider.every((s) => /^:?-{3,}:?$/.test(s))) {
        let end = i + 2;
        while (end < lines.length && mdTableCells(lines[end])) end++;
        if (header.length > 16 || end - i > 100 || (end - i) * header.length * 3 > budget.nodes) {
          mdLiteral(target, lines.slice(i, end).join("\n"), budget, true); i = end; continue;
        }
        const wrap = mdElement("div", undefined, budget); wrap.className = "obs-md-table";
        const table = mdElement("table", undefined, budget), thead = mdElement("thead", undefined, budget), row = mdElement("tr", undefined, budget);
        for (const value of header) { const cell = mdElement("th", undefined, budget); cell.scope = "col"; mdInline(cell, value, budget); row.appendChild(cell); }
        thead.appendChild(row); table.appendChild(thead);
        const tbody = mdElement("tbody", undefined, budget);
        for (let n = i + 2; n < end; n++) {
          const cells = mdTableCells(lines[n]);
          // Keep malformed rows intact instead of dropping surplus cells.
          if (cells.length !== header.length) { const tr = mdElement("tr", undefined, budget), td = mdElement("td", lines[n], budget); td.colSpan = header.length; tr.appendChild(td); tbody.appendChild(tr); continue; }
          const tr = mdElement("tr", undefined, budget);
          for (const value of cells) { const cell = mdElement("td", undefined, budget); mdInline(cell, value, budget); tr.appendChild(cell); }
          tbody.appendChild(tr);
        }
        table.appendChild(tbody); wrap.appendChild(table); target.appendChild(wrap); i = end; continue;
      }
      if (/^ {0,3}>/.test(lines[i])) {
        const quoted = [];
        while (i < lines.length && /^ {0,3}>/.test(lines[i])) quoted.push(lines[i++].replace(/^ {0,3}> ?/, ""));
        const quote = mdElement("blockquote", undefined, budget); mdBlocks(quoted, quote, budget, depth + 1); target.appendChild(quote); continue;
      }
      const firstItem = mdListItem(lines[i]);
      if (firstItem) {
        const list = mdElement(firstItem.ordered ? "ol" : "ul", undefined, budget);
        if (firstItem.ordered) list.start = firstItem.number;
        while (i < lines.length) {
          const item = mdListItem(lines[i]);
          if (!item || item.ordered !== firstItem.ordered || item.indent !== firstItem.indent || budget.nodes < 8) break;
          const li = mdElement("li", undefined, budget); mdInline(li, item.body, budget); i++;
          const nested = [];
          while (i < lines.length && lines[i].trim() && lines[i].startsWith(" ".repeat(firstItem.indent + 2))) nested.push(lines[i++].slice(firstItem.indent + 2));
          if (nested.length) mdBlocks(nested, li, budget, depth + 1);
          list.appendChild(li);
        }
        target.appendChild(list); continue;
      }
      const paragraph = [lines[i++]];
      while (i < lines.length && lines[i].trim() && !mdFence(lines[i]) && !mdHeading(lines[i]) && !mdListItem(lines[i]) && !/^ {0,3}>/.test(lines[i])) {
        const nextDivider = i + 1 < lines.length && mdTableCells(lines[i + 1]);
        if (mdTableCells(lines[i]) && nextDivider && nextDivider.every((s) => /^:?-{3,}:?$/.test(s))) break;
        paragraph.push(lines[i++]);
      }
      const p = mdElement("p", undefined, budget); mdInline(p, paragraph.join("\n"), budget); target.appendChild(p);
    }
  }
  function sectionLabel(section) {
    return section.literal ? "其余原文（分块上限）" : section.title === null ? "文档开头" : section.title || "空标题（原文）";
  }
  function sectionStatus(section) {
    if (section.missing) return section.complete ? "未见标准标题" : "未知 · 本次范围未见";
    if (!section.complete) return "未知 · 本节可能不完整";
    const body = section.body.replace(/\r\n?/g, "\n").trim();
    if (!body) return "空白";
    if ((personaStandard.has(section.title) && body === personaStandard.get(section.title))
      || (legacyPersonaTemplates.has(section.title) && body === legacyPersonaTemplates.get(section.title))) return "仅占位 · 模板原文";
    return "已有内容 · 有原文";
  }
  function sectionExcerpt(card, section, budget) {
    const content = section.body.trim(), chars = Array.from(content);
    if (!chars.length) {
      card.appendChild(node("p", section.missing ? "本次没有可摘录的原文。" : "本节没有非空白正文可摘录。", "obs-hint"));
      return;
    }
    const excerpt = node("blockquote", undefined, "obs-persona-excerpt");
    mdInline(excerpt, chars.slice(0, 180).join(""), budget);
    card.append(node("p", `原文摘录 · 已排版${chars.length > 180 ? " · 前 180 字符，后文已省略" : " · 本节可见正文"}`, "obs-excerpt-label"), excerpt);
  }
  function sectionContent(section, budget, open = false) {
    const content = node("details", undefined, "obs-persona-content"); content.open = open;
    content.appendChild(node("summary", "查看本节内容"));
    const body = node("div", undefined, "obs-markdown");
    if (section.literal || budget.nodes < 8) mdLiteral(body, section.body, budget, true);
    else mdBlocks(section.lines, body, budget);
    if (!section.body.trim()) body.appendChild(node("p", "（空白正文）", "obs-hint"));
    const source = node("details", undefined, "obs-section-source");
    source.append(node("summary", "核对本节 Markdown 原文"), node("pre", section.raw, "obs-text"));
    content.append(body, source);
    return content;
  }
  function sectionChange(section, previous, sampledAt, ambiguous, complete) {
    if (ambiguous) return "同名多节，无法唯一对应前后正文；不推断逐节变化。";
    if (!complete || !section.complete) return "本次范围不完整，无法确认逐节对应或变化。";
    if (!previous) return `观察基线 · ${time(sampledAt)}；本次观察尚未见变化，此前未知。`;
    if (!previous.complete) return `前次范围不完整；${time(sampledAt)} 重新建立比较基线，不推断新增或变化。`;
    const prior = previous.records.get(section.title);
    if (prior?.ambiguous || prior && !prior.complete) return `前次无法唯一、完整对应；${time(sampledAt)} 重新建立比较基线。`;
    if (prior && prior.missing === section.missing && prior.raw === section.raw) return prior.change;
    const interval = `${time(previous.sampledAt)} → ${time(sampledAt)}（观测区间）`;
    if (section.missing) return `上次可见、本次未见标准标题 · ${interval}；可能改名，不能推断删除。`;
    if (!prior || prior.missing) return `本次首次看到此标题 · ${interval}；不代表内容创建时间。`;
    return `观察到原文变化 · ${interval}；实际更新时间未知。`;
  }
  function renderPersonaReading(source, truncated, sampledAt) {
    if (personaFrame && source === personaFrame.source && truncated === personaFrame.truncated) {
      personaFrame.sampledAt = sampledAt;
      return;
    }
    const previous = personaFrame, revision = ++personaRenderGeneration;
    const sectionNodes = el("obs-persona-sections"), toc = el("obs-persona-toc");
    sectionNodes.replaceChildren(); toc.replaceChildren();
    const sections = personaSections(source), total = new Map(), counts = new Map(), nextFolds = new Map(), records = new Map();
    const complete = !truncated && !sections.some((s) => s.literal);
    sections.forEach((s, index) => {
      s.complete = !s.literal && !(truncated && index === sections.length - 1);
      total.set(s.title, (total.get(s.title) || 0) + 1);
    });
    for (const title of personaStandard.keys()) {
      if (!total.has(title)) sections.push({ title, body: "", raw: "", lines: [], missing: true, complete });
    }
    const budget = { nodes: 4000, steps: 4000, fallback: false };
    if (!source.trim()) sectionNodes.appendChild(node("p", truncated ? "可见范围内没有正文；文件完整状态未知。" : "本次未读到非空白人格正文；可能尚未建立或为空白。", "obs-hint"));
    sections.forEach((section, index) => {
      const label = sectionLabel(section), keyBase = JSON.stringify([section.title, !!section.literal]);
      const count = (counts.get(keyBase) || 0) + 1; counts.set(keyBase, count);
      const key = `${keyBase}:${count}`, card = node("article", undefined, "obs-persona-section");
      const heading = node("h4"); mdInline(heading, label, budget);
      card.append(heading, node("p", sectionStatus(section), "obs-persona-state"));
      if (section.missing) card.appendChild(node("p", section.complete
        ? "完整快照中未找到这个标准标题；内容可能写在其他标题下。"
        : "快照被截断或分块达到上限；不能判断文件是否包含这个部分。", "obs-hint"));
      else if (!section.complete) card.appendChild(node("p", section.literal ? "章节较多，余下原文合并保留；不推断其中各部分状态。" : "本节尾部被快照上限截断；只展示可见原文。", "obs-hint"));
      else if (section.title !== null && !personaStandard.has(section.title)) card.appendChild(node("p", "自定义小节 · 沿用原文标题", "obs-hint"));
      section.ambiguous = (total.get(section.title) || 0) > 1;
      section.change = sectionChange(section, previous, sampledAt, section.ambiguous, complete);
      card.appendChild(node("p", section.change, "obs-persona-change"));
      sectionExcerpt(card, section, budget);
      if (!section.missing) {
        const content = sectionContent(section, budget, personaFolds.get(key) || false);
        content.dataset.personaKey = key; nextFolds.set(key, content.open);
        content.addEventListener("toggle", () => {
          if (revision === personaRenderGeneration && content.isConnected) personaFolds.set(key, content.open);
        });
        card.appendChild(content);
      }
      records.set(section.title, section);
      const jump = node("button", `${index + 1}. ${label}`, "btn ghost"); jump.type = "button";
      jump.onclick = () => card.scrollIntoView({ block: "nearest" });
      toc.appendChild(jump); sectionNodes.appendChild(card);
    });
    personaFolds = nextFolds;
    personaFrame = { source, truncated, sampledAt, complete, records };
    text("obs-persona-reading-note", "卡片按实际二级标题顺序展示；末尾补充未见的标准标题，同名小节分别保留。" +
      (budget.fallback ? "部分复杂排版在展开后按原文保留；无文字被丢弃。" : "每卡可展开排版内容或逐字核对原文。"));
  }
  function renderPersona(d) {
    text("obs-persona-meta", `最近核对 ${time(d.sampled_at)} · 全局版本计数 ${d.persona.version} · 全局安装时间 ${time(d.persona.seeded_at)} · ${d.persona.truncated ? "仅前 64000 字符，尾部状态未知" : "本次快照原文完整"}；这些不是各节更新时间。`);
    text("obs-persona-text", d.persona.text || "尚无人格正文");
    renderPersonaReading(d.persona.text || "", !!d.persona.truncated, d.sampled_at);
    text("obs-changelog", (d.changelog.truncated ? "（仅显示记录尾部，历史可能被截断）\n" : "") + (d.changelog.text || "无已有变更记录"));
    if (!personaChange) text("obs-persona-diff-meta", "尚未观察到人格正文变化；第一次快照只建立基线。");
  }
  function renderPersonaDiff() {
    const c = personaChange;
    text("obs-persona-diff-meta", `${time(c.from)} → ${time(c.to)}；按最长共同前缀与后缀标出中间变化片段，可能含未变的中间行。${c.truncated ? "原文有截断，仅比较可见部分。" : ""}`);
    const beforeChars = Array.from(c.before), afterChars = Array.from(c.after);
    let start = 0, suffix = 0;
    while (start < beforeChars.length && start < afterChars.length && beforeChars[start] === afterChars[start]) start++;
    while (suffix < beforeChars.length - start && suffix < afterChars.length - start
      && beforeChars[beforeChars.length - suffix - 1] === afterChars[afterChars.length - suffix - 1]) suffix++;
    const diff = el("obs-persona-diff"); diff.replaceChildren();
    diff.append(node("p", "删除的片段"), node("pre", beforeChars.slice(start, beforeChars.length - suffix).join("") || "（空）", "obs-text obs-removed"),
      node("p", "增加的片段"), node("pre", afterChars.slice(start, afterChars.length - suffix).join("") || "（空）", "obs-text obs-added"));
    const compare = el("obs-persona-compare"); compare.replaceChildren();
    compare.append(node("p", "变更前"), node("pre", c.before, "obs-text"), node("p", "变更后"), node("pre", c.after, "obs-text"));
  }
  function renderList(d, type) {
    status("列表读取成功；整体状态仍以上方最后有效快照时间为准。");
    if (type === "memories" && detail?.view === "memories"
      && !d.items.some((m) => m.id === Number(detail.id))) clearDetail();
    ceiling = d.ceiling; nextBefore = d.next_before ?? null;
    el("obs-next").disabled = nextBefore === null;
    text("obs-list-time", `列表采样：${time(d.sampled_at)} · 本页 ${d.items.length} 条${before !== null ? " · 翻页固定 ID 上界；新条目请回首页" : ""}`);
    const sig = JSON.stringify([type, d.items]);
    if (sig === listSignature) return;
    listSignature = sig;
    const container = el("obs-list"); container.replaceChildren();
    if (!d.items.length) container.appendChild(node("p", "此筛选下没有条目。", "obs-hint"));
    d.items.forEach((r) => {
      const card = node("article", undefined, "obs-record");
      if (type === "memories") card.dataset.memoryId = r.id;
      if (type === "history") {
        card.append(node("strong", r.filename), node("p", `文件修改时间：${time(r.mtime)}`, "obs-hint"));
      } else if (type === "tombstones") {
        card.append(node("strong", `墓碑 #${r.id} · 原记忆 #${r.memory_id} ${r.title}`), node("p", `实际删除：${time(r.expired_at)}`, "obs-hint"), node("p", r.reason));
        if (r.truncated) card.appendChild(node("p", "此记录的长字段已截断；不存在可恢复的记忆正文。", "obs-hint"));
      } else {
        card.append(node("strong", `#${r.id} ${r.title}`), node("p", r.preview || "（空正文）", "obs-preview"));
        card.appendChild(node("p", type === "memories"
          ? `${format("layer", r.layer)} · ${format("kind", r.kind)} · 重要性存储值 ${r.salience} · 压力存储值 ${r.pressure} · 提及 ${r.mention_count} · 使用 ${r.use_count}`
          : `使用 ${r.use_count} · 创建 ${time(r.created_at)}`, "obs-hint"));
      }
      if (type !== "tombstones") {
        const button = node("button", "查看详情", "btn ghost"); button.type = "button";
        button.onclick = () => openDetail(type, type === "history" ? r.filename : r.id);
        card.appendChild(button);
      }
      container.appendChild(card);
    });
  }
  function openDetail(type, id) {
    clearDetail();
    detail = { view: type, id, offset: 0, dirty: true };
    el("obs-detail").classList.remove("hidden");
    text("obs-detail-title", type === "persona" ? "当前人格文件 · 当前片段" : type === "history" ? `历史文件 · ${id} · 当前片段` : `详情 ${id}`);
    text("obs-detail-time", "正在读取…");
    setDetailMode();
    pending.detail = true; pump();
  }
  function setDetailMode() {
    const file = detail && ["persona", "history"].includes(detail.view);
    const reading = file && el("obs-detail-mode").value === "reading";
    el("obs-detail-reading-tools").classList.toggle("hidden", !file);
    el("obs-detail-reading").classList.toggle("hidden", !reading);
    el("obs-detail-text").classList.toggle("hidden", !!reading);
  }
  function renderDetailReading(d, selected) {
    const signature = JSON.stringify([selected.view, selected.id, d.offset, d.text]);
    if (signature === detailReadingSignature) return;
    detailReadingSignature = signature;
    const target = el("obs-detail-reading"); target.replaceChildren();
    target.appendChild(node("p", "只按当前片段可见结构分块，不代表完整章节或全文件状态。片段可能从代码、列表或标题中间开始；不跨段拼接，也不判断缺节、占位或变化。", "obs-hint"));
    const sections = personaSections(d.text), budget = { nodes: 4000, steps: 4000, fallback: false };
    if (!sections.length) target.appendChild(node("p", "当前片段为空白。", "obs-hint"));
    sections.forEach((section) => {
      const card = node("article", undefined, "obs-persona-section obs-persona-fragment");
      const heading = node("h4");
      mdInline(heading, section.title === null && !section.literal ? "片段开头 · 所属小节未知" : sectionLabel(section), budget);
      card.append(heading, node("p", "本段可见结构 · 完整性未知", "obs-persona-state"));
      sectionExcerpt(card, section, budget);
      const content = sectionContent(section, budget);
      content.querySelector("summary").textContent = "展开本段排版内容";
      content.querySelector(".obs-section-source > summary").textContent = "核对本块 Markdown 原文";
      card.appendChild(content); target.appendChild(card);
    });
    if (budget.fallback) target.appendChild(node("p", "复杂格式已按原文保留，可展开或切换片段源码核对。", "obs-hint"));
  }
  function renderDetail(d, selected) {
    detail.dirty = false;
    status("详情读取成功；各段以自己的采样时间为准。");
    nextOffset = d.next_offset ?? null;
    el("obs-detail-next").disabled = nextOffset === null;
    const chars = Array.from(d.text).length;
    text("obs-detail-time", `采样 ${time(d.sampled_at)} · 字符 ${chars ? d.offset + 1 : d.offset}–${d.offset + chars} / ${d.total_chars}（本段替换上一段）`);
    text("obs-detail-text", d.text || "（空正文）");
    if (["persona", "history"].includes(selected.view)) renderDetailReading(d, selected);
    setDetailMode();
    const meta = el("obs-detail-meta"); meta.replaceChildren();
    Object.entries(d.metadata || {}).forEach(([key, value]) => {
      if (!labels[key]) return;
      meta.append(node("dt", labels[key]), node("dd", format(key, value)));
    });
    if (selected.view === "memories") meta.append(node("dt", "来源链"), node("dd",
      d.metadata?.origin && d.metadata.origin !== "conversation"
        ? d.metadata.lifecycle === "memory" ? "带出处的正式见闻，按普通记忆遗忘；可参与当前范围内的思辨，来源声称仍需核实。" : "旧来源记忆，仍按原来源有效期使用。"
        : "普通对话记忆尚无结构化来源轮次；观点出处以原文为准。"));
    if (selected.view === "memories") {
      const link = node("button", "查看出处与相关讨论", "link"); link.type = "button";
      link.onclick = () => window.AliceChatSearch?.showMemory(d.metadata.id);
      meta.append(node("dt", "追溯"), link);
      if (d.metadata.provenance_status === "legacy_unverified") meta.append(node("dt", "迁移状态"), node("dd", "旧记录缺少可核对来源，暂不进入自动召回和思辨。"));
    }
  }
  function resetList() {
    before = null; ceiling = null; nextBefore = null; listSignature = ""; listRevision++;
    text("obs-list", "正在读取…"); text("obs-list-time", ""); el("obs-next").disabled = true;
    clearDetail();
    if (isList()) { pending.list = true; pump(); }
  }
  function changeView() {
    el("obs-overview").classList.toggle("hidden", view() !== "overview");
    el("obs-persona").classList.toggle("hidden", view() !== "persona");
    el("obs-traces").classList.toggle("hidden", view() !== "traces");
    el("obs-browser").classList.toggle("hidden", !isList());
    el("obs-layer").classList.toggle("hidden", view() !== "memories");
    el("obs-kind").classList.toggle("hidden", view() !== "memories");
    el("obs-search").classList.toggle("hidden", view() === "history");
    text("obs-list-hint", view() === "history" ? "文件名 vNNN 是覆盖前旧正文的保存编号，不代表该版本的最终正文。文件修改时间不是精确变更时刻。"
      : view() === "tombstones" ? "这些是库内已有的真实删除记录，包含本次观察之前的历史。墓碑不保存正文，不按标题推断替代关系。"
      : view() === "skills" ? "技能已有提炼记录；当前对话未消费技能，计数不能证明行为影响。"
      : "查询只读，不增加提及或使用次数。列表预览有长度上限，详情可分段查看；全文来源链未记录。");
    el("obs-list-hint").appendChild(document.createTextNode(" 每页独立采样，分页固定 ID 上界但不是冻结快照；条目变化可能改变筛选结果，刷新到最新可重新遍历。"));
    resetList();
  }
  function close() {
    opened = false; generation++; clearTimeout(timer); controller?.abort();
    pending = { snapshot: false, list: false, detail: false };
    clearData(); scope = null;
    el("observer").classList.add("hidden"); el("observer-btn").setAttribute("aria-expanded", "false");
    el("app").classList.remove("observing");
  }
  el("observer-btn").onclick = () => {
    if (opened) { close(); return; }
    window.AliceEvaluations?.close();
    window.AliceWorkers?.close(false);
    opened = true; stopped = false; generation++;
    el("observer").classList.remove("hidden"); el("observer-btn").setAttribute("aria-expanded", "true");
    el("app").classList.add("observing");
    requestSnapshot(); schedule();
  };
  el("obs-close").onclick = close;
  el("obs-refresh").onclick = () => {
    before = null; ceiling = null; nextBefore = null; listRevision++;
    if (detail) detail.dirty = true;
    // Keep the selected detail, refreshing its current text segment after the snapshot.
    requestSnapshot();
  };
  el("obs-clear").onclick = () => {
    generation++; controller?.abort(); clearData();
    pending = { snapshot: false, list: false, detail: false };
    requestSnapshot();
  };
  el("obs-auto").onchange = () => {
    sampleLabel(); schedule();
    if (el("obs-auto").checked) requestSnapshot();
    else pending.snapshot = false;
  };
  el("obs-view").onchange = changeView;
  el("obs-search").onsubmit = (e) => { e.preventDefault(); query = el("obs-query").value.trim(); resetList(); };
  el("obs-layer").onchange = resetList;
  el("obs-kind").onchange = resetList;
  el("obs-first").onclick = resetList;
  el("obs-next").onclick = () => {
    if (nextBefore === null) return;
    before = nextBefore; nextBefore = null; listRevision++; el("obs-next").disabled = true;
    text("obs-list-time", "正在读取下一页；失败可点首页或刷新到最新重试，旧页尚未更新。");
    clearDetail(); pending.list = true; pump();
  };
  el("obs-persona-full").onclick = () => {
    el("obs-view").value = "persona"; changeView();
    el("obs-persona-reading").scrollIntoView({ block: "nearest" });
  };
  el("obs-persona-file").onclick = () => openDetail("persona");
  el("obs-detail-mode").onchange = setDetailMode;
  function foldPersonaSections(open) {
    el("obs-persona-sections").querySelectorAll("details.obs-persona-content").forEach((card) => {
      card.open = open; personaFolds.set(card.dataset.personaKey, open);
    });
  }
  el("obs-persona-expand").onclick = () => foldPersonaSections(true);
  el("obs-persona-collapse").onclick = () => foldPersonaSections(false);
  el("obs-detail-close").onclick = clearDetail;
  el("obs-detail-first").onclick = () => { if (detail) { detail.offset = 0; detail.dirty = true; detailRevision++; pending.detail = true; pump(); } };
  el("obs-detail-next").onclick = () => {
    if (!detail || nextOffset === null) return;
    detail.offset = nextOffset; nextOffset = null; detailRevision++; el("obs-detail-next").disabled = true;
    detail.dirty = true;
    pending.detail = true; pump();
  };
  window.addEventListener("pagehide", close);
  window.AliceObserver = {
    close,
    refreshAfterTurn() { if (el("obs-auto").checked) requestSnapshot(); },
    shutdown() { stopped = true; close(); },
  };
})();
