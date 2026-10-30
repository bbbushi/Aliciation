# B站原生只读适配器

`worker.py` 是 Python 3.11+ 标准库独立子进程，通过下述公开 JSON 行协议与核心通信，不 import `alice`，不自行联网，不读取配置、记忆、凭据或其他应用文件。宿主启动固定脚本并逐请求代发 HTTP；网页、模型和平台响应不能指定程序或新的网络目标。

本版完成匿名视频搜索与实际公开视频简介读取。不包含登录、字幕、音视频下载、评论、点赞、关注、发布或其他写操作；不生成设备指纹、不激活设备、不处理验证码、不重试、不猜测替代端点。平台拒绝或返回异常结构便结束本次操作。

## 启动与消息

宿主使用当前 Python 解释器启动固定 `worker.py`，向 stdin 写入一行 UTF-8 JSON 并 flush；stdout 仅输出协议 JSON 行。每进程只处理一次 search 或 read。宿主管理完整期限、取消、kill/reap 与槽位释放，包括等待 stdin/stdout 时；适配器本身没有重试计时器。

搜索首行：

```json
{"version":1,"op":"search","query":"恒星 演化","limit":5,"response_bytes":1048576,"text_chars":12000}
```

读取首行：

```json
{"version":1,"op":"read","candidate":{"platform":"bilibili","url":"https://www.bilibili.com/video/BV1xx411c7mD/"},"response_bytes":1048576,"text_chars":12000}
```

示例 BV 号仅说明格式；本轮未请求该视频。`candidate` 的 title/snippet 即便存在也不参与读取正文。`limit`、`response_bytes` 由宿主传入用户当前配置，省略时分别为 5、1 MiB；`text_chars` 为宿主证据节选协议参数，Alice 当前传入 12000。三者均须为正整数，单次节选结构边界不作为每日业务额度。首行最多 65536 字节（含换行），后续宿主响应行按 `response_bytes * 2 + 65536` 派生边界，并校验解码正文不超 `response_bytes`；只另受 Python 整数转读取长度的系统表示范围限制。搜索只取一页，不以 limit 触发翻页，返回至多 `min(limit, 本页有效且去重的结果数)`。

适配器向宿主请求单次 HTTP：

```json
{"version":1,"type":"http_request","id":"1","method":"GET","url":"https://api.bilibili.com/x/frontend/finger/spi","headers":{"Accept":"application/json","Referer":"https://www.bilibili.com/"},"max_bytes":1048576,"purpose":"device"}
```

宿主应在每次请求前重核平台许可、取消状态、额度、单请求字节预留与剩余期限，再通过隔离 HTTP worker 请求。适配器不是网络授权边界：宿主独立验证以下完整白名单，拒绝多余参数、重复参数及头字段，固定平台 HTTPS/443 并验证原域名 TLS。直连解析后绑定公网 IP，拒绝私网/保留地址；也可按用户系统配置经过本机 HTTP 代理 CONNECT，由代理解析固定目标域名，不再宣称由 Alice 核验目标 IP。两条路由都不跳转、重试或忽略证书错误，压缩响应仍拒绝。Cookie 只作为本次匿名设备初始化链路中的临时值，不持久化或写入日志。平台返回的任何 URL 均不会被适配器直接请求。

宿主向 stdin 回复一行：

```json
{"version":1,"type":"http_response","id":"1","ok":true,"status":200,"body_base64":"eyJjb2RlIjowLCJkYXRhIjp7fX0=","headers":{}}
```

示例正文只演示封装；它缺少实际设备字段，会被适配器拒绝。`id` 必须逐条匹配。正文为实际响应 UTF-8 JSON 的标准 Base64；`headers` 可省略，适配器不使用 `Set-Cookie`。网络失败/取消/预算不足回复 `ok:false` 即可，后续步骤不再请求；宿主保留自己知道的细分失败原因，适配器仅返回固定 `transport_denied`，不回显响应正文或凭据。

结束时适配器输出一行 result，然后退出：

```json
{"version":1,"type":"result","ok":true,"data":{"candidates":[]}}
```

```json
{"version":1,"type":"result","ok":false,"error":{"code":"platform_rejected","message":"平台接口返回非零业务码，操作已停止"}}
```

失败消息均为固定本地文字；stdout 不输出调试日志。宿主必须区分正常空列表与失败，不把拒绝、超时或协议错误算作“没有搜索结果”。

## 宿主网络白名单

所有请求必须为 GET、无请求体，scheme/host/port 固定 `https://api.bilibili.com:443`，不允许用户信息或 fragment。除表中参数外无其他参数。`Accept` 固定 `application/json`，`Referer` 固定 `https://www.bilibili.com/`；只有 nav/search 允许第三个头 `Cookie`，且必须恰为同进程 spi 返回的 `buvid3=<b_3>`，或在存在 `b_4` 时为 `buvid3=<b_3>; buvid4=<b_4>`。`b_3` 必需，`b_4` 仅在字段缺失时可省略；存在但为 null、空字符串或非法值时仍拒绝。每个值须为 1～256 个符合 [RFC 6265 cookie-octet](https://www.rfc-editor.org/rfc/rfc6265#section-4.1.1) 的可见 ASCII 字符：允许 `+`、`/`、`=`、`%`，禁止空白、控制字符、双引号、裸逗号、分号和反斜杠。值原样回送，不编码、解码、裁剪或补后缀。适配器、宿主请求头校验及宿主 spi 原值绑定遵循同一规则；不允许账号 Cookie、Authorization、用户自定义头或 URL。宿主 HTTP 层可加入自己固定的 User-Agent、Host、Connection 与 Accept-Encoding。

| 操作顺序 | purpose | 路径 | 精确参数 |
| --- | --- | --- | --- |
| search 第 1 次 | device | `/x/frontend/finger/spi` | 无 |
| search 第 2 次 | wbi_keys | `/x/web-interface/nav` | 无 |
| search 第 3 次 | search | `/x/web-interface/wbi/search/type` | `search_type=video`、`keyword=本轮 query 去掉 !'()*`、`page=1`、`order=totalrank`、`wts=当前秒级时间戳`、`w_rid=32 位小写十六进制` |
| read 唯一一次 | video_info | `/x/web-interface/view` | `bvid=本轮候选 URL 提取的 BV 号` |

请求 id 从字符串 `"1"` 顺序递增。search 最多三次，read 最多一次，任一步失败便终止；这些数量来自固定协议步骤，不是每日业务额度。宿主应验证顺序以及 Cookie 与前一步响应的绑定，而非仅检查 Cookie 名称。查询词保持本轮原值，仅按 WBI 规则移除上述五个字符；不能把初始化响应或候选摘要加入 query。签名参数按键排序、值经 URL 编码（空格 `%20`），加入时间戳后使用 nav 两个口令的固定重排结果生成 MD5 `w_rid`；它是公开 Web 协议签名，不是登录身份或 Alice 持有的秘密。nav 的图片样式 URL 只取文件名，不会下载图片。

所有 HTTP 状态必须为 200。JSON `code` 缺失或不是真正整数（包括布尔值、字符串或 null）时返回 `invalid_response`；实际非零业务码返回 `platform_rejected`，不推断其原因，也不因 `data=null` 掩盖这个 API 错误。只有 nav 明确允许匿名状态 `code=-101`，且仍必须返回完整合法 `data.wbi_img.img_url/sub_url`。

对 `code=0` 或 nav 允许的匿名响应，分别检查根对象及 `data` 中的 `v_voucher`：字段缺失、null 或空字符串不触发验证，仍须满足实际资料字段要求；非字符串值返回 `invalid_response`，非空字符串返回 `verification_required` 并结束操作。非空券即使出现在根对象且 `data=null`，也能明确报告验证要求。适配器不会返回远端错误 message、验证券内容或 Cookie，不执行验证码处理或重试；HTTP 200/code 0 本身也不能替代实际结果字段校验。

## 输出与来源语义

搜索 `data.candidates` 的每项：

```json
{"platform":"bilibili","url":"https://www.bilibili.com/video/BV1xx411c7mD/","title":"接口返回的标题","coverage":"search_snippet","snippet":"本次搜索返回的 description"}
```

只接受 `BV` 加 10 个 ASCII 字母数字的视频号，重建规范 URL，丢弃返回的任意跳转地址。标题去掉搜索高亮标签并限制为 300 字符，snippet 限制为 1200 字符；二者为显示/交换字段边界。snippet 只供本轮筛选，不作为已读证据，不应持久化。空 `result:[]` 是正常空结果；缺少 result 不是空结果。适配器不转发搜索响应中的图片、账号头像、广告、风控令牌等字段。

read 的 `data` 直接为证据对象（没有额外 evidence 包装）：

```json
{"platform":"bilibili","url":"https://www.bilibili.com/video/BV1xx411c7mD/","title":"实际 view 响应标题","text":"资料范围说明、元信息与实际公开文字简介","coverage":"video_description_only","fetched_at":"2026-10-30T00:00:00+00:00","content_hash":"实际 text UTF-8 的 SHA256"}
```

必须核验实际响应 bvid 与请求一致。text 首先声明只取得元信息与简介，随后保留实际标题、视频号、存在时的发布者/发布时间/时长及 `desc`。不引用搜索 snippet，不声称观看视频、听过音频或读过字幕。简介为空或常见占位符 `-`/`--` 时返回 `content_unavailable`；即使有标题等元信息也不创建已读正文。`text_chars` 连同范围说明一起计数，容量无法保留实际简介时返回 `text_limit`。正文较长时仅保留配置允许的前段；宿主继续负责证据核验、保存期限、权限撤销、引用绑定和模型判断的来源限制。

## 参考与验证边界

本适配器独立编写，没有引入或执行 `bilibili-api-python`，没有复制其 GPL 代码。公开 Web 端点来自社区作者实际维护的实现/协议记录，并不是 B站提供稳定性承诺的第三方开放 API：

- 搜索与 view 的直接调用参考：[bilibili-mcp-server 固定源码](https://github.com/12qw3er4ty5u-collab/bilibili-mcp-server/blob/5695a636735249b476cc9fc47496bd6eab549c20/server.py)。该实现未提供 WBI 签名，本适配器未沿用其无界 HTTP 行为。
- WBI 规则及固定重排表参考：[bili-subtitle 的 MIT 实现](https://github.com/ymt200120/bili-subtitle/blob/b3f43e4a08bb2cc3580fa19c1fe94d298ca23a30/src/core/wbi.js)与[协议说明](https://github.com/ymt200120/bili-subtitle/blob/b3f43e4a08bb2cc3580fa19c1fe94d298ca23a30/docs/PROTOCOL.md)。这里只独立实现签名步骤，不引入该项目的字幕、账号、缓存或重试行为。
- spi 路径及 `b_3`/可选 `b_4` 响应字段参考：[接口调用作者的实现](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/b5505e3830a2e4b9b8608962f58e96dfe75f23c5/src/dtk/identity/minting/native.py#L130)。本适配器只回送平台本轮颁发的匿名标识，不生成/模拟设备属性。公开[响应示例](https://github.com/pskdje/bilibili-API-collect/blob/main/docs/misc/buvid3_4.md)的 `b_4` 含 `==`，因此不能限定为字母、数字、下划线及连字符。

本轮只进行源码复核与 Python 静态编译，没有调用 B站 API、实际搜索视频、安装依赖、执行外部代码或启动服务。匿名搜索是否在用户网络与平台当前风控状态下可用，仍需获准的真实人工验收；正确签名不保证平台接受请求。字幕须另行明确授权、凭据配置和独立设计，本版本不可用。

固定重排表与协议参考来源的 MIT 许可附记：

```text
MIT License

Copyright (c) 2026 ymt200120

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
