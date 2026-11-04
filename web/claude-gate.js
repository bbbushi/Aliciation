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
    fetch("/api/worker-permissions/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Alice-Intent": "workers" },
      body: JSON.stringify({ id: req.id, decision: decision, task_id: req.task_id,
        attempt_id: req.attempt_id, epoch: req.epoch, policy_revision: req.policy_revision })
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
    }).catch(function (e) { note.textContent = "答复失败：" + e; });
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
      if (req.reason_hint) {
        var hint = el("div", "claude-gate-hint", "工人自述（非宿主事实）：" + req.reason_hint);
        card.appendChild(hint);
      }
      if (req.stale) card.appendChild(el("div", "claude-gate-hint", "⚠ 已超时（宿主将拒绝），仅存档展示"));
      var actions = el("div", "claude-gate-actions");
      [["允许一次", "allow_once"], ["本任务内允许", "allow_task"], ["拒绝", "deny"]].forEach(function (pair) {
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
        note.textContent = '';
        render(res.pending);
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
