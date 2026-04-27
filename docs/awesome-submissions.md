# Awesome List Submissions

本文件记录 boss-agent-cli 向各大 awesome 列表投稿的模板，供维护者提交用。最后更新：master 当前状态 (2026-04-27)。

## 项目一句话介绍

**中文**：专为 AI Agent 设计的 BOSS 直聘求职 CLI，33 个顶层命令 + 7 个招聘者子命令 + 49 个 MCP 工具，全部输出 JSON，支持 Claude Desktop/Cursor/Windsurf 无缝接入，覆盖求职者与招聘者双端工作流。

**English**: AI-agent-first CLI for BOSS Zhipin. 33 top-level commands + 7 recruiter subcommands + 49 MCP tools, JSON envelope output, typed Python SDK (PEP 561), and out-of-the-box integration for Claude Desktop / Cursor / Windsurf.

## 推荐投稿目标

### 1. [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)

分类：Productivity / Job Search

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - BOSS Zhipin (China's largest recruitment platform) integration for AI agents, exposing 49 MCP tools covering search, greet, chat, pipeline, recruiter ops, resume management, and AI-powered interview prep / chat coaching.
```

### 2. [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

分类：CLI Tools / Skills

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Full job hunt automation on BOSS Zhipin. 33 top-level commands plus recruiter workflow subcommands, 4-tier login fallback, AI resume optimization, chat reply drafts, and interview prep generation.
```

### 3. [awesome-agents](https://github.com/kyrolabs/awesome-agents)

分类：Specialized Agents

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) ![](https://img.shields.io/github/stars/can4hou6joeng4/boss-agent-cli) - Job-hunt CLI purpose-built for AI agents. BOSS Zhipin integration with 33 top-level commands, recruiter workflow subcommands, 49 MCP tools, JSON envelope output, and local-first encrypted storage.
```

### 4. [awesome-python-cli](https://github.com/shinokada/awesome-python-cli)

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Agent-friendly BOSS Zhipin CLI with structured JSON output and 4-tier login fallback. Type-safe Python SDK (PEP 561 `py.typed`).
```

### 5. [awesome-ai-tools](https://github.com/mahseema/awesome-ai-tools)

分类：Agents & Automation

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Let your AI agent handle the job hunt. 33 top-level CLI commands + 7 recruiter subcommands + 49 MCP tools covering search, chat, pipeline, recruiter operations, interview prep, and AI resume coaching on BOSS Zhipin.
```

## 投稿前 Checklist（master 当前状态）

- [x] README 双语（中文 + 英文）
- [x] MIT License
- [x] CI 全绿 + **1119 测试**
- [x] 发布到 PyPI（`pip install boss-agent-cli`，当前 latest release 1.11.0）
- [x] GitHub Release 规范（latest release 1.11.0 已发）
- [x] CHANGELOG 完整
- [x] Code of Conduct + Security Policy
- [x] Issue / PR 模板
- [x] Dependabot 启用
- [x] **codecov badge 已挂，覆盖率约 86%**
- [x] **Python 类型 SDK（PEP 561）**：`from boss_agent_cli import AuthManager, BossClient, ...`
- [x] **mypy / typecheck 阻塞 CI，核心业务模块严格化持续推进**
- [x] **Cursor / Windsurf / Codex / Claude Code 四个 Agent 宿主集成文档**
- [x] 英文贡献者指南（CONTRIBUTING.en.md）
- [x] ≥30 stars（当前 108）
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

1. "33 个顶层命令 + 7 个招聘者子命令 + 49 个 MCP 工具，让 AI Agent 帮你打招呼、投简历、聊 HR、准备面试"
2. "第一个 MCP 就绪、类型安全的中国招聘平台 CLI，为 Claude Desktop / Cursor / Windsurf 设计"
3. "`boss ai interview-prep` — 把 JD 扔进 AI，秒出 10 道模拟面试题"
4. "你只负责描述期望，AI Agent 负责搜、聊、投、跟进"
5. "MIT License，本地加密存储，数据不出机"
6. "1119 测试、49 个 MCP 工具、下游 Python 嵌入零学习成本"

## 实际投稿记录 & 渠道约束（2026-04-27 更新）

| 列表 | 日期 | PR/Issue | 状态 | 接续动作 |
|------|------|---------|------|---------|
| [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | 2026-04-17 | PR #4992 | ✅ 已合并（2026-04-26） | 已完成 Glama / introspection 前置并成功并入列表，无需继续跟进此阻塞项 |
| [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) | — | — | ⚠️ 渠道限制 | **必须通过 Web UI issue 表单**（`/issues/new?template=recommend-resource.yml`），**禁止 gh CLI**（违规会被封禁，见 docs/CONTRIBUTING.md 明文警告） |
| [awesome-agents (kyrolabs)](https://github.com/kyrolabs/awesome-agents) | — | — | 🟢 已具备 traction | 规则明确：brand new repo without demonstrated traction 自动拒绝。当前 108 star 已跨过基础门槛，可进入候选投稿队列 |
| [awesome-ai-tools (mahseema)](https://github.com/mahseema/awesome-ai-tools) | — | — | 🟢 可投 | 无明确门槛，可考虑投稿 Agents & Automation 分区 |
| ~~awesome-python-cli (shinokada)~~ | — | — | ❌ 仓库不存在 | 404，从投稿列表移除 |

### 接续路径

1. **短期**：继续推进尚未提交的 awesome 渠道，优先 `awesome-claude-code`（Web UI 表单）与 `awesome-ai-tools`
2. **中期**：基于当前 traction 继续扩展投稿面，并补视频 / asciinema demo 提高通过率
3. **长期**：把投稿状态和对外素材与每次 release 同步维护，避免再次出现“已完成但仓库文档仍写阻塞中”的漂移

### 投稿渠道约束快查表

| 渠道 | 可用 gh CLI？ | 特殊前置 |
|------|------------|---------|
| awesome-mcp-servers | ✅ | 前置已完成，PR #4992 已合并 |
| awesome-claude-code | ❌ 只能 Web UI 表单 | — |
| awesome-agents (kyrolabs) | ✅ | 明示 traction（star ≥ 50 建议） |
| awesome-ai-tools | ✅ | 无明示 |

> 策略总结：**不强投**——对于设有 traction 门槛或非 CLI 渠道的列表，宁可推迟到条件满足，避免无效 PR 浪费维护者时间。`awesome-mcp-servers` 的 Glama 阻塞已解除并完成合并，后续重点转向其余尚未投递的渠道。
