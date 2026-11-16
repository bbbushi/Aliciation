# UI Skills Lab · 设计技能实验室

19项UI技能的独立演示项目，基于本机2026-11-15安装技能包逐项阅读制作。三个Agent分别负责动效制作、审计与设计探索、平台与组件；主代理统一导航、展示、减少动态效果与生命周期接口。

无外部依赖、无构建链。双击index.html即可打开，也可从仓库根目录运行：

```sh
python3 -m http.server 8818 --bind 127.0.0.1 --directory addons/ui-skills-lab
```

在浏览器访问 http://127.0.0.1:8818/ 。导航可搜索、逐项切换、重置。右上角可减少动态效果，底部展开实现代码。URL hash可直接定位技能。

| 分组 | 技能 |
| --- | --- |
| 动效制作 | animate / choreograph / gestures / apple-design / emil-design-eng |
| 性能与审计 | animation-performance / find-animation-opportunities / improve-animations / remove-animations / review-animations |
| 设计探索 | animation-vocabulary / prototype / break-ui / learn-taste |
| 平台与组件 | animate-expo / mobile-native / write-swift / pick-ui-library / ask-sonner |

动效和手势项直接在浏览器运行；术语、选型、审计与规则沉淀项采用固定样本与确定规则，展示技能的典型成果，不声称实时运行AI。Expo、Swift、Sonner项提供网页行为模型与对应技术栈代码示例，未运行原生应用、Swift编译器或React/Sonner。性能对比仅展示属性与布局机制，不伪报FPS。混装的video-material-sourcing为非UI技能，不在范围内。

所有演示状态仅存在页面内存。没有模型调用、Alice配置/记忆访问或外部服务。不会修改正式前端、旧样例或全局技能。切换演示时会清理该演示的监听、计时器与动画。

验证状态：源代码静态检查与预览资源检查；每项效果的真实浏览器/触屏体验仍待人工确认，不以静态检查代替视觉验收。
