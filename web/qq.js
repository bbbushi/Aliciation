/* Local GUI only. QR pixels and identity candidates are never cached in storage. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const endpoint = "/api/connectors/qq";
  const labels = {
    idle: "未连接", scanning: "等待扫码", awaiting_confirmation: "待你确认",
    pairing: "等待配对", configured: "已保存", starting: "正在连接",
    connected: "已连接", stopping: "正在暂停", stopped: "已暂停",
    error: "需要处理", unavailable: "暂不可用",
  };
  const actionButtons = {
    install: $("qq-install"), recheck: $("qq-recheck"),
    scan: $("qq-scan"), pair: $("qq-pair"), confirm: $("qq-confirm"),
    run: $("qq-start"), stop: $("qq-stop"), cancel: $("qq-cancel"),
  };
  let visible = false;
  let busy = false;
  let snapshot = null;
  let timer = null;
  let qrExpiryTimer = null;
  let reader = null;
  let generation = 0;
  let confirmationIdentity = "";

  function clearQR() {
    clearTimeout(qrExpiryTimer);
    const canvas = $("qq-qr");
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    canvas.width = canvas.height = 1;
    $("qq-qr-box").classList.add("hidden");
    $("qq-qr-expiry").textContent = "";
  }

  function clearPrivateDisplay() {
    clearQR();
    $("qq-pair-code").textContent = "";
    $("qq-owner").textContent = "";
    $("qq-pair-box").classList.add("hidden");
    $("qq-confirm-box").classList.add("hidden");
    $("qq-owner-confirmed").checked = false;
    confirmationIdentity = "";
  }

  function drawQR(rows, expiresAt) {
    clearQR();
    if (!Array.isArray(rows) || rows.length < 21 || rows.length > 177 ||
        rows.some(row => typeof row !== "string" || row.length !== rows.length || /[^01]/.test(row))) return;
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) return;
    const canvas = $("qq-qr");
    const scale = 4, quiet = 4;
    canvas.width = canvas.height = (rows.length + quiet * 2) * scale;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#000";
    rows.forEach((row, y) => [...row].forEach((value, x) => {
      if (value === "1") ctx.fillRect((x + quiet) * scale, (y + quiet) * scale, scale, scale);
    }));
    const seconds = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    $("qq-qr-expiry").textContent = `本次连接剩余 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
    $("qq-qr-box").classList.remove("hidden");
    qrExpiryTimer = setTimeout(clearQR, Math.max(0, expiresAt - Date.now()));
  }

  function setStatus(text, error = false) {
    $("qq-status").textContent = text;
    $("qq-status").classList.toggle("error", error);
  }

  function render() {
    if (!visible) return;
    const d = snapshot;
    Object.values(actionButtons).forEach(button => {
      button.classList.add("hidden");
      button.disabled = busy;
    });
    $("qq-refresh").disabled = busy;
    $("qq-setup").classList.add("hidden");
    $("qq-setup").setAttribute("aria-busy", "false");
    $("qq-setup-progress").classList.add("hidden");
    $("qq-node-download").classList.add("hidden");
    if (!d) {
      clearPrivateDisplay();
      return;
    }
    const state = d.state;
    const setup = d.setup && typeof d.setup === "object" ? d.setup : null;
    const needsSetup = ["unavailable", "idle", "stopped"].includes(state) && setup && setup.state !== "ready";
    const installing = needsSetup && setup.state === "installing";
    $("qq-badge").textContent = labels[state] || "状态待确认";
    $("qq-badge").className = "qq-badge" + (state === "connected" ? " connected" : ["error", "unavailable"].includes(state) ? " error" : "");
    setStatus(d.message || ({
      idle: "点击扫码连接 QQ；已经配置过的连接可以直接继续。",
      configured: "本人身份已保存。点击“开始 QQ 聊天”后，在 QQ 中私聊机器人即可。",
      connected: "QQ 已连接，现在可以在机器人私聊窗口与 Alice 说话。",
      stopped: "QQ 聊天已暂停，可以在这里继续连接。",
      starting: "正在连接 QQ，请稍候。",
      stopping: "正在暂停 QQ，当前已提交的对话可能还会继续完成。",
      scanning: "请用本人的手机 QQ 扫码，随后回到这里确认身份。",
      pairing: "请用本人的 QQ 发送下面的配对文字。",
      awaiting_confirmation: "请核对下面的身份，再确认保存。",
    }[state] || "暂时无法确认连接状态，请刷新。"), ["error", "unavailable"].includes(state));
    if (needsSetup) {
      $("qq-setup").classList.remove("hidden");
      $("qq-setup").setAttribute("aria-busy", String(!!installing));
      $("qq-setup-progress").classList.toggle("hidden", !installing);
      $("qq-badge").textContent = installing ? "正在准备" : setup.state === "failed" ? "准备未完成" : "需要准备";
      $("qq-badge").className = "qq-badge" + (setup.state === "failed" ? " error" : "");
      $("qq-setup-title").textContent = installing ? "正在准备 QQ 组件" : setup.state === "failed" ? "准备未完成，请按提示处理" : "首次使用，先准备 QQ 组件";
      if (typeof setup.message === "string" && setup.message) setStatus(setup.message, setup.state === "failed");
      $("qq-setup-hint").textContent = ({
        dependencies_missing: "首次使用需要联网下载组件，安装在运行 Alice 的这台电脑。完成后就可以继续扫码。",
        installing: "正在准备组件，请保持 Alice 运行。关闭这个面板后仍会继续，可稍后回来查看。",
        failed: setup.can_install === true ? "处理上方提示后，可重试安装。若已在电脑上补齐组件，点击“重新检测”。" : "处理上方提示后，点击“重新检测”。",
        node_missing: "请先在运行 Alice 的电脑安装 Node.js 22.19 或更新版本，再点击“重新检测”。若仍找不到，请重启 Alice 后再试。",
        node_old: "请在运行 Alice 的电脑将 Node.js 更新到 22.19 或更新版本，再点击“重新检测”。若仍提示版本较旧，请重启 Alice 后再试。",
        npm_missing: "现有 Node.js 缺少安装工具，请从官网下载完整版本并安装，再点击“重新检测”。若仍未识别，请重启 Alice 后再试。",
        component_missing: "请先更新或重新下载完整的 Alice 项目，再点击“重新检测”。",
      })[setup.state] || "请按上方提示完成准备，再重新检测。";
      $("qq-node-download").classList.toggle("hidden", !["node_missing", "node_old", "npm_missing"].includes(setup.state));
      actionButtons.install.textContent = setup.state === "failed" ? "重试安装" : "安装 QQ 组件";
    }
    $("qq-app-id").textContent = d.app_id || "—";
    const owner = typeof d.owner_openid === "string" ? d.owner_openid : "";
    const candidate = state === "awaiting_confirmation" && !!owner;
    const identity = candidate ? `${d.operation_id}:${d.app_id}:${owner}` : "";
    if (identity !== confirmationIdentity) $("qq-owner-confirmed").checked = false;
    confirmationIdentity = identity;
    $("qq-confirm-box").classList.toggle("hidden", !candidate);
    $("qq-owner").textContent = candidate ? `本次身份标识：…${owner.slice(-8)}（平台提供的标识，不是 QQ 号）` : "";
    const pairCode = state === "pairing" && typeof d.pair_code === "string" ? d.pair_code : "";
    $("qq-pair-box").classList.toggle("hidden", !pairCode);
    $("qq-pair-code").textContent = pairCode ? (pairCode.startsWith("/pair ") ? pairCode : `/pair ${pairCode}`) : "";
    if (state === "scanning") drawQR(d.qr_matrix, d.expires_at);
    else clearQR();
    let actions = [];
    if (["idle", "stopped"].includes(state)) actions = owner ? ["run"] : ["scan", "run"];
    if (state === "error") actions = ["scan", "run", "pair", "stop"];
    if (state === "configured") actions = owner ? ["run"] : ["pair"];
    if (state === "awaiting_confirmation") actions = [owner ? "confirm" : "pair", "cancel"];
    if (["scanning", "pairing"].includes(state)) actions = ["cancel"];
    if (["starting", "connected"].includes(state)) actions = ["stop"];
    if (d.error_code === "owner_pair_required" && state === "error") actions = ["pair", "stop"];
    if (state === "error" && d.error_code === "already_configured") actions = ["run", "pair", "stop"];
    if (state === "error" && d.error_code === "not_configured") actions = ["scan", "stop"];
    if (state === "error" && ["external_bridge_or_stale_lock", "busy", "stop_unconfirmed", "binding_conflict"].includes(d.error_code)) actions = ["stop"];
    if (state === "error" && d.error_code === "external_bridge_or_stale_lock") {
      setStatus(`${d.message} 本页只能停止自己启动的连接；终端启动的连接请在原终端停止。`, true);
    }
    if (state === "error" && d.error_code === "installation_busy") {
      actions = ["stop"];
      setStatus(`${d.message} 请先点击“停止本页连接”，再重新检测。`, true);
    }
    if (["idle", "stopped"].includes(state) && setup?.state === "ready" && setup.can_recheck === true) actions.push("recheck");
    if (needsSetup) {
      actions = [];
      if (!installing && setup.can_install === true) actions.push("install");
      if (!installing && setup.can_recheck === true) actions.push("recheck");
    }
    const validId = typeof d.operation_id === "string" && /^[0-9a-f]{32}$/.test(d.operation_id);
    actions.forEach(action => {
      actionButtons[action].classList.remove("hidden");
      actionButtons[action].disabled = busy || !validId || (action === "confirm" && !$("qq-owner-confirmed").checked);
    });
    actionButtons.run.textContent = state === "idle" || state === "error" ? "已有连接，继续聊天" : "开始 QQ 聊天";
    actionButtons.run.classList.toggle("primary", !["idle", "error"].includes(state));
    actionButtons.run.classList.toggle("ghost", ["idle", "error"].includes(state));
    actionButtons.stop.textContent = state === "error" ? "停止本页连接" : "暂停 QQ 聊天";
  }

  async function request(method, payload, signal) {
    const response = await fetch(endpoint, {
      method, cache: "no-store", credentials: "same-origin", signal,
      headers: { "X-Alice-Intent": "connectors", ...(method === "POST" ? { "Content-Type": "application/json" } : {}) },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("服务没有返回可用的连接状态，请刷新后重试。"); }
    if (!response.ok) {
      if (response.status === 404) throw new Error("当前服务尚未加载网页 QQ 入口，请重新启动 Alice 后再打开。");
      throw new Error(typeof data.error === "string" ? data.error : typeof data.message === "string" ? data.message : "操作未能确认，请刷新连接状态。");
    }
    if (!data || typeof data.state !== "string" || !Object.hasOwn(labels, data.state)) throw new Error("连接状态格式无效，请重新读取。");
    return data;
  }

  function schedule() {
    clearTimeout(timer);
    if (visible && !document.hidden) timer = setTimeout(refresh, 1500);
  }

  async function refresh() {
    clearTimeout(timer);
    if (!visible || document.hidden) return;
    if (busy || reader) { schedule(); return; }
    if (snapshot?.expires_at <= Date.now()) clearQR();
    const ticket = generation;
    const controller = new AbortController();
    reader = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const data = await request("GET", null, controller.signal);
      if (visible && ticket === generation) { snapshot = data; render(); }
    } catch (_) {
      if (visible && ticket === generation) {
        clearPrivateDisplay();
        snapshot = null;
        render();
        $("qq-badge").textContent = "状态待确认";
        $("qq-badge").className = "qq-badge error";
        setStatus(_.name === "AbortError" ? "读取连接状态超时，请确认 Alice 服务仍在运行。" : _.message, true);
      }
    } finally {
      clearTimeout(timeout);
      if (reader === controller) reader = null;
      schedule();
    }
  }

  async function act(action) {
    if (busy || !visible || !snapshot || actionButtons[action].disabled) return;
    const operationId = snapshot.operation_id;
    if (action === "confirm" && !$("qq-owner-confirmed").checked) return;
    busy = true;
    const ticket = ++generation;
    reader?.abort();
    reader = null;
    clearTimeout(timer);
    render();
    setStatus(action === "install" ? "正在开始安装，请稍候…" : action === "recheck" ? "正在检查这台电脑的准备情况…" : "正在提交操作，请稍候…");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const data = await request("POST", { action, operation_id: operationId }, controller.signal);
      if (visible && ticket === generation) { snapshot = data; render(); }
    } catch (error) {
      if (visible && ticket === generation) {
        // The action may already be accepted: only poll, never repeat it automatically.
        snapshot = null;
        clearPrivateDisplay();
        render();
        setStatus(error.name === "AbortError" ? "本次操作结果尚未确认，正在重新读取状态；请先不要重复操作。" : error.message, true);
      }
    } finally {
      clearTimeout(timeout);
      busy = false;
      if (visible) {
        if (snapshot) render();
        else $("qq-refresh").disabled = false;
        schedule();
      }
    }
  }

  Object.entries(actionButtons).forEach(([action, button]) => { button.onclick = () => act(action); });
  $("qq-owner-confirmed").onchange = render;
  $("qq-refresh").onclick = refresh;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      generation++;
      clearTimeout(timer);
      reader?.abort();
      reader = null;
      snapshot = null;
      clearPrivateDisplay();
    }
    else if (visible) refresh();
  });
  window.AliceQQ = {
    open() {
      visible = true;
      generation++;
      snapshot = null;
      clearPrivateDisplay();
      $("qq-badge").textContent = "正在读取";
      setStatus("正在读取 QQ 连接状态…");
      render();
      refresh();
    },
    close() {
      visible = false;
      generation++;
      clearTimeout(timer);
      reader?.abort();
      reader = null;
      snapshot = null;
      clearPrivateDisplay();
      $("qq-app-id").textContent = "—";
    },
  };
})();
