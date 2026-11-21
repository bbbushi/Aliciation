# Alice TUI 终端客户端

Alice TUI 连接已运行的本机档案，和同档案的网页或桌面共用对话、人格和记忆。客户端与宿主均使用标准库。 [设计和人工验收](../../docs/设计文档/Alice-TUI-终端客户端-设计-v1-20261121.md)

## 打开终端

```sh
alice                                # 源码默认档案，或 ALICE_USER_DIR
alice tui --desktop                  # macOS 桌面默认档案
alice tui --user-dir /absolute/path  # 指定另一个正在运行的档案
```

也可以用仓库内的 `addons/alice_tui/alice` 启动，或直接运行 `python3 addons/alice_tui`。宿主和客户端需要使用本版协议。关闭终端后，已接单的回复和宿主后台活动继续运行。

## 本机接入

macOS 使用 `getpeereid()`，Linux 使用 `SO_PEERCRED`。宿主在每条连接进入 HTTP 处理前核对对端的有效 UID。同一操作系统账户内的程序可访问档案的终端接口。

Socket 路径格式为 `~/.alice/tui-<profile-id>.sock`，目录权限为 `0700`，socket 权限为 `0600`。档案 ID 是档案规范绝对路径按文件系统编码后，取 SHA-256 的前 24 个十六进制字符。每个档案只提供自己的端点。遇到权限异常或档案服务未运行时，终端显示连接错误。隔离 Lab 宿主使用自己的私有通道。

`/api/tui/v1` 在 Unix socket 上提供四种操作。现行网页、QQ 和 Lab 走各自的入口。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /status` | 查看会话代次、档案是否就绪、前台忙闲和有效清醒／睡眠状态。 |
| `GET /history` | 读取历史和邀请，支持 `after_id` 增量与 `before_id` 向前分页，两种游标互斥。 |
| `POST /chat` | 提交 `text`、`request_id`、`session_epoch` 并流式接收进度。 |
| `POST /chat/status` | 按原始的三个字段核对同一请求。确认未接单时沿用原 ID 重试。 |

## 交互

- `Enter` 发送，`Ctrl+J` 换行，完整的多行粘贴先进入草稿。支持括号粘贴的终端可直接使用。
- `Ctrl+O` 查看工具进度，`Ctrl+C` 停止等待或清空草稿，`Ctrl+D` 关闭 TUI。
- `/history [关键词]` 打开临时历史视图。用 Enter 翻向更早一页，关键词只筛当前页。带限时来源的历史在这里按宿主当前投影显示。
- `/status` 查看本机状态与上一次请求。 `/help` 查看命令。用 `//` 开头发送以斜杠开头的普通消息。
