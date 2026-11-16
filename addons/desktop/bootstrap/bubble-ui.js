"use strict";
(() => {
  const invoke = (action, payload = null) => window.__TAURI_INTERNALS__.invoke("bubble_action", {action, payload});
  const anchor = document.getElementById("anchor");
  const chatAction = document.getElementById("chat-action");
  const preview = document.getElementById("preview");
  const motion = preview && window.AliceDesktopSentenceMotion ? new window.AliceDesktopSentenceMotion() : null;
  const speech = preview && window.AliceDesktopSpeech ? new window.AliceDesktopSpeech(() => {
    const parts = speech.visible("preview"); preview.textContent = parts.at(-1) || "";
    motion?.enter(preview);
  }) : null;
  let previewKey = null;
  window.bubbleActionsLayout = (state) => {document.body.dataset.side = state.side;};
  window.bubbleState = (state) => {
    if (anchor) {
      document.getElementById("unread").hidden = !state.unread;
      anchor.setAttribute("aria-expanded", String(!!state.actions_open));
      const glyph = document.getElementById("avatar-glyph");
      if (glyph) glyph.textContent = ({letter:"A",orb:"●",moon:"☾",flower:"✿"})[state.avatar] || "A";
      anchor.dataset.accent = state.accent || "sage";
    } else if (preview) {
      if (speech) {
        const key = JSON.stringify(state.key);
        if (key !== previewKey) {speech.clear();motion?.clear();previewKey = key;preview.textContent = "";}
        if (!state.text) {speech.clear();motion?.clear();speech.setActive(false);preview.textContent = "";}
        else {speech.set("preview", state.text, {final:true});speech.setActive(true);}
      } else preview.textContent = state.text || "";
      document.body.classList.toggle("solid", !!state.solid);
    }
  };
  if (anchor) {
    let origin = null, dragged = false;
    anchor.addEventListener("pointerdown", (event) => {if (event.button === 0) {origin = [event.screenX, event.screenY]; dragged = false;}});
    anchor.addEventListener("pointermove", (event) => {
      if (!origin || !(event.buttons & 1)) return;
      if (Math.hypot(event.screenX - origin[0], event.screenY - origin[1]) > 5) {
        origin = null; dragged = true; invoke("drag").catch(() => {});
      }
    });
    anchor.addEventListener("click", () => {origin = null; if (!dragged) invoke("toggle_actions").then(window.bubbleState).catch(() => invoke("settings")); dragged = false;});
    anchor.addEventListener("contextmenu", (event) => {event.preventDefault(); invoke("settings").catch(() => {});});
    invoke("status").then(window.bubbleState).catch(() => {});
  }
  if (chatAction) {
    chatAction.addEventListener("click", () => invoke("open").catch(() => invoke("settings")));
    chatAction.addEventListener("contextmenu", (event) => {event.preventDefault();invoke("settings").catch(() => {});});
    document.body.addEventListener("pointerenter", () => invoke("actions_hover", {inside:true}).catch(() => {}));
    document.body.addEventListener("pointerleave", () => invoke("actions_hover", {inside:false}).catch(() => {}));
  }
  for (const [id, action] of [["settings-action","app_settings"],["appearance-action","appearance"],["quit-action","quit_prompt"]]) {
    document.getElementById(id)?.addEventListener("click", () => invoke(action).catch(() => invoke("settings")));
  }
  const form = document.getElementById("preferences");
  if (!form) return;
  const status = document.getElementById("status");
  const enabled = document.getElementById("enabled"), body = document.getElementById("show-body"), solid = document.getElementById("solid");
  const render = (state) => {
    enabled.checked = !!state.enabled; body.checked = !!state.show_body; solid.checked = !!state.solid;
    form.querySelectorAll("input,button").forEach((node) => {node.disabled = !state.supported;});
    status.textContent = !state.supported ? "当前仅支持 macOS 桌面气泡" : state.error || "设置仅保存在这台电脑，不包含聊天正文。";
  };
  invoke("status").then(render).catch((error) => {status.textContent = String(error);});
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); document.getElementById("save").disabled = true;
    try {render(await invoke("save", {enabled:enabled.checked, show_body:body.checked, solid:solid.checked})); status.textContent = "已保存。切换到其他软件即可使用；菜单栏可随时召回。";}
    catch (error) {status.textContent = String(error);}
    finally {document.getElementById("save").disabled = false;}
  });
  document.getElementById("reset").addEventListener("click", async () => {
    try {await invoke("reset_position"); status.textContent = "气泡位置已重置到屏幕边缘。";}
    catch (error) {status.textContent = String(error);}
  });
})();
