# 上下文观测插件（context-inspector）

固定同源独立页：从一条 Alice 回复进入，查清这轮人格、记忆、前情、历史、工具结果如何进入模型请求，以及哪些材料没有进入、调用后回复有没有被替换。只提供实际记录，不自动评判人格、不猜测模型内部原因。

## 启用

`user/config.toml` 增加配置后重启服务生效（不做热更新）：

```toml
[plugins]
context_inspector_enabled = true   # 固定页面开关，默认关
```

采集开关独立在 `[context]`：`observer_enabled`（元数据，默认开）、
`observer_body_capture`（正文快照，默认关；开启后按 10 个用户请求 · 7 天 · 50MiB 有限额度留存于本机 `user/data/observation/`，不入库、不回灌聊天）。

## 访问

- 页面：`/addons/context-inspector/`
- 从聊天回复旁「查看本轮上下文」进入（携带该回复的 request_id）
- 无 request_id 时只能进入请求列表，不按时间猜测归属

页面与查询只读：读取不触发模型调用、记忆读取、维护或水位变化。
正文只有开启 `observer_body_capture` 后的新请求才留存；更早的调用如实显示「未留存」，
不用当前数据补画历史。来源失效、会话代际变化或撤回后，旧页面停止展示对应正文。
