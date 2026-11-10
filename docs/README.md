# 文档索引

boss-agent-cli 的公开文档都收在这里。第一次接触先从「入门」开始；要接 Agent 或写代码，再看「核心参考」与「Agent 接入」。能力真源始终是 `boss schema`。

## 入门

- [快速上手](getting-started.md) — 安装、登录、第一条命令
- [诊断与排障](troubleshooting.md) — 常见错误、CDP / patchright、错误码与恢复动作

## 核心参考

- [命令参考](commands.md) — 全部命令、参数与 JSON 信封
- [能力矩阵](capability-matrix.md) — CLI / JSON / MCP / Python API 的能力对照
- [架构](architecture.md) — 模块分层、平台抽象、workflow 与状态
- [平台抽象](platform-abstraction.md) — Platform / RecruiterPlatform 双注册表

## Agent 接入

- [Agent Quickstart](agent-quickstart.md) — 信封契约、错误恢复、风控停止规则
- [Agent 宿主示例](agent-hosts.md) — Claude Code / Cursor / Codex 等接入方式

## 边界与安全

- [平台风险边界](platform-risk.md) — Cookie / CDP / 请求频率 / 风控硬边界

## 集成

- [AI 模型](integrations/ai-models.md) — provider 能力边界
- [Claude Code](integrations/claude-code.md)
- [Cursor](integrations/cursor.md)
- [Codex](integrations/codex.md)
- [Windsurf](integrations/windsurf.md)
- [Docker](integrations/docker.md)
- [Python SDK](integrations/python-sdk.md)
- [Shell / Agent 编排](integrations/shell-agent.md)

## 架构决策（ADR）

- [ADR 索引](adr/README.md) — 已记录的关键架构决策

## 维护者

- [发布清单](maintainer/release-checklist.md)
- [分支保护](maintainer/branch-protection.md)
- [标签规范](maintainer/labels.md)
- [README badge 政策](maintainer/readme-badge-policy.md)
- [冒烟测试](maintainer/smoke-testing.md)

## 研究记录

- [平台研究索引](research/platforms/README.md) — 拉勾 / 猎聘 / BOSS 直聘早期 API 调研
- [上游反检测评估（2026-10-29）](research/upstream-antidetection-evaluation-2026-10-29.md)
- [BOSS 直聘 CDP 只读路径（2026-10-29）](research/zhipin-cdp-free-read-path-2026-10-29.md)
- [BOSS 直聘请求身份（2026-10-29）](research/zhipin-request-identity-2026-10-29.md)
