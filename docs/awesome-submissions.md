# Awesome List Submissions

本文件记录 boss-agent-cli 向各大 awesome 列表投稿的模板，供维护者提交用。最后更新：v1.9.0 (2026-04-20)。

## 项目一句话介绍

**中文**：专为 AI Agent 设计的 BOSS 直聘求职 CLI，34 命令 + 43 MCP 工具，全部输出 JSON，支持 Claude Desktop/Cursor/Windsurf 无缝接入，mypy 严格化 81%。

**English**: AI-Agent-first CLI for BOSS Zhipin (China's largest job platform). 34 commands + 43 MCP tools, JSON envelope output, type-safe Python SDK (PEP 561), works out of the box with Claude Desktop / Cursor / Windsurf.

## 推荐投稿目标

### 1. [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)

分类：Productivity / Job Search

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - BOSS Zhipin (China's largest recruitment platform) integration for AI agents, exposing 43 MCP tools covering search, greet, chat, pipeline, resume management, and AI-powered interview prep / chat coaching.
```

### 2. [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

分类：CLI Tools / Skills

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Full job hunt automation on BOSS Zhipin. 34 commands output structured JSON, 4-tier login fallback, AI resume optimization, chat reply drafts, interview prep generator.
```

### 3. [awesome-agents](https://github.com/kyrolabs/awesome-agents)

分类：Specialized Agents

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) ![](https://img.shields.io/github/stars/can4hou6joeng4/boss-agent-cli) - Job hunt CLI purpose-built for AI agents. BOSS Zhipin integration with 34 commands + 43 MCP tools, strict mypy typing (81% coverage), JSON envelope output, local-first data storage.
```

### 4. [awesome-python-cli](https://github.com/shinokada/awesome-python-cli)

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Agent-friendly BOSS Zhipin CLI with structured JSON output and 4-tier login fallback. Type-safe Python SDK (PEP 561 `py.typed`).
```

### 5. [awesome-ai-tools](https://github.com/mahseema/awesome-ai-tools)

分类：Agents & Automation

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Let your AI agent handle the job hunt. 34 CLI commands + 43 MCP tools covering search, chat, pipeline, interview prep, and AI resume coaching on BOSS Zhipin. Multi-model support: OpenAI / Claude / DeepSeek / Qwen / Zhipu / OpenRouter.
```

## 投稿前 Checklist（v1.9.0 最新状态）

- [x] README 双语（中文 + 英文）
- [x] MIT License
- [x] CI 全绿 + **927 测试**
- [x] 发布到 PyPI（`pip install boss-agent-cli`，当前 1.9.0）
- [x] GitHub Release 规范（v1.9.0 已发，共 25+ 个 release）
- [x] CHANGELOG 完整
- [x] Code of Conduct + Security Policy
- [x] Issue / PR 模板
- [x] Dependabot 启用
- [x] **codecov badge 已挂，覆盖率 85%**
- [x] **Python 类型 SDK（PEP 561）**：`from boss_agent_cli import AuthManager, BossClient, ...`
- [x] **mypy --strict 阻塞 CI，81% 模块严格化**
- [x] **Cursor / Windsurf / Codex / Claude Code 四个 Agent 宿主集成文档**
- [x] 英文贡献者指南（CONTRIBUTING.en.md）
- [ ] ≥30 stars（目前 19，建议先跑一轮社群曝光再正式投稿，命中率更高）
- [ ] 视频或 asciinema demo

## 推广平台

| 平台 | 形式 | 最佳投递时间 |
|------|------|------|
| **V2EX `/go/python` 或 `/go/programmer`** | 中文技术文章，讲"给 AI 装上求职能力"的故事 | 工作日上午 10 点 |
| **LinuxDo** | 发布到 `开发调优` 类目 | 随时 |
| **掘金 / 思否** | 长文技术博客 | 任意 |
| **HackerNews "Show HN"** | 英文简介 + live demo | 周二 / 周三 北京时间晚上 |
| **Reddit r/ClaudeAI / r/LocalLLaMA** | 英文，突出 MCP 支持 | 周末 |
| **Twitter / X** | 发布 release 时带 gif | 北京时间晚 10 点 |
| **少数派** | 投稿栏目 | 需要审稿 |

## 一句话钩子（A/B 测试素材）

1. "34 个命令 + 43 个 MCP 工具，让 AI Agent 帮你打招呼、投简历、聊 HR、准备面试"
2. "第一个 MCP 就绪、类型安全的中国招聘平台 CLI，为 Claude Desktop / Cursor / Windsurf 设计"
3. "`boss ai interview-prep` — 把 JD 扔进 AI，秒出 10 道模拟面试题"
4. "你只负责描述期望，AI Agent 负责搜、聊、投、跟进"
5. "MIT License，本地加密存储，数据不出机"
6. "mypy 严格化 81%，927 测试，下游 Python 嵌入零学习成本"

## 实际投稿记录 & 渠道约束（2026-04-20 复盘）

| 列表 | 日期 | PR/Issue | 状态 | 接续动作 |
|------|------|---------|------|---------|
| [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | 2026-04-17 | PR #4992 | ⏳ 机器人要求先注册 [Glama.ai](https://glama.ai/mcp/servers) | **阻塞**：需在 Glama.ai 提交 MCP server → 通过 server introspection check → 加 `[![...](...badges/score.svg)](...)` 徽章到 PR → 重新 push |
| [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) | — | — | ⚠️ 渠道限制 | **必须通过 Web UI issue 表单**（`/issues/new?template=recommend-resource.yml`），**禁止 gh CLI**（违规会被封禁，见 docs/CONTRIBUTING.md 明文警告） |
| [awesome-agents (kyrolabs)](https://github.com/kyrolabs/awesome-agents) | — | — | ⚠️ 星数门槛 | 规则明确：brand new repo without demonstrated traction 自动拒绝。当前 19 star 低于阈值，建议达 50+ 后再投 |
| [awesome-ai-tools (mahseema)](https://github.com/mahseema/awesome-ai-tools) | — | — | 🟢 可投 | 无明确门槛，可考虑投稿 Agents & Automation 分区 |
| ~~awesome-python-cli (shinokada)~~ | — | — | ❌ 仓库不存在 | 404，从投稿列表移除 |

### 接续路径

1. **短期**（本会话或下次）：给 [awesome-mcp-servers PR #4992](https://github.com/punkpeye/awesome-mcp-servers/pull/4992) 解锁——到 glama.ai 注册 MCP server，通过 introspection check，回填徽章
2. **中期**（star ≥ 30 后）：集中批量投稿其他 awesome 列表
3. **长期**（star ≥ 50 后）：awesome-agents (kyrolabs) 满足 demonstrated traction

### 投稿渠道约束快查表

| 渠道 | 可用 gh CLI？ | 特殊前置 |
|------|------------|---------|
| awesome-mcp-servers | ✅ | Glama.ai 注册 + 徽章 |
| awesome-claude-code | ❌ 只能 Web UI 表单 | — |
| awesome-agents (kyrolabs) | ✅ | 明示 traction（star ≥ 50 建议） |
| awesome-ai-tools | ✅ | 无明示 |

> 策略总结：**不强投**——对于设有 traction 门槛或非 CLI 渠道的列表，宁可推迟到条件满足，避免无效 PR 浪费维护者时间。核心阻塞是 PR #4992 的 Glama.ai 注册动作，这是下次会话应优先推进的外部动作。
