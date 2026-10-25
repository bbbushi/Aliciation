# Alice — 陪伴型 Harness（MVP）

单一永久会话、三层记忆、可生长人格的陪伴型 AI harness。设计见 [设计思路.md](./设计思路.md)。

## 文档

- [设计思路.md](./设计思路.md) — 本项目的设计定稿（记忆三层、人格两层、真实性三分法）
- [docs/测试指南.md](./docs/测试指南.md) — **测试者入口**：安装、测试动作清单、已知问题、反馈方式
- [docs/迭代路线.md](./docs/迭代路线.md) — **总设计蓝图（迭代锚点）**：设计不变量 + 各代设计蓝图 + 验收维度
- [docs/架构图.png](./docs/架构图.png) — 当前架构全景图（`tools/make_arch_diagram.py` 可重绘）
- [docs/harness研究/通用设计理念.md](./docs/harness研究/通用设计理念.md) — harness 通用解剖（六大组成/四对张力）与编码/陪伴分野
- [docs/harness研究/代表性harness设计.md](./docs/harness研究/代表性harness设计.md) — pi / Claude Code / SWE-agent / OpenClaw 四样本 + 2026 格局（附四张架构图）
- [docs/harness研究/重要论文.md](./docs/harness研究/重要论文.md) — 按主题分组的论文清单（含 Alice 学术血缘标注与阅读路线）
- [docs/harness研究/参考-pi架构.md](./docs/harness研究/参考-pi架构.md) — pi（Mario Zechner）agent 架构参考，Alice 的工程骨架对标
- [docs/harness研究/参考-陪伴设计.md](./docs/harness研究/参考-陪伴设计.md) — Pi（Inflection）与 Character.ai 陪伴设计对比，含可采纳/警示清单
- [tests/growth/README.md](./tests/growth/README.md) — 成长实验室：压缩周期检验成长广度/底色回归/诚实度/拟人度

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml     # 编辑：填 base_url 与模型名
export ALICE_API_KEY="你的 API Key"     # 任何 OpenAI 兼容平台
.venv/bin/python -m alice              # 首次运行会引导选标签生成人格底色
```

需要 Python 3.11+（标准库 `tomllib`）。

## 命令

| 命令 | 作用 |
|---|---|
| `python -m alice` | 对话（同一个永久会话） |
| `python -m alice memory` | 查看记忆库分层与统计（可加 `active`/`warehouse`/`longterm`） |
| `python -m alice recall 关键词` | 手动检索仓库/长期记忆 |
| `python -m alice consolidate` | 立即运行分层维护与七日线生存过滤 |
| `python -m alice evolve` | 立即运行人格演化 |
| `python -m alice distill` | 从记忆提炼技能 |

对话内 `/exit` 退出（退出前会自动补跑一次记忆提取）。

## 数据在哪

全部在 `data/`（不入 git）：

- `data/alice.db` — 对话轮次、记忆、技能、遗忘墓碑（SQLite）
- `data/persona/persona.md` — 当前人格（底色层 + 生长层）
- `data/persona/history/` — 人格历史快照；`CHANGELOG.md` 记录每次演化

## 首次体验建议

1. 随便聊 10 轮以上，退出时自动触发一次记忆提取
2. `python -m alice memory` 看它记住了什么
3. 隔一两天再聊，观察记忆从活跃层进入仓库层（`memory` 命令）
4. 问它「你还记得 XX 吗」，观察 recall 查证与如实回答
5. 人格演化是数据触发：每累计 15 条新记忆自动演化一次（聊天中自动检查），聊得少则不演化；也可 `evolve` 手动触发并查看 CHANGELOG

## 常见问题

- **模型要求**：平台需兼容 OpenAI Chat Completions 且支持 function calling（主流国产平台均支持）。
- **JSON 模式报错**：个别平台不支持 `response_format`，可换模型或反馈。
- **API Key**：从 `config.toml` 的 `api_key_env` 指定的环境变量读取，默认 `ALICE_API_KEY`。
