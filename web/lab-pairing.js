"use strict";
(() => {
  const form = document.getElementById("lab-pairing-form");
  if (!form) return;
  const revoke = document.getElementById("lab-revoke");
  const code = document.getElementById("lab-code");
  const label = document.getElementById("lab-code-label");
  const status = document.getElementById("lab-pairing-status");
  const submit = form.querySelector("button[type=submit]");
  const open = document.getElementById("lab-open");
  const openStatus = document.getElementById("lab-open-status");
  let pending = false;
  async function send(action) {
    if (pending) return;
    pending = true;submit.disabled = revoke.disabled = true;
    code.value = "";label.hidden = true;
    status.textContent = "正在处理…";
    try {
      const payload = action === "issue" ? {action,mode:form.elements.body.checked ? "body" : "metadata"} : {action};
      const response = await fetch("/api/lab/v1/grant", {method:"POST",
        headers:{"X-Alice-Intent":"lab-pairing","Content-Type":"application/json"},body:JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "未能确认授权");
      if (action === "issue") {
        code.value = `${result.port}:${result.token}`;label.hidden = false;code.select();
        status.textContent = `只读授权有效至 ${new Date(result.expires_at * 1000).toLocaleTimeString()}。`;
      } else status.textContent = "已撤销当前观察授权。";
    } catch (error) {status.textContent = error.message;}
    finally {pending = false;submit.disabled = revoke.disabled = false;}
  }
  form.onsubmit = (event) => {event.preventDefault();send("issue");};
  revoke.onclick = () => send("revoke");
  open.onclick = async () => {
    open.disabled = true;openStatus.textContent = "正在打开…";
    try {
      const response = await fetch("/api/lab/v1/open", {method:"POST",
        headers:{"X-Alice-Intent":"lab-pairing","Content-Type":"application/json"},body:"{}"});
      const result = await response.json();
      if (!response.ok || result.status !== "opened") throw new Error(result.error || "未能打开 Lab");
      openStatus.textContent = "已在系统浏览器打开。";
    } catch (error) {openStatus.textContent = error.message;}
    finally {open.disabled = false;}
  };
})();
