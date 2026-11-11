/* 薄适配层（插件设计 §6）：只调用公开 HTTP 插件查询，不读内部文件、不聚合事件表。 */
"use strict";
(() => {
  let scope = null;

  async function get(path, params) {
    const url = new URLSearchParams(params || {});
    if (scope) url.set("scope", scope);
    const r = await fetch(`/api/plugins/context-inspector/${path}?${url}`, {
      headers: { "X-Alice-Intent": "observer" }, cache: "no-store",
    });
    const d = await r.json().catch(() => ({}));
    if (d.scope) {
      if (scope && d.scope !== scope) window.dispatchEvent(new Event("alice-observer-scope-changed"));
      scope = d.scope;
    }
    return d;
  }

  function resetScope() { scope = null; }

  window.ContextInspectorClient = { get, resetScope, getScope: () => scope };
})();
