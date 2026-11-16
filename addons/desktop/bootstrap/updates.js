"use strict";
const $=(id)=>document.getElementById(id);
let busy=false;
let timer=null;
const labels={unauthorized:"本次 GitHub 授权已清除。",checking:"正在检查发布源…",available:"有新版本可用。",current:"当前已是发布源中的最新版本。",downloading:"正在下载更新…",verifying:"正在核验签名与兼容性…",verified:"验签通过，可以确认安装。",preparing:"正在等待任务结束并创建恢复点…",installing:"恢复点已完成，正在安装新版…",restarting:"更新完成，正在重新启动…"};
function render(value){
  if(value.current) $("current").textContent=`当前版本 ${value.current} · 开发通道`;
  if(value.version){$("release").hidden=false;$("version").textContent=`新版 ${value.version}`;$("notes").textContent=value.notes||"此版本未提供更新说明。";}
  if(value.phase==="checking"||value.phase==="current"||!value.version)$("release").hidden=true;
  $("forget").hidden=!value.has_token;
  const text=value.error||labels[value.phase];
  if(text)$("message").textContent=text;
  $("message").classList.toggle("error",!!value.error);
  const working=!!value.busy;
  $("check").disabled=working||busy;
  $("help").disabled=$("forget").disabled=$("token").disabled=working||busy;
  $("download").hidden=!value.version||value.verified;
  $("install").hidden=!value.verified;
  $("download").disabled=$("install").disabled=working||busy;
  if(value.phase==="downloading"&&value.received!=null){
    $("message").textContent=value.total?`正在下载：${Math.floor(value.received/value.total*100)}%`:`已下载 ${(value.received/1048576).toFixed(1)} MB`;
  }
  if(working){clearTimeout(timer);timer=setTimeout(refresh,1200);}
}
async function invoke(action,confirmed=false,token=null){return window.__TAURI__.core.invoke("update_action",{action,confirmed,token});}
async function refresh(){try{render(await invoke("state"));}catch(error){$("message").textContent=String(error);}}
async function perform(action){
  if(busy)return;busy=true;document.querySelectorAll("main button").forEach(b=>b.disabled=true);
  try{await invoke(action,true,action==="check"?$("token").value:null);}catch(error){$("message").textContent=String(error);$("message").classList.add("error");}
  finally{$("token").value="";busy=false;await refresh();}
}
$("check").onclick=()=>perform("check");
$("download").onclick=()=>perform("download");
$("help").onclick=()=>perform("help");
$("forget").onclick=()=>perform("forget");
$("install").onclick=()=>{
  $("confirm").returnValue="cancel";
  $("confirm").addEventListener("close",()=>{if($("confirm").returnValue==="ok")perform("install");},{once:true});
  $("confirm").showModal();$("confirm").querySelector('[value="cancel"]').focus();
};
window.updateProgress=render;
refresh();
