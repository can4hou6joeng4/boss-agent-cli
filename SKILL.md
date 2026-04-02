# boss-agent-cli

> AI Agent 专用的 BOSS 直聘求职 CLI 工具 — 搜索、筛选、打招呼、沟通管理全流程自动化。

## Install

### Skills CLI (Recommended)

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

### Manual Install

```bash
# 克隆到 skills 目录
mkdir -p .agents/skills
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git .agents/skills/boss-agent-cli

# 安装依赖
cd .agents/skills/boss-agent-cli
uv sync --all-extras
uv run patchright install chromium
```

### pip / uv tool

```bash
uv tool install boss-agent-cli
# 或
pip install boss-agent-cli
```

## Setup

```bash
# 首次登录（自动提取浏览器 Cookie，失败则扫码）
boss login

# 验证
boss status
```

## Commands

| Command | Description |
|---------|-------------|
| `boss schema` | 返回全部命令的 JSON 自描述（Agent 首先调用这个） |
| `boss login` | 登录（Cookie 提取 → CDP → 扫码三级降级） |
| `boss status` | 检查登录状态 |
| `boss search <query>` | 搜索职位（支持城市/薪资/经验/学历/行业/规模/融资/福利筛选） |
| `boss recommend` | 个性化推荐 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss greet <sid> <jid>` | 向招聘者打招呼 |
| `boss batch-greet <query>` | 搜索后批量打招呼（上限 10） |
| `boss chat` | 沟通列表（支持筛选和导出 html/md/csv/json） |
| `boss chatmsg <sid>` | 查看聊天消息历史 |
| `boss mark <sid> --label X` | 联系人标签管理（9 种标签） |
| `boss exchange <sid>` | 请求交换手机/微信 |
| `boss interviews` | 面试邀请列表 |
| `boss history` | 浏览历史 |
| `boss export <query>` | 导出搜索结果为 CSV/JSON |
| `boss cities` | 支持城市列表 |
| `boss me` | 个人信息/简历/求职期望/投递记录 |
| `boss doctor` | 环境诊断 |
| `boss logout` | 退出登录 |

## Agent Usage

### Step 1: Discover capabilities

```bash
boss schema
```

Returns a JSON envelope describing all commands, parameters, error codes, and output conventions.

### Step 2: Typical workflow

```bash
boss status                              # Check auth
boss search "golang" --city 杭州          # Search
boss detail <security_id> --job-id <id>  # View details
boss greet <security_id> <job_id>        # Send greeting
boss chat                                # View conversations
boss chatmsg <security_id>              # Read messages
```

### Step 3: Parse output

All commands output structured JSON to stdout:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "search",
  "data": [...],
  "pagination": {"page": 1, "has_more": true},
  "error": null,
  "hints": {"next_actions": ["boss detail <sid>"]}
}
```

- `ok: true` → exit code 0, `data` contains results
- `ok: false` → exit code 1, `error.code` + `error.recovery_action` for auto-recovery
- `hints.next_actions` → suggested next commands

### Error Recovery

| Error Code | Recoverable | Action |
|-----------|-------------|--------|
| AUTH_REQUIRED | Yes | `boss login` |
| AUTH_EXPIRED | Yes | `boss login` |
| TOKEN_REFRESH_FAILED | Yes | `boss login` |
| RATE_LIMITED | Yes | Wait and retry |
| NETWORK_ERROR | Yes | Retry |
| JOB_NOT_FOUND | No | — |
| ALREADY_GREETED | No | — |
| GREET_LIMIT | No | — |

## Output Conventions

- **stdout**: JSON only (structured envelope)
- **stderr**: Logs and progress (controlled by `--log-level`)
- **exit 0**: Success (`ok: true`)
- **exit 1**: Failure (`ok: false`)

## Requirements

- Python >= 3.10
- Chrome browser (for login and high-risk operations)
- macOS / Linux / Windows

## License

MIT
