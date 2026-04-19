# Windsurf Integration Example

适用版本：`boss-agent-cli` 当前 CLI 契约（2026-04-19）

Windsurf 是 Codeium 发布的 agentic IDE，Cascade 面板是主力 Agent，支持 MCP 协议和项目级 `.windsurfrules`。本指引给两种接入方式：MCP 原生接入（推荐）和规则文件接入（兜底）。

## 适用场景

- 用 Cascade 跑完整求职链路，让 Agent 自主决定何时 search / detail / greet
- 希望把 `boss` 能力以 MCP 工具形式注册，而非每次粘贴终端命令
- 已有 `.windsurfrules` 项目规则，想追加 BOSS 直聘约束

## 最小接入流程

Windsurf 支持两种接入方式，按需二选一。

### 方式一：MCP 服务接入（推荐）

在 Windsurf 设置 → Cascade → MCP Servers 里添加：

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

启用后 Cascade 会自动枚举 `boss_search` / `boss_detail` / `boss_greet` / `boss_ai_*` 等 43 个工具，无需额外提示词。

### 方式二：.windsurfrules 规则接入

在项目根目录 `.windsurfrules` 里追加：

```markdown
## BOSS 直聘求职能力

当任务涉及搜索岗位、查看职位详情、打招呼或推进求职进度时：
1. 先运行 `boss schema` 获取能力和参数
2. 再运行 `boss status` 检查登录态
3. 未登录时运行 `boss login`，提示用户完成扫码
4. 搜索使用 `boss search`，优先带 `--welfare` 精准筛选
5. 命中后用 `boss detail <security_id>` 看完整信息
6. 执行动作用 `boss greet <security_id> <job_id>`
7. 只消费 stdout JSON；`ok=false` 时读 `error.recovery_action` 做恢复决策
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
- `error.recovery_action`：告诉 Cascade 如何修复

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

- 把 `boss ai reply <message>` / `boss ai chat-coach <chat>` 接给 Cascade，让它在沟通过程中提供话术支持
- 用 `boss digest --format md` 生成每日摘要，Cascade 预览面板直出飞书/邮件可用版
