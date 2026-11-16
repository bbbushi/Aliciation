/* Six isolated interaction studies. Native browser APIs, no runtime dependencies. */
(() => {
  'use strict';
  const ease = 'cubic-bezier(0.23, 1, 0.32, 1)';
  function scope(root, api) {
    const abort = new AbortController(), animations = new Set(), disposers = [];
    const on = (el, name, fn, options = {}) => el.addEventListener(name, fn, {...options, signal:abort.signal});
    const reduced = () => api.reduced();
    const play = (el, frames, options) => {
      const animation = api.animate(el, frames, options);
      if (animation) {
        animations.add(animation);
        animation.finished.then(() => { animations.delete(animation); animation.cancel(); }).catch(() => animations.delete(animation));
      }
      return animation;
    };
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    on(media, 'change', () => { if (reduced()) for (const animation of animations) animation.cancel(); });
    return {on, play, reduced, animations, disposers, $:s=>root.querySelector(s),
      stop(){abort.abort();for(const a of animations)a.cancel();animations.clear();for(const fn of disposers)fn();}};
  }
  const keyboard = event => event.type === 'keydown' || event.type === 'click' && event.detail === 0;
  const frame = element => {const css=getComputedStyle(element);return {opacity:css.opacity,transform:css.transform};};

  function mountAnimate(root, api) {
    const s=scope(root,api), trigger=s.$('[data-ma-trigger]'), panel=s.$('[data-ma-panel]'), status=s.$('[data-ma-status]');
    let open=false, animation=null;
    function toggle(next,event) {
      const from=frame(panel);animation?.cancel();open=next;
      const end={opacity:open?1:0,transform:open?'translateY(0) scale(1)':'translateY(-6px) scale(.96)'};
      panel.style.opacity=end.opacity;panel.style.transform=end.transform;
      panel.inert=!open;panel.setAttribute('aria-hidden',String(!open));trigger.setAttribute('aria-expanded',String(open));
      status.textContent=open?'已展开 · 再次点击可中断并收回':'已收回 · 键盘触发即时切换';
      if(!keyboard(event)) animation=s.play(panel,s.reduced()?[{opacity:from.opacity},{opacity:end.opacity}]:[from,end],{duration:open?200:160,easing:ease});
      if(!open && panel.contains(document.activeElement))trigger.focus();
    }
    s.on(trigger,'click',e=>toggle(!open,e));s.on(s.$('[data-ma-close]'),'click',e=>toggle(false,e));
    s.on(root,'keydown',e=>{if(e.key==='Escape'&&open){e.preventDefault();toggle(false,e);}});
    return ()=>s.stop();
  }

  function mountChoreograph(root,api) {
    const s=scope(root,api), parts=[...root.querySelectorAll('[data-mc-part]')];let active=[];
    function run(event) {
      const running=active.some(a=>a.playState==='running'), from=parts.map(frame);
      active.forEach(a=>a.cancel());active=[];
      parts.forEach((part,i)=>{
        if(keyboard(event))return;
        const start=running?from[i]:{opacity:0,transform:`translateY(${[8,5,3][i]}px)`};
        const reduced=s.reduced();
        const animation=s.play(part,reduced?[{opacity:0},{opacity:1}]:[start,{opacity:1,transform:'none'}],{duration:reduced?120:[200,190,180][i],delay:reduced?0:[0,55,90][i],easing:ease,fill:'backwards'});
        if(animation)active.push(animation);
      });
      s.$('[data-mc-status]').textContent=keyboard(event)?'键盘重播：所有内容即时呈现':s.reduced()?'减少动态效果：一起淡入，无错峰':'主信息先到；支持信息重叠跟进；容器与操作不动';
    }
    s.on(s.$('[data-mc-play]'),'click',run);
    return ()=>s.stop();
  }

  function mountPerformance(root,api) {
    const s=scope(root,api), range=s.$('[data-mp-range]'), width=s.$('[data-mp-width]'), scale=s.$('[data-mp-scale]');
    let active=[];
    function set(value,event) {
      const beforeWidth=width.getBoundingClientRect().width, beforeTransform=getComputedStyle(scale).transform;
      active.forEach(a=>a.cancel());active=[];
      width.style.width=`${value}%`;scale.style.transform=`scaleX(${value/100})`;
      range.value=String(value);s.$('[data-mp-value]').textContent=`${value}%`;
      if(!keyboard(event)&&!s.reduced()) {
        const a=s.play(width,[{width:`${beforeWidth}px`},{width:`${value}%`}],{duration:250,easing:ease});
        const b=s.play(scale,[{transform:beforeTransform},{transform:`scaleX(${value/100})`}],{duration:250,easing:ease});
        active=[a,b].filter(Boolean);
      }
    }
    s.on(range,'input',e=>set(Number(range.value),key?{type:'keydown'}:e));
    let key=false;s.on(range,'keydown',()=>key=true);s.on(range,'pointerdown',()=>key=false);
    // Input events do not retain their originating key; capture that source at the range.
    s.on(s.$('[data-mp-toggle]'),'click',e=>set(Number(range.value)>50?28:86,e));
    return ()=>s.stop();
  }

  // A small physical spring for the two custom draggable-object studies.
  // The pointer tracks directly. Only release uses integration, with velocity in px/s.
  function springObject(element,area,api,mode,status) {
    const abort=new AbortController();const on=(el,n,fn,o={})=>el.addEventListener(n,fn,{...o,signal:abort.signal});
    let x=0,y=0,vx=0,vy=0,tx=0,ty=0,raf=0,last=0,drag=null,dismissed=false;
    const limits=()=>({x:Math.max(0,area.clientWidth-element.offsetWidth-24),y:Math.max(0,area.clientHeight-element.offsetHeight-24)});
    const draw=()=>{element.style.transform=`translate(${x}px, ${y}px)`;};
    function stop(){cancelAnimationFrame(raf);raf=0;last=0;}
    function settle(targetX,targetY,velocityX=0,velocityY=0,instant=false){
      stop();tx=targetX;ty=targetY;vx=velocityX;vy=velocityY;
      if(api.reduced()||instant){x=tx;y=ty;draw();finish();return;}
      const damping=Math.abs(velocityX)+Math.abs(velocityY)>5?.8:1;
      const tick=time=>{
        if(api.reduced()){x=tx;y=ty;draw();raf=0;last=0;finish();return;}
        const dt=Math.min(.032,last?(time-last)/1000:.016);last=time;
        // Response .4s; damping ratio .8 only for momentum, otherwise critical damping.
        const w=2*Math.PI/.4,z=damping;
        const steps=Math.ceil(dt/.004),h=dt/steps;
        for(let i=0;i<steps;i++){vx+=(-w*w*(x-tx)-2*z*w*vx)*h;vy+=(-w*w*(y-ty)-2*z*w*vy)*h;x+=vx*h;y+=vy*h;}
        draw();
        if(Math.abs(x-tx)+Math.abs(y-ty)<.2&&Math.abs(vx)+Math.abs(vy)<2){x=tx;y=ty;draw();raf=0;last=0;finish();}
        else raf=requestAnimationFrame(tick);
      };
      raf=requestAnimationFrame(tick);
    }
    function finish(){if(dismissed){const hadFocus=document.activeElement===element;element.style.visibility='hidden';element.tabIndex=-1;status.textContent='卡片已移开 · 点击“复位”可恢复';if(hadFocus)area.parentElement.querySelector('[data-mg-reset]')?.focus();}}
    const rubber=(value,min,max)=>value<min?min+(value-min)*.2:value>max?max+(value-max)*.2:value;
    on(element,'pointerdown',e=>{
      if(e.button!==0||drag||dismissed)return;
      stop();element.setPointerCapture(e.pointerId);
      drag={id:e.pointerId,px:e.clientX,py:e.clientY,x,y,samples:[{x:e.clientX,y:e.clientY,t:performance.now()}]};
      element.style.cursor='grabbing';status.textContent='正在直接跟随指针';
    });
    on(element,'pointermove',e=>{
      if(!drag||drag.id!==e.pointerId)return;
      const b=limits();x=mode==='swipe'?rubber(drag.x+e.clientX-drag.px,-area.clientWidth,area.clientWidth):rubber(drag.x+e.clientX-drag.px,0,b.x);
      y=mode==='swipe'?0:rubber(drag.y+e.clientY-drag.py,0,b.y);draw();
      const now=performance.now();drag.samples.push({x:e.clientX,y:e.clientY,t:now});drag.samples=drag.samples.filter(p=>now-p.t<=100);e.preventDefault();
    });
    function release(cancelled=false){
      if(!drag)return;const data=drag;drag=null;element.style.cursor='grab';
      if(element.hasPointerCapture(data.id))element.releasePointerCapture(data.id);
      const samples=data.samples,a=samples[0],b=samples[samples.length-1];
      const fresh=performance.now()-b.t<100;
      const velocityX=fresh&&b.t>a.t?(b.x-a.x)/(b.t-a.t)*1000:0,velocityY=fresh&&b.t>a.t?(b.y-a.y)/(b.t-a.t)*1000:0;
      if(cancelled){dismissed=false;settle(data.x,data.y,0,0,true);status.textContent='拖动已取消，恢复抓取时的位置';return;}
      if(mode==='swipe'){
        dismissed=Math.abs(x)>area.clientWidth*.4||Math.abs(velocityX)>500;
        const direction=Math.abs(velocityX)>500?Math.sign(velocityX):Math.sign(x)||1;
        settle(dismissed?direction*(area.clientWidth+element.offsetWidth):0,0,velocityX,0);
        status.textContent=dismissed?'正在沿手势方向移开':'未超过距离或速度阈值，回到原位';
      }else{
        const bounds=limits(), project=v=>(v/1000)*.998/(1-.998);
        const px=x+project(velocityX),py=y+project(velocityY);
        settle(px>bounds.x/2?bounds.x:0,py>bounds.y/2?bounds.y:0,velocityX,velocityY);
        status.textContent='依据释放速度预测落点；可以在落稳前重新抓住';
      }
    }
    on(element,'pointerup',()=>release());on(element,'pointercancel',()=>release(true));on(element,'lostpointercapture',()=>release(true));
    on(element,'keydown',e=>{
      if(e.key==='Escape'){if(drag)release(true);else {stop();settle(0,0,0,0,true);}e.preventDefault();return;}
      const directions={ArrowLeft:[-32,0],ArrowRight:[32,0],ArrowUp:[0,-32],ArrowDown:[0,32]};
      if(!directions[e.key]||dismissed)return;e.preventDefault();const b=limits(),[dx,dy]=directions[e.key];
      settle(Math.max(mode==='swipe'?-b.x:0,Math.min(b.x,x+dx)),mode==='swipe'?0:Math.max(0,Math.min(b.y,y+dy)),0,0,true);status.textContent='键盘微调 · 即时响应，无弹簧';
    });
    on(document,'visibilitychange',()=>{if(document.hidden){if(drag)release(true);stop();x=tx;y=ty;draw();finish();}});
    const mq=matchMedia('(prefers-reduced-motion: reduce)');on(mq,'change',()=>{if(api.reduced()&&!drag){stop();x=tx;y=ty;draw();finish();}});
    const resize=new ResizeObserver(()=>{if(drag)release(true);stop();const b=limits();if(!dismissed){x=Math.max(mode==='swipe'?-b.x:0,Math.min(b.x,x));y=mode==='swipe'?0:Math.max(0,Math.min(b.y,y));tx=x;ty=y;draw();}});resize.observe(area);
    function reset(){if(drag)release(true);dismissed=false;element.style.visibility='visible';element.tabIndex=0;settle(0,0,0,0,true);status.textContent='已复位 · 可拖动，也可用方向键移动';}
    return {reset,dismiss(){dismissed=true;settle(area.clientWidth+element.offsetWidth,0,0,0,true);},stop(){if(drag)release(true);stop();resize.disconnect();abort.abort();}};
  }

  function mountGestures(root,api){
    const s=scope(root,api), object=springObject(s.$('[data-mg-card]'),s.$('[data-mg-area]'),api,'swipe',s.$('[data-mg-status]'));
    s.on(s.$('[data-mg-reset]'),'click',()=>object.reset());s.on(s.$('[data-mg-dismiss]'),'click',()=>object.dismiss());
    return ()=>{object.stop();s.stop();};
  }
  function mountApple(root,api){
    const s=scope(root,api), object=springObject(s.$('[data-map-card]'),s.$('[data-map-area]'),api,'corners',s.$('[data-map-status]'));
    s.on(s.$('[data-map-reset]'),'click',()=>object.reset());
    return ()=>{object.stop();s.stop();};
  }

  function mountEmil(root,api){
    const s=scope(root,api),trigger=s.$('[data-me-trigger]'),popup=s.$('[data-me-popup]'),copy=s.$('[data-me-copy]'),status=s.$('[data-me-status]');
    let popupOpen=false,popupAnimation=null,copyAnimation=null,count=0;
    function setPopup(next,event){
      const from=frame(popup);popupAnimation?.cancel();popupOpen=next;
      const target={opacity:next?1:0,transform:next?'scale(1) translateY(0)':'scale(.97) translateY(-4px)'};
      popup.style.opacity=target.opacity;popup.style.transform=target.transform;popup.inert=!next;popup.setAttribute('aria-hidden',String(!next));trigger.setAttribute('aria-expanded',String(next));
      if(!keyboard(event))popupAnimation=s.play(popup,s.reduced()?[{opacity:from.opacity},{opacity:target.opacity}]:[from,target],{duration:next?180:125,easing:ease});
      if(!next&&popup.contains(document.activeElement))trigger.focus();
    }
    s.on(trigger,'click',e=>setPopup(!popupOpen,e));
    s.on(s.$('[data-me-dismiss]'),'click',e=>setPopup(false,e));
    s.on(copy,'click',e=>{
      copyAnimation?.cancel();count++;copy.textContent=`已保存 ${count} 次`;status.textContent='按钮宽度保持稳定；文字变化就是结果反馈';
      if(!keyboard(e))copyAnimation=s.play(copy,[{opacity:.55},{opacity:1}],{duration:160,easing:ease});
    });
    s.on(root,'keydown',e=>{if(e.key==='Escape'&&popupOpen){e.preventDefault();setPopup(false,e);}});
    s.on(document,'pointerdown',e=>{if(popupOpen&&!popup.contains(e.target)&&!trigger.contains(e.target))setPopup(false,e);});
    let keyboardMode=false;
    s.on(root,'keydown',()=>{keyboardMode=true;root.classList.add('me-keys');});
    s.on(root,'pointerdown',()=>{keyboardMode=false;root.classList.remove('me-keys');});
    s.on(copy,'pointerdown',()=>{if(!s.reduced()&&!keyboardMode)copy.style.transform='scale(.97)';});
    const unpress=()=>copy.style.transform='none';s.on(copy,'pointerup',unpress);s.on(copy,'pointerleave',unpress);s.on(copy,'pointercancel',unpress);
    return ()=>{root.classList.remove('me-keys');s.stop();};
  }

  Object.assign(window.UI_LAB ||= {}, {
    animate:{title:'可中断的展开',description:'先判断目的与频率，再选择属性、曲线与退场。这里用短暂的展开解释面板的来源。',mode:'live',hint:'连续点击展开与收回，感受反向接续。Tab 聚焦后按 Enter，状态会即时切换。',code:mountAnimate.toString(),
      render(){return `<style>.ma-stage{min-height:330px;padding:26px;border:1px solid #e5e3df;background:#f8f7f4;border-radius:14px}.ma-anchor{position:relative;max-width:350px;margin:10px auto}.ma-panel{position:relative;margin-top:14px;transform-origin:24px 0;transform:translateY(-6px) scale(.96);opacity:0;background:white;border:1px solid #dad7d1;border-radius:12px;box-shadow:0 12px 30px #2221;padding:22px}.ma-panel h3{margin:0 0 10px}.ma-panel p{line-height:1.8;font-size:14px;color:#70685d}.ma-panel button{margin-top:10px}</style><div class="ma-stage"><div class="ma-anchor"><button class="ui-button primary" data-ma-trigger aria-expanded="false">展开面板 ↗</button><section class="ma-panel" data-ma-panel aria-hidden="true" inert><h3>让变化有来处</h3><p>200ms 展开，160ms 收回。反向操作从当前画面继续，不必等待动画结束。</p><button class="ui-button" data-ma-close>收回面板</button></section></div></div><p class="demo-note" data-ma-status role="status">偶发操作 · 空间关系 · transform 与 opacity</p>`;},mount:mountAnimate},
    choreograph:{title:'把一组变化排成一句话',description:'先分主角、支持与静止部分，再安排有意义的重叠。不是给每一行都添加延迟。',mode:'live',hint:'点击“重播编排”。主信息先到，支持信息在同一波里跟进。快速重播可中断；键盘触发不做错峰。',code:mountChoreograph.toString(),
      render(){return `<style>.mc-stage{padding:28px;background:#f6f7f5;border:1px solid #e1e5dc;border-radius:14px}.mc-top{font-size:12px;color:#63815c;margin-bottom:20px}.mc-lead{font-size:25px;letter-spacing:-.5px;margin:0 0 12px}.mc-support{line-height:1.9;color:#697064;max-width:460px}.mc-bottom{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.mc-detail{padding:12px 16px;background:white;border:1px solid #dfe4d9;border-radius:8px;font-size:13px}.mc-timeline{font-size:12px;line-height:1.8;margin-top:18px;color:#777}</style><div class="mc-stage"><div class="mc-top">一次完成提示 · 背景与容器保持静止</div><h3 class="mc-lead" data-mc-part>你的收藏，已经整理好了。</h3><p class="mc-support" data-mc-part>主要结果先出现，随后呈现整理范围。支持信息与主信息重叠，不排成长队。</p><div class="mc-bottom" data-mc-part><span class="mc-detail">12 篇文章</span><span class="mc-detail">3 个主题</span><span class="mc-detail">原始来源已保留</span></div></div><div class="demo-actions"><button class="ui-button primary" data-mc-play>重播编排</button></div><p class="mc-timeline">主信息 0 / 200ms → 支持 55 / 190ms → 结果 90 / 180ms。总计 270ms。</p><p class="demo-note" data-mc-status role="status">操作按钮始终可用；不为容器和子元素叠加入场。</p>`;},mount:mountChoreograph},
    'animation-performance':{title:'改变布局，还是只改变画面',description:'并排操作 width 与 scaleX，观察相邻标签的位置。展示渲染路径的差别，不伪造性能测试。',mode:'live',hint:'拖动进度滑块，或点击切换。上方 width 会推动标签；下方 scaleX 保留布局位置。可打开浏览器性能面板自行录制。',code:mountPerformance.toString(),
      render(){return `<style>.mp-demo{display:grid;gap:22px}.mp-track{height:52px;display:flex;align-items:center;gap:8px;border:1px solid #ddd9d1;padding:8px;overflow:hidden;background:#f9f8f5;border-radius:8px}.mp-bar{height:28px;min-width:0;background:#bb866e;border-radius:4px;flex-shrink:0}.mp-width{width:28%}.mp-slot{width:100%;height:28px;flex-shrink:0}.mp-scale{width:100%;transform:scaleX(.28);transform-origin:left;background:#698b73}.mp-tag{font-size:10px;white-space:nowrap;color:#67645c}.mp-row p{font-size:12px;margin:0 0 7px}.mp-bottom{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center}.mp-bottom input{width:100%;min-height:44px}</style><div class="mp-demo"><div class="mp-row"><p>width · 布局 → 绘制 → 合成</p><div class="mp-track"><div class="mp-bar mp-width" data-mp-width></div><span class="mp-tag">相邻标签</span></div></div><div class="mp-row"><p>transform · 保留原布局，优先尝试合成</p><div class="mp-track"><div class="mp-slot" style="width:72%"><div class="mp-bar mp-scale" data-mp-scale></div></div><span class="mp-tag">相邻标签</span></div></div><div class="mp-bottom"><label>进度 <output data-mp-value>28%</output><input type="range" min="15" max="86" value="28" data-mp-range aria-label="对比进度"></label><button class="ui-button" data-mp-toggle>切换进度</button></div></div><p class="demo-note">这不是 FPS 跑分：没有制造主线程卡顿，没有伪造帧率；硬件加速仍由浏览器决定。</p>`;},mount:mountPerformance},
    gestures:{title:'跟手、松手与取消',description:'自定义滑动卡片：抓住时直接跟随，释放时把速度交给弹簧。距离或快速甩动都可以移开。',mode:'live',hint:'水平拖动卡片，短距离快速甩动也可移开。按 Esc 取消抓取。可用方向键微调，或直接点击“移开卡片”。',code:springObject.toString(),
      render(){return `<style>.mg-area{position:relative;min-height:245px;overflow:hidden;border:1px dashed #ccc5b8;border-radius:14px;background:#f7f5f0;padding:12px}.mg-card{position:relative;left:0;top:44px;width:min(260px,85%);min-height:132px;margin:auto;padding:22px;background:#fff;border:1px solid #dbd2c5;border-radius:13px;box-shadow:0 10px 22px #45361a12;cursor:grab;touch-action:pan-y;user-select:none}.mg-card h3{margin:0 0 10px;font-size:18px}.mg-card p{font-size:13px;color:#807260;line-height:1.7;margin:0}</style><div class="mg-area" data-mg-area><div class="mg-card" data-mg-card tabindex="0" role="group" aria-label="可拖动卡片；左右方向键移动，Escape取消"><h3>抓住这张卡片</h3><p>指针捕获 · 1:1 跟随<br>松手后依据速度与距离判断</p></div></div><div class="demo-actions"><button class="ui-button" data-mg-reset>复位</button><button class="ui-button" data-mg-dismiss>移开卡片</button></div><p class="demo-note" data-mg-status role="status">真实 Pointer Events；触屏保留纵向页面滚动。</p>`;},mount:mountGestures},
    'apple-design':{title:'可抓取的空间与惯性',description:'浮动对象可以随时被重新抓住。松手时预测落点，沿 X / Y 两个独立弹簧吸附到角落。',mode:'live',hint:'拖动小窗到不同位置，快速甩动，再在它落稳前抓住。方向键提供即时位置调整。',code:springObject.toString(),
      render(){return `<style>.map-area{height:330px;position:relative;overflow:hidden;border:1px solid #dfe4e8;border-radius:17px;background:#edf2f7;padding:12px}.map-corner{position:absolute;width:44px;height:44px;border:1px dashed #b5c3cf;border-radius:8px}.map-corner:nth-child(1){top:12px;left:12px}.map-corner:nth-child(2){top:12px;right:12px}.map-corner:nth-child(3){bottom:12px;left:12px}.map-corner:nth-child(4){bottom:12px;right:12px}.map-card{position:relative;width:min(190px,70%);min-height:112px;border:1px solid #fff;border-radius:16px;padding:18px;background:#ffffffc9;backdrop-filter:blur(12px);box-shadow:0 8px 24px #30455c1c;cursor:grab;touch-action:none;user-select:none}.map-card strong{display:block;font-size:17px;letter-spacing:-.3px}.map-card p{font-size:12px;color:#55697b;line-height:1.7;margin:8px 0 0}@media(prefers-reduced-transparency:reduce),(prefers-contrast:more){.map-card{background:white;backdrop-filter:none;border-color:#60778d}}</style><div class="map-area" data-map-area><i class="map-corner"></i><i class="map-corner"></i><i class="map-corner"></i><i class="map-corner"></i><div class="map-card" data-map-card tabindex="0" role="group" aria-label="可移动浮窗；方向键移动，Escape回到左上角"><strong>一扇小窗</strong><p>先跟手，再交给惯性。<br>随时抓住，随时改变方向。</p></div></div><div class="demo-actions"><button class="ui-button" data-map-reset>回到左上角</button></div><p class="demo-note" data-map-status role="status">减少动态效果时，保留直接拖动，释放后即时落位，不反弹。</p>`;},mount:mountApple},
    'emil-design-eng':{title:'按钮与弹层的细节',description:'按下立即反馈；结果文字直接说明发生了什么；弹层从触发器生长，键盘与 Escape 即时响应。',mode:'live',hint:'连续点击保存，观察固定宽度的结果反馈。展开说明后试试 Esc 与外部点击关闭。',code:mountEmil.toString(),
      render(){return `<style>.me-stage{min-height:310px;padding:28px;border:1px solid #e5e1dc;border-radius:14px;background:#faf9f7}.me-tools{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.me-copy{min-width:130px;min-height:44px;transition:transform 160ms cubic-bezier(.23,1,.32,1)}.me-anchor{position:relative;max-width:310px}.me-popup{margin-top:12px;padding:19px;background:white;border:1px solid #ddd5c9;border-radius:10px;box-shadow:0 9px 24px #2221;transform-origin:20px 0;opacity:0;transform:scale(.97) translateY(-4px)}.me-popup h3{font-size:15px;margin:0 0 10px}.me-popup p{line-height:1.8;font-size:13px;color:#73685b}.me-keys .me-copy{transition:none}@media(prefers-reduced-motion:reduce){.me-copy{transition:none}}@media(hover:hover) and (pointer:fine){.me-copy:hover{border-color:#9b8063}}</style><div class="me-stage"><div class="me-tools"><button class="ui-button primary me-copy" data-me-copy>保存到收藏</button></div><div class="me-anchor"><button class="ui-button" data-me-trigger aria-expanded="false" style="margin-top:18px">为什么这样做？ ↗</button><section class="me-popup" data-me-popup inert aria-hidden="true"><h3>小反馈，直接发生</h3><p>按压 160ms；弹层 180ms 展开 / 125ms 收回；原点靠近按钮。没有为高频操作添加长入场。</p><button class="ui-button" data-me-dismiss>明白了</button></section></div></div><p class="demo-note" data-me-status role="status">所有保存只改变本页计数，不写剪贴板或真实资料。</p>`;},mount:mountEmil}
  });
})();
