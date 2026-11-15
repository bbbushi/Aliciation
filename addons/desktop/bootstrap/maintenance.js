"use strict";
const $ = (id) => document.getElementById(id);
let snapshot = null;
let active = false;
let poll = null;

function message(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
function confirmAction(title, text) {
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = text;
  $("confirm").returnValue = "cancel";
  return new Promise((resolve) => {
    $("confirm").addEventListener("close", () => resolve($("confirm").returnValue === "ok"), { once: true });
    $("confirm").showModal();
    $("confirm").querySelector('[value="cancel"]').focus();
  });
}
function invoke(action, backupId = null, confirmed = false) {
  return window.__TAURI__.core.invoke("maintenance_action", { action, backupId, confirmed });
}
function watch() {
  clearTimeout(poll);
  poll = setTimeout(() => refresh(true), 1500);
}
function render(value) {
  snapshot = value;
  if (value.busy) {
    $("description").textContent = "正在等待当前任务结束，再准备本机数据。";
    document.querySelectorAll(".actions button").forEach((button) => { button.disabled = true; });
    watch();
    return;
  }
  const running = value.running === true;
  $("description").textContent = running ? "Alice 正在运行。更新前可以保存完整恢复点，再退出。"
    : value.phase === "prepared" ? "更新恢复点已保存。可以退出后安装新版，也可以继续启动当前版本。"
      : "Alice 已暂停。可核验恢复点、恢复数据，或继续启动当前版本。";
  $("pause").hidden = !running;
  $("resume").hidden = running || value.incomplete_restore;
  $("rollback").hidden = !value.incomplete_restore;
  $("prepare").disabled = !!value.incomplete_restore;
  $("pause").disabled = false;
  $("resume").disabled = false;
  $("rollback").disabled = false;
  $("version").textContent = `当前程序 ${value.release?.version || ""} · 仅本机手动更新`;
  $("count").textContent = value.backup_count ? `(${value.backup_count})` : "";
  $("list").replaceChildren();
  if (!(value.backups || []).length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "尚无已完成的恢复点。";
    $("list").append(empty);
  }
  for (const backup of value.backups || []) {
    const row = document.createElement("div"); row.className = "point";
    const date = document.createElement("p"); date.textContent = new Date(backup.created_at).toLocaleString();
    const meta = document.createElement("small");
    meta.textContent = `由程序 ${backup.release.version} 创建 · ${backup.file_count} 个文件 · ${(backup.bytes / 1048576).toFixed(1)} MB`;
    const actions = document.createElement("div"); actions.className = "point-actions";
    const verify = document.createElement("button"); verify.className = "secondary"; verify.textContent = "核验";
    const restore = document.createElement("button"); restore.className = "secondary"; restore.textContent = "恢复到这里…";
    verify.disabled = restore.disabled = running;
    verify.onclick = () => perform("verify", backup.backup_id);
    restore.onclick = async () => {
      if (await confirmAction("恢复此时间的数据？", `${date.textContent}\n恢复后将回到此恢复点。之后的对话不会合入恢复结果，当前数据会另行保留。确认后开始核验与恢复。`)) {
        perform("restore", backup.backup_id, true);
      }
    };
    actions.append(verify, restore); row.append(date, meta, actions); $("list").append(row);
  }
  if (!running && value.backups?.length) $("points").open = true;
  if (value.incomplete_restore) message("上次恢复尚未结束。请先撤销未完成的恢复，保留原数据后再选择恢复点。", true);
  else if (value.phase === "failed" && value.error) message(value.error, true);
}
async function refresh(quiet = false) {
  if (active) { if (quiet) watch(); return; }
  active = true;
  try { render(await invoke("status")); }
  catch (error) { message(String(error), true); if (quiet || !snapshot) watch(); }
  finally { active = false; }
}
async function perform(action, backupId = null, confirmed = false) {
  if (active) return;
  active = true;
  document.querySelectorAll("main button").forEach((button) => { button.disabled = true; });
  message(action === "restore" ? "正在核验并恢复，请等待完成…" : "正在处理，请稍候…");
  try {
    const value = await invoke(action, backupId, confirmed);
    if (value.busy) { render(value); }
    else if (value.status === "starting") { message("正在启动 Alice…"); watch(); }
    else {
      message(action === "verify" ? "恢复点摘要核验通过。" : action === "restore" ? "数据已恢复，原数据已另行保留。可以继续启动当前版本。"
        : action === "rollback" ? "未完成的恢复已撤销，可以继续使用原数据。" : "操作已完成。");
    }
  } catch (error) { message(String(error), true); }
  finally {
    active = false;
    $("refresh").disabled = false;
    await refresh();
  }
}
$("prepare").onclick = async () => {
  if (await confirmAction("备份并退出 Alice？", "等待当前任务结束，保存含本机凭据的完整恢复点，然后退出。核验失败会停留在恢复窗口；请先完成备份，再手动安装新版。")) {
    perform("prepare", null, true);
  }
};
$("pause").onclick = async () => {
  if (await confirmAction("暂停 Alice 并进入恢复模式？", "等待当前任务结束后关闭服务，不新增退出前模型调用。之后可以选择恢复点，或继续启动当前版本。")) perform("pause", null, true);
};
$("resume").onclick = async () => {
  if (await confirmAction("继续启动当前版本？", "程序会先检查数据格式，启动完成后恢复聊天。若新版无法启动，仍可从此窗口选择恢复点。")) perform("resume", null, true);
};
$("rollback").onclick = async () => {
  if (await confirmAction("撤销未完成的恢复？", "核对现场并将已移动的原数据放回；若现场有其他改动，会停止并保留所有文件。")) perform("rollback", null, true);
};
$("refresh").onclick = () => refresh();
window.updateMaintenanceState = (frame) => {
  if (frame.event === "backup_started") message("服务已停止，正在创建并核验恢复点…");
  if (frame.event === "backup_complete") message("恢复点已保存，正在退出 Alice…");
  if (frame.event === "backup_failed") message(frame.error, true);
  watch();
};
refresh();
