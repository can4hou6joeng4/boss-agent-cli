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

[快速上手](docs/getting-started.md) · [安装](#-安装) · [快速开始](#-快速开始) · [角色模式](#-角色模式与多平台) · [Agent 集成](#-ai-agent-集成) · [命令参考](#-命令参考) · [排障](#-诊断与排障) · [架构](#-技术架构) · [更新日志](CHANGELOG.md) · [路线图](ROADMAP.md)

**中文** | [English](README.en.md)

<a href="demo/showcase/boss-agent-cli-showcase.mp4" title="观看完整项目展示视频">
  <img src="demo/showcase/boss-agent-cli-showcase.gif" alt="boss-agent-cli 项目展示动图" width="100%">
</a>

**[观看完整展示视频](demo/showcase/boss-agent-cli-showcase.mp4)** · [查看终端交互演示](demo-zh.gif) · schema 驱动 · 福利筛选 · JSON 信封 · 开源工程质量

<p align="center">
  <img src="demo-zh.gif" alt="boss-agent-cli 终端交互演示（1280×720 / 30fps）" width="100%">
</p>

</div>

> A local-assist CLI for AI Agents working around [BOSS Zhipin](https://www.zhipin.com/) data already available to the user. Default low-risk mode is read-only first, user-triggered, and does not automate outreach, bulk actions, risk-control bypasses, or candidate personal-data workflows. See [README.en.md](README.en.md) for the English version.

## ⚠️ 合规边界

本项目默认启用低风险辅助模式，目标是收缩为“本地辅助 / 只读优先 / 用户主动触发 / 不规避风控 / 不批量触达 / 不抓取平台数据”的低风险工具。CLI 默认会阻断打招呼、批量打招呼、投递、联系方式交换、招聘者候选人搜索、候选人简历、聊天记录、附件简历请求和消息回复等敏感能力。需要投递、沟通、候选人处理或联系方式交换时，请回到 BOSS 直聘官方页面由用户手动完成。

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
- [命令参考](#-命令参考)
- [诊断与排障](#-诊断与排障)
- [配置](#-配置)
- [技术架构](#-技术架构)
- [贡献](#-贡献)

---

## 🎬 演示素材

| 类型 | 入口 | 适合场景 |
|------|------|----------|
| 项目展示动图 | [首页自动播放 GIF](demo/showcase/boss-agent-cli-showcase.gif) | 快速理解项目定位、schema 驱动、JSON 信封与开源工程质量 |
| 完整展示视频 | [16 秒 MP4](demo/showcase/boss-agent-cli-showcase.mp4) | 查看更清晰、更完整的项目叙事 |
| 终端交互演示 | [终端 GIF](demo-zh.gif) · [VHS 录制脚本](demo-zh.tape) | 直接观察 CLI 命令和输出形态（1280×720 / 30fps） |
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

- `🔌 多平台抽象`：`Platform` / `RecruiterPlatform` 双注册表已落地；默认低风险模式优先暴露只读和本地辅助链路。命令：`--platform zhipin|zhilian`
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

# 4. 搜索广州的 Golang 职位，要求双休+五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 5. 查看详情 → 加入本地候选池
boss detail <security_id>
boss shortlist add <security_id> <job_id>

# 6. 导出 + 本地统计
boss export "Golang" --city 广州 --count 50 -o jobs.csv
boss stats

# 7. 本地搜索预设（自动增量拉取默认阻断）
boss watch add my-golang "Golang" --city 广州 --welfare "双休"
boss watch list

# 9. 招聘者模式
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
- `boss --platform zhilian login` 已可用，当前覆盖**求职者侧**认证链路
- `boss --platform zhilian` 目前已支持候选者侧 `search / detail / user_info`；推荐流和写操作默认受低风险模式阻断
- `boss --platform zhilian hr ...` 仍不支持，CLI 会直接拒绝执行招聘者侧子命令

涉及 Cookie、CDP、patchright、真实账号、请求频率或平台接口漂移的问题，请先阅读 [平台风险边界](docs/platform-risk.md)。

<details>
<summary>📖 CDP 启动示例</summary>

macOS：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Linux：

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Windows PowerShell：

```powershell
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "Google Chrome executable was not found" }

& $chrome `
  --remote-debugging-port=9222 `
  --remote-allow-origins=* `
  --user-data-dir="$env:LOCALAPPDATA\boss-agent-cdp-profile"
```

启动后在另一个终端使用 CDP 登录：

```bash
boss --cdp-url http://localhost:9222 login --cdp
```

</details>

---

## 🎭 角色模式与多平台

boss-agent-cli 同时覆盖求职者和招聘者两端，并为后续接入更多招聘平台做了抽象。

### 角色切换

| 选项 | 说明 | 典型命令 |
|------|------|----------|
| `--role candidate`（默认） | 求职者视角 | `search` / `detail` / `shortlist` |
| `--role recruiter` | 招聘者视角 | `hr jobs list`；候选人相关敏感命令默认阻断 |

快捷入口：`boss hr <子命令>` 会自动把当前会话切换到招聘者角色，不必显式传 `--role`。

```bash
# 方式 A: --role 显式指定
boss --role recruiter ...

# 方式 B: 招聘者快捷组（自动切换 role）
boss hr applications
boss hr candidates "Golang"
```

注意：
- `boss hr ...` 当前仅支持默认招聘者平台 `zhipin-recruiter`
- 若当前平台是 `zhilian`，CLI 会在入口直接提示切回 `boss --platform zhipin hr ...`

### 多平台抽象

`Platform` / `RecruiterPlatform` 双注册表让命令层不耦合具体平台协议：

| 平台 | 求职者 | 招聘者 | 状态 |
|------|:------:|:------:|------|
| BOSS 直聘 (`zhipin`) | ✅ | ✅ | 默认 |
| 智联招聘 (`zhilian`) | 🟡 候选者侧登录 + 读写链路已接通 | — | 招聘者侧未接入，运行时会直接拒绝 `hr` 子命令 |

```bash
# 指定平台
boss --platform zhilian search "Python"

# 设为默认
boss config set platform zhilian
```

设计细节见 [docs/platform-abstraction.md](docs/platform-abstraction.md)。

---

## 🤖 AI Agent 集成

推荐先阅读：[Agent Quickstart](docs/agent-quickstart.md) · [Host Examples](docs/agent-hosts.md) · [Capability Matrix](docs/capability-matrix.md)

### 方式一：Skill 安装（推荐）

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

安装后 Agent 自动获得调用 `boss` 命令的能力，无需手动配置。

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

## 📚 命令参考

### 基础操作

| 命令 | 说明 |
|------|------|
| `boss schema` | 输出完整工具能力描述 JSON（34 个顶层命令 + hr 分组展开，Agent 首先调用） |
| `boss login` | 四级降级登录 |
| `boss logout` | 退出登录 |
| `boss status` | 检查登录态 |
| `boss doctor` | 诊断环境、依赖、凭据完整性和网络 |
| `boss me` | 我的信息（用户/简历/期望/投递记录） |

### 职位搜索

| 命令 | 说明 |
|------|------|
| `boss search <query>` | 搜索职位（支持 `--welfare` 筛选、`--preset` 预设） |
| `boss recommend` | 受限：默认低风险模式阻断，避免自动读取推荐流 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss cities` | 40 个支持城市 |

### 求职动作

| 命令 | 说明 |
|------|------|
| `boss greet <sid> <jid>` | 受限：默认低风险模式阻断，打招呼请回到平台官网手动完成 |
| `boss batch-greet <query>` | 受限：默认低风险模式阻断，避免批量触达 |
| `boss apply <sid> <jid>` | 受限：默认低风险模式阻断，投递请回到平台官网手动完成 |
| `boss exchange <sid>` | 受限：默认低风险模式阻断，联系方式交换涉及个人信息 |

### 沟通跟进

| 命令 | 说明 |
|------|------|
| `boss chat` | 受限：默认低风险模式阻断，涉及会话数据 |
| `boss chatmsg <sid>` | 受限：默认低风险模式阻断，涉及通信内容 |
| `boss chat-summary <sid>` | 受限：默认低风险模式阻断，依赖通信内容 |
| `boss mark <sid> --label X` | 受限：默认低风险模式阻断，涉及平台关系写入 |
| `boss interviews` | 面试邀请 |
| `boss history` | 浏览历史 |

### 流水线监控

| 命令 | 说明 |
|------|------|
| `boss pipeline` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss follow-up` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss digest` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss watch add/list/remove/run` | add/list/remove 为本地预设；run 默认阻断，避免自动增量拉取平台数据 |
| `boss shortlist add/list/remove` | 候选池 |
| `boss preset add/list/remove` | 搜索预设 |

### 招聘者模式

| 命令 | 说明 |
|------|------|
| `boss hr applications` | 受限：默认低风险模式阻断，涉及候选人投递申请 |
| `boss hr resume <geek_id> --job-id <id> --security-id <id>` | 受限：默认低风险模式阻断，涉及候选人在线简历 |
| `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | 受限：默认低风险模式阻断，涉及联系方式交换 |
| `boss hr chat` | 受限：默认低风险模式阻断，涉及候选人沟通列表 |
| `boss hr chatmsg <friend_id>` | 受限：默认低风险模式阻断，涉及候选人聊天记录 |
| `boss hr last-messages [--friend-id <id>]` | 受限：默认低风险模式阻断，涉及候选人消息摘要 |
| `boss hr jobs list/offline/online` | 职位列表与上下线管理 |
| `boss hr candidates <keyword>` | 受限：默认低风险模式阻断，涉及候选人搜索 |
| `boss hr reply <friend_id> <message>` | 受限：默认低风险模式阻断，回复请回到平台官网手动完成 |
| `boss hr request-resume <friend_id>` | 受限：默认低风险模式阻断，附件简历请求请回到平台官网手动完成 |

### 简历与 AI

| 命令 | 说明 |
|------|------|
| `boss resume init/list/show/edit/delete/export/import/clone/diff/link/applications` | 本地简历管理 |
| `boss ai config` | 配置 AI 服务 |
| `boss ai analyze-jd` | 分析岗位要求 |
| `boss ai polish` | 润色简历 |
| `boss ai optimize` | 针对目标岗位优化 |
| `boss ai suggest` | 求职建议 |
| `boss ai reply` | 生成招聘者消息回复草稿 |
| `boss ai interview-prep` | 基于 JD 生成模拟面试题 |
| `boss ai chat-coach` | 基于聊天记录给沟通建议 |

> 支持 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 等最新模型，详见 [推荐模型与入口](docs/integrations/ai-models.md)。

### 系统管理

| 命令 | 说明 |
|------|------|
| `boss config list/set/reset` | 配置管理 |
| `boss clean` | 清理缓存 |
| `boss stats` | 投递转化漏斗统计（greeted/applied/shortlist） |
| `boss export <query>` | 导出结果（CSV/JSON） |

<details>
<summary>🔎 搜索筛选参数详解</summary>

```bash
boss search "golang" \
  --city 广州 \             # 城市（40 个可选）
  --salary 20-50K \         # 薪资范围
  --experience 3-5年 \      # 经验要求
  --education 本科 \        # 学历要求
  --scale 100-499人 \       # 公司规模
  --industry 互联网 \       # 行业
  --stage 已上市 \          # 融资阶段
  --welfare "双休,五险一金"  # 福利筛选（AND 逻辑）
```

**福利筛选工作原理**：
1. 先检查职位福利标签（`welfareList`）
2. 标签不匹配时自动获取职位描述全文搜索
3. 自动翻页（最多 5 页）
4. 每个结果带 `welfare_match` 说明匹配来源

支持关键词：`双休` `五险一金` `年终奖` `餐补` `住房补贴` `定期体检` `股票期权` `加班补助` `带薪年假`

</details>

---

## 🔧 诊断与排障

```bash
boss doctor
```

| 检查项 | 说明 |
|--------|------|
| `python` | Python 版本 >= 3.10 |
| `patchright` | CLI 已安装 |
| `patchright_chromium` | Chromium 已安装 |
| `cookie_extract` | 本地浏览器 Cookie 可提取 |
| `auth_session` | 登录态存在且可解密 |
| `auth_token_quality` | 核心凭据（wt2 / stoken） |
| `cookie_completeness` | 辅助凭据（wbg / zp_at） |
| `cdp` | Chrome 调试端口可连 |
| `network` | zhipin.com 可访问 |

<details>
<summary>📖 常见问题修复</summary>

```bash
# 安装浏览器内核
patchright install chromium

# 重建登录态
boss logout && boss login

# CDP 诊断
boss --cdp-url http://localhost:9222 doctor
```

**auth_session 显示"损坏"**：登录态来自旧机器指纹或文件损坏 → `boss logout && boss login`

**auth_token_quality 各状态含义**：
- `wt2/stoken 均存在`：完整，可正常使用
- `wt2 存在，stoken 缺失`：部分可用，接口失败时 `boss login` 刷新
- `wt2 缺失`：无效 → `boss logout && boss login`

</details>

<details>
<summary>📖 错误码与自动修复</summary>

| 错误码 | 含义 | Agent 自动修复 |
|--------|------|---------------|
| `AUTH_REQUIRED` | 未登录 | `boss login` |
| `AUTH_EXPIRED` | 登录过期 | `boss login` |
| `RATE_LIMITED` | 频率过高 | 等待后重试 |
| `TOKEN_REFRESH_FAILED` | Token 刷新失败 | `boss login` |
| `ACCOUNT_RISK` | 风控拦截 | 停止自动化访问，回到平台官网手动处理 |
| `COMPLIANCE_BLOCKED` | 默认低风险模式阻断敏感操作 | 回到平台官网手动完成 |
| `INVALID_PARAM` | 参数错误 | 修正参数 |
| `ALREADY_GREETED` | 已打过招呼 | 跳过 |
| `GREET_LIMIT` | 今日次数用完 | 告知用户 |
| `NETWORK_ERROR` | 网络错误 | 重试 |
| `AI_NOT_CONFIGURED` | AI 未配置 | `boss ai config` |
| `PLATFORM_NOT_SUPPORTED` | 当前平台不支持该角色或子命令 | 切换到支持的平台 |

</details>

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
    │       └── ZhilianPlatform (求职者侧登录 + 读写链路已接通，招聘者侧未接入)
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
| 测试 | pytest（1119 项） |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

```bash
# 本地开发
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/ -v    # 运行测试
uv run ruff check src/     # 代码检查
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)

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
