# FRAGMENT · 03 单条消息（B 舱壁 Bulkhead）

> 源实现：`parts-impl/03/part.html`（`P03` 命名空间与其 CSS 块为部件本体，实验台/分区/信号日志为演示脚手架，组装时丢弃）。
> 上游契约：`../PARTS.md` §2.1（Turn 全字段）、§2.3（信号）。本部件**只发出一个信号** `retry:send(id)`，不新增跨部分字段/信号。

## 0. 定位与动效决策（PRINCIPLES §1 #5/#6，中频→近不可感知档，显式记录）

- **用户消息入场**：显式二选一取后者——按压档 fade + `scale(0.96→1)`，opacity 140ms / transform 160ms，**无位移**；origin `100% 100%`（用户面板锚定右下、来自 composer 方向，入场 scale 与 press 共用同一装配锚点）。
- **Alice 回复入场**：小浮层档 fade + `translateY(8px→0)`（沿内容流自下，≤8px），opacity 180ms / transform 160ms；有初始 transform，禁纯 fade。
- **无退场动效**：消息移除只经 02 全量重建（instant）——无 exit 即不触不对称条款。
- **press**：仅失败消息（唯一可按压形态）`:active` 面板 `scale(0.97)`，160ms `var(--ease-out)`（§2.1 底线）。
- **状态零动效**：sending/failed/silent/正在回复… 的文字与颜色变化全部瞬时（状态不得靠动效表达）；仅 hover 走 140ms ease 颜色过渡且门控。
- 工具阶梯：CSS transition + `@starting-style` 即够（白名单第一、二档）——**无 keyframes**（中频反复触发禁用，§2.5）、无 WAAPI、无 rAF。`@starting-style` 不支持的浏览器＝消息直接出现，不破损。

## 1. 所有权切分（02 保留序列骨架，03 接管面板内部）

| 归属 | 元素 |
| --- | --- |
| 02（骨架，B 基线 `.msg` 同值代演于本演示） | `article.p03-msg` 的对齐（`data-role` 自/尾）、`max-width:78%`、组间 16px/组内 6px（`data-cont`）、`data-id`、日期行插入 |
| 03（本体） | `.p03-bub` 面板内部（句单元/占位）、`.p03-meta` 元信息行、入场类、失败重试交互、公共切句实现 |

**可编程角色**：方向只读 `data-role`（读屏/测试不得依赖左右位置推断）；`data-id`、`data-status`、`bub[data-units]`、`span[data-si]` 均为稳定测试钩子。

## 2. HTML 块（前缀 `p03-`）

```html
<article class="p03-msg" data-role="user|assistant" data-id="…" data-status="ok|sending|failed" [data-cont]>
  <div class="p03-bub" data-units="N">
    <span class="p03-sent" data-si="0">第一句。</span><span class="p03-sent" data-si="1">第二句！</span>…
    <!-- 等待首句：<span class="p03-sent p03-sent--wait" data-si="0">…</span>
         沉默/兜底：<span class="p03-sent p03-sent--blank" data-si="0">（沉默）|（无内容）</span> -->
  </div>
  <div class="p03-meta">
    <span class="p03-mt">14:32</span><span class="p03-ch">QQ</span>
    <span class="p03-st-send">发送中</span> | <span class="p03-st-bad">发送失败 · 点击重试</span> | <span class="p03-st-live">正在回复…</span>
  </div>
</article>
```

句单元是**内容结构不是视觉分隔**：inline、无缝连排、不带可见序号；演示页「分句轮廓」仅为离线核对视图，不随装配。`textContent` 原样赋值——【情绪】/(动作) 标记、ZWJ 组合、`&<>` 均不被转义或拆坏。

## 3. CSS 块（依赖全页 `:root` 共享 token，不自造曲线/配色）

```css
.p03-bub{padding:10px 14px;font-size:14px;line-height:1.75;color:var(--tx);background:var(--panel);
  border:1px solid var(--line);border-radius:3px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:normal}
.p03-msg[data-role="assistant"] .p03-bub{clip-path:polygon(13px 0,100% 0,100% 100%,0 100%,0 13px)}
.p03-msg[data-role="user"] .p03-bub{background:var(--panel-user);border-color:var(--line-user);
  clip-path:polygon(0 0,calc(100% - 13px) 0,100% 13px,100% 100%,0 100%);transform-origin:100% 100%}
.p03-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:5px;font:10.5px/1 var(--mono);letter-spacing:.05em;color:var(--dim)}
/* 失败（规则在前）：press 挂面板 .p03-bub；命中区＝整条 article（本体或状态行均可点） */
.p03-msg[data-status="failed"]{cursor:pointer}
.p03-msg[data-status="failed"] .p03-bub{border-color:#5a3831;transition:transform 160ms var(--ease-out),border-color 140ms ease}
.p03-msg[data-status="failed"]:active .p03-bub{transform:scale(.97)}
/* 入场（规则在后，失败+入场同在时清单覆盖全部所需属性） */
.p03-msg.p03-enter .p03-bub{transition:opacity 180ms var(--ease-out),transform 160ms var(--ease-out),border-color 140ms ease}
.p03-msg.p03-enter[data-role="user"] .p03-bub{transition-duration:140ms,160ms,140ms}
@starting-style{
  .p03-msg.p03-enter[data-role="assistant"] .p03-bub{opacity:0;transform:translateY(8px)}
  .p03-msg.p03-enter[data-role="user"] .p03-bub{opacity:0;transform:scale(.96)}
}
@media (hover:hover) and (pointer:fine){
  .p03-msg[data-status="failed"]:hover .p03-bub{border-color:#6d443b}
  .p03-msg[data-status="failed"]:hover .p03-st-bad{color:#e08a75}
}
@media (prefers-reduced-motion:reduce){
  .p03-msg.p03-enter .p03-bub{transition:opacity 160ms var(--ease-out)}
  @starting-style{.p03-msg.p03-enter .p03-bub{opacity:0;transform:none}}
  .p03-msg[data-status="failed"] .p03-bub{transition:border-color 140ms ease}
}
```

工程决定：① transition/press 全挂 `.p03-bub`、入场类挂 article——article 自身永无 transition，状态/字段变化零动效；② transform 统一 160ms（press 内聚 §4，且落按压档），opacity 分档 140/180；③ 失败规则在前、入场规则在后，同特异级时后者的属性清单恰好覆盖两态并存（注入即失败的 Turn）。白名单：仅 `transform`/`opacity`（+`clip-path` 静态例外），无布局属性，无 `transition:all`。

## 4. JS 块（`window.P03`；组装取 part.html 中同名块）

```js
P03.init(bus)                    /* 注入总线；唯一发出 retry:send */
P03.fill(art, turn, opts)        /* 把一轮发言渲染进 02 的 article 骨架；可反复调用＝原位更新 */
  /* opts.entrance  仅实时尾插 true → 加 .p03-enter（一次）；重建/回填/更新一律 false */
  /* opts.revealing 揭示中 → 正文取 turn.shownText；强制无 sending/failed；meta 出「正在回复…」 */
P03.splitSent(text)              /* 公共切句实现（02 未读计数、04 单元边界共用，防漂移） */
P03.fmtTime(ms) -> 'HH:MM'|null  /* null/非数/Invalid → null（字段隐藏） */
```

fill 幂等且原地（节点不动，焦点保留）；重试监听每 article 只绑一次，仅 `dataset.status==='failed'` 时放行（状态先行翻转，连点不重发）；failed 时 article 得 `role="button"`+`tabindex=0`+`aria-label="发送失败，点击重试"`，Enter/空格触发。

## 5. 信号

| 信号 | 方向 | 载荷 | 触发 |
| --- | --- | --- | --- |
| `retry:send({id})` | 03 → 总线（08 执行） | turn id | 点击失败消息本体/状态行，或其聚焦后 Enter/空格；重试期间由 08 置 `status:'sending'` |

消费均为只读渲染参数（Turn 字段经 02 传入），不发信号。

## 6. Turn 数据字段需求（PARTS §2.1 全量）

| 字段 | 消费方式 |
| --- | --- |
| id | `data-id`、retry:send 载荷、04 对齐键 |
| role | 对齐/面板/占位分支（assistant→（沉默）、user→（无内容））；fill 强制 assistant 无 sending/failed |
| text | 非揭示态正文真源；揭示完成时页面所见必须逐字等于它（fill 重渲保真已验证） |
| shownText | 仅 `opts.revealing` 时消费；空串 → 句单元「…」占位 |
| channel | 严格 `==='qq'` 才显示「QQ」徽标；web/非法值（wechat 等）静默隐藏 |
| status | ok/silent → meta 无状态字段；sending/failed 仅 user 侧；failed 加交互态 |
| time | number（ms）→ HH:MM；null/NaN/Invalid → 字段隐藏；全部字段缺省 → meta 整行不渲染 |

## 7. 与 02 / 04 / 08 的耦合

**02（宿主）**
- 尾插快路（inject 命中队尾）：`P03.fill(newArticle, t, {entrance:true})`；**仅此路径入场**——`renderList`/`loadDone`/乱序中插/`update` 一律 `entrance:false`（同内容只入场一次）。
- `update` 建议改为对既有 article 调 `P03.fill`（原地、保焦点）替代 `replaceWith(fresh)`；若保留 replaceWith，fill 语义不变。
- `revealProgress/revealDone`（04 信号）：改 shownText/state 后调 `fill(art,t,{revealing})`——只换内容不重建节点，读位/选择由 02 的 anchorGrowth 维持。
- 切句公共化：02 的未读句计数删本地 `splitSent`，改用 `P03.sentences`（= `splitSent`）。
- 0→1 边界与 06 让位时序仍归 02；首条上屏是否补入场由 02 定（renderList 加 entranceId 参数即可）。

**04（逐句揭示流）**
- 单元钩子：`span.p03-sent[data-si]`（0 起、连续）；shownText 必须为完整句单元前缀（progress 语义）。
- 等待占位「…」是**存在的句单元**（data-si=0，`p03-sent--wait`）——沉默轮同理（`p03-sent--blank`），对齐逻辑按 id+si 寻址，占位不等于节点缺席。
- 「正在回复…」挂在 meta（`p03-st-live`），出现/消失时序归 04（fill 调用即生效）；03 决策：揭示中不在正文尾追加省略号。
- 逐句出现的动效（如需）挂 `.p03-sent`，须自守中频档；03 本体不给句单元加任何动效。
- 揭示中的点击（跳过）归 04；B 基线曾把 press 挂揭示气泡，03 不实现，04 可自行在 fill 后挂接。

**08（演示控制器/数据收口）**
- 最坏数据串与 02 dev 同源沿用：`LONG`（≈2000 字）、`URLISH`（无句界 204 字符）、`EMOJI_MIXED`；03 另备 `EMOJI_PURE`（纯 ZWJ 组合）、`'？？？'`（仅标点）、纯空白、单字符。
- 重试剧本：`retry:send` → 08 置 sending → 回执 ok/failed；05 的 `send:failed` 产生的 failed Turn 亦走本部件渲染。

## 8. 时间线（入场一次；播放中一切可点，CSS transition 不阻断命中）

| 元素 | 档 | 时长 | 动作 |
| --- | --- | --- | --- |
| 用户消息 `.p03-bub` | 中频·按压档（#5） | opacity 140 / transform 160 | fade + scale(0.96→1)，无位移，origin 右下 |
| Alice 回复 `.p03-bub` | 中频·小浮层档（#6） | opacity 180 / transform 160 | fade + translateY(8px→0)，自下沿内容流 |
| 失败消息 press | 底线（§2.1） | 160 | `:active` 面板 scale(0.97)，瞬时可达 |
| 状态/字段/重试回执 | — | 0 | 瞬时可读 |

退场：无（移除走重建，instant）。

## 9. reduced-motion 退化（同 ship）

入场折叠为**纯 fade 160ms**（`transform:none`，B 基线 reduce 同值）；press 去掉 transform 过渡（scale 瞬时、反馈仍在）；保留 hover/状态颜色过渡；结构、文案、`role=button` 可达性不变。演示页 `html.sim-reduce` 镜像仅为离线核对伪影，不随装配。

## 10. 验收对照（PRINCIPLES §3 × 03 规格 §6，浏览器实测过）

- 正当性：入场=新内容到达反馈；press=命中反馈；其余零动效 ✓　频率：#5/#6 档内（140/180/160），键盘触发（Enter 重试）零入场动效 ✓
- 曲线/时长：仅 token 曲线 + `ease`（颜色）；全文件无 >300ms（CSSOM 扫描 0 条）、无 keyframes ✓
- 物理：无 scale(0)（起点 0.96）；非 reduce 无纯 fade（有初始 transform）；origin 语义记录在案 ✓
- 可中断/一次性：transition 可中断不叠加；重建/原位更新实测不重放（enter 计数不变）✓
- 白名单/门控/reduce：transform+opacity only；hover 全部 `@media(hover:hover) and (pointer:fine)`；reduce 纯 fade ✓
- 数据压力：2000 字（1900 字实测重渲零丢字）、无句界串折行无横向溢出（scrollWidth=clientWidth）、ZWJ 组合 15 码点完整、单字符/仅标点（3 单元）/仅空白（沉默）、连发 10 条同帧入场 ≤180ms 无 stagger、failed 命中区在超长消息上可达、非法字段静默隐藏无 undefined/NaN/Invalid ✓

## 11. 与 `variants/B.html` 占位的差异（组装替换点）

B 的 `.msg/.bubble/.meta` 为整串文本直填（`nodeTurn`/`fillBub` 占位），入场 180/140ms 双属性同 duration、press 与入场可能互相覆盖时长。03 升级为：句单元结构 + 全状态矩阵（沉默/等待/兜底/重试键盘可达）+ 入场类挂 article、transition 挂 bub、transform 统一 160ms 的规则排序方案；色板/字体/切角/间距 token 与 B 逐值一致，未引入新方向。组装时以本 FRAGMENT 整体替换 B 的消息渲染块与 02 的 `fillBub`。
