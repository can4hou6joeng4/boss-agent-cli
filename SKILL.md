---
name: boss-agent-cli
description: AI Agent 专用的 BOSS 直聘本地辅助 CLI。通过 boss schema 自描述能力，stdout 只输出 JSON 信封；默认低风险模式只做本地辅助、只读优先、用户主动触发。
---

# boss-agent-cli

> AI Agent 专用的 BOSS 直聘本地辅助 CLI。默认低风险模式只做本地辅助、只读优先、用户主动触发，不自动触达、不批量操作、不抓取平台数据。

## Install

### Skills CLI

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

### CLI

```bash
uv tool install boss-agent-cli
patchright install chromium  # only needed for browser-assisted login
```

## First Minute

按这个顺序跑，不需要先读完整 README：

```bash
boss doctor
boss schema
boss status
boss login   # only if status reports AUTH_REQUIRED / AUTH_EXPIRED
```

完成标准：

- `boss doctor` 返回 `ok=true`，或给出可执行的 `error.recovery_action`。
- `boss schema` 返回当前命令、参数、平台和错误码自描述——它是能力真源，不要硬编码命令表。
- `boss status` 返回当前登录态；未登录时由用户主动执行 `boss login`。

之后的一切以 `boss schema` 与下方文档为准：只解析 stdout 的 JSON 信封；`ok=false` 时按 `error.code` 与 `error.recovery_action` 恢复；投递、打招呼、沟通等敏感动作回到平台官网由用户手动完成。

## Docs

- [Agent Quickstart](docs/agent-quickstart.md)
- [Agent Host Examples](docs/agent-hosts.md)
- [Capability Matrix](docs/capability-matrix.md)
- [README](README.md)

## License

MIT
