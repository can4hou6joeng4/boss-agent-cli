<div align="center">

<img src="docs/assets/logo.png" width="112" alt="boss-agent-cli logo">

# boss-agent-cli

*🤖 面向真人与 AI Agent 的招聘平台 CLI —— 纯终端向导 · 福利筛选 · 双角色工作流 · JSON 信封。*

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli/branch/master/graph/badge.svg)](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white&style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)
[![PyPI Downloads](https://img.shields.io/pypi/dm/boss-agent-cli?style=flat-square)](https://pypi.org/project/boss-agent-cli/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/pulls)
[![MCP Toplist](https://mcptoplist.com/badge/glama%2Fcan4hou6joeng4%2Fboss-agent-cli.svg)](https://mcptoplist.com/server/glama%2Fcan4hou6joeng4%2Fboss-agent-cli)

[快速上手](docs/getting-started.md) · [Agent 集成](#-agent-集成) · [命令](#-命令) · [排障](docs/troubleshooting.md) · [路线图](ROADMAP.md) · **中文** | [English](README.en.md)

<a href="demo/showcase/boss-agent-cli-showcase.mp4" title="观看完整项目展示视频">
  <img src="demo/showcase/boss-agent-cli-showcase.gif" alt="boss-agent-cli 项目展示动图" width="100%">
</a>

**[观看完整展示视频](demo/showcase/boss-agent-cli-showcase.mp4)** · [终端交互演示](demo/demo-zh.gif) · schema 驱动 · 福利筛选 · JSON 信封

</div>

<p align="center">
  <a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=boss-agent-cli">
    <img src="docs/assets/atlas-cloud-logo.png" alt="Atlas Cloud" width="180">
  </a>
</p>

> 🎁 **[Atlas Cloud](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=boss-agent-cli)** 为 `boss ai` 提供了一个全模态、OpenAI 兼容的推理入口 —— 一个 key 即可访问 DeepSeek、Qwen、GLM、Kimi、MiniMax、Claude、GPT 等模型，无需逐家接入。在 `boss ai config` 里选用 `--provider atlas`（`base_url=https://api.atlascloud.ai/v1`、默认模型 `deepseek-ai/deepseek-v4-pro`）即可，配置详见 [AI 模型接入](docs/integrations/ai-models.md#atlas-cloud一个-key-覆盖多家模型)；预算友好的 [coding plan](https://www.atlascloud.ai/console/coding-plan)。

> [!TIP]
> <img src="https://github.com/peterfei/ai-agent-team/raw/main/examples/doloffer.png" alt="Doloffer logo" width="220">
>
> **Doloffer Guide** 致力于让优质 AI 工具的获取更简单。平台主打 GPT 与 Claude 等主流 AI 服务的正版会员充值，提供一站式订阅管理，主打安全稳定与无忧售后。
>
> 💡 **极速订阅**： [专属链接](https://doloffer.com/friend/BEv3yvKS)（输入优惠码 `AI8888` 享 9 折特惠）

## 🧭 为什么

boss-agent-cli 把职位发现、福利筛选、本地简历与 AI、投递沟通、招聘者候选人处理和可恢复采集统一到一个 CLI。真人直接运行 `boss` 进入纯终端向导；Agent 使用 JSON、schema、MCP 或 Python API 调用同一套 workflow 与状态。`boss schema` 始终是能力真源。

## ⚠️ 运行边界

历史配置 `operating_mode=assisted|research` 继续兼容，但两种模式都可调用全部已实现能力，不再产生模式级 `COMPLIANCE_BLOCKED`。平台尚未实现的能力仍准确返回 `NOT_SUPPORTED`；认证失效、账号风险和网络错误保留结构化错误与恢复动作。长流程仍受 timeout、retry、预算、checkpoint 和 stop 控制。

## ✨ 核心能力

- **职位发现**：关键词搜索 + 8 维筛选，按编号回看缓存结果 —— `search` `show` `detail`
- **福利筛选（核心差异化）**：`--welfare "双休,五险一金"` 自动翻页补抓、按 AND 逻辑做**真实匹配**，并可 `--sort score` 按本地匹配分排序 —— `search --welfare`
- **纯终端向导**：直接运行 `boss` 或 `boss wizard`，选择角色、平台和目标；同一 workflow 也可用 `--input-json`、run ID 或 MCP 推进和恢复
- **本地候选池与统计**：查看详情后本地保存、读取带有效状态的网页职位收藏并仅同步有效项 / 用标签和备注复盘候选岗位、离线对比、查看漏斗统计 —— `shortlist` `stats` `watch` `preset` `favorites`
- **AI 求职增强 + 本地模型**：JD 分析、简历润色、定向优化、候选池匹配、模拟面试、沟通指导；本地模型权重外置，支持 Ollama/vLLM OpenAI 兼容接口 —— `ai analyze-jd` `ai local configure` `ai local smoke`
- **Schema 驱动 + JSON 信封**：stdout 只输出 `{ok, data, pagination, error, hints}` 信封，`boss schema` 是能力真源，适合 CLI 编排 / Shell Agent / MCP / Python SDK
- **招聘者完整链路**：候选人搜索、推荐牛人、首次招呼及会话红点处理、简历、聊天/最近消息、回复和职位管理 —— `hr candidates/recommendations/greet/applications/resume/chat/last-messages/reply/request-resume/jobs`
- **多平台抽象**：`Platform` / `RecruiterPlatform` 双注册表，`--platform zhipin|zhilian|qiancheng`

## 🚀 快速开始

```bash
# 安装（uv 推荐；浏览器内核仅用于用户主动登录 / 本地导出）
uv tool install boss-agent-cli
patchright install chromium

# 真人入口：直接进入角色、平台和目标向导
boss

# Agent / 高级用户仍可直接调用命令
boss doctor                                                   # 环境自检
boss login                                                    # 登录（按平台选择链路）
boss status                                                   # 验证登录态
boss search "Golang" --city 广州 --welfare "双休,五险一金"     # 搜索 + 福利筛选
boss detail <security_id>                                     # 查看详情
boss shortlist add <security_id> <job_id> --tags 后端,远程    # 加入本地候选池并打本地标签
boss shortlist compare --tag 远程                             # 离线对比候选岗位
boss stats                                                    # 本地统计

# 招聘者模式
boss hr candidates "Python" --city 101010100
boss hr recommendations --job-id <encJobId>
boss hr jobs list
```

所有命令输出结构化 JSON（`ok` 判断成败，`exit 0/1`）。完整上手见 [快速上手](docs/getting-started.md)。

## 🎭 角色与多平台

| 平台 | 求职者 | 招聘者 | 状态 |
|------|:--:|:--:|------|
| BOSS 直聘 (`zhipin`) | ✅ | ✅ | 默认 |
| 智联招聘 (`zhilian`) | ✅ 候选者侧只读 + 本地辅助对等 | 🟡 `agent` browser/CDP 自动化 V1 | `hr` 子命令仍仅限 BOSS；智联招聘者侧通过 `boss --platform zhilian --role recruiter agent ...` 进入 |
| 前程无忧 / 51job (`qiancheng`) | 🚧 已注册占位 | — | 统一返回 `NOT_SUPPORTED`，待只读研究门槛满足后再接入 |

```bash
boss --platform zhilian search "Python"   # 指定平台（也支持 --platform zhipin|zhilian|qiancheng）
boss config set platform zhilian          # 设为默认
```

`boss hr ...` 当前仅支持默认招聘者平台 `zhipin-recruiter`；智联招聘者侧自动化走 `agent` 命令和 browser/CDP adapter。设计细节见 [docs/platform-abstraction.md](docs/platform-abstraction.md)。

## 🤖 Agent 集成

推荐先读：[Agent Quickstart](docs/agent-quickstart.md) · [Capability Matrix](docs/capability-matrix.md) · [Host Examples](docs/agent-hosts.md)

```json
// 方式一：MCP（推荐）—— Claude Desktop / Cursor 等 MCP 宿主，暴露 75 个已实现工具
{ "mcpServers": { "boss-agent": { "command": "uvx", "args": ["--from", "boss-agent-cli[mcp]", "boss-mcp"] } } }
```

不想在本机配 Python 工具链，可用仓库自带的容器：`BOSS_UID=$(id -u) BOSS_GID=$(id -g) docker compose run --rm boss-mcp`。镜像刻意不含浏览器内核 —— 先在宿主机 `boss login`，再把 `~/.boss-agent` 挂进去，详见 [Docker 接入](docs/integrations/docker.md)。

OpenCode 源码项目可直接使用仓库示例：

```bash
cp examples/opencode/opencode.json ./opencode.json
uv sync --all-extras
uv run boss-mcp --data-dir ./.boss-agent --help
```

portable / 全局安装后，在任意 OpenCode 项目里使用 `examples/opencode.json`，它会启动 `boss-mcp --data-dir ./.boss-agent`，让 review、pending、日志按项目隔离。

```bash
# 方式二：subprocess —— 先让 Agent 读能力自描述，再解析 stdout JSON
boss schema
```

```python
# 方式三：Python 直接嵌入（随 py.typed 发布，可作类型化库）
from boss_agent_cli import AuthManager, BossClient, AuthRequired
with BossClient(AuthManager(...)) as client:
    result = client.search_jobs("Golang", city="广州")
```

## 📚 命令

`boss schema` 暴露 39 个顶层命令 + 11 个一级招聘者子命令，按工作流分组：

- **认证**：`login` · `logout` · `status` · `doctor`
- **职位发现**：`search` · `detail` · `show` · `cities` · `history`
- **本地整理**：`watch` · `preset` · `shortlist` · `stats` · `favorites`
- **可恢复采集**：`crawl configure/run/start/status/results/resume/stop/shortlist`
- **简历 / AI**：`resume` · `me` · `ai analyze-jd` · `ai polish` · `ai optimize` · `ai fit` · `ai suggest-keywords` · `ai resume-optimize` · `ai cover-letter` · `ai interview-prep` · `ai chat-coach` · `ai local`
- **系统 / workflow**：`wizard` · `schema` · `platforms` · `export` · `config` · `clean`
- **候选者动作**：`greet` · `batch-greet` · `apply` · `exchange` · `chat*` · `pipeline` · `digest`
- **招聘者**：`hr applications/candidates/recommendations/greet/resume/chat/chatmsg/last-messages/reply/request-resume/jobs`

完整命令表、参数与福利筛选原理见 **[命令参考](docs/commands.md)**；能力真源是 `boss schema`（支持 `--format openai-tools` / `anthropic-tools` 导出工具定义）。

批量采集需要额外安装 `uv sync --extra crawl`。它使用独立的 `<data-dir>/crawl/chrome-profile`，不会接管日常 Chrome profile。默认不注入 Hook；如需使用本地脚本，必须同时显式提供 Hook 档位和包含 `SHA256SUMS` 的目录：

```powershell
boss crawl configure --max-requests 20 --max-details 50 --max-seconds 600 --max-retries 1
boss crawl run "AI" --city 杭州 --pages 3 --with-detail `
  --hook-profile screenshot-full --hook-dir E:\boss-agent-cli-local-hooks\AntiDebug_Breaker
boss crawl resume <run_id>
boss crawl stop <run_id>
boss agent crawl --run-id <run_id> --resume <简历名>
```

`crawl run` 顺序执行并保存 SQLite 断点和 JSON / CSV / XLSX 增量产物；请求数、详情数、墙钟时间和重试均受固定预算约束，`boss crawl stop` 可在下一个安全点停止。导出和 `crawl results` 默认不会暴露 `security_id`、职位 ID 或招聘者字段；执行 `boss clean --privacy` 会删除 crawl 状态、预算和导出。细粒度 MCP crawl tools 读取或导入已有 `run_id`；`boss_wizard` 可通过共享 workflow 启动、恢复和停止任务。出现平台风险码或安全页时停止并返回恢复命令。

## 🩺 诊断与排障

```bash
boss doctor             # 环境自检
boss status --live      # 可选：一次低频只读探测
boss doctor --live-probe
```

错误信封统一携带 `code` + `recoverable` + `recovery_action`，可程序化恢复。Browser Bridge 本地诊断覆盖 `bridge_daemon` / `bridge_extension` / `bridge_protocol` / `bridge_workspace` / `bridge_exec` / `bridge_fetch` / `bridge_navigate` 七项，daemon 用 `python -m boss_agent_cli.bridge.daemon --serve` 启动。Bridge 已连接时，BOSS 的 `chat` / `chatmsg` 优先复用现有浏览器做只读请求且不读取 CLI 保存的 Cookie；Bridge 未连接时保留本地凭据的 httpx 路径。现有浏览器候选耗尽返回 `BROWSER_SESSION_NOT_FOUND` + `boss doctor`，普通未登录路径仍返回 `AUTH_REQUIRED` + `boss login`。两种兼容模式命中平台风控时都停止当前 workflow 并保存 checkpoint；适配器必须有限运行、脱敏、可停止，并只在风险状态解除后显式恢复。

完整检查项、CDP 启动示例与错误码见 **[诊断与排障](docs/troubleshooting.md)**；涉及 Cookie / CDP / patchright / 请求频率 / 接口漂移的问题先读 [平台风险边界](docs/platform-risk.md)。

## ⚙️ 配置

```bash
boss config list                    # 查看所有配置
boss config set log_level debug     # 设置日志级别
boss config reset                   # 恢复默认
```

配置位于 `~/.boss-agent/config.json`：运行模式（`operating_mode=assisted|research`）、请求间隔、批量打招呼间隔、日志级别、CDP 地址、导出目录、平台 / 角色。

## 🏗️ 技术架构

```
CLI (Click)
  └─ 兼容运行元数据（assisted / research 均开放已实现能力）
       └─ AuthManager ── 用户主动登录态（Fernet + PBKDF2 机器绑定加密）
       └─ Platform 双注册表 ── BossPlatform / ZhilianPlatform / QianchengPlatform
       └─ BossClient ── httpx + 节流（高斯延迟）；兼容 CDP / Bridge / patchright 登录与导出
       └─ CacheStore（SQLite WAL） · AIService（OpenAI-compatible / Ollama / vLLM）
            └─ output.py → JSON 信封 → stdout
```

`QianchengPlatform (51job 占位适配器，统一返回 NOT_SUPPORTED)`：仅用于平台注册与 schema 可见性，接真实接口前需满足只读研究门槛。

**不变量**：stdout 仅 JSON 信封 · stderr 仅日志 · `exit 0/1` · 错误含 `code/recoverable/recovery_action` · `boss schema` 为能力真源。
**双受众提示**：`hints.next_actions` 是给 Agent 执行的后继命令，`hints.operator_actions` 是给真人操作者的自然语言指引（扫码、在浏览器里调整条件等需要离开终端完成的动作）；TTY 下只渲染后者到 stderr，Agent 应把它转述给操作者。
**命令还是 wizard**：单次、无状态的能力调用走顶层命令；需要跨步骤状态、可恢复、或中途要把指引递给真人的走 `boss wizard`（goal 取值见 `boss schema` 的 `wizard_catalog`）。
**选型**：Python ≥ 3.10 · Click · httpx · patchright / CDP / Bridge（登录、导出与声明的浏览器 adapter）· cryptography（Fernet）· sqlite3（WAL）· pytest（1600+ 项）。

## 🔌 本地存储

所有状态在 `~/.boss-agent/`：加密登录态、搜索缓存、候选池、本地简历、AI 配置与外置模型登记。模型权重不进入 Python 包；除显式发起的 API 调用或本地模型下载外，数据不离开本机。

## 🤝 贡献者

欢迎 Issue / PR：`git clone` → `feat/xxx` 分支 → 写测试 → `python scripts/quality_baseline.py`（Windows 中文系统可先 `$env:PYTHONUTF8='1'`）→ PR。详见 [CONTRIBUTING.md](CONTRIBUTING.md)，上手路径见 [快速上手](docs/getting-started.md)。

感谢每一位让 boss-agent-cli 变得更好的贡献者，去关注他们！❤️

<a href="https://github.com/can4hou6joeng4/boss-agent-cli/graphs/contributors">
  <img src="./CONTRIBUTORS.svg" alt="贡献者" width="1000" />
</a>

## ❤️ 支持

- 如果它帮到了你，最直接的支持是点一个 [Star ⭐](https://github.com/can4hou6joeng4/boss-agent-cli)，或分享给正在找工作的人。
- 用出问题、有新想法，欢迎提 [Issue](https://github.com/can4hou6joeng4/boss-agent-cli/issues)；想动手就直接上 PR。
- 想看看船队的其他船，欢迎靠泊母港 [bobochang.cn](https://bobochang.cn) 🧭，航海记录在[掘金专栏](https://juejin.cn/user/1187904004821262)。

本项目受益于 [geekgeekrun](https://github.com/geekgeekrun/geekgeekrun) · [boss-cli](https://github.com/jackwener/boss-cli) · [opencli](https://github.com/jackwener/opencli)，一并致谢。

## ⭐ Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/star-history-dark.svg">
  <img alt="Star History" src="docs/assets/star-history.svg" width="100%">
</picture>

由 [mystarhistory](https://github.com/carsteneu/mystarhistory) 本地生成的静态 SVG，与仓库同源、不依赖第三方服务。

## ⚠️ 免责声明

使用时请遵守适用法律、平台协议和隐私要求。对批量触达、候选人个人数据和浏览器适配设置明确的输入、数量、超时和停止条件，并妥善保护本地凭据与导出产物。因不当使用产生的后果由使用者自行承担，与项目作者无关。

## 📑 许可证 & 友情链接

[MIT](LICENSE) © [can4hou6joeng4](https://github.com/can4hou6joeng4) · 友链 [LINUX DO](https://linux.do/)
