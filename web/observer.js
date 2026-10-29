/* Read-only developer observer. No storage, model calls, or conversation writes. */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const layers = { active: "活跃", warehouse: "仓库", longterm: "长期" };
  const labels = {
    id: "稳定 ID", title: "标题", layer: "层级", kind: "类型", created_at: "记录时间 created_at",
    salience: "重要性", pressure: "压力标记", mention_count: "提及次数", use_count: "使用次数",
    last_used_at: "最近使用", promoted_at: "晋升时间", keywords: "关键词", source_titles: "出处标题（未验证）",
    filename: "快照文件", mtime: "文件修改时间", version: "当前版本计数", seeded_at: "人格安装时间",
    truncated: "元信息有截断", memory_id: "原记忆 ID", expired_at: "实际删除时间", reason: "存储原因",
  };
  const times = new Set(["created_at", "last_used_at", "promoted_at", "seeded_at", "mtime", "expired_at"]);
  let opened = false, stopped = false, running = false, generation = 0;
  let timer = null, controller = null, scope = null, snapshot = null, personaChange = null;
  let pending = { snapshot: false, list: false, detail: false }, changes = [], firstTime = null;
  let listRevision = 0, detailRevision = 0, before = null, ceiling = null, nextBefore = null;
  let listSignature = "", detail = null, nextOffset = null;
  let query = "", interrupted = false;
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
    detail = null; nextOffset = null; detailRevision++;
    el("obs-detail").classList.add("hidden");
    for (const id of ["obs-detail-title", "obs-detail-time", "obs-detail-meta", "obs-detail-text"]) text(id, "");
    el("obs-detail-next").disabled = true;
  }
  function clearData() {
    snapshot = null; personaChange = null; changes = []; firstTime = null; interrupted = false;
    before = null; ceiling = null; nextBefore = null; listSignature = ""; listRevision++;
    clearDetail();
    for (const id of ["obs-time", "obs-counts", "obs-coverage", "obs-progress", "obs-changes", "obs-list", "obs-list-time",
      "obs-persona-meta", "obs-persona-text", "obs-persona-diff-meta", "obs-persona-diff", "obs-persona-compare", "obs-changelog"]) text(id, "");
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
    return String(value);
  }
  function renderOverview(d) {
    const m = d.memory_counts, p = d.progress, x = p.extraction;
    text("obs-counts", `存活记忆：活跃 ${m.active} · 仓库 ${m.warehouse} · 长期 ${m.longterm}`);
    text("obs-coverage", `差异覆盖 ${d.manifest.items.length} / ${d.manifest.total} 条存活记忆（最近 ID，上限 ${d.manifest.limit}）。${d.manifest.complete ? "当前全部存活 ID 在范围内。" : "范围不完整；未跟踪条目仍可在记忆页检索。"} ${d.manifest.items.some((r) => r.text_truncated) ? "包含超长字段，仅比较前 16000 字与长度。" : ""}`);
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
  function renderPersona(d) {
    text("obs-persona-meta", `当前版本计数 ${d.persona.version} · 安装 ${time(d.persona.seeded_at)} · ${d.persona.truncated ? "当前原文仅显示前 64000 字符，尾部变化不可见" : "当前原文完整"}`);
    text("obs-persona-text", d.persona.text || "尚无人格正文");
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
          ? `${format("layer", r.layer)} · ${format("kind", r.kind)} · 重要 ${r.salience} · 压力 ${r.pressure} · 提及 ${r.mention_count} · 使用 ${r.use_count}`
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
    text("obs-detail-title", type === "persona" ? "当前人格完整原文" : `详情 ${id}`);
    text("obs-detail-time", "正在读取…");
    pending.detail = true; pump();
  }
  function renderDetail(d, selected) {
    detail.dirty = false;
    status("详情读取成功；各段以自己的采样时间为准。");
    nextOffset = d.next_offset ?? null;
    el("obs-detail-next").disabled = nextOffset === null;
    const chars = Array.from(d.text).length;
    text("obs-detail-time", `采样 ${time(d.sampled_at)} · 字符 ${chars ? d.offset + 1 : d.offset}–${d.offset + chars} / ${d.total_chars}（本段替换上一段）`);
    text("obs-detail-text", d.text || "（空正文）");
    const meta = el("obs-detail-meta"); meta.replaceChildren();
    Object.entries(d.metadata || {}).forEach(([key, value]) => {
      if (!labels[key]) return;
      meta.append(node("dt", labels[key]), node("dd", format(key, value)));
    });
    if (selected.view === "memories") meta.append(node("dt", "来源链"), node("dd", "未记录结构化来源 ID / 轮次；观点出处以原文为准。"));
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
  el("obs-persona-full").onclick = () => openDetail("persona");
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
    refreshAfterTurn() { if (el("obs-auto").checked) requestSnapshot(); },
    shutdown() { stopped = true; close(); },
  };
})();
