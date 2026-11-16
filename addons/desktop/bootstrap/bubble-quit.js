"use strict";
(() => {
  const status = document.getElementById("status"), confirm = document.getElementById("quit-confirm"), cancel = document.getElementById("quit-cancel");
  const invoke = (action, payload = null) => window.__TAURI_INTERNALS__.invoke("bubble_action", {action, payload});
  let quitting = false;
  cancel.onclick = () => {if (!quitting) invoke("quit_cancel").catch((error) => {status.textContent = String(error);});};
  confirm.onclick = async () => {
    if (quitting) return;quitting = true;confirm.disabled = true;cancel.disabled = true;status.textContent = "正在保存并退出，请稍候…";
    try {await invoke("quit_confirm", {confirmed:true});}
    catch (error) {quitting = false;confirm.disabled = false;cancel.disabled = false;status.textContent = String(error);}
  };
  document.addEventListener("keydown", (event) => {if (event.key === "Escape" && !quitting) {event.preventDefault();cancel.click();}});
})();
