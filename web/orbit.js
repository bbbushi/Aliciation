/* =========================================================================
 * orbit.js · Orbit 主题唯一控制器（2026-11-17 集成施工）
 *
 * 职责（01 大纲 v2.1 §五/§八 + 02a/b/c 结构壳 + 03a §3.1 dock 契约 +
 * 04a §1 切页五连/§3 召唤窗 + 04b M-02/M-03/M-09/M-10）：
 *   1) 主题安装/卸载：MutationObserver 监听 html[data-theme]，install() 建
 *      dock + 页面骨架，uninstall() 按还原表全量还原（注释标记 replaceWith）。
 *   2) 切页时冻结退场页几何，更新 dock、神经球焦点和设置入口自转。
 *   3) 设置使用共用设置窗。内容发现与思想页保有独立入口。
 *   4) 状态镜像：OrbitBG.setState 和对话待确认徽标。
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
  const effects = new Set();

  function later(fn, ms) {
    const t = setTimeout(() => { timers.delete(t); fn(); }, ms);
    timers.add(t);
    return t;
  }
  function cancelTimer(timer) {
    clearTimeout(timer);
    timers.delete(timer);
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

  function finishEffect(node, type, fallbackMs, done, propertyName = null) {
    const finish = () => { cancel(); done(); };
    const onEnd = (event) => {
      if (event.target === node && (!propertyName || event.propertyName === propertyName)) finish();
    };
    const timer = later(finish, fallbackMs);
    function cancel() {
      node.removeEventListener(type, onEnd);
      cancelTimer(timer);
      effects.delete(cancel);
    }
    node.addEventListener(type, onEnd);
    effects.add(cancel);
    return cancel;
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
    cancelTimer(holdT); cancelTimer(dismissT); holdT = dismissT = 0;
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
      cancelTimer(holdT);
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
      cancelTimer(holdT);
      holdT = later(() => {
        if (tipTarget !== t) return;
        tipShow(t);
        cancelTimer(dismissT);
        dismissT = later(tipHide, AUTO_DISMISS);
      }, HOLD_TOUCH);
    }, true);
    wire(document, "pointermove", (e) => {
      if (e.pointerType !== "touch" || !holdT) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) < MOVE_CANCEL) return;
      cancelTimer(holdT); holdT = 0; tipTarget = null; // 位移 >8px 才清除（R4 P1-3）
    }, true);
    ["pointerup", "pointercancel"].forEach((t) =>
      wire(document, t, () => { cancelTimer(holdT); holdT = 0; }, true));
    wire(document, "keydown", (e) => { if (e.key === "Escape") tipHide(); }, true);
  }

  /* ---------- 五、页面路由与切页五连（04a §1） ---------- */

  const PAGE_KEYS = ["chat", "discovery", "settings"];
  const pages = {};            // key → 容器元素（install 时填充）
  const pageHooks = [];        // 页级副作用（如 P4 球面引擎的启停）：切页时按最终页名回调
  let currentPage = "chat";
  let pageToken = 0;           // 切页令牌：过期退场计时器不得再动 DOM
  let cancelPageEntry = null;

  /* 退场页必须先脱离文档流再淡出：#app 是 flex-row，#orbit-main 与 .orbit-page 都是
     width: min(1080px, 100%)，两页同时在场会被对半分宽（772 → 各 363px），一秒内再把新页
     弹回全宽——2026-11-19 用户报「页面之间的过渡不流畅」即此。冻结成 fixed 盒子（位置/尺寸
     取切换前一帧的 rect，视觉零位移）后新旧页不再互相挤压，退场只是叠在上面淡出。 */
  const FREEZE_PROPS = ["position", "left", "top", "width", "height", "maxHeight", "margin"];
  function freezePage(el) {
    if (el.dataset.orbitFrozen) return;
    const r = el.getBoundingClientRect();
    el.style.position = "fixed";
    el.style.left = r.left + "px";
    el.style.top = r.top + "px";
    el.style.width = r.width + "px";
    el.style.height = r.height + "px";
    el.style.maxHeight = "none";
    el.style.margin = "0";
    el.dataset.orbitFrozen = "1";
  }
  function unfreezePage(el) {
    if (!el.dataset.orbitFrozen) { el.classList.remove("is-leaving"); return; }
    delete el.dataset.orbitFrozen;
    for (const p of FREEZE_PROPS) el.style[p] = "";
    el.classList.remove("is-leaving");
  }

  /* ---- 子页覆盖的进出场（与 writePage 同一套原语）----
     进入子页 = 当前页 freeze 成 fixed 盒子淡出 125ms（is-leaving）后隐藏，
               子页以 orbit-page-in 240ms 上浮淡入 —— 与独立页之间的切换完全一致。
     退出/切页时 uncover 还原，当前页重播 is-entering。 */
  let coverToken = 0;
  function coverUnderlyingPage() {
    const el = pages[currentPage];
    if (!el || el.style.visibility === "hidden") return;   // 已被子页覆盖：子页间切换不重复退场
    const reduce = root.dataset.motion !== "on" || matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { el.style.visibility = "hidden"; return; }
    const token = ++coverToken;
    freezePage(el);                           // 先量 rect 再冻结：子页尚未显示，布局未变
    el.classList.add("is-leaving");           // orbit-page-out 125ms 淡出（与页切换同一支）
    later(() => {
      if (token !== coverToken) return;       // 期间已 uncover：过期计时器不得再动 DOM
      unfreezePage(el);
      el.style.visibility = "hidden";
    }, 140);
  }
  function uncoverPages() {
    coverToken++;
    for (const key of PAGE_KEYS) {
      const el = pages[key];
      if (!el) continue;
      unfreezePage(el);
      if (el.style.visibility) el.style.visibility = "";
    }
  }
  // 切页时若子页还开着：先关掉（且不回 hub），让目标页正常接管
  function closeSubpageForRoute() {
    const modal = $("settings-modal");
    if (modal && modal.classList.contains("orbit-page-mode") && !modal.classList.contains("hidden")) {
      suppressReturnOnClose = true;
      window.AliceUI?.closeSettings();
    }
  }

  function writePage(name) {
    if (document.body.dataset.orbitPage === name) return;
    closeSubpageForRoute();
    uncoverPages();
    cancelPageEntry?.();
    cancelPageEntry = null;
    document.body.dataset.orbitPage = name;
    // 每次切页领一个令牌：退场计时器只对「最后一次切页」生效。
    // 原本按写入时捕获的页名判断（key !== name），启动时 onboarding→chat 的竞态会让
    // 旧计时器把刚显示出来的 chat 又藏掉（刷新后消息页空白，2026-11-19 用户报障）。
    const token = ++pageToken;
    const reduce = root.dataset.motion !== "on" || matchMedia("(prefers-reduced-motion: reduce)").matches;
    // 先量后动：退场页的 rect 必须在「新页显示出来之前」取，否则已经被挤成半宽
    const leaving = reduce ? [] : PAGE_KEYS
      .map((key) => [key, pages[key]])
      .filter(([key, el]) => el && key !== name && !el.hidden && el.style.display !== "none");
    for (const [key, el] of leaving) {
      freezePage(el);
      el.classList.add("is-leaving");
      later(() => {
        if (token !== pageToken) return;      // 期间又切过页：交由最新那次收尾
        unfreezePage(el);
        el.hidden = true; el.style.display = "none";
        if (key === "discovery") window.AliceDiscovery?.close(false);
      }, 150);
    }
    for (const key of PAGE_KEYS) {
      const el = pages[key];
      if (!el) continue;
      el.inert = key !== name;
      if (key === name) {
        unfreezePage(el);                     // 快速往返：退场中重返则撤销冻结与退场
        el.hidden = false; el.style.display = "";
        el.classList.add("is-entering");
        cancelPageEntry = finishEffect(el, "animationend", 400, () => {
          el.classList.remove("is-entering");
          cancelPageEntry = null;
        });
      } else {
        el.classList.remove("is-entering");
        if (leaving.some(([k]) => k === key)) continue;   // 已冻结在退场层，等计时器收尾
        unfreezePage(el);
        if (!el.hidden || el.style.display !== "none") {
          el.hidden = true; el.style.display = "none"; // 双保险归位（.orbit-page 类 display:flex 会盖掉 [hidden]）
        }
      }
    }
    // dock aria-current（同帧）
    for (const key of PAGE_KEYS) {
      const btn = dockIcons.primary[key];
      if (btn) { if (key === name) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current"); }
    }
    if (window.OrbitBG && typeof window.OrbitBG.setPage === "function") window.OrbitBG.setPage(name);
    window.OrbitBG?.setFocus?.(name);
    // 分区导航条当前项（discovery 独立页与子页互通）
    if (name === "discovery") markSectionNav(name);
    for (const hook of pageHooks) hook();
    if (name === "discovery") window.AliceDiscovery?.open?.(false);
    else if (!leaving.some(([key]) => key === "discovery")) window.AliceDiscovery?.close(false);
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

  const dockIcons = { primary: {} };
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

  /* dock 收纳（2026-11-18 用户要求：底部 dock 可收成小把手）
     几何：展开＝56 高整条轨道；收起＝52×40、只剩 chevron（把手宽由 --orbit-dock-tuck 定）。
     auto 宽度无法过渡，所以收/开都先把当前宽度钉成像素，过渡完再交还 CSS。 */
  const DOCK_TUCK_KEY = "alice.orbit.dock.v1";
  let dock = null, dockToggle = null, dockTucked = false;
  let cancelDockWidth = null, dockFollowRaf = null;

  function dockTuckWidth() {
    const n = parseFloat(getComputedStyle(dock).getPropertyValue("--orbit-dock-tuck"));
    return Number.isFinite(n) && n > 0 ? n : 52;
  }
  function setDockTucked(next, animate = true) {
    if (!dock || next === dockTucked) return;
    cancelDockWidth?.();
    cancelDockWidth = null;
    stopDockFollow();
    const motion = animate && !reduceMotion();
    // 载入时恢复上次收纳态不该播动画（页面已画过一帧）：开一个短免过渡窗口
    if (!animate) {
      root.dataset.dockInstant = "";
      later(() => { delete root.dataset.dockInstant; }, 80);
    }
    const from = dock.getBoundingClientRect().width;
    if (motion) { dock.style.width = from + "px"; void dock.offsetWidth; }
    dockTucked = next;
    dock.toggleAttribute("data-tucked", next);
    // 底部预留跟着收：--orbit-dock-clear 96 → 68，页面壳/聊天卡片长高，输入框随之下移
    root.dataset.dock = next ? "tucked" : "open";
    // 贴底阅读时把消息列一起钉到底：卡片长高的 28px 不该变成最新消息下方的空白
    const msgs = $("msgs");
    const pinnedToBottom = !!msgs?.clientHeight && msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 40;
    if (dockToggle) {
      const label = next ? "展开导航" : "收起导航";
      dockToggle.setAttribute("aria-expanded", String(!next));
      dockToggle.setAttribute("aria-label", label);
      dockToggle.setAttribute("data-tip", label);
      // 键盘路径：焦点还在被收起的图标上时交回把手，避免焦点掉到 body
      if (next && dock.contains(document.activeElement) && document.activeElement !== dockToggle) {
        dockToggle.focus({ preventScroll: true });
      }
    }
    try { localStorage.setItem(DOCK_TUCK_KEY, next ? "tucked" : "open"); } catch (_) { /* 隐私模式忽略 */ }
    // 待确认消息徽标挂在「对话」图标上，收起后图标不可见——暂时挪到把手上，展开再还回去
    const badge = badges.mail, chatIcon = dockIcons.primary.chat;
    if (badge && dockToggle && chatIcon) {
      const host = next ? dockToggle : chatIcon;
      if (badge.parentElement !== host) host.appendChild(badge);
    }
    if (!motion) { dock.style.width = ""; if (pinnedToBottom) msgs.scrollTop = msgs.scrollHeight; return; }
    if (pinnedToBottom) {
      // 过渡期间每帧跟到底（高度在 180ms 内连续变化）
      const stick = () => { msgs.scrollTop = msgs.scrollHeight; };
      stick();
      const until = performance.now() + 320;
      const tick = () => {
        dockFollowRaf = null;
        if (!msgs.clientHeight || performance.now() > until) return;
        stick();
        dockFollowRaf = requestAnimationFrame(tick);
      };
      dockFollowRaf = requestAnimationFrame(tick);
    }
    if (next) {
      dock.style.width = dockTuckWidth() + "px";
    } else {
      const pinned = dock.style.width;               // 收起态像素宽
      dock.style.width = "max-content";              // 量一次展开后的自然宽
      const to = dock.getBoundingClientRect().width;
      dock.style.width = pinned;
      void dock.offsetWidth;
      dock.style.width = to + "px";
    }
    cancelDockWidth = finishEffect(dock, "transitionend", 260, () => {
      dock.style.width = "";
      cancelDockWidth = null;
    }, "width");
  }

  function stopDockFollow() {
    if (dockFollowRaf !== null) cancelAnimationFrame(dockFollowRaf);
    dockFollowRaf = null;
  }

  function buildDock() {
    dockTucked = false;
    dock = document.createElement("div");
    dock.id = "orbit-dock";
    dock.setAttribute("role", "navigation");
    dock.setAttribute("aria-label", "轨道导航");
    const texture = document.createElement("div");
    texture.className = "orbit-dock-texture";
    dock.appendChild(texture);
    // 收纳把手常驻轨道最左：收起后它是唯一可见的按钮（chevron 收起向下、展开向上）
    dockToggle = document.createElement("button");
    dockToggle.type = "button";
    dockToggle.className = "orbit-icon orbit-dock-toggle";
    dockToggle.setAttribute("aria-controls", "orbit-dock-cluster");
    dockToggle.setAttribute("aria-expanded", "true");
    dockToggle.setAttribute("aria-label", "收起导航");
    dockToggle.setAttribute("data-tip", "收起导航");
    const chevron = document.createElement("span");
    chevron.className = "orbit-dock-chevron";
    chevron.setAttribute("aria-hidden", "true");
    dockToggle.appendChild(chevron);
    wire(dockToggle, "click", () => setDockTucked(!dockTucked));
    dock.appendChild(dockToggle);
    // 三站 dock：设置 / 对话 / 退出（用户裁定精简；单簇无分隔线）
    const cluster = document.createElement("div");
    cluster.className = "orbit-dock-cluster";
    cluster.id = "orbit-dock-cluster";
    cluster.dataset.cluster = "primary";
    dock.appendChild(cluster);

    const settings = dockButton({ icon: "gear", label: "设置", tip: "设置", cluster });
    wire(settings, "click", () => route("settings"));
    const chat = dockButton({ icon: "chat", label: "我们的对话 · 01 / conversation", tip: "对话", cluster });
    wire(chat, "click", () => route("chat"));
    const exit = dockButton({ icon: "exit", label: "退出 Alice", tip: "退出 Alice", cluster });
    wire(exit, "click", () => { const t = $("exit-btn"); if (t) t.click(); });

    dockIcons.primary.settings = settings;
    dockIcons.primary.chat = chat;
    // 待确认消息徽标改挂「对话」（待确认消息本就在对话页处理，语义一致）
    const badge = document.createElement("span");
    badge.className = "orbit-badge";
    badge.dataset.tone = "warn";
    badge.hidden = true;
    wire(badge, "animationend", (e) => {
      if (e.animationName === "orbit-badge-glow") badge.setAttribute("data-attention", "done");
    });
    chat.appendChild(badge);
    badges.mail = badge;
    badgePrev.mail = 0;
    document.body.appendChild(dock);
    undos.push(() => { dock.remove(); dock = null; dockToggle = null; });
    // 恢复上次收纳状态；窄窗（≤720 通栏态）一律保持展开，JS 也随视口变化收回展开
    let savedTuck = null;
    try { savedTuck = localStorage.getItem(DOCK_TUCK_KEY); } catch (_) { savedTuck = null; }
    const narrow = matchMedia("(max-width: 720px)");
    root.dataset.dock = "open";
    if (savedTuck === "tucked" && !narrow.matches) setDockTucked(true, false);
    const onNarrow = () => { if (narrow.matches && dockTucked) setDockTucked(false); };
    wire(narrow, "change", onNarrow);
    undos.push(() => { delete root.dataset.dock; delete root.dataset.dockInstant; });
    // 主题首次进入：dock 整体 180ms 仅淡入（04a §1.5 顺序注）
    const enteringDock = dock;
    enteringDock.classList.add("is-entering");
    finishEffect(enteringDock, "animationend", 400, () => enteringDock.classList.remove("is-entering"));
  }

  /* ---------- 七、M-03 徽标一次性双波协议 ---------- */

  const badgePrev = { mail: 0 };
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
      later(() => {
        if (badgePrev[key]) return;
        badge.hidden = true;
        badge.textContent = "";
        badge.removeAttribute("data-fading");
      }, 150);
      return;
    }
    badge.removeAttribute("data-fading");
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

  /* ---------- 设置窗和独立页面 ---------- */

  function returnToSettings() {
    route("settings");
    const modal = $("settings-modal");
    if (modal) {
      modal.classList.remove("orbit-page-mode");   // 回 hub 时收起 page 形态
      const title = modal.querySelector("#settings-title");
      if (title) title.textContent = "设置";
    }
    if (!pages.settings.contains(document.activeElement) && !dock.contains(document.activeElement)) {
      (dockTucked ? dockToggle : dockIcons.primary.settings)?.focus({ preventScroll: true });
    }
  }

  /* ---- 子页导航条：把设置各分区提升为「独立页」呈现（统一页转场） ----
     从 hub 点进任意分区 → 打开全屏子页 + 顶部导航条；导航条可在各子页间互通。
     thoughts/discovery 已有独立页走 route；其余复用 settings-modal 但以 page 形态呈现。
     不搬移任何 section DOM、不碰各模块 setVisible 契约。 */
  const SECTION_NAV = [
    ["general", "对话与模型"], ["appearance", "外观"], ["discovery", "联网搜索"],
    ["observer", "外部观察"], ["qq", "QQ 连接"], ["workers", "DSH 授权"],
    ["claude", "Claude 授权"], ["thoughts", "思想与探索"],
  ];
  let sectionNavEls = [];              // 所有已插入的分区导航条实例（modal / P2 / P3 各一条）
  let suppressReturnOnClose = false;   // 从子页跳到 thoughts/discovery 时，关 modal 不回 hub
  let suppressRoute = false;           // openSectionPage 内部调原生 openSettings 时放行，防递归
  let currentSubKey = "general";       // 划页手势：当前子页分区（openSectionPage 写入）
  let subpageSwipeReset = null;        // 划页手势的中止入口（modal 被外部关闭时清中间态）

  function createSectionNav() {
    const nav = document.createElement("nav");
    nav.className = "orbit-section-nav";
    nav.setAttribute("aria-label", "设置分区导航");
    for (const [key, label] of SECTION_NAV) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.dataset.section = key;
      wire(b, "click", () => openSectionPage(key));
      nav.appendChild(b);
    }
    return nav;
  }

  function registerNav(nav) {
    sectionNavEls.push(nav);
    undos.push(() => { nav.remove(); sectionNavEls = sectionNavEls.filter((n) => n !== nav); });
  }

  // 设置 modal 的导航条：懒插入（首进 page 形态时），置于窗头之下
  function ensureModalNav() {
    const modal = $("settings-modal");
    if (!modal || modal.querySelector(".modal-card > .orbit-section-nav")) return;
    const nav = createSectionNav();
    const head = modal.querySelector(".modal-card > .modal-head");
    const card = modal.querySelector(".modal-card");
    if (head) head.after(nav); else card.prepend(nav);
    registerNav(nav);
  }

  function markSectionNav(key) {
    for (const nav of sectionNavEls) {
      nav.querySelectorAll("button").forEach((b) => {
        if (b.dataset.section === key) b.setAttribute("aria-current", "page");
        else b.removeAttribute("aria-current");
      });
    }
    // 不用 scrollIntoView：它会滚动最近的滚动祖先（#app/body），把对话页内容滚出视口。
  }

  function replayPageEntry(el) {
    if (!el) return;
    // 减动效/动效关不在此门控：与独立页一致，交给 CSS 降级为 orbit-fade-in 125ms
    el.classList.remove("is-entering");
    void el.offsetWidth;                       // 强制 reflow 让动画重播
    el.classList.add("is-entering");
    later(() => el.classList.remove("is-entering"), 400);
  }

  function openSectionPage(key, { replay = true } = {}) {
    ensureModalNav();
    const modal = $("settings-modal");
    if (!modal || !window.AliceUI) return;
    coverUnderlyingPage();                     // 当前页走与页切换相同的 125ms 退场，再让子页入场
    // 镜头随子页退出 P4 特写：与「设置 → 思想与探索」一致，神经球/房间从 1.32 倍平滑退回常态。
    // （writePage 里的 setFocus 只在切页时触发，子页是覆盖层不经过它，必须在这里补。）
    window.OrbitBG?.setFocus?.("settings-sub");
    modal.classList.add("orbit-page-mode");    // CSS：独立页视觉（与 P3 同配方）+ orbit-page-in 转场
    currentSubKey = key;                       // 划页手势读当前分区序号用
    // 页头标题跟随分区，读作独立页（如「QQ 连接」），回 hub 时由 returnToSettings 还原
    const label = SECTION_NAV.find(([k]) => k === key)?.[1];
    const title = modal.querySelector("#settings-title");
    if (title && label) title.textContent = label;
    suppressRoute = true;                      // 原生 openSettings 内部不再路由回这里
    try { window.AliceUI.openSettings(key); }
    finally { suppressRoute = false; }
    markSectionNav(key);                       // modal 已可见时 spy 不触发，这里显式标记
    if (replay) replayPageEntry(modal);        // 划页提交时改用 3D 翻页入场，不叠加这支
  }

  /* ---- 子页 3D 左右划切换（2026-11-20 用户需求）----
     设置子页以 3D 透视呈现；按住横向拖 = 跟手 1:1（TRACK，无任何过渡），
     松手按距离（>90px）或速度（>0.55px/ms）决定翻页或快速弹回（RELEASE，过渡接管），
     首尾分区橡皮筋；入场飞行途中可被接住继续拖。切换走 openSectionPage(replay:false)，
     与导航条/dock 共用一套分区状态。 */
  function installSubpageSwipe() {
    const modal = $("settings-modal");
    const card = modal?.querySelector(".modal-card");
    if (!modal || !card) return;
    const reduce = () => root.dataset.motion !== "on" || matchMedia("(prefers-reduced-motion: reduce)").matches;
    // phase：null=静止，"out"=飞出相（不可接），"in"=入场相（可按下结算后续划）
    const sw = { down: false, locked: false, phase: null, pid: 0, x0: 0, y0: 0, vx: 0, lastX: 0, lastT: 0, dragged: false };

    const idxNow = () => SECTION_NAV.findIndex(([k]) => k === currentSubKey);
    // orbit-ring-live：环已接管横向手势（orbit-ring.js 装环时标记）——划页让位，避免整卡拖拽与环旋转打架
    const active = () => modal.classList.contains("orbit-page-mode") && !modal.classList.contains("hidden")
      && !modal.classList.contains("orbit-ring-live");

    // TRACK：1:1 透视姿态。左拖 = 左缘压进屏幕深处（rotateY 负）；边界橡皮筋 0.18。
    const trackPose = (d) => {
      const i = idxNow();
      const edge = (i <= 0 && d > 0) || (i >= SECTION_NAV.length - 1 && d < 0);
      const dd = edge ? d * 0.18 : d;
      const k = Math.min(Math.abs(dd), 300) / 300;        // 深度随距离缓增、封顶
      card.style.transform =
        `translateX(${(dd * 0.42).toFixed(1)}px) rotateY(${(dd * 0.05 * (1 - k * 0.35)).toFixed(2)}deg) scale(${(1 - k * 0.03).toFixed(3)})`;
    };
    const clearTransform = () => {
      card.classList.remove("orbit-swiping");
      card.style.transition = ""; card.style.transform = ""; card.style.opacity = "";
    };
    subpageSwipeReset = () => { sw.down = false; sw.locked = false; sw.phase = null; clearTransform(); };
    undos.push(() => { subpageSwipeReset = null; });

    // RELEASE·翻页提交：当前页 3D 飞出 → 内容瞬换并落到对侧入场姿态 → 过渡回正位
    const commit = (dir) => {                  // dir=1 下一页（左划），dir=-1 上一页
      sw.phase = "out"; sw.locked = false;
      const nextKey = SECTION_NAV[idxNow() + dir]?.[0];
      card.classList.remove("orbit-swiping");
      const dur = reduce() ? 110 : 200;
      const fly = dir === 1 ? "translateX(-55%) rotateY(-32deg)" : "translateX(55%) rotateY(32deg)";
      const enter = dir === 1 ? "translateX(55%) rotateY(32deg)" : "translateX(-55%) rotateY(-32deg)";
      card.style.transition = `transform ${dur}ms cubic-bezier(.45,.1,.55,1), opacity ${dur}ms ease`;
      card.style.transform = fly; card.style.opacity = "0";
      later(() => {
        if (!nextKey || !active()) { clearTransform(); sw.phase = null; return; }
        // 瞬换：内容已换、姿态直接落到对侧（无过渡），再整页转正
        card.classList.add("orbit-swiping");   // transition:none
        sw.phase = "in";                       // 入场相：可被新按下结算（支持快速连划）
        openSectionPage(nextKey, { replay: false });
        card.style.transform = enter; card.style.opacity = "0";
        void card.offsetWidth;
        card.classList.remove("orbit-swiping");
        const durIn = reduce() ? 110 : 260;
        card.style.transition = `transform ${durIn}ms cubic-bezier(.2,.7,.2,1), opacity ${durIn}ms ease`;
        card.style.transform = ""; card.style.opacity = "";
        later(() => { card.style.transition = ""; sw.phase = null; }, durIn + 60);
      }, dur + 10);
    };
    // RELEASE·弹回：快（160ms），不带弹跳——按钮关页没有力，就不该有 bounce
    const snapBack = () => {
      card.classList.remove("orbit-swiping");
      card.style.transition = "transform 160ms cubic-bezier(.2,.7,.2,1), opacity 120ms ease";
      card.style.transform = ""; card.style.opacity = "";
      later(() => { card.style.transition = ""; }, 200);
    };
    const guardClick = (e) => { e.stopPropagation(); e.preventDefault(); };  // 拖过就吃掉误点

    wire(card, "pointerdown", (e) => {
      if (!active() || e.button > 0) return;
      if (sw.phase === "out") return;          // 飞出相（≈200ms）不可接
      // 入场相被再次按下：立即结算到正位，从这里接新一划——快速连划不丢手势
      if (sw.phase === "in" || card.style.transform) {
        card.classList.remove("orbit-swiping");
        card.style.transition = "none";
        card.style.transform = ""; card.style.opacity = "";
        void card.offsetWidth;
        card.style.transition = "";
        sw.phase = null;
      }
      if (e.target.closest("input, textarea, select, option, summary, [contenteditable], .orbit-hub-item")) return;
      sw.locked = false;
      sw.down = true; sw.pid = e.pointerId; sw.x0 = e.clientX; sw.y0 = e.clientY;
      sw.vx = 0; sw.lastX = e.clientX; sw.lastT = performance.now(); sw.dragged = false;
    });
    wire(card, "pointermove", (e) => {
      if (!sw.down || e.pointerId !== sw.pid) return;
      const dx = e.clientX - sw.x0, dy = e.clientY - sw.y0;
      if (!sw.locked) {
        if (Math.abs(dx) < 12 || Math.abs(dx) < Math.abs(dy) * 1.3) return;   // 等横向意图确认
        if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { sw.down = false; return; }  // 纵向滚动
        sw.locked = true; sw.dragged = true;
        card.classList.add("orbit-swiping");   // 关过渡 + 禁选择（跟手必须零延迟）
        try { card.setPointerCapture(sw.pid); } catch { /* 指针已不在 */ }
      }
      // 速度只在 ≥8ms 窗口采样：亚毫秒窗口会把抖动放大成几百 px/ms，轻拖误判成甩
      const t = performance.now();
      if (t - sw.lastT >= 8) { sw.vx = (e.clientX - sw.lastX) / (t - sw.lastT); sw.lastT = t; sw.lastX = e.clientX; }
      trackPose(dx);
    });
    const release = (e) => {
      if (!sw.down || e.pointerId !== sw.pid) return;
      sw.down = false;
      if (!sw.locked) return;                  // 未锁横向：是普通点击，放行
      const dx = e.clientX - sw.x0;
      const dir = dx < 0 ? 1 : -1;
      const i = idxNow();
      const target = i + dir;
      const go = Math.abs(dx) > 90 || Math.abs(sw.vx) > 0.55;
      sw.locked = false;
      card.addEventListener("click", guardClick, { capture: true, once: true });
      later(() => card.removeEventListener("click", guardClick, true), 50);
      if (go && target >= 0 && target < SECTION_NAV.length) commit(dir);
      else snapBack();                         // 未过阈值或越界方向：弹回
    };
    wire(card, "pointerup", release);
    wire(card, "pointercancel", (e) => {
      if (sw.down && sw.locked) { sw.down = false; sw.locked = false; snapBack(); }
      else sw.down = false;
    });
  }

  function installSettingsRoutes() {
    const ui = window.AliceUI;
    if (!ui) return;
    const modal = $("settings-modal");
    let modalVisible = !modal.classList.contains("hidden");
    let returnOnClose = true;

    function handleRoute(section) {
      if (suppressRoute) return false;         // openSectionPage 内部的原生调用，放行
      // 统一页形态：任何入口（dock、旧导航、welcome、功能事件）打开设置分区
      // 都走 openSectionPage —— 与 hub 徽章同一条 page 视觉 + 导航条 + 转场。
      openSectionPage(section);
      return true;
    }
    if (ui.registerSettingsRoute) {
      undos.push(ui.registerSettingsRoute(handleRoute));
    } else {
      // The macOS packager can pin app.js to a revision before route registration.
      const { selectSettings, openSettings } = ui;
      ui.selectSettings = (section) => { if (!handleRoute(section)) selectSettings(section); };
      ui.openSettings = (section) => { if (!handleRoute(section)) openSettings(section); };
      undos.push(() => Object.assign(ui, { selectSettings, openSettings }));
    }
    spy(modal, { attributes: true, attributeFilter: ["class"] }, () => {
      const visible = !modal.classList.contains("hidden");
      // 不在这里改 modal.class——spy 监听的正是 class，改它会再触发一轮回调，
      // 与 hidden/returnOnClose 状态机交错，是「关不掉」的元凶。page-mode 类留着无害。
      if (modalVisible && !visible && returnOnClose && !suppressReturnOnClose) returnToSettings();
      modalVisible = visible;
      if (!visible) {
        returnOnClose = true; suppressReturnOnClose = false;
        subpageSwipeReset?.();                // 中途被关（Esc/切页）：清掉划页的中间态
        // 子页退出：底层页恢复可见；若本次关闭没有伴随切页（回 hub 同名早退），
        // 给当前页补播一次 is-entering，与独立页返回的入场节奏一致。
        uncoverPages();
        // 镜头还给当前页（回 hub = 1.32 倍特写；回对话/探索 = 常态）。伴随切页时 writePage
        // 已设过同值，这里幂等；同名早退（回 hub）时 writePage 不触发，必须在这里补。
        window.OrbitBG?.setFocus?.(currentPage);
        const el = pages[currentPage];
        if (el && !el.hidden && !el.classList.contains("is-entering")) replayPageEntry(el);
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
    // 分区导航条（页级跨子页跳转）：内容发现页顶部也挂同一条，可直达其余设置子页
    registerNav(p2.appendChild(createSectionNav()));
    // 05 轮次 12 追记：P2 ×（discovery.js goChat 会切回房间视图并聚焦输入框）——orbit 下
    // 捕获段拦截改道，关闭必回设置页；其余按钮（含 #discovery-go-chat「回到对话」）不受影响
    wire(p2, "click", (event) => {
      if (event.target.closest("#discovery-close")) {
        event.stopPropagation();
        returnToSettings();
      }
    }, true);
    const dclose = $("discovery-close");
    if (dclose) { subAttr(dclose, "aria-label", "关闭内容发现页，回到设置"); subAttr(dclose, "data-tip", "关闭"); }
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
      const learning = $("discovery-learning-preview");
      const activity = discovery.querySelector(".discovery-task");
      subClass(learning, "orbit-drawer");
      subClass(activity, "orbit-drawer");
      moveNode(discovery.querySelector(".discovery-overview"), side);
      moveNode(learning, side);
      moveNode(activity, side);
      moveNode(discovery.querySelector(".discovery-results"), mainCol);
    }
    pages.discovery = p2;

    // P4：#orbit-page-settings hub（02c §一：新建，无现有节点搬入）
    // （2026-11-21 用户裁定：思想与探索不再是独立页——#thought-settings 留在
    //   settings-body，与其他分区同样经 openSectionPage/全息环打开，P3 页与
    //   route("thoughts") 整链退役。）
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
      ["observer", "外部观察", "layers"],
      ["qq", "QQ 连接", "qq"],
      ["workers", "DSH 授权", "dsh"],
      ["claude", "Claude 授权", "claude"],
      ["thoughts", "思想与探索", "stars"],
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
      wire(b, "click", () => openSectionPage(key));
      grid.appendChild(b);
    }

    // 三站 dock 以设置页访问内容发现与人格。
    const EXTRA = [
      ["内容发现", "search", () => openSectionPage("discovery")],   // 2026-11-21：与其他分区统一，开环不再跳独立页
      ["人格", "diamond", () => { const t = $("persona-btn"); if (t) t.click(); }],
    ];
    window.OrbitRoute = route;               // 环内深链接（如发现屏 → P2 完整页）复用同一路由
    for (const [name, icon, onOpen] of EXTRA) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "orbit-hub-item";
      b.setAttribute("data-tip", name);
      const s = svg(icon);
      const strong = document.createElement("strong");
      strong.textContent = name;
      b.append(s, strong);
      wire(b, "click", onOpen);
      grid.appendChild(b);
    }
    p4.append(head, grid);
    app.insertBefore(p4, app.querySelector(".terminal-foot") || null);
    undos.push(() => p4.remove());
    pages.settings = p4;

    // —— P4 3D 球面引擎（用户裁定）：入口贴在神经球面上随球朝向（不正对镜头），背面被遮挡核挡住；
    //    自动缓转 + 上下左右拖拽；窄屏(<900px)留原平面网格。只写 CSS 变量，不碰卡的 DOM/点击。 ——
    const wide = window.matchMedia("(min-width: 900px)");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)");
    const cards = [...grid.querySelectorAll(".orbit-hub-item")];
    // 每张徽章补装饰环 + 光柱（一次性，纯装饰）
    for (const c of cards) {
      const ring = document.createElement("span"); ring.className = "glyph-ring"; c.appendChild(ring);
      const beam = document.createElement("span"); beam.className = "glyph-beam"; c.appendChild(beam);
    }
    let spin = 0, dragSpin = 0, tilt = 12, dragTilt = 0;
    let dragging = false, dragStartX = 0, dragStartY = 0, dragStartSpin = 0, dragStartTilt = 0;
    let rafHub = 0, lastT = 0, CORE = 300, RADIUS = 300;
    const N = cards.length;
    // 球面经纬分布：按卡数自动分层（3 层：上/赤道/下），每层经度均分并错开半格
    const latBands = [-34, 0, 34];
    const layout = () => {
      CORE = Math.max(220, Math.min(window.innerHeight * 0.38, 360));
      RADIUS = CORE / 2 + 66;                          // 球面半径 = 球半径 + 徽章半深余量
      grid.style.setProperty("--core-size", CORE.toFixed(0) + "px");
      grid.style.setProperty("--radius", RADIUS.toFixed(0) + "px");
      const perBand = Math.ceil(N / latBands.length);
      cards.forEach((c, i) => {
        const band = Math.min(latBands.length - 1, Math.floor(i / perBand));
        const idxInBand = i - band * perBand;
        const bandCount = Math.min(perBand, N - band * perBand);
        const lon = (idxInBand / bandCount) * 360 + (band % 2 ? 180 / bandCount : 0);
        c.style.setProperty("--lon", lon.toFixed(1) + "deg");
        c.style.setProperty("--lat", (-latBands[band]).toFixed(1) + "deg");
      });
    };
    const paint = () => {
      grid.style.setProperty("--spin", (spin + dragSpin).toFixed(2) + "deg");
      grid.style.setProperty("--tilt", Math.max(-72, Math.min(72, tilt + dragTilt)).toFixed(2) + "deg");
    };
    const frame = (ts) => {
      rafHub = 0;
      const dt = lastT ? Math.min(0.05, (ts - lastT) / 1000) : 0.016;
      lastT = ts;
      if (!dragging) spin = (spin + dt * 6) % 360;   // 约 60s 一圈
      paint();
      rafHub = requestAnimationFrame(frame);
    };
    const prepare = () => {
      if (!wide.matches) {
        grid.classList.remove("orbit-hub-orbit");
        cards.forEach((c) => { c.style.cssText = ""; });
        grid.style.cssText = "";
        return;
      }
      grid.classList.add("orbit-hub-orbit");
      layout(); paint();
    };
    const start = () => {
      prepare();
      if (rafHub) return;
      if (!wide.matches || still.matches || root.dataset.motion !== "on") return;
      lastT = 0;
      rafHub = requestAnimationFrame(frame);
    };
    const stop = () => { if (rafHub) { cancelAnimationFrame(rafHub); rafHub = 0; } };
    // 双轴拖拽：水平改经度 spin，垂直改整体俯仰 tilt；点在卡上不拖（保留点击）
    wire(grid, "pointerdown", (e) => {
      if (!wide.matches || e.target.closest(".orbit-hub-item")) return;
      dragging = true;
      dragStartX = e.clientX; dragStartY = e.clientY;
      dragStartSpin = dragSpin; dragStartTilt = dragTilt;
      try { grid.setPointerCapture(e.pointerId); } catch (_) {}
    });
    wire(grid, "pointermove", (e) => {
      if (!dragging) return;
      dragSpin = dragStartSpin + (e.clientX - dragStartX) * 0.45;
      dragTilt = dragStartTilt - (e.clientY - dragStartY) * 0.35;
      dragTilt = Math.max(-72 - tilt, Math.min(72 - tilt, dragTilt));
      paint();
    });
    const endDrag = () => { dragging = false; };
    wire(grid, "pointerup", endDrag);
    wire(grid, "pointercancel", endDrag);
    const syncHub = () => {
      if (document.body.dataset.orbitPage === "settings" && wide.matches && !still.matches && root.dataset.motion === "on") {
        start();
      } else {
        stop();
        prepare();
      }
    };
    wire(wide, "change", syncHub);
    wire(still, "change", syncHub);
    spy(root, { attributes: true, attributeFilter: ["data-motion"] }, syncHub);
    prepare();
    pageHooks.push(syncHub);
    undos.push(stop);

    for (const page of Object.values(pages)) {
      page.hidden = true;
      page.style.display = "none";
    }

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
    // 用户裁定：顶栏去掉导航钮（app-nav 已隐藏，☰ 点了无效）与记忆概览入口（记忆概览窗不再需要）
    subStyle($("nav-toggle"), "display:none");
    subStyle(document.querySelector("details.memory-summary"), "display:none");

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
    // OB2（02b §五.0 全部运行时适配）
    const tools = document.querySelector(".welcome-tools");
    if (tools) subStyle(tools, "display:none"); // 裁定①：orbit 域隐藏不删除
    const tagsHint = document.querySelector("#tab-tags .hint");
    if (tagsHint) {
      subText(tagsHint, "选 2~4 个气质标签当底色。");
      subAttr(tagsHint, "title", "剩下的性格由经历慢慢长");
    }
    const importMd = $("import-md");
    if (importMd) subAttr(importMd, "placeholder", "# Alice 的人格");
  }

  /* ---------- 十三、状态镜像（OrbitBG 和 dock 徽标） ---------- */

  function mirrorState() {
    // OrbitBG：discoveryRunning / thoughtState（单向传入，orbit-bg 不自查业务 DOM）
    const running = window.AliceDiscovery?.isSearching() ?? false;
    const lifecycle = $("thought-lifecycle");
    const stop = $("thought-stop");
    let thoughtState = 0;
    if (stop && !stop.classList.contains("hidden") && !stop.disabled) thoughtState = 1;
    else if (lifecycle && lifecycle.dataset.state === "sleeping") thoughtState = 2;
    if (window.OrbitBG && typeof window.OrbitBG.setState === "function") {
      window.OrbitBG.setState({ discoveryRunning: running, thoughtState });
    }
    // 待确认消息徽标。
    const attempt = $("chat-new-attempt"), notice = $("chat-notice");
    const mailOn = (attempt && !attempt.classList.contains("hidden")) ||
      (notice && !notice.classList.contains("hidden") && notice.textContent.trim().length > 0);
    setBadge("mail", mailOn ? 1 : 0);
  }
  function installPanelLayout() {
    const composerWrap = document.querySelector(".composer")?.parentElement;
    if (composerWrap?.tagName === "FOOTER") subClass(composerWrap, "orbit-composer-wrap");
  }

  function installMirrors() {
    wire(window, "alice:discovery-state", mirrorState);
    wire(window, "alice:discovery-cleared", mirrorState);
    const watch = [
      [$("thought-lifecycle"), { attributes: true, attributeFilter: ["data-state"], childList: true, characterData: true, subtree: true }],
      [$("thought-stop"), { attributes: true, attributeFilter: ["class", "disabled"] }],
      [$("chat-new-attempt"), { attributes: true, attributeFilter: ["class"] }],
      [$("chat-notice"), { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"] }],
    ];
    for (const [node, opts] of watch) if (node) spy(node, opts, mirrorState);
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
    installSubpageSwipe();
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
    if (document.body.dataset.orbitPage !== "discovery") window.AliceDiscovery?.close(false);
    for (const cancel of effects) cancel();
    cancelPageEntry = cancelDockWidth = null;
    stopDockFollow();
    for (const timer of timers) cancelTimer(timer);
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
    pageHooks.length = 0;
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
    new MutationObserver(() => {
      // On entry, let Minimal restore the original panels in its theme observer first.
      if (root.dataset.theme === "orbit") queueMicrotask(applyTheme);
      else applyTheme();
    }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
})();

/* =========================================================================
 * 裁剪与降级登记（待真机验证后补）：
 * 1) P2 使用现有入口打开联网搜索设置；设置与兴趣表单由共用设置窗承载。
 * 2) M1 人格窗 foot「更多」收纳（.orbit-foot-more）未实施——#modal 按钮区为
 *    app.js 动态重建，防交叉写入需独立 observer 编排，登记后补。
 * 3) 「外部观察」打开共用设置窗的 Lab 授权页。完整观测由 Lab 承载。
 * 4) S7 悬停卡 12 秒收回计时（03c 5.5）未实施——worker-tasks.js 是否已有
 *    等价驻留逻辑转 06 核对后再定归属。
 * ========================================================================= */
