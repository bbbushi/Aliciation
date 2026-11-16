/* Platform-oriented skills: honest browser models, no native runtime or packages. */
(() => {
  'use strict';
  const scope = root => {
    const controller=new AbortController(), timers=new Set(), animations=new Set();
    return {
      on(element,event,handler,options={}){element.addEventListener(event,handler,{...options,signal:controller.signal});},
      delay(fn,ms){const id=setTimeout(()=>{timers.delete(id);fn();},ms);timers.add(id);return id;},
      cancel(id){clearTimeout(id);timers.delete(id);},
      motion(api,element,frames,options){const animation=api.animate(element,frames,options);if(animation?.cancel)animations.add(animation);return animation;},
      cleanup(){controller.abort();timers.forEach(clearTimeout);animations.forEach(animation=>animation.cancel());}
    };
  };
  Object.assign(window.UI_LAB ||= {}, {
    'animate-expo': {
      title:'Expo 手势与弹簧', mode:'model',
      description:'连续手势交给 UI runtime；共享值携带位置，松手时交接速度。',
      hint:'拖动把手再松开，或点击快甩与归位。这里是浏览器弹簧示意，不是 Expo 性能验证。',
      code:`// React Native + Reanimated / Gesture Handler（组件内）
const y = useSharedValue(0);
const origin = useSharedValue(0);
const pan = useMemo(() => Gesture.Pan()
  .activeOffsetY([-10, 10])
  .onStart(() => {
    cancelAnimation(y);
    origin.set(y.get());
  })
  .onUpdate(e => { y.set(Math.max(0, origin.get() + e.translationY)); })
  .onEnd(e => {
    const dismiss = y.get() + e.velocityY * 0.2 > 120;
    y.set(withSpring(dismiss ? 300 : 0, {
      duration: 300, dampingRatio: 0.8,
      velocity: e.velocityY, reduceMotion: ReduceMotion.System,
    }));
  }), []);
const style = useAnimatedStyle(() => ({ transform: [{ translateY: y.get() }] }));
// <GestureDetector gesture={pan}><Animated.View style={style} /></GestureDetector>
// 根部需要 GestureHandlerRootView；需真机 release build 验证。`,
      render(){return `<style>.px-phone{position:relative;max-width:340px;height:340px;overflow:hidden;border:1px solid #d9d5cd;border-radius:26px;background:#efeeeb;margin:auto}.px-head{padding:27px 22px;color:#6b6862}.px-sheet{position:absolute;inset:105px 10px auto;background:#fff;border-radius:18px;padding:0 22px 23px;box-shadow:0 -6px 30px #00000012;min-height:240px;will-change:transform}.px-handle{display:block;width:100%;height:44px;border:0;background:transparent;touch-action:none;cursor:grab}.px-handle:before{content:'';display:block;width:38px;height:4px;border-radius:4px;background:#b7b3ac;margin:auto}.px-sheet h3{margin:0 0 12px}.px-sheet p{line-height:1.8;color:#6c6860;font-size:13px}.px-readout{font-variant-numeric:tabular-nums;text-align:center;margin:14px 0}.px-controls{justify-content:center}</style><div class="px-phone"><div class="px-head">附近的想法<br><small>浏览器交互模型</small></div><div class="px-sheet"><button class="px-handle" aria-label="拖动抽屉；也可使用下方归位或快甩按钮"></button><h3>给一个想法留点空间</h3><p>拖动时跟随位置；松手时，根据距离与速度决定归位或收起。</p><span class="ui-badge">原生目标：UI runtime</span></div></div><p class="px-readout" role="status">位置 0 px · 已归位</p><div class="ui-row demo-actions px-controls"><button class="ui-button" data-px="home">归位</button><button class="ui-button primary" data-px="fling">向下快甩</button></div><p class="demo-note">此处用浏览器采样曲线表达弹簧；没有运行 Reanimated、haptics 或原生手势识别。真机需要检查中途接手与速度交接。</p>`;},
      mount(root,api){const life=scope(root),sheet=root.querySelector('.px-sheet'),handle=root.querySelector('.px-handle'),status=root.querySelector('.px-readout');let y=0,animation=null,drag=null;
        const current=()=>{const matrix=getComputedStyle(sheet).transform;return matrix==='none'?0:new DOMMatrixReadOnly(matrix).m42;};
        const stop=()=>{y=current();animation?.cancel();animation=null;sheet.style.transform=`translateY(${y}px)`;};
        const settle=(target,velocity=0,keyboard=false)=>{stop();const start=y;y=target;sheet.style.transform=`translateY(${target}px)`;status.textContent=`位置 ${target} px · ${target?'已收起，可归位':'已归位'}`;if(api.reduced()||keyboard)return;
          const frames=Array.from({length:25},(_,i)=>{const t=i/24,decay=Math.exp(-7*t),wave=Math.cos(10*t);const position=target+(start-target)*decay*wave+(velocity/1000)*30*decay*Math.sin(10*t);return {transform:`translateY(${i===24?target:Math.min(240,Math.max(-8,position))}px)`};});
          animation=life.motion(api,sheet,frames,{duration:300,easing:'linear'});
        };
        life.on(handle,'pointerdown',event=>{if(event.button!==0||drag)return;stop();drag={id:event.pointerId,start:event.clientY,origin:y,lastY:event.clientY,lastTime:event.timeStamp,velocity:0};handle.setPointerCapture(event.pointerId);});
        life.on(handle,'pointermove',event=>{if(!drag||drag.id!==event.pointerId)return;const dt=event.timeStamp-drag.lastTime;if(dt>0)drag.velocity=(event.clientY-drag.lastY)/dt*1000;drag.lastY=event.clientY;drag.lastTime=event.timeStamp;const next=drag.origin+event.clientY-drag.start;y=next<0?next*.2:Math.min(240,next);sheet.style.transform=`translateY(${y}px)`;status.textContent=`位置 ${Math.round(y)} px · 跟随手指`;});
        const release=event=>{if(!drag||drag.id!==event.pointerId)return;const velocity=event.timeStamp-drag.lastTime>100?0:drag.velocity;drag=null;settle(y+velocity*.2>110?235:0,velocity);};
        life.on(handle,'pointerup',release);life.on(handle,'pointercancel',event=>{if(drag?.id===event.pointerId){drag=null;settle(0);}});
        root.querySelectorAll('[data-px]').forEach(button=>life.on(button,'click',event=>settle(button.dataset.px==='home'?0:235,button.dataset.px==='home'?0:900,event.detail===0)));
        return ()=>{if(drag&&handle.hasPointerCapture(drag.id))handle.releasePointerCapture(drag.id);animation?.cancel();life.cleanup();};}
    },
    'pick-ui-library': {
      title:'按任务选库',mode:'live',description:'先识别任务，再检查现有技术栈；从技能清单给出一个适合的选择。',
      hint:'输入任务，切换项目约束。不会联网或安装依赖；遇到清单未覆盖的任务会明确说明。',
      code:`// 来自 pick-ui-library 的决策次序：
// 1. 识别任务，不盲从用户说出的库名。
// 2. 先读 package.json；已有可用库就复用。
// 3. 清单有明确答案时，只推荐一个。
// 4. 清单未覆盖时，明确说出边界。
// Alice 原生 JS / 零依赖：简单 fade 直接 CSS，不引入 motion。`,
      render(){return `<form class="lib-form ui-stack"><label>你要完成什么？<input class="ui-input" name="task" value="上传文件后显示通知" placeholder="例如：长列表、图表、拖拽、验证码" required></label><div class="ui-grid"><label>项目约束<select name="stack" class="ui-input"><option value="react">React · 可引入依赖</option><option value="native">Alice · 原生 JS 零依赖</option></select></label><label>现有依赖<select name="existing" class="ui-input"><option value="none">尚未安装相关库</option><option value="sonner">已有 Sonner</option><option value="react-window">已有 react-window</option></select></label></div><div class="demo-actions"><button class="ui-button primary" type="submit">给出选择</button></div></form><section class="ui-card lib-result" aria-live="polite" style="margin-top:20px;overflow-wrap:anywhere"></section><p class="demo-note">决策仅依据本地技能附带的清单。现有依赖由你选择，未读取项目 package.json。</p>`;},
      mount(root){const life=scope(root),form=root.querySelector('form'),out=root.querySelector('.lib-result');
        const rules=[[/通知|toast|sonner/i,'Sonner','通知与 promise 状态回执'],[/弹窗|菜单|下拉|对话框|popover|dialog/i,'base-ui','可访问的 UI primitives'],[/抽屉|sheet|drawer/i,'Vaul','抽屉与底部面板'],[/验证码|otp/i,'input-otp','一次性验证码输入'],[/命令|command|cmdk/i,'cmdk','命令面板'],[/^(?=.*(?:实时|流式|streaming))(?=.*(?:图表|chart)).*$/i,'Liveline','实时流式图表'],[/图表|chart/i,'recharts','通用图表'],[/拖拽|拖放|drag/i,'dnd kit','拖放交互'],[/长列表|虚拟|virtual/i,'Virtuoso','长列表与表格虚拟化'],[/数字|计数|价格/i,'NumberFlow','数字变化'],[/弹簧|布局动画|手势动画/i,'motion','连续或可中断动画'],[/淡入|悬停|fade|hover/i,'CSS transition','简单状态过渡不需要动画库'],[/状态管理|共享状态/i,'zustand','共享状态管理'],[/主题|深色|dark/i,'next-themes','无闪烁主题切换'],[/语法高亮|highlight/i,'shiki','代码语法高亮']];
        function choose(event){event?.preventDefault();const task=form.elements.task.value.trim();if(!task)return;const found=rules.find(([pattern])=>pattern.test(task));out.replaceChildren();const title=document.createElement('h3'),reason=document.createElement('p');
          if(!found){title.textContent='本地清单未覆盖这个任务';reason.textContent='请补充你需要的具体交互。这里不会凭空给出清单之外的推荐。';}
          else if(form.elements.stack.value==='native'&&found[1]!=='CSS transition'){title.textContent='保持原生 JS，不新增依赖';reason.textContent=`清单对应 ${found[1]}（${found[2]}），但 Alice 的零依赖约束优先。复杂需求需另行设计原生方案；这里不建议为此切换到 React。`;}
          else if(found[1]==='Virtuoso'&&form.elements.existing.value==='react-window'){title.textContent='先复用已有 react-window';reason.textContent='清单推荐 Virtuoso；当前已有同类库，先确认实际缺口，不自动更换依赖。';}
          else{title.textContent=found[1];reason.textContent=`用于${found[2]}。${found[1]==='Sonner'&&form.elements.existing.value==='sonner'?'项目已有，直接复用。':'这只是建议，未执行安装。'}`;}out.append(title,reason);
        }life.on(form,'submit',choose);choose();return ()=>life.cleanup();}
    },
    'ask-sonner': {
      title:'Toast 的完整生命周期',mode:'model',description:'一个稳定 id，从 loading 更新到 success 或 error，再自动或手动关闭。',
      hint:'提交模拟上传，切换失败与常驻，尝试处理中关闭。这里没有安装或运行 Sonner。',
      code:`import { Toaster, toast } from 'sonner';
// Toaster 只在应用根部挂一次：
<Toaster theme="system" richColors closeButton />

async function upload(file) {
  const id = toast.loading('上传中…');
  try {
    await sendFile(file);
    toast.success('上传成功', { id, duration: 4000 });
  } catch {
    toast.error('上传未完成', { id, duration: Infinity });
  }
}
// 主动关闭：toast.dismiss(id)
// 更简洁：toast.promise(sendFile(file), {
//   loading: '上传中…', success: '上传成功', error: '上传未完成'
// });`,
      render(){return `<style>.sn-stage{min-height:200px;display:grid;align-items:end;background:#eeefed;border-radius:12px;padding:20px}.sn-toast{background:#fff;border:1px solid #d9ddd7;border-radius:10px;padding:16px;box-shadow:0 8px 24px #0000000b;display:flex;align-items:center;gap:12px;width:min(100%,380px);margin-left:auto}.sn-mark{font-size:20px}.sn-copy{flex:1}.sn-copy strong,.sn-copy small{display:block}.sn-copy small{margin-top:5px;color:#73766f}.sn-dismiss{border:0;background:transparent;min-width:44px;min-height:44px;font-size:20px;cursor:pointer}.sn-toast[hidden]{display:none}.sn-state{font-size:12px;color:#65705f;min-height:24px}</style><div class="ui-stack"><div class="ui-row"><label><input type="checkbox" data-sn="fail"> 模拟失败</label><label><input type="checkbox" data-sn="persist"> 结果常驻</label></div><div class="demo-actions"><button class="ui-button primary" data-sn="submit">模拟上传</button><button class="ui-button" data-sn="clear">关闭通知</button></div><div class="sn-stage"><div class="sn-toast" hidden role="status" aria-live="polite"><span class="sn-mark" aria-hidden="true"></span><div class="sn-copy"><strong></strong><small></small></div><button class="sn-dismiss" aria-label="关闭通知">×</button></div></div><p class="sn-state" role="status">等待操作</p><p class="demo-note">原生 JS 生命周期模型，不是 Sonner 组件。没有上传文件或真实网络请求；鼠标停留、键盘焦点进入或页面不可见时暂停结果倒计时。</p></div>`;},
      mount(root,api){const life=scope(root),toast=root.querySelector('.sn-toast'),submit=root.querySelector('[data-sn="submit"]'),status=root.querySelector('.sn-state');let generation=0,loadingTimer=null,autoTimer=null,deadline=0,remaining=0,hover=false,phase='idle';
        const pause=()=>{if(autoTimer){remaining=Math.max(0,deadline-performance.now());life.cancel(autoTimer);autoTimer=null;}};
        const dismiss=()=>{const focusedInside=toast.contains(document.activeElement);generation++;life.cancel(loadingTimer);life.cancel(autoTimer);loadingTimer=autoTimer=null;phase='idle';remaining=0;toast.hidden=true;submit.disabled=false;if(focusedInside)submit.focus();status.textContent='通知已关闭；旧任务回执不会让它重新出现。';};
        const resume=()=>{if(autoTimer||!remaining||hover||document.hidden||toast.contains(document.activeElement)||phase==='loading')return;deadline=performance.now()+remaining;autoTimer=life.delay(()=>{autoTimer=null;remaining=0;toast.hidden=true;phase='idle';status.textContent='结果通知已自动关闭。';},remaining);};
        life.on(root.querySelector('[data-sn="submit"]'),'click',event=>{const wasHidden=toast.hidden;generation++;const run=generation;pause();remaining=0;life.cancel(loadingTimer);phase='loading';submit.disabled=true;toast.hidden=false;toast.querySelector('.sn-mark').textContent='◌';toast.querySelector('strong').textContent='上传中…';toast.querySelector('small').textContent='同一个通知 id · 正在处理示例';status.textContent='loading → 等待模拟回执';if(wasHidden&&event.detail!==0&&!api.reduced())life.motion(api,toast,[{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'cubic-bezier(.23,1,.32,1)'});const fail=root.querySelector('[data-sn="fail"]').checked,persist=root.querySelector('[data-sn="persist"]').checked;
          loadingTimer=life.delay(()=>{if(run!==generation)return;phase=fail?'error':'success';submit.disabled=false;toast.querySelector('.sn-mark').textContent=fail?'!':'✓';toast.querySelector('strong').textContent=fail?'上传未完成':'上传成功';toast.querySelector('small').textContent=fail?'示例连接失败，通知保留供查看。':'文件已处理（示例），没有创建第二个通知。';status.textContent=`loading → ${phase} · ${fail||persist?'常驻':'4 秒后关闭'}`;remaining=fail||persist?0:4000;resume();},1200);
        });
        life.on(root.querySelector('[data-sn="clear"]'),'click',dismiss);life.on(root.querySelector('.sn-dismiss'),'click',dismiss);life.on(toast,'focusin',pause);life.on(toast,'focusout',()=>queueMicrotask(()=>{if(toast.isConnected)resume();}));life.on(toast,'pointerenter',()=>{hover=true;pause();});life.on(toast,'pointerleave',()=>{hover=false;resume();});life.on(document,'visibilitychange',()=>document.hidden?pause():resume());return ()=>{generation++;life.cleanup();};}
    },
    'learn-taste': {
      title:'把偏好变成可复用规则',mode:'live',description:'用户说过什么、规则适用于哪里、为什么；确认后才得到可复制的 DESIGN 草稿。',
      hint:'填写原话与原因，选择规则范围，确认预览后复制。不会写入文件或修改全局技能。',
      code:`## Interaction

- **每个操作步骤只呈现当下理解和行动所需的信息；更多细节由明确入口展开。**
  Why: 减少同一时刻需要处理的信息，信息过少同样会影响理解和行动。
  <!-- 用户确认后保留原话、日期和适用范围 -->

// learn-taste 流程：收集纠正 → 过滤泛化 → 核对 why → 提案 → 用户确认 → 写入。
// 原因若只是推断，必须标注 inferred 并得到确认。`,
      render(){return `<form class="taste-form ui-stack"><label>用户原话<textarea class="ui-input" name="quote" rows="2" required>让用户每一步接受的信息量刚刚好，以减少认知负担为目标。</textarea></label><label>适用范围<select name="scope" class="ui-input"><option>页面的信息呈现</option><option>操作反馈</option><option>详情窗口</option></select></label><label>候选规则<textarea class="ui-input" name="rule" rows="2" required>只呈现当前步骤理解和行动所需的信息，进一步细节由明确入口展开。</textarea></label><label>原因（用户给出；未知时先询问）<input class="ui-input" name="why" required value="减少同一时刻需要处理的信息，降低认知负担。"></label><label><input name="inferred" type="checkbox"> 原因是我的推断，尚未得到用户确认</label><button class="ui-button" type="submit">生成待确认草稿</button></form><div class="ui-card" style="margin-top:20px"><strong>规则预览</strong><textarea class="ui-input taste-preview" rows="7" readonly aria-label="DESIGN 规则草稿" style="width:100%;margin:12px 0;font:12px/1.8 ui-monospace,monospace"></textarea><label><input class="taste-approved" type="checkbox"> 我已核对原话、规则范围与原因，确认此草稿</label><div class="demo-actions" style="margin-top:14px"><button class="ui-button primary taste-copy" disabled>复制已确认规则</button></div><p class="taste-status demo-note" role="status"></p></div>`;},
      mount(root){const life=scope(root),form=root.querySelector('form'),preview=root.querySelector('.taste-preview'),approved=root.querySelector('.taste-approved'),copy=root.querySelector('.taste-copy'),status=root.querySelector('.taste-status');let generated=false,disposed=false;
        const invalidate=()=>{approved.checked=false;copy.disabled=true;generated=false;status.textContent='输入已改变，请重新生成并确认。';};
        const generate=event=>{event?.preventDefault();const quote=form.elements.quote.value.trim(),rule=form.elements.rule.value.trim(),why=form.elements.why.value.trim();if(!quote||!rule||!why){status.textContent='原话、规则与原因都需要填写；未知原因应先向用户询问。';return;}preview.value=`## ${form.elements.scope.value}\n\n- **${rule}**\n  Why: ${form.elements.inferred.checked?'[inferred，待确认] ':''}${why}\n  用户原话：「${quote}」\n  状态：待用户确认；未写入任何文件。`;generated=true;approved.checked=false;copy.disabled=true;status.textContent='先核对预览，再确认；这个页面不会自行写规则文件。';};
        life.on(form,'input',invalidate);life.on(form,'submit',generate);life.on(approved,'change',()=>{copy.disabled=!approved.checked||!generated;status.textContent=approved.checked?'已确认，可以复制；仍未写入文件。':'草稿尚未确认。';});
        life.on(copy,'click',async()=>{if(!approved.checked||!generated)return;const text=preview.value.replace('待用户确认；未写入任何文件。','用户已在本页确认；待人工写入项目 DESIGN.md。').replace('[inferred，待确认] ','[推断原因，用户已在本页确认] ');try{if(!navigator.clipboard?.writeText)throw Error('Clipboard unavailable');await navigator.clipboard.writeText(text);if(!disposed)status.textContent='已复制。你可以自行粘贴到项目 DESIGN.md；未自动写入。';}catch{if(disposed)return;preview.value=text;preview.focus();preview.select();status.textContent='浏览器未提供剪贴板权限，已选中草稿，可手动复制。';}});generate();return ()=>{disposed=true;life.cleanup();};}
    },
    'mobile-native': {
      title:'安全区域与键盘',mode:'model',description:'用可控手机模型观察安全区与视口收缩；实际触屏、输入缩放和键盘仍需真机确认。',
      hint:'切换安全区修复和模拟键盘，观察顶部标题与底部输入框是否被遮挡。输入框可以真实编辑。',
      code:`<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content" />
<style>
.app { min-height: 100dvh; }
.header { padding-top: env(safe-area-inset-top, 0px); }
.composer { padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px)); }
input, textarea { font-size: 16px; } /* 修复 iOS 输入缩放，绝不禁用 zoom */
button { touch-action: manipulation; user-select: none; }
@media (hover: hover) and (pointer: fine) { button:hover { background: #eee; } }
</style>
<!-- env() 和真实键盘行为必须在手机上验收，桌面模拟不能证明通过。 -->`,
      render(){return `<style>.mn-phone{width:min(100%,290px);height:390px;margin:20px auto;position:relative;overflow:hidden;background:#18191b;border-radius:28px;padding:6px;border:1px solid #333}.mn-screen{height:100%;background:#f5f4f0;border-radius:23px;position:relative;overflow:hidden}.mn-notch{position:absolute;width:100px;height:22px;top:0;left:50%;transform:translateX(-50%);background:#18191b;border-radius:0 0 14px 14px;z-index:3}.mn-content{height:100%;position:relative;display:flex;flex-direction:column;overflow:hidden}.mn-header{font-size:16px;padding:6px 14px 12px;background:#fff;border-bottom:1px solid #ddd}.mn-message{padding:22px 15px;flex:1;font-size:13px;line-height:1.8;overflow:auto;overscroll-behavior:contain}.mn-composer{padding:9px 9px 0;background:#fff;border-top:1px solid #ddd}.mn-composer input{width:100%;min-width:0;border:1px solid #d8d5cd;border-radius:7px;font-size:16px;padding:10px;background:#fff;color:#30322d}.mn-home{position:absolute;bottom:6px;width:95px;height:5px;border-radius:10px;background:#1f211e;left:50%;transform:translateX(-50%);z-index:3}.mn-screen[data-fixed] .mn-header{padding-top:34px}.mn-screen[data-fixed] .mn-composer{padding-bottom:25px}.mn-keyboard[hidden]{display:none}.mn-keyboard{position:absolute;bottom:0;left:0;right:0;height:130px;background:#cccdd1;padding:18px 12px 28px;z-index:2;font:12px/2 ui-monospace,monospace;text-align:center;letter-spacing:8px;color:#444}.mn-screen[data-keyboard][data-fixed] .mn-content{height:calc(100% - 130px)}.mn-screen[data-keyboard] .mn-home{bottom:5px}</style><div class="ui-row demo-actions"><label><input type="checkbox" class="mn-fix" checked> 应用安全区与视口修复</label><label><input type="checkbox" class="mn-key-toggle"> 模拟键盘展开</label></div><div class="mn-phone"><div class="mn-screen" data-fixed><div class="mn-notch" aria-hidden="true"></div><div class="mn-content"><header class="mn-header">Alice · 对话</header><div class="mn-message">顶部内容应避开刘海。<br><br>键盘出现时，输入框应留在可见区域。<br><br>这些尺寸只是示意，不是某一台手机的真实安全区。</div><div class="mn-composer"><input placeholder="给 Alice 留句话" aria-label="手机模型中的示例输入框" enterkeyhint="send"></div></div><div class="mn-keyboard" hidden aria-hidden="true">Q W E R T Y<br>A S D F G H<br>Z X C V B N</div><div class="mn-home" aria-hidden="true"></div></div></div><p class="mn-status demo-note" role="status">修复开启：标题与输入区均为安全区预留空间。</p><p class="demo-note">浏览器模型：键盘与刘海为绘制占位。真实设备的 env()、地址栏、横屏、粗指针 hover 和软键盘需要分别验证。</p>`;},
      mount(root){const life=scope(root),screen=root.querySelector('.mn-screen'),keyboard=root.querySelector('.mn-keyboard'),fixed=root.querySelector('.mn-fix'),toggle=root.querySelector('.mn-key-toggle'),status=root.querySelector('.mn-status');const update=()=>{screen.toggleAttribute('data-fixed',fixed.checked);screen.toggleAttribute('data-keyboard',toggle.checked);keyboard.hidden=!toggle.checked;status.textContent=fixed.checked?(toggle.checked?'模型中的视口已收缩，输入区仍可见。':'修复开启：标题与输入区均为安全区预留空间。'):(toggle.checked?'修复关闭：模拟键盘遮住了底部输入区。':'修复关闭：顶部标题靠近刘海，底部缺少安全区间距。');};life.on(fixed,'change',update);life.on(toggle,'change',update);return ()=>life.cleanup();}
    },
    'write-swift': {
      title:'Swift 的隔离与 await',mode:'model',description:'观察 await 前后状态可能变化；用版本校验避免迟到回执覆盖用户的新编辑。',
      hint:'开始读取后，先注入一次编辑，再让旧回执返回。每个阶段都由你推进，无后台线程或 Swift 运行时。',
      code:`// Swift 6：示例采用 MainActor 隔离 UI 状态。
@MainActor
final class DraftModel {
  private(set) var text = "原始内容"
  private var revision = 0

  func edit(_ value: String) {
    revision += 1
    text = value
  }

  func load() async throws {
    let requestedRevision = revision
    // Task.sleep 是本地模拟延迟，不代表 CPU 工作已离开 MainActor。
    try await Task.sleep(for: .milliseconds(250))
    let candidate = "较早请求的回执"
    guard revision == requestedRevision else { return }
    text = candidate
  }
}
// await 允许其他工作插入；actor 隔离不等于跨 await 的事务。
// 真正的 CPU 密集工作先 profile，再决定是否需要 @concurrent。`,
      render(){return `<style>.sw-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.sw-node{border:1px solid #ddd9d0;border-radius:10px;padding:16px;min-width:0;overflow-wrap:anywhere}.sw-node[data-active]{border-color:#a28865;background:#faf4e9}.sw-node small{display:block;color:#6c7065;margin-top:9px;line-height:1.7}.sw-node strong{font-size:13px}.sw-value{font:14px/1.8 ui-monospace,monospace;overflow-wrap:anywhere}.sw-log{min-height:78px;line-height:1.9}@media(max-width:600px){.sw-flow{grid-template-columns:1fr}}</style><div class="sw-flow"><div class="sw-node" data-sw-node="start"><strong>① MainActor</strong><small>捕获当前 revision</small></div><div class="sw-node" data-sw-node="await"><strong>② await 挂起</strong><small>允许其他编辑插入</small></div><div class="sw-node" data-sw-node="resume"><strong>③ MainActor</strong><small>恢复后重新核对</small></div></div><div class="ui-card" style="margin:18px 0"><div class="sw-value">revision = <span data-sw="version">0</span><br>text = <span data-sw="value">原始内容</span></div></div><div class="ui-row demo-actions"><button class="ui-button primary" data-sw="start">开始读取</button><button class="ui-button" data-sw="edit" disabled>插入一次用户编辑</button><button class="ui-button" data-sw="resume" disabled>让旧回执返回</button><button class="ui-button" data-sw="reset">重置</button></div><p class="sw-log demo-note" role="status">准备就绪。此图表示隔离域与状态变化，不表示真实线程调度。</p><p class="demo-note">浏览器状态模型；没有编译或运行 Swift。代码中的 await 不保证更换线程，MainActor 也不是跨 await 的事务锁。</p>`;},
      mount(root){const life=scope(root);let version=0,captured=null,value='原始内容',phase='start';const controls=name=>root.querySelector(`[data-sw="${name}"]`);const paint=()=>{controls('version').textContent=version;controls('value').textContent=value;controls('start').disabled=phase==='await';controls('edit').disabled=phase!=='await';controls('resume').disabled=phase!=='await';root.querySelectorAll('[data-sw-node]').forEach(node=>node.toggleAttribute('data-active',node.dataset.swNode===phase));};const log=text=>root.querySelector('.sw-log').textContent=text;
        life.on(controls('start'),'click',()=>{captured=version;phase='await';paint();log(`读取开始时捕获 revision ${captured}，现在停在 await。你可以先修改 UI 状态。`);});life.on(controls('edit'),'click',()=>{version++;value=`用户的新编辑 ${version}`;paint();log(`await 期间用户完成新编辑，revision 变为 ${version}。旧回执仍对应 ${captured}。`);});life.on(controls('resume'),'click',()=>{phase='resume';if(version===captured){value='较早请求的回执';log('revision 未变，接受回执并更新 UI。');}else log(`revision ${version} ≠ 请求时的 ${captured}：拒绝旧回执，保留用户的新编辑。`);paint();});life.on(controls('reset'),'click',()=>{version=0;captured=null;value='原始内容';phase='start';paint();log('已重置。每一步都是浏览器内的状态演示。');});paint();return ()=>life.cleanup();}
    }
  });
})();
