/* Orbit 设置子页 · 360° 全息投影环（用户裁定 2026-11-20）
   分区面板绕神经球心环形排布：左右拖动旋转整环，松手吸附最近一块"投影屏"，
   正对镜头的面板即当前分区。分区切换仍走 orbit-section-nav → openSectionPage
   的既有管线（标题/导航态/划页状态机不变），本模块只接管几何与拖拽。
   2026-11-21 布局改版：环模式收掉 modal-head 标题栏（关闭钮迁入 dock 尾），
   dock 升为文档流顶行独立组件，面板按视口放大（透视随半径走，手感不变）。
   同日修复：旋转从 track 逐屏下放——Chrome 命中测试会整树剔除侧棱朝前(±90°)
   的祖先平面后代，track 自转的偶数屏此前整屏点不动/滚不了。
   安装条件：data-theme=orbit 且设置窗处于 orbit-page-mode 且视口 ≥900px；
   窄屏不安装（保留既有划页手势）。主题退出时完整还原 DOM。 */
(() => {
  const root = document.documentElement;
  const ORDER = [
    ["general", "对话与模型"], ["appearance", "外观"], ["discovery", "联网搜索"],
    ["observer", "外部观察"], ["qq", "QQ 连接"], ["workers", "DSH 授权"],
    ["claude", "Claude 授权"], ["thoughts", "思想与探索"],
  ];
  const STEP = 360 / ORDER.length;
  let installed = false;      // 环已构建（DOM 已搬移）
  let active = false;         // 设置窗处于 page-mode（环可见）
  let stage = null, track = null;
  let panels = [];            // {el, key, label}
  let idx = 0;                // 当前正对面板序号
  let indexF = 0;             // 拖动中的浮点序号
  let R = 0;                  // 环半径（px）
  let drag = null;            // {pid, x0, downAngle, moved, lastX, lastT, vx}
  let modalObs = null, sectionObs = null;
  const wideMQ = window.matchMedia("(min-width: 900px)");

  const $ = (id) => document.getElementById(id);
  const modal = () => $("settings-modal");
  const bodyEl = () => modal()?.querySelector(".modal-card > .settings-body") || null;
  const navBtn = (key) => modal()?.querySelector(`.orbit-section-nav button[data-section="${key}"]`) || null;

  /* ---------- 几何 ---------- */
  function measure() {
    const card = modal()?.querySelector(".modal-card");
    const avail = card ? card.clientWidth : window.innerWidth;
    /* 面板放大（2026-11-21）：环几何是「track 后移 R、面板前移 R」，正对屏恰好落在
       z=0 ——投影即 CSS 尺寸（探针实测 frontRectW == CSS W），放大就是直接放大 W/H；
       透视取 2.46×半径，侧屏进深比例与初版 1900/771 同手感。高度吃满 dock 之下的
       环舞台（页头已收，整行高度还给环），宽度帽 700、两侧屏照旧向视口外出画。 */
    const W = Math.max(360, Math.min(700, avail - 130));
    R = Math.round((W + 30) / (2 * Math.sin(Math.PI / ORDER.length)));
    track.style.transform = `translateZ(${-R}px)`;             // 环心后移（静态）：旋转已逐屏下放，track 永不侧棱朝前
    stage.style.setProperty("--ring-w", W + "px");
    stage.style.setProperty("--ring-r", R + "px");
    stage.style.setProperty("--ring-p", Math.round(R * 2.46) + "px");
    const body = bodyEl();
    const H = Math.max(320, Math.min(680, body ? body.clientHeight : window.innerHeight - 180));
    stage.style.setProperty("--ring-h", Math.round(H) + "px");
  }
  function paint() {                                           // 动态：逐屏写世界角 + 正面态
    /* 旋转写在每块面板自身（世界角），track 只保留向后的 -R 位移、永不自转——
       **Chrome 命中测试会整树剔除「被转到侧棱朝前(±90°)的祖先平面」的后代**：
       旧方案 track 转到 ±90° 的屏（偶数屏）整屏点不动、滚不了，2026-11-21 实测确认。
       面板各自旋转后，任何角度下正对屏都不经过侧棱祖先，命中恢复；
       translate(-50%,-50%) 必须保留：它抵消 left/top:50% 的左上锚定（配 transform-origin:center）。 */
    panels.forEach((p, i) => {
      const off = ((STEP * i - indexF * STEP) % 360 + 540) % 360 - 180;   // 世界角 -180..180
      const ab = Math.abs(off);
      const front = ab < STEP / 2;
      p.el.style.transform =
        `translate(-50%, -50%) rotateY(${off.toFixed(2)}deg) translateZ(${R}px)`;
      p.el.style.opacity = ab >= 128 ? "0" : (front ? "1" : "0.45").toString();
      p.el.classList.toggle("is-front", front);
      p.el.setAttribute("aria-hidden", front ? "false" : "true");
    });
  }
  const N = ORDER.length;
  const wrapIdx = (i) => ((i % N) + N) % N;                    // 环绕：-1→7、8→0
  const nearestIdx = (i) => i + N * Math.round((idx - i) / N); // 相对当前 idx 的最短旋转等价序号

  function setIndex(i, { animate = true } = {}) {
    idx = wrapIdx(i);
    indexF = i;                                                // 保留连续值（角度取模，越界无害）
    track.classList.toggle("is-dragging", !animate);
    measure(); paint();
    if (!animate) requestAnimationFrame(() => track.classList.remove("is-dragging"));
  }
  function syncTo(key, { animate = true } = {}) {
    const i = ORDER.findIndex(([k]) => k === key);
    if (i < 0) return;
    const t = nearestIdx(i);
    if (wrapIdx(t) !== wrapIdx(Math.round(indexF))) setIndex(t, { animate });
    else { measure(); paint(); }
  }

  /* ---------- 分区切换（走既有管线；thoughts 已是普通分区） ---------- */
  function switchTo(key) {
    navBtn(key)?.click();                                      // → openSectionPage：标题/导航态/划页状态机全套
  }

  /* ---------- 基座隐藏了非当前分区 → 环模式下强制全员在场 ---------- */
  function keepVisible(section) {
    if (section.hasAttribute("hidden")) section.removeAttribute("hidden");
    section.classList.remove("hidden");
    section.style.display = "";                                // 交还环面板样式控制
  }

  /* ---------- 安装 / 还原 ---------- */
  function install() {
    const m = modal(), body = bodyEl();
    if (!m || !body || installed) return;
    const nav = m.querySelector(".modal-card > .orbit-section-nav");
    if (!nav) return;                                          // dock 不在（管线异常）就不装：页头得留着当关闭口
    const sections = new Map();
    for (const [key] of ORDER) {
      const list = [...body.querySelectorAll(`:scope > section[data-settings-section="${key}"]`)];
      if (list.length) sections.set(key, [...list]);
    }
    if (!sections.size) return;
    /* 下面不再有提前出口，这里才动页头相关 DOM：页头收起 + 关闭钮迁入 dock 尾 */
    m.classList.add("orbit-ring-live");                        // 环接管横向手势：基座划页（orbit.js）在环模式让位
    m.setAttribute("aria-label", "设置");                      // 页头收起后 aria-labelledby 失效，补个名字
    const closeBtn = document.createElement("button");         // 页头 × 的替代口：dock 尾部分隔线后一枚关闭钮
    closeBtn.type = "button";
    closeBtn.className = "orbit-ring-close";
    closeBtn.setAttribute("aria-label", "关闭设置");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", () => $("settings-x")?.click());
    nav.appendChild(closeBtn);

    stage = document.createElement("div");
    stage.className = "orbit-ring-stage";
    stage.setAttribute("data-ring", "");
    stage.tabIndex = 0;                                        // 键盘左右箭头的落点
    track = document.createElement("div");
    track.className = "orbit-ring";
    stage.appendChild(track);
    for (const [side, label, step] of [["left", "上一屏", -1], ["right", "下一屏", 1]]) {
      const arrow = document.createElement("button");
      arrow.type = "button";
      arrow.className = "orbit-ring-arrow orbit-ring-arrow--" + side;
      arrow.setAttribute("aria-label", label);
      arrow.setAttribute("data-tip", label);
      arrow.textContent = side === "left" ? "\u2039" : "\u203A";
      arrow.addEventListener("click", () => {
        const t = idx + step;                                  // 环绕：首尾相接
        setIndex(t);
        switchTo(ORDER[wrapIdx(t)][0]);
      });
      stage.appendChild(arrow);
    }
    body.appendChild(stage);
    body.classList.add("orbit-ring-host");                     // CSS：交出滚动与内衬
    panels = [];
    const anchors = [];                                        // 还原用：[el, 原父, 原下一位]（文档序）
    for (const [key, label] of ORDER) {
      const list = sections.get(key);
      if (!list) continue;
      const panel = document.createElement("div");
      panel.className = "orbit-ring-panel";
      panel.dataset.ringKey = key;
      const head = document.createElement("div");
      head.className = "orbit-ring-panel-head";
      const tag = document.createElement("span");
      tag.className = "orbit-ring-panel-tag";
      tag.textContent = "// " + label;
      head.appendChild(tag);
      panel.appendChild(head);
      const frame = document.createElement("div");
      frame.className = "orbit-ring-panel-body";
      list.forEach((sec) => {
        anchors.push([sec, sec.parentElement, sec.nextElementSibling]);   // 搬出前记录原位
        frame.appendChild(sec);
      });
      /* 深链接出口：发现的真实体验页（P2）仍在环外——屏内给一个显式入口，
         点击 hub 的「内容发现」位（其处理器即 route("discovery")）整体跳转 */
      if (key === "discovery") {
        const jump = document.createElement("button");
        jump.type = "button";
        jump.className = "orbit-ring-jumplink";
        jump.textContent = "打开完整内容发现页 ›";
        jump.addEventListener("click", () => window.OrbitRoute?.("discovery"));
        frame.appendChild(jump);
      }
      panel.appendChild(frame);
      track.appendChild(panel);
      panels.push({ el: panel, key, label });
    }
    installed = true;
    (m._ringUndos = m._ringUndos || []).push(() => {
      closeBtn.remove();                                       // dock 尾关闭钮随环拆走（页头在非环形态回归）
      m.classList.remove("orbit-ring-live");                   // 手势闸门交还基座划页
      if (m.getAttribute("aria-label") === "设置") m.removeAttribute("aria-label");
    });
    (m._ringUndos = m._ringUndos || []).push(() => {
      try {
        // 逆文档序插回：此时每个 next 要么从未搬走、要么已在还原早段回位，引用必然有效
        for (let i = anchors.length - 1; i >= 0; i--) {
          const [el, parent, next] = anchors[i];
          try { parent.insertBefore(el, next); } catch (_) { parent.appendChild(el); }
        }
      } finally {
        stage.remove();
        installed = false; active = false; panels = []; stage = null; track = null;
      }
    });

    /* 旧划页手势让位：环模式下，非交互目标的 pointerdown 在 stage 冒泡段截停
       （stage 先于 modal-card 收到冒泡，stopPropagation 即屏蔽 card 上的旧手势；
       面板内部控件照常收到事件）。 */

    /* 拖拽：1:1 旋转，松手按最近面板吸附（经既有管线切换分区） */
    stage.addEventListener("pointerdown", (e) => {
      if (e.button > 0 || drag) return;
      if (e.target.closest("input, textarea, select, button, a, label, [contenteditable], details, summary, .orbit-ring-arrow")) return;
      e.stopPropagation();                                   // 屏蔽 card 上的旧划页手势
      track.classList.add("is-dragging");                    // 抓取即冻结过渡：中途接住不打架
      drag = { pid: e.pointerId, x0: e.clientX, moved: 0, lastX: e.clientX, lastT: performance.now(), vx: 0 };
      try { stage.setPointerCapture(e.pointerId); } catch (_) {}
    });
    stage.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.pid) return;
      const dx = e.clientX - drag.x0;
      drag.moved = Math.max(drag.moved, Math.abs(dx));
      const now = performance.now();
      const dt = Math.max(1, now - drag.lastT);
      drag.vx = 0.72 * drag.vx + 0.28 * ((e.clientX - drag.lastX) / dt);   // px/ms
      drag.lastX = e.clientX; drag.lastT = now;
      if (drag.moved > 8 && !track.classList.contains("is-dragging")) track.classList.add("is-dragging");
      if (drag.moved > 8) {
        e.preventDefault();                                    // 压掉原生文字选择/拖拽
        indexF = idx - dx / 150;                               // ~150px 切一屏
        paint();
      }
    });
    const release = (e) => {
      if (!drag || e.pointerId !== drag.pid) return;
      if (e.type === "pointercancel") {                      // 坐标不可信：只回弹
        drag = null;
        track.classList.remove("is-dragging");
        setIndex(idx);
        return;
      }
      const dx = e.clientX - drag.x0;
      const flick = Math.abs(drag.vx) > 0.45 ? Math.sign(-drag.vx) : 0;    // 甩动方向
      let target = idx;
      if (drag.moved > 8) target = wrapIdx(Math.round(indexF));
      if (flick && target === idx) target = wrapIdx(idx + flick);
      const wasDrag = drag.moved > 8;
      drag = null;
      try { stage.releasePointerCapture(e.pointerId); } catch (_) {}
      track.classList.remove("is-dragging");
      if (wasDrag) {
        const key = ORDER[target][0];
        if (target !== idx) { setIndex(nearestIdx(target)); switchTo(key); }
        else setIndex(target);                                 // 同屏回弹
        if (Math.abs(dx) > 8) {                                // 吃掉拖后误点
          const eat = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
          stage.addEventListener("click", eat, { capture: true, once: true });
          setTimeout(() => stage.removeEventListener("click", eat, true), 240);
        }
      }
    };
    stage.addEventListener("pointerup", release);
    stage.addEventListener("pointercancel", release);
    /* 键盘：stage 聚焦时左右箭头换屏 */
    stage.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const t = idx + (e.key === "ArrowLeft" ? 1 : -1);        // 环绕
      setIndex(t);
      switchTo(ORDER[wrapIdx(t)][0]);
    });

    /* 分区可见性漏斗：基座（导航/标题栏/快捷入口）显示某分区 → 环对准它 */
    sectionObs = new MutationObserver(() => {
      if (!active) return;
      panels.forEach((p) => {
        const list = [...p.el.querySelectorAll(":scope > .orbit-ring-panel-body > section")];
        list.forEach(keepVisible);
      });
      const cur = [...document.querySelectorAll(".orbit-section-nav button[data-section]")]
        .find((b) => b.getAttribute("aria-current") === "page")?.dataset.section;
      if (cur) syncTo(cur, { animate: true });
    });
    panels.forEach((p) => p.el.querySelectorAll(":scope > section").forEach((s) =>
      sectionObs.observe(s, { attributes: true, attributeFilter: ["hidden", "class"] })));
    sectionObs.observe(m.querySelector(".orbit-section-nav") || m, { subtree: true, attributeFilter: ["aria-current"] });

    panels.forEach((p) => p.el.querySelectorAll(":scope > .orbit-ring-panel-body > section").forEach(keepVisible));

        /* 安装发生在 openSectionPage 中段：markSectionNav 已先行，观察器收不到
       首次 aria-current——这里直接读当前标记对准起始屏 */
    const cur = [...m.querySelectorAll(".orbit-section-nav button[data-section]")]
      .find((b) => b.getAttribute("aria-current") === "page")?.dataset.section;
    const startKey = cur && ORDER.some(([k]) => k === cur) ? cur : ORDER[idx][0];
    idx = wrapIdx(ORDER.findIndex(([k]) => k === startKey));
    indexF = idx;
    measure(); paint();
  }
  function teardown() {
    const m = modal();
    /* 还原基座可见性约定：当前分区之外补回 hidden class。
       **只动 class、绝不设 hidden 属性**——基座漏斗（app.js selectSettingsSection）
       只切换 class；一旦留下属性隐藏，之后的任何 class 切换都救不回来（空正文），
       环开着切主题等半拆场景就会踩中（2026-11-21 空正文排查结论）。 */
    if (installed && panels.length) {
      const curKey = ORDER[idx]?.[0];
      for (const p of panels) {
        const vis = p.key === curKey;
        p.el.querySelectorAll(":scope > .orbit-ring-panel-body > section").forEach((s) => {
          s.classList.toggle("hidden", !vis);
          s.removeAttribute("hidden");               // 兜底摘属性：环期间 keepVisible 之外的残留也清掉
        });
      }
    }
    bodyEl()?.classList.remove("orbit-ring-host");
    if (m && m._ringUndos) { m._ringUndos.splice(0).forEach((f) => f()); }
    if (sectionObs) { sectionObs.disconnect(); sectionObs = null; }
  }

  /* ---------- 状态机：主题 × 视口 × page-mode ---------- */
  function evalState() {
    const orbit = root.dataset.theme === "orbit";
    const m = modal();
    const pageMode = !!m && m.classList.contains("orbit-page-mode") && !m.classList.contains("hidden");
    const want = orbit && wideMQ.matches && pageMode;
    if (!want && installed) teardown();                        // 关窗/切主题/窄屏：先归还再拆环
    if (want && !installed && m) install();
    if (installed) { active = true; measure(); paint(); }
  }
  modalObs = new MutationObserver(evalState);
  if (modal()) modalObs.observe(modal(), { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(evalState).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  wideMQ.addEventListener?.("change", evalState);
  /* resize 直接触发 evalState（装/拆/量/画都收敛在这）：跨 900px 边界的窗口缩放
     不能只依赖 MQ change——有的宿主（如内嵌视口）不派发这些事件，环会滞留窄屏。 */
  window.addEventListener("resize", () => evalState());
  evalState();
})();
