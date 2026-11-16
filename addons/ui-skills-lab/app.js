'use strict';
const groups=[
 {name:'动效制作',items:[['animate','新建动效'],['choreograph','多元素编排'],['gestures','手势与拖动'],['apple-design','流体与空间'],['emil-design-eng','组件打磨']]},
 {name:'性能与审计',items:[['animation-performance','动画与帧预算'],['find-animation-opportunities','寻找动效机会'],['improve-animations','审计与改进计划'],['remove-animations','做动效减法'],['review-animations','动效代码审阅']]},
 {name:'设计探索',items:[['animation-vocabulary','查找动效术语'],['prototype','多方向原型'],['break-ui','极端数据挑战'],['learn-taste','沉淀设计规则']]},
 {name:'平台与组件',items:[['animate-expo','Expo 原生动效'],['mobile-native','移动端原生感'],['write-swift','Swift 并发与状态'],['pick-ui-library','组件库选型'],['ask-sonner','Toast 生命周期']]}
];
const all=groups.flatMap(group=>group.items.map(([id,label])=>({id,label,group:group.name})));
const $=s=>document.querySelector(s), stage=$('#demo-stage'), registry=window.UI_LAB || {};
const reducedQuery=matchMedia('(prefers-reduced-motion: reduce)');
let selected='animate', cleanup=null;
const activeAnimations=new Set();
const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function reduced(){return reducedQuery.matches || $('#reduce-motion').checked;}
function animate(element,frames,options={}){
 if(!element)return null;
 if(reduced() || !element.animate){
  const final=Array.isArray(frames)?frames.at(-1):Object.fromEntries(Object.entries(frames).map(([key,value])=>[key,Array.isArray(value)?value.at(-1):value]));
  for(const [key,value] of Object.entries(final || {}))if(!['offset','easing','composite'].includes(key))element.style[key]=value;
  return null;
 }
 const animation=element.animate(frames,options);activeAnimations.add(animation);
 animation.finished.then(()=>activeAnimations.delete(animation)).catch(()=>activeAnimations.delete(animation));return animation;
}
function nav(){
 const search=$('#skill-search').value.trim().toLocaleLowerCase();
 let matches=0;
 $('#skill-nav').innerHTML=groups.map(group=>{
  const visible=group.items.filter(([id,label])=>`${id} ${label} ${registry[id]?.description || ''}`.toLocaleLowerCase().includes(search));matches+=visible.length;
  if(!visible.length)return '';
  return `<section class="nav-group"><h2 class="nav-group-heading">${group.name}<span>${visible.length}</span></h2>${visible.map(([id,label])=>`<button class="nav-skill ${id===selected?'active':''}" data-skill="${id}" ${id===selected?'aria-current="page"':''} title="${id}"><span class="nav-index">${String(all.findIndex(i=>i.id===id)+1).padStart(2,'0')}</span><span class="nav-label">${label}</span><span class="nav-indicator" aria-hidden="true">↗</span></button>`).join('')}</section>`;
 }).join('') || '<div class="nav-empty">没有匹配的技能，试试“手势”“toast”或“审计”。</div>';
 $('#skill-total').textContent=`${matches} / 19`;
}
function dispose(){
 try{cleanup?.();}catch(error){console.error('Demo cleanup:',error);}cleanup=null;
 for(const animation of activeAnimations)animation.cancel();activeAnimations.clear();stage.replaceChildren();
}
function show(id){
 if(!all.some(item=>item.id===id))id='animate';
 dispose();selected=id;const item=all.find(i=>i.id===id),demo=registry[id],index=all.indexOf(item);
 document.documentElement.dataset.reduced=String(reduced());
 $('#group-name').textContent=item.group;$('#skill-slug').textContent=id;
 $('#demo-title').textContent=demo?.title || item.label;
 $('#demo-description').textContent=demo?.description || '该演示模块尚未加载。';
 $('#demo-number').textContent=`${String(index+1).padStart(2,'0')} / 19`;
 $('#guide-number').textContent=String(index+1).padStart(2,'0');
 $('#demo-hint').textContent=demo?.hint || '请刷新页面重新加载模块。';
 $('#guide-purpose').textContent=demo?.description || '';
 const model=demo?.mode==='model';$('#mode-tag').textContent=model?'行为模型 / 成果示例':'可交互演示';$('#mode-tag').classList.toggle('model',model);
 $('#guide-scope').textContent=model?'当前为浏览器模型或固定样本成果。原生、框架和审计能力的运行边界见演示与代码。':'当前效果直接在浏览器运行。所有状态仅保留在本页，不访问 Alice 配置或记忆。';
 $('#skill-path').textContent=`${id}/SKILL.md`;
 $('#demo-code').textContent=demo?.code || '// 模块尚未加载';
 $('#previous-demo').disabled=index===0;$('#next-demo').disabled=index===all.length-1;
 nav();document.body.classList.remove('lab-menu-open');$('#menu-toggle').setAttribute('aria-expanded','false');
 try{
  if(!demo)throw Error(`缺少模块：${id}`);
  stage.innerHTML=demo.render();
  const result=demo.mount?.(stage,{escape,reduced,animate});if(typeof result==='function')cleanup=result;
 }catch(error){console.error('Demo mount:',error);stage.innerHTML=`<div class="demo-error">演示加载未完成：${escape(error.message)}</div>`;}
 const hash=`#${id}`;if(location.hash!==hash)history.replaceState(null,'',hash);
 document.title=`${item.label} · UI Skills Lab`;
}
$('#skill-nav').addEventListener('click',event=>{const button=event.target.closest('[data-skill]');if(button){show(button.dataset.skill);if(innerWidth<=760)$('#demo-title').scrollIntoView({block:'start'});}});
$('#skill-search').addEventListener('input',nav);
$('#restart-demo').addEventListener('click',()=>show(selected));
$('#previous-demo').addEventListener('click',()=>show(all[Math.max(0,all.findIndex(i=>i.id===selected)-1)].id));
$('#next-demo').addEventListener('click',()=>show(all[Math.min(all.length-1,all.findIndex(i=>i.id===selected)+1)].id));
$('#reduce-motion').addEventListener('change',()=>show(selected));
reducedQuery.addEventListener('change',()=>show(selected));
$('#menu-toggle').addEventListener('click',()=>{const expanded=document.body.classList.toggle('lab-menu-open');$('#menu-toggle').setAttribute('aria-expanded',String(expanded));});
window.addEventListener('hashchange',()=>show(location.hash.slice(1)));
window.addEventListener('pagehide',dispose);
window.addEventListener('pageshow',event=>{if(event.persisted)show(selected);});
document.addEventListener('keydown',event=>{
 document.body.classList.add('lab-keyboard');
 if(event.key==='/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest('input,textarea,select,[contenteditable=true]')){event.preventDefault();$('#skill-search').focus();if(innerWidth<=760){document.body.classList.add('lab-menu-open');$('#menu-toggle').setAttribute('aria-expanded','true');}}
 if(event.key==='Escape' && document.body.classList.contains('lab-menu-open')){document.body.classList.remove('lab-menu-open');$('#menu-toggle').setAttribute('aria-expanded','false');$('#menu-toggle').focus();}
});
document.addEventListener('pointerdown',()=>document.body.classList.remove('lab-keyboard'));
show(location.hash.slice(1) || 'animate');
