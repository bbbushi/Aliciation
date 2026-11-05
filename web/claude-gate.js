/* claude-agent 权限质询面板（分支限定 ytq）：轮询 /api/worker-permissions/list，
 * 渲染挂起质询卡（工具/输入摘要/模型自述），三按钮写三层答案。
 * 独立轻面板，不依赖 worker-tasks.js；模型自述标注"非宿主事实"。 */
(function () {
  "use strict";
  var POLL_MS = 3000;
  var box, list, note, timer, polling = false;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  function summary(input) {
    try {
      return JSON.stringify(input || {}, null, 2);
    } catch (e) { return "(无法展示)"; }
  }

  function answer(req, decision, card) {
    if (card.dataset.answering === "true") return;
    card.dataset.answering = "true";
    card.querySelectorAll("button").forEach(function (button) { button.disabled = true; });
    fetch("/api/worker-permissions/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Alice-Intent": "workers" },
      body: JSON.stringify({ id: req.id, decision: decision, task_id: req.task_id,
        attempt_id: req.attempt_id, epoch: req.epoch, policy_revision: req.policy_revision,
        worker_id: req.worker_id, expires_at: req.expires_at, action_digest: req.action_digest, resource_digest: req.resource_digest })
    }).then(function (r) {
      if (!r.ok) throw new Error('服务暂不可用，请同步后重试（' + r.status + '）');
      return r.json();
    }).then(function (res) {
      if (res && res.delivered) {
        card.remove();
        note.textContent = "已答复：" + decision;
        if (!list.children.length) box.hidden = true;
      } else {
        note.textContent = "答复失败：" + ((res && res.error) || "未知错误");
      }
    }).catch(function (e) {
      note.textContent = "答复未确认：" + e + "。请刷新状态后再答复；首答不会被覆盖。";
      card.dataset.answering = "false";
      card.querySelectorAll("button").forEach(function (button) { button.disabled = false; });
    });
  }

  function render(pending) {
    list.textContent = "";
    (pending || []).forEach(function (req) {
      var card = el("div", "claude-gate-card");
      var head = el("div", "claude-gate-head");
      head.appendChild(el("strong", null, "协作者质询（claude-agent）"));
      head.appendChild(el("span", "claude-gate-tool", req.tool + " · " + (req.age_seconds || 0) + "s 前"));
      card.appendChild(head);
      card.appendChild(el("div", "claude-gate-input", summary(req.input)));
      if (req.resolved_resource) card.appendChild(el("div", "claude-gate-input", "实际资源：" + req.resolved_resource));
      if (req.reason_hint) {
        var hint = el("div", "claude-gate-hint", "工人自述（非宿主事实）：" + req.reason_hint);
        card.appendChild(hint);
      }
      if (req.stale) card.appendChild(el("div", "claude-gate-hint", "⚠ 已超时（宿主将拒绝），仅存档展示"));
      var actions = el("div", "claude-gate-actions");
      [["允许一次", "allow_once"], ["期限内允许相同完整文件动作", "allow_task"], ["拒绝", "deny"]].forEach(function (pair) {
        var b = el("button", "claude-gate-btn claude-gate-" + pair[1], pair[0]);
        b.addEventListener("click", function () { answer(req, pair[1], card); });
        actions.appendChild(b);
      });
      card.appendChild(actions);
      list.appendChild(card);
    });
    box.hidden = !list.children.length;
  }

  function poll() {
    if (polling) return;
    polling = true;
    fetch("/api/worker-permissions/list", { headers: { "X-Alice-Intent": "workers" } })
      .then(function (r) {
      if (!r.ok) throw new Error('服务暂不可用，请同步后重试（' + r.status + '）');
      return r.json();
    })
      .then(function (res) {
        if (!res || !Array.isArray(res.pending)) throw new Error('权限列表格式无效');
        var messages = [];
        if (res.scan && res.scan.exhausted) messages.push('活动权限扫描达到上限，当前列表可能不完整（' + res.scan.reason + '）；请保留任务状态并核查控制目录，不要重复派工。');
        if (res.scan && res.scan.read_errors) messages.push('有 ' + res.scan.read_errors + ' 项活动权限记录读取失败；未将读取失败当作无待答请求。');
        if (res.answer_diagnostic && res.answer_diagnostic.count) {
          var answerReason = res.answer_diagnostic.last_code === 'first_answer_preserved' ? '后来的不同答复已被拒绝，原首答保持' : '部分答复无法写入，请核查任务控制目录';
          messages.push(answerReason + '（累计 ' + res.answer_diagnostic.count + ' 次）；此问题不会直接中止整个任务。');
        }
        note.textContent = messages.join(' ');
        render(res.pending);
        if (messages.length) box.hidden = false;
      })
      .catch(function () {
        note.textContent = '权限请求读取失败，正在重试。';
        box.hidden = false;
        list.querySelectorAll('button').forEach(function (button) { button.disabled = true; });
      }).finally(function () { polling = false; });
  }

  function init() {
    box = el("div", "claude-gate-panel");
    box.hidden = true;
    box.appendChild(el("div", "claude-gate-title", "权限质询（claude-agent · 本分支实验功能）"));
    note = el("div", "claude-gate-note", "");
    list = el("div", "claude-gate-list");
    box.appendChild(list);
    box.appendChild(note);
    document.body.appendChild(box);
    poll();
    timer = setInterval(poll, POLL_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
