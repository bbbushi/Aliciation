/* 背景：网格伪 3D 房间 + 中央神经球（弹簧粒子群 + 局部小幅聚合散开）。
   纯装饰，不读会话数据、不发请求、不响应指针。
   动效门禁与氛围层一致：设置关闭 / 系统减动效 / 页面隐藏时停帧（留一帧静态画面）；
   AI 回复中（#chat.is-receiving）粒子群轻微躁动；仅房间主题绘制——orbit 的常驻场景由
   orbit-bg.js 接管，经典主题由 CSS 整层停用。 */
(() => {
  const root = document.documentElement;
  const chat = document.getElementById("chat");
  const layer = chat && chat.querySelector(".chat-atmosphere");
  const canvas = layer && layer.querySelector(".atmosphere-room");
  const ctx = canvas && canvas.getContext("2d");
  if (!ctx) return;

  const TAU = Math.PI * 2;
  const MESH_N = 160;                   // 前 MESH_N 个粒子构成神经网格（连边骨架）
  const SHELL_N = 240;                  // 其余为自由壳层粒子
  const P_N = MESH_N + SHELL_N;
  const CAM_DIST = 3.0;                 // 相机到原点距离（伪 3D 简化投影）
  const FOV = 1.05;                     // ×画布高
  const SPHERE = { x: 0, y: -0.04, z: 0.75, r: 0.52 };
  // 房间：地面 y=+floor，天花板 y=-ceil，两侧墙 x=±wallX，纵深 z∈[zNear,zFar]；无后墙，远处隐入雾
  const ROOM = { floor: 1.02, ceil: 1.18, wallX: 2.55, zNear: -1.5, zFar: 3.6, step: 0.52 };
  const SPRING_K = 26;                  // 弹簧刚度（回弹手感）
  const DRAG = 7;                       // 速度阻尼系数

  // ---------- 粒子群：斐波那契球面为家位置，弹簧动力学 ----------
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
    const thick = mesh ? 0 : (Math.random() - 0.5) * 0.07;   // 壳层粒子带径向厚度
    const R = 1 + thick;
    particles.push({
      hx: Math.cos(th) * rad * R, hy: y * R, hz: Math.sin(th) * rad * R,   // 家位置（对象空间）
      x: Math.cos(th) * rad * R, y: y * R, z: Math.sin(th) * rad * R,      // 当前位置（从家出发）
      vx: 0, vy: 0, vz: 0, sp: 0, env: 0,
      r0: (0.85 + Math.random() * 0.5) + (mesh ? 0.35 : 0),   // 粒子大小（网格粒子略大）
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
      for (const [, j] of ds.slice(0, 3)) {                    // 每网格节点连 3 近邻
        const key = i < j ? i + "_" + j : j + "_" + i;
        if (!seen.has(key)) { seen.add(key); edges.push([i, j]); }
      }
    }
  }

  // ---------- 局部小幅聚合散开：游走的松动区，区内粒子目标半径轻微外扩、飘移后收回 ----------
  const patches = [];
  let nextPatchAt = 0;
  function rotateDir(dir, axis, ang) {  // Rodrigues 旋转（游走用）
    const c = Math.cos(ang), s = Math.sin(ang);
    const dot = dir[0] * axis[0] + dir[1] * axis[1] + dir[2] * axis[2];
    const cross = [axis[1] * dir[2] - axis[2] * dir[1], axis[2] * dir[0] - axis[0] * dir[2], axis[0] * dir[1] - axis[1] * dir[0]];
    for (let k = 0; k < 3; k++) dir[k] = dir[k] * c + cross[k] * s + axis[k] * dot * (1 - c);
  }
  function newPatch(t) {
    return {
      dir: randomUnit(), axis: randomUnit(),
      spin: (Math.random() < 0.5 ? -1 : 1) * (0.15 + Math.random() * 0.35),
      radius: 0.55 + Math.random() * 0.4,          // 角半径：一次只松动球面一小片
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
      s.k = s.base * Math.sin(Math.PI * life) * (1 + 0.5 * excite);   // 平滑淡入淡出
      s.cosR = Math.cos(s.radius);
    }
  }
  const patchEnv = (pt) => {  // 粒子在松动区内的包络 0..1（对象空间，随球自旋一起转）
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

  // ---------- 投影 ----------
  let W = 0, H = 0;
  const project = (x, y, z) => {
    const zz = z + CAM_DIST;
    const f = H * FOV / zz;
    return [W / 2 + x * f, H * 0.47 + y * f, zz];
  };
  // 线段深度雾：近处实、远处淡
  const fog = (z1, z2) => {
    const zc = (z1 + z2) / 2;
    return Math.max(0.16, 1 - Math.max(0, zc - 1.4) / (CAM_DIST + ROOM.zFar - 1.4) * 0.82);
  };

  const drawRoom = () => {
    const { floor, ceil, wallX, zNear, zFar, step } = ROOM;
    ctx.lineWidth = 1;
    const seg = (x1, y1, z1, x2, y2, z2, base) => {
      const [ax, ay, az] = project(x1, y1, z1);
      const [bx, by, bz] = project(x2, y2, z2);
      const a = base * fog(az, bz);
      if (a < 0.01) return;
      ctx.strokeStyle = `rgba(120,159,215,${a.toFixed(3)})`;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    };
    for (let z = zNear; z <= zFar + 1e-6; z += step) {          // 地面/天花板横线
      seg(-wallX, floor, z, wallX, floor, z, 0.30);
      seg(-wallX, -ceil, z, wallX, -ceil, z, 0.15);
    }
    for (let x = -wallX; x <= wallX + 1e-6; x += step) {        // 地面/天花板纵线
      seg(x, floor, zNear, x, floor, zFar, 0.30);
      seg(x, -ceil, zNear, x, -ceil, zFar, 0.15);
    }
    for (let z = zNear; z <= zFar + 1e-6; z += step) {          // 侧墙竖线
      seg(wallX, -ceil, z, wallX, floor, z, 0.20);
      seg(-wallX, -ceil, z, -wallX, floor, z, 0.20);
    }
    for (let y = -ceil; y <= floor + 1e-6; y += step) {         // 侧墙横线
      seg(wallX, y, zNear, wallX, y, zFar, 0.20);
      seg(-wallX, y, zNear, -wallX, y, zFar, 0.20);
    }
    // 地面边框（房间轮廓，略亮）
    seg(-wallX, floor, zNear, -wallX, floor, zFar, 0.42);
    seg(wallX, floor, zNear, wallX, floor, zFar, 0.42);
    seg(-wallX, floor, zFar, wallX, floor, zFar, 0.42);
    seg(-wallX, floor, zNear, wallX, floor, zNear, 0.42);
  };

  // ---------- 粒子物理：弹簧回位 + 呼吸噪声力 + 松动区目标外扩 ----------
  const rot = { cy: 1, sy: 0, cx: 1, sx: 0, R: SPHERE.r };   // 每帧由 updateParticles 更新
  function updateParticles(t, excite, dt) {
    const breathe = 1 + 0.022 * Math.sin(t * 0.45) + excite * 0.05 * Math.sin(t * 5.2);
    const ry = t * 0.14;
    const rx = 0.14 + 0.03 * Math.sin(t * 0.21) + excite * 0.03 * Math.sin(t * 2.6);
    rot.cy = Math.cos(ry); rot.sy = Math.sin(ry);
    rot.cx = Math.cos(rx); rot.sx = Math.sin(rx);
    rot.R = SPHERE.r * breathe;
    const A = 0.5 * (1 + 1.2 * excite);        // 呼吸噪声力幅度（说话时略躁）
    const K = SPRING_K - 4 * excite;
    const dragF = Math.exp(-DRAG * dt);
    const swayX = excite * 0.02 * Math.sin(t * 4.7);
    const swayY = excite * 0.016 * Math.sin(t * 3.9 + 1.2);
    const FOVK = H * FOV, CX = W / 2, CY = H * 0.47;
    for (let i = 0; i < P_N; i++) {
      const pt = particles[i];
      const env = patchEnv(pt);                // 松动区包络
      let fx = A * Math.sin(t * pt.f[0] + pt.p[0]);
      let fy = A * Math.sin(t * pt.f[1] + pt.p[1]);
      let fz = A * Math.sin(t * pt.f[2] + pt.p[2]);
      // 目标：家位置 ×（1 + 小幅外扩）+ 轻微切向漂移；弹簧把粒子拉到目标上
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
      // 投影（内联，避免每粒子分配临时数组）
      const x2 = pt.x * rot.cy + pt.z * rot.sy, z2 = pt.z * rot.cy - pt.x * rot.sy;
      const y2 = pt.y * rot.cx - z2 * rot.sx, z3 = pt.y * rot.sx + z2 * rot.cx;
      const wx = SPHERE.x + swayX + x2 * rot.R, wy = SPHERE.y + swayY + y2 * rot.R, wz = SPHERE.z + z3 * rot.R;
      const f = FOVK / (wz + CAM_DIST);
      const p = pts[i];
      p.x = CX + wx * f; p.y = CY + wy * f; p.z = wz + CAM_DIST; p.env = env;
    }
  }
  function projectStatic() {                   // 静态帧：家位置成形（不跑物理）
    const ry = 9.7 * 0.14, rx = 0.14 + 0.03 * Math.sin(9.7 * 0.21);
    rot.cy = Math.cos(ry); rot.sy = Math.sin(ry);
    rot.cx = Math.cos(rx); rot.sx = Math.sin(rx);
    rot.R = SPHERE.r * 1.022;
    const FOVK = H * FOV, CX = W / 2, CY = H * 0.47;
    for (let i = 0; i < P_N; i++) {
      const pt = particles[i];
      const x2 = pt.hx * rot.cy + pt.hz * rot.sy, z2 = pt.hz * rot.cy - pt.hx * rot.sy;
      const y2 = pt.hy * rot.cx - z2 * rot.sx, z3 = pt.hy * rot.sx + z2 * rot.cx;
      const wx = SPHERE.x + x2 * rot.R, wy = SPHERE.y + y2 * rot.R, wz = SPHERE.z + z3 * rot.R;
      const f = FOVK / (wz + CAM_DIST);
      const p = pts[i];
      p.x = CX + wx * f; p.y = CY + wy * f; p.z = wz + CAM_DIST; p.env = 0;
      pt.sp = 0;
    }
  }
  const nearness = (zz) => Math.min(1, Math.max(0.12, (5.2 - zz) / 3.2));

  function drawParticles(excite) {
    const eBuckets = new Map();
    for (const [i, j] of edges) {
      const a = pts[i], b = pts[j];
      const nz = nearness((a.z + b.z) / 2);
      const alpha = (0.11 + 0.07 * nz) * (1 + 0.4 * excite);
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
      const glow = Math.min(0.3, pt.sp * 1.4);                   // 越躁越亮
      const a = Math.min(1, 0.34 + 0.40 * nz + glow + pt.env * 0.15);
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

  // ---------- 主绘制 ----------
  function draw(t, excite, isStatic, dt) {
    ctx.clearRect(0, 0, W, H);
    drawRoom();
    const c = project(SPHERE.x, SPHERE.y, SPHERE.z);            // 球心柔光
    const gr = ctx.createRadialGradient(c[0], c[1], 0, c[0], c[1], H * 0.28);
    gr.addColorStop(0, "rgba(120,159,215,0.12)");
    gr.addColorStop(1, "rgba(120,159,215,0)");
    ctx.fillStyle = gr;
    ctx.fillRect(c[0] - H * 0.28, c[1] - H * 0.28, H * 0.56, H * 0.56);
    if (isStatic) {                              // 静态帧：成形球体，不跑物理
      projectStatic();
      drawParticles(0);
      return;
    }
    updatePatches(t, excite, dt);
    updateParticles(t, excite, dt);
    drawParticles(excite);
  }

  // ---------- 尺寸 / 循环 / 门禁 ----------
  let raf = 0, last = 0, excite = 0, speaking = false;
  const themeOK = () => root.dataset.theme === "room";
  const hardOK = () => root.dataset.motion === "on" && !document.hidden && themeOK();
  const visOK = () => !chat.closest("[inert], .hidden");

  const paintStatic = () => draw(9.7, 0, true);

  function tick(ts) {
    raf = 0;
    if (!hardOK()) { paintStatic(); return; }
    const t = ts / 1000;
    const dt = Math.min(0.1, last ? t - last : 0.016);
    last = t;
    if (visOK()) {
      excite += ((speaking ? 1 : 0) - excite) * Math.min(1, dt * (speaking ? 3.2 : 1.4));
      draw(t, excite, false, dt);
    }
    raf = requestAnimationFrame(tick);
  }
  function kick() {
    if (raf) return;
    if (!hardOK()) { paintStatic(); return; }
    last = 0;
    raf = requestAnimationFrame(tick);
  }

  const resize = () => {
    const r = layer.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.round(r.width); H = Math.round(r.height);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!raf) paintStatic();
  };
  new ResizeObserver(resize).observe(layer);

  new MutationObserver(() => { speaking = chat.classList.contains("is-receiving"); })
    .observe(chat, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(kick).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
  new MutationObserver(kick).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  const app = document.getElementById("app");
  if (app) new MutationObserver(kick).observe(app, { attributes: true, attributeFilter: ["class"] });
  document.addEventListener("visibilitychange", kick);
  speaking = chat.classList.contains("is-receiving");
  resize();
  kick();
})();
