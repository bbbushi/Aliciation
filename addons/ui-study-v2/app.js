'use strict';

// A closed, in-memory interaction study. No requests or persistent user data.
const content = window.STUDY_CONTENT;
const thoughts = content.thoughts.map(item => ({...item, body:[...item.body], connections:item.connections.map(connection => ({...connection}))}));
const history = content.history.map(item => ({...item}));
const $ = selector => document.querySelector(selector);
const make = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const icons = {
  spark:'<svg aria-hidden="true"><use href="#spark"/></svg>',
  archive:'<svg aria-hidden="true"><use href="#archive"/></svg>',
  back:'<svg aria-hidden="true"><use href="#back"/></svg>'
};
const state = {filter:'open', search:'', selected:thoughts.find(item => item.status === 'open')?.id || null,
  automatic:false, permission:true, failNext:false, running:null, nextId:1,
  feedback:'', feedbackType:'', retry:false};
const sidebarFeedback = make('div','feedback sidebar-feedback');
sidebarFeedback.setAttribute('role','status');
sidebarFeedback.setAttribute('aria-live','polite');
$('.sidebar-footer').before(sidebarFeedback);
const mobile = matchMedia('(max-width: 760px)');
const selectedThought = () => thoughts.find(item => item.id === state.selected);
const filteredThoughts = () => thoughts.filter(item => item.status === state.filter &&
  `${item.title} ${item.summary} ${item.body.join(' ')}`.toLocaleLowerCase().includes(state.search));

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const feedbackSignatures = new WeakMap();
const feedbackAnimations = new WeakMap();
const motionEase = 'cubic-bezier(0.23, 1, 0.32, 1)';
function updateFeedback() {
  const areas = [$('#feedback'), sidebarFeedback];
  const focused = document.activeElement;
  const focusedArea = areas.find(element => element.contains(focused));
  const focusedAction = focused?.dataset.feedbackAction;
  const inList = mobile.matches && !document.body.classList.contains('mobile-detail');
  const visibleArea = inList ? sidebarFeedback : $('#feedback');
  const signature = JSON.stringify([state.feedback, state.feedbackType, state.retry,
    state.retry ? state.retryId : null, state.viewId]);
  for (const element of areas) {
    element.hidden = element !== visibleArea || !state.feedback;
    const changed = feedbackSignatures.get(element) !== signature;
    if (changed || element.hidden) {
      feedbackAnimations.get(element)?.cancel();
      feedbackAnimations.delete(element);
    }
    if (!changed) continue;
    feedbackSignatures.set(element,signature);
    element.replaceChildren(document.createTextNode(state.feedback));
    element.className = `feedback ${state.feedbackType}`;
    if (element === sidebarFeedback) element.classList.add('sidebar-feedback');
    if (state.retry) {
      const retry = make('button','text-button','重试');
      retry.type = 'button'; retry.dataset.retry=''; retry.dataset.feedbackAction='retry';
      element.append(document.createTextNode(' '),retry);
    }
    if(state.viewId) {
      const viewId=state.viewId;
      const viewButton=make('button','text-button','查看新增思路');
      viewButton.type='button';viewButton.dataset.feedbackAction='view';
      viewButton.addEventListener('click',()=>{
        const thought=thoughts.find(item=>item.id===viewId);if(!thought)return;
        state.filter=thought.status;state.search='';$('#search-input').value='';state.selected=thought.id;
        render();setMobileDetail(true);
      });
      element.append(document.createTextNode(' '),viewButton);
    }
    // Only a new message fades; repeated renders and visibility changes are instant.
    if(state.feedback && !element.hidden && !document.body.classList.contains('keyboard-input') && element.animate) {
      const animation=element.animate([{opacity:0},{opacity:1}],{duration:120,easing:motionEase});
      feedbackAnimations.set(element,animation);
      animation.finished.then(()=>{
        if(feedbackAnimations.get(element)===animation)feedbackAnimations.delete(element);
      }).catch(()=>{});
    }
  }
  if(focusedArea && (!focused.isConnected || focusedArea.hidden)) {
    const replacement=focusedAction && visibleArea.querySelector(`[data-feedback-action="${focusedAction}"]`);
    if(state.feedback) {
      visibleArea.tabIndex=-1;
      (replacement || visibleArea.querySelector('button') || visibleArea).focus({preventScroll:true});
    } else {
      ($('#explore-button').disabled ? $('#new-button') : $('#explore-button')).focus({preventScroll:true});
    }
  }
}
function feedback(text,type='',retry=false,viewId=null) {
  state.feedback=text; state.feedbackType=type; state.retry=retry; state.viewId=viewId; updateFeedback();
}
function setMobileDetail(value) {
  document.body.classList.toggle('mobile-detail',value);
  updateFeedback();
}
function renderList() {
  const open = thoughts.filter(item => item.status === 'open').length;
  $('#open-count').textContent = String(open);
  $('#archived-count').textContent = String(thoughts.length-open);
  $('#thought-count').textContent = String(thoughts.length);
  $('#open-filter').setAttribute('aria-pressed',String(state.filter === 'open'));
  $('#archived-filter').setAttribute('aria-pressed',String(state.filter === 'closed'));
  const list = $('#thought-list');
  list.replaceChildren();
  const visible = filteredThoughts();
  if (!visible.length) {
    list.append(make('div','empty-state',state.search ? '没有找到这个想法。试试另一个关键词。' : state.filter === 'closed' ? '还没有放下的想法。' : '这里暂时没有疑问，可以留一个新的。'));
  }
  visible.forEach(item => {
    const button = make('button',`thought-item${item.id === state.selected ? ' selected is-selected' : ''}`);
    button.type='button'; button.dataset.thought=item.id;
    button.setAttribute('aria-pressed',String(item.id === state.selected));
    if (item.id === state.selected) button.setAttribute('aria-current','true');
    const meta=make('span','thought-item-meta');
    meta.append(make('span','thought-item-category',item.status==='closed'?'已放下':'正在想'),make('span','thought-item-date',item.updated));
    button.append(meta,make('span','thought-item-title',item.title),make('span','thought-item-preview',item.summary));
    button.addEventListener('click',()=>{
      state.selected=item.id; renderList(); renderReading(); setMobileDetail(true);
      if (mobile.matches) {$('#back-button').focus();$('#reading-pane').scrollTop=0;}
      else $(`[data-thought="${item.id}"]`)?.focus();
    });
    list.append(button);
  });
}
function renderReading() {
  const item=selectedThought();
  $('#archive-button').disabled=!item;
  $('.thought-article .article-meta').hidden=!item;
  $('.source-details').hidden=!item;
  $('.connection-section').hidden=!item || !item.connections.length;
  $('.article-signature').hidden=!item;
  $('.reasoning-section').hidden=!item;
  $('#thought-title').textContent=item?.title || (state.search?'没有匹配的想法':'这里暂时安静着');
  $('#thought-summary').textContent=item?.summary || (state.search?'换一个关键词，或留下一个新的疑问。':'可以先留下一个疑问，等合适的时候再想一想。');
  $('#thought-body').replaceChildren(); $('#thought-connections').replaceChildren();
  if (item) {
    $('#thought-category').textContent=item.status==='closed'?'暂时放下':'持续思考';
    $('#thought-status').textContent=item.status==='closed'?'暂时放下':'还在想';
    $('#thought-updated').textContent=item.updated;
    $('#thought-source').textContent=item.source;
    const archive=$('#archive-button');
    archive.innerHTML=item.status==='closed'?icons.back:icons.archive;
    archive.setAttribute('aria-label',item.status==='closed'?'重新拾起当前想法':'放下当前想法');
    archive.title=item.status==='closed'?'重新拾起':'先放下';
    item.body.forEach(paragraph=>$('#thought-body').append(make('p','',paragraph)));
    item.connections.forEach(connection=>{
      const detail=make('details','connection-link');
      detail.append(make('summary','connection-label',connection.label),make('p','connection-text',connection.text));
      $('#thought-connections').append(detail);
    });
  } else $('#thought-category').textContent='尚未选择';
  $('.source-details').open=false;
  updateAction();
}
function updateAction() {
  const item=selectedThought(), operation=state.running;
  const button=$('#explore-button');
  button.setAttribute('aria-busy',String(!!operation));
  button.disabled=!operation && (!state.permission || !item || item.status==='closed');
  if (operation) {
    button.innerHTML='<span class="spinner" aria-hidden="true"></span><span>停止探索</span>';
    $('#explore-title').textContent='正在模拟探索';
    $('#explore-hint').textContent=`正在模拟探索「${operation.title}」，可以随时停止。`;
  } else {
    button.innerHTML=icons.spark+'<span>探索一轮</span>';
    $('#explore-title').textContent='沿着这个想法，再走一步';
    $('#explore-hint').textContent=!state.permission?'当前样例未允许模拟探索，请在探索偏好中开启。':!item?'先选择或留下一个疑问。':item.status==='closed'?'重新拾起这个想法后，可以继续探索。':'模拟一次探索，沿着当前想法继续。';
  }
  $('#auto-status').textContent=state.automatic ? (state.permission?'自动探索：演示开启':'自动探索：等待许可') : '自动探索：已暂停';
  $('.sidebar-footer .status-dot').classList.toggle('is-active',state.automatic && state.permission);
  updateFeedback();
}
function renderHistory() {
  $('#history-list').replaceChildren();
  if (!history.length) $('#history-list').append(make('p','empty-state','还没有探索记录。'));
  history.forEach(entry=>{
    const row=make('article','history-item');
    row.append(make('h3','',entry.title),make('p','',entry.summary),make('time','',entry.time));
    $('#history-list').append(row);
  });
}
function render() {renderList();renderReading();renderHistory();}
function changeFilter(value) {
  state.filter=value;
  const visible=filteredThoughts();
  if (!visible.some(item=>item.id===state.selected)) state.selected=visible[0]?.id || null;
  setMobileDetail(false);render();
}
$('#open-filter').addEventListener('click',()=>changeFilter('open'));
$('#archived-filter').addEventListener('click',()=>changeFilter('closed'));
$('#search-input').addEventListener('input',event=>{
  state.search=event.target.value.trim().toLocaleLowerCase();
  const visible=filteredThoughts();
  if(!visible.some(item=>item.id===state.selected)) state.selected=visible[0]?.id || null;
  renderList();renderReading();
});
$('#back-button').addEventListener('click',()=>{
  setMobileDetail(false);
  const item=$(`[data-thought="${state.selected}"]`);
  (item || $('#search-input')).focus();
});
$('#archive-button').addEventListener('click',()=>{
  const item=selectedThought(); if(!item)return;
  item.status=item.status==='closed'?'open':'closed';
  const closed=item.status==='closed', title=item.title;
  // Keep the item in view in its destination filter: the result is visible immediately.
  state.filter=item.status; state.search='';$('#search-input').value='';
  render();
  feedback(`「${title}」${closed?'已放到「已放下」，随时可以重新拾起。':'已回到「正在想」。'}`,'success');
});

const openers=new WeakMap();
const dialogMotions=new WeakMap();
function isKeyboard(event) {
  return event?.type==='click' ? event.detail===0 : document.body.classList.contains('keyboard-input');
}
function cancelDialogMotion(dialog) {
  dialogMotions.get(dialog)?.animation.cancel();
  dialogMotions.delete(dialog);
}
function animateDialog(dialog,phase,keyboard,from) {
  cancelDialogMotion(dialog);
  if(keyboard || !dialog.animate) {
    if(phase==='closing')dialog.close();
    return;
  }
  const reduced=reducedMotion.matches;
  const hidden={opacity:0,transform:reduced?'none':'translateY(7px) scale(.985)'};
  const visible={opacity:1,transform:'none'};
  const animation=dialog.animate([from || (phase==='opening'?hidden:visible),phase==='opening'?visible:hidden],{
    duration:reduced?120:phase==='opening'?220:160,easing:motionEase,fill:'both'
  });
  const motion={animation,phase};dialogMotions.set(dialog,motion);
  animation.finished.then(()=>{
    if(dialogMotions.get(dialog)!==motion)return;
    if(phase==='closing')dialog.close();
    else cancelDialogMotion(dialog);
  }).catch(()=>{});
}
function currentDialogFrame(dialog) {
  const style=getComputedStyle(dialog);
  return {opacity:style.opacity,transform:style.transform};
}
function openDialog(dialog,trigger,event) {
  const closing=dialogMotions.get(dialog)?.phase==='closing';
  if(dialog.open && !closing)return;
  const from=dialog.open?currentDialogFrame(dialog):null;
  openers.set(dialog,trigger || document.activeElement);
  if(!dialog.open)dialog.showModal();
  animateDialog(dialog,'opening',isKeyboard(event),from);
}
function closeDialog(dialog,event) {
  if(!dialog.open)return;
  const keyboard=event?.type==='cancel' || isKeyboard(event);
  if(dialogMotions.get(dialog)?.phase==='closing' && !keyboard)return;
  const from=currentDialogFrame(dialog);
  animateDialog(dialog,'closing',keyboard,from);
}
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',event=>closeDialog(dialog,event)));
  dialog.addEventListener('click',event=>{
    if(event.target!==dialog)return;
    const rect=dialog.getBoundingClientRect();
    if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom)closeDialog(dialog,event);
  });
  dialog.addEventListener('cancel',()=>cancelDialogMotion(dialog)); // Native Esc closes immediately.
  dialog.addEventListener('close',()=>{
    if(dialog.open)return; // A queued close event must not cancel an already reopened window.
    cancelDialogMotion(dialog);
    const opener=openers.get(dialog);
    if(opener?.isConnected && !opener.disabled)opener.focus({preventScroll:true});
  });
  dialog.addEventListener('submit',event=>{
    if(dialogMotions.get(dialog)?.phase==='closing') {
      event.preventDefault();event.stopImmediatePropagation();
    }
  },true);
}
reducedMotion.addEventListener('change',()=>{
  if(!reducedMotion.matches)return;
  for(const dialog of document.querySelectorAll('dialog[open]')) {
    const closing=dialogMotions.get(dialog)?.phase==='closing';
    cancelDialogMotion(dialog);
    if(closing)dialog.close();
  }
});
function showSettings(event) {
  $('#auto-input').checked=state.automatic;
  $('#permission-input').checked=state.permission;
  $('#failure-input').checked=state.failNext;
  openDialog($('#settings-dialog'),event.currentTarget,event);
}
$('#settings-button').addEventListener('click',showSettings);
$('#sidebar-settings').addEventListener('click',showSettings);
$('#settings-form').addEventListener('submit',event=>{
  event.preventDefault();
  state.automatic=$('#auto-input').checked;
  state.permission=$('#permission-input').checked;
  state.failNext=$('#failure-input').checked;
  updateAction();closeDialog($('#settings-dialog'),event);
  if(!state.running) feedback('探索偏好已保存，仅对当前样例生效。','success');
});
$('#new-button').addEventListener('click',event=>openDialog($('#new-dialog'),event.currentTarget,event));
$('#about-button').addEventListener('click',event=>openDialog($('#about-dialog'),event.currentTarget,event));
$('#history-button').addEventListener('click',event=>{renderHistory();openDialog($('#history-dialog'),event.currentTarget,event);});
$('#question-input').addEventListener('input',()=>$('#question-input').setCustomValidity(''));
$('#new-form').addEventListener('submit',event=>{
  event.preventDefault();const title=$('#question-input').value.trim();
  if(!title){$('#question-input').setCustomValidity('写下一点疑问，再留下它。');$('#question-input').reportValidity();return;}
  const thought={id:`new-${state.nextId++}`,title,summary:'一个刚刚留下的疑问，还没有定论。',source:'你在当前样例中留下的疑问。',updated:'刚刚',status:'open',body:['这个问题值得先放在这里。','可以从一个具体的经历开始，想想是什么让你注意到了它。'],connections:[]};
  thoughts.unshift(thought);state.selected=thought.id;state.filter='open';state.search='';$('#search-input').value='';
  render();closeDialog($('#new-dialog'),event);$('#new-form').reset();setMobileDetail(true);
  if(!state.running) feedback('疑问已经留下。','success');
});
function startExploration() {
  if(state.running)return;
  const thought=selectedThought();
  if(!state.permission || !thought || thought.status==='closed')return;
  const operation={id:thought.id,title:thought.title,fail:state.failNext};
  state.failNext=false;
  state.running=operation;updateAction();
  feedback('正在模拟探索，从已有思路里寻找下一步…','pending');
  operation.timer=setTimeout(()=>{
    if(state.running!==operation)return;
    state.running=null;
    const current=thoughts.find(item=>item.id===operation.id);
    if(operation.fail){
      history.unshift({title:'探索未完成',summary:`「${operation.title}」的已有思路已保留。`,time:'刚刚 · 示例'});
      renderHistory();updateAction();
      // Retry returns to the original item rather than unexpectedly exploring another selection.
      state.retryId=operation.id;
      feedback(`「${operation.title}」探索未完成：模拟连接中断。已有思路已保留。`,'error',true);
      return;
    }
    const thoughtNumber=history.filter(entry=>entry.generated).length+1;
    current.body.push(thoughtNumber % 2 ? '再往前想一步：也许需要先分清「我观察到了什么」和「我推测了什么」。把两者分开，才能给对方留下解释自己的空间。' : '这次想到的是：与其急着给出判断，不如从一个具体的时刻开始核对。一个小问题，往往比完整的解释更容易让彼此靠近。');
    current.updated='刚刚';
    history.unshift({title:'多想了一步',summary:`「${operation.title}」新增了一段思路。`,time:'刚刚 · 示例',generated:true});
    render();feedback(`模拟探索完成：「${operation.title}」新增了一段思路。`,'success',false,state.selected!==operation.id?operation.id:null);
  },2200);
}
$('#explore-button').addEventListener('click',()=>{
  if(state.running){
    const operation=state.running;clearTimeout(operation.timer);state.running=null;updateAction();
    history.unshift({title:'探索已停止',summary:`「${operation.title}」保留原有思路。`,time:'刚刚 · 示例'});renderHistory();
    feedback('探索已停止，已有思路没有改变。');
  }else startExploration();
});
// The original failed item remains the retry target even after the user browses elsewhere.
for(const area of [$('#feedback'),sidebarFeedback]) area.addEventListener('click',event=>{
  if(!event.target.closest('[data-retry]') || !state.retry || state.running)return;
  const retryThought=thoughts.find(item=>item.id===state.retryId);
  if(!state.permission){feedback('当前样例未允许模拟探索，请在探索偏好中开启。','error');return;}
  if(!retryThought){feedback('找不到原来的想法，请先选择一个疑问。','error');return;}
  if(retryThought.status==='closed'){feedback('这个想法已放下，重新拾起后可以继续探索。','error');return;}
  if(retryThought){
    state.filter='open';state.search='';$('#search-input').value='';state.selected=retryThought.id;render();setMobileDetail(true);startExploration();
  }
});
mobile.addEventListener('change',updateFeedback);
document.addEventListener('keydown',()=>document.body.classList.add('keyboard-input'));
document.addEventListener('pointerdown',()=>document.body.classList.remove('keyboard-input'));
render();
