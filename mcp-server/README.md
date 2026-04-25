# boss-agent-cli MCP Server

将 boss-agent-cli 作为 MCP 工具接入 Claude Desktop / Cursor 等客户端。

相关文档：
- [Agent Quickstart](../docs/agent-quickstart.md)
- [Capability Matrix](../docs/capability-matrix.md)

## 安装

```bash
# 1. 安装 boss CLI
uv tool install boss-agent-cli
patchright install chromium

# 2. 安装 MCP 依赖并暴露 MCP 入口
uv tool install "boss-agent-cli[mcp]"
```

## 配置 Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "boss-agent-cli": {
      "command": "boss-mcp",
      "args": []
    }
  }
}
```

将 `/path/to/` 替换为实际项目路径。

## 配置 Cursor

在 Cursor Settings → MCP Servers 中添加：

```json
{
  "boss-agent-cli": {
    "command": "boss-mcp",
    "args": []
  }
}
```

## 可用工具

当前 MCP Server 暴露 **49 个工具**，覆盖求职者链路、AI 辅助能力，以及招聘者侧 `hr` 工作流。

### 认证与环境

| 工具 | 说明 |
|------|------|
| `boss_status` | 检查登录态 |
| `boss_doctor` | 诊断环境 |
| `boss_config` | 查看和修改配置项 |
| `boss_clean` | 清理过期缓存和临时文件 |

### 职位发现

| 工具 | 说明 |
|------|------|
| `boss_search` | 搜索职位（支持城市、薪资、福利筛选） |
| `boss_recommend` | 个性化推荐 |
| `boss_detail` | 职位详情 |
| `boss_show` | 按编号查看上次搜索结果中的职位 |
| `boss_cities` | 城市列表 |
| `boss_history` | 浏览历史 |

### 求职动作

| 工具 | 说明 |
|------|------|
| `boss_greet` | 向招聘者打招呼 |
| `boss_batch_greet` | 搜索后批量打招呼 |
| `boss_apply` | 发起投递或立即沟通 |

### 沟通管理

| 工具 | 说明 |
|------|------|
| `boss_chat` | 沟通列表 |
| `boss_chatmsg` | 聊天消息历史 |
| `boss_chat_summary` | 聊天摘要与下一步建议 |
| `boss_mark` | 联系人标签管理 |
| `boss_exchange` | 交换联系方式 |
| `boss_interviews` | 面试邀请 |

### 流程管理

| 工具 | 说明 |
|------|------|
| `boss_pipeline` | 候选进度视图 |
| `boss_follow_up` | 跟进筛选 |
| `boss_digest` | 日报汇总 |

### 用户信息

| 工具 | 说明 |
|------|------|
| `boss_me` | 用户信息（基本信息、简历、求职期望、投递记录） |

### 招聘者工作流

| 工具 | 说明 |
|------|------|
| `boss_hr_applications` | 查看候选人投递申请列表 |
| `boss_hr_candidates` | 搜索候选人 |
| `boss_hr_chat` | 招聘者沟通列表 |
| `boss_hr_resume` | 查看候选人在线简历 |
| `boss_hr_reply` | 回复候选人消息 |
| `boss_hr_request_resume` | 请求候选人附件简历 |
| `boss_hr_jobs` | 职位列表与上下线管理 |

## 使用示例

配置完成后，在 Claude Desktop 中直接说：

> "帮我搜一下广州的 Golang 职位，要双休和五险一金"

Claude 会自动调用 `boss_search` 工具并展示结果。

## 传输层（Transports）

### stdio（默认，已支持）

Claude Desktop / Cursor / Codex 等通过 stdin/stdout 直接对接。上述 `claude_desktop_config.json` 配置方式即为 stdio 模式。

```bash
boss-mcp
```

### HTTP / SSE（规划中，见 [Issue #48](https://github.com/can4hou6joeng4/boss-agent-cli/issues/48)）

面向远程宿主、自定义编排器的 Server-Sent Events 传输，允许跨机部署 MCP 服务。

```bash
# 规划形态（未实现，等 PR 合入）
python3 mcp-server/server.py --transport sse --host 127.0.0.1 --port 8765
```

**设计约束**：
- `stdio` 保持为默认行为，不破坏现有集成
- HTTP 传输默认绑定 `127.0.0.1`，远程暴露需用户显式 `--host 0.0.0.0`
- 不内置鉴权 / TLS，需要时通过反向代理（nginx / Caddy / Cloudflare Tunnel）处理

## 其他 Agent 宿主接入

本 MCP Server 专注 stdio/SSE 协议。如果你用的 Agent 框架不支持 MCP，可以用 `boss schema` 命令直接生成对应格式：

```bash
# OpenAI Functions / Tools API（GPT-4 / GPT-5）
boss schema --format openai-tools

# Anthropic Tool Use（Claude API 直连）
boss schema --format anthropic-tools
```

然后把 stdout 的 `data.tools` 数组直接喂给对应 SDK 即可。

## 贡献

欢迎在 [Issue Tracker](https://github.com/can4hou6joeng4/boss-agent-cli/issues) 认领带 `good first issue` / `help wanted` 标签的任务。

开发环境：

```bash
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/test_mcp_server.py -v  # 65 tests covering tool schema + arg construction
```

代码风格：tab 缩进，`uv run ruff check src/ tests/` 必须通过。
