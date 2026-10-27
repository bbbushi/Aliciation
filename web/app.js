/* Alice GUI：种子选择 + 流式聊天 + 记忆栏。无构建链，原生 JS。 */
"use strict";

const $ = (s) => document.querySelector(s);
let busy = false;
let memory = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${r.status}`);
  }
  return r.json();
}

/* ---------- 渲染 ---------- */

function bubble(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const txt = document.createElement("div");
  txt.className = "txt";
  txt.textContent = text;
  div.appendChild(txt);
  $("#msgs").appendChild(div);
  scrollBottom();
  return div;
}

function sysline(text) {
  const div = document.createElement("div");
  div.className = "sys";
  div.textContent = text;
  $("#msgs").appendChild(div);
  scrollBottom();
}

function scrollBottom() {
  const box = $("#msgs");
  box.scrollTop = box.scrollHeight;
}

function renderMemory(m) {
  memory = m;
  if (!m) return;
  $("#memory").textContent =
    `活跃 ${m.active} · 仓库 ${m.warehouse} · 长期 ${m.longterm} · 已忘 ${m.forgotten} · 技能 ${m.skills}`;
}

/* ---------- 启动 ---------- */

async function boot() {
  const s = await api("/api/state");
  if (s.needs_seed) {
    seedScreen(s);
  } else {
    await startChat(s);
  }
}

function seedScreen(s) {
  $("#seed").classList.remove("hidden");
  $("#seed-name").textContent = s.name;
  const list = $("#tag-list");
  const picked = new Set();
  s.tags.forEach((tag) => {
    const el = document.createElement("div");
    el.className = "tag";
    el.textContent = tag;
    el.onclick = () => {
      if (picked.has(tag)) picked.delete(tag);
      else if (picked.size >= 4) return;
      else picked.add(tag);
      el.classList.toggle("on", picked.has(tag));
      $("#seed-go").disabled = !(picked.size >= 2);
    };
    list.appendChild(el);
  });
  $("#seed-go").onclick = async () => {
    $("#seed-go").disabled = true;
    $("#seed-go").textContent = "生成底色中…";
    try {
      const st = await api("/api/seed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags: [...picked] }),
      });
      await startChat(st);
    } catch (e) {
      $("#seed-go").disabled = false;
      $("#seed-go").textContent = "开始";
      alert(e.message);
    }
  };
}

async function startChat(s) {
  $("#seed").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#name").textContent = s.name;
  document.title = s.name;
  renderMemory(s.memory);
  sysline(s.continuity);
  const h = await api("/api/history");
  h.turns.forEach((t) => bubble(t.role, t.content));
  scrollBottom();
  $("#input").focus();
  loadPanel();
}

/* ---------- 监测仪 ---------- */

function sparkline(values) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 120 26");
  if (values.length >= 2) {
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) => {
      const x = (i / (values.length - 1)) * 118 + 1;
      const y = 23 - ((v - min) / span) * 20;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    poly.setAttribute("points", pts);
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "#b0895e");
    poly.setAttribute("stroke-width", "1.5");
    svg.appendChild(poly);
  }
  return svg;
}

function metricCard(name, c) {
  const card = document.createElement("div");
  card.className = "card";

  const head = document.createElement("div");
  head.className = "card-head";
  const nm = document.createElement("div");
  nm.className = "card-name";
  nm.textContent = c.title;
  const x = document.createElement("button");
  x.className = "card-x";
  x.title = "移除";
  x.textContent = "×";
  x.onclick = () => toggleMetric(name, false);
  head.appendChild(nm);
  head.appendChild(x);

  const val = document.createElement("div");
  val.className = "card-val";
  if (c.last != null) {
    val.textContent = Math.round(c.last);
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = c.unit;
    val.appendChild(unit);
  } else {
    val.textContent = "—";
  }

  const sub = document.createElement("div");
  sub.className = "card-sub";
  sub.textContent = c.last != null
    ? `均值 ${Math.round(c.avg)} · 近 ${c.history.length} 轮` + (c.detail ? ` · ${c.detail}` : "")
    : "等待数据";

  card.appendChild(head);
  card.appendChild(val);
  card.appendChild(sub);
  card.appendChild(sparkline(c.history || []));
  card.title = c.desc;
  return card;
}

function renderPanel(snapshot) {
  if (!snapshot || !snapshot.cards) return;
  const cardsBox = $("#cards");
  const poolBox = $("#pool");
  cardsBox.textContent = "";
  poolBox.textContent = "";
  for (const [name, c] of Object.entries(snapshot.cards)) {
    if (c.enabled) cardsBox.appendChild(metricCard(name, c));
    else {
      const chip = document.createElement("div");
      chip.className = "chip";
      chip.textContent = `+ ${c.title}`;
      chip.title = c.desc + (c.judge ? "（每轮一次廉价评审）" : "（零成本）");
      chip.onclick = () => toggleMetric(name, true);
      poolBox.appendChild(chip);
    }
  }
}

async function loadPanel() {
  try {
    const d = await api("/api/metrics");
    $("#thinking").value = d.thinking;
    renderPanel(d.snapshot);
  } catch (e) { /* 面板失败不影响聊天 */ }
}

async function toggleMetric(name, on) {
  try {
    const d = await api("/api/metrics/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ module: name, on }),
    });
    renderPanel(d.snapshot);
  } catch (e) { /* 忽略 */ }
}

$("#panel-btn").onclick = () => {
  $("#panel").classList.toggle("hidden");
  loadPanel();
};

$("#thinking").onchange = async () => {
  try {
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thinking: $("#thinking").value }),
    });
  } catch (e) {
    alert(e.message);
    loadPanel();
  }
};

/* ---------- 发送与流式接收 ---------- */

function ensureThinking(node) {
  let d = node.querySelector("details.think");
  if (!d) {
    d = document.createElement("details");
    d.className = "think";
    d.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "思考过程";
    const body = document.createElement("div");
    body.className = "r-txt";
    d.appendChild(summary);
    d.appendChild(body);
    node.insertBefore(d, node.querySelector(".txt"));
  }
  return d.querySelector(".r-txt");
}

function handleEvent(ev, node) {
  const txt = node.querySelector(".txt");
  if (ev.type === "reasoning") {
    ensureThinking(node).textContent += ev.text;
    scrollBottom();
  } else if (ev.type === "delta") {
    txt.textContent += ev.text;
    scrollBottom();
  } else if (ev.type === "tool") {
    sysline(ev.name === "remember" ? "（悄悄记下了一件事…）" : "（翻了一下记忆…）");
  } else if (ev.type === "meta") {
    sysline(ev.text);
  } else if (ev.type === "done") {
    if (ev.reply && ev.reply !== txt.textContent) txt.textContent = ev.reply;
    if (!txt.textContent.trim()) txt.textContent = "（沉默）";
    renderMemory(ev.memory);
    if (ev.metrics) renderPanel(ev.metrics);
  } else if (ev.type === "error") {
    if (!txt.textContent.trim()) node.remove();
    sysline(ev.message);
  }
}

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true;
  input.value = "";
  input.disabled = true;
  bubble("user", text);
  const node = bubble("assistant", "");
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        handleEvent(JSON.parse(line.slice(6)), node);
      }
    }
  } catch (e) {
    sysline(`连接中断：${e.message}`);
    if (!node.querySelector(".txt").textContent.trim()) node.remove();
  } finally {
    busy = false;
    input.disabled = false;
    input.focus();
  }
}

$("#send").onclick = send;
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});

boot().catch((e) => alert(`初始化失败：${e.message}`));
