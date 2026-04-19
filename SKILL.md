# boss-agent-cli

> AI Agent 专用的 BOSS 直聘求职 CLI 工具 — 32 个命令覆盖搜索、筛选、打招呼、沟通、流水线、简历优化全流程。

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
boss login     # 四级降级：Cookie 提取 → CDP → QR httpx → patchright 扫码
boss status    # 验证登录态
```

## Agent Decision Tree

```
用户意图 → 选择命令链
│
├─ "帮我找工作"
│   → boss status → boss search "关键词" --city X --welfare "Y"
│   → boss detail <sid> → boss greet <sid> <jid>
│
├─ "有什么新职位？"
│   → boss watch run <name>  (已有监控)
│   → boss recommend         (个性化推荐)
│
├─ "我的求职进展怎样？"
│   → boss pipeline → boss follow-up → boss digest
│
├─ "帮我优化简历"
│   → boss ai analyze-jd → boss ai polish → boss ai optimize
│
├─ "查看沟通记录"
│   → boss chat → boss chatmsg <sid> → boss chat-summary <sid>
│
├─ "登录/环境有问题"
│   → boss doctor → boss login
│
└─ "不知道能做什么"
    → boss schema  (返回全部能力 JSON)
```

## Commands (32)

### Discovery & Auth

| Command | Description |
|---------|-------------|
| `boss schema` | 返回全部命令的 JSON 自描述（Agent 首先调用） |
| `boss login` | 四级降级登录（Cookie → CDP → QR httpx → patchright） |
| `boss logout` | 退出登录 |
| `boss status` | 检查登录态 |
| `boss doctor` | 诊断环境、依赖、凭据完整性和网络 |
| `boss me` | 个人信息/简历/求职期望/投递记录 |

### Job Search

| Command | Description |
|---------|-------------|
| `boss search <query>` | 搜索职位（8 维筛选：城市/薪资/经验/学历/规模/行业/融资/福利） |
| `boss recommend` | 个性化推荐 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss cities` | 40 个支持城市 |

### Job Actions

| Command | Description |
|---------|-------------|
| `boss greet <sid> <jid>` | 打招呼 |
| `boss batch-greet <query>` | 批量打招呼（上限 10） |
| `boss apply <sid> <jid>` | 投递/立即沟通（幂等） |
| `boss exchange <sid>` | 交换手机/微信 |

### Communication

| Command | Description |
|---------|-------------|
| `boss chat` | 沟通列表（导出 html/md/csv/json） |
| `boss chatmsg <sid>` | 聊天消息历史 |
| `boss chat-summary <sid>` | 结构化沟通摘要（阶段/待办/风险） |
| `boss mark <sid> --label X` | 标签管理（9 种） |
| `boss interviews` | 面试邀请 |
| `boss history` | 浏览历史 |

### Pipeline & Monitoring

| Command | Description |
|---------|-------------|
| `boss pipeline` | 求职流水线（各阶段状态） |
| `boss follow-up` | 跟进提醒（超时未推进） |
| `boss digest` | 每日摘要 |
| `boss watch add/list/remove/run` | 增量监控 |
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

Returns a JSON envelope describing all 32 commands, parameters, 17 error codes, and output conventions.

### Step 2: Check auth, then act

```bash
boss status                                        # Check auth
boss search "golang" --city 杭州 --welfare "双休"    # Search with welfare filter
boss detail <security_id> --job-id <id>            # View details (fast path)
boss greet <security_id> <job_id>                  # Send greeting
boss pipeline                                      # Track progress
boss digest                                        # Daily summary
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
| ACCOUNT_RISK | Yes | Retry with CDP Chrome |
| NETWORK_ERROR | Yes | Retry |
| AI_NOT_CONFIGURED | Yes | `boss ai config` |
| AI_API_ERROR | Yes | Retry |
| AI_PARSE_ERROR | Yes | Retry |
| EXPORT_FAILED | Yes | Check dependencies |
| JOB_NOT_FOUND | No | — |
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
