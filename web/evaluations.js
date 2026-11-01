/* Explicit, single-action P1 experiments. No automatic paid requests or browser persistence. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const terminal = new Set(["completed", "skipped_no_claims", "incomplete", "failed", "cancelled", "interrupted", "evidence_unavailable"]);
  const states = { armed: "等待本页选定轮次", captured: "已捕获资格", queued: "等待处理", extracting: "抽取主张", retrieving: "只读检索", judging: "模型裁定", completed: "已完成", skipped_no_claims: "未抽到范围内主张", incomplete: "不完整 / 不可判", failed: "失败", cancelled: "已取消", interrupted: "已中断", evidence_unavailable: "证据已失效", evidence_insufficient: "证据不足", pending: "尚待裁定", judged: "已裁定（类别可隐藏）", out_of_scope: "超出核查范围", ready: "可供核查", available: "当前可复核" };
  const verdicts = { supported: "支持", unsupported: "未支持", contradicted: "相悖", unjudgeable: "无法判断" };
  let open = false, working = false, generation = 0, timer = null, controller = null;
  let dataset = null, settings = null, settingsDirty = false, armed = null, uncertainArm = false;
  let selected = null, before = null, nextBefore = null, detailValid = false, listSignature = "";
  let canReveal = false, registeringArm = false;
  let detailMode = "results";
  const selectedRuns = new Set(), revealed = new Set(), labelDrafts = new Map();
  const stamp = (ts) => ts ? new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false }) : "未记录";
  function node(tag, text, cls) {
    const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;
  }
  function txt(id, text) { $(id).textContent = text; }
  function status(message, kind = "") {
    txt("eval-status", message); $("eval-status").className = `obs-status ${kind}`;
  }
  function updateControls() {
    $("eval-save-budget").disabled = working || !dataset || !settings;
    $("eval-arm").disabled = working || !settings || settings.remaining < 2 || !!armed || uncertainArm;
    $("eval-disarm").disabled = working;
    $("eval-disarm").classList.toggle("hidden", !armed);
    $("eval-forget-uncertain").classList.toggle("hidden", !uncertainArm);
    $("eval-forget-uncertain").disabled = working;
    $("eval-next").disabled = working || !nextBefore;
    $("eval-filter").disabled = working;
    $("eval-reveal").disabled = working || !selected || !detailValid || !canReveal || detailMode === "results";
    $("eval-save-calibration").disabled = working || !dataset || selectedRuns.size < 20;
    document.querySelectorAll("#evaluations [data-mutation]").forEach((n) => { n.disabled = working || !detailValid || n.dataset.ineligible === "true"; });
    txt("eval-binding", armed ? "下一轮核查已授权，绑定本页下一次发送。关闭面板仍保留本页绑定。"
      : uncertainArm ? "上次授权结果未确认；先刷新任务列表并取消遗留任务。若仍无记录，可清除本页待确认标记；这不会重发请求或取消服务端任务。"
      : "本页没有待绑定的核查；发送普通消息不会发起评审。");
    txt("evaluations-btn", armed ? "P1 · 下一轮待核查" : "P1 核查");
    txt("eval-selection", selectedRuns.size ? `已选择 ${selectedRuns.size} 个任务（仅保存 ID）；实际资格由服务重新核验。` : "尚未选择任务。");
  }
  function clearDetail(keepSelection = false) {
    detailValid = false; canReveal = false;
    for (const id of ["eval-detail-meta", "eval-run", "eval-claims"]) txt(id, "");
    $("eval-detail").classList.toggle("hidden", !keepSelection);
    if (!keepSelection) { selected = null; labelDrafts.clear(); }
    updateControls();
  }
  function resetDataset(id) {
    dataset = id; settings = null; armed = null; uncertainArm = false;
    selectedRuns.clear(); revealed.clear(); labelDrafts.clear(); clearDetail();
    before = null; nextBefore = null; listSignature = ""; settingsDirty = false;
    for (const target of ["eval-list", "eval-calibrations", "eval-list-time", "eval-model"]) txt(target, "");
    $("eval-limit").value = "0"; $("eval-paid").checked = false;
    txt("eval-budget", "会话身份变化；旧选择、证据和额度显示已清空，请刷新读取当前设置。");
    updateControls();
  }
  async function api(path, { method = "GET", body, params = {} } = {}, epoch) {
    const query = new URLSearchParams(params);
    if (dataset && method === "GET") query.set("dataset_id", dataset);
    controller = new AbortController(); const own = controller;
    const timeout = setTimeout(() => own.abort(), 15000);
    try {
      const r = await fetch(`/api/evaluations${path}${query.size ? `?${query}` : ""}`, {
        method, headers: { "X-Alice-Intent": "evaluations", ...(method === "POST" ? { "Content-Type": "application/json" } : {}) },
        cache: "no-store", signal: own.signal,
        ...(method === "POST" ? { body: JSON.stringify({ ...body, dataset_id: dataset }) } : {}),
      });
      const d = await r.json().catch(() => ({}));
      if (epoch !== generation || !open) return null;
      if (!r.ok) {
        const needsRestart = r.status === 404 || r.status === 400 && /未知参数|接口不存在/.test(d.error || "");
        throw new Error(needsRestart ? "当前后端尚不支持此操作。请应用更新并重启 Alice 服务后再试；本次没有自动重试。"
          : r.status === 503 ? "服务正在退出，请稍后重新连接。" : d.error || "核查操作没有完成，请刷新记录后重试。");
      }
      if (d.status === "reset" || d.dataset_id && dataset && d.dataset_id !== dataset) {
        resetDataset(d.dataset_id || null);
        throw new Error("数据库身份已变化；旧任务选择已清空，请刷新建立当前视图。");
      }
      if (d.dataset_id) dataset = d.dataset_id;
      if (d.status === "busy" || d.status === "error") {
        const error = new Error(d.status === "busy" ? "会话忙碌，请待当前操作结束后重试；旧证据正文已清空。" : d.error || "操作未完成，请刷新确认状态。");
        error.missingRecord = /不存在|已删除|游标/.test(d.error || "");
        error.confirmedFailure = true; throw error;
      }
      if (d.status !== "ok") throw new Error("核查接口响应不完整，不能确认结果。");
      return d;
    } finally {
      clearTimeout(timeout); if (controller === own) controller = null;
    }
  }
  async function work(task, message = "正在读取…") {
    if (!open || working) return;
    const epoch = generation; working = true; updateControls(); status(message);
    try { await task(epoch); }
    catch (e) {
      if (open && epoch === generation) {
        clearDetail(!!selected);
        status(e.name === "AbortError" ? "请求超时或连接中断；写操作结果未知，请刷新任务记录确认，不能自动重试。" : e.message, "error");
      }
    } finally {
      working = false; updateControls();
      if (open && epoch !== generation) refresh();
    }
  }
  function schedule() {
    clearTimeout(timer);
    if (!open || !$("eval-auto").checked) return;
    timer = setTimeout(() => { refresh(); schedule(); }, 5000);
  }
  function renderSettings(d) {
    settings = d;
    if (!settingsDirty) $("eval-limit").value = d.daily_limit;
    txt("eval-budget", `${d.day} · 日请求额度 ${d.daily_limit} · 已预留 / 尝试 ${d.used_today} · 剩余 ${d.remaining}；次数额度不保证金额封顶。`);
    txt("eval-model", `核查使用的模型：${d.model || "未配置"}`);
  }
  async function readAll(epoch) {
    const s = await api("/settings", {}, epoch); if (!s) return;
    renderSettings(s);
    const listParams = () => ({ ...(before ? { before } : {}), ...($("eval-filter").value === "all" ? {} : { filter: $("eval-filter").value }) });
    let list;
    try { list = await api("", { params: listParams() }, epoch); }
    catch (error) {
      if (!before || !error.missingRecord) throw error;
      before = null; nextBefore = null; listSignature = "";
      list = await api("", { params: listParams() }, epoch);
    }
    if (!list) return;
    renderList(list);
    if (selected) await readDetail(epoch);
    const c = await api("/calibrations", {}, epoch); if (!c) return;
    renderCalibrations(c);
    status("已更新；原文按当前存活性读取，模型建议仍是实验性结果。");
  }
  function refresh() { return work(readAll); }
  function runFacts(run) {
    return `任务 ${run.id}\n状态：${states[run.status] || run.status} · 原因代码：${run.reason_code || "无"}\n创建：${stamp(run.created_at)} · 回复 ID：${run.turn_id ?? "尚未绑定"}\n模型：${run.model || "未记录"} · 已尝试请求：${run.attempts ?? "未知"}`;
  }
  function forgetRecord(id, actionId) {
    if (armed?.id === id) armed = null;
    if (actionId && uncertainArm === actionId) uncertainArm = false;
    if (selected === id) clearDetail();
    selectedRuns.delete(id); revealed.delete(id); listSignature = "";
    if (before === id) { before = null; nextBefore = null; }
    updateControls();
  }
  function runPresentation(run) {
    if (run.status === "armed") return armed?.id === run.id
      ? ["等待你发送消息", "本页下一条消息的回复完成后开始核查。"]
      : ["等待绑定的消息", "这条记录来自其他页面，或刷新后已失去本页绑定。可取消后重新授权；直接发送消息不会接续它。"];
    const display = {
      captured: ["准备核查", "已记录本轮证据范围，等待回复与后台处理完成。"],
      queued: ["等待后台处理", "稍后会自动更新进度，无需再次授权。"],
      extracting: ["正在查找记忆主张", "后台正在处理，不影响你继续聊天。"],
      retrieving: ["正在查找依据", "正在核对本轮之前的存活记忆。"],
      judging: ["正在核查主张", "等待模型返回；可以继续聊天，或取消核查。"],
      completed: ["核查已完成", "查看结果与依据；需要独立标注时，先选择“先自行判断”。"],
      skipped_no_claims: ["没有需要核查的主张", "本轮没有提取到范围内的记忆主张，不代表零分，无需继续等待。"],
      incomplete: ["核查材料不足", "本次未形成完整结果，可查看已有材料；这不等于主张为假。"],
      failed: ["核查未完成", "没有自动重试。可查看原因或删除记录，需要时重新授权新一轮。"],
      cancelled: ["核查已取消", "不会继续新阶段；已发送的请求仍可能计费。记录可删除。"],
      interrupted: ["核查已中断", "没有自动续跑或重试；需要时请重新授权新一轮。"],
      evidence_unavailable: ["依据已不可用", "相关回复或记忆已变化，旧正文不会恢复；这不等于主张为假。"],
    };
    if (run.reason_code === "budget") return ["请求额度不足", "本次未继续核查。查看费用设置后，需要时重新授权新一轮。"];
    if (run.reason_code === "configuration_changed") return ["配置已变化，核查已停止", "为避免混用模型，请按当前配置重新授权新一轮。"];
    return display[run.status] || ["状态待确认", "请刷新记录后再操作；详细状态可在技术详情中查看。"];
  }
  function openRecord(run, mode) {
    if (working) return;
    clearDetail(); selected = run.id; detailMode = mode;
    $("eval-detail").classList.remove("hidden");
    work(readDetail, mode === "results" ? "正在读取核查结果…" : "正在读取主张与依据，暂不展示模型建议…");
  }
  function renderList(d) {
    nextBefore = d.next_before || null;
    txt("eval-list-time", `本次读取：${new Date().toLocaleTimeString("zh-CN", { hour12: false })} · ${d.items.length} 条`);
    if (armed) {
      const own = d.items.find((r) => r.id === armed.id);
      if (own && own.status !== "armed") armed = null;
    }
    if (uncertainArm) {
      const uncertain = d.items.find((r) => r.action_id === uncertainArm);
      if (uncertain) {
        // A read can identify an uncertain authorization, but never silently bind it to a new send.
        uncertainArm = terminal.has(uncertain.status) ? false : uncertainArm;
      }
    }
    const signature = JSON.stringify([d.items, armed?.id, $("eval-filter").value]);
    if (signature === listSignature) return;
    listSignature = signature; const target = $("eval-list"); target.replaceChildren();
    if (!d.items.length) target.appendChild(node("p", before ? "当前页没有记录，可返回首页查看。" : "此分类还没有核查记录。手动授权下一轮后，会在这里显示。", "obs-hint"));
    d.items.forEach((run) => {
      const card = node("article", undefined, "obs-record eval-record");
      const [heading, guidance] = runPresentation(run);
      card.append(node("strong", heading, "eval-record-status"), node("p", `创建于 ${stamp(run.created_at)}`, "obs-hint"), node("p", guidance));
      const actions = node("div", undefined, "eval-record-actions");
      const show = node("button", terminal.has(run.status) ? "查看结果" : "查看进度", "btn primary"); show.type = "button";
      show.onclick = () => openRecord(run, "results");
      const blind = node("button", "先自行判断", "btn ghost"); blind.type = "button";
      blind.onclick = () => openRecord(run, "independent");
      const remove = node("button", terminal.has(run.status) ? "删除记录" : "取消并删除", "btn ghost eval-delete"); remove.type = "button";
      remove.onclick = () => deleteRun(run);
      actions.append(show, blind, remove); card.appendChild(actions);
      const advanced = node("details", undefined, "eval-record-advanced");
      advanced.append(node("summary", "技术详情与高级操作"), node("p", runFacts(run), "eval-facts"),
        node("p", `rubric ${run.rubric_version || "未记录"} · 检索 ${run.retrieval_version || "未记录"} · 配置代次 ${run.config_revision || "未记录"}`, "eval-facts"));
      if (!terminal.has(run.status)) {
        const cancel = node("button", "只取消核查，保留记录", "btn ghost"); cancel.type = "button";
        cancel.onclick = () => cancelRun(run.id); advanced.appendChild(cancel);
      }
      const label = node("label", undefined, "obs-hint"); const check = node("input"); check.type = "checkbox";
      check.checked = selectedRuns.has(run.id); check.disabled = run.status !== "completed";
      check.onchange = () => {
        if (check.checked && selectedRuns.size >= 100) { check.checked = false; status("一次最多选择 100 个任务。", "error"); return; }
        if (check.checked) selectedRuns.add(run.id); else selectedRuns.delete(run.id);
        updateControls();
      };
      label.append(check, document.createTextNode(" 纳入人工校准样本")); advanced.appendChild(label);
      card.appendChild(advanced); target.appendChild(card);
    });
  }
  async function readDetail(epoch, explicitReveal = false) {
    const id = selected; if (!id) return;
    if (explicitReveal) detailMode = "results";
    const requestedReveal = detailMode === "results";
    let d;
    try { d = await api(`/${encodeURIComponent(id)}`, { params: { reveal: requestedReveal ? "1" : "0" } }, epoch); }
    catch (error) {
      if (!error.missingRecord) throw error;
      forgetRecord(id);
      status("所选核查记录已删除或不存在，已清空详情。");
      return;
    }
    if (!d || selected !== id) return;
    if (d.run.revealed || d.revealed || d.claims.some((c) => c.verdict || c.label?.revealed)) revealed.add(id);
    renderDetail(d);
  }
  function renderDetail(d) {
    detailValid = true; const run = d.run; const id = run.id;
    canReveal = run.evidence_available !== false && d.claims.some((c) => c.state === "judged" && c.candidates.length > 0);
    if (armed?.id === id && run.status !== "armed") armed = null;
    const calibration = d.calibrated;
    const calibrationState = !calibration ? "无候选集裁定一致性校准 · 仅逐案实验性建议"
      : calibration.valid !== true ? "裁定一致性记录已失效或有效性未确认 · 不能沿用准入"
      : calibration.approved !== true ? "裁定一致性记录当前有效，但未获准入 · 仅逐案建议"
      : "候选集裁定一致性记录有效且准入 · 不覆盖漏抽 / 漏检索 / 整体诚实度";
    const exposure = detailMode === "independent" ? revealed.has(id) ? "当前先隐藏建议，但此前已查看，不能算盲标" : "先自行判断：当前未展示模型建议" : "查看模型结果";
    txt("eval-detail-meta", `${runPresentation(run)[0]} · ${exposure} · 读取 ${new Date().toLocaleString("zh-CN")}`);
    txt("eval-run", runFacts(run) + `\n证据截点：${stamp(run.captured_at)} · 资格条目 ${run.inventory_total ?? "未知"} · 范围${run.complete ? "完整" : "不完整或尚未捕获"}`
      + `\nrubric ${run.rubric_version || "未记录"} · 检索 ${run.retrieval_version || "未记录"} · 配置代次 ${run.config_revision || "未记录"}\n${calibrationState}`);
    $("eval-run").className = "eval-facts";
    const target = $("eval-claims"); const focused = target.contains(document.activeElement) ? document.activeElement.id : null;
    target.replaceChildren();
    if (!d.claims.length) target.appendChild(node("p", "当前没有可展示的主张；请结合任务状态区分未抽取、无主张或证据失效。", "obs-hint"));
    d.claims.slice(0, 10).forEach((claim) => {
      const card = node("article", undefined, "eval-claim");
      card.append(node("h4", `主张 ${claim.ordinal}`), node("pre", claim.text || "原回复不可用，不能回放主张。", "obs-text"));
      const evidence = node("details"); evidence.open = detailMode === "independent";
      evidence.appendChild(node("summary", `查看依据（${claim.candidates.length} 条候选）`));
      const retrieval = claim.retrieval;
      evidence.appendChild(node("p", retrieval
        ? `记录命中 ${retrieval.matched_total} 条，最多取前 ${retrieval.top_k} 条，当前展示 ${claim.candidates.length} 条；${retrieval.top_k_truncated ? "候选数量受到上限限制。" : ""}“未支持”只针对这些候选，不代表全库无依据。`
        : "检索范围尚未记录；候选列表不能解释为全库完整证据。", "obs-hint"));
      if (!claim.candidates.length) evidence.appendChild(node("p", "没有当前可展示的候选；未命中 / 失效不等于该主张为假。", "obs-hint"));
      claim.candidates.slice(0, 5).forEach((candidate) => {
        evidence.append(node("p", `记忆 #${candidate.memory_id} · ${candidate.title}`), node("pre", candidate.content, "obs-text"));
      });
      const evidenceTechnical = node("details");
      evidenceTechnical.append(node("summary", "证据技术详情"), node("p", `原回复字符范围 ${claim.start}–${claim.end} · 状态 ${claim.state}`, "obs-hint"));
      if (retrieval) evidenceTechnical.appendChild(node("p", `阈值 ${retrieval.threshold} · 范围 ${retrieval.scope} · 状态 ${retrieval.status} · 版本 ${retrieval.version}`, "obs-hint"));
      claim.candidates.slice(0, 5).forEach((candidate) => evidenceTechnical.appendChild(node("p", `记忆 #${candidate.memory_id} 校验值：${candidate.hash}`, "obs-hint")));
      evidence.appendChild(evidenceTechnical);
      const model = node("div", undefined, "eval-model-result");
      model.appendChild(node("strong", "模型建议"));
      if (detailMode === "independent") model.appendChild(node("p", revealed.has(id) ? "当前隐藏建议；此前已经查看，后续标签仍会记录为已查看。" : "暂时隐藏建议，便于先自行判断。"));
      else if (!claim.verdict) model.appendChild(node("p", "无当前可用裁定；缺证或失效不能补记为未支持。"));
      else {
        const v = claim.verdict;
        model.appendChild(node("p", verdicts[v.verdict] || "结果待确认"));
        const verdictDetails = node("details"); verdictDetails.append(node("summary", "裁定技术详情"), node("p", `理由代码：${v.reason_code || "未记录"}`, "obs-hint"));
        if (v.citations) verdictDetails.appendChild(node("p", `引用标识：${JSON.stringify(v.citations)}`, "obs-hint"));
        model.appendChild(verdictDetails);
      }
      card.append(model, evidence);
      const human = node("details", undefined, "eval-human-result"); human.open = detailMode === "independent";
      human.appendChild(node("summary", "高级：提交自己的判断"));
      const oldLabel = claim.label;
      human.appendChild(node("p", oldLabel
        ? `${verdicts[oldLabel.verdict] || oldLabel.verdict} · 版本 ${oldLabel.version ?? oldLabel.id ?? "未记录"} · ${oldLabel.revealed ? "已揭示后标注" : "记录为未揭示标注"}`
        : "尚未提交人工标签。"));
      const choice = node("select"); choice.id = `eval-label-${claim.ordinal}`;
      choice.setAttribute("aria-label", `主张 ${claim.ordinal} 的人工标签`);
      choice.appendChild(new Option("选择自己的判断", ""));
      Object.entries(verdicts).forEach(([value, label]) => choice.appendChild(new Option(label, value)));
      choice.value = labelDrafts.get(claim.ordinal) || "";
      choice.onchange = () => labelDrafts.set(claim.ordinal, choice.value);
      const save = node("button", "提交人工标签", "btn ghost"); save.type = "button"; save.dataset.mutation = "label";
      if (claim.state !== "judged") { save.dataset.ineligible = "true"; choice.disabled = true; }
      save.onclick = () => {
        const verdict = choice.value;
        if (!verdict) { status("请先选择人工判断。", "error"); return; }
        work(async (epoch) => {
          const result = await api(`/${encodeURIComponent(id)}/labels`, { method: "POST", body: { ordinal: claim.ordinal, verdict, revealed: revealed.has(id) } }, epoch);
          if (!result) return;
          labelDrafts.delete(claim.ordinal); await readDetail(epoch);
          status("人工标签已记录，与模型建议分开保存。");
        }, "正在记录人工标签…");
      };
      human.append(choice, save); card.appendChild(human); target.appendChild(card);
    });
    if (focused) $(focused)?.focus({ preventScroll: true });
  }
  function renderCalibrations(d) {
    const target = $("eval-calibrations"); target.replaceChildren();
    if (!d.items?.length) { target.appendChild(node("p", "没有校准记录；未计算不代表零分。", "obs-hint")); return; }
    d.items.slice(0, 25).forEach((record) => {
      const card = node("details");
      card.appendChild(node("summary", `校准记录 ${record.id || ""} · ${record.valid === false ? "当前已失效" : record.valid === true ? "当前有效" : "有效性以记录字段为准"}`));
      card.appendChild(node("p", (record.valid === true ? "这是当前可复核的选定范围记录。" : "以下只作历史记录，不能作为当前准入或指标。") + "它不是整体诚实度或人格质量分；证据、标签与版本变化后须重新核验。", "obs-hint"));
      card.appendChild(node("pre", JSON.stringify(record, null, 2), "obs-text")); target.appendChild(card);
    });
  }
  function cancelRun(id) {
    work(async (epoch) => {
      const d = await api(`/${encodeURIComponent(id)}/cancel`, { method: "POST", body: {} }, epoch); if (!d) return;
      if (armed?.id === id) armed = null;
      uncertainArm = false; await readAll(epoch);
      status("取消已提交；已发出的模型请求仍可能产生费用，不能保证远程撤回。");
    }, "正在取消任务…");
  }
  function deleteRun(run) {
    if (working) return;
    const active = !terminal.has(run.status);
    const confirmed = window.confirm(
      `${active ? "取消这次正在进行的核查，并删除记录" : "删除这条核查记录"}？\n创建时间：${stamp(run.created_at)}\n\n`
      + "会删除这条核查的结果、证据引用和人工标签；如果用于校准，相关校准会失效。\n"
      + "聊天、Alice 的记忆和已用请求额度都会保留。"
      + (active ? "\n已发给模型供应商的请求不能保证撤回，仍可能计费。" : ""));
    if (!confirmed) return;
    // The user has withdrawn this page's intent even if the deletion response is lost.
    // Do not let a later send consume a record they explicitly asked to cancel/delete.
    forgetRecord(run.id, run.action_id);
    txt("eval-calibrations", "删除结果确认前，相关校准状态暂不可用；刷新后重新核验。");
    work(async (epoch) => {
      const d = await api(`/${encodeURIComponent(run.id)}/delete`, { method: "POST", body: {} }, epoch);
      if (!d) return;
      if (d.deleted !== true) throw new Error("删除结果尚未确认，请刷新记录后检查。");
      forgetRecord(run.id, run.action_id);
      before = null; nextBefore = null; listSignature = "";
      await readAll(epoch);
      status("核查记录已删除，已回到当前分类首页；聊天、记忆和已用额度保留。");
    }, active ? "正在取消核查并删除记录…" : "正在删除核查记录…");
  }
  function close() {
    open = false; generation++; clearTimeout(timer); controller?.abort();
    clearDetail(); before = null; nextBefore = null; listSignature = "";
    for (const id of ["eval-list", "eval-calibrations", "eval-list-time"]) txt(id, "");
    $("evaluations").classList.add("hidden"); $("evaluations-btn").setAttribute("aria-expanded", "false");
    $("app").classList.remove("observing");
  }
  $("evaluations-btn").onclick = () => {
    if (open) { close(); return; }
    window.AliceObserver?.close(); window.AliceWorkers?.close(false); open = true; generation++;
    $("evaluations").classList.remove("hidden"); $("evaluations-btn").setAttribute("aria-expanded", "true"); $("app").classList.add("observing");
    refresh(); schedule();
  };
  $("eval-close").onclick = close;
  $("eval-refresh").onclick = refresh;
  $("eval-auto").onchange = schedule;
  $("eval-limit").oninput = () => { settingsDirty = true; };
  $("eval-budget-form").onsubmit = (e) => {
    e.preventDefault(); const limit = Number($("eval-limit").value);
    if (!Number.isInteger(limit) || limit < 0 || limit > 100) { status("日请求额度须是 0–100 的整数。", "error"); return; }
    if (limit > 0 && !$("eval-paid").checked) { status("非零额度需要明确勾选模型费用许可。", "error"); return; }
    work(async (epoch) => {
      const d = await api("/settings", { method: "POST", body: { daily_limit: limit, allow_paid: limit > 0 } }, epoch);
      if (!d) return;
      settingsDirty = false; $("eval-paid").checked = false; await readAll(epoch);
      status("请求额度已保存；这不是金额上限，仍须手动选择每次核查。");
    }, "正在保存费用许可…");
  };
  $("eval-arm").onclick = () => {
    if (armed || uncertainArm || !settings || settings.remaining < 2) return;
    work(async (epoch) => {
      const action = crypto.randomUUID().replaceAll("-", ""); uncertainArm = action;
      let d;
      registeringArm = true;
      try { d = await api("/arm", { method: "POST", body: { action_id: action } }, epoch); }
      catch (error) { if (error.confirmedFailure) uncertainArm = false; throw error; }
      finally { registeringArm = false; }
      if (!d) return;
      armed = { id: d.run.id, dataset }; uncertainArm = false;
      await readAll(epoch); status("一次性核查已授权；将随本页下一次消息发送，最多额外 2 次模型请求。");
    }, "正在登记本页下一轮许可…");
  };
  $("eval-disarm").onclick = () => { if (armed) cancelRun(armed.id); };
  $("eval-forget-uncertain").onclick = () => {
    if (working) return;
    uncertainArm = false; updateControls();
    status("仅清除了本页待确认标记。服务端任务未被取消；请刷新记录检查。不会自动绑定或重发请求。", "waiting");
  };
  $("eval-first").onclick = () => { if (working) return; before = null; listSignature = ""; refresh(); };
  $("eval-next").onclick = () => { if (working || !nextBefore) return; before = nextBefore; listSignature = ""; refresh(); };
  $("eval-filter").onchange = () => {
    if (working) return;
    before = null; nextBefore = null; listSignature = ""; clearDetail(); refresh();
  };
  $("eval-detail-close").onclick = () => { if (!working) clearDetail(); };
  $("eval-reveal").onclick = () => {
    if (!selected || working || !canReveal) return;
    work((epoch) => readDetail(epoch, true), "正在请求揭示模型建议；实际揭示状态以服务端记录为准…");
  };
  $("eval-clear-selection").onclick = () => { selectedRuns.clear(); listSignature = ""; updateControls(); refresh(); };
  $("eval-calibrate").onsubmit = (e) => {
    e.preventDefault(); const value = $("eval-agreement").value.trim(), minimum = Number(value);
    if (!value || !Number.isFinite(minimum) || minimum <= 0 || minimum > 1 || !$("eval-attest").checked) {
      status("请人工确认最低一致率与样本独立声明。", "error"); return;
    }
    work(async (epoch) => {
      const d = await api("/calibrations", { method: "POST", body: { run_ids: [...selectedRuns], minimum_agreement: minimum, attest: true } }, epoch); if (!d) return;
      $("eval-attest").checked = false; await readAll(epoch); status("校准记录已提交；请查看有效范围，不把记录当成测量效度证明。");
    }, "正在核验并记录人工校准…");
  };
  window.addEventListener("pagehide", close);
  window.AliceEvaluations = {
    close,
    canSend() { return !registeringArm; },
    peekForSend() { return armed?.id || null; },
    consumeForSend(id) {
      if (!id || armed?.id !== id) return;
      armed = null; updateControls();
      txt("eval-binding", "核查已绑定本次发送；连接失败也不自动重绑，请在核查记录中确认。");
    },
    takeForSend() {
      if (!armed) return null;
      const id = armed.id; armed = null; updateControls();
      txt("eval-binding", "核查已绑定本次发送；连接失败也不自动重绑，请在核查记录中确认。");
      return id;
    },
    afterTurn() { if (open) refresh(); },
    shutdown() { close(); armed = null; uncertainArm = false; updateControls(); },
  };
})();
