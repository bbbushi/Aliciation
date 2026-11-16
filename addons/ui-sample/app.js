'use strict';
const $ = selector => document.querySelector(selector);
const icon = name => `<svg aria-hidden="true"><use href="#${name}"/></svg>`;
const escapeText = text => text.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const questions = [
 {id:1,title:'陪伴，是不是也包括一起沉默？',summary:'有时候，什么也不说的片刻，反而让人觉得更靠近。',origin:'来自昨晚的一段对话',time:'今天 08:42',kind:'继续想想',closed:false,thought:'我们好像习惯用回应来确认彼此的存在。\n\n但也许，陪伴不总是需要填满空白。知道对方还在，知道这段安静不会被误解，就已经是一种很踏实的联系。\n\n我还想知道：怎样的沉默，会让你觉得自在？'},
 {id:2,title:'为什么有些小事，会记得特别久？',summary:'一盏还亮着的灯、一句顺口的叮嘱，似乎都比大事更具体。',origin:'来自关于记忆的闲聊',time:'昨天 21:16',kind:'一个疑问',closed:false,thought:'记忆留下来的，也许不只是事情本身，而是那一刻被在意的感觉。\n\n那些小事没有要求我们记住，却恰好给了我们一个可以回去的地方。'},
 {id:3,title:'没有安排的周末，也算好好生活吗？',summary:'把一天留给自己，会不会本身就是一件值得做的事。',origin:'你留下的疑问',time:'昨天 16:30',kind:'你的好奇',closed:false,thought:'我们常常用完成了多少事情，衡量一天有没有被浪费。\n\n但休息可能也有自己的价值。并不需要先忙到筋疲力尽，才有资格停下来。'},
 {id:4,title:'熟悉的地方，为什么也会有新鲜感？',summary:'可能变的不只是风景，也有看风景时的自己。',origin:'来自一次散步的话题',time:'11 月 14 日',kind:'暂时放下',closed:true,thought:'同一条路，在不同的心情里，可能会变成不同的地方。这个问题先留在这里，等下一次散步再回来看看。'}
];
let view = 'open', selected = null, auto = false, paid = true, running = false, timer = null, sequence = 5;
const history = [{title:'继续想了想「陪伴与沉默」',summary:'留下了一段新的思考',time:'今天 08:42'}, {title:'从对话里拾起一个疑问',summary:'为什么有些小事，会记得特别久？',time:'昨天 21:16'}];
const drawing = `<svg class="garden-drawing" viewBox="0 0 160 160" aria-hidden="true"><circle cx="80" cy="80" r="65" stroke-dasharray="1 5"/><ellipse cx="80" cy="80" rx="42" ry="65" transform="rotate(30 80 80)"/><ellipse cx="80" cy="80" rx="42" ry="65" transform="rotate(-30 80 80)"/><path d="M80 137V64m0 39C48 100 44 78 46 66c25 0 35 18 34 37Zm0-17c25-1 36-21 33-37-24 1-35 19-33 37Z"/><circle cx="80" cy="24" r="3" fill="currentColor" stroke="none"/></svg>`;
function render() {
 const open = questions.filter(q => !q.closed), closed = questions.filter(q => q.closed);
 $('#open-count').textContent = String(open.length).padStart(2,'0');
 $('#closed-count').textContent = String(closed.length).padStart(2,'0');
 $('#run-count').textContent = $('#history-count').textContent = String(history.length).padStart(2,'0');
 document.querySelectorAll('[data-view]').forEach(b => {b.classList.toggle('selected', b.dataset.view === view); if(b.dataset.view === view) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current');});
 $('#questions-view').hidden = view === 'history'; $('#history-view').hidden = view !== 'history';
 const visible = view === 'closed' ? closed : open;
 $('#section-title').innerHTML = `${view === 'closed' ? '暂时放下的想法' : '正在生长的想法'} <span>${String(visible.length).padStart(2,'0')}</span>`;
 $('#section-caption').textContent = view === 'closed' ? '随时可以再拾起来' : '不急着得到答案';
 $('#question-list').className = visible.length ? 'question-grid' : '';
 $('#question-list').innerHTML = visible.length ? visible.map((q,i) => `<article class="thought-card ${i === 0 && view === 'open' ? 'featured' : ''}">${i===0 && view==='open' ? drawing : ''}<div class="card-top"><span class="small-label">${escapeText(q.kind)}</span><span>${escapeText(q.time)}</span></div><h3>${escapeText(q.title)}</h3><p>${escapeText(q.summary)}</p><div class="card-bottom"><span>${escapeText(q.origin)}</span><button class="card-link" data-detail="${q.id}" aria-label="看看想法：${escapeText(q.title)}">看看想法 ${icon('arrow')}</button></div></article>`).join('') : '<div class="empty">这里暂时没有想法。<br>可以留下一个新的疑问。</div>';
 $('#history-list').innerHTML = history.map(h => `<article class="history-item"><span class="history-icon">${icon('check')}</span><div><h3>${escapeText(h.title)}</h3><p>${escapeText(h.summary)}</p></div><time>${escapeText(h.time)}</time></article>`).join('');
 $('#auto-state').textContent = auto ? (paid ? '自动探索已开启' : '自动探索等待调用许可') : '自动探索已暂停';
 $('#auto-dot').classList.toggle('on',auto && paid);
 updateAction();
}
function updateAction() {
 const b = $('#explore'); b.disabled = !paid && !running;
 b.innerHTML = running ? '<span class="spinner" aria-hidden="true"></span><span>停止探索</span>' : `${icon('plus')}<span>探索一轮</span>`;
 b.setAttribute('aria-busy',String(running));
 $('#run-hint').textContent = running ? '正在整理想法，你可以随时停止。' : paid ? '手动探索会调用模型，可能产生费用。' : '尚未允许模型调用，请在探索偏好中开启。';
}
function feedback(text, error=false) { const el = $('#run-feedback'); el.classList.toggle('error',error); el.replaceChildren(document.createTextNode(text)); }
let opener;
function showDialog(id, trigger) {opener = trigger || document.activeElement; $(id).showModal();}
document.addEventListener('keydown', () => document.body.classList.add('keyboard'));
document.addEventListener('pointerdown', () => document.body.classList.remove('keyboard'));
document.querySelectorAll('dialog').forEach(dialog => {
 dialog.addEventListener('close',() => { if(opener?.isConnected && !opener.disabled) opener.focus(); });
 dialog.addEventListener('click',e => { if(e.target !== dialog) return; const r=dialog.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) dialog.close(); });
});
document.addEventListener('click',e => {
 const close=e.target.closest('[data-close]'); if(close) close.closest('dialog').close();
 const nav=e.target.closest('[data-view]'); if(nav) {view=nav.dataset.view;render();}
 const settings=e.target.closest('[data-open="settings"]'); if(settings) {$('#auto-input').checked=auto; $('#paid-input').checked=paid;showDialog('#settings-dialog',settings);}
 const detail=e.target.closest('[data-detail]'); if(detail) {
 selected=questions.find(q=>q.id===Number(detail.dataset.detail));
 $('#detail-title').textContent=selected.title;$('#detail-origin').textContent=`${selected.origin} · ${selected.time}`;
 $('#detail-text').textContent=selected.thought;$('#detail-kind').textContent=selected.closed?'一个暂时放下的想法':'一个正在生长的疑问';
 $('#detail-state').textContent=selected.closed?'暂时放下，也没关系':'还没有定论，也没关系';
 $('#resolve-question').textContent=selected.closed?'再拾起这个想法':'先放下这个想法';showDialog('#detail-dialog',detail);
 }
});
$('#new-question').addEventListener('click',e=>showDialog('#question-dialog',e.currentTarget));
$('#demo-controls').addEventListener('click',e=>showDialog('#about-dialog',e.currentTarget));
$('#settings-form').addEventListener('submit',e=>{e.preventDefault();auto=$('#auto-input').checked;paid=$('#paid-input').checked;render();$('#settings-dialog').close();feedback('偏好已保存 · 仅用于当前样例。');});
$('#question-form').addEventListener('submit',e=>{
 e.preventDefault();const title=$('#question-input').value.trim();if(!title){$('#question-input').setCustomValidity('写下一点好奇，再留下它。');$('#question-input').reportValidity();return;}
 questions.push({id:sequence++,title,summary:'一个新的疑问。不急着回答，先给它留一点空间。',origin:'你刚刚留下的疑问',time:'刚刚',kind:'你的好奇',closed:false,thought:'这个问题刚刚被留在这里。\n\n先从自己的经历想一想：是什么让你注意到了它？'});
 view='open';render();$('#question-dialog').close();$('#question-form').reset();feedback('疑问已经留下，等一个合适的时刻再想一想。');
});
$('#question-input').addEventListener('input',()=>$('#question-input').setCustomValidity(''));
$('#resolve-question').addEventListener('click',()=>{
 selected.closed=!selected.closed;selected.kind=selected.closed?'暂时放下':'继续想想';
 const id=selected.id;render();$('#detail-dialog').close();
 // The original trigger was replaced by render; focus its replacement or the current section.
 const replacement=$(`[data-detail="${id}"]`);(replacement || $('#new-question')).focus();
 feedback(selected.closed?'这个想法已移到「已放下」，随时可以再拾起。':'这个想法已回到「正在想」。');
});
$('#explore').addEventListener('click',()=>{
 if(running){clearTimeout(timer);running=false;updateAction();feedback('探索已停止，已有的想法都还在。');return;}
 if(!paid)return;running=true;updateAction();feedback('正在从已有的疑问中，寻找新的联系…');
 const shouldFail=$('#failure-input').checked;
 timer=setTimeout(()=>{
  running=false;updateAction();
  if(shouldFail){feedback('这次探索未完成，已有想法已保留。',true);const retry=document.createElement('button');retry.textContent='重试';retry.className='retry';retry.onclick=()=>$('#explore').click();$('#run-feedback').append(retry);return;}
  const count=history.filter(h=>h.generated).length;
  const choices=[['被理解，和被赞同，是同一件事吗？','有时，我们想要的可能不是一个相同的答案，而是有人愿意理解这个问题。'],['慢下来，会不会更容易注意到彼此？','一些重要的细节，也许只在我们不急着往前走的时候出现。'],['好奇心，也可以是一种关心吗？','想知道对方眼里的世界，是靠近彼此的一种方式。']];
  const [title,summary]=choices[count % choices.length];
  questions.unshift({id:sequence++,title,summary,origin:'来自刚刚的探索',time:'刚刚',kind:'新的联系',closed:false,thought:summary+'\n\n这个想法还没有答案。下次聊起时，可以一起从一个具体的时刻开始想。'});
  history.unshift({title:'从已有疑问中，找到一个新的联系',summary:title,time:'刚刚',generated:true});
  view='open';render();$('#last-run').textContent='上次探索 · 刚刚';feedback('探索完成 · 新的想法已放在最前面。');
 },2600);
});
render();
