# 20 个设计类 skill 通读报告（团队共用情报）

> 来源：探索代理通读 `/Users/huziyao/.agents/skills/` 全部 20 个 SKILL.md 与 README.md 的综合结论。
> 供本次"Alice 聊天消息流组件 demo"演练各角色引用。

共性与否决名单见文末。逐 skill 摘要：

## 逐个 skill

**animate** — web 动画施工七步门：该不该动（频率表可产出零行代码）→命名目的→最便宜工具（CSS transition→@starting-style→CSS animation→WAAPI→Motion）→属性→曲线时长→中断退场。禁忌：transition:all、scale(0)、UI 上 ease-in、无理由 >300ms、自造数值。与 review-animations 同标准。

**animate-expo** — RN/Expo 版（本演练不适用）。三公理：无 hover、动效留 UI 线程、手指在元素上。

**animation-performance** — 纯诊断："每帧来得及吗"（60fps=16.7ms）；症状表→七步病因排序（layout 属性→主线程忙→每帧 setState→CSS 变量继承风暴→blur>20px→最后才是 will-change）。禁忌：快机下结论、will-change 当第一招。

**animation-vocabulary** — 反查词典："气泡弹一下"→术语（Pop in 等），多义给辨析。只命名不施工。

**apple-design** — WWDC 流体交互观参照系：pointer-down 即反馈、拖拽 1:1、可中断（velocity 贯穿换向）、spring=damping ratio+response、rubber-band 公式、材质层级、字号随 tracking/leading。取思想不取库。

**ask-sonner** — React toast 库 Sonner 手册（本演练不适用，禁依赖）。

**break-ui** — 对抗测试：最坏真实用户数据（超长名、2000字、纯emoji、单字符、空、1000行）+ dev-only 切换器（Demo/Worst/Empty/One/1000），20+ 失败签名表逐项核对，Broken/Ugly/Fragile 三级报告，说 `fix 1,3` 才修。无项目场景的官方分支=单文件 HTML（正对本演练）。

**choreograph** — 多元素编排：先 casting（Lead/Support/Decoration/Static，多数在这步被砍），关系四选一 Together/Overlap/Sequence/Carry；"顺序无意义=纯延迟"；stagger 30-80ms 且总量<300ms；禁忌：一容器两层入场、等距 stagger（节拍器感）、序列不耐中断。

**emil-design-eng** — 整包哲学母本与兜底：taste 可训练、看不见的细节复利；频率门/目的命名/三曲线/时长表 + 组件工艺（press 0.97、popover origin-aware、@starting-style、cohesion、隔天复看）；评审必须 Before|After|Why 三列表格。

**find-animation-opportunities** — 只读找"该动没动"：按缝扫描（无 :active、状态瞬移、缺空间故事），候选过四问门（频率/目的/速度/功能），输出 ≤5-7 条按杠杆排序建议 + **必须有的被否决名单**。不改源码。

**gestures** — 拖/滑/sheet 两阶段铁律：按下=TRACK（1:1、无曲线、transform 直写元素）；抬起=RELEASE（带速度的 spring/transition，绝不用 keyframe）；dismiss 判定距离或速度任一；hold 不对称（2s linear/200ms ease-out）。纯按钮方案不需要它。

**improve-animations** — 全库动效审计：recon→八类并行只读审计→逐条 file:line 复核→分优先级计划文件（零上下文可执行）。不改源码。

**learn-taste** — 设计纠正沉淀：五类信号（显式纠正/静默改值/反复nudge/回退/确认过的非常规选择）→过滤→最窄可行级别规则带 why→DESIGN.md/项目 skill/lint。只记用户 taste；propose-then-stop，说 `write all` 才写。

**mobile-native** — 手机原生感 11 症状各一两行修复：hover 门 `(hover:hover) and (pointer:fine)`、tap-highlight 透明、100dvh/100svh、input 16px 防缩放、`touch-action:manipulation`、`overscroll-behavior:contain`、safe-area env()、theme-color 分明暗。禁忌：禁缩放、UA 嗅探。

**pick-ui-library** — Emil 精选清单查表给唯一推荐（本演练禁依赖，结论作废，仅存目）。

**prototype** — 发散探索：3-5 个**命名了差异轴**的真方向（非换色假发散），每个完整可交互、picker 切换零动画；无项目分支=单文件自包含 HTML。禁忌：三 tint 假发散、lorem ipsum、死按钮、预选最爱。

**remove-animations** — 动效减法：判决树（键盘→删；100+/天→删；说不出目的→删；高频合法→REDUCE 近不可感知）+ 必有的 Kept 表；删前查 hazards（opacity:0 残留、transitionend 等待、卸载时序）。

**review-animations** — 动效评审关卡：十条不可协商标准（正当性/频率匹配/ease-out 家族/<300ms/origin 物理正确/可中断/GPU 属性/reduced-motion+hover 门/不对称时序/cohesion）；默认 flag，approval 靠挣；Before|After|Why 表。

**write-swift** / **video-material-sourcing** — Swift 写作 / 视频素材域，与本次演练无关。

## 推荐流水线（针对本样例）

主线：`prototype`（多方向）→ `emil-design-eng`（工艺底线）→ `animate`（微动效逐个过频率门，预计否决一半）→ `break-ui`（官方单文件模式，喂坏数据）→ `mobile-native`（composer 输入密集：16px、enterkeyhint、overscroll-contain、100dvh、safe-area）→ `review-animations` 终审 + `learn-taste` 沉淀 DESIGN.md。

旁证（取思想不占施工位）：`apple-design`（spring/材质/字距参照）、`animation-vocabulary`（统一术语）、`choreograph`（用"一容器一入场"否决逐条 stagger）、`animation-performance`（长列表/键盘弹出才查帧）、`find-animation-opportunities` + `remove-animations`（定稿后加减双审计作复盘）。

**不适用**：animate-expo、ask-sonner、pick-ui-library（生态/依赖冲突）、write-swift、video-material-sourcing。

**执行注意**：`prototype`、`review-animations`、`learn-taste`、`pick-ui-library` 带 disable-model-invocation——子代理须直接读对应 SKILL.md 并照做，不依赖自动触发。
