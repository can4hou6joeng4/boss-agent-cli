<div align="center">

# boss-agent-cli

**专为 AI Agent 设计的 BOSS 直聘求职 CLI 工具**

搜索职位 · 福利筛选 · 个性化推荐 · 自动打招呼 · 导出数据

[![CI](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/can4hou6joeng4/boss-agent-cli/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-≥3.10-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/can4hou6joeng4/boss-agent-cli)](https://github.com/can4hou6joeng4/boss-agent-cli/releases)

[English](#features) · [安装](#安装) · [快速开始](#快速开始) · [AI Agent 集成](#ai-agent-集成) · [命令参考](#命令参考)

<img src="demo.gif" alt="boss-agent-cli 终端演示" width="100%">

</div>

---

## 特性

- **AI Agent 友好** — 所有输出为结构化 JSON，`boss schema` 自描述协议让 Agent 一次调用就理解全部能力
- **福利精准筛选** — `--welfare "双休,五险一金"` 自动翻页逐个检查职位详情，只返回匹配结果
- **免扫码登录** — 优先从本地浏览器提取 Cookie（支持 Chrome/Firefox/Edge 等 10+ 浏览器），失败才弹出扫码
- **反检测登录** — 基于 [patchright](https://github.com/nichochar/patchright)（Playwright 反检测 fork），从二进制层面修补自动化标记
- **智能反爬** — 高斯分布请求延迟 + 指数退避重试，模拟人类操作节奏
- **错误自愈** — 每个错误响应包含 `recovery_action`，Agent 可自动修复

## 安装

```bash
# 安装 CLI 工具（从 GitHub）
uv tool install git+https://github.com/can4hou6joeng4/boss-agent-cli.git

# 安装浏览器（用于登录）
patchright install chromium
```

<details>
<summary>从源码安装</summary>

```bash
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run patchright install chromium
```

</details>

## 登录链路说明

`boss login` 当前采用三级降级：

1. **Cookie 提取**
   - 优先尝试从本机浏览器提取 `zhipin.com` Cookie
   - 适合你已经在 Chrome / Edge / Firefox 中登录过 BOSS 直聘的场景
2. **CDP 登录**
   - 若检测到带远程调试端口的 Chrome，则复用用户浏览器完成登录
   - 适合希望保持浏览器真实指纹、减少额外扫码的场景
3. **patchright 扫码**
   - 最后兜底，拉起 patchright Chromium 让你扫码登录

推荐工作流：

```bash
boss doctor
boss login
boss status
```

### CDP 启动示例

macOS:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Linux:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/boss-chrome
```

然后可执行：

```bash
boss --cdp-url http://localhost:9222 doctor
boss --cdp-url http://localhost:9222 login --cdp
```

## 快速开始

```bash
# 0. 先做环境自检（推荐）
boss doctor

# 1. 登录（优先免扫码，失败弹出浏览器）
boss login

# 2. 验证登录态
boss status

# 3. 搜索广州的 Golang 职位，要求双休+五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 4. 查看职位详情
boss detail <security_id>

# 5. 向招聘者打招呼
boss greet <security_id> <job_id>

# 6. 获取个性化推荐
boss recommend

# 7. 导出 50 条搜索结果为 CSV
boss export "Golang" --city 广州 --count 50 -o jobs.csv
```

## AI Agent 集成

### 方式一：Skill 安装（推荐）

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

安装后 Agent 自动获得调用 `boss` 命令的能力，无需手动配置。

### 方式二：手动配置

在 AI Agent 的规则文件中添加：

```markdown
当用户要求搜索职位、投递、打招呼等 BOSS 直聘操作时，通过 Bash 调用 `boss` CLI：
1. 运行 `boss status` 检查登录态
2. 若未登录，运行 `boss login` 提示用户扫码
3. 根据用户意图调用 search / recommend / detail / greet
4. 解析 stdout JSON，`ok` 字段判断成败
5. 用户提到福利要求时使用 `--welfare` 参数
```

### 输出协议

所有命令输出 JSON 到 stdout：

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

## 命令参考

| 命令 | 说明 |
|------|------|
| `boss schema` | 输出完整能力描述（Agent 首先调用这个） |
| `boss login` | 登录（Cookie 提取 → CDP → 扫码三级降级） |
| `boss status` | 检查登录态 |
| `boss doctor` | 诊断本地环境、依赖、登录态和网络 |
| `boss me` | 我的信息（用户/简历/期望/投递记录） |
| `boss search <query>` | 搜索职位（支持 `--welfare` 福利筛选） |
| `boss recommend` | 个性化推荐 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss greet <sid> <jid>` | 向招聘者打招呼 |
| `boss batch-greet <query>` | 批量打招呼（上限 10） |
| `boss chat` | 沟通列表（支持筛选和导出 html/md/csv/json） |
| `boss chatmsg <sid>` | 查看聊天消息历史 |
| `boss mark <sid> --label X` | 联系人标签管理（9 种标签） |
| `boss exchange <sid>` | 请求交换手机/微信 |
| `boss interviews` | 面试邀请列表 |
| `boss history` | 浏览历史 |
| `boss export <query>` | 导出搜索结果（CSV / JSON） |
| `boss cities` | 列出支持的 40 个城市 |
| `boss logout` | 退出登录 |

### 搜索筛选参数

```bash
boss search "golang" \
  --city 广州 \             # 城市（40 个可选，用 boss cities 查看）
  --salary 20-50K \         # 薪资范围
  --experience 3-5年 \      # 经验要求
  --education 本科 \        # 学历要求
  --scale 100-499人 \       # 公司规模
  --welfare "双休,五险一金"  # 福利筛选（逗号分隔，AND 逻辑）
```

### 福利筛选

`--welfare` 是本工具的核心特色功能：

```bash
# 单条件
boss search "Python" --welfare 双休

# 多条件组合（AND 逻辑，所有条件都必须满足）
boss search "Golang" --city 广州 --welfare "双休,五险一金,年终奖"
```

工作原理：
1. 先检查职位的福利标签（`welfareList`）
2. 标签不匹配时自动调用 `card.json` 获取职位描述全文搜索
3. 自动翻页（最多 5 页）直到找到所有匹配结果
4. 每个结果带 `welfare_match` 字段说明匹配来源

支持的福利关键词：`双休` `五险一金` `年终奖` `餐补` `住房补贴` `定期体检` `股票期权` `加班补助` `带薪年假`

## 诊断与排障

优先执行：

```bash
boss doctor
```

典型诊断项：
- `patchright`：CLI 是否已安装
- `patchright_chromium`：Chromium 内核是否已安装
- `cookie_extract`：是否能从本地浏览器提取 zhipin Cookie
- `auth_session`：本地登录态是否存在、是否可解密
- `auth_token_quality`：当前登录态质量（是否具备 wt2 / stoken）
- `cdp`：Chrome 远程调试端口是否可连
- `network`：是否可访问 `https://www.zhipin.com/`
- `data_dir`：数据目录是否可写

常见修复动作：

```bash
# 安装浏览器内核
patchright install chromium

# 清除损坏/旧机器指纹的登录态
boss logout

# 重新登录
boss login

# 指定 CDP 地址做诊断
boss --cdp-url http://localhost:9222 doctor
```

如果 `doctor` 中 `auth_session` 显示“session 文件但无法解密/已损坏”，通常意味着：
- 登录态来自旧机器指纹
- 或 session 文件已损坏

此时执行 `boss logout && boss login` 即可恢复。

如果 `doctor` 中 `auth_token_quality` 显示：
- `wt2/stoken 均存在`：登录态完整，可优先执行 `boss status`
- `wt2 存在，但 stoken 缺失`：通常仍能读取部分信息；若接口失败，再执行 `boss login`
- `wt2 缺失`：说明关键 Cookie 不完整，建议直接 `boss logout && boss login`

## 错误处理

| 错误码 | 含义 | Agent 自动修复 |
|--------|------|---------------|
| `AUTH_REQUIRED` | 未登录 | `boss login` |
| `AUTH_EXPIRED` | 登录过期 | `boss login` |
| `RATE_LIMITED` | 频率过高 | 等待后重试 |
| `TOKEN_REFRESH_FAILED` | Token 刷新失败 | `boss login` |
| `INVALID_PARAM` | 参数错误 | 修正参数 |
| `ALREADY_GREETED` | 已打过招呼 | 跳过 |
| `GREET_LIMIT` | 今日次数用完 | 告知用户 |
| `NETWORK_ERROR` | 网络错误 | 重试 |

## 配置

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
| `default_city` | 搜索时的默认城市 |
| `default_salary` | 搜索时的默认薪资范围 |
| `request_delay` | 请求间隔范围（秒），`[min, max]` |
| `batch_greet_delay` | 批量打招呼间隔范围（秒） |
| `batch_greet_max` | 批量打招呼上限 |
| `log_level` | 日志级别（error/warning/info/debug） |
| `login_timeout` | 登录超时时间（秒） |
| `cdp_url` | Chrome CDP 地址（如 `http://localhost:9222`） |
| `export_dir` | 导出文件默认保存目录 |

## 技术架构

```
CLI (Click)  →  AuthManager  →  patchright (登录/Token刷新)
                    ↓
              BossClient (httpx)  →  BOSS 直聘 wapi
                    ↓
              CacheStore (SQLite WAL)
                    ↓
              output.py  →  JSON 信封  →  stdout
```

- **认证**：patchright 反检测浏览器扫码 + browser-cookie3 本地提取
- **反爬**：高斯分布延迟 + 指数退避 + stoken 浏览器环境生成
- **缓存**：SQLite WAL 模式，搜索 100 条上限 + 24h TTL
- **加密**：Fernet 对称加密 + PBKDF2 机器绑定密钥

## 致谢

本项目参考了以下优秀开源项目的设计理念：

- [geekgeekrun](https://github.com/geekgeekrun/geekgeekrun) — 浏览器自动化 + 反检测策略
- [boss-cli](https://github.com/jackwener/boss-cli) — CLI 结构化输出 + Agent 友好设计
- [opencli](https://github.com/jackwener/opencli) — Browser Bridge 架构理念

## 许可证

[MIT](LICENSE)
