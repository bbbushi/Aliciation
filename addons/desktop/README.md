# Alice 桌面应用

Tauri 2 窗口复用核心公开 CLI 和 HTTP/SSE，不 import Alice 内部。0.1.0 安装版基本流程、0.1.1 备份退出与重开继续启动已获用户真人验收；0.1.2 新增私有 GitHub Releases 手动签名更新，待真人验收及发布。设计和批次见[桌面 App 文档](../../docs/设计文档/桌面App/Alice桌面App-架构与更新设计-v1-20261114.md)。

## macOS 开发包

当前包内含 Python 3.14.5、模型 SDK、网页和人格准备所需源文件；适用 Apple Silicon、macOS 15 及以上。打开 DMG，将 Alice 拖入“应用程序”，再打开应用。无需本机 Python、源码目录或 `.venv`。现有桌面用户沿用 `~/Library/Application Support/Alice/`，不自动导入源码 `user/`；覆盖安装前先正常退出。

产物位于 `build/releases/<build-id>/`，`build/latest-release.json` 指向最新的 `.app`、`.dmg` 和构建报告。报告含源提交/dirty 状态、逐文件资源摘要、平台与依赖版本、组件范围和静态校验结果；同一 build-id 同时写入宿主和冻结后端。包采用 ad-hoc 临时签名，未做 Developer ID 签名与 Apple 公证，属于本机人工验收开发包。

### 更新前备份与恢复（0.1.1）

菜单栏“数据与更新 → 备份与恢复”提供“备份并退出，准备更新”。完成后手动安装新版；再次打开时需确认继续启动当前版本。若想恢复，可先在同一窗口暂停 Alice，选择恢复点、核验并确认恢复。恢复点包含实际存在的本机凭据，文件权限仅供当前用户；更新/恢复退出不新增模型收尾调用。

恢复点位于用户根的 `.maintenance/backups/<id>/`，不递归备份历史恢复点和日志/下载缓存。恢复先暂存核验，再按日志替换，旧数据保留于 `.maintenance/replaced/<operation>/previous/`；未完成恢复会阻止聊天，可明确撤销。不会自动回滚、合并新旧对话或删除旧恢复点。当前 UI 显示最近 20 个恢复点，较早记录仍保留，可按 ID 走公开 CLI 核验/恢复。

维护协议和数据格式现均为 v1；未来数据格式变化需要明确迁移实现，不支持的格式会在打开 Store 前拒绝。0.1.0 不理解新的维护状态，维护期间不要重新打开旧程序。实际恢复与中断撤销演练按用户要求暂缓，不能视为通过。详见[备份与恢复施工记录](../../docs/设计文档/桌面App/更新前备份与失败恢复-实施与人工验收-20261115.md)。

### 私有仓库签名更新（0.1.2）

菜单栏“数据与更新 → 检查更新”读取 `bbbushi/Aliciation` 私有 Releases。点击“打开 GitHub 创建令牌”，创建 fine-grained token，仅选择该仓库，Contents 设为 Read-only；在更新窗口填写。令牌只保存在本次 Alice 进程内存中，可清除，不使用 Codex 的 GitHub 连接，也不使用模型 API Key。每次重开 Alice 需要重新授权。

流程为手动检查、显示说明、下载并验签、确认安装、等待当前任务结束与保存恢复点、保留旧版程序、替换并重启。不在启动时自动联网，不自动回滚数据。目前仅接受 Apple Silicon macOS、维护协议 v1、数据格式 v1 且无需迁移的更新。0.1.1 没有联网更新入口，第一次需手动换成 0.1.2；同版本不会触发升级。

发布者在源码目录执行以下步骤，生成版本 Release 附件及开发通道清单，不会自动上传：

```sh
python3 addons/desktop/dev.py --signer
python3 addons/desktop/packaging/prepare_update_release.py --notes addons/desktop/packaging/release-notes-0.1.2.md
```

默认私钥在 `~/Library/Application Support/Alice Release Keys/updater.key`，仅公钥入库。先将版本附件发布为私有仓库的 `v0.1.2` prerelease，再将 `latest.json` 发布到固定 `desktop-dev` prerelease。后续版本替换通道清单前先上传对应版本附件。详见[私有更新实施与人工验收](../../docs/设计文档/桌面App/应用内签名更新与私有GitHub授权-实施与人工验收-20261116.md)。

在 macOS arm64、CPython 3.14 和已准备好的 `.toolchain/` 工具链环境中构建：

```sh
python3.14 -m venv addons/desktop/packaging/.venv
addons/desktop/packaging/.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r addons/desktop/packaging/requirements-macos-arm64-py314.lock.txt
addons/desktop/packaging/.venv/bin/python addons/desktop/packaging/build_macos.py
```

构建使用 PyInstaller onedir、Cargo release/custom-protocol；固定锁文件与本地 Rust 目录源。脚本按标准 macOS bundle 布局组装应用，不依赖额外 Tauri CLI。图标转换、签名与 DMG 生成需要本机系统服务权限。构建只生成及检查文件，不启动后端、不读取用户配置记忆。详情见[安装包实施与人工验收](../../docs/设计文档/桌面App/macOS安装包-实施与人工验收-20261115.md)。

## 源码原型

需要 Rust/cargo、系统构建工具，以及仓库 `.venv` 中的 Python/OpenAI SDK。没有 Node 或前端打包步骤。macOS 首期为 arm64；Windows 未构建验收。

在仓库根目录执行：

```sh
.venv/bin/python addons/desktop/dev.py --build
# 真人验收时启动：
.venv/bin/python addons/desktop/dev.py
```

`--build` 只编译。启动时使用 macOS `~/Library/Application Support/Alice/` 或 Windows `%LOCALAPPDATA%/Alice/`，首次进入后自行配置模型，不自动复制源码 `user/`。日志在该目录的 `logs/desktop.log`；不要上传含配置和记忆的整个目录。点击关窗/退出等待正常收尾；无自动强杀。普通退出沿用核心原有收尾配置，可能进行既有获准的记忆提取；宿主管道断开则不新增提取。

开发启动器会先寻找系统 cargo，再寻找本项目已忽略的 `.toolchain/native/bin/`，最后兼容旧的 `/private/tmp/alice-desktop-toolchain/`。当前本地工具链放在 `.toolchain/`，官方 Rust 组件及 `Cargo.lock` 固定的依赖均核验下载摘要；Cargo 使用本地目录源，避免临时缓存清理后重复恢复。`Cargo.lock` 锁定依赖，使用 `--locked` 构建。

主导航“设置”下面的电源图标用于退出；应用内确认后等待后端收尾及进程结束，再关闭桌面窗口。取消确认继续使用。旧“设置 → 服务 → 关闭服务”入口已移除。收尾失败仍保留警告窗口供核对。

## 当前交付边界

- 源码原型：同源页面、实际随机端口、核心目录锁、重复点击唤醒、私有管道状态/退出、缺配置先进入设置。
- 管道 v1 支持 `status`、`maintenance_status` 与 `shutdown(reason=quit/update/recovery)`；明确 `updates=false`，不支持任意路径/程序/模型工具。恢复通过固定离线 CLI 在后端实际退出后执行，不向聊天页面授予原生命令权限。
- 外链/新窗口与导入导出、SSE、神经球在系统 WebView 中仍需真人验收；不得因编译成功认定可用。
- QQ、浏览器、B站与协作者在源码模式沿用既有独立组件；冻结模式不把冻结程序当 Python 解释器调用，未打包组件返回不可用，不自动改用户许可。
- `packaging/resource-manifest.json` v4 是资源与组件清单，包含实现摘要所需源文件；0.1.2 冻结后端、`.app`、`.dmg` 及签名更新归档已生成。联网授权、双版本安装与重启仍待真人验收；未因编译或文件签名通过认定更新可用。

## 核心公共入口

```sh
# 独立数据目录：必须为绝对路径；--config 本身不改变数据根。
.venv/bin/python -m alice --user-dir /absolute/alice-profile serve --no-open
```

`desktop-host` 由原生宿主用固定命令启动，启动随机标识通过继承环境传入，stdin/stdout 专用于控制帧，日志走 stderr。普通 shell 用户不应手工调用它来冒充 GUI。目录锁文件 `.alice-instance.lock` 会保留，锁由 OS 自动释放；不要删除锁文件来“解锁”。旧版本不认识锁，迁移/升级前仍须正常关闭旧服务。
