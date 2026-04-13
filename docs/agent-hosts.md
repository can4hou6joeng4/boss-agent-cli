# Agent Host Examples

面向不同 Agent 宿主的最小接入索引，目标是把 `boss-agent-cli` 变成“复制就能接”的工具，而不是需要二次猜测的协议。

当前示例覆盖的 CLI 基线：
- 通过 `boss schema` 做能力发现
- 通过 `boss status` 确认登录态
- 通过 `boss search` / `boss detail` / `boss greet` 跑通最小行动闭环
- 通过 JSON 信封读取 `ok`、`data`、`error.code`、`error.recovery_action`

## 示例列表

| 宿主 | 适合场景 | 示例 |
|---|---|---|
| Codex | 在终端内直接做 Bash 编排和 JSON 解析 | [Codex](integrations/codex.md) |
| Claude Code | 通过 skill / 规则文件接入求职动作 | [Claude Code](integrations/claude-code.md) |
| Shell Agent | 任意支持 shell tool 的 Agent 框架或自建编排器 | [Shell Agent](integrations/shell-agent.md) |

## 选型建议

- 如果 Agent 原生支持终端和多步工具调用，优先看 `Codex`
- 如果你已经在用 skill 分发和规则驱动，优先看 `Claude Code`
- 如果你有自己的 Agent 宿主或调度器，优先看 `Shell Agent`

## 共同接入原则

1. 首次接入先跑 `boss schema`，不要硬编码命令表。
2. 所有成功/失败都以 stdout JSON 信封为准，不要解析 stderr。
3. 把 `boss doctor`、`boss login`、`boss status` 当成统一恢复入口。
4. 用户提到福利要求时，优先把条件落到 `--welfare`。
