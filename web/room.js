/* 背景：网格伪 3D 房间 + 中央神经球。纯装饰，不读会话数据、不发请求。
   动效门禁与氛围层一致：设置关闭 / 系统减动效 / 页面隐藏时停帧（留一帧静态画面）；
   AI 回复中（#chat.is-receiving）神经球抖动加大、信号脉冲更密。 */
(() => {
  const root = document.documentElement;
  const chat = document.getElementById("chat");
  const layer = chat && chat.querySelector(".chat-atmosphere");
  const canvas = layer && layer.querySelector(".atmosphere-room");
  const ctx = canvas && canvas.getContext("2d");
  if (!ctx) return;

  const TAU = Math.PI * 2;
  const NODES_N = 160;
  const CAM_DIST = 3.0;                 // 相机到原点距离（伪 3D 简化投影）
  const FOV = 1.05;                     // ×画布高
  const SPHERE = { x: 0, y: -0.04, z: 0.75, r: 0.52 };
  // 房间：地面 y=+floor，天花板 y=-ceil，两侧墙 x=±wallX，纵深 z∈[zNear,zFar]；无后墙，远处隐入雾
  const ROOM = { floor: 1.02, ceil: 1.18, wallX: 2.55, zNear: -1.5, zFar: 3.6, step: 0.52 };

  // ---------- 神经球节点（斐波那契球面）+ 近邻连边 ----------
  const nodes = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < NODES_N; i++) {
    const y = 1 - (i / (NODES_N - 1)) * 2;
    const rad = Math.sqrt(Math.max(0, 1 - y * y));
    const th = golden * i;
    nodes.push({
      x: Math.cos(th) * rad, y, z: Math.sin(th) * rad,
      // 每节点三轴独立相位的低频漂移（自然抖动的底噪）+ 高频微颤相位
      p: [Math.random() * TAU, Math.random() * TAU, Math.random() * TAU],
      f: [0.4 + Math.random() * 0.7, 0.4 + Math.random() * 0.7, 0.4 + Math.random() * 0.7],
      s: [Math.random() * TAU, Math.random() * TAU, Math.random() * TAU],
    });
  }
  const edges = [];
  {
    const seen = new Set();
    for (let i = 0; i < NODES_N; i++) {
      const ds = [];
      for (let j = 0; j < NODES_N; j++) {
        if (j === i) continue;
        const a = nodes[i], b = nodes[j];
        ds.push([(a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2, j]);
      }
      ds.sort((u, v) => u[0] - v[0]);
      for (const [, j] of ds.slice(0, 3)) {                    // 每节点连 3 近邻，网更密
        const key = i < j ? i + "_" + j : j + "_" + i;
        if (!seen.has(key)) { seen.add(key); edges.push([i, j]); }
      }
    }
  }

  // ---------- 局部粒子风暴：球面上游走的扰动区，平时间歇出现，说话时更多更强 ----------
  const storms = [];
  let nextStormAt = 0;
  const randomUnit = () => {
    const z = Math.random() * 2 - 1, th = Math.random() * TAU, r = Math.sqrt(Math.max(0, 1 - z * z));
    return [Math.cos(th) * r, Math.sin(th) * r, z];
  };
  function rotateDir(dir, axis, ang) {  // Rodrigues 旋转（游走用）
    const c = Math.cos(ang), s = Math.sin(ang);
    const dot = dir[0] * axis[0] + dir[1] * axis[1] + dir[2] * axis[2];
    const cross = [axis[1] * dir[2] - axis[2] * dir[1], axis[2] * dir[0] - axis[0] * dir[2], axis[0] * dir[1] - axis[1] * dir[0]];
    for (let k = 0; k < 3; k++) dir[k] = dir[k] * c + cross[k] * s + axis[k] * dot * (1 - c);
  }
  function newStorm(t, excite) {
    return {
      dir: randomUnit(), axis: randomUnit(),
      spin: (Math.random() < 0.5 ? -1 : 1) * (0.25 + Math.random() * 0.5),
      radius: 0.5 + Math.random() * 0.35 + excite * 0.12,
      born: t, die: t + 7 + Math.random() * 9,
      base: 0.5 + Math.random() * 0.5,
    };
  }
  function updateStorms(t, excite, dt) {
    for (let i = storms.length - 1; i >= 0; i--) {
      const s = storms[i];
      if (t > s.die) { storms.splice(i, 1); continue; }
      rotateDir(s.dir, s.axis, s.spin * dt);
    }
    const want = excite > 0.25 ? 2 : (t > nextStormAt ? 1 : 0);
    while (storms.length < want) {
      storms.push(newStorm(t, excite));
      nextStormAt = t + 4 + Math.random() * 9;  // 风暴间歇：平时只有微弱波动
    }
    // 每帧一次算好 [强度, cos(半径)]，节点包络查表
    for (const s of storms) {
      const life = Math.min(1, Math.max(0, (t - s.born) / (s.die - s.born)));
      s.k = s.base * Math.sin(Math.PI * life) * (1 + 1.1 * excite);   // 平滑淡入淡出
      s.cosR = Math.cos(s.radius);
    }
  }
  const stormEnv = (n) => {  // 节点在风暴区内的包络 0..1（对象空间，随球自旋一起转）
    let e = 0;
    for (const s of storms) {
      const dot = n.x * s.dir[0] + n.y * s.dir[1] + n.z * s.dir[2];
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

  // ---------- 神经球 ----------
  const pts = new Array(NODES_N);
  const rot = { cy: 1, sy: 0, cx: 1, sx: 0, R: SPHERE.r };   // 每帧由 spherePoints 更新；喷流粒子与球同转
  function sphereToWorld(x, y, z, R) {                        // 对象空间 → 世界空间（与节点同一旋转）
    const x2 = x * rot.cy + z * rot.sy, z2 = -x * rot.sy + z * rot.cy;   // 绕 Y
    const y2 = y * rot.cx - z2 * rot.sx, z3 = y * rot.sx + z2 * rot.cx;  // 绕 X
    return [SPHERE.x + x2 * R, SPHERE.y + y2 * R, SPHERE.z + z3 * R];
  }
  function spherePoints(t, excite) {
    const breathe = 1 + 0.022 * Math.sin(t * 0.45) + excite * 0.08 * Math.sin(t * 5.2);
    const ry = t * 0.14;
    const rx = 0.35 + 0.06 * Math.sin(t * 0.21) + excite * 0.06 * Math.sin(t * 2.6);
    rot.cy = Math.cos(ry); rot.sy = Math.sin(ry);
    rot.cx = Math.cos(rx); rot.sx = Math.sin(rx);
    rot.R = SPHERE.r * breathe;
    const amp = 0.028 + excite * (0.10 + 0.04 * Math.sin(t * 6.5));   // 微弱波动底噪
    const shiver = excite * 0.02;
    const swayX = excite * 0.03 * Math.sin(t * 4.7);
    const swayY = excite * 0.024 * Math.sin(t * 3.9 + 1.2);
    for (let i = 0; i < NODES_N; i++) {
      const n = nodes[i];
      const w1 = Math.sin(t * n.f[0] * TAU * 0.25 + n.p[0]);
      const w2 = Math.sin(t * n.f[1] * TAU * 0.25 + n.p[1]);
      const w3 = Math.sin(t * n.f[2] * TAU * 0.25 + n.p[2]);
      const env = stormEnv(n);                       // 风暴包络：源区表面沸腾
      const burst = env * 0.13;
      let x = n.x + amp * w1 + burst * Math.sin(t * 9.1 + n.s[0] * 3.1) * Math.sin(t * 4.3 + n.p[0]);
      let y = n.y + amp * w2 + burst * Math.sin(t * 8.3 + n.s[1] * 2.7) * Math.sin(t * 5.1 + n.p[1]);
      let z = n.z + amp * w3 + burst * Math.sin(t * 9.7 + n.s[2] * 3.7) * Math.sin(t * 4.7 + n.p[2]);
      if (env > 0.02) {                              // 风暴内叠加高频碎颤
        x += env * 0.05 * Math.sin(t * 15.3 + n.s[1]);
        y += env * 0.05 * Math.sin(t * 14.1 + n.s[2]);
      }
      const [wx, wy, wz] = sphereToWorld(x, y, z, rot.R);
      const [px, py, zz] = project(SPHERE.x + swayX + (wx - SPHERE.x),
                                   SPHERE.y + swayY + (wy - SPHERE.y),
                                   SPHERE.z + (wz - SPHERE.z));
      pts[i] = { x: px, y: py, z: zz, env };
    }
  }
  const nearness = (zz) => Math.min(1, Math.max(0.12, (5.2 - zz) / 3.2));

  function drawSphere(excite) {
    for (const [i, j] of edges) {
      const a = pts[i], b = pts[j];
      const nz = nearness((a.z + b.z) / 2);
      const energized = Math.min(1, (a.env + b.env) / 2);        // 风暴区连线被“点亮”
      const alpha = (0.07 + 0.10 * nz + 0.12 * energized) * (1 + 0.8 * excite);
      ctx.strokeStyle = `rgba(156,196,244,${alpha.toFixed(3)})`;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    for (const p of pts) {
      const nz = nearness(p.z);
      const r = 1.0 + 1.5 * nz;
      if (nz > 0.75) {                                          // 近处节点带一点光晕
        ctx.fillStyle = `rgba(163,198,255,${(0.10 * nz).toFixed(3)})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, r * 3, 0, TAU); ctx.fill();
      }
      ctx.fillStyle = `rgba(163,198,255,${Math.min(1, 0.32 + 0.48 * nz + 0.2 * p.env).toFixed(3)})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.fill();
    }
  }

  // ---------- 太阳风暴喷流：喷发粒子流、磁环（日珥）与源区闪光 ----------
  const ejecta = [];   // 对象空间粒子：p 位置 / v 速度（单位球坐标系）
  const loops = [];    // 磁环：a/b 端点方向 / h 顶高
  let flash = null;    // {dir, born, strength}
  let eruptionHerald = 0;
  const GRAVITY = 0.5; // 把喷出的等离子往回拽的“磁重力”

  function surfacePointInStorm() {   // 在风暴锥内取一个喷发源方向
    const s = storms[(Math.random() * storms.length) | 0];
    if (!s) return null;
    const tx = Math.abs(s.dir[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
    let u = [s.dir[1] * tx[2] - s.dir[2] * tx[1], s.dir[2] * tx[0] - s.dir[0] * tx[2], s.dir[0] * tx[1] - s.dir[1] * tx[0]];
    const ul = Math.hypot(u[0], u[1], u[2]) || 1;
    u = [u[0] / ul, u[1] / ul, u[2] / ul];
    const w = [s.dir[1] * u[2] - s.dir[2] * u[1], s.dir[2] * u[0] - s.dir[0] * u[2], s.dir[0] * u[1] - s.dir[1] * u[0]];
    const ang = Math.random() * s.radius * 0.7, ph = Math.random() * TAU, sa = Math.sin(ang);
    const d = [0, 0, 0];
    for (let k = 0; k < 3; k++) d[k] = s.dir[k] * Math.cos(ang) + u[k] * sa * Math.cos(ph) + w[k] * sa * Math.sin(ph);
    return { d, s };
  }

  function spawnEruption(t, excite, big) {
    const src = surfacePointInStorm();
    if (!src) return;
    const { d, s } = src;
    if (big) flash = { dir: d.slice(), born: t, strength: Math.min(1, s.k * 1.2) };
    const n = big ? 32 + (Math.random() * 16 | 0) : 6 + (Math.random() * 6 | 0);
    const speedK = (big ? 0.9 : 0.55) * (1 + 1.1 * excite);
    for (let i = 0; i < n; i++) {
      const jit = [d[0] + (Math.random() - 0.5) * 0.3, d[1] + (Math.random() - 0.5) * 0.3, d[2] + (Math.random() - 0.5) * 0.3];
      const jl = Math.hypot(jit[0], jit[1], jit[2]) || 1;
      const speed = speedK * (0.7 + Math.random() * 0.6);
      ejecta.push({
        p: [jit[0] / jl, jit[1] / jl, jit[2] / jl],
        v: [jit[0] / jl * speed, jit[1] / jl * speed, jit[2] / jl * speed],
        born: t + Math.random() * 0.3,             // 错峰喷发
        ttl: 1.3 + Math.random() * 1.5,
        bright: 0.45 + Math.random() * 0.55,
      });
    }
  }

  function spawnSpicule(t) {                       // 平时的零星针状体：小股喷发后回落
    const d = randomUnit();
    const speed = 0.32 + Math.random() * 0.22;
    ejecta.push({ p: d.slice(), v: [d[0] * speed, d[1] * speed, d[2] * speed],
                  born: t, ttl: 1.1 + Math.random() * 0.9, bright: 0.3 });
  }

  function updateEjecta(t, dt, excite) {
    for (const s of storms) if (Math.random() < s.k * 2.0 * dt) spawnEruption(t, excite, false);
    if (!storms.length && Math.random() < 1.2 * dt) spawnSpicule(t);   // 风暴间歇：微弱波动之外的小喷发
    if (excite > 0.5 && eruptionHerald <= 0.5) spawnEruption(t, excite, true);  // 开口说话：大喷发
    eruptionHerald = excite;
    for (let i = ejecta.length - 1; i >= 0; i--) {
      const e = ejecta[i];
      if (t < e.born) continue;
      if (t - e.born > e.ttl) { ejecta.splice(i, 1); continue; }
      const pl = Math.hypot(e.p[0], e.p[1], e.p[2]) || 1;
      for (let k = 0; k < 3; k++) {
        e.v[k] -= (GRAVITY * e.p[k] / pl) * dt;    // 向心“磁重力”：减速、回落
        e.p[k] += e.v[k] * dt;
      }
      if (Math.hypot(e.p[0], e.p[1], e.p[2]) < 1.0) ejecta.splice(i, 1);  // 落回球面熄灭
    }
    if (ejecta.length > 220) ejecta.splice(0, ejecta.length - 220);
    for (let i = loops.length - 1; i >= 0; i--) if (t > loops[i].die) loops.splice(i, 1);
    if (storms.length && Math.random() < 0.25 * (1 + excite) * dt) {
      const src = surfacePointInStorm();
      if (src) {
        const b = src.d.slice();
        rotateDir(b, randomUnit(), 0.15 + Math.random() * 0.25);
        loops.push({ a: src.d.slice(), b, h: 0.35 + Math.random() * 0.45,
                     born: t, die: t + 3 + Math.random() * 3, k: src.s.k });
      }
    }
    if (flash && t - flash.born > 0.5) flash = null;
  }

  function drawEjecta(t) {
    const R = rot.R;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";      // 等离子体加色发光
    for (const lp of loops) {                      // 磁环：对象空间二次贝塞尔，与球同转
      const age = Math.min(1, Math.max(0, (t - lp.born) / (lp.die - lp.born)));
      const fade = Math.sin(Math.PI * age);
      if (fade <= 0.01) continue;
      const mid = [(lp.a[0] + lp.b[0]) / 2, (lp.a[1] + lp.b[1]) / 2, (lp.a[2] + lp.b[2]) / 2];
      const ml = Math.hypot(mid[0], mid[1], mid[2]) || 1;
      const apex = [mid[0] / ml * (1 + lp.h), mid[1] / ml * (1 + lp.h), mid[2] / ml * (1 + lp.h)];
      const SEG = 16;
      let started = false;
      ctx.beginPath();
      for (let sgi = 0; sgi <= SEG; sgi++) {
        const u = sgi / SEG, iu = 1 - u;
        const bx = iu * iu * lp.a[0] + 2 * iu * u * apex[0] + u * u * lp.b[0];
        const by = iu * iu * lp.a[1] + 2 * iu * u * apex[1] + u * u * lp.b[1];
        const bz = iu * iu * lp.a[2] + 2 * iu * u * apex[2] + u * u * lp.b[2];
        const [wx, wy, wz] = sphereToWorld(bx, by, bz, R);
        const [ax, ay] = project(wx, wy, wz);
        if (!started) { ctx.moveTo(ax, ay); started = true; } else ctx.lineTo(ax, ay);
      }
      ctx.strokeStyle = `rgba(156,196,244,${(0.50 * fade * Math.min(1, lp.k * 1.6)).toFixed(3)})`;
      ctx.lineWidth = 2.0;
      ctx.stroke();
    }
    const segs = new Map();                        // 按 快/慢×亮度档 分桶批绘；内联变换避免每粒子分配临时数组
    const FOVK = H * FOV, CX = W / 2, CY = H * 0.47;
    for (const e of ejecta) {                      // 喷发粒子：速度拖尾，快者更亮更白
      if (t < e.born) continue;
      const age = t - e.born;
      const sp = Math.hypot(e.v[0], e.v[1], e.v[2]);
      const fade = Math.max(0, 1 - age / e.ttl);
      const a = Math.min(0.95, e.bright * fade * Math.min(1, 0.45 + sp) * 1.0);
      if (a < 0.02) continue;
      const tail = 0.06 + sp * 0.05;
      let ax = 0, ay = 0, bx2 = 0, by2 = 0;
      for (let k = 0; k < 2; k++) {                // k=0 头部，k=1 尾部
        const px0 = k ? e.p[0] - e.v[0] * tail : e.p[0];
        const py0 = k ? e.p[1] - e.v[1] * tail : e.p[1];
        const pz0 = k ? e.p[2] - e.v[2] * tail : e.p[2];
        const x2 = px0 * rot.cy + pz0 * rot.sy, z2 = pz0 * rot.cy - px0 * rot.sy;
        const y2 = py0 * rot.cx - z2 * rot.sx, z3 = py0 * rot.sx + z2 * rot.cx;
        const wx = SPHERE.x + x2 * R, wy = SPHERE.y + y2 * R, wz = SPHERE.z + z3 * R;
        const f = FOVK / (wz + CAM_DIST);
        if (!k) { ax = CX + wx * f; ay = CY + wy * f; }
        else { bx2 = CX + wx * f; by2 = CY + wy * f; }
      }
      const bucket = (sp > 0.62 ? "F" : "S") + Math.min(4, Math.max(1, Math.round(a * 4)));
      if (!segs.has(bucket)) segs.set(bucket, []);
      segs.get(bucket).push(ax, ay, bx2, by2);
    }
    for (const [bucket, list] of segs) {
      const a = Number(bucket.slice(1)) / 4;
      ctx.strokeStyle = bucket[0] === "F" ? `rgba(214,231,255,${a.toFixed(3)})`
                                          : `rgba(156,196,244,${(a * 0.85).toFixed(3)})`;
      ctx.lineWidth = bucket[0] === "F" ? 2.2 : 1.4;
      ctx.beginPath();
      for (let k = 0; k < list.length; k += 4) {
        ctx.moveTo(list[k + 2], list[k + 3]);
        ctx.lineTo(list[k], list[k + 1]);
      }
      ctx.stroke();
    }
    if (flash) {                                   // 源区闪光
      const a = flash.strength * Math.max(0, 1 - (t - flash.born) / 0.6) * 0.8;
      if (a > 0.01) {
        const [wx, wy, wz] = sphereToWorld(flash.dir[0] * 1.02, flash.dir[1] * 1.02, flash.dir[2] * 1.02, R);
        const [cx2, cy2] = project(wx, wy, wz);
        const rr = H * 0.10;
        const gr = ctx.createRadialGradient(cx2, cy2, 0, cx2, cy2, rr);
        gr.addColorStop(0, `rgba(214,231,255,${a.toFixed(3)})`);
        gr.addColorStop(1, "rgba(214,231,255,0)");
        ctx.fillStyle = gr;
        ctx.fillRect(cx2 - rr, cy2 - rr, rr * 2, rr * 2);
      }
    }
    ctx.restore();
  }

  // ---------- 信号脉冲：沿随机边传播的光点 ----------
  const pulses = [];
  let nextPulse = 0;
  function drawPulses(t, excite) {
    if (t >= nextPulse) {
      const [i, j] = edges[(Math.random() * edges.length) | 0];
      pulses.push({ i, j, t0: t, dur: 0.9 + Math.random() * 0.8 });
      nextPulse = t + (excite > 0.3 ? 0.55 + Math.random() * 0.5 : 2.2 + Math.random() * 1.6);
    }
    for (let k = pulses.length - 1; k >= 0; k--) {
      const p = pulses[k];
      const u = (t - p.t0) / p.dur;
      if (u >= 1) { pulses.splice(k, 1); continue; }
      const a = pts[p.i], b = pts[p.j];
      const ease = u * u * (3 - 2 * u);
      const nz = nearness((a.z + b.z) / 2);
      for (let g = 0; g < 3; g++) {                             // 拖尾
        const gu = Math.max(0, ease - g * 0.05);
        const x = a.x + (b.x - a.x) * gu, y = a.y + (b.y - a.y) * gu;
        ctx.fillStyle = `rgba(207,226,255,${((1 - u) * (0.55 - g * 0.16) * (0.4 + 0.6 * nz)).toFixed(3)})`;
        ctx.beginPath(); ctx.arc(x, y, 2.2 - g * 0.5, 0, TAU); ctx.fill();
      }
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
    if (!isStatic) updateStorms(t, excite, dt);   // 静态帧冻结当前风暴形态
    spherePoints(t, excite);
    drawSphere(excite);
    if (!isStatic) { updateEjecta(t, dt, excite); drawEjecta(t); }
    if (!isStatic) drawPulses(t, excite);
  }

  // ---------- 尺寸 / 循环 / 门禁 ----------
  let raf = 0, last = 0, excite = 0, speaking = false;
  const themeOK = () => root.dataset.theme !== "legacy";  // 经典视图：整层隐藏，不画
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
  new MutationObserver(() => {  // 切到经典视图时停循环，切回房间视图再启动
    if (root.dataset.theme === "legacy") {
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
    } else kick();
  }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  const app = document.getElementById("app");
  if (app) new MutationObserver(kick).observe(app, { attributes: true, attributeFilter: ["class"] });
  document.addEventListener("visibilitychange", kick);
  speaking = chat.classList.contains("is-receiving");
  resize();
  kick();
})();
