/* orbit 主题常驻舞台：网格房间 + 中央神经球（改编自 room.js，全局 body 级画布）。
   纯装饰，不读会话数据、不发请求、不响应指针、不监听滚动（滚动是页内行为）。
   球位/半径/亮度/视差按 body[data-orbit-page] 五档联动（04a §2.1，值来自 03b §六基准）：
   0.9s 临界阻尼弹簧插值（无过冲，球不得越入文字区正后）；睡眠档强制安静（自转/呼吸降档）。
   门禁：动效关 / 系统减动效 / 页面隐藏 / 非 orbit 主题 → 一帧静态（当前页档基准位直接成形）。
   运行态信号由 orbit.js 经 OrbitBG.setState 单向传入；唯一自读信号是 #chat 的 is-receiving
   （room.js 先例）。思想星线以粒子松动区抽象表达（05 轮次 7 裁定），不复制原 svg 逻辑。 */
(() => {
  const root = document.documentElement;
  const chat = document.getElementById("chat");
  const TAU = Math.PI * 2;
  const MESH_N = 160;                   // 前 MESH_N 个粒子构成神经网格（连边骨架）
  const SHELL_N = 240;                  // 其余为自由壳层粒子
  const P_N = MESH_N + SHELL_N;
  const CAM_DIST = 3.0;
  const FOV = 1.05;
  const BASE = { z: 0.75, r: 0.52 };    // 球心深度与基准半径（球心平面位由页档驱动）
  const ROOM = { floor: 1.02, ceil: 1.18, wallX: 2.55, zNear: -1.5, zFar: 3.6, step: 0.52 };
  const SPRING_K = 26;
  const DRAG = 7;
  // 五档构图目标（视口分数 + 半径档 + 亮度因子 + 房间视差），04a §2.1
  const POSE = {
    chat:       { fx: .72, fy: .62, r: 1.00, dim: 1.00, pan: -.10 },
    discovery:  { fx: .82, fy: .24, r: 0.70, dim: 1.00, pan: -.14 },
    thoughts:   { fx: .18, fy: .20, r: 0.70, dim: 1.00, pan: .14 },
    settings:   { fx: .50, fy: .88, r: 0.46, dim: 0.72, pan: 0 },
    onboarding: { fx: .50, fy: .22, r: 1.15, dim: 1.00, pan: 0 },
  };
  // 弹簧插值（apple-design springObject 同族）：T=0.9s 临界阻尼，4ms 子步进，落定吸附
  const SPRING_W = Math.PI * 2 / 0.9;

  // ---------- 舞台画布：body 级、z0，非 orbit 主题隐藏 ----------
  const canvas = document.createElement("canvas");
  canvas.id = "orbit-stage";
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText = "position:fixed;inset:0;z-index:0;pointer-events:none;display:none";
  document.body.insertBefore(canvas, document.body.firstChild);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  // ---------- 粒子群：斐波那契球面为家位置，弹簧动力学（room.js 同源） ----------
  const golden = Math.PI * (3 - Math.sqrt(5));
  const randomUnit = () => {
    const z = Math.random() * 2 - 1, th = Math.random() * TAU, r = Math.sqrt(Math.max(0, 1 - z * z));
    return [Math.cos(th) * r, Math.sin(th) * r, z];
  };
  const meshIdx = [];
  for (let m = 0; m < MESH_N; m++) meshIdx.push(Math.round(m * (P_N - 1) / (MESH_N - 1)));
  const meshSet = new Set(meshIdx);
  const particles = [];
  for (let i = 0; i < P_N; i++) {
    const y = 1 - (i / (P_N - 1)) * 2;
    const rad = Math.sqrt(Math.max(0, 1 - y * y));
    const th = golden * i;
    const mesh = meshSet.has(i);
    const thick = mesh ? 0 : (Math.random() - 0.5) * 0.07;
    const R = 1 + thick;
    particles.push({
      hx: Math.cos(th) * rad * R, hy: y * R, hz: Math.sin(th) * rad * R,
      x: Math.cos(th) * rad * R, y: y * R, z: Math.sin(th) * rad * R,
      vx: 0, vy: 0, vz: 0, sp: 0, env: 0,
      r0: (0.85 + Math.random() * 0.5) + (mesh ? 0.35 : 0),
      p: [Math.random() * TAU, Math.random() * TAU, Math.random() * TAU],
      f: [0.5 + Math.random() * 0.8, 0.5 + Math.random() * 0.8, 0.5 + Math.random() * 0.8],
    });
  }
  const pts = Array.from({ length: P_N }, () => ({ x: 0, y: 0, z: 0, env: 0 }));
  const edges = [];
  {
    const seen = new Set();
    for (const i of meshIdx) {
      const ds = [];
      for (const j of meshIdx) {
        if (j === i) continue;
        const a = particles[i], b = particles[j];
        ds.push([(a.hx - b.hx) ** 2 + (a.hy - b.hy) ** 2 + (a.hz - b.hz) ** 2, j]);
      }
      ds.sort((u, v) => u[0] - v[0]);
      for (const [, j] of ds.slice(0, 3)) {
        const key = i < j ? i + "_" + j : j + "_" + i;
        if (!seen.has(key)) { seen.add(key); edges.push([i, j]); }
      }
    }
  }

  // ---------- 局部松动区（思想活跃的抽象表达通道之一），room.js 同源 ----------
  const patches = [];
  let nextPatchAt = 0;
  function rotateDir(dir, axis, ang) {
    const c = Math.cos(ang), s = Math.sin(ang);
    const dot = dir[0] * axis[0] + dir[1] * axis[1] + dir[2] * axis[2];
    const cross = [axis[1] * dir[2] - axis[2] * dir[1], axis[2] * dir[0] - axis[0] * dir[2], axis[0] * dir[1] - axis[1] * dir[0]];
    for (let k = 0; k < 3; k++) dir[k] = dir[k] * c + cross[k] * s + axis[k] * dot * (1 - c);
  }
  function newPatch(t) {
    return {
      dir: randomUnit(), axis: randomUnit(),
      spin: (Math.random() < 0.5 ? -1 : 1) * (0.15 + Math.random() * 0.35),
      radius: 0.55 + Math.random() * 0.4,
      born: t, die: t + 9 + Math.random() * 12,
      base: 0.5 + Math.random() * 0.5,
    };
  }
  function updatePatches(t, excite, dt) {
    for (let i = patches.length - 1; i >= 0; i--) {
      const s = patches[i];
      if (t > s.die) { patches.splice(i, 1); continue; }
      rotateDir(s.dir, s.axis, s.spin * dt);
    }
    const want = excite > 0.3 ? 2 : (t > nextPatchAt ? 1 : 0);
    while (patches.length < want) {
      patches.push(newPatch(t));
      nextPatchAt = t + 6 + Math.random() * 14;
    }
    for (const s of patches) {
      const life = Math.min(1, Math.max(0, (t - s.born) / (s.die - s.born)));
      s.k = s.base * Math.sin(Math.PI * life) * (1 + 0.5 * excite);
      s.cosR = Math.cos(s.radius);
    }
  }
  const patchEnv = (pt) => {
    let e = 0;
    for (const s of patches) {
      const dot = pt.x * s.dir[0] + pt.y * s.dir[1] + pt.z * s.dir[2];
      if (dot <= s.cosR) continue;
      const u = (dot - s.cosR) / (1 - s.cosR);
      const v = u * u * (3 - 2 * u) * s.k;
      if (v > e) e = v;
    }
    return e;
  };

  // ---------- 投影（球心平面位 cur.x/cur.y 由页档弹簧驱动） ----------
  let W = 0, H = 0;
  let CX = 0, CY = 0, FOVK = 0, FCENTER = 0;
  const recalcMetrics = () => { FOVK = H * FOV; CX = W / 2; CY = H * 0.47; FCENTER = FOVK / (BASE.z + CAM_DIST); };
  const project = (x, y, z) => {
    const zz = z + CAM_DIST;
    const f = FOVK / zz;
    return [CX + x * f, CY + y * f, zz];
  };
  const fog = (z1, z2) => {
    const zc = (z1 + z2) / 2;
    return Math.max(0.16, 1 - Math.max(0, zc - 1.4) / (CAM_DIST + ROOM.zFar - 1.4) * 0.82);
  };

  const drawRoom = (dim, pan) => {
    const { floor, ceil, wallX, zNear, zFar, step } = ROOM;
    const lineEnergy = 0.6 + 0.4 * dim;                 // P4 低亮度托底：只降能量不改色相
    ctx.lineWidth = 1;
    const seg = (x1, y1, z1, x2, y2, z2, base) => {
      const [ax, ay, az] = project(x1 + pan, y1, z1);
      const [bx, by, bz] = project(x2 + pan, y2, z2);
      const a = base * lineEnergy * fog(az, bz);
      if (a < 0.01) return;
      ctx.strokeStyle = `rgba(120,159,215,${a.toFixed(3)})`;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    };
    for (let z = zNear; z <= zFar + 1e-6; z += step) {
      seg(-wallX, floor, z, wallX, floor, z, 0.30);
      seg(-wallX, -ceil, z, wallX, -ceil, z, 0.15);
    }
    for (let x = -wallX; x <= wallX + 1e-6; x += step) {
      seg(x, floor, zNear, x, floor, zFar, 0.30);
      seg(x, -ceil, zNear, x, -ceil, zFar, 0.15);
    }
    for (let z = zNear; z <= zFar + 1e-6; z += step) {
      seg(wallX, -ceil, z, wallX, floor, z, 0.20);
      seg(-wallX, -ceil, z, -wallX, floor, z, 0.20);
    }
    for (let y = -ceil; y <= floor + 1e-6; y += step) {
      seg(wallX, y, zNear, wallX, y, zFar, 0.20);
      seg(-wallX, y, zNear, -wallX, y, zFar, 0.20);
    }
    seg(-wallX, floor, zNear, -wallX, floor, zFar, 0.42);
    seg(wallX, floor, zNear, wallX, floor, zFar, 0.42);
    seg(-wallX, floor, zFar, wallX, floor, zFar, 0.42);
    seg(-wallX, floor, zNear, wallX, floor, zNear, 0.42);
  };

  // ---------- 粒子物理：弹簧回位 + 呼吸噪声力 + 松动区外扩（room.js 同源） ----------
  const rot = { cy: 1, sy: 0, cx: 1, sx: 0, R: BASE.r };
  function updateParticles(t, excite, dt, cur) {
    const sleeping = cur.sleep ? 1 : 0;
    const breathe = 1 + (sleeping ? 0.012 : 0.022) * Math.sin(t * 0.45) + excite * 0.05 * Math.sin(t * 5.2);
    const ry = t * (sleeping ? 0.06 : 0.14);
    const rx = 0.14 + 0.03 * Math.sin(t * 0.21) + excite * 0.03 * Math.sin(t * 2.6);
    rot.cy = Math.cos(ry); rot.sy = Math.sin(ry);
    rot.cx = Math.cos(rx); rot.sx = Math.sin(rx);
    rot.R = BASE.r * cur.r * breathe;
    const A = 0.5 * (1 + 1.2 * excite);
    const K = SPRING_K - 4 * excite;
    const dragF = Math.exp(-DRAG * dt);
    const swayX = excite * 0.02 * Math.sin(t * 4.7);
    const swayY = excite * 0.016 * Math.sin(t * 3.9 + 1.2);
    for (let i = 0; i < P_N; i++) {
      const pt = particles[i];
      const env = patchEnv(pt);
      let fx = A * Math.sin(t * pt.f[0] + pt.p[0]);
      let fy = A * Math.sin(t * pt.f[1] + pt.p[1]);
      let fz = A * Math.sin(t * pt.f[2] + pt.p[2]);
      const drift = env * (0.10 + 0.05 * Math.sin(t * 0.9 + pt.p[1]));
      const wob = env * 0.035;
      const tx = pt.hx * (1 + drift) + wob * Math.sin(t * 1.6 + pt.p[0]);
      const ty = pt.hy * (1 + drift) + wob * Math.sin(t * 1.9 + pt.p[1]);
      const tz = pt.hz * (1 + drift) + wob * Math.sin(t * 1.4 + pt.p[2]);
      fx += (tx - pt.x) * K;
      fy += (ty - pt.y) * K;
      fz += (tz - pt.z) * K;
      pt.vx = (pt.vx + fx * dt) * dragF;
      pt.vy = (pt.vy + fy * dt) * dragF;
      pt.vz = (pt.vz + fz * dt) * dragF;
      pt.x += pt.vx * dt; pt.y += pt.vy * dt; pt.z += pt.vz * dt;
      pt.sp = Math.hypot(pt.vx, pt.vy, pt.vz);
      pt.env = env;
      const x2 = pt.x * rot.cy + pt.z * rot.sy, z2 = pt.z * rot.cy - pt.x * rot.sy;
      const y2 = pt.y * rot.cx - z2 * rot.sx, z3 = pt.y * rot.sx + z2 * rot.cx;
      const wx = cur.x + swayX + x2 * rot.R, wy = cur.y + swayY + y2 * rot.R, wz = BASE.z + z3 * rot.R;
      const f = FOVK / (wz + CAM_DIST);
      const p = pts[i];
      p.x = CX + wx * f; p.y = CY + wy * f; p.z = wz + CAM_DIST; p.env = env;
    }
  }
  function projectStatic(cur) {                  // 静态帧：页档基准位直接成形（不经弹簧）
    const ry = 9.7 * 0.14, rx = 0.14 + 0.03 * Math.sin(9.7 * 0.21);
    rot.cy = Math.cos(ry); rot.sy = Math.sin(ry);
    rot.cx = Math.cos(rx); rot.sx = Math.sin(rx);
    rot.R = BASE.r * cur.r * 1.022;
    for (let i = 0; i < P_N; i++) {
      const pt = particles[i];
      const x2 = pt.hx * rot.cy + pt.hz * rot.sy, z2 = pt.hz * rot.cy - pt.hx * rot.sy;
      const y2 = pt.hy * rot.cx - z2 * rot.sx, z3 = pt.hy * rot.sx + z2 * rot.cx;
      const wx = cur.x + x2 * rot.R, wy = cur.y + y2 * rot.R, wz = BASE.z + z3 * rot.R;
      const f = FOVK / (wz + CAM_DIST);
      const p = pts[i];
      p.x = CX + wx * f; p.y = CY + wy * f; p.z = wz + CAM_DIST; p.env = 0;
      pt.sp = 0;
    }
  }
  const nearness = (zz) => Math.min(1, Math.max(0.12, (5.2 - zz) / 3.2));

  function drawParticles(excite, dim) {
    const eBuckets = new Map();
    for (const [i, j] of edges) {
      const a = pts[i], b = pts[j];
      const nz = nearness((a.z + b.z) / 2);
      const alpha = (0.11 + 0.07 * nz) * (1 + 0.4 * excite) * dim;
      const key = Math.min(9, Math.round(alpha * 12));
      if (key <= 0) continue;
      if (!eBuckets.has(key)) eBuckets.set(key, []);
      eBuckets.get(key).push(a.x, a.y, b.x, b.y);
    }
    ctx.lineWidth = 1;
    for (const [key, list] of eBuckets) {
      ctx.strokeStyle = `rgba(156,196,244,${(key / 12).toFixed(3)})`;
      ctx.beginPath();
      for (let k = 0; k < list.length; k += 4) {
        ctx.moveTo(list[k], list[k + 1]);
        ctx.lineTo(list[k + 2], list[k + 3]);
      }
      ctx.stroke();
    }
    const pBuckets = new Map();
    for (let i = 0; i < P_N; i++) {
      const pt = particles[i], p = pts[i];
      const nz = nearness(p.z);
      const glow = Math.min(0.3, pt.sp * 1.4);
      const a = Math.min(1, (0.34 + 0.40 * nz + glow + pt.env * 0.15) * dim);
      const key = Math.min(8, Math.round(a * 8));
      if (key <= 0) continue;
      const r = pt.r0 * (0.75 + 0.55 * nz);
      if (!pBuckets.has(key)) pBuckets.set(key, []);
      pBuckets.get(key).push(p.x, p.y, r);
    }
    for (const [key, list] of pBuckets) {
      ctx.fillStyle = `rgba(163,198,255,${(key / 8).toFixed(3)})`;
      ctx.beginPath();
      for (let k = 0; k < list.length; k += 3) {
        ctx.moveTo(list[k] + list[k + 2], list[k + 1]);
        ctx.arc(list[k], list[k + 1], list[k + 2], 0, TAU);
      }
      ctx.fill();
    }
  }

  // ---------- 页档状态与弹簧（逐量独立，落定吸附） ----------
  const chan = () => ({ x: 0, y: 0, r: 1, dim: 1, pan: 0 });
  const cur = chan(), vel = chan();
  const tgt = chan();
  tgt.sleep = 0;
  let poseName = "chat";
  const CH = ["x", "y", "r", "dim", "pan"];
  const settled = { x: true, y: true, r: true, dim: true, pan: true };
  function retarget() {
    const p = POSE[poseName] || POSE.chat;
    tgt.x = (p.fx * W - CX) / FCENTER;               // 视口分数 → 世界坐标（一次解出）
    tgt.y = (p.fy * H - CY) / FCENTER;
    tgt.r = p.r; tgt.dim = p.dim; tgt.pan = p.pan;
    tgt.sleep = poseName === "thoughts" ? signals.thoughtState : 0;
    for (const k of CH) settled[k] = false;
    kick();
  }
  function integrateSpring(dt) {
    const steps = Math.max(1, Math.ceil(dt / 0.004));
    const h = dt / steps;
    for (let s = 0; s < steps; s++) {
      for (const k of CH) {
        if (settled[k]) continue;
        const a = SPRING_W * SPRING_W * (tgt[k] - cur[k]) - 2 * SPRING_W * vel[k];   // 临界阻尼 z=1
        vel[k] += a * h;
        cur[k] += vel[k] * h;
        if (Math.abs(tgt[k] - cur[k]) < 0.002 && Math.abs(vel[k]) < 0.01) {
          cur[k] = tgt[k]; vel[k] = 0; settled[k] = true;
        }
      }
    }
  }

  // ---------- 运行态信号（orbit.js 单向传入；唯一自读 #chat.is-receiving） ----------
  const signals = { receiving: false, discoveryRunning: false, thoughtState: 0 };   // thoughtState: 0 清醒 / 1 运行 / 2 睡眠
  let excite = 0;
  function exciteTarget() {
    if (signals.thoughtState === 2) return 0;        // 睡眠强制安静（优先级最高）
    return Math.max(signals.receiving ? 1 : 0, signals.discoveryRunning ? 0.35 : 0,
      signals.thoughtState === 1 ? 0.35 : 0);
  }

  // ---------- 主绘制 ----------
  function draw(t, dt) {
    ctx.clearRect(0, 0, W, H);
    drawRoom(cur.dim, cur.pan);
    const gx = CX + cur.x * FCENTER, gy = CY + cur.y * FCENTER;
    const gr = ctx.createRadialGradient(gx, gy, 0, gx, gy, H * 0.28);
    gr.addColorStop(0, `rgba(120,159,215,${(0.12 * cur.dim).toFixed(3)})`);
    gr.addColorStop(1, "rgba(120,159,215,0)");
    ctx.fillStyle = gr;
    ctx.fillRect(gx - H * 0.28, gy - H * 0.28, H * 0.56, H * 0.56);
    updatePatches(t, excite, dt);
    updateParticles(t, excite, dt, cur);
    drawParticles(excite, cur.dim);
  }
  function paintStatic() {
    ctx.clearRect(0, 0, W, H);
    drawRoom(tgt.dim, tgt.pan);
    const gx = CX + tgt.x * FCENTER, gy = CY + tgt.y * FCENTER;
    const gr = ctx.createRadialGradient(gx, gy, 0, gx, gy, H * 0.28);
    gr.addColorStop(0, `rgba(120,159,215,${(0.12 * tgt.dim).toFixed(3)})`);
    gr.addColorStop(1, "rgba(120,159,215,0)");
    ctx.fillStyle = gr;
    ctx.fillRect(gx - H * 0.28, gy - H * 0.28, H * 0.56, H * 0.56);
    projectStatic(tgt);
    drawParticles(0, tgt.dim);
  }

  // ---------- 循环与门禁 ----------
  let raf = 0, last = 0;
  const themeOK = () => root.dataset.theme === "orbit";
  const hardOK = () => root.dataset.motion === "on" && !document.hidden && themeOK();
  function tick(ts) {
    raf = 0;
    if (!hardOK()) {
      // 主题已切走时把舞台一起收起（kick 在 rAF 活动期会早退，这里负责隐藏）。
      (window.__bgLog = window.__bgLog || []).push(["tick-stop", root.dataset.theme]);
      if (!themeOK()) canvas.style.display = "none";
      else paintStatic();
      return;
    }
    const t = ts / 1000;
    const dt = Math.min(0.1, last ? t - last : 0.016);
    last = t;
    integrateSpring(dt);
    const target = exciteTarget();
    const rate = signals.receiving && signals.thoughtState !== 2 ? 3.2 : 1.4;
    excite += (target - excite) * Math.min(1, dt * rate);
    draw(t, dt);
    raf = requestAnimationFrame(tick);
  }
  function kick() {
    // 诊断面：舞台停画时序排查（量小无扰；与 orbit.js __orbitApplyLog 同族）
    (window.__bgLog = window.__bgLog || []).push(["kick", root.dataset.theme, raf ? "raf" : "idle"]);
    if (raf) return;
    canvas.style.display = themeOK() ? "" : "none";
    if (!hardOK()) { if (W) paintStatic(); return; }
    last = 0;
    raf = requestAnimationFrame(tick);
  }

  const resize = () => {
    const w = window.innerWidth, h = window.innerHeight;
    if (!w || !h) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    W = w; H = h;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    recalcMetrics();
    retarget();
    if (!raf && W) paintStatic();
  };
  window.addEventListener("resize", resize);

  if (chat) {
    const syncReceiving = () => { signals.receiving = chat.classList.contains("is-receiving"); };
    new MutationObserver(() => { syncReceiving(); }).observe(chat, { attributes: true, attributeFilter: ["class"] });
    syncReceiving();
  }
  new MutationObserver(kick).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
  new MutationObserver(kick).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  document.addEventListener("visibilitychange", kick);

  window.OrbitBG = {
    setPage(name) {
      poseName = POSE[name] ? name : "chat";
      retarget();
    },
    setState(patch) {
      let changed = false;
      for (const k of Object.keys(patch)) {
        if (signals[k] !== patch[k]) { signals[k] = patch[k]; changed = true; }
      }
      if (changed && poseName === "thoughts") retarget();   // 睡眠/清醒切换影响tgt.sleep
    },
  };

  resize();
  kick();
})();
