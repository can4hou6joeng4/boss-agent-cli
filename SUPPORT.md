# 获取支持

> 遇到报错先看 [诊断与排障](docs/troubleshooting.md)——错误信封里的 `error.code`、`error.recoverable` 和 `error.recovery_action` 通常已经写明恢复动作。英文版见 [SUPPORT.en.md](SUPPORT.en.md)。

## 先看这些

- [快速开始](docs/getting-started.md) — 安装、登录、第一条命令
- [诊断与排障](docs/troubleshooting.md) — 常见错误、CDP / patchright、风控锁
- [命令参考](docs/commands.md) — 全部命令与参数
- [Agent 接入](docs/agent-quickstart.md) — MCP / JSON 信封 / Python API
- 本地自检：`boss doctor`、`boss status`、`boss schema --format native`

## 在哪里提问

| 问题类型 | 渠道 | 说明 |
|----------|------|------|
| 使用疑问、安装困难、Agent 接入 | [GitHub Discussions](https://github.com/can4hou6joeng4/boss-agent-cli/discussions) | 适合开放式讨论，不必是已确认的 Bug |
| 可复现的 Bug / 功能建议 | [Issue](https://github.com/can4hou6joeng4/boss-agent-cli/issues/new/choose) | 按模板填写；未启用空白 issue |
| 安全漏洞 | [SECURITY.md](SECURITY.md) | 走私密报告，不要公开提 Issue |

## 提问时附上

- `boss --version` 的输出
- 完整命令（含全局选项，如 `--platform`、`--browser-source`）
- 错误信封的 `error.code`、`error.message`、`error.recovery_action`
- 操作系统与安装方式（`pip` / `uv tool` / 源码仓库）

**请先脱敏**：不要粘贴 cookie、token、手机号、微信号、真实姓名或真实 `security_id`。

## 响应预期

- 这是维护者用业余时间维护的项目，按尽力而为的方式回复，不承诺响应时限。
- 60 天没有互动的 Issue / PR 会被标记 `stale`，再过 14 天自动关闭；补一条回复或推一个提交即可保留。
- 安全问题按 [SECURITY.md](SECURITY.md) 的流程处理，细节不在公开渠道讨论。
