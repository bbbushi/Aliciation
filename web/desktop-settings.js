"use strict";
(() => {
  if (!window.__TAURI_INTERNALS__?.invoke) return;
  const invoke = (action) => window.__TAURI_INTERNALS__.invoke("bubble_action", {action, payload:null});
  const result = document.getElementById("desktop-bubble-result");
  let openingSettings = false;
  window.AliceDesktopOpenSettings = () => {
    if (!window.AliceUI?.openSettings || openingSettings) return;
    openingSettings = true;
    delete window.__aliceOpenSettings;
    try {window.AliceUI.openSettings();}
    catch {openingSettings = false;result.textContent = "暂时无法打开设置，请从主窗口重试。";return;}
    invoke("settings_opened").catch(() => {}).finally(() => {openingSettings = false;});
  };
  if (window.__aliceOpenSettings) window.AliceDesktopOpenSettings();
  invoke("status").then((state) => {
    if (state.supported) document.getElementById("desktop-bubble-settings").hidden = false;
    if (state.main_settings_pending) window.AliceDesktopOpenSettings();
  }).catch(() => {});
  document.getElementById("desktop-bubble-open").addEventListener("click", () => {
    invoke("settings").catch((error) => {result.textContent = String(error);});
  });
})();
