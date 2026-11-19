/* Claude has its own grant. Opening settings never starts a CLI task. */
(function () {
  "use strict";
  const modal = document.getElementById("settings-modal");
  const tab = document.createElement("button");
  tab.type = "button"; tab.dataset.settings = "claude";
  tab.setAttribute("aria-pressed", "false"); tab.setAttribute("aria-controls", "claude-settings"); tab.textContent = "Claude";
  modal.querySelector('[data-settings="workers"]').after(tab);
  const section = document.createElement("section");
  section.id = "claude-settings";
  section.className = "settings-section worker-config hidden";
  section.dataset.settingsSection = "claude";
  section.setAttribute("aria-labelledby", "claude-settings-title");
  section.innerHTML = `
    <div class="worker-settings-head settings-page-head"><h2 id="claude-settings-title" class="panel-title">Claude 授权</h2><button id="claude-settings-refresh" class="link" type="button">同步状态</button></div>
    <p id="claude-settings-status" class="worker-status" role="status" aria-live="polite">尚未读取授权状态</p>
    <p class="settings-hint settings-page-note">先安装并登录本机 Claude CLI，再保存派工授权。</p>
    <form id="claude-policy-form" class="settings-group" aria-labelledby="claude-policy-title">
      <div class="worker-config-heading">
        <h3 id="claude-policy-title">派工授权</h3>
        <label class="worker-grant-toggle"><input id="claude-enabled" type="checkbox" role="switch" disabled><span>允许 Alice 调用 Claude</span><span class="worker-grant-track" aria-hidden="true"></span></label>
      </div>
      <div class="worker-config-grid">
        <section class="worker-field-group" aria-labelledby="claude-scope-title">
          <h4 id="claude-scope-title">派工范围</h4>
          <div class="settings-field"><label for="claude-workspace">工作目录（绝对路径）</label><input id="claude-workspace" type="text" maxlength="1000" placeholder="例如 D:\\AliceTasks\\Claude" autocomplete="off" spellcheck="false" disabled></div>
          <div class="settings-field"><label for="claude-max-turns">单任务最多轮数</label><input id="claude-max-turns" type="number" min="1" max="100" step="1" value="12" disabled></div>
        </section>
        <section class="worker-field-group" aria-labelledby="claude-limits-title">
          <h4 id="claude-limits-title">费用阈值</h4>
          <div class="worker-policy-limits">
            <label for="claude-task-budget">单任务（美元）<input id="claude-task-budget" type="number" min="0" max="10000" step="0.01" value="0.50" disabled></label>
            <label for="claude-day-budget">每日合计（美元）<input id="claude-day-budget" type="number" min="0" max="10000" step="0.01" value="2.00" disabled></label>
          </div>
          <p class="worker-hint">阈值依据 Claude 自报费用判断。实际账单可能超过阈值，未知费用需人工核对。</p>
        </section>
      </div>
      <div class="worker-permission-boundary">
        <p id="claude-permission-note">权限质询和原生权限监督不能替代操作系统沙箱。</p>
        <label class="worker-ack"><input id="claude-native-ack" type="checkbox" aria-describedby="claude-permission-note" disabled><span>我了解权限边界，并授权 Claude 在以上范围执行任务。</span></label>
      </div>
      <p class="worker-hint">更改或关闭已有授权会停止 Claude 的旧任务并撤销其正文。保存前会请你确认。</p>
      <div class="worker-config-footer"><p id="claude-policy-result" class="worker-hint" role="status"></p><div class="settings-actions"><button id="claude-policy-save" class="btn primary" type="submit" disabled>保存授权</button></div></div>
    </form>
    <details class="settings-help"><summary>运行方式与任务记录</summary>
      <p class="worker-hint">Claude 在执行任务时启动本机 CLI，使用独立于 DSH 的授权。两者各可运行一个任务、排队四个，并可同时工作。参数一致时保留当前授权。</p>
      <p class="worker-hint">Claude 窗口只展示 Claude 会话。归档、恢复和删除仅整理 Alice 中的记录，工作目录文件保留。已有任务可引用到主聊天，同一任务暂不支持追加执行。</p>
    </details>`;
  modal.querySelector(".settings-body").append(section);
  const $ = (id) => document.getElementById(id);
  const form = $("claude-policy-form"), result = $("claude-policy-result");
  let snapshot = null, dirty = false, busy = false, visible = false, reading = false;
  let requestVersion = 0, stopped = false, mustSync = false;
  const controllers = new Set();
  function message(text, error = false) {
    result.textContent = text;
    result.style.color = error ? "var(--warning)" : "";
  }
  function controls() {
    for (const item of form.querySelectorAll("input,button")) item.disabled = busy || !snapshot || stopped;
    $("claude-policy-save").disabled ||= mustSync || reading;
    $("claude-settings-refresh").disabled = busy || reading || stopped;
  }
  function fill(data) {
    const policy = data.policy;
    $("claude-enabled").checked = policy.enabled === true;
    $("claude-native-ack").checked = policy.native_sandbox_ack === true;
    $("claude-workspace").value = policy.workspace_root || "";
    $("claude-max-turns").value = policy.max_turns;
    $("claude-task-budget").value = policy.budget_usd_per_task;
    $("claude-day-budget").value = policy.budget_usd_per_day;
  }
  function accept(data, overwrite = false) {
    if (!data?.policy || data.worker_id !== "claude-agent" || !data.session_epoch || !Number.isSafeInteger(data.revision)) {
      throw new Error("Claude 授权状态格式无效，请同步后再试。");
    }
    const epochChanged = snapshot && snapshot.session_epoch !== data.session_epoch;
    const revisionChanged = snapshot && snapshot.revision !== data.revision;
    snapshot = data;
    if (overwrite || epochChanged || !dirty) { fill(data); dirty = false; }
    if (epochChanged) message("Alice 会话已变化，已重新读取授权，请确认后再保存。", true);
    else if (revisionChanged && dirty && !overwrite) message("授权已在其他位置改变；你的输入已保留，请核对当前范围后再保存。", true);
    $("claude-settings-status").textContent = `${data.policy.enabled ? "授权已开启" : "授权未开启"} · 本版本已登记 ${Number(data.runs_used ?? data.worker?.runs_used ?? 0)} 次执行`;
    controls();
  }
  async function request(body) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    controllers.add(controller);
    try {
      const response = await fetch("/api/workers/claude/policy", {
        method: body ? "POST" : "GET", signal: controller.signal, cache: "no-store",
        headers: { "X-Alice-Intent": "workers", ...(body ? { "Content-Type": "application/json" } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.code === "host_busy" ? "Alice 正忙，Claude 授权状态尚未刷新。" : data.error || "Claude 授权暂不可用");
        error.code = data.code; throw error;
      }
      return data;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  async function refresh() {
    if (reading || busy || stopped || document.hidden) return;
    const version = ++requestVersion;
    reading = true; controls();
    try {
      const data = window.AliceWorkers?.readStatus
        ? await window.AliceWorkers.readStatus("claude-agent", { fresh: true }) : await request();
      if (version === requestVersion && !stopped) {
        const unchanged = snapshot && snapshot.session_epoch === data.session_epoch && snapshot.revision === data.revision;
        accept(data); mustSync = false;
        if (unchanged && !dirty) message("");
      }
    }
    catch (error) {
      if (version === requestVersion && !stopped) {
        mustSync = true;
        message(error.name === "AbortError" ? "读取超时，请重新同步。" : error.message, true);
        $("claude-settings-status").textContent = "状态待同步";
      }
    } finally { reading = false; controls(); }
  }
  form.addEventListener("input", (event) => {
    dirty = true; message("");
    if (!["claude-enabled", "claude-native-ack"].includes(event.target.id)) $("claude-native-ack").checked = false;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!snapshot || busy || reading || stopped || mustSync) return;
    const body = {
      session_epoch: snapshot.session_epoch, revision: snapshot.revision,
      request_id: crypto.randomUUID().replaceAll("-", ""),
      enabled: $("claude-enabled").checked, native_sandbox_ack: $("claude-native-ack").checked,
      workspace_root: $("claude-workspace").value.trim(), max_turns: Number($("claude-max-turns").value),
      budget_usd_per_task: Number($("claude-task-budget").value), budget_usd_per_day: Number($("claude-day-budget").value),
    };
    if (body.enabled && (!body.native_sandbox_ack || !body.workspace_root)) {
      message("请填写任务工作目录，并确认本次授权。", true); return;
    }
    const fields = ["enabled", "native_sandbox_ack", "workspace_root", "max_turns", "budget_usd_per_task", "budget_usd_per_day"];
    const changed = fields.some((field) => body[field] !== snapshot.policy[field]);
    if (changed && snapshot.policy.enabled && !confirm("修改 Claude 授权会停止旧任务并撤销其正文。继续保存？DSH 不受影响。")) return;
    let policyWriteToken;
    try { policyWriteToken = window.AliceWorkers?.beginPolicyWrite?.("claude-agent"); }
    catch (error) { message(error.message, true); return; }
    busy = true; ++requestVersion; controls(); message("正在保存…");
    try {
      const data = await request(body);
      if (stopped) return;
      accept(data, true);
      message(data.policy_unchanged ? "参数未变化，原授权继续有效。" : body.enabled ? "Claude 授权已保存。" : "Claude 授权已关闭。");
    } catch (error) {
      mustSync = true;
      if (!stopped) message(`${error.name === "AbortError" ? "保存结果未确认" : error.message}。请先同步状态，勿反复提交。`, true);
    } finally {
      busy = false;
      window.AliceWorkers?.endPolicyWrite?.("claude-agent", policyWriteToken);
      controls();
    }
  });
  $("claude-settings-refresh").onclick = refresh;
  window.AliceClaudeSettings = {
    open() { $("settings-btn").click(); window.AliceUI?.selectSettings("claude"); },
    setVisible(value) {
      visible = value === true;
      window.AliceWorkers?.setToolSettingsVisible?.("claude-agent", visible);
      if (visible) refresh();
    },
    refresh,
  };
  window.addEventListener("alice-worker-status", (event) => {
    if (!visible || stopped || reading || busy || event.detail?.workerId !== "claude-agent") return;
    if (event.detail.error) {
      mustSync = true; controls();
      $("claude-settings-status").textContent = "授权状态待同步";
      message(event.detail.error.message || String(event.detail.error), true);
      return;
    }
    try { accept(event.detail.data); }
    catch (error) { message(error.message, true); }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && visible) refresh(); });
  window.addEventListener("pagehide", () => {
    stopped = true; ++requestVersion;
    controllers.forEach((controller) => controller.abort()); controls();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    stopped = false; controls(); if (visible) refresh();
  });
})();
