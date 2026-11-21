"use strict";
const byId = (id) => document.getElementById(id);
const sessionKey = location.hash.slice(1);
if (sessionKey) history.replaceState(null, "", location.pathname);

const modes = {interest:"兴趣探索", problem:"问题思考", iteration:"自我迭代", contact:"主动交流", idle:"发呆"};
const reasons = {weighted_rotation:"按权重抽签", priority_content:"优先事项", explicit_task:"明确指定",
  no_actionable_work:"无可推进事项", activity_finish_rest:"Alice 提议休息", user_requested_rest:"用户安排休息",
  same_material_window:"同材料窗口用尽", mode_disabled:"模式未开启", value_waiting:"等待价值确认",
  new_material_or_curiosity:"新材料或好奇", voluntary_rest:"自愿休息", idle_disabled:"发呆未开启",
  actionable_question:"有可推进问题", no_content:"无内容", contact_opportunity:"可判断主动交流",
  contact_context_reviewed:"已判断本批材料", contact_delivery_mode:"需要选择站内主动消息"};
const layerLabels = {active:"活跃", warehouse:"仓库", longterm:"长期"};
const kindLabels = {event:"事件", stance:"立场"};
const lifecycleLabels = {live:"在用", stale:"陈旧", archived:"已归档", pending:"待定"};
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
const modeColors = {interest:C[1], problem:C[2], iteration:C[3], contact:C[0], idle:C[5]};
const layerColors = {active:C[0], warehouse:C[2], longterm:C[1]};
const phaseColors = {completed:C[0], db_committed:C[0], file_replaced:C[2], prepared:C[2],
  rolled_back:C[4], aborted:C[4], superseded:C[4], unknown:C[5], unreadable:C[4]};
const statusColors = {open:C[2], resolved:C[0], closed:C[5], unknown:C[4]};
const purposeColors = {main:C[1], main_stream:C[1], main_fallback:C[2], extraction:C[3], evolution:C[4],
  distill:C[5], deliberate:C[2], judge:C[0], review:C[3], summary:C[5], vision:C[4], search:C[0]};
function statusColor(key) {
  if (statusColors[key]) return statusColors[key];
  return String(key).startsWith("waiting") ? C[3] : C[4];
}
function purposeColor(value) {
  return purposeColors[value] || "#7c8b83";
}

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
function opTime(id) {
  const m = String(id || "").match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
  return m ? `${m[2]}/${m[3]} ${m[4]}:${m[5]}` : "—";
}

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
/* 细节默认收起：摘要行给出「有多少、多近」，需要核对时再展开。 */
function disclosure(title, meta, content, hint, inline = false) {
  const box = el("details", "more" + (inline ? " inline" : ""));
  const summary = node("summary");
  summary.append(el("span", "more-title", title), el("span", "more-meta", meta || ""));
  const body = el("div", "more-body");
  if (hint) body.append(el("p", "more-hint", hint));
  body.append(content);
  box.append(summary, body);
  return box;
}
function emptyNote(text) {
  return el("p", "empty", text);
}
/* ---------- 图表语法 ---------- */
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
  return n;
}
function donut(segments, opts = {}) {
  const alive = segments.filter((s) => (s.value || 0) > 0);
  const total = alive.reduce((a, s) => a + s.value, 0);
  if (!total) return emptyNote("暂无数据。");
  const size = 128, width = 13, r = (size - width) / 2 - 3, cx = size / 2, len = 2 * Math.PI * r;
  const chart = sv("svg", {viewBox:`0 0 ${size} ${size}`, class:"donut-ring", role:"img",
    "aria-label": alive.map((s) => `${s.label} ${fmt.int(s.value)}，占 ${fmt.pct(s.value / total)}`).join("；")});
  chart.append(sv("circle", {cx, cy:cx, r, fill:"none", stroke:"#e9ede4", "stroke-width":width}));
  let offset = 0;
  for (const seg of alive) {
    const span = (seg.value / total) * len, drawn = Math.max(span - 1.2, 0.4);
    const arc = sv("circle", {cx, cy:cx, r, fill:"none", stroke:seg.color, "stroke-width":width,
      "stroke-dasharray":`${drawn} ${len - drawn}`, "stroke-dashoffset":-offset,
      transform:`rotate(-90 ${cx} ${cx})`});
    const tip = sv("title");
    tip.textContent = `${seg.label} ${fmt.int(seg.value)} · ${fmt.pct(seg.value / total)}`;
    arc.append(tip);
    chart.append(arc);
    offset += span;
  }
  const big = sv("text", {x:cx, y:cx + 1, "text-anchor":"middle", "font-size":21, "font-weight":600, fill:"#182722"});
  big.textContent = opts.centerValue ?? fmt.int(total);
  const small = sv("text", {x:cx, y:cx + 18, "text-anchor":"middle", "font-size":10.5, fill:"#4c6158"});
  small.textContent = opts.centerLabel || "合计";
  chart.append(big, small);
  const legend = el("div", "donut-legend");
  for (const seg of segments) {
    if (!(seg.value > 0)) continue;
    const row = el("div", "dl-row"), dot = el("span", "dot");
    dot.style.background = seg.color;
    row.append(dot, el("span", "dl-name", seg.label), el("b", "dl-pct", fmt.pct(seg.value / total)));
    if (seg.note) row.append(el("span", "dl-note", seg.note));
    legend.append(row);
  }
  const wrap = el("div", "donut");
  wrap.append(chart, legend);
  return wrap;
}
function waterfall(steps) {
  let run = 0;
  const marks = steps.map((step) => {
    if (step.kind === "total") return {bottom:0, height:run, at:run};
    if (step.kind === "add") {
      const mark = {bottom:run, height:step.value || 0};
      run += step.value || 0;
      mark.at = run;
      return mark;
    }
    run = step.value || 0;
    return {bottom:0, height:run, at:run};
  });
  const max = Math.max(...marks.map((m) => m.bottom + m.height), 1);
  const plot = el("div", "wf"), axis = el("div", "wf-axis");
  steps.forEach((step, i) => {
    const mark = marks[i], col = el("div", "wf-col"), gap = el("div", "wf-gap");
    gap.style.height = ((mark.bottom / max) * 100).toFixed(2) + "%";
    const bar = el("div", "wf-bar" + (step.kind === "total" ? " total" : ""));
    bar.style.height = ((mark.height / max) * 100).toFixed(2) + "%";
    bar.style.background = step.color;
    bar.title = `${step.label} ${fmt.int(step.value)}${step.kind === "add" ? `（累计 ${fmt.int(mark.at)}）` : ""}`;
    bar.append(el("span", "wf-val", (step.kind === "add" && step.value ? "+" : "") + fmt.int(step.value)));
    col.append(gap, bar);
    plot.append(col);
    axis.append(el("span", "wf-lab", step.label));
  });
  const wrap = el("div", "wf-wrap");
  wrap.append(plot, axis);
  return wrap;
}
function ribbon(items, opts = {}) {
  const bar = el("div", "ribbon");
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", opts.label || "");
  for (const item of items) {
    const seg = el("span", "ribbon-seg");
    seg.style.background = item.color;
    seg.title = item.title || "";
    bar.append(seg);
  }
  const wrap = el("div", "ribbon-wrap");
  wrap.append(bar);
  if (items.length && (opts.start || opts.end)) {
    const axis = el("div", "ribbon-axis");
    axis.append(el("span", "", opts.start || ""), el("span", "", opts.end || ""));
    wrap.append(axis);
  }
  return wrap;
}
function scatter(points, opts = {}) {
  const maxX = Math.max(...points.map((p) => p.x || 0), 1);
  const maxY = Math.max(...points.map((p) => p.y || 0), 1);
  const wrap = el("div", "scatter"), ycol = el("div", "sc-y");
  ycol.append(el("span", "", fmt.int(maxY)), el("span", "", "0"));
  const plot = el("div", "sc-plot");
  plot.setAttribute("role", "img");
  plot.setAttribute("aria-label", opts.label || "散点图");
  for (let i = 1; i < 4; i += 1) {
    const grid = el("div", "sc-grid");
    grid.style.bottom = i * 25 + "%";
    plot.append(grid);
  }
  const buckets = new Map();
  for (const p of points) {
    const px = (p.x || 0) / maxX, py = (p.y || 0) / maxY;
    const key = `${Math.round(px * 80)}:${Math.round(py * 80)}`;
    const slot = buckets.get(key) || {px:0, py:0, n:0, head:p, ring:false};
    slot.px += px;
    slot.py += py;
    slot.n += 1;
    slot.ring = slot.ring || Boolean(p.ring);
    buckets.set(key, slot);
  }
  for (const slot of buckets.values()) {
    const px = slot.px / slot.n, py = slot.py / slot.n;
    const size = Math.min(18, 9 + Math.sqrt(slot.n - 1) * 3.2);
    const dot = el("span", "sc-dot" + (slot.ring || slot.head.ring ? " imp" : ""));
    dot.style.left = `calc(5px + (100% - 10px) * ${px.toFixed(4)})`;
    dot.style.bottom = `calc(5px + (100% - 10px) * ${py.toFixed(4)})`;
    dot.style.width = size.toFixed(1) + "px";
    dot.style.height = size.toFixed(1) + "px";
    dot.style.background = slot.head.color;
    dot.title = slot.n > 1 ? `${slot.n} 条记忆数值接近` : slot.head.title;
    plot.append(dot);
  }
  const xaxis = el("div", "sc-x");
  xaxis.append(el("span", "", "0"), el("span", "", `${opts.xName || "横轴"} ${fmt.int(maxX)}`));
  const right = el("div", "sc-right");
  right.append(plot, xaxis);
  wrap.append(ycol, right);
  return wrap;
}
function heatmap(rowLabels, colLabels, values) {
  const max = Math.max(...values.flat(), 1);
  const grid = el("div", "heat");
  grid.style.gridTemplateColumns = `minmax(46px,auto) repeat(${colLabels.length}, minmax(30px,1fr))`;
  grid.append(el("span", "heat-corner"));
  for (const label of colLabels) grid.append(el("span", "heat-col", label));
  rowLabels.forEach((label, r) => {
    grid.append(el("span", "heat-row", label));
    colLabels.forEach((col, c) => {
      const value = values[r][c] || 0, cell = el("span", "heat-cell", value ? fmt.int(value) : "");
      if (value) {
        const k = 0.12 + 0.76 * (value / max);
        cell.style.background = `rgba(47,122,99,${k.toFixed(2)})`;
        if (k > 0.52) cell.style.color = "#fff";
      }
      cell.title = `${label} · ${col}：${value}`;
      grid.append(cell);
    });
  });
  const wrap = el("div", "heat-wrap");
  wrap.append(grid);
  return wrap;
}
function histogram(values, edges, labels) {
  const counts = new Array(edges.length + 1).fill(0);
  for (const value of values) {
    let index = edges.findIndex((edge) => value < edge);
    if (index < 0) index = edges.length;
    counts[index] += 1;
  }
  const max = Math.max(...counts, 1);
  const plot = el("div", "hist");
  counts.forEach((count, i) => {
    const col = el("div", "hist-col");
    col.append(el("span", "hist-val", fmt.int(count)));
    const bar = el("div", "hist-bar");
    bar.style.height = Math.max(count ? 3 : 1, (count / max) * 100) + "%";
    bar.style.background = i === counts.length - 1 ? C[4] : C[1];
    bar.style.opacity = count ? "1" : ".3";
    bar.title = `${labels[i]}：${count} 次`;
    col.append(bar);
    plot.append(col);
  });
  const axis = el("div", "hist-axis");
  for (const label of labels) axis.append(el("span", "", label));
  const wrap = el("div", "hist-wrap");
  wrap.append(plot, axis);
  return wrap;
}
function lollipop(items) {
  const max = Math.max(...items.map((i) => i.value || 0), 1);
  const wrap = el("div", "lol");
  for (const item of items) {
    const row = el("div", "lol-row"), name = el("span", "lol-name", item.label);
    name.title = item.label;
    const pct = Math.max(2, ((item.value || 0) / max) * 100);
    const track = el("div", "lol-track"), line = el("span", "lol-line"), dot = el("span", "lol-dot");
    line.style.width = pct + "%";
    line.style.background = item.color || C[1];
    dot.style.left = pct + "%";
    dot.style.background = item.color || C[1];
    track.append(line, dot);
    const note = el("span", "lol-note", item.note ?? fmt.int(item.value));
    note.title = item.note ?? "";
    row.append(name, track, note);
    wrap.append(row);
  }
  return wrap;
}
function unitGrid(cells, label) {
  const wrap = el("div", "units");
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", label || "");
  for (const cell of cells) {
    const unit = el("span", "unit");
    unit.style.background = cell.color;
    unit.title = cell.title || "";
    wrap.append(unit);
  }
  return wrap;
}
function layerColumn(segments) {
  const total = segments.reduce((a, s) => a + (s.value || 0), 0);
  if (!total) return emptyNote("暂无记忆。");
  const wrap = el("div", "vlayers"), col = el("div", "vlayer");
  col.setAttribute("role", "img");
  col.setAttribute("aria-label", segments.map((s) => `${s.label} ${fmt.int(s.value)}`).join("；"));
  for (const seg of segments) {
    if (!seg.value) continue;
    const part = el("span");
    part.style.height = ((seg.value / total) * 100).toFixed(2) + "%";
    part.style.background = seg.color;
    part.title = `${seg.label} ${fmt.int(seg.value)} · ${fmt.pct(seg.value / total)}`;
    col.append(part);
  }
  const legend = el("div", "donut-legend");
  for (const seg of segments) {
    const row = el("div", "dl-row"), dot = el("span", "dot");
    dot.style.background = seg.color;
    row.append(dot, el("span", "dl-name", seg.label), el("b", "dl-pct", fmt.pct((seg.value || 0) / total)));
    legend.append(row);
  }
  wrap.append(col, legend);
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
/* ---------- 字段翻译：内部字段名与代码值 → 日常说法 ---------- */
const FIELD_LABELS = {
  id:"编号", ref:"引用", version:"版本", detail_state:"详情状态", name:"名称", seq:"顺序",
  created_at:"创建时间", updated_at:"更新时间", last_used_at:"最近使用", promoted_at:"晋升时间",
  expires_at:"到期时间", expired_at:"过期时间", due_at:"到期", sampled_at:"采样时间",
  first_event_at:"首个事件", last_event_at:"末个事件", at:"发生时间", ts:"发生时间",
  layer:"所在层", kind:"类型", salience:"重大事项", pressure:"压力标记",
  mention_count:"被提及", use_count:"被使用", origin:"来源", scope:"范围",
  lifecycle:"生命周期", provenance_status:"溯源状态", title:"标题", content:"正文",
  activity:"活动模式", reason:"原因", schedule_reason:"调度说明", draw_weights:"抽签权重",
  dry_windows:"连续无进展", blocked_modes:"受阻模式数", content_score:"内容分",
  importance:"重要度", urgency:"紧迫度", value_waiting:"等待确认",
  question:"疑问内容", status:"状态", next_step:"下一步", progress:"进展",
  parent_id:"上级编号", outcome_id:"结论编号", basis:"依据",
  op_id:"操作号", phase:"阶段", epoch_matches:"属于本会话", db_marker_present:"库内有标记",
  inserted_count:"已写入条数", before_sha256:"变更前指纹", after_sha256:"变更后指纹",
  call_id:"调用号", request_id:"请求号", request_seq:"请求序号", session_epoch:"会话代号",
  purpose:"用途", model:"模型", model_source:"模型来源", duration_ms:"耗时",
  usage:"用量", usage_state:"用量状态", attempt:"第几次尝试", tool_round:"工具轮次",
  error_type:"错误类型", fallback:"回退", storage:"存储状态", response_started:"已开始响应",
  counts:"分层计数", items:"条目", notice:"说明", pending_dropped:"已丢弃",
  scan_limit:"扫描上限", truncated:"是否截断", scan_incomplete:"扫描不完整",
  total_chars:"总字数", count:"条数", chars:"字数", estimated_tokens:"估算 token",
  finished:"已结束", event_count:"事件数", blocks:"装配块", selections:"取舍决策",
  gaps:"采集缺口", delivery:"投递", assistant_turn_id:"回复编号", sdk_dropped:"SDK 丢弃",
  body:"正文留存", body_state:"正文状态", content_hash:"内容指纹", messages_hash:"消息指纹",
  start:"起点", end:"终点", message_seq:"消息序号", block_seq:"装配块序号",
  role:"角色", input:"输入", tools:"工具清单", parameters:"参数", bindings:"绑定",
  order:"消息顺序", prompt:"输入", completion:"输出", model_tokens:"模型 token",
  source_id:"来源编号", included:"是否采用", modes:"各模式权重", mode:"模式",
  token:"操作令牌", source_hash:"来源指纹", before_version:"变更前版本", after_version:"变更后版本",
  projection_chars:"投影字数", rollback_token:"回滚令牌", rollback_of:"回滚自", format:"格式",
  metadata:"元信息",
};
const MEMORY_ORIGINS = {conversation:"对话", chat:"对话", document:"文档", web:"网页",
  manual:"手动", import:"导入", extraction:"提取", discovery:"发现", system:"系统"};
const PROVENANCE = {valid:"有效", confirmed:"已确认", unverified:"未核实",
  legacy_unverified:"旧数据未核", needs_review:"待复核", invalid:"已失效", superseded:"已被取代"};
const SCOPES = {local:"本机", global:"全局", gui:"界面", shared:"共享", private:"私有"};
const EXPR_STATUS = {applied:"已生效", preparing:"准备中", prepare_failed:"准备失败",
  state_unconfirmed:"状态未确认", unreadable:"不可读", rolled_back:"已回滚"};
const BODY_STATES = {stored:"已留存", head:"仅留开头", on_disk:"已存磁盘", none:"未留存",
  historical_not_captured:"当时未采集", rolling_capture:"滚动采集", not_captured:"未采集"};
const FIELD_ENUMS = {
  layer: layerLabels, kind: {...kindLabels, question:"疑问"},
  lifecycle: lifecycleLabels, phase: phaseLabels,
  purpose: purposeLabels, activity: modes, reason: reasons,
  origin: {...originLabels, ...MEMORY_ORIGINS},
  body: BODY_STATES, body_state: BODY_STATES,
  storage: {committed:"已落库", pending:"待写入"},
  usage_state: {ok:"已回执", unknown:"未知", missing:"缺失", unsupported:"不支持"},
  status: {...questionStatus, ...callStatusLabels, ...EXPR_STATUS, entered:"已开始"},
  role: {user:"用户", assistant:"Alice", system:"系统", tool:"工具"},
  salience: {0:"否", 1:"是"}, pressure: {0:"否", 1:"是"},
  included: {true:"已采用", false:"未采用"},
  provenance_status: PROVENANCE, scope: SCOPES,
};
const VALUE_LABELS = {
  ok:"正常", error:"失败", cancelled:"取消", stream_interrupted:"流中断", entered:"已开始",
  none:"无", unknown:"未知", unsupported:"不支持", missing:"缺失",
  committed:"已落库", pending:"待写入", stored:"已留存", head:"仅留开头", on_disk:"已存磁盘",
  historical_not_captured:"当时未采集", rolling_capture:"滚动采集", not_captured:"未采集",
};
const TIME_KEY = /(^|_)(at|ts)$/;
function humanize(key) { return String(key).replace(/_/g, " "); }
function fieldLabel(key) {
  return FIELD_LABELS[key] || modes[key] || layerLabels[key] || kindLabels[key] || humanize(key);
}
function fieldValue(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  const table = FIELD_ENUMS[key];
  if (table && table[value] !== undefined) return table[value];
  if (VALUE_LABELS[value] !== undefined && typeof value === "string") return VALUE_LABELS[value];
  if (typeof value === "number") {
    if (TIME_KEY.test(key)) return value ? fmt.time(value) : (/expire|due/.test(key) ? "无期限" : "—");
    if (/_ms$/.test(key)) return fmt.dur(value);
    if (/tokens?$/.test(key)) return fmt.tok(value) + " tok";
    if (key === "content_score" || key === "importance" || key === "urgency") return String(value) + " / 3";
    return fmt.int(value);
  }
  if (Array.isArray(value)) return value.length ? `${value.length} 项` : "—";
  if (typeof value === "string" && /^\[.*\]$/.test(value.trim())) {
    try {
      const list = JSON.parse(value);
      if (Array.isArray(list) && list.length) {
        return list.map((v) => (SCOPES[v] || String(v))).join("、");
      }
    } catch (err) { /* 不是 JSON 就按普通文本处理 */ }
  }
  if (typeof value === "object") return "见下方分组";
  const text = String(value);
  if (/sha256$/.test(key)) return text.slice(0, 8) + "…";
  return text;
}
function isFlat(obj) {
  return !!obj && typeof obj === "object" && !Array.isArray(obj)
    && Object.values(obj).every((v) => v === null || typeof v !== "object");
}
function pairText(obj) {
  return Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${fieldLabel(k)} ${fieldValue(k, v)}`).join(" · ") || "—";
}
function fieldCell(key, text) {
  const cell = el("div", "field");
  cell.append(el("span", "field-k", fieldLabel(key)), el("span", "field-v", text));
  return cell;
}
function fieldTable(obj, depth = 0, skip = []) {
  const box = el("div", "fields");
  const nested = [];
  for (const [key, value] of Object.entries(obj || {})) {
    if (value === undefined || skip.includes(key)) continue;
    if ((value && typeof value === "object" && !Object.keys(value).length)) continue;
    if (isFlat(value)) {
      const text = pairText(value);
      if (text !== "—") box.append(fieldCell(key, text));
    } else if (Array.isArray(value)) {
      const records = value.filter((v) => v && typeof v === "object");
      const text = !value.length ? ""
        : records.length ? `${value.length} 条记录`
        : value.slice(0, 8).map((v) => String(v)).join("、") + (value.length > 8 ? ` 等 ${value.length} 项` : "");
      if (text) box.append(fieldCell(key, text));
      if (records.length && depth < 2) nested.push([key, records]);
    } else if (value && typeof value === "object") {
      box.append(fieldCell(key, "分组"));
      if (depth < 2) nested.push([key, [value]]);
    } else {
      const text = fieldValue(key, value);
      if (text !== "—") box.append(fieldCell(key, text));
    }
  }
  for (const [key, records] of nested) {
    const group = el("div", "field-sub");
    group.append(el("div", "field-sub-head", fieldLabel(key)));
    for (const record of records.slice(0, 20)) group.append(fieldTable(record, depth + 1));
    if (records.length > 20) group.append(el("p", "field-hint", `另有 ${records.length - 20} 条未展开。`));
    box.append(group);
  }
  return box;
}
function recordTitle(obj) {
  const bits = [];
  const skip = [];
  const identKey = ["id", "op_id", "call_id", "request_id"].find((k) => obj[k] != null);
  if (identKey) {
    const text = String(obj[identKey]);
    bits.push(text.length > 20 ? text.slice(0, 20) + "…" : text);
    if (identKey !== "request_id") skip.push(identKey);
  }
  if (obj.purpose) { bits.push(purposeLabel(obj.purpose)); skip.push("purpose"); }
  else if (obj.activity) { bits.push(modes[obj.activity] || obj.activity); skip.push("activity"); }
  else if (obj.phase) { bits.push(phaseLabels[obj.phase] || obj.phase); skip.push("phase"); }
  else if (obj.name) bits.push(String(obj.name));
  else if (obj.question) {
    const text = String(obj.question);
    bits.push(text.length > 26 ? text.slice(0, 26) + "…" : text);
    skip.push("question");
  }
  if (obj.status) { bits.push(statusLabel(obj.status)); skip.push("status"); }
  else if (obj.lifecycle) { bits.push(lifecycleLabels[obj.lifecycle] || obj.lifecycle); skip.push("lifecycle"); }
  return { title: bits.join(" · ") || "记录", skip };
}
function fieldRecords(value) {
  const box = el("div", "field-body");
  const records = Array.isArray(value) ? value : (value && typeof value === "object" ? [value] : []);
  if (!records.length) return box;
  box.append(el("p", "field-hint", "内部字段名和代码值已翻译成日常说法。"));
  records.slice(0, 20).forEach((record, index) => {
    const item = el("div", "field-item");
    if (!record || typeof record !== "object") {
      item.append(el("span", "field-v", String(record)));
    } else {
      const { title, skip } = recordTitle(record);
      const head = el("div", "field-head");
      head.append(el("span", "field-index", String(index + 1).padStart(2, "0")));
      head.append(el("span", "field-title", title));
      item.append(head, fieldTable(record, 0, skip));
    }
    box.append(item);
  });
  if (records.length > 20) box.append(el("p", "field-hint", `另有 ${records.length - 20} 条未展开，可在上方明细表里按序号查看。`));
  return box;
}
function dataDetails(label, value, opts = {}) {
  const block = el("details", "raw" + (opts.inline ? " inline" : ""));
  block.append(node("summary", label));
  let built = false;
  const build = () => {
    if (built) return;
    built = true;
    block.append(fieldRecords(value));
  };
  block.addEventListener("toggle", () => { if (block.open) build(); });
  if (opts.open) { build(); block.open = true; }
  return block;
}
function renderValue(target, value) {
  target.replaceChildren(fieldRecords(value));
  target.hidden = false;
}
function line(label, value) {
  const row = el("div", "line");
  row.append(node("span", label), node("b", value));
  return row;
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
function statusLabel(value) {
  if (!value) return "—";
  if (questionStatus[value]) return questionStatus[value];
  if (callStatusLabels[value]) return callStatusLabels[value];
  if (EXPR_STATUS[value]) return EXPR_STATUS[value];
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

  const weights = drawable ? cur.draw_weights
    : Object.fromEntries(keys.map((key) => [key, info(key).weight || 0]));
  const weightTotal = Object.keys(weights).reduce((a, key) => a + (weights[key] || 0), 0);
  const topKey = Object.keys(weights).reduce((best, key) => ((weights[key] || 0) > (weights[best] || 0) ? key : best), keys[0]);
  const weightCard = card("抽签权重", drawable
    ? "按权重归一化得到抽中概率；内容分来自问题重要度/紧急度与轮转债。"
    : "本次存在优先事项，直接指定，不进入抽签。");
  weightCard.body.append(donut(keys.map((key) => ({
    label: modes[key],
    value: weights[key] || 0,
    color: modeColors[key],
    note: drawable ? `内容分 ${info(key).content_score ?? "—"}`
      : `权重 ${info(key).weight ?? "—"} · 内容分 ${info(key).content_score ?? "—"}`,
  })), {
    centerValue: drawable ? probability(cur.draw_weights, topKey) : fmt.int(weightTotal),
    centerLabel: drawable ? modes[topKey] : "权重合计",
  }));
  if (!drawable && weightTotal) weightCard.body.append(el("p", "note", "没有 draw_weights：优先事项或明确指定时不会抽签。"));

  const dry = cur.dry_windows || 0, blocked = cur.blocked_modes || 0;
  const dryAdd = 2 * Math.min(dry, 3), blockAdd = 2 * Math.min(blocked, 3);
  const restCard = card("发呆分构成", "发呆分 = 1 + 2×无进展轮（最多 3）+ 2×受阻模式（最多 3）。");
  restCard.body.append(waterfall([
    {label:"基数", value:1, kind:"base", color:C[5]},
    {label:"无进展", value:dryAdd, kind:"add", color:C[2]},
    {label:"受阻", value:blockAdd, kind:"add", color:C[4]},
    {label:"合计", value:1 + dryAdd + blockAdd, kind:"total", color:C[0]},
  ]));
  restCard.body.append(line("发呆权重", String(info("idle").weight ?? "—")));
  restCard.body.append(line("发呆秒数", cur.idle_seconds ? cur.idle_seconds + "s" : "未开启"));

  const recentCard = card("最近选择", "最近一次在下面；色带是最近 12 次的模式序列，单次留痕收在页尾。");
  if (!recs.length) recentCard.body.append(emptyNote("还没有运行记录。"));
  else {
    const latest = recs[0];
    const pick = el("div", "pick");
    pick.append(el("span", "pick-label", "最近一次"), el("span", "pick-time", fmt.clock(latest.at)),
      el("b", null, modes[latest.activity] || latest.activity));
    pick.append(pill(reasons[latest.reason] || latest.reason || "原因未记录",
      latest.reason === "weighted_rotation" ? "ok" : ""));
    const lastChance = probability(latest.draw_weights, latest.activity);
    if (lastChance !== "—") pick.append(pill("概率 " + lastChance));
    recentCard.body.append(pick);
    const seq = recs.slice(0, 12).slice().reverse();
    recentCard.body.append(ribbon(seq.map((item) => ({
      color: modeColors[item.activity] || C[5],
      title: `${fmt.clock(item.at)} ${modes[item.activity] || item.activity} · ${reasons[item.reason] || "原因未记录"}`,
    })), {label:"最近选择的模式序列", start:fmt.clock(seq[0].at), end:fmt.clock(seq[seq.length - 1].at)}));
    const usedModes = [...new Set(seq.map((item) => item.activity))];
    const modeLegend = el("div", "legend");
    for (const key of usedModes) {
      const item = el("span"), dot = el("span", "dot");
      dot.style.background = modeColors[key] || C[5];
      item.append(dot, node("span", modes[key] || key));
      modeLegend.append(item);
    }
    recentCard.body.append(modeLegend);
  }

  mountCards(out, [
    {box: weightCard.box}, {box: restCard.box}, {box: recentCard.box, wide:true},
  ]);
  if (recs.length) {
    const detail = el("div");
    detail.append(table(["时间", "活动", "原因", "当时概率", "无进展"], recs.map((item) => [
      fmt.time(item.at), modes[item.activity] || item.activity, reasons[item.reason] || item.reason || "—",
      probability(item.draw_weights, item.activity), item.dry_windows ?? "—",
    ])), dataDetails("查看全部字段", recs));
    out.append(disclosure("决策留痕", `${recs.length} 条 · 最近 ${fmt.clock(recs[0].at)}`,
      detail, "每次选择的当时概率与抽签池；按时间倒序。"));
  }
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
  layerCard.body.append(layerColumn([
    {label:"活跃", value:counts.active || 0, color:C[0]},
    {label:"仓库", value:counts.warehouse || 0, color:C[2]},
    {label:"长期", value:counts.longterm || 0, color:C[1]},
  ]));
  const weekAgo = Date.now() / 1000 - 7 * 86400;
  const usedThisWeek = items.filter((i) => i.last_used_at && i.last_used_at >= weekAgo).length;
  const soonest = items.reduce((min, i) => (i.expires_at && (!min || i.expires_at < min) ? i.expires_at : min), null);
  layerCard.body.append(line("近 7 天有取用", `${usedThisWeek} 条`));
  layerCard.body.append(line("最近到期", soonest ? fmt.time(soonest) : "无期限"));
  const bodyAllowed = targetMode === "body" || targetMode === "isolated";
  layerCard.body.append(el("p", "card-sub auto-bottom", bodyAllowed
    ? "正文授权：含正文，可按 ID 读取。"
    : "正文授权：仅元数据；标题与正文需在 Alice 单独勾选。"));

  const heatCard = card("提及 × 取用", "横轴被提及次数，纵轴被取用次数；带圈的是重大事项。右下角 = 说过很多次、却很少用到。");
  heatCard.body.append(items.length
    ? scatter(items.map((i) => ({
        x:i.mention_count || 0, y:i.use_count || 0,
        color:layerColors[i.layer] || C[5],
        ring:Boolean(i.salience),
        title:`#${i.id} ${kindLabels[i.kind] || ""} · 提及 ${fmt.int(i.mention_count)} · 取用 ${fmt.int(i.use_count)}${i.salience ? " · 重要" : ""}`,
      })), {xName:"提及", label:`${items.length} 条记忆的提及与取用分布`})
    : emptyNote("还没有记忆。"));
  if (items.length) {
    const mix = el("div", "legend");
    for (const key of Object.keys(layerLabels)) {
      const item = el("span"), dot = el("span", "dot");
      dot.style.background = layerColors[key];
      item.append(dot, node("span", layerLabels[key]));
      mix.append(item);
    }
    const imp = el("span");
    imp.append(el("span", "dot ring"), node("span", "重大事项"));
    mix.append(imp);
    heatCard.body.append(mix);
    const sumMention = items.reduce((a, i) => a + (i.mention_count || 0), 0);
    const sumUse = items.reduce((a, i) => a + (i.use_count || 0), 0);
    const never = items.filter((i) => !i.use_count).length;
    const talky = items.filter((i) => (i.mention_count || 0) >= 5 && (i.use_count || 0) <= 1).length;
    const stats = el("div", "rows stats");
    stats.append(line("合计提及 / 取用", `${fmt.int(sumMention)} / ${fmt.int(sumUse)}`));
    stats.append(line("从未被取用", `${never} 条`));
    stats.append(line("提及多、取用少", `${talky} 条`));
    heatCard.body.append(stats);
    heatCard.body.append(el("p", "note",
      "salience 只有 0/1 两档，不是连续价值分；点越大，表示落在附近的记忆越多。"));
  }

  const pair = el("div", "grid equal");
  pair.append(layerCard.box, heatCard.box);
  out.append(pair);
  if (items.length) {
    const lastUse = items.reduce((max, i) => Math.max(max, i.last_used_at || 0), 0);
    const detail = el("div");
    detail.append(table(["#", "层", "类型", "重要", "提及", "使用", "最近使用", "到期", "状态"], items.map((i) => [
      String(i.id), layerLabels[i.layer] || i.layer || "—", kindLabels[i.kind] || i.kind || "—",
      i.salience ? pill("重要", "warn") : "—", fmt.int(i.mention_count), fmt.int(i.use_count),
      fmt.ago(i.last_used_at), i.expires_at ? fmt.time(i.expires_at) : "—",
      lifecycleLabels[i.lifecycle] || i.lifecycle || i.provenance_status || "—",
    ]), {cards:true}), dataDetails("查看全部字段", items));
    out.append(disclosure("最近记忆", `${items.length} 条 · 最近取用 ${lastUse ? fmt.ago(lastUse) : "无"}`,
      detail, "按 id 倒序；只有元数据，正文需单独授权。"));
  }
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
  const rank = (status) => status === "open" ? 2 : String(status || "").startsWith("waiting") ? 1 : 0;
  const ordered = [...items].sort((a, b) =>
    rank(b.status) - rank(a.status) || (b.updated_at || 0) - (a.updated_at || 0));
  out.append(kpis([
    {label:"疑问总数", value: fmt.int(items.length), hint:"最多 40 条"},
    {label:"待处理", value: fmt.int(open)},
    {label:"来源种类", value: fmt.int(Object.keys(origins).length)},
    {label:"最近更新", value: latest ? fmt.ago(latest) : "—"},
  ]));

  const statusKeys = Object.keys(statuses).sort((a, b) => statuses[b] - statuses[a]);
  const originKeys = Object.keys(origins).sort((a, b) => origins[b] - origins[a]);
  const statusCard = card("状态分布", "waiting* 表示在等外部条件，不是卡住。");
  statusCard.body.append(statusKeys.length
    ? donut(statusKeys.map((key) => ({label:statusLabel(key), value:statuses[key], color:statusColor(key)})),
        {centerValue:fmt.int(items.length), centerLabel:"疑问"})
    : emptyNote("暂无疑问。"));

  const originCard = card("来源 × 状态", "行是来源，列是状态；格子越深，数量越多。");
  originCard.body.append(originKeys.length && statusKeys.length
    ? heatmap(originKeys.map((key) => originLabels[key] || key), statusKeys.map((key) => statusLabel(key)),
        originKeys.map((origin) => statusKeys.map((status) =>
          items.filter((i) => (i.origin || "unknown") === origin && (i.status || "unknown") === status).length)))
    : emptyNote("暂无来源记录。"));

  const listCard = card("疑问清单", "待处理优先，其余按更新时间；默认显示 6 条。");
  if (!items.length) listCard.body.append(emptyNote("当前会话没有疑问。"));
  else {
    const questionRow = (item) => {
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
        button.className = "quiet explore";
        button.style.marginTop = "6px";
        button.onclick = () => perform(async () => {
          await api("control", {id:item.id, version:item.version}, {command:"run"});
        });
        body.append(button);
      }
      box.append(left, dotEl, body);
      return box;
    };
    const wrap = el("div", "rows");
    for (const item of ordered.slice(0, 6)) wrap.append(questionRow(item));
    listCard.body.append(wrap);
    if (items.length > 8) {
      const rest = el("div", "rows");
      for (const item of ordered.slice(6)) rest.append(questionRow(item));
      listCard.body.append(disclosure(`其余 ${items.length - 6} 条`, "按需要展开", rest, null, true));
    }
    if (targetMode !== "isolated") {
      listCard.body.append(el("p", "note", "日常连接只读：推进疑问要在隔离实例里操作，这里只显示状态。"));
    }
    listCard.body.append(dataDetails("查看全部字段", ordered));
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

  const peak = Math.max(...items.map((i) => i.duration_ms || 0), 1);
  const useLog = peak > Math.max(median || 1, 1) * 5 && peak > 20000;
  const heightOf = (value) => useLog
    ? Math.log10(1 + Math.max(0, value)) / Math.log10(1 + peak)
    : Math.max(0, value) / peak;
  const trendCard = card("耗时趋势", useLog
    ? `每次模型调用一根柱，左旧右新；存在超长调用（峰值 ${fmt.dur(peak)}），高度按对数压缩。`
    : "每次模型调用一根柱，左旧右新；虚线是本次样本的中位数。");
  if (!items.length) trendCard.body.append(emptyNote("还没有调用回执。"));
  else {
    const cols = el("div", "cols");
    for (const item of items) {
      const bar = el("div", "col-bar");
      bar.style.height = Math.max(2, heightOf(item.duration_ms || 0) * 100) + "%";
      bar.style.background = item.status && item.status !== "ok" ? C[4] : item.attempt > 1 ? C[2] : C[1];
      bar.title = `${fmt.clock(item.ts)} · ${purposeLabels[item.purpose] || item.purpose} · ${fmt.dur(item.duration_ms)}${item.status && item.status !== "ok" ? " · " + item.status : ""}`;
      cols.append(bar);
    }
    if (median) {
      const ref = el("div", "ref");
      ref.style.bottom = (heightOf(median) * 100).toFixed(1) + "%";
      ref.append(el("span", "ref-lab", "中位 " + fmt.dur(median)));
      cols.append(ref);
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
    const dist = el("div", "trend-extra");
    dist.append(el("div", "mini-head", "耗时分布"),
      el("p", "mini-hint", "按调用次数分桶；最后一桶 ≥30s。"));
    dist.append(durations.length
      ? histogram(durations, [1000, 3000, 10000, 30000], ["<1s", "1–3s", "3–10s", "10–30s", "≥30s"])
      : emptyNote("还没有带耗时的回执。"));
    trendCard.body.append(dist);
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
  const purposeCard = card("用途分布", "一格代表一次调用；颜色是用途，悬停看时间与耗时。");
  purposeCard.body.append(items.length
    ? unitGrid(items.map((i) => ({
        color:purposeColor(i.purpose),
        title:`${fmt.clock(i.ts)} · ${purposeLabel(i.purpose)} · ${fmt.dur(i.duration_ms)}`,
      })), `${items.length} 次调用的用途`)
    : emptyNote("暂无调用。"));
  if (items.length) {
    const used = el("div", "legend");
    for (const [key, slot] of Object.entries(purposeMap).sort((a, b) => b[1].count - a[1].count)) {
      const item = el("span"), dot = el("span", "dot");
      dot.style.background = purposeColor(key);
      item.append(dot, node("span", `${purposeLabel(key)} ${slot.count} · ${fmt.tok(slot.tokens)}`));
      used.append(item);
    }
    purposeCard.body.append(used);
  }

  const modelMap = {};
  for (const i of items) {
    const key = i.model || "未知";
    const slot = modelMap[key] || (modelMap[key] = {count:0, tokens:0, ms:0});
    slot.count += 1;
    slot.ms += i.duration_ms || 0;
    const u = i.usage;
    if (u && typeof u === "object") slot.tokens += (u.prompt || 0) + (u.completion || 0);
  }
  const modelCard = card("模型分布", "回执里报告的模型；点越靠右，调用越多。");
  modelCard.body.append(Object.keys(modelMap).length
    ? lollipop(Object.entries(modelMap).sort((a, b) => b[1].count - a[1].count).map(([key, slot]) => ({
        label:key, value:slot.count, color:C[1],
        note:`${slot.count} 次 · ${fmt.tok(slot.tokens)} tok`})))
    : emptyNote("暂无模型回执。"));

  out.append(trendCard.box);
  mountCards(out, [{box: purposeCard.box}, {box: modelCard.box}]);
  if (items.length) {
    const detail = el("div");
    detail.append(table(
      ["时间", "用途", "模型", "状态", "耗时", "入", "出"],
      items.slice().reverse().map((i) => {
        const u = i.usage && typeof i.usage === "object" ? i.usage : {};
        return [fmt.time(i.ts), purposeLabel(i.purpose), i.model || "—",
          callStatusLabels[i.status] || i.status || "—", fmt.dur(i.duration_ms),
          fmt.tok(u.prompt), fmt.tok(u.completion)];
      })), dataDetails("查看全部字段", items.slice().reverse()));
    if (v.notice) detail.append(el("p", "note", v.notice));
    out.append(disclosure("调用明细",
      `${items.length} 次 · 中位 ${fmt.dur(median)}${failed ? ` · ${failed} 次未成功` : ""}`,
      detail, `按时间倒序；usage 回执 ${tokens.known}/${items.length}${v.pending_dropped ? `，丢弃 ${v.pending_dropped} 条` : ""}，缺失按未知计。`));
  }
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
  const phaseCard = card("导入序列", "一个色块一次导入，左旧右新；颜色是阶段，计数在下方图例。");
  if (!items.length) phaseCard.body.append(emptyNote("没有人格导入记录。"));
  else {
    const chrono = items.slice().reverse();
    phaseCard.body.append(ribbon(chrono.map((item) => ({
      color:phaseColors[item.phase] || C[5],
      title:`${item.op_id} · ${phaseLabels[item.phase] || item.phase}`,
    })), {label:"导入操作序列"}));
    const counts = el("div", "legend");
    for (const [key, n] of Object.entries(phaseMap).sort((a, b) => b[1] - a[1])) {
      const item = el("span"), dot = el("span", "dot");
      dot.style.background = phaseColors[key] || C[5];
      item.append(dot, node("span", `${phaseLabels[key] || key} ${n}`));
      counts.append(item);
    }
    phaseCard.body.append(counts);
    const inserted = items.reduce((a, i) => a + (i.inserted_count || 0), 0);
    const unfinished = items.filter((i) => !["completed", "rolled_back", "superseded", "aborted"].includes(i.phase)).length;
    phaseCard.body.append(line("插入合计", `${fmt.int(inserted)} 条`));
    phaseCard.body.append(line("未到终态", `${fmt.int(unfinished)} 条`));
    phaseCard.body.append(line("最近一次", opTime(items[0] && items[0].op_id)));
  }

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
      resetCard.body.append(dataDetails("查看全部字段", exprItems.slice(0, 8)));
    }
  }

  out.append(gridOf([phaseCard.box, resetCard.box]));
  if (items.length) {
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
    const detail = el("div");
    detail.append(wrap, dataDetails("查看全部字段", v));
    if (v.truncated || v.scan_incomplete) detail.append(el("p", "note", "本次扫描不完整，列表可能少于实际记录。"));
    if (v.notice) detail.append(el("p", "note", v.notice));
    const flag = v.truncated || v.scan_incomplete ? " · 扫描不完整" : "";
    out.append(disclosure("导入记录", `${items.length} 条${flag} · 最近 ${opTime(items[0].op_id)}`,
      detail, "op_id 可以对照 Alice 的日志定位。"));
  }
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
  document.querySelector("main").classList.toggle("embedded", next === "dashboard" && topic === "context");
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
    unmountContext();
    const data = await api("observe", {}, {topic:next});
    const value = data.result;
    if (value && value.status && value.status !== "ok") throw new Error(value.error || "数据暂不可读，请稍后再试");
    topic = next;
    const output = byId("observations");
    output.hidden = false;
    output.replaceChildren();
    byId("body-controls").hidden = !(next === "memory" && ["body", "isolated"].includes(targetMode));
    setPage("dashboard");
    if (next === "schedule") paintSchedule(value, output);
    else if (next === "memory") paintMemory(value, output);
    else if (next === "questions") paintQuestions(value, output);
    else if (next === "calls") paintCalls(value, output);
    else if (next === "persona_operations") paintPersona(value, output);
    else output.append(dataDetails("查看全部字段", value));
    stamp();
    return;
  }
  topic = "context";
  byId("observations").replaceChildren();
  byId("body-controls").hidden = true;
  setPage("dashboard");
  mountContext();
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
  unmountContext();
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
function mountContext() {
  if (!connectionId) throw new Error("请先连接 Alice");
  const host = byId("context-host");
  host.hidden = false;
  if (contextFrame) return;
  const frame = node("iframe");
  frame.title = "上下文观测";
  frame.allow = "clipboard-write";
  frame.src = "/context-inspector/";
  host.replaceChildren(frame);
  contextFrame = frame;
}
function unmountContext() {
  if (!contextFrame) return;
  contextFrame.remove();
  contextFrame = null;
  const host = byId("context-host");
  host.replaceChildren();
  host.hidden = true;
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
    renderValue(byId("body-result"), data);
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
  renderValue(byId("p1-output"), result);
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
