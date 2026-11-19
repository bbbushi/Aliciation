"use strict";
(() => {
  let scope = null;
  async function get(name, params = {}, signal) {
    const bridge = window.parent !== window ? window.parent.AliceLabContext : null;
    if (!bridge) throw new Error("请从已连接的 Alice Lab 打开观测器。");
    const data = await bridge.read(window, name, params, signal);
    if (data.scope && scope && data.scope !== scope) {
      window.dispatchEvent(new Event("alice-observer-scope-changed"));
    }
    if (data.scope) scope = data.scope;
    if (data.status !== "ok") throw new Error(data.error || (data.status === "busy" ? "Alice 忙碌，请稍后刷新。" : "此份观测记录暂不可读。"));
    return data;
  }
  window.ContextInspectorClient = {get, getScope: () => scope};
})();
