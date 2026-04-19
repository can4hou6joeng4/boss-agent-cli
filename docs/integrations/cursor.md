# Cursor Integration Example

适用版本：`boss-agent-cli` 当前 CLI 契约（2026-04-19）

Cursor 是基于 VS Code 的 AI 优先 IDE，Composer 模式的 Agent 可以直接执行终端命令，0.42+ 还支持 MCP 协议接入。本指引给两种接入方式：MCP 原生接入（推荐）和 Shell 命令接入（兜底）。

## 适用场景

- 在 Cursor 里用 Composer Agent 推进完整求职链路
- 希望 `boss` 命令以 MCP 工具形式暴露给 Cursor，而非依赖 terminal 脚本
- 已经有 `.cursor/rules/` 规则体系，想追加 BOSS 直聘能力

## 最小接入流程

Cursor 支持两种接入方式，按需二选一。

### 方式一：MCP 服务接入（推荐）

在 Cursor 设置 → MCP 里添加一个 stdio 服务器，指向本仓库的 `mcp-server/server.py`：

```json
{
  "mcpServers": {
    "boss-agent-cli": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/boss-agent-cli",
        "run",
        "python",
        "mcp-server/server.py"
      ]
    }
  }
}
```

启用后 Composer 会自动看到 `boss_search` / `boss_detail` / `boss_greet` 等 43 个工具，直接在会话里调用即可。不需要额外粘贴 shell 命令。

### 方式二：Shell 命令接入

在 `.cursor/rules/boss-agent-cli.mdc` 里加入以下规则：

```markdown
---
description: BOSS 直聘求职能力
globs:
alwaysApply: false
---

当用户要求搜索岗位 / 查看详情 / 打招呼 / 推进候选进度时：
1. 先运行 `boss schema` 获取能力与参数
2. 再运行 `boss status` 检查登录态
3. 未登录时运行 `boss login`，提示用户扫码
4. 搜索使用 `boss search <query> --city <city> --welfare <keywords>`
5. 命中后用 `boss detail <security_id>` 看完整信息
6. 执行动作用 `boss greet <security_id> <job_id>`
7. 只读取 stdout JSON；`ok=false` 时读 `error.recovery_action`
```

最小命令链路：

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

## 解析字段

- `ok`：判断命令是否成功
- `data`：读取职位、详情或动作结果
- `hints.next_actions`：决定下一条命令
- `error.code`：做恢复分流
- `error.recovery_action`：告诉 Agent 如何修复

## 失败恢复

推荐恢复顺序：

```bash
boss doctor
boss status
boss login
```

常见分流：

- `AUTH_REQUIRED` / `AUTH_EXPIRED`：重新执行 `boss login`
- `INVALID_PARAM`：回退到 `boss schema` 校验参数名
- `RATE_LIMITED`：等待后重试，不要盲目连发 `boss greet`
- `ACCOUNT_RISK`：启动 CDP Chrome（`boss-chrome` alias），再重试

## 进阶

- 把 `boss ai interview-prep <jd>` / `boss ai chat-coach <chat>` 接入 Composer，让 Cursor 承担简历匹配与沟通辅导
- 用 `boss digest --format md -o daily.md` 生成每日摘要，Cursor 侧边栏打开预览即可
