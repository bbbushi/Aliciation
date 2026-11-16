# 01 会话头栏 · 组装契约（方向 B 舱壁）

> 上游：`parts/01-会话头栏.md`（内容规格）、`PARTS.md §2`（总线）、`PRINCIPLES.md`（工艺宪法）。
> 视觉语言取自 `variants/B.html` 头栏段：冰蓝钢色、mono 副题、右上切角徽标与状态章、发丝分隔线——未新增方向语言。
> 演示页 `part.html` 中 `lab-*` 前缀是 mock 环境，**组装时整体丢弃**，只取下面三块。

## 0. 依赖

- 方向 B 共享 token（组装页 `:root` 统一定义，本部件不重复定义）：
  `--bg --srf --line --tx --dim --faint --acc --acc-dim --ok --bad --mono --sans --ease-out`
- 本部件**自带零动画**：CSS 里没有任何 transition/animation，因此也无需独立 reduced-motion 规则（页面级退化由入场容器承担）。

## 1. CSS 块（原样粘贴）

```css
/* ===== 01 会话头栏（p01-*） ===== */
.p01-head{display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:11px 18px;border-bottom:1px solid var(--line);overflow:hidden}
.p01-id{display:flex;align-items:center;gap:11px;min-width:0}
.p01-badge{width:27px;height:27px;flex:none;display:grid;place-items:center;
  background:#16232c;border:1px solid var(--acc-dim);color:var(--acc);font:12px/1 var(--mono);
  clip-path:polygon(0 0,calc(100% - 9px) 0,100% 9px,100% 100%,0 100%)}
.p01-t{display:flex;flex-direction:column;gap:3px;min-width:0}
.p01-name{font-size:14.5px;font-weight:600;color:#e2e9ee;letter-spacing:.02em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.p01-sub{font:10.5px/1 var(--mono);color:var(--faint);letter-spacing:.12em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.p01-linkwrap{flex:none;margin:0;display:flex}
.p01-link{display:none;font:11px/1 var(--sans);letter-spacing:.03em;color:var(--dim);
  border:1px solid var(--line);border-radius:2px;padding:6px 10px;white-space:nowrap;
  clip-path:polygon(0 0,calc(100% - 7px) 0,100% 7px,100% 100%,0 100%)}
.p01-link::before{content:'●';font-size:7px;margin-right:7px;vertical-align:1px}
/* 可见性白名单：仅 up/closing 各显一条；缺失/非法 → 状态行整体隐藏，不出占位符 */
.p01-head[data-p01-link="up"] .p01-link[data-p01-on="up"],
.p01-head[data-p01-link="closing"] .p01-link[data-p01-on="closing"]{display:block}
.p01-link[data-p01-on="up"]::before{color:var(--ok)}
.p01-link[data-p01-on="closing"]{color:var(--bad);border-color:#5a3831}
.p01-link[data-p01-on="closing"]::before{color:var(--bad)}
```

## 2. HTML 块（置于 08 演示装置之后、02 消息列表之前）

```html
<header class="p01-head" data-p01-link="up" data-p01-abnormal="false" aria-label="会话头栏">
  <div class="p01-id">
    <span class="p01-badge" aria-hidden="true">A</span>
    <div class="p01-t">
      <span class="p01-name">Alice</span>
      <span class="p01-sub">单一会话 · 记忆持续</span>
    </div>
  </div>
  <p class="p01-linkwrap" role="status">
    <span class="p01-link" data-p01-on="up">本机已连接</span>
    <span class="p01-link" data-p01-on="closing">服务正在关闭</span>
  </p>
</header>
```

初始 `data-p01-link="up"` 写死在标记里：首帧无 JS 也正确；随后一切切换由 JS块 驱动。

## 3. JS 块

```js
function p01Render(state){
  const head=document.querySelector('.p01-head');
  if(!head)return;
  const nameEl=head.querySelector('.p01-name');
  const title=(typeof state.sessionTitle==='string')?state.sessionTitle:'Alice';
  nameEl.textContent=title;
  /* 信息可达：全文经平台标准悬浮提示属性；仅在实际发生截断时挂载 */
  if(nameEl.scrollWidth>nameEl.clientWidth+1)nameEl.setAttribute('title',title);
  else nameEl.removeAttribute('title');
  /* linkState 规范化：非法/缺失 → unknown（CSS 白名单使状态行整体隐藏，不出 "undefined"） */
  const link=(state.linkState==='up'||state.linkState==='closing')?state.linkState:'unknown';
  head.dataset.p01Link=link;
  head.dataset.p01Abnormal=(link==='closing')?'true':'false';
}
```

**接线（08/组装方负责调用）**

- 初始：`p01Render({sessionTitle, linkState})`（总线快照，PARTS §2.2）
- 订阅：`link:change` → `p01Render({sessionTitle: 当前值, linkState: 新值})`
- 窗口 resize 后重调一次（刷新截断提示的按需挂载；部件自身不挂常驻监听）
- **发出：无**（规格 §4——无主动交互、无对外信号）

## 4. 与 02 / 08 / 05 的状态耦合

- **02**：无耦合。listState（loading/empty/ready）不触达 01——状态矩阵"空/加载不影响头栏任何元素"；02 宿主重渲染（切档/回填）时不得重建或重放入场中的头栏。
- **08**：linkState 的唯一写入者（`link:change`，含演示抖动剧本）；sessionTitle 恒为缺省 "Alice"，2000 字注入只是演示装置能力，不是产品态。
- **05**：与 01 同源消费 linkState——01 只呈现（状态章"服务正在关闭"），05 消费它锁输入并展示自己的处理类提示；两处文案不同、各自归属（PARTS §2.4 判例：连接类归 01、处理类归 05）。

## 5. 动效决定表（PRINCIPLES 对档记录）

| 交互 | 档 | 决定 |
| --- | --- | --- |
| 首次加载 | 罕见 #17 | 部件自身零入场，随页面容器一次性入场（B 基准 `.app` 220ms）——一容器一入场 |
| linkState up↔closing | 偶发 | **即时切换、零动效**：状态即时可读是底线（§2.1）；display 白名单切换天然无叠字，快翻以最后一次为准 |
| closing 状态点 | — | 静态红点，**不做呼吸/闪烁**（§5 #7 常驻循环禁令，"looks cool"不是目的） |
| 超长标题悬浮提示 | 平台行为 | 原生 `title`，仅在 `scrollWidth` 实测截断时挂载；不加工、不算交互 |
| hover / press | — | 部件无可压元素（规格：纯信息区、无主动交互、不进 Tab 序）；无 hover 动效 |

## 6. 组装注意事项

1. **可编程状态标记**：根元素 `data-p01-link="up|closing|unknown"`（unknown = 缺失/非法 → 状态行整体隐藏，页面永不出现 "undefined" 字样）；`data-p01-abnormal="true|false"` 为派生异常标记（规格 §2"状态语义标记"，供读屏与测试断言）。
2. **读屏**：状态章容器 `role="status"`（隐含 polite），linkState 变化时播报可见文案；徽标纯装饰 `aria-hidden`。头栏无任何 focusable 元素，天然不进 Tab 序——组装时勿往里加按钮。
3. **布局防护**：`.p01-id`/`.p01-t` `min-width:0`，名称/副题单行省略，状态章 `flex:none`——超长标题与窄视口下先省略名称，副题与状态章不被挤出（验收：2000 字注入 + 窄视口两行互不吞并）。
4. **sessionTitle 边界**：非字符串（null/undefined/数字）视为缺失 → 回退缺省 "Alice"；空串是合法字符串，按原样呈现（规格只定义缺失回退，未定义空串隐藏）。
5. **文案唯一来源**：`parts/01 §5` 文案表四条（Alice / 单一会话 · 记忆持续 / 本机已连接 / 服务正在关闭），组装时不得改动；两条状态文案预置在 HTML 中，不散落 JS。
