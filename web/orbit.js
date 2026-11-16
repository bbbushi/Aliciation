/* =========================================================================
 * orbit.js · Orbit 主题唯一控制器（2026-11-17 集成施工）
 *
 * 职责（01 大纲 v2.1 §五/§八 + 02a/b/c 结构壳 + 03a §3.1 dock 契约 +
 * 04a §1 切页五连/§3 召唤窗 + 04b M-02/M-03/M-09/M-10）：
 *   1) 主题安装/卸载：MutationObserver 监听 html[data-theme]，install() 建
 *      dock + 页面骨架，uninstall() 按还原表全量还原（注释标记 replaceWith）。
 *   2) 切页五连：body[data-orbit-page] → 旧页即隐 → 新页 is-entering →
 *      dock aria-current → OrbitBG.setPage。
 *   3) 召唤窗：自建 <dialog class="orbit-window">（S2/M8/M9/M10；M2–M7 复用
 *      #settings-modal——02c 权威口径），origin 夹取 20–80%、键盘回退 50%、
 *      退场 B 案 data-closing ≤120ms、防重入、焦点先回后 close。
 *   4) 运行时文案替换 + 还原表（轮次 3 裁定 1：不改 index.html）。
 *   5) MutationObserver 重应用（轮次 3 裁定 2）：evaluations armed 显隐、
 *      workers-empty 替身、rows=0 隐藏、thoughts 冻结对抗（三时机 setVisible）、
 *      thought-detail 联动抽屉。
 *   6) 状态镜像：OrbitBG.setState + dock 徽标/状态点（M-03 一次性双波）。
 *   7) 跨组接点：claude-settings 依赖 #settings-btn.click()（原按钮保留隐藏，
 *      dock 代理点击）；P4「去思想与探索」跨页跳 P3；待确认徽标切 P1。
 *
 * 红线自检：#msgs 内部与 revealStates/revealSync DOM 零触碰；零外部依赖；
 * 单 IIFE；全部监听/观察器/计时器在 uninstall 断开（对齐 app.js minimal 先例）。
 *
 * 裁剪登记（待真机验证后补，见文件尾注释）。
 * ========================================================================= */
(() => {
  "use strict";

  /* ---------- 一、基础设施：清理表 / 还原表 / 计时器 ---------- */

  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  const reduceMotion = () =>
    matchMedia("(prefers-reduced-motion: reduce)").matches ||
    root.dataset.motion === "off";

  let installed = false;
  let seq = 0;
  /** undos：结构/监听/观察器类，uninstall 时逆序执行（对齐 minimal cleanup 纪律） */
  const undos = [];
  /** restores：值类还原（textContent/innerHTML/style/open/class），逆序执行 */
  const restores = [];
  const timers = new Set();
  const controller = new AbortController(); // document 级监听统一信号
  const signal = controller.signal;

  function later(fn, ms) {
    const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
    timers.add(t);
    return t;
  }
  function every(fn, ms) {
    const t = setInterval(fn, ms);
    timers.add(t);
    return t;
  }
  undos.push(() => { for (const t of timers) { clearTimeout(t); clearInterval(t); } timers.clear(); });
  undos.push(() => controller.abort());

  /** 观察器登记：uninstall 全部断开（02b 还原表 #13） */
  function spy(node, options, fn) {
    if (!node) return null;
    const ob = new MutationObserver(fn);
    ob.observe(node, options);
    undos.push(() => ob.disconnect());
    return ob;
  }
  /** 元素监听登记 */
  function wire(node, type, fn, opts) {
    if (!node) return;
    node.addEventListener(type, fn, opts);
    undos.push(() => node.removeEventListener(type, fn, opts));
  }

  /* ---------- 二、还原表 helper（轮次 3 裁定 1：改写一律记录可还原） ---------- */

  /** 文本替换：restores[i] 还原 textContent */
  function subText(node, text) {
    if (!node) return;
    const old = node.textContent;
    if (old === text) return;
    node.textContent = text;
    restores.push(() => { node.textContent = old; });
  }
  /** innerHTML 替换（图标化按钮用；按钮自身监听不受影响） */
  function subHtml(node, html) {
    if (!node) return;
    const old = node.innerHTML;
    node.innerHTML = html;
    restores.push(() => { node.innerHTML = old; });
  }
  /** 属性写入（placeholder/title/aria-label/tabindex…），null 表示原本无 */
  function subAttr(node, name, value) {
    if (!node) return;
    const old = node.getAttribute(name);
    if (old === value) return;
    node.setAttribute(name, value);
    restores.push(() => {
      if (old === null) node.removeAttribute(name); else node.setAttribute(name, name === "tabindex" ? old : old);
    });
  }
  /** style 整体改写（clip 隐藏等），记录旧 style 串 */
  function subStyle(node, css) {
    if (!node) return;
    const old = node.getAttribute("style");
    node.setAttribute("style", old ? old + ";" + css : css);
    restores.push(() => {
      if (old === null) node.removeAttribute("style"); else node.setAttribute("style", old);
    });
  }
  /** class 追加（记旧 className 全量还原） */
  function subClass(node, cls) {
    if (!node || node.classList.contains(cls)) return;
    const old = node.className;
    node.classList.add(cls);
    restores.push(() => { node.className = old; });
  }
  /** 摘除 .hidden 类通道（app.js 对面板用类+属性双通道隐藏；还原回原类串） */
  function unhideClass(node) {
    if (!node || !node.classList.contains("hidden")) return;
    const old = node.className;
    node.classList.remove("hidden");
    restores.push(() => { node.className = old; });
  }
  /** open/hidden 类布尔态改写（记旧值） */
  function subFlag(node, prop, value) {
    if (!node || node[prop] === value) return;
    const old = node[prop];
    node[prop] = value;
    restores.push(() => { node[prop] = old; });
  }
  /** 文本节点摘除（label 内含 input 时只隐文字不动控件），成对还原 */
  function stripTexts(node) {
    if (!node) return;
    const removed = [];
    const walk = (n) => {
      for (const child of n.childNodes) {
        if (child.nodeType === 3 && child.nodeValue.trim()) { removed.push([child, child.nodeValue]); child.nodeValue = ""; }
        else if (child.nodeType === 1 && child.tagName !== "INPUT" && child.tagName !== "SELECT") walk(child);
      }
    };
    walk(node);
    if (removed.length) restores.push(() => { for (const [t, v] of removed) t.nodeValue = v; });
  }
  /** 搬移原节点：留注释标记占位，uninstall 时 marker.replaceWith 还位（minimal 先例） */
  function moveNode(node, newParent, before) {
    if (!node || !newParent) return null;
    if (!node.parentElement) {
      // 新建节点（createElement 后未落 DOM）直接插入即可：没有原位可标记，
      // undo 记「移除」——逆序还原时先摘壳再由既有节点的 marker 归位，次序自洽。
      newParent.insertBefore(node, before || null);
      undos.push(() => node.remove());
      return null;
    }
    const marker = document.createComment("orbit-move:" + (node.id || ++seq));
    node.parentElement.insertBefore(marker, node);
    newParent.insertBefore(node, before || null);
    // 面板可能被业务脚本在 orbit 期间整段重绘：标记失联说明原位已有新节点，
    // 被搬走的旧节点应当弃置而不是强行塞回（塞回会抛 NotFoundError 且产生重复）。
    undos.push(() => { if (marker.isConnected) marker.replaceWith(node); });
    return marker;
  }
  /** 包一层新建壳（容器级包装；unwrap 按 childNodes 原序还原） */
  function wrapNode(node, wrapper, childSlots) {
    const parent = node.parentElement;
    const marker = document.createComment("orbit-wrap:" + (node.id || ++seq));
    parent.insertBefore(marker, node);
    parent.insertBefore(wrapper, marker);
    // childSlots: [{container, nodes}]——容器级重排时逐组登记还原
    const groups = [];
    const regroup = (container, nodes) => { groups.push([container, [...container.childNodes]]); for (const n of nodes) container.appendChild(n); };
    if (childSlots) for (const s of childSlots) regroup(s.container, s.nodes);
    else wrapper.appendChild(node);
    undos.push(() => {
      for (const [container, kids] of groups.reverse()) for (const k of kids) container.appendChild(k);
      marker.replaceWith(node);
      wrapper.remove();
    });
  }

  /* ---------- 三、SVG 线条图标库（03a §4.1：stroke 1.6 / viewBox 24） ---------- */

  const PATHS = {
    chat: '<path d="M4 5.5h16v10.5h-9.2L6 19.7v-3.7H4z"/>',
    search: '<circle cx="11" cy="11" r="6.4"/><path d="M15.7 15.7 21 21"/>',
    stars: '<circle cx="6" cy="17" r="1.3"/><circle cx="12" cy="7" r="1.3"/><circle cx="18" cy="13.5" r="1.3"/><path d="M6.9 15.8 11 8.4m1.9.3 3.9 4"/>',
    gear: '<circle cx="12" cy="12" r="3.1"/><path d="M12 3.2v2.5m0 12.6v2.5M3.2 12h2.5m12.6 0h2.5M5.7 5.7l1.8 1.8m9 9 1.8 1.8m0-14.8-1.8 1.8m-9 9-1.8 1.8"/>',
    diamond: '<path d="M12 3.6 19 12l-7 8.4L5 12z"/>',
    layers: '<path d="M4 6h16M4 12h16M4 18h9"/>',
    shield: '<path d="M12 3l7 2.8v5.3c0 4.3-2.9 7.5-7 9.4-4.1-1.9-7-5.1-7-9.4V5.8z"/><path d="m9.2 11.7 2 2 3.8-4.2"/>',
    qq: '<circle cx="12" cy="10.5" r="6.4"/><path d="m14.6 15.2 3.6 4.2"/>',
    dsh: '<path d="M7 4.5h3.8a7.5 7.5 0 0 1 0 15H7z"/>',
    claude: '<path d="M17.4 7.4a6.6 6.6 0 1 0 0 9.2"/>',
    moon: '<path d="M20 13.4A8 8 0 1 1 10.6 4 6.3 6.3 0 0 0 20 13.4z"/>',
    mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="1.6"/><path d="m4.5 7.5 7.5 5.4 7.5-5.4"/>',
    exit: '<path d="M12 3.5v8M6.5 6a8 8 0 1 0 11 0"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    sync: '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 3.6V8h-4.4"/>',
    palette: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1.1"/><circle cx="15" cy="9" r="1.1"/><circle cx="9" cy="15" r="1.1"/><circle cx="15" cy="15" r="1.1"/>',
    info: '<circle cx="12" cy="12" r="8.2"/><path d="M12 11.2v4.6"/><path d="M12 8.1h.01"/>',
    book: '<path d="M5.5 4.5H16a2.5 2.5 0 0 1 2.5 2.5v12H8a2.5 2.5 0 0 1-2.5-2.5z"/><path d="M5.5 4.5V16.5"/>',
  };
  function svg(name, cls) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", cls || "orbit-symbol");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.6");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = PATHS[name] || "";
    return s;
  }

  /* ---------- 四、tip 控制器（04b M-02 修订版：350/0/450ms + >8px 清除） ---------- */

  const HOLD_MOUSE = 350, HOLD_TOUCH = 450, AUTO_DISMISS = 1000, MOVE_CANCEL = 8;
  let tipEl = null, tipTarget = null, holdT = 0, dismissT = 0, downX = 0, downY = 0;

  function tipShow(target) {
    if (!tipEl) return;
    tipTarget = target;
    tipEl.textContent = target.getAttribute("data-tip");
    if (tipEl.textContent.length > 12) tipEl.setAttribute("data-long", "");
    else tipEl.removeAttribute("data-long");
    const r = target.getBoundingClientRect();
    tipEl.style.visibility = "hidden";
    tipEl.setAttribute("data-show", "");
    // 位置：上方 8px 水平居中，贴边夹取（固定定位不随滚动）
    const tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
    let x = r.left + r.width / 2 - tw / 2;
    x = Math.max(8, Math.min(innerWidth - tw - 8, x));
    let y = r.top - th - 8;
    if (y < 8) y = r.bottom + 8;
    tipEl.style.left = x + "px";
    tipEl.style.top = y + "px";
    tipEl.style.visibility = "";
  }
  function tipHide() {
    clearTimeout(holdT); clearTimeout(dismissT); holdT = dismissT = 0;
    if (tipEl) tipEl.removeAttribute("data-show");
    tipTarget = null;
  }
  function installTip() {
    tipEl = document.createElement("div");
    tipEl.className = "orbit-tip";
    tipEl.setAttribute("role", "tooltip");
    tipEl.style.pointerEvents = "none";
    document.body.appendChild(tipEl);
    undos.push(() => { tipEl.remove(); tipEl = null; tipTarget = null; });
    document.addEventListener("pointerover", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (!t || t === tipTarget || e.pointerType === "touch") return;
      clearTimeout(holdT);
      tipHide();
      tipTarget = t; // 占位防重入；真正展示在延迟后
      holdT = later(() => { if (tipTarget === t) tipShow(t); }, HOLD_MOUSE);
    }, { capture: true, signal });
    document.addEventListener("pointerout", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (t && t === tipTarget && !(e.relatedTarget && t.contains(e.relatedTarget))) tipHide();
    }, { capture: true, signal });
    document.addEventListener("focusin", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (t) tipShow(t); // 键盘 0ms
    }, { capture: true, signal });
    document.addEventListener("focusout", (e) => {
      if (tipTarget && e.target.closest && e.target.closest("[data-tip]") === tipTarget) tipHide();
    }, { capture: true, signal });
    document.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "touch") return;
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (!t) return;
      downX = e.clientX; downY = e.clientY; tipTarget = t;
      clearTimeout(holdT);
      holdT = later(() => {
        if (tipTarget !== t) return;
        tipShow(t);
        clearTimeout(dismissT);
        dismissT = later(tipHide, AUTO_DISMISS);
      }, HOLD_TOUCH);
    }, { capture: true, signal });
    document.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "touch" || !holdT) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) < MOVE_CANCEL) return;
      clearTimeout(holdT); holdT = 0; tipTarget = null; // 位移 >8px 才清除（R4 P1-3）
    }, { capture: true, signal });
    ["pointerup", "pointercancel"].forEach((t) =>
      document.addEventListener(t, () => { clearTimeout(holdT); holdT = 0; }, { capture: true, signal }));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") tipHide(); }, { capture: true, signal });
  }

  /* ---------- 五、页面路由与切页五连（04a §1） ---------- */

  const PAGE_KEYS = ["chat", "discovery", "thoughts", "settings"];
  const pages = {};            // key → 容器元素（install 时填充）
  let currentPage = "chat";

  function writePage(name) {
    document.body.dataset.orbitPage = name;
    for (const key of PAGE_KEYS) {
      const el = pages[key];
      if (!el) continue;
      if (key === name) {
        el.hidden = false; el.style.display = "";
        el.classList.add("is-entering");
        el.addEventListener("animationend", () => el.classList.remove("is-entering"), { once: true });
        later(() => el.classList.remove("is-entering"), 400); // 动画被 display:none 打断时兜底（04a §1.6）
      } else {
        el.hidden = true; el.style.display = "none"; // 旧页即隐；display 双保险（.orbit-page 类 display:flex 会盖掉 [hidden]）
        el.classList.remove("is-entering");
      }
    }
    // dock aria-current（同帧）
    for (const key of PAGE_KEYS) {
      const btn = dockIcons.primary[key];
      if (btn) { if (key === name) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current"); }
    }
    if (window.OrbitBG && typeof window.OrbitBG.setPage === "function") window.OrbitBG.setPage(name);
    syncThoughtsVisible(); // 切页时机：P3 可见性重申（02b 三时机之二）
  }

  function effectivePage() {
    const onb = $("onboarding"), wel = $("welcome"), app = $("app");
    if ((onb && !onb.classList.contains("hidden")) || (wel && !wel.classList.contains("hidden"))) return "onboarding";
    if (app && !app.classList.contains("hidden")) return currentPage;
    return "onboarding"; // app 未开且无引导卡：视为 onboarding 档（球位下缘让位）
  }
  function route(name, opts) {
    currentPage = name;
    writePage(effectivePage() === "onboarding" ? "onboarding" : name);
    if (!opts || !opts.silent) { /* 供键盘/鼠标同路径调用，无差异处理 */ }
  }

  /** thoughts 可见性三时机之一：统一出口（thoughts.js settings-modal observer 会冻结） */
  function syncThoughtsVisible() {
    const modal = $("settings-modal");
    const modalHidden = !modal || modal.classList.contains("hidden");
    try {
      window.AliceThoughts && window.AliceThoughts.setVisible &&
        window.AliceThoughts.setVisible(currentPage === "thoughts" && modalHidden);
    } catch (_) { /* 思想模块未就绪时静默 */ }
  }

  /* ---------- 六、dock（03a §3.1 契约：四簇 / SVG / 纹理 / 徽标挂点） ---------- */

  const dockIcons = { primary: {}, tools: {}, system: {} };
  const badges = {};   // 徽标元素登记（M-03 协议用）

  function dockButton({ icon, label, tip, idle, cluster }) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "orbit-icon";
    b.setAttribute("aria-label", label);
    b.appendChild(svg(icon));
    if (tip) b.setAttribute("data-tip", tip);
    if (idle) b.dataset.idle = idle;
    (cluster || dock).appendChild(b);
    return b;
  }

  let dock = null;
  function buildDock() {
    dock = document.createElement("div");
    dock.id = "orbit-dock";
    dock.setAttribute("role", "navigation");
    dock.setAttribute("aria-label", "轨道导航");
    const texture = document.createElement("div");
    texture.className = "orbit-dock-texture";
    dock.appendChild(texture);
    const mkCluster = (name) => {
      const c = document.createElement("div");
      c.className = "orbit-dock-cluster";
      c.dataset.cluster = name;
      dock.appendChild(c);
      return c;
    };
    const cPrimary = mkCluster("primary");
    const cPersona = mkCluster("persona");
    const cTools = mkCluster("tools");
    const cSystem = mkCluster("system");

    // 簇① 一级四图标（点击走切页五连，鼠标键盘同路径）
    const primaries = [
      ["chat", "chat", "我们的对话 · 01 / conversation", "对话"],
      ["discovery", "search", "内容发现", "发现"],
      ["thoughts", "stars", "思想与探索", "思想"],
      ["settings", "gear", "设置", "设置"],
    ];
    for (const [key, icon, label, tip] of primaries) {
      const b = dockButton({ icon, label, tip, cluster: cPrimary });
      wire(b, "click", () => route(key));
      dockIcons.primary[key] = b;
    }
    // 簇② 人格（召 M1：代理原 #persona-btn，节点保留）
    const persona = dockButton({ icon: "diamond", label: "人格", tip: "人格", idle: "mid", cluster: cPersona });
    wire(persona, "click", () => { const t = $("persona-btn"); if (t) t.click(); });
    dockIcons.persona = persona;
    // 簇③ 工具簇：记忆观察器 / P1 核查（N2）+ P1 待绑定徽标
    const obs = dockButton({ icon: "layers", label: "记忆观察器", tip: "记忆观察器", idle: "mid", cluster: cTools });
    wire(obs, "click", () => { const t = $("observer-btn"); if (t) t.click(); });
    const p1 = dockButton({ icon: "shield", label: "P1 核查", tip: "P1 核查", idle: "mid", cluster: cTools });
    wire(p1, "click", () => { const t = $("evaluations-btn"); if (t) t.click(); });
    dockIcons.tools.observer = obs; dockIcons.tools.p1 = p1;
    // 簇④ 系统簇：QQ / DSH / Claude / 清醒睡眠 状态 + 待确认徽标 + 退出（N2）
    const qq = dockButton({ icon: "qq", label: "QQ 连接", tip: "QQ 连接", idle: "mid", cluster: cSystem });
    wire(qq, "click", () => { const t = $("qq-btn"); if (t) t.click(); });
    const dsh = dockButton({ icon: "dsh", label: "DSH 授权", tip: "DSH 授权", idle: "mid", cluster: cSystem });
    wire(dsh, "click", () => window.AliceUI && window.AliceUI.openSettings("workers"));
    const cl = dockButton({ icon: "claude", label: "Claude 授权", tip: "Claude 授权", idle: "mid", cluster: cSystem });
    wire(cl, "click", () => { const t = $("settings-btn"); if (t) t.click(); window.AliceUI && window.AliceUI.selectSettings("claude"); });
    const wake = dockButton({ icon: "moon", label: "清醒 / 睡眠", tip: "清醒 / 睡眠", idle: "mid", cluster: cSystem });
    wire(wake, "click", () => route("thoughts"));
    const mail = dockButton({ icon: "mail", label: "待确认消息", tip: "待确认消息", cluster: cSystem });
    wire(mail, "click", () => {
      route("chat");
      const n = $("chat-notice");
      if (n && !n.classList.contains("hidden")) n.focus && n.focus();
    });
    const exit = dockButton({ icon: "exit", label: "退出 Alice", tip: "退出 Alice", cluster: cSystem });
    wire(exit, "click", () => { const t = $("exit-btn"); if (t) t.click(); });
    dockIcons.system = { qq, dsh, claude: cl, wake, mail, exit };
    // 徽标挂点：待确认（warn）挂信封；P1 待绑定（info）挂工具簇 P1；DSH 未读（warn）挂 DSH
    for (const [key, tone] of [["mail", "warn"], ["p1", "info"], ["dsh", "warn"]]) {
      const holder = key === "p1" ? p1 : dockIcons.system[key];
      const badge = document.createElement("span");
      badge.className = "orbit-badge";
      badge.dataset.tone = tone;
      badge.hidden = true;
      badge.addEventListener("animationend", (e) => {
        if (e.animationName === "orbit-badge-glow") badge.setAttribute("data-attention", "done"); // 播完永久静止
      });
      holder.appendChild(badge);
      badges[key] = badge;
    }
    // 状态点：QQ / DSH / Claude / 清醒睡眠（data-state 镜像）
    const dots = {};
    for (const [key, holder] of [["qq", qq], ["dsh", dsh], ["claude", cl], ["wake", wake]]) {
      const d = document.createElement("span");
      d.className = "orbit-dot";
      d.dataset.state = "unknown";
      holder.appendChild(d);
      dots[key] = d;
    }
    dockIcons.dots = dots;
    document.body.appendChild(dock);
    undos.push(() => dock.remove());
    // 主题首次进入：dock 整体 180ms 仅淡入（04a §1.5 顺序注）
    dock.classList.add("is-entering");
    dock.addEventListener("animationend", () => dock.classList.remove("is-entering"), { once: true });
    later(() => dock.classList.remove("is-entering"), 400);
  }

  /* ---------- 七、M-03 徽标一次性双波协议 ---------- */

  const badgePrev = { mail: 0, p1: 0, dsh: 0 };
  function setBadge(key, value) {
    const badge = badges[key];
    if (!badge) return;
    const prev = badgePrev[key] || 0;
    const val = Math.max(0, Number(value) || 0);
    if (val === prev) { if (val) badge.textContent = val > 99 ? "99+" : String(val); return; }
    badgePrev[key] = val;
    if (!val) {
      // 消散：150ms 淡出后移除内容（reduce 直接隐藏）
      if (reduceMotion()) { badge.hidden = true; badge.textContent = ""; return; }
      badge.setAttribute("data-fading", "");
      later(() => { badge.hidden = true; badge.textContent = ""; badge.removeAttribute("data-fading"); }, 150);
      return;
    }
    badge.hidden = false;
    badge.textContent = val > 99 ? "99+" : String(val);
    // 双落点去重：当前页同源可见则静默出现（M-03）
    const duplicated = key === "mail" && currentPage === "chat" &&
      !$("chat-new-attempt").classList.contains("hidden");
    if (reduceMotion()) return; // 停帧只留状态色与数字
    if (!prev) badge.setAttribute("data-attention", duplicated ? "quiet" : "");
    else if (val > prev) {
      badge.removeAttribute("data-attention"); void badge.offsetWidth; // 重置动画只重播 pop
      badge.setAttribute("data-attention", duplicated ? "quiet" : "");
    }
  }

  /* ---------- 八、召唤窗核心（04a §3：origin / B 案退场 / 防重入） ---------- */

  function computeOrigin(dialog, opener) {
    let ox = 50, oy = 50;
    if (opener && opener.nodeType === 1) {
      const r = opener.getBoundingClientRect();
      const w = dialog.getBoundingClientRect();
      if (r.width || r.top || r.left) {
        ox = Math.round(Math.max(20, Math.min(80, ((r.left + r.width / 2) - w.left) / Math.max(1, w.width) * 100)));
        oy = Math.round(Math.max(20, Math.min(80, ((r.top + r.height / 2) - w.top) / Math.max(1, w.height) * 100)));
      }
    }
    return [ox, oy];
  }
  function openOrbitWindow(dialog, opener) {
    if (dialog.open) return;
    if (dialog.dataset.closing) { dialog.close(); } // 退场中立即真关再开（04a §3.2 快速开关）
    dialog._orbitOpener = opener || document.activeElement;
    dialog.showModal();
    const [ox, oy] = opener && opener.nodeType === 1 && opener.matches(":focus-visible") ? [50, 50]
      : computeOrigin(dialog, opener && opener.nodeType === 1 ? opener : null);
    // 键盘触发（event.detail===0 的点击或 focus 路径）回退 50%
    dialog.style.setProperty("--orbit-win-ox", ox + "%");
    dialog.style.setProperty("--orbit-win-oy", oy + "%");
    if (!reduceMotion()) {
      dialog.setAttribute("data-opening", "");
      dialog.addEventListener("animationend", () => dialog.removeAttribute("data-opening"), { once: true });
      later(() => dialog.removeAttribute("data-opening"), 300);
    }
  }
  function closeOrbitWindow(dialog) {
    if (!dialog.open || dialog.dataset.closing) return; // 防重入
    const finish = () => { delete dialog.dataset.closing; dialog.inert = false; dialog.close(); };
    if (reduceMotion()) { dialog.close(); return; } // reduce 不播退场直接关（原生回焦）
    dialog.dataset.closing = "1";
    dialog.inert = true; // 含 pointer-events:none（03c §1.4 防误触）
    // 焦点先回召唤入口（与原生 close 回焦同帧同目标）
    const opener = dialog._orbitOpener;
    if (opener && opener.isConnected && opener.getClientRects().length) opener.focus({ preventScroll: true });
    else if (dialog._orbitDockFallback && dialog._orbitDockFallback.isConnected) dialog._orbitDockFallback.focus({ preventScroll: true });
    dialog.addEventListener("animationend", finish, { once: true });
    later(finish, 150); // animationend 因隐藏丢失时兜底
  }
  /** 自建窗骨架：dialog.orbit-window + 可选窗头（唯一可见标题） */
  function makeWindow({ id, title, size, label, ariaLabel }) {
    const d = document.createElement("dialog");
    d.id = id;
    d.className = "orbit-window" + (size ? " orbit-window--" + size : "");
    if (ariaLabel) d.setAttribute("aria-label", ariaLabel); // R3 P1-6：显式复制，不靠继承
    if (title) {
      const head = document.createElement("div");
      head.className = "orbit-win-head";
      const t = document.createElement("div");
      t.className = "orbit-win-title";
      t.id = id + "-title";
      t.textContent = title;
      head.appendChild(t);
      const tools = document.createElement("div");
      tools.className = "orbit-win-tools";
      const x = document.createElement("button");
      x.type = "button";
      x.className = "orbit-win-btn";
      x.setAttribute("aria-label", "关闭");
      x.setAttribute("data-tip", "关闭");
      x.appendChild(svg("x", ""));
      x.querySelector("svg").style.width = "18px";
      x.querySelector("svg").style.height = "18px";
      wire(x, "click", () => closeOrbitWindow(d));
      tools.appendChild(x);
      head.appendChild(tools);
      d.setAttribute("aria-labelledby", t.id);
      d.appendChild(head);
      d._orbitCloseBtn = x;
    }
    const body = document.createElement("div");
    body.className = "orbit-window-body";
    body.setAttribute("data-density", "mid");
    d.appendChild(body);
    d._orbitBody = body;
    // 点 backdrop 关闭（minimal 先例的矩形判定）
    wire(d, "click", (e) => {
      if (e.target !== d) return;
      const r = d.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeOrbitWindow(d);
    });
    // Esc：cancel 事件收敛为 B 案单一路径
    wire(d, "cancel", (e) => { e.preventDefault(); closeOrbitWindow(d); });
    document.body.appendChild(d);
    undos.push(() => { if (d.open) d.close(); d.remove(); });
    return d;
  }

  /* ---------- 九、四自建窗安装（S2 / M8 / M9 / M10） ---------- */

  function installWindows() {
    // —— S2 记忆概览（轮次 4 裁定 4：自建窗 + #memory 搬移；details 接管点击） ——
    const memSummary = document.querySelector("details.memory-summary");
    if (memSummary) {
      const win = makeWindow({ id: "orbit-win-memory", title: "记忆概览", size: "sm" });
      win._orbitDockFallback = dockIcons.primary.chat;
      const memory = $("memory");
      if (memory) moveNode(memory, win._orbitBody);
      subClass(memSummary.querySelector(":scope > summary"), "orbit-icon-entry"); // 入口图标化语言
      const sum = memSummary.querySelector(":scope > summary");
      if (sum) { subAttr(sum, "data-tip", "记忆概览"); subHtml(sum, ""); sum.appendChild(svg("book", "orbit-symbol")); }
      wire(memSummary, "click", (e) => { e.preventDefault(); e.stopPropagation(); openOrbitWindow(win, sum); }, { capture: true });
      win._orbitCloseBtn && (win._orbitCloseBtn.dataset.tip = "关闭");
    }

    // —— M8 记忆观察器 / M9 P1 核查 / M10 DSH 协作：搬移整节点 + hidden↔dialog 开合同步 ——
    const follow = (asideId, dialog, opts) => {
      const aside = $(asideId);
      if (!aside) return;
      dialog._orbitDockFallback = opts.dockFallback || null;
      moveNode(aside, dialog._orbitBody);
      const sync = () => {
        const hidden = aside.classList.contains("hidden");
        if (!hidden && !dialog.open) openOrbitWindow(dialog, null);
        else if (hidden && dialog.open) {
          if (dialog.dataset.closing) return; // B 案已在退场，等 close 事件闭环
          dialog.close(); // 宿主主动隐藏（Esc 链路兼容，甲-08）
        }
      };
      spy(aside, { attributes: true, attributeFilter: ["class"] }, sync);
      wire(dialog, "close", () => {
        if (!aside.classList.contains("hidden")) aside.classList.add("hidden"); // close 回写信号
        if (opts.onClose) opts.onClose();
        const opener = dialog._orbitOpener;
        if (!(opener && opener.isConnected && opener.getClientRects().length) &&
            dialog._orbitDockFallback && dialog._orbitDockFallback.isConnected) {
          dialog._orbitDockFallback.focus({ preventScroll: true }); // 焦点接管回 dock 图标（02c 0.5）
        }
      });
    };

    const obsWin = makeWindow({ id: "orbit-window-observer", title: "观察器", ariaLabel: "记忆观察器" });
    obsWin._orbitDockFallback = dockIcons.tools.observer;
    const obsHead = document.querySelector("#observer .obs-head");
    if (obsHead) {
      subClass(obsHead, "orbit-win-head"); // 次级头改主窗头（03c §三）
      const strong = obsHead.querySelector("strong");
      subText(strong, "开发者观察器"); // 文本保留（还原表无改写），clip 隐藏交由唯一标题规则视觉
      subStyle(strong, "position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;");
      subAttr(strong, "tabindex", "-1");
      const close = $("obs-close");
      if (close && obsWin._orbitCloseBtn) {
        // obs-close 收进窗头 tools（关窗单一路径；原 title 还原）
        subAttr(close, "data-tip", "关闭并清空观察记录");
        moveNode(close, obsWin._orbitCloseBtn.parentElement, obsWin._orbitCloseBtn);
        subClass(close, "orbit-win-btn");
        subHtml(close, ""); close.appendChild(svg("x", ""));
        close.querySelector("svg").style.width = "18px";
        close.querySelector("svg").style.height = "18px";
        wire(close, "click", (e) => { e.stopPropagation(); closeOrbitWindow(obsWin); }, { capture: true });
        obsWin._orbitCloseBtn.hidden = true; // 双 × 收敛为一个（工具头唯一）
      }
    }
    follow("observer", obsWin, { dockFallback: dockIcons.tools.observer });

    const evalWin = makeWindow({ id: "orbit-win-evaluations", size: "md", ariaLabel: "P1 核查" });
    evalWin._orbitDockFallback = dockIcons.tools.p1;
    const evalHead = document.querySelector("#evaluations .obs-head");
    if (evalHead) subClass(evalHead, "orbit-win-head");
    follow("evaluations", evalWin, { dockFallback: dockIcons.tools.p1 });

    const workerWin = makeWindow({ id: "orbit-win-workers", ariaLabel: "DSH 协作会话" }); // 窗头无标题文本（03c 硬约束）
    workerWin._orbitDockFallback = dockIcons.system.dsh;
    follow("worker-tasks", workerWin, { dockFallback: dockIcons.system.dsh });
  }

  /* ---------- 十、设置窗增强（M2–M7 复用 #settings-modal；02c 权威口径） ---------- */

  const SECTION_NAMES = {
    general: "对话与模型", appearance: "外观", discovery: "联网搜索",
    qq: "QQ 连接", workers: "DSH 授权", claude: "Claude 授权", thoughts: "思想与探索",
  };
  let switching = false; // M-10 switchStep 纪元重入守卫

  function installSettingsWindow() {
    const modal = $("settings-modal");
    const card = modal && modal.querySelector(".modal-card.settings-card");
    if (!card) return;
    subClass(card, "orbit-window");   // 03c §1.1 权威口径：运行时追加类
    subClass(card, "orbit-window--md");
    subAttr(card, "data-density", "mid");
    const head = card.querySelector(".modal-head");
    if (head) {
      subClass(head, "orbit-win-head");
      const title = $("settings-title");
      subClass(title, "orbit-win-title");
    }
    const x = $("settings-x");
    subClass(x, "orbit-win-btn");
    subAttr(x, "data-tip", "关闭");

    // 步点条：原 nav.settings-nav 已在窗头下方，只挂类 + 图标化（监听全保留，02c 0.3）
    const nav = card.querySelector("nav.settings-nav");
    if (nav) {
      subClass(nav, "orbit-step-dots");
      const STEP_ICON = { general: "chat", appearance: "palette", discovery: "search", qq: "qq", workers: "dsh" };
      for (const b of nav.querySelectorAll("button[data-settings]")) {
        subClass(b, "orbit-step");
        const key = b.getAttribute("data-settings");
        subAttr(b, "data-tip", SECTION_NAMES[key] || key);
        subHtml(b, "");
        b.appendChild(svg(STEP_ICON[key] || "gear", ""));
        b.querySelector("svg").style.width = "18px";
        b.querySelector("svg").style.height = "18px";
      }
      // thoughts.js 注入的「思想与探索」按钮：隐藏（归 P3，轮次 3 裁定 1）
      const thBtn = nav.querySelector('button[data-settings="thoughts"], .settings-nav button');
      for (const b of nav.querySelectorAll("button")) {
        if (!b.getAttribute("data-settings")) subStyle(b, "display:none"); // 动态注册按钮（thoughts）
      }
      // switchStep 重入守卫 + 窗头标题随动（M-09/M-10）
      wire(nav, "click", (e) => {
        const b = e.target.closest && e.target.closest("button[data-settings]");
        if (!b) return;
        if (switching) { e.stopImmediatePropagation(); e.preventDefault(); return; }
        switching = true;
        later(() => { switching = false; }, 240);
        later(syncSettingsTitle, 0);
      }, { capture: true });
    }
    // 五个分区自带标题 clip 隐藏保节点（唯一可见标题规则；还原表 02c #4）
    for (const tid of ["discovery-settings-title", "qq-title", "workers-settings-title", "claude-settings-title"]) {
      const t = $(tid);
      if (!t) continue;
      subStyle(t, "position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;");
    }
    // 窗头标题随 selectSettings 更新（还原表 02c #1）。原值恒为「设置」，
    // subText 对同值不登记——这里强制登记，保证运行期换名后能还原。
    const settingsTitle = $("settings-title");
    if (settingsTitle) {
      const oldTitle = settingsTitle.textContent;
      restores.push(() => { settingsTitle.textContent = oldTitle; });
    }
    syncSettingsTitle();
    spy(modal, { attributes: true, attributeFilter: ["class"], subtree: true, childList: true }, () => {
      syncSettingsTitle();
      syncThoughtsVisible(); // 三时机之三：设置模态开/关后重申 P3 可见性（02b 机制条款）
      // 关窗焦点接管：模态转 hidden 且焦点落空 → 回 dock 设置图标
      if (modal.classList.contains("hidden")) {
        const ae = document.activeElement;
        const lost = !ae || ae === document.body || (modal.contains(ae));
        if (lost && dockIcons.primary.settings) dockIcons.primary.settings.focus({ preventScroll: true });
      }
    });

    // AliceUI 拦截：thoughts 分区 → P3 跨页（02c 六.1：拦截责任在 orbit.js）
    const ui = window.AliceUI;
    if (ui && typeof ui.selectSettings === "function") {
      const origSelect = ui.selectSettings, origOpen = ui.openSettings;
      ui.selectSettings = function (key) {
        if (key === "thoughts") { route("thoughts"); syncThoughtsVisible(); return; }
        return origSelect.apply(this, arguments);
      };
      ui.openSettings = function (key) {
        if (key === "thoughts") { route("thoughts"); syncThoughtsVisible(); return; }
        return origOpen.apply(this, arguments);
      };
      undos.push(() => { ui.selectSettings = origSelect; ui.openSettings = origOpen; });
    }
  }
  function syncSettingsTitle() {
    const body = document.querySelector("#settings-modal .settings-body");
    const title = $("settings-title");
    if (!body || !title) return;
    const cur = body.querySelector('[data-settings-section]:not(.hidden)');
    if (!cur) return;
    const key = cur.getAttribute("data-settings-section");
    const name = SECTION_NAMES[key];
    if (name && title.textContent !== name) title.textContent = name; // 首次写入已被 subText 登记还原
  }

  /* ---------- 十一、页面骨架与搬移（结构壳：02a/02b/02c） ---------- */

  function installPages() {
    const app = $("app");

    // P1：#orbit-main 舞台，#chat 整体搬入（#app 内，原位标记还原）
    const chat = $("chat");
    const main = document.createElement("div");
    main.id = "orbit-main";
    main.setAttribute("data-density", "wide");
    if (chat) { app.insertBefore(main, chat); moveNode(chat, main); }
    undos.push(() => main.remove());   // LIFO：还原时先由 marker 把 chat 归位，再摘空壳
    pages.chat = main;

    // P2：#orbit-page-discovery；#discovery 整容器搬入 + 容器级包装（02b）
    const discovery = $("discovery");
    const p2 = document.createElement("section");
    p2.id = "orbit-page-discovery";
    p2.className = "orbit-page";
    p2.setAttribute("data-density", "wide");
    app.insertBefore(p2, app.querySelector(".terminal-foot") || null);
    undos.push(() => p2.remove());
    if (discovery) {
      moveNode(discovery, p2);
      subFlag(discovery, "hidden", false); // 面板可见性归页容器（还原表 02b #1：回原 hidden）
      // app.js 用 .hidden 类 + 属性双通道隐藏面板：类通道在入页时一并摘除（还原回原类串）
      unhideClass(discovery);
      // 双列包装：grid 只挂 .orbit-body（R3 P1-2 防棋盘化）
      const body = document.createElement("div");
      body.className = "orbit-body";
      const side = document.createElement("div");
      side.className = "orbit-body-side";
      const mainCol = document.createElement("div");
      mainCol.className = "orbit-body-main";
      // 左列：策略抽屉（#discovery-settings-box 搬入，须晚于 discovery.js:139 搬移——脚本序已保证）
      const drawer = document.createElement("details");
      drawer.id = "orbit-strategy-drawer";
      drawer.className = "orbit-drawer";
      const sum = document.createElement("summary");
      sum.textContent = "寻找策略";
      drawer.appendChild(sum);
      const box = $("discovery-settings-box");
      if (box) moveNode(box, drawer);
      // 右主列：行动区 + 结果区 + 偏好
      const overview = discovery.querySelector(".discovery-overview");
      const results = discovery.querySelector(".discovery-results");
      const prefs = $("discovery-preferences");
      moveNode(overview, mainCol);
      moveNode(results, mainCol);
      moveNode(prefs, mainCol);
      // 结果区说明句：标题悬停承接（02b：短版入「找到的资料」悬停）
      const resHead = results && results.querySelector("h2");
      if (resHead) subAttr(resHead, "title", "原文与判断分开保留");
      // 偏好 note ≈49 字收纳：隐藏 + 头部悬停短句（02b P1-2）
      const note = document.querySelector(".discovery-preferences-note");
      if (note) {
        subStyle(note, "display:none");
        const prefHead = document.querySelector(".discovery-preferences-head");
        if (prefHead) subAttr(prefHead, "title", "平台即点即存；草稿会保留");
      }
      // 左列余项：见闻维护 / 活动用量（原 details 原样入列）
      const learn = $("discovery-learning-preview");
      const task = document.querySelector("details.discovery-task");
      moveNode(learn, side);
      moveNode(task, side);
      moveNode(drawer, side);
      // interests-box 初始 open 移除（02b 裁定请求 3，还原恢复）
      const interests = $("discovery-interests-box");
      if (interests) subFlag(interests, "open", false);
      // 偏好浮层包装：CSS 呈浮层抽屉（节点留在面板内）
      if (prefs) { const f = document.createElement("div"); f.className = "orbit-float"; wrapNode(prefs, f); }
      body.appendChild(side);
      body.appendChild(mainCol);
      discovery.appendChild(body);
      // app.js 运行时可能给面板加 hidden（导航语义残留）：orbit 期间剥离
      spy(discovery, { attributes: true, attributeFilter: ["class"] }, () => {
        if (discovery.classList.contains("hidden")) discovery.classList.remove("hidden");
      });
    }
    pages.discovery = p2;

    // P3：#orbit-page-thoughts；#thought-settings 整容器搬入 + 双列/抽屉栈（02b）
    const thought = $("thought-settings");
    const p3 = document.createElement("section");
    p3.id = "orbit-page-thoughts";
    p3.className = "orbit-page";
    p3.setAttribute("data-density", "wide");
    app.insertBefore(p3, app.querySelector(".terminal-foot") || null);
    undos.push(() => p3.remove());
    if (thought) {
      moveNode(thought, p3);
      subFlag(thought, "hidden", false);
      unhideClass(thought);
      const body = document.createElement("div");
      body.className = "orbit-body";
      const mainCol = document.createElement("div");
      const side = document.createElement("div");
      side.className = "orbit-drawer-stack";
      // 分类：探索与联系设置 details 与其余运维 details 入右列；已解决入右列；thought-detail 留主列
      const opsDrawer = document.createElement("details");
      opsDrawer.id = "orbit-thought-ops";
      opsDrawer.className = "orbit-drawer";
      const opsSum = document.createElement("summary");
      opsSum.textContent = "运行记录";
      opsSum.setAttribute("data-tip", "探索用量、调度、队列与事件");
      opsDrawer.appendChild(opsSum);
      for (const d of [...thought.querySelectorAll(":scope > details")]) {
        const s = d.querySelector(":scope > summary");
        const name = s ? s.textContent.trim() : "";
        if (d.id === "thought-detail") continue; // 详情浮层留主列
        if (name.includes("探索与联系设置") || name.includes("已解决")) moveNode(d, side);
        else moveNode(d, opsDrawer);
      }
      moveNode(opsDrawer, side);
      // 主列 = 其余全部子节点
      for (const child of [...thought.childNodes]) {
        if (child === body || child.nodeType !== 1) continue;
        moveNode(child, mainCol);
      }
      // 44 字睡眠提示：隐藏换 15 字替身（轮次 3 已批运行时替换）
      const hint = [...thought.querySelectorAll(".settings-hint, p")]
        .find((p) => p.textContent.includes("清醒时选择兴趣探索"));
      if (hint) {
        subStyle(hint, "display:none");
        const rep = document.createElement("p");
        rep.className = "orbit-thought-hint";
        rep.textContent = "睡眠待机，清醒自主；消息照常回应。";
        rep.title = hint.textContent.trim();
        mainCol.insertBefore(rep, mainCol.firstChild.nextSibling || null);
        undos.push(() => rep.remove());
      }
      // #thought-run 文案替换（「调用模型」入悬停）
      const run = $("thought-run");
      if (run) { subText(run, "现在探索一轮"); subAttr(run, "title", "调用模型，计入后台额度"); }
      // 疑问空态收纳：只读 observer + h3 悬停（轮次 4 钉死模板写死文案计入）
      const questions = $("thought-questions");
      if (questions) {
        const tuck = () => {
          const empty = questions.textContent.trim() === "暂时没有未解决的疑问。";
          questions.style.display = empty ? "none" : "";
        };
        tuck();
        spy(questions, { childList: true, characterData: true, subtree: true }, tuck);
        undos.push(() => { questions.style.display = ""; });
        const qh3 = [...thought.querySelectorAll("h3")].find((h) => h.textContent.includes("未解决的疑问"));
        if (qh3) subAttr(qh3, "title", "暂时没有未解决的疑问");
      }
      // 思辨提示句隐藏 + h3 悬停
      const stanceHint = [...thought.querySelectorAll("p, .obs-hint, .settings-hint")]
        .find((p) => p.textContent.length > 20 && p.textContent.includes("思辨"));
      if (stanceHint) subStyle(stanceHint, "display:none");
      // thought-detail 程序化 open 时自动展开运行记录抽屉（02b 交互点）
      const detail = $("thought-detail");
      if (detail) {
        spy(detail, { attributes: true, attributeFilter: ["open"] }, () => {
          if (detail.open) opsDrawer.open = true;
        });
      }
      body.appendChild(mainCol);
      body.appendChild(side);
      thought.appendChild(body);
      // app.js selectSettings 会给本 section 切 hidden：orbit 期间剥离（P3 永远可见）
      spy(thought, { attributes: true, attributeFilter: ["class"] }, () => {
        if (thought.classList.contains("hidden")) thought.classList.remove("hidden");
      });
    }
    pages.thoughts = p3;

    // P4：#orbit-page-settings hub（02c §一：新建，无现有节点搬入）
    const p4 = document.createElement("section");
    p4.id = "orbit-page-settings";
    p4.className = "orbit-page";
    p4.setAttribute("data-density", "wide");
    const head = document.createElement("div");
    head.className = "orbit-page-head";
    const kicker = document.createElement("p");
    kicker.className = "orbit-kicker";
    kicker.textContent = "04 / SETTINGS";
    const h1 = document.createElement("h1");
    h1.textContent = "设置";
    const lede = document.createElement("p");
    lede.className = "orbit-lede";
    lede.textContent = "连接、外观与授权按分区打开。";
    head.append(kicker, h1, lede);
    const grid = document.createElement("div");
    grid.className = "orbit-hub-grid";
    const HUB = [
      ["general", "对话与模型", "chat"],
      ["appearance", "外观", "palette"],
      ["discovery", "联网搜索", "search"],
      ["qq", "QQ 连接", "qq"],
      ["workers", "DSH 授权", "dsh"],
      ["claude", "Claude 授权", "claude"],
    ];
    for (const [key, name, icon] of HUB) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "orbit-hub-item";
      b.setAttribute("data-tip", name);
      const s = svg(icon);
      const strong = document.createElement("strong");
      strong.textContent = name;
      b.append(s, strong);
      wire(b, "click", () => {
        if (switching) return; // 纪元重入守卫同源
        switching = true;
        later(() => { switching = false; }, 240);
        if (window.AliceUI) window.AliceUI.openSettings(key);
      });
      grid.appendChild(b);
    }
    // 跨页跳转行（裁定②）：不带 data-settings，走 orbit 路由
    const jump = document.createElement("button");
    jump.type = "button";
    jump.className = "orbit-hub-item";
    jump.setAttribute("data-jump", "");
    jump.setAttribute("data-tip", "打开思想与探索");
    const js = svg("stars");
    const jn = document.createElement("strong");
    jn.textContent = "去思想与探索";
    jump.append(js, jn);
    wire(jump, "click", () => route("thoughts"));
    grid.appendChild(jump);
    p4.append(head, grid);
    app.insertBefore(p4, app.querySelector(".terminal-foot") || null);
    undos.push(() => p4.remove());
    pages.settings = p4;

    // OB1/OB2：fixed 包装（03b §五；#onboarding/#welcome 转 display:contents 由 CSS 承接）
    for (const [screenId, wrapId] of [["onboarding", "orbit-page-onboarding"], ["welcome", "orbit-page-welcome"]]) {
      const screen = $(screenId);
      if (!screen) continue;
      const wrap = document.createElement("div");
      wrap.id = wrapId;
      wrap.setAttribute("data-density", "wide");
      wrapNode(screen, wrap);
      // 内屏 hidden ↔ 包装 display 同步（fixed 包装不能在空态挡点击）
      const sync = () => {
        const off = screen.classList.contains("hidden");
        wrap.style.display = off ? "none" : "";
        if (!off) route("onboarding"); else route(currentPage);
      };
      sync();
      spy(screen, { attributes: true, attributeFilter: ["class"] }, sync);
    }

    // 旧导航整体隐藏（甲-09：不得残留半空导航；节点保留供 click 代理）
    subStyle($("app-nav"), "display:none");
    subStyle(document.querySelector(".terminal-bar"), "display:none");
    subStyle(document.querySelector(".terminal-foot"), "display:none");

    // OB 期间 #app hidden ↔ 路由回 onboarding
    const appEl = $("app");
    spy(appEl, { attributes: true, attributeFilter: ["class"] }, () => route(currentPage));
  }

  /* ---------- 十二、运行时文案改写（还原表逐项登记） ---------- */

  function applyTextRewrites() {
    // 甲组还原表 #1：chat-new-attempt（原句入悬停）
    const attempt = $("chat-new-attempt");
    if (attempt) { subText(attempt, "作为新一轮发送"); subAttr(attempt, "title", "允许作为新一轮发送"); }
    // P1 顶栏图标化：#chat-refresh（同步记录 → 图标，还原表 #19 CSS 侧已备案）
    const refresh = $("chat-refresh");
    if (refresh) {
      subClass(refresh, "orbit-icon-entry");
      subAttr(refresh, "data-tip", "同步记录");
      subAttr(refresh, "aria-label", "同步记录");
      subHtml(refresh, ""); refresh.appendChild(svg("sync"));
    }
    // M9 还原表 #2-#12
    const evalHeadStrong = document.querySelector("#evaluations .obs-head strong");
    subText(evalHeadStrong, "P1核查");
    const feeDetails = document.querySelector("#evaluations details.eval-section[open], #evaluations details.eval-section");
    if (feeDetails) {
      subText(feeDetails.querySelector(":scope > summary"), "费用许可");
      subFlag(feeDetails, "open", false); // #8：初始 open 移除
      // #9：325 行边界说明 p 搬入折叠顶部
      const firstHint = document.querySelector("#evaluations .obs-scroll > p.obs-hint");
      if (firstHint && firstHint.parentElement === feeDetails.parentElement) moveNode(firstHint, feeDetails, feeDetails.querySelector(":scope > summary").nextSibling);
      // #12：绑定三钮搬出折叠常驻（甲-02 主路径一步）
      const arm = $("eval-arm"), disarm = $("eval-disarm"), forget = $("eval-forget-uncertain");
      const after = feeDetails.nextSibling;
      const holder = document.createElement("div");
      holder.className = "orbit-entry-row";
      feeDetails.parentElement.insertBefore(holder, after);
      moveNode(arm, holder); moveNode(disarm, holder); moveNode(forget, holder);
      undos.push(() => holder.remove());
      subText(arm, "授权并核查本页下一轮"); subAttr(arm, "title", "最多 2 次模型请求");
      subText(forget, "清除本页待确认标记"); subAttr(forget, "title", "不重发、不取消远端任务");
      // #20：armed 或详情打开时隐藏 arm（MutationObserver 显隐规则）
      const detail = $("eval-detail");
      const syncArm = () => {
        const armed = disarm && !disarm.classList.contains("hidden");
        const detailOpen = detail && !detail.classList.contains("hidden");
        if (arm) {
          const want = !!(armed || detailOpen);
          if (want && !arm.hidden) { arm.dataset.orbitHidden = "1"; arm.hidden = true; }
          else if (!want && arm.dataset.orbitHidden) { delete arm.dataset.orbitHidden; arm.hidden = false; }
        }
      };
      syncArm();
      spy($("evaluations"), { attributes: true, attributeFilter: ["class"], subtree: true }, syncArm);
      undos.push(() => {
        if (arm && arm.dataset.orbitHidden) { delete arm.dataset.orbitHidden; arm.hidden = false; } // 还原 evaluations.js 原显隐
      });
    }
    const evalSections = [...document.querySelectorAll("#evaluations .obs-scroll > .eval-section")];
    const recordSection = evalSections.find((s) => s.querySelector("h3") && s.querySelector("h3").textContent.includes("核查记录"));
    if (recordSection && recordSection.tagName === "SECTION") {
      // #10：容器级包 details（h3 → summary；其余子节点原序搬入折叠体）
      const fold = document.createElement("details");
      fold.className = "orbit-fold eval-section";
      const sum = document.createElement("summary");
      sum.appendChild(recordSection.querySelector("h3"));
      const parent = recordSection.parentElement;
      const marker = document.createComment("orbit-fold:eval-record");
      parent.insertBefore(marker, recordSection);
      fold.appendChild(sum);
      for (const c of [...recordSection.childNodes]) fold.appendChild(c);
      parent.insertBefore(fold, marker);
      marker.remove();
      subAttr(sum, "data-tip", "点击授权会建一条记录；结束后记录保留");
      undos.push(() => {
        // 拆除包装：正文回 section、h3 出 summary 回首位，标记位还原
        const body2 = [...fold.childNodes].filter((n) => n !== sum);
        for (const n of body2) recordSection.appendChild(n);
        const h3 = sum.querySelector("h3");
        if (h3) recordSection.insertBefore(h3, recordSection.firstChild);
        marker.replaceWith(recordSection);
        fold.remove();
      });
    }
    const reveal = $("eval-reveal");
    if (reveal) {
      subText(reveal, "查看结果");
      subAttr(reveal, "title", "点击后记录为已查看，不可恢复盲标");
      // #11：349 行盲标说明 p 搬进「技术详情」details
      const tech = reveal.nextElementSibling;
      if (tech && tech.matches("p.obs-hint")) {
        const runBox = $("eval-run");
        if (runBox) moveNode(tech, runBox.parentElement);
      }
    }
    const closeEval = $("eval-close");
    subAttr(closeEval, "title", "关闭"); // #7
    const calibSummary = [...document.querySelectorAll("#evaluations details.eval-section > summary")]
      .find((s) => s.textContent.includes("人工校准"));
    subText(calibSummary, "校准记录"); // #4
    // M10 还原表 #13：#workers-empty 替身 + observer 重应用（甲-18）
    const workersEmpty = $("workers-empty");
    if (workersEmpty) {
      const SUBS = {
        session: "先在 设置 → DSH 授权，再在对话里告诉 Alice 要做什么。",
        archived: "暂无已归档任务，归档后在会话列表查看。",
      };
      let lastApplied = "";
      const applyEmpty = () => {
        const archived = ($("workers-archived") && $("workers-archived").getAttribute("aria-pressed") === "true");
        const want = archived ? SUBS.archived : SUBS.session;
        if (workersEmpty.textContent !== want && workersEmpty.textContent !== lastApplied) return; // 宿主写中
        if (workersEmpty.textContent !== want) { lastApplied = want; workersEmpty.textContent = want; }
        else lastApplied = want;
      };
      applyEmpty();
      spy(workersEmpty, { childList: true, characterData: true, subtree: true }, applyEmpty);
      undos.push(() => { try { workersEmpty.textContent = "还没有协作任务。请在“设置 → DSH”连接并授权任务范围，然后在主聊天告诉 Alice 要做什么。"; } catch (_) {} });
    }
    // 还原表 #15：rows=0 时隐藏 #workers-chat-empty（甲-19）
    const list = $("workers-list"), chatEmpty = $("workers-chat-empty");
    if (list && chatEmpty) {
      const syncRows = () => {
        const zero = list.children.length === 0;
        if (zero && !chatEmpty.hidden) { chatEmpty.dataset.orbitRows = "1"; chatEmpty.hidden = true; }
        else if (!zero && chatEmpty.dataset.orbitRows) { delete chatEmpty.dataset.orbitRows; chatEmpty.hidden = false; }
      };
      syncRows();
      spy(list, { childList: true }, syncRows);
      undos.push(() => {
        if (chatEmpty.dataset.orbitRows) { delete chatEmpty.dataset.orbitRows; chatEmpty.hidden = false; }
      });
    }
    // M11 还原表 #17/#18
    const soothe = document.querySelector("#shutdown-screen .seed-card p:not([id])");
    if (soothe) { subText(soothe, "记忆已保存，下次继续。"); subAttr(soothe, "title", "对话、人格与记忆都在本机"); }
    const restart = $("restart-instructions");
    if (restart) subText(restart.querySelector(":scope > summary"), "重启命令");
    // M2（02c #7）
    const cheapLabel = document.querySelector('label[for="model-cheap"]');
    if (cheapLabel) { subText(cheapLabel, "整理模型"); subAttr(cheapLabel, "title", "记忆与整理模型"); }
    const mFetch = $("model-fetch"), mTest = $("model-test"), mSave = $("model-save");
    if (mFetch) { subText(mFetch, "获取列表"); subAttr(mFetch, "title", "获取模型列表"); }
    if (mTest) { subText(mTest, "测试"); subAttr(mTest, "title", "测试候选连接"); }
    if (mSave) { subText(mSave, "保存"); subAttr(mSave, "title", "验证产生少量费用"); }
    // M4：顶部 40 字 settings-hint → 折叠「保存说明」（02c #12）
    const m4hint = document.querySelector("#discovery-settings > p.settings-hint");
    if (m4hint) {
      const fold = document.createElement("details");
      fold.className = "orbit-fold";
      const sum = document.createElement("summary");
      sum.textContent = "保存说明";
      const parent = m4hint.parentElement;
      const marker = document.createComment("orbit-fold:m4-save");
      parent.insertBefore(marker, m4hint);
      fold.appendChild(sum);
      fold.appendChild(m4hint);
      parent.insertBefore(fold, marker);
      marker.remove();
      undos.push(() => { marker.replaceWith(m4hint); fold.remove(); });
    }
    // M6（02c #17 部分：预算相关常驻文案）
    const wsLabel = document.querySelector('label[for="workers-workspace"]');
    if (wsLabel) { subText(wsLabel, "工作目录"); subAttr(wsLabel, "title", "本次任务工作目录（绝对路径）"); }
    const adv = $("workers-advanced");
    if (adv) { subText(adv.querySelector(":scope > summary"), "高级"); subAttr(adv.querySelector(":scope > summary"), "title", "高级设置 · 手动令牌与更换"); }
    const syncSave = $("workers-task-settings-save");
    if (syncSave) { subText(syncSave, "保存同步"); subAttr(syncSave, "title", "保存同步设置"); }
    const workersEnabled = $("workers-enabled");
    if (workersEnabled) {
      const lb = workersEnabled.closest("label");
      if (lb) { stripTexts(lb); subAttr(lb, "title", "允许 Alice 在以下范围调用 DSH"); const t = document.createElement("span"); t.textContent = "允许调用 DSH"; lb.appendChild(t); undos.push(() => t.remove()); }
    }
    const runsLabel = document.querySelector('label[for="workers-max-runs"]');
    if (runsLabel) { const inp = $("workers-max-runs"); stripTexts(runsLabel); const t = document.createElement("span"); t.textContent = "执行次数"; runsLabel.insertBefore(t, inp); subAttr(runsLabel, "title", "本次授权总执行次数"); undos.push(() => t.remove()); }
    const secsLabel = document.querySelector('label[for="workers-max-seconds"]');
    if (secsLabel) { const inp = $("workers-max-seconds"); stripTexts(secsLabel); const t = document.createElement("span"); t.textContent = "最长秒数"; secsLabel.insertBefore(t, inp); subAttr(secsLabel, "title", "每次执行最长秒数"); undos.push(() => t.remove()); }
    // OB1（02b 还原表 #12）：45 字 hint 替身；原句全文入悬停（title 先于正文改写登记）
    const ob1hint = document.querySelector("#onboarding .onboarding-card .hint");
    if (ob1hint) {
      const original = ob1hint.textContent.trim();
      subAttr(ob1hint, "title", original);
      subText(ob1hint, "Alice 需要模型。选择供应商，填入 API Key。");
    }
    // OB2（02b §五.0 全部运行时适配）
    const tools = document.querySelector(".welcome-tools");
    if (tools) subStyle(tools, "display:none"); // 裁定①：orbit 域隐藏不删除
    const back = $("onboarding-back");
    if (back) { subText(back, "←"); subAttr(back, "title", "返回模型配置"); subAttr(back, "aria-label", "返回模型配置"); }
    const doneStep = document.querySelector("#welcome .onboarding-steps li:first-child");
    if (doneStep) { subText(doneStep, "✓"); subAttr(doneStep, "title", "模型 API 已配置"); subAttr(doneStep, "aria-label", "模型 API 已配置"); }
    const tagsHint = document.querySelector("#tab-tags .hint");
    if (tagsHint) {
      subText(tagsHint, "选 2~4 个气质标签当底色。");
      subAttr(tagsHint, "title", "剩下的性格由经历慢慢长");
    }
    const importMd = $("import-md");
    if (importMd) subAttr(importMd, "placeholder", "# Alice 的人格");
  }

  /* ---------- 十三、状态镜像（OrbitBG + dock 点/徽标） ---------- */

  function stateOf(text) {
    if (!text) return "unknown";
    if (/已连接|已授权|已开启|ready|正常/.test(text)) return "ready";
    if (/错误|失败|断开|error/.test(text)) return "error";
    if (/等待|读取|进行|正在|busy|同步中/.test(text)) return "waiting";
    return "unknown";
  }
  function mirrorState() {
    // OrbitBG：discoveryRunning / thoughtState（单向传入，orbit-bg 不自查业务 DOM）
    const cancel = $("discovery-cancel");
    const run = $("discovery-run");
    const running = !!(cancel && !cancel.classList.contains("hidden")) ||
      !!(run && run.disabled && run.textContent.indexOf("正在寻找") === 0);
    const lifecycle = $("thought-lifecycle");
    const stop = $("thought-stop");
    let thoughtState = 0;
    if (stop && !stop.classList.contains("hidden") && !stop.disabled) thoughtState = 1;
    else if (lifecycle && lifecycle.dataset.state === "sleeping") thoughtState = 2;
    if (window.OrbitBG && typeof window.OrbitBG.setState === "function") {
      window.OrbitBG.setState({ discoveryRunning: running, thoughtState });
    }
    // dock 状态点
    const dots = dockIcons.dots || {};
    const qqBadge = $("qq-badge");
    if (dots.qq && qqBadge) dots.qq.dataset.state = stateOf(qqBadge.textContent);
    const wt = $("worker-tasks");
    if (dots.dsh && wt) dots.dsh.dataset.state = wt.dataset.state || "unknown";
    const clStatus = $("claude-settings-status");
    if (dots.claude) dots.claude.dataset.state = clStatus ? stateOf(clStatus.textContent) : "unknown";
    if (dots.wake && lifecycle) dots.wake.dataset.state = lifecycle.dataset.state === "sleeping" ? "sleeping" : "awake";
    // 徽标：待确认消息 / P1 待绑定 / DSH 未读（M-03）
    const attempt = $("chat-new-attempt"), notice = $("chat-notice");
    const mailOn = (attempt && !attempt.classList.contains("hidden")) ||
      (notice && !notice.classList.contains("hidden") && notice.textContent.trim().length > 0);
    setBadge("mail", mailOn ? 1 : 0);
    const composerEval = $("composer-evaluation");
    setBadge("p1", composerEval && !composerEval.classList.contains("hidden") ? 1 : 0);
    const count = $("workers-count");
    const n = count ? parseInt(count.textContent.replace(/[^0-9]/g, ""), 10) || 0 : 0;
    setBadge("dsh", n);
  }
  /* ---------- 十三·补、字数帽预算文案（02c M2 / 02b P3；轮次 3 批准的运行时替换） ----------
     帽内常驻文案精简，被收起的全文走「说明」悬停卡与控件 title（口径 2/4：按需展开、完整性保留）。 */
  function applyBudgetRewrites() {
    // S7 协作者头像卡：拖拽坐标（inset）是 room 主题留下的，orbit 首装重置到会话区
    // 右下角（还原表登记旧值；拖拽功能不动——worker-tasks.js 继续写 inline inset）。
    const colab = document.querySelector(".collaborator-avatar");
    if (colab) subStyle(colab, "inset: auto 16px 16px auto");
    // —— M2「对话与模型」：四条静态说明收进窗头「说明」悬停卡；Base URL 降为次级 ——
    const m2 = $("model-settings");
    if (m2) {
      const hints = [];
      const collect = (el, label) => {
        if (!el) return;
        const text = (el.textContent || "").replace(/\s+/g, " ").trim();
        if (!text) return;
        subStyle(el, "display:none");
        hints.push(label + "：" + text);
      };
      collect(document.querySelector('section[data-settings-section="general"] .settings-hint'), "思考深度");
      collect(m2.querySelector(":scope > .settings-hint"), "接口与费用");
      collect(m2.querySelector("p.settings-hint"), "整理模型");
      for (const el of m2.querySelectorAll(".settings-hint"))
        if ((el.textContent || "").includes("密钥不回显")) collect(el, "密钥");
      // Base URL：只读展示降次级（完整值挂「供应商」悬停；值随供应商变化，转 07 抽验）
      subStyle($("model-base-url"), "display:none");
      const baseUrlLabel = document.querySelector('label[for="model-base-url"]');
      if (baseUrlLabel) collect(baseUrlLabel, "Base URL");
      const providerLabel = document.querySelector('label[for="model-provider"]');
      const baseUrl = $("model-base-url");
      if (providerLabel && baseUrl)
        subAttr(providerLabel, "title", "Base URL：" + (baseUrl.value || "随供应商自动填入"));
      // 标签/按钮短名（全名入悬停；02c 预算清单原样）。app.js 会在状态变化时重写按钮
      // 文案（fetch 每次成功后重建动作行），短名用维持式 observer 保持。
      const maintain = (node, target) => {
        if (!node) return;
        subText(node, target);
        spy(node, { childList: true, characterData: true, subtree: true }, () => {
          if (node.textContent !== target) node.textContent = target;
        });
      };
      const cheapLabel = document.querySelector('label[for="model-cheap"]');
      if (cheapLabel) { subText(cheapLabel, "整理模型"); subAttr(cheapLabel, "title", "记忆与整理模型"); }
      const fetchBtn = $("model-fetch");
      if (fetchBtn) { maintain(fetchBtn, "获取列表"); subAttr(fetchBtn, "title", "获取模型列表"); }
      const testBtn = $("model-test");
      if (testBtn) { maintain(testBtn, "测试"); subAttr(testBtn, "title", "测试候选连接"); }
      const saveBtn = $("model-save");
      if (saveBtn) { maintain(saveBtn, "保存"); subAttr(saveBtn, "title", "验证并保存可能产生少量 API 费用"); }
      // 窗头「说明」图标：四段全文悬停（触屏点按兜底由 tip 控制器承担）
      if (hints.length) {
        const head = document.querySelector("#settings-modal .orbit-win-head");
        if (head) {
          const info = document.createElement("button");
          info.type = "button";
          info.className = "orbit-icon orbit-icon-entry";
          info.setAttribute("data-tip", hints.join(" ｜ "));
          info.setAttribute("data-tip-long", "");
          info.setAttribute("aria-label", "对话与模型说明");
          info.appendChild(svg("info"));
          head.appendChild(info);
          undos.push(() => info.remove());
        }
      }
    }
    // —— P3 思想与探索：睡眠提示 15 字替身、探索按钮短名、思辨提示收纳（02b 裁定 1） ——
    const run = $("thought-run");
    if (run) { subText(run, "现在探索一轮"); subAttr(run, "title", "调用模型，计入后台额度"); }
    const lifeHint = [...document.querySelectorAll("#thought-settings .settings-hint")]
      .find((el) => (el.textContent || "").includes("清醒时选择兴趣探索"));
    if (lifeHint) {
      subStyle(lifeHint, "display:none");
      const lifecycle = $("thought-lifecycle");
      if (lifecycle) subAttr(lifecycle, "title", (lifeHint.textContent || "").replace(/\s+/g, " ").trim());
    }
    const stanceHint = [...document.querySelectorAll("#thought-settings .settings-hint")]
      .find((el) => (el.textContent || "").includes("这是当前看法"));
    if (stanceHint) {
      subStyle(stanceHint, "display:none");
      const h3 = stanceHint.previousElementSibling;
      if (h3 && h3.tagName === "H3") subAttr(h3, "title", "当前看法，未必已独立验证");
    }
    // 疑问空态句（thoughts.js 运行时写入）：空态文案时收纳为悬停，observer 维持
    const questions = $("thought-questions");
    if (questions) {
      const apply = () => {
        const empty = (questions.textContent || "").includes("暂时没有未解决的疑问");
        questions.style.display = empty ? "none" : "";
        if (empty) questions.setAttribute("title", "暂时没有未解决的疑问");
        else questions.removeAttribute("title");
      };
      apply();
      spy(questions, { childList: true, characterData: true, subtree: true }, apply);
    }
  }

  function installMirrors() {
    const watch = [
      [$("discovery-run"), { attributes: true, attributeFilter: ["disabled"] }],
      [$("discovery-cancel"), { attributes: true, attributeFilter: ["class"] }],
      [$("thought-lifecycle"), { attributes: true, attributeFilter: ["data-state"], childList: true, characterData: true, subtree: true }],
      [$("thought-stop"), { attributes: true, attributeFilter: ["class", "disabled"] }],
      [$("chat-new-attempt"), { attributes: true, attributeFilter: ["class"] }],
      [$("chat-notice"), { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"] }],
      [$("composer-evaluation"), { attributes: true, attributeFilter: ["class"] }],
      [$("workers-count"), { childList: true, characterData: true, subtree: true }],
      [$("qq-badge"), { childList: true, characterData: true, subtree: true }],
      [$("claude-settings-status"), { childList: true, characterData: true, subtree: true }],
    ];
    for (const [node, opts] of watch) if (node) spy(node, opts, mirrorState);
    spy($("worker-tasks"), { attributes: true, attributeFilter: ["data-state"] }, mirrorState);
    every(mirrorState, 5000); // 兜底轮询（动态写入无 attribute 变更的少数路径）
    mirrorState();
  }

  /* ---------- 十四、安装 / 卸载 / 主题监听 ---------- */

  function install() {
    if (installed) return;
    installed = true;
    // 防叠加：异常中断的历史残留（半拆状态）先清掉再建。
    for (const stale of document.querySelectorAll("#orbit-dock, #orbit-main, .orbit-page, .orbit-tip"))
      stale.remove();
    buildDock();
    installTip();
    installPages();
    installSettingsWindow();
    installWindows();
    applyTextRewrites();
    applyBudgetRewrites();
    installMirrors();
    route("chat", { silent: true });
  }
  function uninstall() {
    if (!installed) return;
    installed = false;
    // 1) 值类还原（逆序：后改的先还原）
    for (const fn of restores.reverse()) { try { fn(); } catch (_) {} }
    restores.length = 0;
    // 2) 结构/监听/观察器/计时器（逆序）。单项失败不中断拆除——剩余结构必须清干净，
    //    否则切回 orbit 会叠加旧壳（真机回归抓过这个半拆状态）。
    const failed = [];
    for (const fn of undos.reverse()) {
      try { fn(); } catch (error) { failed.push(String(error && error.message)); }
    }
    undos.length = 0;
    if (failed.length) window.__orbitUndoErrors = failed;
    // 3) 全局残留清理
    delete document.body.dataset.orbitPage;
    for (const key of PAGE_KEYS) delete pages[key];
    currentPage = "chat";
  }

  function applyTheme() {
    const next = root.dataset.theme === "orbit";
    // 诊断面：主题切换计数（真机排查装/卸时序用；量小无扰）
    window.__orbitApplyLog = window.__orbitApplyLog || [];
    window.__orbitApplyLog.push([next ? "in?" : "out?", installed ? "installed" : "not"]);
    if (next === installed) return;
    if (next) {
      try { install(); }
      catch (error) {
        // 安装失败不允许半残 UI 运行：回退卸载并留下诊断面（真机排查用）。
        installed = true;          // 强制走过 uninstall 的早退守卫，残留清干净
        try { uninstall(); } catch (_) {}
        window.__orbitInstallError = `${error && error.message} @ ${(error && error.stack || "").split("\n")[1] || "?"}`;
        console.error("[orbit] install failed:", error);
      }
    } else uninstall();
  }
  function ready() {
    applyTheme();
    new MutationObserver(applyTheme).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    // 三时机之一：窗口可见性恢复时重申 P3（02b 冻结对抗）
    document.addEventListener("visibilitychange", syncThoughtsVisible, { signal });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
})();

/* =========================================================================
 * 裁剪与降级登记（待真机验证后补）：
 * 1) P2 策略抽屉四簇折叠（开关与时段/范围与方式/额度与容量/取材与工作量）
 *    未拆——#discovery-settings-box 整体入抽屉，簇级分组待真机核对字段结构后补。
 * 2) M1 人格窗 foot「更多」收纳（.orbit-foot-more）未实施——#modal 按钮区为
 *    app.js 动态重建，防交叉写入需独立 observer 编排，登记后补。
 * 3) M5 QQ 六句静态文案的「连接说明/使用限制」折叠、M6/M7 分步包装（步点条
 *    交互式）、M8 视图级「说明」折叠与快照比较图标化、M10「更多」popover 保留
 *    现状未动——均属 02x 分步/折叠细颗粒适配，先保功能可达，字数帽待真机抽验。
 * 4) worker-message-nav 三按钮、eval-auto、obs 刷新/清空等图标化（R1 档）未
 *    一一挂 data-tip；「更多」popover、来源行图标化沿用现状。
 * 5) P4 hub 行健康徽标（QQ/DSH/Claude/发现态镜像到行内）未挂——状态点已在
 *    dock 系统簇镜像，hub 行徽标登记后补。
 * 6) S7 悬停卡 12 秒收回计时（03c 5.5）未实施——worker-tasks.js 是否已有
 *    等价驻留逻辑转 06 核对后再定归属。
 * ========================================================================= */
