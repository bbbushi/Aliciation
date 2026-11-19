"use strict";
const byId = (id) => document.getElementById(id);
const sessionKey = location.hash.slice(1);
if (sessionKey) history.replaceState(null, "", location.pathname);

const modes = {interest:"兴趣探索", problem:"问题思考", iteration:"自我迭代", idle:"发呆"};
const reasons = {weighted_rotation:"按权重抽签", priority_content:"优先事项", explicit_task:"明确指定",
  no_actionable_work:"无可推进事项", activity_finish_rest:"Alice 提议休息", user_requested_rest:"用户安排休息",
  same_material_window:"同材料窗口用尽", mode_disabled:"模式未开启", value_waiting:"等待价值确认",
  new_material_or_curiosity:"新材料或好奇", voluntary_rest:"自愿休息", idle_disabled:"发呆未开启",
  actionable_question:"有可推进问题", no_content:"无内容"};
const layerLabels = {active:"活跃", warehouse:"仓库", longterm:"长期"};
const kindLabels = {event:"事件", stance:"立场"};
const phaseLabels = {completed:"已完成", rolled_back:"已回滚", superseded:"已被取代", aborted:"已中止",
  prepared:"已预备", db_committed:"已入库", file_replaced:"已换文件", unknown:"未知", unreadable:"不可读"};
const purposeLabels = {main:"主对话", main_stream:"主对话·流式", main_fallback:"主对话·回退",
  extraction:"记忆提取", evolution:"人格演化", distill:"技能蒸馏", deliberate:"思考", judge:"评审",
  review:"复读标注", summary:"摘要", vision:"读图", search:"联网", unspecified:"未标注"};
const questionStatus = {open:"待处理", resolved:"已解决", closed:"已关闭"};
const callStatusLabels = {ok:"成功", error:"失败", cancelled:"取消", stream_interrupted:"流中断"};
const originLabels = {interest:"兴趣", exploration:"探索", review:"复读审核", user_request:"用户指定",
  problem:"问题", iteration:"迭代", extraction:"提取"};
const C = ["#2f7a63", "#3d6f9c", "#b07a2f", "#8a5a9e", "#a8544c", "#6d7c46"];

let targetMode = null, epoch = null, topic = "schedule", armed = null, working = false;
let contextFrame = null, connectionId = null, grantExpiry = null, page = "gate", loadedAt = null;

/* ---------- 基础工具 ---------- */
function node(tag, text) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  return el;
}
function el(tag, cls, text) {
  const n = node(tag, text);
  if (cls) n.className = cls;
  return n;
}
const fmt = {
  int: (n) => (n == null || Number.isNaN(n) ? "—" : Number(n).toLocaleString("zh-CN")),
  tok: (n) => {
    if (n == null || Number.isNaN(n)) return "—";
    if (n >= 100000) return (n / 1000).toFixed(0) + "k";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return String(Math.round(n));
  },
  dur: (ms) => {
    if (ms == null || Number.isNaN(ms)) return "—";
    if (ms < 1000) return Math.round(ms) + "ms";
    if (ms < 60000) return (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + "s";
    return Math.floor(ms / 60000) + "m" + Math.round((ms % 60000) / 1000) + "s";
  },
  pct: (x) => (x == null || !Number.isFinite(x) ? "—" : (x * 100).toFixed(1) + "%"),
  time: (ts) => (ts ? new Date(ts * 1000).toLocaleString("zh-CN",
    {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"}) : "—"),
  clock: (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString("zh-CN",
    {hour:"2-digit", minute:"2-digit"}) : "—"),
  ago: (ts) => {
    if (!ts) return "—";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return "刚刚";
    if (s < 3600) return Math.round(s / 60) + " 分钟前";
    if (s < 86400) return Math.round(s / 3600) + " 小时前";
    return Math.round(s / 86400) + " 天前";
  },
  short: (id) => (id ? String(id).slice(0, 8) : "—"),
};

function kpis(specs) {
  const row = el("div", "kpis");
  for (const s of specs) {
    const box = el("div", "kpi");
    box.append(node("span", s.label), node("b", s.value ?? "—"));
    if (s.hint) box.append(node("i", s.hint));
    row.append(box);
  }
  return row;
}
function card(title, sub, badge) {
  const box = el("section", "card");
  const head = el("div", "card-head");
  head.append(node("h3", title));
  if (badge) head.append(pill(badge.text, badge.tone));
  box.append(head);
  if (sub) box.append(el("p", "card-sub", sub));
  const body = el("div", "card-body");
  box.append(body);
  return {box, body};
}
function pill(text, tone) {
  return el("span", "pill" + (tone ? " " + tone : ""), text);
}
function emptyNote(text) {
  return el("p", "empty", text);
}
function barRows(items, opts = {}) {
  const wrap = el("div", "bars"), max = Math.max(...items.map((i) => i.value || 0), 0.0001);
  for (const item of items) {
    const row = el("div", "bar-row");
    if (opts.labelWidth) row.style.gridTemplateColumns = `${opts.labelWidth}px minmax(0,1fr) 112px`;
    const name = el("span", "bar-name", item.label);
    name.title = item.label;
    row.append(name);
    const track = el("div", "bar-track"), fill = el("div", "bar-fill");
    fill.style.width = Math.max(0, Math.min(100, ((item.value || 0) / max) * 100)).toFixed(1) + "%";
    fill.style.background = item.color || C[1];
    track.append(fill);
    const note = el("span", "bar-note", item.note ?? fmt.int(item.value));
    note.title = item.note ?? fmt.int(item.value);
    row.append(track, note);
    wrap.append(row);
  }
  return wrap;
}
function stack(segments) {
  const bar = el("div", "stack"), legend = el("div", "legend"), total = segments.reduce((a, s) => a + (s.value || 0), 0);
  for (const seg of segments) {
    if (!seg.value) continue;
    const part = el("span");
    part.style.width = ((seg.value / (total || 1)) * 100).toFixed(2) + "%";
    part.style.background = seg.color;
    part.title = `${seg.label} ${fmt.int(seg.value)}`;
    bar.append(part);
    const item = el("span");
    const dot = el("span", "dot");
    dot.style.background = seg.color;
    item.append(dot, node("span", `${seg.label} ${fmt.int(seg.value)} · ${fmt.pct(seg.value / (total || 1))}`));
    legend.append(item);
  }
  const wrap = el("div");
  wrap.append(bar, legend);
  return wrap;
}
function table(headers, rows, opts = {}) {
  const wrap = el("div", "scroll" + (opts.cards ? " cards" : "")), result = node("table"),
    top = node("tr"), head = node("thead"), body = node("tbody");
  for (const name of headers) {
    const th = node("th", name);
    th.scope = "col";
    top.append(th);
  }
  head.append(top);
  for (const row of rows) {
    const tr = node("tr");
    row.forEach((value, index) => {
      const td = node("td");
      if (headers[index]) td.dataset.label = headers[index];
      if (value instanceof HTMLElement) td.append(value);
      else td.textContent = value ?? "—";
      if (!(value instanceof HTMLElement) && (value == null || value === "—")) td.className = "blank";
      tr.append(td);
    });
    body.append(tr);
  }
  result.append(head, body);
  wrap.append(result);
  return wrap;
}
function rawDetails(label, value) {
  const block = node("details");
  block.append(node("summary", label), node("pre", JSON.stringify(value, null, 2)));
  return block;
}
function line(label, value) {
  const row = el("div", "line");
  row.append(node("span", label), node("b", value));
  return row;
}
function keyValues(obj, skip = []) {
  const wrap = el("div", "rows");
  for (const [key, value] of Object.entries(obj || {})) {
    if (skip.includes(key)) continue;
    if (value == null || typeof value === "object") continue;
    wrap.append(line(key, String(value)));
  }
  return wrap;
}
function gridOf(cards) {
  const grid = el("div", "grid");
  for (const c of cards) grid.append(c);
  return grid;
}
function mountCards(out, items) {
  let grid = null;
  for (const item of items) {
    const box = item.box || item;
    if (item.wide) {
      if (grid) { out.append(grid); grid = null; }
      out.append(box);
    } else {
      if (!grid) { grid = el("div", "grid"); out.append(grid); }
      grid.append(box);
    }
  }
}
const lifecycleLabels = {live:"在用", stale:"陈旧", archived:"已归档", pending:"待定"};
function statusLabel(value) {
  if (!value) return "—";
  if (questionStatus[value]) return questionStatus[value];
  if (String(value).startsWith("waiting")) return "等待中";
  return value;
}
function purposeLabel(value) {
  if (!value) return "未标注";
  return purposeLabels[value] || String(value).replace(/_/g, "·").slice(0, 16);
}

/* ---------- 各模块渲染 ---------- */
function probability(weights, mode) {
  if (!weights || !Object.keys(weights).length) return "—";
  const total = Object.values(weights).reduce((a, b) => a + b, 0);
  return fmt.pct((weights[mode] || 0) / (total || 1));
}

function paintSchedule(v, out) {
  const cur = v.current || {}, recs = v.records || [], keys = Object.keys(modes);
  const info = (key) => cur.modes?.[key] || {};
  const heaviest = keys.reduce((best, key) => ((info(key).weight || 0) > (info(best).weight || 0) ? key : best), keys[0]);
  const drawable = Object.keys(cur.draw_weights || {}).length > 0;

  out.append(kpis([
    {label:"当前最重模式", value: modes[heaviest], hint: `权重 ${info(heaviest).weight ?? "—"}`},
    {label:"连续无进展轮", value: fmt.int(cur.dry_windows), hint: "抬高发呆分"},
    {label:"受阻模式", value: fmt.int(cur.blocked_modes), hint: "有事项但被窗口挡住"},
    {label:"上一项活动", value: modes[cur.previous_activity] || "无", hint: "同模式权重 ×0.55"},
    {label:"抽签记录", value: fmt.int(recs.length), hint: "本会话最近 30 次"},
  ]));

  const weightCard = card("抽签权重", drawable
    ? "按权重归一化得到抽中概率；内容分来自问题重要度/紧急度与轮转债。"
    : "本次存在优先事项，直接指定，不进入抽签。");
  weightCard.body.append(barRows(keys.map((key) => ({
    label: modes[key],
    value: info(key).weight || 0,
    color: key === "idle" ? C[2] : C[1],
    note: `${probability(cur.draw_weights, key)} · 分 ${info(key).content_score ?? "—"}`,
  }))));
  if (!drawable) weightCard.body.append(el("p", "note", "没有 draw_weights：优先事项或明确指定时不会抽签。"));

  const dry = cur.dry_windows || 0, blocked = cur.blocked_modes || 0;
  const restCard = card("发呆分构成", "发呆分 = 1 + 2×无进展轮（最多 3）+ 2×受阻模式（最多 3）。");
  restCard.body.append(stack([
    {label:"基数", value:1, color:C[5]},
    {label:"无进展", value:2 * Math.min(dry, 3), color:C[2]},
    {label:"受阻", value:2 * Math.min(blocked, 3), color:C[4]},
  ]));
  restCard.body.append(line("发呆权重", String(info("idle").weight ?? "—")));
  restCard.body.append(line("发呆秒数", cur.idle_seconds ? cur.idle_seconds + "s" : "未开启"));

  const recentCard = card("最近实际选择", "最近 12 次调度，含原因与当时概率。");
  if (!recs.length) recentCard.body.append(emptyNote("还没有运行记录。"));
  else {
    const tl = el("div", "tl tl-cols");
    for (const item of recs.slice(0, 12)) {
      const row = el("div", "tl-item");
      row.append(el("div", "tl-at", fmt.clock(item.at)));
      const dotEl = el("div", "tl-node");
      dotEl.style.background = item.activity === "idle" ? C[2] : C[1];
      row.append(dotEl);
      const body = el("div", "tl-body"), title = el("div", "tl-title");
      title.append(node("span", modes[item.activity] || item.activity));
      title.append(pill(reasons[item.reason] || item.reason || "原因未记录", item.reason === "weighted_rotation" ? "ok" : ""));
      const chance = probability(item.draw_weights, item.activity);
      if (chance !== "—") title.append(pill("概率 " + chance));
      body.append(title);
      body.append(el("div", "tl-sub", item.draw_weights && Object.keys(item.draw_weights).length
        ? "抽签池 " + Object.keys(item.draw_weights).map((k) => modes[k]).join(" / ")
        : item.reason === "weighted_rotation" ? "旧记录未保存抽签权重。" : "这次没有抽签。"));
      row.append(body);
      tl.append(row);
    }
    recentCard.body.append(tl);
  }

  const tableCard = card("记录明细", "按时间倒序；展开可看当时的完整权重。");
  if (!recs.length) tableCard.body.append(emptyNote("暂无记录。"));
  else {
    tableCard.body.append(table(["时间", "活动", "原因", "当时概率", "无进展"], recs.map((item) => [
      fmt.time(item.at), modes[item.activity] || item.activity, reasons[item.reason] || item.reason || "—",
      probability(item.draw_weights, item.activity), item.dry_windows ?? "—",
    ])));
    tableCard.body.append(rawDetails("查看原始记录", recs));
  }

  mountCards(out, [
    {box: weightCard.box}, {box: restCard.box},
    {box: recentCard.box, wide:true}, {box: tableCard.box, wide:true},
  ]);
}

function paintMemory(v, out) {
  const counts = v.counts || {}, items = v.items || [];
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const pressured = items.filter((i) => i.pressure).length;
  const important = items.filter((i) => i.salience >= 1).length;
  out.append(kpis([
    {label:"记忆总数", value: fmt.int(total), hint:"三层合计"},
    {label:"活跃", value: fmt.int(counts.active)},
    {label:"仓库", value: fmt.int(counts.warehouse)},
    {label:"长期", value: fmt.int(counts.longterm)},
    {label:"重要事项", value: fmt.int(important), hint:"salience=1，最近 40 条"},
    {label:"压力中", value: fmt.int(pressured), hint:"pressure=1"},
  ]));

  const layerCard = card("三层分布", "活跃层进上下文，仓库层待晋升，长期层是稳定结论。");
  layerCard.body.append(stack([
    {label:"活跃", value:counts.active || 0, color:C[0]},
    {label:"仓库", value:counts.warehouse || 0, color:C[2]},
    {label:"长期", value:counts.longterm || 0, color:C[1]},
  ]));
  const weekAgo = Date.now() / 1000 - 7 * 86400;
  const usedThisWeek = items.filter((i) => i.last_used_at && i.last_used_at >= weekAgo).length;
  const soonest = items.reduce((min, i) => (i.expires_at && (!min || i.expires_at < min) ? i.expires_at : min), null);
  layerCard.body.append(line("近 7 天有取用", `${usedThisWeek} 条`));
  layerCard.body.append(line("最近到期", soonest ? fmt.time(soonest) : "无期限"));

  const ranked = [...items].sort((a, b) => (b.salience || 0) - (a.salience || 0)
    || (b.use_count || 0) - (a.use_count || 0)).slice(0, 8);
  const heatCard = card("重要与常用", "与 Alice 自己的排序取向一致：先 salience（重大事项 0/1），再取用次数。");
  heatCard.body.append(ranked.length
    ? barRows(ranked.map((i) => ({label:`#${i.id} ${kindLabels[i.kind] || i.kind || ""}`,
        value:i.use_count || 0, color:i.salience ? C[3] : C[1],
        note:`${i.salience ? "重要" : "一般"} · ${fmt.int(i.use_count)} 次`})))
    : emptyNote("还没有记忆。"));
  if (ranked.length) {
    heatCard.body.append(el("p", "note",
      "salience 只有 0/1 两档（生日、健康、重大决定、重要约定、强烈情绪），不是连续价值分；层与取用次数才是长期依据。"));
  }

  const listCard = card("最近记忆", `按 id 倒序，最多 ${items.length} 条；只有元数据。`);
  if (!items.length) listCard.body.append(emptyNote("还没有记忆。"));
  else {
    listCard.body.append(table(["#", "层", "类型", "重要", "提及", "使用", "最近使用", "到期", "状态"], items.map((i) => [
      String(i.id), layerLabels[i.layer] || i.layer || "—", kindLabels[i.kind] || i.kind || "—",
      i.salience ? pill("重要", "warn") : "—", fmt.int(i.mention_count), fmt.int(i.use_count),
      fmt.ago(i.last_used_at), i.expires_at ? fmt.time(i.expires_at) : "—",
      lifecycleLabels[i.lifecycle] || i.lifecycle || i.provenance_status || "—",
    ]), {cards:true}));
    listCard.body.append(rawDetails("查看原始元数据", items));
  }

  const noteCard = card("正文与标题", "元数据授权只到 id 与计数；标题、正文要 Alice 单独勾选正文授权后再读。");
  noteCard.body.append(el("p", "note",
    targetMode === "body" ? "当前连接已含正文授权，可在下方按 ID 读取已留存的正文。"
      : "当前连接只有元数据授权，按 ID 读取正文会被拒绝。"));
  const column = el("div", "col");
  column.append(layerCard.box, noteCard.box);
  const pair = el("div", "grid");
  pair.append(column, heatCard.box);
  out.append(pair);
  mountCards(out, [{box: listCard.box, wide:true}]);
}

function paintQuestions(v, out) {
  const items = v.items || [];
  const open = items.filter((i) => i.status === "open").length;
  const statuses = {}, origins = {};
  for (const i of items) {
    statuses[i.status || "unknown"] = (statuses[i.status || "unknown"] || 0) + 1;
    origins[i.origin || "unknown"] = (origins[i.origin || "unknown"] || 0) + 1;
  }
  const latest = items.reduce((max, i) => Math.max(max, i.updated_at || 0), 0);
  out.append(kpis([
    {label:"疑问总数", value: fmt.int(items.length), hint:"最多 40 条"},
    {label:"待处理", value: fmt.int(open)},
    {label:"来源种类", value: fmt.int(Object.keys(origins).length)},
    {label:"最近更新", value: latest ? fmt.ago(latest) : "—"},
  ]));

  const statusCard = card("状态分布", "waiting* 表示在等外部条件，不是卡住。");
  statusCard.body.append(Object.keys(statuses).length
    ? barRows(Object.entries(statuses).sort((a, b) => b[1] - a[1]).map(([key, n]) => ({
        label: statusLabel(key), value:n, color:C[0]})))
    : emptyNote("暂无疑问。"));

  const originCard = card("来源分布", "谁把它放进 Alice 的工作台。");
  originCard.body.append(Object.keys(origins).length
    ? barRows(Object.entries(origins).sort((a, b) => b[1] - a[1]).map(([key, n]) => ({
        label: originLabels[key] || key, value:n, color:C[3]})))
    : emptyNote("暂无来源记录。"));

  const listCard = card("疑问清单", "按更新时间倒序；隔离测试里可直接推进一步。");
  if (!items.length) listCard.body.append(emptyNote("当前会话没有疑问。"));
  else {
    const wrap = el("div", "rows");
    for (const item of items) {
      const box = el("div", "tl-item");
      box.style.borderBottom = "1px solid var(--rule-soft)";
      const left = el("div", "tl-at", "#" + item.id);
      const dotEl = el("div", "tl-node");
      dotEl.style.background = item.status === "open" ? C[0] : C[5];
      const body = el("div", "tl-body"), title = el("div", "tl-title");
      const text = item.question || `疑问 #${item.id}`;
      const label = el("span", "q", text);
      label.title = text;
      title.append(label);
      title.append(pill(statusLabel(item.status), item.status === "open" ? "ok" : ""));
      title.append(pill(originLabels[item.origin] || item.origin || "来源未知"));
      if (item.kind && item.kind !== "question") title.append(pill(item.kind));
      body.append(title);
      const meta = [`更新 ${fmt.time(item.updated_at)}`];
      if (item.next_step) meta.push("下一步：" + item.next_step);
      body.append(el("div", "tl-sub", meta.join(" · ")));
      if (topic === "questions" && targetMode === "isolated" && item.status === "open") {
        const button = node("button", "探索此疑问");
        button.className = "quiet";
        button.style.marginTop = "6px";
        button.onclick = () => perform(async () => {
          await api("control", {id:item.id, version:item.version}, {command:"run"});
        });
        body.append(button);
      }
      box.append(left, dotEl, body);
      wrap.append(box);
    }
    listCard.body.append(wrap);
    if (targetMode !== "isolated") {
      listCard.body.append(el("p", "note", "日常连接只读：推进疑问要在隔离实例里操作，这里只显示状态。"));
    }
    listCard.body.append(rawDetails("查看原始记录", items));
  }
  out.append(gridOf([statusCard.box, originCard.box]));
  mountCards(out, [{box: listCard.box, wide:true}]);
}

function paintCalls(v, out) {
  const items = (v.items || []).slice().reverse();
  const failed = items.filter((i) => i.status && i.status !== "ok").length;
  const tokens = items.reduce((acc, i) => {
    const u = i.usage;
    if (u && typeof u === "object") {
      acc.prompt += u.prompt || 0;
      acc.completion += u.completion || 0;
      acc.known += 1;
    }
    return acc;
  }, {prompt:0, completion:0, known:0});
  const durations = items.map((i) => i.duration_ms).filter((n) => typeof n === "number").sort((a, b) => a - b);
  const median = durations.length ? durations[Math.floor(durations.length / 2)] : null;

  out.append(kpis([
    {label:"调用次数", value: fmt.int(items.length), hint:`扫描上限 ${v.scan_limit ?? "—"}`},
    {label:"未成功", value: fmt.int(failed), hint:"失败或取消"},
    {label:"输入 tokens", value: fmt.tok(tokens.prompt), hint:`${tokens.known} 次有回执`},
    {label:"输出 tokens", value: fmt.tok(tokens.completion)},
    {label:"中位耗时", value: fmt.dur(median)},
  ]));

  const trendCard = card("耗时趋势", "每次模型调用一根柱，左旧右新。");
  if (!items.length) trendCard.body.append(emptyNote("还没有调用回执。"));
  else {
    const max = Math.max(...items.map((i) => i.duration_ms || 0), 1);
    const cols = el("div", "cols");
    for (const item of items) {
      const bar = el("div");
      bar.style.height = Math.max(2, ((item.duration_ms || 0) / max) * 100) + "%";
      bar.style.background = item.status && item.status !== "ok" ? C[4] : item.attempt > 1 ? C[2] : C[1];
      bar.title = `${fmt.clock(item.ts)} · ${purposeLabels[item.purpose] || item.purpose} · ${fmt.dur(item.duration_ms)}${item.status && item.status !== "ok" ? " · " + item.status : ""}`;
      cols.append(bar);
    }
    trendCard.body.append(cols);
    const axis = el("div", "axis");
    axis.append(node("span", fmt.clock(items[0].ts)), node("span", fmt.clock(items[items.length - 1].ts)));
    trendCard.body.append(axis);
    const legend = el("div", "legend");
    for (const [color, text] of [[C[1], "成功"], [C[2], "重试"], [C[4], "失败/取消"]]) {
      const s = el("span"), dot = el("span", "dot");
      dot.style.background = color; s.append(dot, node("span", text)); legend.append(s);
    }
    trendCard.body.append(legend);
  }

  const purposeMap = {};
  for (const i of items) {
    const key = i.purpose || "unspecified";
    const slot = purposeMap[key] || (purposeMap[key] = {count:0, tokens:0, ms:0});
    slot.count += 1;
    slot.ms += i.duration_ms || 0;
    const u = i.usage;
    if (u && typeof u === "object") slot.tokens += (u.prompt || 0) + (u.completion || 0);
  }
  const purposeCard = card("用途分布", "同一请求里可能有多次调用：主对话、记忆提取、评审分别计量。");
  purposeCard.body.append(Object.keys(purposeMap).length
    ? barRows(Object.entries(purposeMap).sort((a, b) => b[1].count - a[1].count).map(([key, s]) => ({
        label: purposeLabel(key), value:s.count, color:C[3],
        note:`${s.count} 次 · ${fmt.tok(s.tokens)} tok`})))
    : emptyNote("暂无调用。"));

  const stateCard = card("回执完整度", "哪些调用拿到了真实用量。");
  stateCard.body.append(line("有 usage 回执", `${tokens.known} / ${items.length}`));
  stateCard.body.append(line("缺失丢弃", fmt.int(v.pending_dropped)));
  stateCard.body.append(line("未知用量条数", fmt.int(items.length - tokens.known)));
  if (v.notice) stateCard.body.append(el("p", "note", v.notice));

  const modelMap = {};
  for (const i of items) {
    const key = i.model || "未知";
    const slot = modelMap[key] || (modelMap[key] = {count:0, tokens:0, ms:0});
    slot.count += 1;
    slot.ms += i.duration_ms || 0;
    const u = i.usage;
    if (u && typeof u === "object") slot.tokens += (u.prompt || 0) + (u.completion || 0);
  }
  const modelCard = card("模型分布", "回执里报告的模型；缺失时按未知计。");
  modelCard.body.append(Object.keys(modelMap).length
    ? barRows(Object.entries(modelMap).sort((a, b) => b[1].count - a[1].count).map(([key, s]) => ({
        label: key, value:s.count, color:C[1],
        note:`${s.count} 次 · ${fmt.tok(s.tokens)} tok`})), {labelWidth:128})
    : emptyNote("暂无模型回执。"));

  const tableCard = card("调用明细", "最近 40 次，按时间倒序。");
  tableCard.body.append(items.length ? table(
    ["时间", "用途", "模型", "状态", "耗时", "入", "出"],
    items.slice().reverse().map((i) => {
      const u = i.usage && typeof i.usage === "object" ? i.usage : {};
      return [fmt.time(i.ts), purposeLabel(i.purpose), i.model || "—",
        callStatusLabels[i.status] || i.status || "—", fmt.dur(i.duration_ms),
        fmt.tok(u.prompt), fmt.tok(u.completion)];
    })) : emptyNote("暂无调用。"));
  if (items.length) tableCard.body.append(rawDetails("查看原始回执", items));

  out.append(gridOf([trendCard.box, purposeCard.box, stateCard.box, modelCard.box]));
  mountCards(out, [{box: tableCard.box, wide:true}]);
}

function paintPersona(v, out) {
  const items = v.items || [];
  const terminal = items.filter((i) => ["completed", "rolled_back", "superseded", "aborted"].includes(i.phase)).length;
  const broken = items.filter((i) => ["unreadable", "unknown"].includes(i.phase)).length;
  const matched = items.filter((i) => i.epoch_matches).length;
  out.append(kpis([
    {label:"操作记录", value: fmt.int(items.length), hint:"最近 20 条"},
    {label:"已到终态", value: fmt.int(terminal)},
    {label:"异常", value: fmt.int(broken), hint:"不可读或未知阶段"},
    {label:"当前会话内", value: fmt.int(matched), hint:"epoch 匹配"},
  ]));

  const phaseMap = {};
  for (const i of items) phaseMap[i.phase || "unknown"] = (phaseMap[i.phase || "unknown"] || 0) + 1;
  const phaseCard = card("阶段分布", "终态之外还留在日志里的，都是需要人工核对的迁移。");
  phaseCard.body.append(items.length
    ? barRows(Object.entries(phaseMap).sort((a, b) => b[1] - a[1]).map(([key, n]) => ({
        label: phaseLabels[key] || key, value:n,
        color:["completed"].includes(key) ? C[0] : ["rolled_back", "aborted", "superseded"].includes(key) ? C[5] : C[2]})))
    : emptyNote("没有人格导入记录。"));

  const resetCard = card("重置与迁移", "只读操作元数据，不触发恢复。");
  const reset = v.reset;
  if (!reset) resetCard.body.append(emptyNote("没有复位记录。"));
  else {
    resetCard.body.append(line("阶段", phaseLabels[reset.phase] || reset.phase || "—"));
    resetCard.body.append(line("旧 epoch", reset.old_epoch ? fmt.short(reset.old_epoch) : "—"));
    resetCard.body.append(line("新 epoch", reset.new_epoch ? fmt.short(reset.new_epoch) : "—"));
    resetCard.body.append(line("归档", reset.archive || "—"));
  }
  const expr = v.expression_updates;
  if (expr && typeof expr === "object") {
    const exprItems = Array.isArray(expr.items) ? expr.items : [];
    resetCard.body.append(line("表达更新", `${exprItems.length} 条`));
    if (exprItems.length) {
      resetCard.body.append(rawDetails("查看表达更新", exprItems.slice(0, 8)));
    }
  }

  const listCard = card("导入记录", "op_id 可以对照 Alice 的日志定位。");
  if (!items.length) listCard.body.append(emptyNote("没有记录。"));
  else {
    const wrap = el("div", "rows");
    items.forEach((item, index) => {
      const row = el("div", "tl-item");
      row.style.borderBottom = "1px solid var(--rule-soft)";
      row.append(el("div", "tl-at", String(items.length - index).padStart(2, "0")));
      const dotEl = el("div", "tl-node");
      dotEl.style.background = item.phase === "completed" ? C[0] : ["unreadable", "unknown"].includes(item.phase) ? C[4] : C[2];
      row.append(dotEl);
      const body = el("div", "tl-body"), title = el("div", "tl-title");
      const phase = el("span", "q one", item.op_id || "—");
      phase.style.fontFamily = "ui-monospace,monospace";
      phase.style.fontSize = "12px";
      title.append(phase);
      title.append(pill(phaseLabels[item.phase] || item.phase || "—",
        ["completed"].includes(item.phase) ? "ok" : ["unreadable", "unknown"].includes(item.phase) ? "bad" : "warn"));
      if (item.epoch_matches) title.append(pill("本会话"));
      if (item.db_marker_present) title.append(pill("有库标记"));
      body.append(title);
      const meta = [`插入 ${fmt.int(item.inserted_count)} 条`];
      if (item.before_sha256) meta.push("前 " + item.before_sha256.slice(0, 8));
      if (item.after_sha256) meta.push("后 " + item.after_sha256.slice(0, 8));
      body.append(el("div", "tl-sub", meta.join(" · ")));
      row.append(body);
      wrap.append(row);
    });
    listCard.body.append(wrap, rawDetails("查看原始记录", v));
    if (v.truncated || v.scan_incomplete) listCard.body.append(el("p", "note", "本次扫描不完整，列表可能少于实际记录。"));
    if (v.notice) listCard.body.append(el("p", "note", v.notice));
  }
  out.append(gridOf([phaseCard.box, resetCard.box]));
  mountCards(out, [{box: listCard.box, wide:true}]);
}

const kindLabelsCtx = {stable:"系统与规则", background:"背景", index:"索引", history:"对话历史",
  tool_result:"工具结果", selection:"选择决策"};
function requestSegments(request) {
  const groups = {};
  for (const block of request.blocks || []) {
    const key = block.name === "tool_schemas" ? "tool" : (block.kind || "other");
    groups[key] = (groups[key] || 0) + (block.estimated_tokens || 0);
  }
  const palette = {stable:C[1], background:C[0], index:C[5], history:C[4], tool_result:C[2], tool:C[3], other:C[5]};
  return Object.entries(groups).map(([key, value]) => ({
    label: key === "tool" ? "工具定义" : (kindLabelsCtx[key] || key), value, color: palette[key] || C[5]}));
}
async function paintContext(out) {
  const [requests, status] = await Promise.all([
    api("observe", {}, {topic:"context"}).then((r) => r.result),
    api("observe", {}, {topic:"context_status"}).then((r) => r.result).catch(() => null),
  ]);
  const list = requests.requests || [];
  const calls = list.reduce((n, r) => n + (r.calls || []).length, 0);
  const blocks = list.reduce((n, r) => n + (r.blocks || []).length, 0);
  const bodyOk = status?.can_read_body;

  out.append(kpis([
    {label:"最近请求", value: fmt.int(list.length), hint:"观测窗口内"},
    {label:"模型调用", value: fmt.int(calls)},
    {label:"装配块", value: fmt.int(blocks)},
    {label:"正文授权", value: bodyOk ? "已授权" : "未授权", hint: bodyOk ? "可按片段读原文" : "仅元数据"},
  ]));

  const overview = card("请求组成", "每个请求一条堆叠条；长度按装配块估算 token 归一化。");
  if (!list.length) overview.body.append(emptyNote("还没有上下文观测记录。"));
  else {
    const wrap = el("div", "rows");
    for (const request of list.slice(0, 6)) {
      const row = el("div");
      const head = el("div", "line");
      head.append(node("span", `${fmt.time(request.first_event_at)} · ${(request.calls || []).length} 次调用 · ${(request.blocks || []).length} 块`),
        node("b", request.finished ? "已结束" : "进行中"));
      row.append(head, stack(requestSegments(request)));
      wrap.append(row);
    }
    const open = node("button", "打开完整观测器");
    open.className = "inline-action";
    open.onclick = () => { try { openContext(); } catch (err) { feedback(err.message, true); } };
    wrap.append(open);
    overview.body.append(wrap);
  }

  const tableCard = card("最近请求", "点开原始记录可核对事件数与决策留痕。");
  if (list.length) {
    tableCard.body.append(table(["时间", "调用", "工具轮次", "装配块", "估算 tokens", "事件", "状态"], list.map((r) => {
      const estimated = (r.blocks || []).reduce((n, b) => n + (b.estimated_tokens || 0), 0);
      const rounds = (r.calls || []).reduce((n, c) => Math.max(n, c.tool_round || 0), 0);
      return [fmt.time(r.first_event_at), String((r.calls || []).length), rounds || "—",
        String((r.blocks || []).length), fmt.tok(estimated), fmt.int(r.event_count),
        r.finished ? "已结束" : "进行中"];
    })));
    tableCard.body.append(rawDetails("查看原始请求投影", list));
  } else tableCard.body.append(emptyNote("暂无请求。"));

  const stateCard = card("采集状态", "协议版本与采集范围决定这里能看到多少。");
  if (!status) stateCard.body.append(emptyNote("采集状态不可读。"));
  else {
    stateCard.body.append(line("协议版本", status.protocol_version || "—"));
    stateCard.body.append(line("元数据采集", status.capture?.metadata ? "开启" : "关闭"));
    stateCard.body.append(line("正文采集", status.capture?.body ? "开启" : "关闭"));
    if (status.quota) {
      stateCard.body.append(line("正文额度", `${fmt.int(status.quota.requests?.used)} / ${fmt.int(status.quota.requests?.limit)} 请求`));
      stateCard.body.append(line("占用", fmt.tok(status.quota.body_dir_bytes) + " / " + fmt.tok(status.quota.limit_bytes)));
    }
  }
  if (requests.notice) stateCard.body.append(el("p", "note", requests.notice));

  const blockLabels = {constitution:"宪法", persona_core:"人格核心", tool_schemas:"工具定义",
    memory_active:"活跃记忆", knowledge_index:"见闻索引", recent_turns:"近期回合",
    recall:"召回结果", question_index:"疑问索引"};
  const blockMap = {};
  for (const request of list) {
    for (const block of request.blocks || []) {
      const key = block.name || block.kind || "未命名";
      blockMap[key] = (blockMap[key] || 0) + (block.estimated_tokens || 0);
    }
  }
  const blocksCard = card("装配块用量", "按名称合并最近请求里的估算 token。");
  const topBlocks = Object.entries(blockMap).sort((a, b) => b[1] - a[1]).slice(0, 6);
  blocksCard.body.append(topBlocks.length
    ? barRows(topBlocks.map(([key, value]) => ({
        label: blockLabels[key] || key, value, color:C[3], note:fmt.tok(value) + " tok"})))
    : emptyNote("还没有装配块记录。"));

  out.append(overview.box, tableCard.box);
  mountCards(out, [{box: stateCard.box}, {box: blocksCard.box}]);
}

/* ---------- 页面装配 ---------- */
const topics = {
  schedule: {title:"调度权重", sub:"谁在决定 Alice 接下来做什么，以及为什么。"},
  context: {title:"上下文", sub:"每次请求装了什么、装了多少；完整观测器另有入口。"},
  memory: {title:"记忆", sub:"三层记忆的存量、使用与期限状态。"},
  questions: {title:"疑问", sub:"Alice 手头的问题、来源与推进状态。"},
  calls: {title:"模型调用", sub:"每次调用的耗时、用量与去向。"},
  persona_operations: {title:"人格导入", sub:"人格文件安装与迁移的操作日志。"},
};

function setPage(next) {
  page = next;
  const connected = Boolean(targetMode);
  byId("gate").hidden = next !== "gate";
  byId("observe").hidden = next !== "dashboard";
  byId("controls").hidden = next !== "bench";
  byId("refresh").hidden = next !== "dashboard";
  byId("bench-body").hidden = targetMode !== "isolated";
  byId("bench-note").hidden = targetMode === "isolated";
  document.querySelectorAll("#topics button").forEach((btn) => {
    btn.disabled = !connected;
    btn.setAttribute("aria-pressed", String(next === "dashboard" && btn.dataset.topic === topic));
  });
  if (next === "dashboard") {
    byId("page-title").textContent = topics[topic]?.title || "运行数据";
    byId("page-sub").textContent = topics[topic]?.sub || "";
  } else if (next === "bench") {
    byId("page-title").textContent = "测试台";
    byId("page-sub").textContent = "隔离实例里的可写操作；日常连接只读。";
  } else {
    byId("page-title").textContent = "连接 Alice";
    byId("page-sub").textContent = "从 Alice 的设置生成只读授权码，或启动独立测试实例。";
  }
}
function feedback(message, bad = false) {
  const box = byId("feedback");
  box.className = bad ? "error" : "";
  box.textContent = message;
}
function stamp() {
  loadedAt = new Date();
  byId("stamp").textContent = loadedAt.toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", second:"2-digit"}) + " 更新";
}
async function show(next = topic) {
  if (next !== "context") {
    const data = await api("observe", {}, {topic:next});
    const value = data.result;
    if (value && value.status && value.status !== "ok") throw new Error(value.error || "数据暂不可读，请稍后再试");
    topic = next;
    const output = byId("observations");
    output.replaceChildren();
    byId("body-controls").hidden = !(next === "memory" && ["body", "isolated"].includes(targetMode));
    setPage("dashboard");
    if (next === "schedule") paintSchedule(value, output);
    else if (next === "memory") paintMemory(value, output);
    else if (next === "questions") paintQuestions(value, output);
    else if (next === "calls") paintCalls(value, output);
    else if (next === "persona_operations") paintPersona(value, output);
    else output.append(rawDetails("查看数据", value));
    stamp();
    return;
  }
  topic = "context";
  const output = byId("observations");
  output.replaceChildren();
  byId("body-controls").hidden = true;
  setPage("dashboard");
  await paintContext(output);
  stamp();
}
async function load(next = topic) {
  if (!targetMode) return;
  byId("observe").classList.add("busy");
  byId("refresh").disabled = true;
  try {
    await show(next);
    feedback("");
  } catch (err) {
    feedback(err.message, true);
    if (["invalid_grant", "connection_changed"].includes(err.code)) connect(null, "观察连接已失效，请重新连接。");
  } finally {
    byId("observe").classList.remove("busy");
    byId("refresh").disabled = false;
  }
}
async function perform(operation) {
  if (working) return;
  working = true;
  document.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  feedback("处理中…");
  try {
    await operation();
    feedback("已完成。");
  } catch (err) {
    feedback(err.message, true);
  } finally {
    working = false;
    document.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    setPage(page);
  }
}
function connect(mode, message, url) {
  closeContext();
  clearTimeout(grantExpiry);
  connectionId = null;
  targetMode = mode; epoch = null; armed = null;
  byId("connection").textContent = message;
  byId("connected-actions").hidden = !mode;
  byId("target").hidden = !url;
  if (url) byId("target").href = url; else byId("target").removeAttribute("href");
  for (const id of ["observations", "body-result", "chat-reply", "p1-output"]) {
    byId(id).replaceChildren();
    if (id !== "observations") byId(id).hidden = true;
  }
  setPage(mode ? "dashboard" : "gate");
  if (!mode) byId("stamp").textContent = "";
}
function showConnection(info) {
  if (info.mode === "isolated") {
    connect("isolated", `隔离实例 · Alice ${info.app_version} · ${info.lab_profile}`, info.url);
    byId("capture").checked = Boolean(info.capture);
    byId("review").checked = Boolean(info.review);
    byId("search-quota").checked = Boolean(info.search_quota);
    byId("search-test-label").hidden = !info.test_controls;
    byId("p1").hidden = !info.lab_experiments;
  } else if (info.mode) {
    connect(info.mode, `日常实例 · 只读${info.mode === "body" ? "（含正文）" : "元数据"} · 至 ${new Date(info.expires_at * 1000).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit"})}`);
    const left = info.expires_at * 1000 - Date.now();
    grantExpiry = setTimeout(() => connect(null, "观察授权已到期，请重新连接。"), Math.max(0, left));
  }
  connectionId = info.connection_id || null;
}

/* ---------- 上下文观测桥 ---------- */
function closeContext() {
  if (byId("context-window").open) byId("context-window").close();
  contextFrame?.remove();
  contextFrame = null;
}
function openContext() {
  if (!connectionId) throw new Error("请先连接 Alice");
  const frame = node("iframe");
  frame.title = "上下文观测";
  frame.allow = "clipboard-write";
  frame.src = "/context-inspector/";
  byId("context-frame-host").replaceChildren(frame);
  contextFrame = frame;
  byId("context-window").showModal();
}
window.AliceLabContext = {
  async read(caller, name, params, signal) {
    if (caller !== contextFrame?.contentWindow || !connectionId) throw new Error("观测窗口已关闭");
    const mapped = {status:"context_status", requests:"context", request:"context", body:"body",
                    call:"context_call", "reply-cache":"reply_cache"};
    if (!Object.hasOwn(mapped, name)) throw new Error("不支持此观测查询");
    const identity = connectionId;
    const bodies = targetMode === "body" || targetMode === "isolated";
    if (["body", "call", "reply-cache"].includes(name) && !bodies) throw new Error("当前连接未授权读取正文");
    try {
      const response = await api("observe", params, {topic:mapped[name], connection_id:identity}, signal);
      if (identity !== connectionId || caller !== contextFrame?.contentWindow) throw new Error("观测连接已变化");
      return response.result;
    } catch (error) {
      if (identity === connectionId && ["invalid_grant", "connection_changed"].includes(error.code)) {
        connect(null, "观察连接已失效，请重新连接。");
      }
      if (error.code === "invalid_read" && name === "status")
        return {status:"ok", can_read_body:bodies, full_call:false, capture:{metadata:null, body:null}};
      if (error.code === "invalid_read" && name === "call")
        throw new Error("当前 Alice 尚未提供完整上下文，请更新后重连。");
      throw error;
    }
  },
};
byId("close-context").onclick = closeContext;
byId("context-window").addEventListener("close", () => {
  if (!byId("context-window").open) closeContext();
});

/* ---------- 事件绑定 ---------- */
async function api(action, params = {}, fields = {}, signal) {
  if (!sessionKey) throw new Error("请从终端重新打开本次 Lab 的完整地址。");
  const response = await fetch("/api", {method:"POST", signal,
    headers:{"Content-Type":"application/json", "X-Alice-Lab":sessionKey},
    body:JSON.stringify({action, params, ...fields})});
  const data = await response.json();
  if (!response.ok || ["error", "refused", "unsupported"].includes(data.status)) {
    const error = new Error(data.error || "操作未完成");
    error.code = data.code;
    throw error;
  }
  return data;
}
byId("pair").onsubmit = (event) => {
  event.preventDefault();
  perform(async () => {
    const input = event.target.elements.key;
    const key = input.value.trim();
    input.value = "";
    showConnection(await api("pair", {key}));
    await show("schedule");
  });
};
byId("start").onsubmit = (event) => {
  event.preventDefault();
  perform(async () => {
    showConnection(await api("start", {test_controls:event.target.elements.relax.checked}));
    await show("schedule");
  });
};
byId("disconnect").onclick = () => perform(async () => {
  await api(targetMode === "isolated" ? "stop" : "disconnect");
  connect(null, "尚未连接");
});
byId("exit-lab").onclick = () => perform(async () => {
  await api("exit_lab");
  connect(null, "Alice Lab 已退出。可从 Alice 设置重新打开。");
  byId("gate").hidden = true;
  byId("exit-lab").remove();
  feedback("Alice Lab 已退出，可以关闭此标签页。");
});
document.querySelectorAll("[data-topic]").forEach((button) => {
  button.onclick = () => load(button.dataset.topic);
});
byId("refresh").onclick = () => load();
byId("bench-entry").onclick = () => setPage("bench");
document.querySelectorAll("[data-control]").forEach((button) => {
  button.onclick = () => perform(async () => {
    await api("control", {}, {command:button.dataset.control});
    await show();
  });
});
for (const [id, command] of [["capture", "capture"], ["review", "review"], ["search-quota", "search_quota"]]) {
  byId(id).onchange = (event) => {
    const box = event.target, enabled = box.checked;
    perform(async () => {
      try { await api("control", {enabled}, {command}); }
      catch (err) { box.checked = !enabled; throw err; }
    });
  };
}
byId("body-form").onsubmit = (event) => {
  event.preventDefault();
  perform(async () => {
    const id = event.target.elements.id.value.trim();
    const data = (await api("observe", {id}, {topic:"memory_detail", connection_id:connectionId})).result;
    byId("body-result").textContent = JSON.stringify(data, null, 2);
    byId("body-result").hidden = false;
  });
};
byId("chat").onsubmit = (event) => {
  event.preventDefault();
  perform(async () => {
    const message = event.target.elements.text;
    if (!epoch) await show("schedule");
    const evaluation = armed;
    armed = null;
    const result = (await api("chat", {text:message.value, session_epoch:epoch,
      ...(evaluation ? {evaluation_id:evaluation} : {})})).result;
    byId("chat-reply").textContent = [result.reply, result.evaluation_warning].filter(Boolean).join("\n");
    byId("chat-reply").hidden = false;
    message.value = "";
  });
};
byId("question").onsubmit = (event) => {
  event.preventDefault();
  perform(async () => {
    const input = event.target.elements.question;
    await api("control", {question:input.value}, {command:"question"});
    input.value = "";
    await show("questions");
  });
};
async function p1(action, params = {}) {
  const state = (await api("experiment", {action:"settings"})).result;
  if (state.status !== "ok") throw new Error(state.error || "核查状态暂不可用");
  const result = (await api("experiment", {...params, action, dataset_id:state.dataset_id})).result;
  if (result.status !== "ok") throw new Error(result.error || "核查操作未完成");
  const output = byId("p1-output");
  output.textContent = JSON.stringify(result, null, 2);
  output.hidden = false;
  return result;
}
byId("p1-list").onclick = () => perform(() => p1("list"));
byId("p1-arm").onclick = () => perform(async () => {
  armed = (await p1("arm")).run?.id || null;
  if (!armed) throw new Error("没有收到本轮核查身份");
});
byId("p1-limit").onsubmit = (event) => {
  event.preventDefault();
  perform(() => p1("set_limit", {daily_limit:Number(event.target.elements.limit.value),
    allow_paid:event.target.elements.paid.checked}));
};
byId("p1-operation").onsubmit = (event) => {
  event.preventDefault();
  perform(() => p1(event.target.elements.action.value, JSON.parse(event.target.elements.params.value)));
};

setPage("gate");
if (sessionKey) perform(async () => {
  const connection = await api("connection");
  showConnection(connection);
  if (connection.mode) await show("schedule");
});
