"use strict";
(() => {
  const form = document.getElementById("dress-form"), status = document.getElementById("status"), preview = document.getElementById("avatar-preview");
  const invoke = (action, payload = null) => window.__TAURI_INTERNALS__.invoke("bubble_action", {action, payload});
  const glyphs = {letter:"A", orb:"●", moon:"☾", flower:"✿"};
  function render() {const data = new FormData(form);preview.textContent = glyphs[data.get("avatar")] || "A";preview.dataset.accent = data.get("accent");}
  form.addEventListener("change", render);
  form.querySelectorAll("input,button").forEach((node) => {node.disabled = true;});
  invoke("status").then((state) => {
    form.elements.namedItem("avatar").value = state.avatar;
    form.elements.namedItem("accent").value = state.accent;
    form.querySelectorAll("input,button").forEach((node) => {node.disabled = !state.supported;});
    status.textContent = state.error || "选择后可预览，保存后生效。";render();
  }).catch((error) => {status.textContent = String(error);document.getElementById("dress-cancel").disabled = false;});
  form.addEventListener("submit", async (event) => {
    event.preventDefault();const data = new FormData(form);document.getElementById("save").disabled = true;
    try {await invoke("appearance_save", {avatar:data.get("avatar"),accent:data.get("accent")});status.textContent = "装扮已保存。关闭窗口后即可在桌面查看。";}
    catch (error) {status.textContent = String(error);}
    finally {document.getElementById("save").disabled = false;}
  });
  document.getElementById("dress-cancel").onclick = () => invoke("appearance_close").catch((error) => {status.textContent = String(error);});
  document.addEventListener("keydown", (event) => {if (event.key === "Escape") {event.preventDefault();document.getElementById("dress-cancel").click();}});
})();
