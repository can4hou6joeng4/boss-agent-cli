# Awesome List Submissions

本文件记录 boss-agent-cli 向各大 awesome 列表投稿的模板，供维护者提交用。最后更新：master 当前状态 (2026-06-24)。

## 项目一句话介绍

**中文**：专为 AI Agent 设计的 BOSS 直聘求职 CLI，36 个顶层命令 + 9 个招聘者子命令 + 43 个 MCP 工具，全部输出 JSON，支持 Claude Desktop/Cursor/Windsurf/OpenCode 接入，聚焦职位发现、本地候选池整理、AI 辅助与显式入口下的招聘者辅助。

**English**: AI-agent-first CLI for BOSS Zhipin. 36 top-level commands + 9 recruiter subcommands + 43 MCP tools, JSON envelope output, typed Python SDK (PEP 561), and MCP-first integration for Claude Desktop / Cursor / Windsurf / OpenCode.

## 推荐投稿目标

### 1. [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)

分类：Productivity / Job Search

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - BOSS Zhipin integration for AI agents, exposing 35 default low-risk MCP tools for read-only job discovery, job detail inspection, local shortlists, resume helpers, and AI interview preparation.
```

### 2. [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

分类：CLI Tools

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Low-risk job-search CLI for BOSS Zhipin agents. 36 top-level commands plus recruiter workflow subcommands, MCP-first integration, local encrypted storage, OpenCode examples, and official-platform handoff for sensitive actions.
```

### 3. [awesome-agents](https://github.com/kyrolabs/awesome-agents)

分类：Specialized Agents

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) ![](https://img.shields.io/github/stars/can4hou6joeng4/boss-agent-cli) - Job-search CLI purpose-built for AI agents. BOSS Zhipin integration with 36 top-level commands, recruiter workflow subcommands, 43 MCP tools, JSON envelope output, and local-first encrypted storage.
```

### 4. [awesome-python-cli](https://github.com/shinokada/awesome-python-cli)

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Agent-friendly BOSS Zhipin CLI with structured JSON output, MCP integration, and a type-safe Python SDK (PEP 561 `py.typed`).
```

### 5. [awesome-ai-tools](https://github.com/mahseema/awesome-ai-tools)

分类：Agents & Automation

```markdown
- [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli) - Let your AI agent organize the low-risk parts of a job search. 35 top-level CLI commands + 9 recruiter subcommands + 35 default low-risk MCP tools covering search, detail, local shortlist, interview prep, and AI resume coaching on BOSS Zhipin.
```

## 投稿前 Checklist（master 当前状态）

- [x] README 双语（中文 + 英文）
- [x] MIT License
- [x] CI 全绿（pytest / ruff / mypy / CodeQL）
- [x] 发布到 PyPI（`pip install boss-agent-cli`，当前 latest release v1.14.0）
- [x] GitHub Release 规范（latest release v1.14.0 已发）
- [x] CHANGELOG 完整
- [x] Code of Conduct + Security Policy
- [x] Issue / PR 模板
- [x] Dependabot 启用
- [x] **codecov badge 已挂，覆盖率约 86%**
- [x] **Python 类型 SDK（PEP 561）**：`from boss_agent_cli import AuthManager, BossClient, ...`
- [x] **mypy / typecheck 阻塞 CI，核心业务模块严格化持续推进**
- [x] **Cursor / Windsurf / Codex / Claude Code 四个 Agent 宿主集成文档**
- [x] 英文贡献者指南（CONTRIBUTING.en.md）
- [x] GitHub 项目基础资料完整（README、License、Security、贡献指南、release）
- [x] 视频 / 终端录屏 demo（`demo.gif` + `demo.tape` + `demo_showcase.py`）

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

1. "36 个顶层命令 + 9 个招聘者子命令 + 43 个 MCP 工具，让 AI Agent 帮你完成职位发现、本地整理、招聘者辅助和面试准备"
2. "第一个 MCP 就绪、类型安全的中国招聘平台 CLI，为 Claude Desktop / Cursor / Windsurf 设计"
3. "`boss ai interview-prep` — 把 JD 扔进 AI，秒出 10 道模拟面试题"
4. "你只负责描述期望，AI Agent 负责搜索、筛选、整理候选岗位；投递和沟通回到官方页面手动完成"
5. "MIT License，本地加密存储，数据不出机"
6. "CI 质量门禁、43 个 MCP 工具、下游 Python 嵌入零学习成本"

## 实际投稿记录 & 渠道约束（2026-04-28 更新）

| 列表 | 日期 | PR/Issue | 状态 | 接续动作 |
|------|------|---------|------|---------|
| [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | 2026-04-17 | PR #4992 | ✅ 已合并（2026-04-26） | 已完成 Glama / introspection 前置并成功并入列表，无需继续跟进此阻塞项 |
| [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) | — | — | ⚠️ 渠道限制 | **必须通过 Web UI issue 表单**（`/issues/new?template=recommend-resource.yml`），**禁止 gh CLI**（违规会被封禁，见 docs/CONTRIBUTING.md 明文警告） |
| [awesome-agents (kyrolabs)](https://github.com/kyrolabs/awesome-agents) | 2026-04-27 | [PR #423](https://github.com/kyrolabs/awesome-agents/pull/423) | ⚠️ 已关闭（2026-04-28） | 由 `botbocks` 直接关闭，**无 review / 无 comment / 无 merge**；同仓库 `Software Development` 分区近期也有多条投稿被静默关闭，暂不建议立刻重投 |
| [awesome-ai-tools (mahseema)](https://github.com/mahseema/awesome-ai-tools) | 2026-04-27 | [PR #1206](https://github.com/mahseema/awesome-ai-tools/pull/1206) | 🟡 仍为 OPEN | 已按 `Developer tools` 分区提交，继续观察维护者反馈 |
| [awesome-cli-apps-in-a-csv](https://github.com/toolleeo/awesome-cli-apps-in-a-csv) | 2026-05-28 | [PR #276](https://github.com/toolleeo/awesome-cli-apps-in-a-csv/pull/276) | 🟡 仍为 OPEN | 已按仓库要求修改 `data/apps.csv` 并重新生成 README，归入 `Productivity` |
| [awesome-cli-apps](https://github.com/agarrharr/awesome-cli-apps) | 2026-05-28 | [PR #1107](https://github.com/agarrharr/awesome-cli-apps/pull/1107) | 🟡 仍为 OPEN | 已按贡献指南以单应用 PR 投稿，归入 `AI / Agents` |
| [awesome-chinese-ai-agents](https://github.com/FatBy/awesome-chinese-ai-agents) | 2026-05-28 | [PR #2](https://github.com/FatBy/awesome-chinese-ai-agents/pull/2) | 🟡 仍为 OPEN | 已按中文资源格式加入开发工具章节 |
| ~~awesome-python-cli (shinokada)~~ | — | — | ❌ 仓库不存在 | 404，从投稿列表移除 |

### 接续路径

1. **短期**：继续跟进 `awesome-ai-tools` PR #1206；`awesome-agents` PR #423 已转为关闭态观察样本，先不急于重投
2. **中期**：如果 `awesome-agents` 后续出现明确投稿规范、批量 reopen、或新增同类条目被合并，再决定是否带更强 traction / 描述重投
3. **长期**：把投稿状态和对外素材与每次 release 同步维护，避免再次出现“外部状态已变化，但仓库内记录仍滞后”的漂移

### 投稿渠道约束快查表

| 渠道 | 可用 gh CLI？ | 特殊前置 |
|------|------------|---------|
| awesome-mcp-servers | ✅ | 前置已完成，PR #4992 已合并 |
| awesome-claude-code | ❌ 只能 Web UI 表单 | — |
| awesome-agents (kyrolabs) | ✅ | 明示 traction（star ≥ 50 建议）；近期 `Software Development` 分区存在 bot 静默关单现象 |
| awesome-ai-tools | ✅ | 无明示 |

> 策略总结：**不强投**——对于设有 traction 门槛或维护节奏不透明的列表，宁可先观察真实通过模式，也不要反复提交无效 PR。`awesome-mcp-servers` 的 Glama 阻塞已解除并完成合并；`awesome-ai-tools` 仍在待审；`awesome-agents` 已出现 bot 静默关单，后续重点是跟踪规则变化，而不是立即重复投稿。
