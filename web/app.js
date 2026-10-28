/* Alice GUI：初始化 + 流式聊天 + 记忆栏 + 人格面板。无构建链，原生 JS。 */
"use strict";

const $ = (s) => document.querySelector(s);
let busy = false;
let memory = null;
let state = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${r.status}`);
  }
  return r.json();
}

const postJSON = (path, data) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

/* ---------- 小工具 ---------- */

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");

let toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

/* 极简 markdown：标题、列表、引用、表格、粗体、行内代码——够看人格文件了 */
function renderMd(md) {
  const out = [];
  let inList = false;
  let inQuote = false;
  let table = [];
  const closeList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };
  const closeQuote = () => {
    if (inQuote) { out.push("</blockquote>"); inQuote = false; }
  };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?[\s:|-]+\|?$/.test(r));
    table = [];
    if (!rows.length) return;
    const cells = (r) => r.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    const [head, ...rest] = rows;
    out.push(
      "<table><thead><tr>" + cells(head).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
      rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
      "</tbody></table>",
    );
  };
  for (const raw of String(md).split("\n")) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (/^\|.*\|$/.test(line.trim())) { closeList(); closeQuote(); table.push(line.trim()); continue; }
    flushTable();
    if ((m = line.match(/^>\s?(.*)$/))) {
      closeList();
      if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
      out.push(`<p>${inline(m[1])}</p>`);
    } else if ((m = line.match(/^###\s+(.*)$/))) { closeQuote(); closeList(); out.push(`<h3>${inline(m[1])}</h3>`); }
    else if ((m = line.match(/^##\s+(.*)$/))) { closeQuote(); closeList(); out.push(`<h2>${inline(m[1])}</h2>`); }
    else if ((m = line.match(/^#\s+(.*)$/))) { closeQuote(); closeList(); out.push(`<h1>${inline(m[1])}</h1>`); }
    else if ((m = line.match(/^[-*]\s+(.*)$/))) {
      closeQuote();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (!line.trim()) { closeList(); closeQuote(); }
    else { closeQuote(); closeList(); out.push(`<p>${inline(line)}</p>`); }
  }
  flushTable();
  closeList();
  closeQuote();
  return out.join("");
}

/* ---------- 聊天渲染 ---------- */

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
  state = await api("/api/state");
  const forceSetup = new URLSearchParams(location.search).has("setup");  // ?setup 可主动回到初始化页
  if (state.turns > 0 && !forceSetup) await startChat(state);
  else showWelcome(state, forceSetup);
}

async function startChat(s) {
  state = s;
  $("#welcome").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#name").textContent = s.name;
  document.title = s.name;
  renderMemory(s.memory);
  sysline(s.continuity);
  const h = await api("/api/history");
  $("#msgs").textContent = "";
  h.turns.forEach((t) => bubble(t.role, t.content));
  scrollBottom();
  $("#input").focus();
  loadPanel();
}

/* ---------- 初始化（第一次打开） ---------- */

function personaMeta(p) {
  if (!p || !p.installed) return "还没有人格";
  const ver = p.version ? `v${p.version}` : "初始版";
  return `${ver} · ${p.installed_at || "刚安装"} · ${p.chars} 字`;
}

function showWelcome(s, reseting = false) {
  state = s;
  $("#welcome").classList.remove("hidden");
  $("#seed-name").textContent = s.name;
  $("#w-sub").textContent = reseting
    ? "正在重新设置人格；已有的对话和记忆都不会丢。"
    : s.persona && s.persona.installed
      ? "她已经有一个先装好的人格。也可以换一个人格再开始。"
      : "先定下她的底色，再开始。";

  if (s.persona && s.persona.installed) {
    $("#current-persona").classList.remove("hidden");
    $("#cur-meta").textContent = personaMeta(s.persona);
    $("#cur-view").onclick = () => openPersonaView();
    $("#cur-start").onclick = () => startChat(s);
  }

  $("#tab-tags .hint").textContent = s.persona && s.persona.installed
    ? "用标签重新生成，会替换她现在的底色（旧版自动快照，可回滚）。想保留现在的人格，点上面的「以她开始」。"
    : "选 2~4 个气质标签当底色。剩下的性格，由你们的经历慢慢长。";

  /* 标签 */
  const list = $("#tag-list");
  list.textContent = "";
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
    const btn = $("#seed-go");
    btn.disabled = true;
    btn.textContent = "生成底色中…";
    try {
      const st = await postJSON("/api/seed", { tags: [...picked] });
      await startChat(st);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "生成底色并开始";
      toast(e.message, 4000);
    }
  };

  /* 导入 */
  wireImport(
    {
      drop: $("#drop"),
      pick: $("#pick-file"),
      file: $("#import-file"),
      box: $("#import-md"),
      check: $("#import-check"),
      preview: $("#import-preview"),
      go: $("#import-go"),
      idle: "安装并开始",
      busy: "安装中…",
      back: () => closeModal(),
    },
    async (md, src) => {
      const st = await postJSON("/api/persona", { persona_md: md, source: src });
      await startChat(st);
    },
  );

  /* 标签页 */
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
      const toImport = tab.dataset.tab === "import";
      $("#tab-tags").classList.toggle("hidden", toImport);
      $("#tab-import").classList.toggle("hidden", !toImport);
      if (toImport) setTimeout(() => $("#import-md").focus(), 30);
    };
  });
}

/* ---------- 人格文档导入（初始化页与弹层共用） ---------- */

function validateMd(md) {
  const text = md.trim();
  if (!text) return { ok: false, msg: "" };
  /* Alice 格式：认「## 底色」；外部角色提示词（无底色）：整份装进底色层 */
  const aliceShape = /^##\s*底色/m.test(text);
  const words = text.length;
  if (!aliceShape) {
    const msg = words > 12000
      ? `没有「## 底色」：会整份放进底色层；但 ${words} 字偏长，每轮上下文都会被它占一块，建议压到 4000 字内。`
      : "没有「## 底色」小节：把整份文档放进底色层（适合角色提示词 / 角色卡），生长三节自动补占位。";
    return { ok: true, note: !words || words <= 12000, warn: words > 12000, msg };
  }
  if (words > 12000) {
    return { ok: true, warn: true, msg: `含「## 底色」小节，但 ${words} 字偏长——太长会挤压每轮上下文，建议压到 2000 字内。` };
  }
  return { ok: true, msg: "含「## 底色」小节，可以安装。缺失的生长小节会自动补齐。" };
}

function wireImport(els, install) {
  let md = "";
  let source = "";
  const refresh = (text) => {
    md = text;
    els.box.value = text;
    const v = validateMd(text);
    els.check.innerHTML = !text.trim() ? "" : (v.ok ? "✓ " : "✗ ") + v.msg;
    els.check.className = "check" + (v.ok && !v.warn ? " ok" : "") + (v.warn ? " warn" : "") + (v.note ? " note" : "");
    els.go.disabled = !v.ok;
    els.preview.disabled = !text.trim();
  };

  els.pick.onclick = () => els.file.click();
  els.file.onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    source = f.name;
    refresh(await f.text());
  };
  els.box.oninput = () => { source = ""; refresh(els.box.value); };

  /* 拖放 */
  els.drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.drop.classList.add("over");
  });
  els.drop.addEventListener("dragleave", () => els.drop.classList.remove("over"));
  els.drop.addEventListener("drop", async (e) => {
    e.preventDefault();
    els.drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (!/\.(md|markdown|txt)$/i.test(f.name)) {
      toast("只认 .md / .markdown / .txt", 3000);
      return;
    }
    source = f.name;
    refresh(await f.text());
  });

  els.preview.onclick = () => {
    openModal("人格预览", `${md.trim().length} 字`, `<div class="md">${renderMd(md)}</div>`, [
      { label: "返回编辑", onClick: () => (els.back ? els.back(md) : closeModal()) },
      { label: "关闭", onClick: closeModal },
    ]);
  };

  els.go.onclick = async () => {
    els.go.disabled = true;
    const idle = els.go.textContent;
    els.go.textContent = els.busy;
    try {
      await install(md, source || "用户导入");
    } catch (e) {
      toast(e.message, 5000);
      els.go.textContent = idle;
      els.go.disabled = !validateMd(md).ok;
    }
  };
}

/* ---------- 弹层 ---------- */

function openModal(title, meta, bodyHtml, buttons) {
  $("#modal-title").textContent = title;
  $("#modal-meta").innerHTML = meta || "";
  $("#modal-body").innerHTML = bodyHtml;
  const foot = $("#modal-foot");
  foot.textContent = "";
  buttons.forEach((b) => {
    const el = document.createElement("button");
    el.className = b.cls || "btn ghost";
    el.textContent = b.label;
    el.onclick = b.onClick;
    foot.appendChild(el);
  });
  $("#modal").classList.remove("hidden");
}

function closeModal() {
  $("#modal").classList.add("hidden");
}

function downloadMd(md, name = "persona.md") {
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

async function openPersonaView() {
  let p;
  try {
    p = await api("/api/persona");
  } catch (e) {
    toast(`读取人格失败：${e.message}`);
    return;
  }
  const meta = p.installed ? `${personaMeta(p)}${p.turns ? ` · 聊过 ${p.turns} 轮` : ""}` : "还没有人格";
  const body = p.installed
    ? `<div class="md">${renderMd(p.md)}</div>` +
      (p.changelog && p.changelog.length
        ? `<div class="changelog"><div class="panel-title small">最近变更</div><ul>${p.changelog
            .map((x) => `<li>${esc(x)}</li>`)
            .join("")}</ul></div>`
        : "")
    : `<p class="hint">还没有安装人格。点下面的按钮导入一份，或以气质标签生成。</p>`;
  openModal("当前人格", meta, body, [
    { label: "导入新的人格", cls: "btn primary", onClick: () => openPersonaImport() },
    ...(p.installed ? [{ label: "下载 .md", onClick: () => downloadMd(p.md) }] : []),
    { label: "关闭", onClick: closeModal },
  ]);
}

function openPersonaImport(prefill = "") {
  $("#modal-title").textContent = "导入人格文档";
  $("#modal-meta").textContent = "会替换当前人格；旧版自动存进 history，可回滚。";
  $("#modal-body").innerHTML = `
    <div class="drop" id="m-drop">
      <div class="drop-icon">↓</div>
      <div class="drop-main">把文档拖到这里，或 <button class="link" id="m-pick" type="button">选择文件</button></div>
      <div class="hint">须含一行「## 底色」；生长小节缺失会自动补齐</div>
      <input type="file" id="m-file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden>
    </div>
    <textarea id="m-md" rows="10" spellcheck="false" placeholder="# Alice 的人格&#10;&#10;## 底色 · 气质与风格&#10;&#10;- …"></textarea>
    <div id="m-check" class="check"></div>
    <div class="row">
      <button id="m-preview" class="btn ghost" disabled>预览</button>
      <button id="m-go" class="btn primary" disabled>替换并保存</button>
    </div>`;
  const foot = $("#modal-foot");
  foot.textContent = "";
  const cancel = document.createElement("button");
  cancel.className = "btn ghost";
  cancel.textContent = "取消";
  cancel.onclick = () => openPersonaView();
  foot.appendChild(cancel);
  $("#modal").classList.remove("hidden");

  wireImport(
    {
      drop: $("#m-drop"),
      pick: $("#m-pick"),
      file: $("#m-file"),
      box: $("#m-md"),
      check: $("#m-check"),
      preview: $("#m-preview"),
      go: $("#m-go"),
      idle: "替换并保存",
      busy: "替换中…",
      back: (text) => openPersonaImport(text),
    },
    async (md, src) => {
      await postJSON("/api/persona", { persona_md: md, source: src });
      state = await api("/api/state");
      toast("人格已替换，旧版已快照");
      await openPersonaView();
    },
  );
  if (prefill) {
    const box = $("#m-md");
    box.value = prefill;
    box.dispatchEvent(new Event("input"));
  }
  setTimeout(() => $("#m-md").focus(), 30);
}

$("#modal-x").onclick = closeModal;
$("#modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) closeModal();
});
$("#persona-btn").onclick = () => openPersonaView();

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
    const d = await postJSON("/api/metrics/toggle", { module: name, on });
    renderPanel(d.snapshot);
  } catch (e) { /* 忽略 */ }
}

$("#panel-btn").onclick = () => {
  $("#panel").classList.toggle("hidden");
  loadPanel();
};

$("#thinking").onchange = async () => {
  try {
    await postJSON("/api/settings", { thinking: $("#thinking").value });
  } catch (e) {
    toast(e.message);
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
