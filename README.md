<div align="center">

# boss-agent-cli

**专为 AI Agent 设计的 BOSS 直聘本地辅助 CLI 工具**

> 默认低风险模式：本地辅助 · 只读优先 · 用户主动触发 · 不规避风控 · 不批量触达 · 不抓取平台数据
>
> 求职者：搜索 · 福利筛选 · 详情查看 · 候选池 · 本地简历与 AI 辅助

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli/branch/master/graph/badge.svg)](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white&style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)
[![PyPI Downloads](https://img.shields.io/pypi/dm/boss-agent-cli?style=flat-square)](https://pypi.org/project/boss-agent-cli/)
[![Contributors](https://img.shields.io/github/contributors/can4hou6joeng4/boss-agent-cli?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/graphs/contributors)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/can4hou6joeng4/boss-agent-cli/pulls)
[![Open in Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/can4hou6joeng4/boss-agent-cli)

[快速上手](docs/getting-started.md) · [安装](#-安装) · [快速开始](#-快速开始) · [角色模式](#-角色模式与多平台) · [Agent 集成](#-ai-agent-集成) · [命令速查](#-命令速查) · [排障](docs/troubleshooting.md) · [架构](#-技术架构) · [更新日志](CHANGELOG.md) · [路线图](ROADMAP.md)

**中文** | [English](README.en.md)

<a href="demo/showcase/boss-agent-cli-showcase.mp4" title="观看完整项目展示视频">
  <img src="demo/showcase/boss-agent-cli-showcase.gif" alt="boss-agent-cli 项目展示动图" width="100%">
</a>

**[观看完整展示视频](demo/showcase/boss-agent-cli-showcase.mp4)** · [查看终端交互演示](demo/demo-zh.gif) · schema 驱动 · 福利筛选 · JSON 信封 · 开源工程质量

<p align="center">
  <img src="demo/demo-zh.gif" alt="boss-agent-cli 终端交互演示（1280×720 / 30fps）" width="100%">
</p>

</div>

> [!TIP]
> <img src="https://github.com/peterfei/ai-agent-team/raw/main/examples/doloffer.png" alt="Doloffer logo" width="220">
>
> **Doloffer Guide** 致力于让优质 AI 工具的获取更简单。平台主打 GPT 与 Claude 等主流 AI 服务的正版会员充值，提供一站式订阅管理，主打安全稳定与无忧售后。
>
> 💡 **极速订阅**： [专属链接](https://doloffer.com/friend/BEv3yvKS)（输入优惠码 `AI8888` 享 9 折特惠）

> A local-assist CLI for AI Agents working around [BOSS Zhipin](https://www.zhipin.com/) data already available to the user. Default low-risk mode is read-only first, user-triggered, and does not automate outreach, bulk actions, risk-control bypasses, or candidate personal-data workflows. See [README.en.md](README.en.md) for the English version.

## ⚠️ 合规边界

本项目默认启用低风险辅助模式，目标是收缩为"本地辅助 / 只读优先 / 用户主动触发 / 不规避风控 / 不批量触达 / 不抓取平台数据"的低风险工具。CLI 默认会阻断打招呼（greet / batch-greet）、投递、联系方式交换、招聘者候选人搜索、候选人简历、聊天记录、附件简历请求和消息回复等敏感能力，被阻断的命令返回 `COMPLIANCE_BLOCKED` 错误码。需要投递、沟通、候选人处理或联系方式交换时，请回到 BOSS 直聘平台官网由用户手动完成。

---

## 💡 为什么用 boss-agent-cli？

传统求职：打开网页 → 翻几十页 → 逐个看详情 → 手动整理候选岗位 → 忘了跟进谁。

**boss-agent-cli 让 AI Agent 帮你做本地整理和只读辅助**：

```bash
boss search "Golang" --city 广州 --welfare "双休,五险一金"   # 搜索 + 福利筛选
boss detail <security_id>                                    # 查看详情
boss shortlist add <security_id> <job_id>                    # 加入本地候选池
boss stats                                                   # 本地统计
```

所有输出为 **结构化 JSON**，Agent 一调用就能理解；涉及投递、沟通和候选人个人信息处理的动作默认回到平台官网手动完成。

---

## 🧭 导航目录

- [为什么用 boss-agent-cli](#-为什么用-boss-agent-cli)
- [演示素材](#-演示素材)
- [核心能力](#-核心能力)
- [安装](#-安装)
- [快速开始](#-快速开始)
- [登录链路](#-登录链路)
- [角色模式与多平台](#-角色模式与多平台)
- [AI Agent 集成](#-ai-agent-集成)
- [命令速查](#-命令速查)
- [诊断与排障](#-诊断与排障)
- [配置](#-配置)
- [技术架构](#-技术架构)
- [本地存储](#-本地存储)
- [贡献](#-贡献)

---

## 🎬 演示素材

| 类型 | 入口 | 适合场景 |
|------|------|----------|
| 项目展示动图 | [首页自动播放 GIF](demo/showcase/boss-agent-cli-showcase.gif) | 快速理解项目定位、schema 驱动、JSON 信封与开源工程质量 |
| 完整展示视频 | [16 秒 MP4](demo/showcase/boss-agent-cli-showcase.mp4) | 查看更清晰、更完整的项目叙事 |
| 终端交互演示 | [终端 GIF](demo/demo-zh.gif) · [VHS 录制脚本](demo/demo-zh.tape) | 直接观察 CLI 命令和输出形态（1280×720 / 30fps） |
| 可复现源工程 | [HyperFrames 源文件](demo/hyperframes-showcase/) | 维护或迭代 README 首页展示动画 |

---

## 🌟 核心能力

### 求职者工作流

- `🔍 职位发现`：关键词搜索、8 维筛选、按编号回看同一条结果。命令：`search` `show`
- `🎯 福利筛选`：`--welfare "双休,五险一金"` 会自动翻页、补抓详情、按 AND 逻辑做真实匹配。命令：`search --welfare`
- `📌 本地候选池`：查看详情后保存、移除、复盘候选岗位；投递和沟通回到平台官网手动完成。命令：`detail` `show` `shortlist`
- `📊 本地统计`：基于本地缓存查看候选池、投递记录和清理结果。命令：`stats` `shortlist` `clean`
- `👀 本地预设`：保存搜索条件和候选池；自动增量拉取默认阻断。命令：`watch add/list/remove` `preset` `shortlist`
- `💬 沟通边界`：聊天记录、会话摘要、标签和联系方式交换等敏感链路默认阻断；沟通请回到平台官网手动完成。
- `🤖 AI 求职增强`：JD 分析、简历润色、定向优化、模拟面试、沟通指导。命令：`ai analyze-jd` `ai polish` `ai optimize` `ai interview-prep` `ai chat-coach`

### 招聘者工作流

- `👔 招聘者边界`：候选人搜索、投递申请、在线简历、沟通记录、附件简历请求、联系方式交换和消息回复默认阻断，请回到 BOSS 直聘官方招聘者页面手动处理。
- `📌 职位管理`：查看职位、上架、下架，作为招聘者端的最小可操作闭环。命令：`hr jobs list` `hr jobs online` `hr jobs offline`

### 平台与集成基础

- `🔌 多平台抽象`：`Platform` / `RecruiterPlatform` 双注册表已落地；默认低风险模式优先暴露只读和本地辅助链路。命令：`--platform zhipin|zhilian|qiancheng`
- `📤 结构化输出`：stdout 只输出 JSON 信封，适合 CLI 编排、Shell Agent、MCP 和 Python SDK。命令：`schema` `export`
- `🧩 Agent 接入`：同一套能力可通过 Skill、subprocess、MCP、Python SDK 四种路径暴露给 Agent。文档：`docs/agent-quickstart.md` `docs/agent-hosts.md`

---

## 📦 安装

```bash
# 推荐：通过 uv 安装（秒级，自动隔离）
uv tool install boss-agent-cli

# 安装浏览器（仅用于用户主动登录和本地导出场景）
patchright install chromium
```

<details>
<summary>📋 其他安装方式</summary>

```bash
# pipx（隔离环境）
pipx install boss-agent-cli
patchright install chromium

# pip
pip install boss-agent-cli
patchright install chromium

# 从源码（开发用）
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run patchright install chromium
```

</details>

---

## 🚀 快速开始

```bash
# 1. 环境自检
boss doctor

# 2. 登录（按平台选择链路）
boss login

# 3. 验证登录态
boss status

# 可选：查看本地平台注册与能力状态（不触网）
boss platforms
boss platforms --platform qiancheng  # 仅查看单个平台；也支持 51job 别名

# 4. 搜索广州的 Golang 职位，要求双休+五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 5. 查看详情 → 加入本地候选池
boss detail <security_id>
boss shortlist add <security_id> <job_id>

# 6. 导出 + 本地统计
boss export "Golang" --city 广州 --count 50 -o jobs.csv
boss stats

# 7. 招聘者模式
boss hr jobs list                     # 我发布的职位（只读）
# 候选人搜索、简历、聊天、回复、联系方式交换等默认低风险模式会阻断
```

---

## 🔐 登录链路

`boss login` 会按当前平台选择登录链路：

| 平台 | 登录链路 | 说明 |
|------|----------|------|
| `zhipin` | 用户主动登录链路（Cookie / CDP / QR / 浏览器兜底） | 仅用于低风险辅助，不用于规避平台风控 |
| `zhilian` | 用户主动登录链路（Cookie / CDP / 浏览器兜底） | 当前优先复用本地浏览器登录态 |

补充说明：
- `boss login` 默认按当前 `--platform` / 配置文件里的 `platform` 工作
- `boss --platform zhilian login` 已可用，当前覆盖**求职者侧**认证链路；推荐流和写操作默认受低风险模式阻断
- CDP 启动示例与登录类故障见 [诊断与排障](docs/troubleshooting.md)

涉及 Cookie、CDP、patchright、真实账号、请求频率或平台接口漂移的问题，请先阅读 [平台风险边界](docs/platform-risk.md)。

---

## 🎭 角色模式与多平台

| 角色 | 选项 | 典型命令 |
|------|------|----------|
| 求职者（默认） | `--role candidate` | `search` / `detail` / `shortlist` |
| 招聘者 | `--role recruiter`，或 `boss hr <子命令>` 快捷组（自动切换角色） | `hr jobs list`；候选人相关敏感命令默认阻断 |

| 平台 | 求职者 | 招聘者 | 状态 |
|------|:------:|:------:|------|
| BOSS 直聘 (`zhipin`) | ✅ | ✅ | 默认 |
| 智联招聘 (`zhilian`) | 🟡 候选者侧登录 + 读写链路已接通 | — | 招聘者侧未接入，运行时会直接拒绝 `hr` 子命令 |
| 前程无忧 / 51job (`qiancheng`) | 🚧 已注册占位 | — | 统一返回 `NOT_SUPPORTED`，待只读研究门槛满足后再接入真实能力 |

`boss platforms` 会在 JSON 与终端输出中附带 `capability_status_legend`，用于解释能力状态；也可以用 `--capability <capability>` 按现有本地能力矩阵反查平台状态，不会触发登录、浏览器/CDP 或网络请求：

```bash
# 查看哪些平台对 status 是 available / placeholder / blocked_by_policy / not_supported
boss platforms --capability status

# 可与平台过滤组合；51job 会解析为 qiancheng
boss platforms --platform 51job --capability search
```

`--capability` 的 `capability_filter.status_groups` 使用 Agent 友好的归一化状态名：`available`、`placeholder`、`blocked_by_policy`、`not_supported`；每个平台的 `capability_match.raw_status` 保留矩阵原始状态（如 `placeholder_only` / `low_risk_blocked`）。


| 状态 | 语义 |
|------|------|
| `available` | 本地 CLI 已接入该能力；是否需要登录仍以具体命令契约为准 |
| `not_supported` | 当前平台适配器没有实现该真实工作流；CLI 会稳定返回 `NOT_SUPPORTED` |
| `placeholder_only` | 仅用于平台注册、别名、schema/config 可见性；不代表真实平台能力已接入 |
| `low_risk_blocked` | 涉及写操作、敏感数据或平台风险边界；默认低风险模式阻断并提示回到官方页面手动处理 |

```bash
boss --platform zhilian search "Python"   # 指定平台
boss config set platform zhilian          # 设为默认
boss --platform qiancheng status          # 51job 当前仅用于识别平台身份
```

注意：`boss hr ...` 当前仅支持默认招聘者平台 `zhipin-recruiter`；若当前平台是 `zhilian`，CLI 会在入口直接提示切回 `boss --platform zhipin hr ...`。设计细节见 [docs/platform-abstraction.md](docs/platform-abstraction.md)。

---

## 🤖 AI Agent 集成

推荐先阅读：[Agent Quickstart](docs/agent-quickstart.md) · [Host Examples](docs/agent-hosts.md) · [Capability Matrix](docs/capability-matrix.md)

### 方式一：Skill 安装（推荐）

```bash
npx skills add can4hou6joeng4/boss-skill
```

安装后 Agent 自动获得调用 `boss` 命令的能力，无需手动配置。

> 本 CLI 的 Agent Skill 在独立仓库 [boss-skill](https://github.com/can4hou6joeng4/boss-skill) 中管理（仓库本身即 Skill），与 CLI 发版节奏解耦。

### 方式二：手动配置

在 AI Agent 的规则文件中添加：

```markdown
当用户要求搜索职位、整理候选岗位等 BOSS 直聘辅助操作时，通过 Bash 调用 `boss` CLI：
1. 运行 `boss status` 检查登录态
2. 若未登录，运行 `boss login` 提示用户扫码
3. 根据用户意图调用 search / detail / show / shortlist
4. 解析 stdout JSON，`ok` 字段判断成败
5. 用户提到福利要求时使用 `--welfare` 参数
6. 投递、打招呼、交换联系方式、招聘者候选人处理和消息回复必须回到平台官网由用户手动完成
```

### 方式三：Python 直接嵌入（不走 subprocess）

包已随 `py.typed` 标记发布，可直接作为类型化的 Python 库使用：

```python
from boss_agent_cli import AuthManager, BossClient, AuthRequired

auth = AuthManager(data_dir=Path("~/.boss-agent").expanduser())
try:
    with BossClient(auth) as client:
        result = client.search_jobs("Golang", city="广州")
except AuthRequired:
    ...  # 提示用户 boss login
```

公开 API（详见 `boss_agent_cli.__all__`）：`AuthManager` / `BossClient` / `CacheStore` / `JobItem` / `JobDetail` / `AIService` / `ResumeData` 等核心类型。

### 输出协议

所有命令输出 JSON 到 stdout，统一信封格式：

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "search",
  "data": [...],
  "pagination": {"page": 1, "has_more": true, "total": 15},
  "error": null,
  "hints": {"next_actions": ["boss detail <security_id>"]}
}
```

| 约定 | 说明 |
|------|------|
| `stdout` | 仅 JSON 结构化数据 |
| `stderr` | 日志和进度信息 |
| `exit 0` | 命令成功 (`ok=true`) |
| `exit 1` | 命令失败 (`ok=false`) |

---

## 📚 命令速查

`boss schema` 当前暴露 35 个顶层命令和 9 个一级招聘者子命令，按工作流分组：

| 阶段 | 命令 |
|------|------|
| **认证** | `login` · `logout` · `status` · `doctor` |
| **职位发现** | `search` · `detail` · `show` · `cities` · `history` |
| **受限动作** | `greet` · `batch-greet` · `apply` · `exchange` · `mark` 默认低风险模式阻断 |
| **受限跟进链路** | `chat` · `chatmsg` · `chat-summary` · `pipeline` · `follow-up` · `digest` 默认阻断；本地状态用 `stats` |
| **本地整理** | `watch` · `preset` · `shortlist` |
| **简历** | `resume` · `me` |
| **AI** | `ai config` · `ai analyze-jd` · `ai polish` · `ai optimize` · `ai suggest` · `ai reply` · `ai interview-prep` · `ai chat-coach` |
| **系统** | `schema` · `platforms` · `export` · `config` · `clean` |
| **招聘者** | `hr jobs list/offline/online`；候选人数据与消息链路默认阻断 |

完整命令表、参数详解与福利筛选原理见 **[命令参考](docs/commands.md)**；能力真源是 `boss schema`（支持 `--format openai-tools` / `anthropic-tools` 导出工具定义）。

---

## 🔧 诊断与排障

```bash
boss doctor
boss status
# 可选：执行一次低频只读平台验证
boss status --live
boss doctor --live-probe
```

错误信封统一携带 `code` + `recoverable` + `recovery_action`，Agent 可程序化恢复。

Browser Bridge 本地诊断：`bridge_daemon` / `bridge_extension` / `bridge_protocol` / `bridge_workspace` / `bridge_exec` / `bridge_fetch` / `bridge_navigate` 七项检查覆盖 daemon、扩展、tab 与基础浏览器命令健康度，daemon 用 `python -m boss_agent_cli.bridge.daemon --serve` 启动。Bridge 只用于本地诊断、用户主动登录兼容和只读辅助，命中平台风控时停止自动化访问，不要切换通道重试。

完整检查项说明、CDP 启动示例、常见故障修复与错误码表见 **[诊断与排障](docs/troubleshooting.md)**。

---

## ⚙️ 配置

```bash
boss config list            # 查看所有配置
boss config set default_city 广州   # 设置默认城市
boss config reset           # 恢复默认
```

<details>
<summary>📖 完整配置项</summary>

`~/.boss-agent/config.json`：

```json
{
  "default_city": null,
  "default_salary": null,
  "request_delay": [1.5, 3.0],
  "batch_greet_delay": [2.0, 5.0],
  "batch_greet_max": 10,
  "log_level": "error",
  "login_timeout": 120,
  "cdp_url": null,
  "export_dir": null
}
```

| 配置项 | 说明 |
|--------|------|
| `default_city` | 默认城市 |
| `default_salary` | 默认薪资范围 |
| `request_delay` | 请求间隔（秒），`[min, max]` |
| `batch_greet_delay` | 批量打招呼间隔 |
| `batch_greet_max` | 批量打招呼上限 |
| `log_level` | 日志级别（error/warning/info/debug） |
| `login_timeout` | 登录超时（秒） |
| `cdp_url` | CDP 地址 |
| `export_dir` | 导出目录 |

</details>

---

## 🏗️ 技术架构

```
CLI (Click)
    │
    ├── AuthManager ── 用户主动登录态管理（本地加密）
    │       └── TokenStore (Fernet + PBKDF2 机器绑定加密)
    │
    ├── Platform 抽象层（多平台注册表）
    │       ├── BossPlatform (求职者) / BossRecruiterPlatform (招聘者)
    │       ├── ZhilianPlatform (求职者侧登录 + 读写链路已接通，招聘者侧未接入)
    │       └── QianchengPlatform (51job 占位适配器，统一返回 NOT_SUPPORTED)
    │
    ├── Compliance Guardrails ── 默认低风险模式，阻断敏感写操作和候选人个人信息链路
    │
    ├── BossClient / BossRecruiterClient ── httpx + 浏览器兼容通道
    │       ├── RequestThrottle (高斯延迟 + 突发惩罚)
    │       ├── BrowserSession (CDP / Bridge / patchright，兼容保留)
    │       └── BOSS 直聘 wapi (求职者 30 端点 + 招聘者 24 端点，共 54 端点)
    │
    ├── CacheStore (SQLite WAL)
    ├── AIService (OpenAI / Anthropic / 兼容 API)
    └── output.py → JSON 信封 → stdout
```

| 层级 | 选型 |
|------|------|
| 语言 | Python >= 3.10 |
| CLI | Click |
| HTTP | httpx |
| 浏览器 | patchright / CDP / Bridge（兼容登录和导出；不得用于规避平台风控） |
| Cookie | browser-cookie3（10+ 浏览器） |
| 加密 | cryptography (Fernet + PBKDF2) |
| 数据库 | sqlite3 (WAL 模式) |
| 渲染 | rich |
| AI | OpenAI / Anthropic Chat Completions API |
| 测试 | pytest（1400+ 项） |

---

## 🔌 本地存储

所有状态都在 `~/.boss-agent/` 下：加密登录态、搜索缓存、候选池、本地简历与 AI 配置。除显式发起的 API 调用外，数据不离开本机。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

```bash
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
python scripts/quality_baseline.py  # P0 基线/CI 门禁：ruff + 全量离线 pytest + mypy
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)，上手路径见 [docs/getting-started.md](docs/getting-started.md)。

---

## 🙏 致谢

- [geekgeekrun](https://github.com/geekgeekrun/geekgeekrun) — 早期浏览器自动化经验参考
- [boss-cli](https://github.com/jackwener/boss-cli) — CLI 结构化输出 + Agent 友好设计
- [opencli](https://github.com/jackwener/opencli) — Browser Bridge 架构理念

---

## ⚠️ 免责声明

本项目仅用于学习交流和本地辅助，使用时请遵守相关法律法规、BOSS 直聘平台用户协议和隐私政策。默认低风险模式会阻断自动触达、批量操作、规避风控和候选人个人信息处理链路；任何投递、沟通、联系方式交换、招聘者候选人处理都应回到平台官网由用户手动完成。因不当使用产生的一切后果由使用者自行承担，与本项目作者无关。

---

## 📑 许可证

[MIT](LICENSE)

## 👭 友情链接

- [LINUX DO - 新的理想型社区](https://linux.do/)
