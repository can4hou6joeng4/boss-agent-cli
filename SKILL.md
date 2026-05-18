# boss-agent-cli

> AI Agent 专用的 BOSS 直聘本地辅助 CLI 工具 — 34 个顶层命令，默认低风险模式聚焦本地辅助、只读优先、用户主动触发，不做自动触达、批量操作或平台数据抓取。

## Install

### Skills CLI (Recommended)

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

### pip / uv tool

```bash
uv tool install boss-agent-cli
patchright install chromium
```

## Setup

```bash
boss login     # 用户主动登录；兼容 Cookie / CDP / QR / patchright，不用于规避风控
boss status    # 验证登录态
```

## Agent Decision Tree

```
用户意图 → 选择命令链
│
├─ "帮我找工作"
│   → boss status → boss search "关键词" --city X --welfare "Y"
│   → boss detail <sid> → 回到平台官网由用户手动投递或沟通
│
├─ "有什么新职位？"
│   → boss search "关键词" --city X
│   → boss show <编号>
│
├─ "我的求职进展怎样？"
│   → boss shortlist list → boss stats
│
├─ "帮我优化简历"
│   → boss ai analyze-jd → boss ai polish → boss ai optimize
│
├─ "查看沟通记录"
│   → 默认低风险模式阻断平台会话读取；回到平台官网手动查看
│
├─ "登录/环境有问题"
│   → boss doctor → boss login
│
└─ "不知道能做什么"
    → boss schema  (返回全部能力 JSON)
```

## Commands

当前 `boss schema` 暴露：
- **34 个顶层命令**
- **`hr` 下 9 个一级招聘者子命令（敏感候选人数据链路默认阻断）**

### Recruiter Workflow

| Command | Description |
|---------|-------------|
| `boss hr applications` | 查看候选人投递申请 |
| `boss hr candidates <keyword>` | 搜索候选人 |
| `boss hr chat` | 招聘者沟通列表 |
| `boss hr resume` | 查看/请求候选人简历 |
| `boss hr reply <friend_id> <message>` | 回复候选人消息 |
| `boss hr request-resume <friend_id> --job-id <id>` | 请求候选人附件简历 |
| `boss hr jobs list/online/offline` | 职位列表与上下线管理 |

### Discovery & Auth

| Command | Description |
|---------|-------------|
| `boss schema` | 返回全部命令的 JSON 自描述（Agent 首先调用，可加 `--format mcp` 直出 Model Context Protocol 工具集） |
| `boss login` | 四级降级登录（Cookie → CDP → QR httpx → patchright） |
| `boss logout` | 退出登录 |
| `boss status` | 检查登录态 |
| `boss doctor` | 诊断环境、依赖、凭据完整性、网络连通性（含智联端点跨平台探针） |
| `boss me` | 个人信息/简历/求职期望/投递记录 |

### Job Search

| Command | Description |
|---------|-------------|
| `boss search <query>` | 搜索职位（8 维筛选：城市/薪资/经验/学历/规模/行业/融资/福利） |
| `boss recommend` | 受限：默认低风险模式阻断，避免自动读取推荐流 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss cities` | 40 个支持城市 |

### Job Actions

| Command | Description |
|---------|-------------|
| `boss greet <sid> <jid>` | 受限：默认低风险模式阻断，回到平台官网手动完成 |
| `boss batch-greet <query>` | 受限：默认低风险模式阻断，避免批量触达 |
| `boss apply <sid> <jid>` | 受限：默认低风险模式阻断，回到平台官网手动完成 |
| `boss exchange <sid>` | 受限：默认低风险模式阻断，涉及个人信息处理 |

### Communication

| Command | Description |
|---------|-------------|
| `boss chat` | 受限：默认低风险模式阻断，涉及会话数据 |
| `boss chatmsg <sid>` | 受限：默认低风险模式阻断，涉及通信内容 |
| `boss chat-summary <sid>` | 受限：默认低风险模式阻断，依赖通信内容 |
| `boss mark <sid> --label X` | 受限：默认低风险模式阻断，涉及平台关系写入 |
| `boss interviews` | 面试邀请 |
| `boss history` | 浏览历史 |

### Pipeline & Monitoring

| Command | Description |
|---------|-------------|
| `boss pipeline` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss follow-up` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss digest` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss watch add/list/remove/run` | add/list/remove 为本地预设；run 默认阻断，避免自动增量拉取平台数据 |
| `boss shortlist add/list/remove` | 候选池 |
| `boss preset add/list/remove` | 搜索预设 |

### Resume & AI

| Command | Description |
|---------|-------------|
| `boss resume init/list/show/edit/delete/export/import/clone/diff` | 本地简历管理 |
| `boss ai config` | 配置 AI 服务（OpenAI / Anthropic / 兼容 API） |
| `boss ai analyze-jd` | 分析岗位要求 |
| `boss ai polish` | 润色简历 |
| `boss ai optimize` | 针对岗位优化简历 |
| `boss ai suggest` | 求职建议 |
| `boss ai reply` | 招聘者消息回复草稿 |
| `boss ai interview-prep` | 基于 JD 生成模拟面试题 |
| `boss ai chat-coach` | 基于聊天记录给沟通建议 |

### System

| Command | Description |
|---------|-------------|
| `boss config list/set/reset` | 配置管理 |
| `boss clean` | 清理缓存 |
| `boss export <query>` | 导出搜索结果（CSV/JSON） |

## Agent Usage

### Step 1: Discover capabilities

```bash
boss schema
```

Returns a JSON envelope describing all 34 top-level commands, the `hr` recruiter command group, parameters, error codes, and output conventions.

### Step 2: Check auth, then act

```bash
boss status                                        # Check auth
boss search "golang" --city 杭州 --welfare "双休"    # Search with welfare filter
boss detail <security_id> --job-id <id>            # View details (fast path)
boss shortlist add <security_id> <job_id>          # Organize locally
# 投递、沟通、候选人处理请回到平台官网由用户手动完成
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
- `hints.next_actions` → suggested next commands for the Agent to follow

### Error Recovery

| Error Code | Recoverable | Action |
|-----------|-------------|--------|
| AUTH_REQUIRED | Yes | `boss login` |
| AUTH_EXPIRED | Yes | `boss login` |
| TOKEN_REFRESH_FAILED | Yes | `boss login` |
| RATE_LIMITED | Yes | Wait and retry |
| ACCOUNT_RISK | No | Stop automation and use the official website manually |
| NETWORK_ERROR | Yes | Retry |
| AI_NOT_CONFIGURED | Yes | `boss ai config` |
| AI_API_ERROR | Yes | Retry |
| AI_PARSE_ERROR | Yes | Retry |
| EXPORT_FAILED | Yes | Check dependencies |
| JOB_NOT_FOUND | No | — |
| COMPLIANCE_BLOCKED | No | Use local/read-only commands or complete manually on the official website |
| ALREADY_GREETED | No | Skip |
| ALREADY_APPLIED | No | Skip |
| GREET_LIMIT | No | Inform user |
| INVALID_PARAM | No | Fix parameters |
| RESUME_NOT_FOUND | No | Check name |
| RESUME_ALREADY_EXISTS | No | Use different name |

## Output Conventions

- **stdout**: JSON only (structured envelope)
- **stderr**: Logs and progress (controlled by `--log-level`)
- **exit 0**: Success (`ok: true`)
- **exit 1**: Failure (`ok: false`)

## Welfare Filter (Core Feature)

`--welfare "双休,五险一金"` triggers deep inspection:
1. Check job's welfare tags first
2. If tags don't match, fetch full job description and search
3. Auto-paginate (up to 5 pages)
4. Each result includes `welfare_match` field explaining the match source

Keywords: `双休` `五险一金` `年终奖` `餐补` `住房补贴` `定期体检` `股票期权` `加班补助` `带薪年假`

## Requirements

- Python >= 3.10
- patchright + Chromium (for login; QR httpx mode works without browser)
- macOS / Linux / Windows

## Docs

- [Agent Quickstart](docs/agent-quickstart.md)
- [Agent Host Examples](docs/agent-hosts.md)
- [Capability Matrix](docs/capability-matrix.md)

## License

MIT
