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
 *   4) 页面和召唤窗的展示调整与还原。共用设置窗管理分区与表单。
 *   5) MutationObserver 同步召唤窗、核查许可与任务状态。
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
  /** restores：值类还原（文本、属性、样式与状态），逆序执行 */
  const restores = [];
  const timers = new Set();

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

  /** 只替换纯文本，保留标签内的控件及其关联。 */
  function subText(node, text) {
    if (!node || node.childElementCount) return;
    const old = node.textContent;
    if (old === text) return;
    node.textContent = text;
    restores.push(() => {
      if (!node.childElementCount && node.textContent === text) node.textContent = old;
    });
  }
  function useIcon(node, name, cls) {
    if (node.childElementCount) return;
    const children = [...node.childNodes];
    const icon = svg(name, cls);
    node.replaceChildren(icon);
    restores.push(() => {
      if (node.childNodes.length === 1 && node.firstChild === icon) node.replaceChildren(...children);
    });
  }
  /** 属性写入（placeholder/title/aria-label/tabindex…），null 表示原本无 */
  function subAttr(node, name, value) {
    if (!node) return;
    const old = node.getAttribute(name);
    if (old === value) return;
    node.setAttribute(name, value);
    restores.push(() => {
      if (node.getAttribute(name) !== value) return;
      if (old === null) node.removeAttribute(name); else node.setAttribute(name, old);
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
  /** 还原时只移除 Orbit 添加的类。 */
  function subClass(node, cls) {
    if (!node || node.classList.contains(cls)) return;
    node.classList.add(cls);
    restores.push(() => node.classList.remove(cls));
  }
  /** open/hidden 类布尔态改写（记旧值） */
  function subFlag(node, prop, value) {
    if (!node || node[prop] === value) return;
    const old = node[prop];
    node[prop] = value;
    restores.push(() => { node[prop] = old; });
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
    undos.push(() => { tipHide(); tipEl.remove(); tipEl = null; });
    wire(document, "pointerover", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (!t || t === tipTarget || e.pointerType === "touch") return;
      clearTimeout(holdT);
      tipHide();
      tipTarget = t; // 占位防重入；真正展示在延迟后
      holdT = later(() => { if (tipTarget === t) tipShow(t); }, HOLD_MOUSE);
    }, true);
    wire(document, "pointerout", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (t && t === tipTarget && !(e.relatedTarget && t.contains(e.relatedTarget))) tipHide();
    }, true);
    wire(document, "focusin", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (t) tipShow(t); // 键盘 0ms
    }, true);
    wire(document, "focusout", (e) => {
      if (tipTarget && e.target.closest && e.target.closest("[data-tip]") === tipTarget) tipHide();
    }, true);
    wire(document, "pointerdown", (e) => {
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
    }, true);
    wire(document, "pointermove", (e) => {
      if (e.pointerType !== "touch" || !holdT) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) < MOVE_CANCEL) return;
      clearTimeout(holdT); holdT = 0; tipTarget = null; // 位移 >8px 才清除（R4 P1-3）
    }, true);
    ["pointerup", "pointercancel"].forEach((t) =>
      wire(document, t, () => { clearTimeout(holdT); holdT = 0; }, true));
    wire(document, "keydown", (e) => { if (e.key === "Escape") tipHide(); }, true);
  }

  /* ---------- 五、页面路由与切页五连（04a §1） ---------- */

  const PAGE_KEYS = ["chat", "discovery", "thoughts", "settings"];
  const pages = {};            // key → 容器元素（install 时填充）
  let currentPage = "chat";

  function writePage(name) {
    if (document.body.dataset.orbitPage === name) return;
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
    if (name === "discovery") window.AliceDiscovery?.open?.(false);
    else window.AliceDiscovery?.close(false);
  }

  function effectivePage() {
    const onb = $("onboarding"), wel = $("welcome"), app = $("app");
    if ((onb && !onb.classList.contains("hidden")) || (wel && !wel.classList.contains("hidden"))) return "onboarding";
    if (app && !app.classList.contains("hidden")) return currentPage;
    return "onboarding"; // app 未开且无引导卡：视为 onboarding 档（球位下缘让位）
  }
  function route(name) {
    currentPage = name;
    writePage(effectivePage());
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
  function makeWindow({ id, title, size, ariaLabel }) {
    const d = document.createElement("dialog");
    d.id = id;
    d.className = "orbit-window" + (size ? " orbit-window--" + size : "");
    d.setAttribute("data-density", "mid");   // R5d P1-2：窗头在 body 外，须在 dialog 层挂档否则 --den-* 归零
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
      if (sum) {
        subAttr(sum, "data-tip", "记忆概览");
        subAttr(sum, "aria-label", "记忆概览");
        useIcon(sum, "book", "orbit-symbol");
      }
      wire(memSummary, "click", (e) => { e.preventDefault(); e.stopPropagation(); openOrbitWindow(win, sum); }, { capture: true });
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
        if (!aside.classList.contains("hidden")) {
          if (opts.onClose) opts.onClose();
          else aside.classList.add("hidden");
        }
        const opener = dialog._orbitOpener;
        if (!(opener && opener.isConnected && opener.getClientRects().length) &&
            dialog._orbitDockFallback && dialog._orbitDockFallback.isConnected) {
          dialog._orbitDockFallback.focus({ preventScroll: true }); // 焦点接管回 dock 图标（02c 0.5）
        }
      });
      sync();
    };

    const obsWin = makeWindow({ id: "orbit-window-observer", ariaLabel: "记忆观察器" });
    subText($("obs-close"), "关闭观察器");
    follow("observer", obsWin, {
      dockFallback: dockIcons.tools.observer,
      onClose: () => window.AliceObserver?.close(),
    });

    const evalWin = makeWindow({ id: "orbit-win-evaluations", size: "md", ariaLabel: "P1 核查" });
    evalWin._orbitDockFallback = dockIcons.tools.p1;
    const evalHead = document.querySelector("#evaluations .obs-head");
    if (evalHead) subClass(evalHead, "orbit-win-head");
    follow("evaluations", evalWin, { dockFallback: dockIcons.tools.p1 });

    const workerWin = makeWindow({ id: "orbit-win-workers", ariaLabel: "DSH 协作会话" }); // 窗头无标题文本（03c 硬约束）
    workerWin._orbitDockFallback = dockIcons.system.dsh;
    follow("worker-tasks", workerWin, { dockFallback: dockIcons.system.dsh });
  }

  /* ---------- 十、设置窗与独立页面的入口 ---------- */

  function installSettingsRoutes() {
    const ui = window.AliceUI;
    if (!ui) return;
    const modal = $("settings-modal");
    const select = ui.selectSettings;
    const open = ui.openSettings;

    function showThoughts() {
      if (!modal.classList.contains("hidden")) ui.closeSettings();
      route("thoughts");
      later(() => {
        if (document.body.dataset.orbitPage === "thoughts" && modal.classList.contains("hidden")) {
          $("thought-title")?.focus({ preventScroll: true });
        }
      }, 0);
    }

    ui.selectSettings = (section) => section === "thoughts" ? showThoughts() : select(section);
    ui.openSettings = (section) => section === "thoughts" ? showThoughts() : open(section);
    undos.push(() => { ui.selectSettings = select; ui.openSettings = open; });
    if (modal.querySelector('[data-settings="thoughts"][aria-pressed="true"]')) {
      select("general");
      if (!modal.classList.contains("hidden")) showThoughts();
    }
    spy(modal, { attributes: true, attributeFilter: ["class"] }, () => {
      if (modal.classList.contains("hidden") && document.activeElement === document.body) {
        dockIcons.primary.settings.focus({ preventScroll: true });
      }
    });
  }


  /* ---------- 十一、页面骨架与搬移（结构壳：02a/02b/02c） ---------- */

  function installPages() {
    const app = $("app");

    // P1：#orbit-main 舞台，#chat 整体搬入（#app 内，原位标记还原）
    const chat = $("chat");
    const main = document.createElement("div");
    main.id = "orbit-main";
    main.setAttribute("data-density", "wide");
    undos.push(() => main.remove());
    if (chat) { app.insertBefore(main, chat); moveNode(chat, main); }
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
      const body = document.createElement("div");
      body.className = "orbit-body";
      const side = document.createElement("div");
      side.className = "orbit-body-side";
      const mainCol = document.createElement("div");
      mainCol.className = "orbit-body-main";
      body.append(side, mainCol);
      discovery.querySelector(".discovery-grid").appendChild(body);
      undos.push(() => body.remove());
      moveNode(discovery.querySelector(".discovery-overview"), side);
      moveNode($("discovery-learning-preview"), side);
      moveNode(discovery.querySelector("details.discovery-task"), side);
      moveNode(discovery.querySelector(".discovery-results"), mainCol);
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
      thought.classList.remove("hidden");
      undos.push(() => thought.classList.add("hidden"));
      subClass(thought.querySelector(".thought-head"), "orbit-page-head");
      subClass($("thought-lifecycle"), "orbit-state-chip");

      const policy = $("thought-config").closest("details");
      const records = [];
      for (let node = policy.nextElementSibling; node; node = node.nextElementSibling) {
        records.push(node);
      }
      const body = document.createElement("div");
      body.className = "orbit-body";
      const mainCol = document.createElement("div");
      mainCol.className = "orbit-thought-column";
      const side = document.createElement("div");
      side.className = "orbit-thought-column";
      const history = document.createElement("details");
      history.className = "settings-group";
      const summary = document.createElement("summary");
      summary.textContent = "运行记录";
      history.appendChild(summary);
      side.appendChild(history);
      body.append(mainCol, side);
      thought.appendChild(body);
      undos.push(() => body.remove());
      moveNode(policy, side, history);
      for (const node of records) {
        moveNode(node, node.matches("details:not(#thought-detail)") ? history : mainCol);
      }
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
      ["observer", "上下文观测", "layers"],
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
      wire(b, "click", () => window.AliceUI?.openSettings(key));
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
        route(currentPage);
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
    wire($("chat-nav"), "click", () => route("chat"), true);
  }

  /* ---------- 十二、运行时文案改写（还原表逐项登记） ---------- */

  function applyTextRewrites() {
    // 甲组还原表 #1：chat-new-attempt（原句入悬停）
    const attempt = $("chat-new-attempt");
    if (attempt) { subText(attempt, "作为新一轮发送"); subAttr(attempt, "title", "允许作为新一轮发送"); }
    // P1 顶栏图标化：#chat-refresh 图标化并搬进顶栏动作区（02a：同步记录→顶栏图标；
    // sync-bar 空壳隐藏——49px 空横条只挂一个图标是排版噪音，还原表 #19）
    const refresh = $("chat-refresh");
    if (refresh) {
      subClass(refresh, "orbit-icon-entry");
      subAttr(refresh, "data-tip", "同步记录");
      subAttr(refresh, "aria-label", "同步记录");
      useIcon(refresh, "sync");
      const headerActions = document.querySelector(".chat-header-actions");
      if (headerActions) moveNode(refresh, headerActions);
      const syncBar = document.querySelector(".chat-sync-bar");
      if (syncBar) {
        const visible = [...syncBar.children].some((c) =>
          c !== refresh && !c.classList.contains("hidden"));
        if (!visible) subStyle(syncBar, "display:none");
      }
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
      recordSection.replaceWith(fold);
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
  function installPanelLayout() {
    const composerWrap = document.querySelector(".composer")?.parentElement;
    if (composerWrap?.tagName === "FOOTER") subClass(composerWrap, "orbit-composer-wrap");
    // —— M9 P1 核查：额度/人工校准两个表单区折叠（02c 三抽屉落地——费用许可本就是 details，
    //    这两个是 section.eval-section 常驻展开）；确认句=口径 4 豁免保留在折叠内 ——
    const evAsideM9 = $("evaluations");
    if (evAsideM9) {
      for (const sec of evAsideM9.querySelectorAll("section.eval-section")) {
        const h3 = sec.querySelector(":scope > h3");
        if (!h3) continue;
        const name = h3.textContent.trim() || "说明";
        if (name.includes("费用许可")) continue;   // details 版已在别处折叠
        const d = document.createElement("details");
        d.className = "eval-section orbit-fold";
        const sum = document.createElement("summary");
        sum.textContent = name;
        d.appendChild(sum);
        subStyle(h3, "display:none");   // summary 接管标题
        wrapNode(sec, d);
      }
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
    currentPage = $("app").classList.contains("discovering") ? "discovery" : "chat";
    // 防叠加：异常中断的历史残留（半拆状态）先清掉再建。
    for (const stale of document.querySelectorAll("#orbit-dock, #orbit-main, .orbit-page, .orbit-tip"))
      stale.remove();
    buildDock();
    installTip();
    installPages();
    installSettingsRoutes();
    installWindows();
    applyTextRewrites();
    installPanelLayout();
    installMirrors();
    route(currentPage);
    // R5a P2-3 启动自检：composer 列宽对齐依赖 footer.orbit-composer-wrap（base 仍 760），
    // 漏挂会静默失轴——只警示不阻断。
    const cWrap = document.querySelector(".composer")?.parentElement;
    if (!cWrap || !cWrap.classList.contains("orbit-composer-wrap"))
      console.warn("[orbit] composer wrap missing .orbit-composer-wrap — 消息列对齐未生效");
  }
  function uninstall() {
    if (!installed) return;
    installed = false;
    for (const timer of timers) {
      clearTimeout(timer);
      clearInterval(timer);
    }
    timers.clear();
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
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
})();

/* =========================================================================
 * 裁剪与降级登记（待真机验证后补）：
 * 1) P2 使用现有入口打开联网搜索设置；设置与兴趣表单由共用设置窗承载。
 * 2) M1 人格窗 foot「更多」收纳（.orbit-foot-more）未实施——#modal 按钮区为
 *    app.js 动态重建，防交叉写入需独立 observer 编排，登记后补。
 * 3) M8 保留双栏观察界面，M10「更多」popover 沿用现状。
 * 4) worker-message-nav 三按钮、eval-auto、obs 刷新/清空等图标化（R1 档）未
 *    一一挂 data-tip；「更多」popover、来源行图标化沿用现状。
 * 5) P4 hub 行健康徽标（QQ/DSH/Claude/发现态镜像到行内）未挂——状态点已在
 *    dock 系统簇镜像，hub 行徽标登记后补。
 * 6) S7 悬停卡 12 秒收回计时（03c 5.5）未实施——worker-tasks.js 是否已有
 *    等价驻留逻辑转 06 核对后再定归属。
 * ========================================================================= */
