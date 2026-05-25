# boss-agent-cli MCP Server

将 boss-agent-cli 作为默认低风险 MCP 工具接入 Claude Desktop / Cursor 等客户端。MCP 默认只暴露本地辅助、职位搜索/详情、本地候选池、简历和 AI 辅助等低风险工具；自动触达、批量操作、聊天记录、候选人个人信息和招聘者敏感工作流不会默认暴露。

相关文档：
- [Agent Quickstart](../docs/agent-quickstart.md)
- [Capability Matrix](../docs/capability-matrix.md)

## 安装

```bash
uv tool install "boss-agent-cli[mcp]"
```

如从源码运行：

```bash
uv sync --all-extras
uv run python mcp-server/server.py
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

## 配置 Cursor

在 Cursor Settings -> MCP Servers 中添加：

```json
{
  "boss-agent-cli": {
    "command": "boss-mcp",
    "args": []
  }
}
```

## 配置 VS Code（Windows）

在 VS Code 的 `mcp.json` 中添加 stdio server。将 `E:\tools\boss-agent-cli` 替换为你的本地项目路径：

```json
{
  "servers": {
    "boss-agent-cli": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "E:\\tools\\boss-agent-cli",
        "run",
        "python",
        "mcp-server/server.py"
      ]
    }
  }
}
```

MCP Server 内部调用 `boss` CLI 时会关闭子进程 stdin，避免子进程误读 VS Code 的 MCP stdio 协议流导致阻塞超时。

## 可用工具

当前 MCP Server 默认暴露 **32 个低风险工具**。

### 认证与环境

| 工具 | 说明 |
|------|------|
| `boss_status` | 检查登录态 |
| `boss_doctor` | 诊断环境 |
| `boss_config` | 查看和修改配置项 |
| `boss_clean` | 清理过期缓存和临时文件 |

### 职位发现与本地整理

| 工具 | 说明 |
|------|------|
| `boss_search` | 搜索职位（支持城市、薪资、福利筛选） |
| `boss_detail` | 职位详情 |
| `boss_show` | 按编号查看上次搜索结果中的职位 |
| `boss_export` | 导出搜索结果为 CSV / JSON / HTML（支持 `--url` 复用网页筛选；默认脱敏 job_id/security_id/boss_name） |
| `boss_cities` | 城市列表 |
| `boss_history` | 浏览历史 |
| `boss_shortlist_list` | 查看本地候选池 |
| `boss_shortlist_add` | 加入本地候选池 |
| `boss_shortlist_remove` | 从本地候选池移除 |
| `boss_preset_add/list/remove` | 管理本地搜索预设 |
| `boss_watch_add/list/remove` | 管理本地监控预设；`watch run` 默认不暴露 |

### 用户与简历

| 工具 | 说明 |
|------|------|
| `boss_me` | 用户信息（基本信息、简历、求职期望、投递记录） |
| `boss_resume_list` | 列出本地简历 |
| `boss_resume_show` | 查看本地简历 |

### AI 辅助

| 工具 | 说明 |
|------|------|
| `boss_ai_analyze_jd` | 分析岗位描述 |
| `boss_ai_optimize` | 基于岗位优化本地简历草稿 |
| `boss_ai_suggest` | 生成简历改进建议 |
| `boss_ai_reply` | 基于用户提供文本生成回复草稿 |
| `boss_ai_interview_prep` | 基于岗位描述生成面试准备 |
| `boss_ai_chat_coach` | 基于用户主动提供文本生成沟通建议 |

### 招聘者低风险入口

| 工具 | 说明 |
|------|------|
| `boss_hr_jobs` | 职位列表与上下线管理 |
| `boss_hr_jobs_detail` | 查看招聘者职位详情 |

敏感工具（如 `boss_greet`、`boss_apply`、`boss_chat`、`boss_chatmsg`、`boss_pipeline`、`boss_digest`、`boss_hr_candidates`、`boss_hr_reply` 等）默认不暴露；若通过 CLI 直接调用，也会在默认低风险模式返回 `COMPLIANCE_BLOCKED`。

## 使用示例

配置完成后，在 Claude Desktop 中直接说：

> "帮我搜一下广州的 Golang 职位，要双休和五险一金，然后把合适的岗位加入候选池。"

Claude 会调用 `boss_search` / `boss_detail` / `boss_shortlist_add` 等低风险工具。投递、沟通、联系方式交换和候选人处理应回到平台官网由用户手动完成。

## 传输层（Transports）

### stdio（默认）

```bash
boss-mcp
```

### SSE

```bash
boss-mcp --transport sse --host 127.0.0.1 --port 8765
```

默认路径：
- SSE 建链：`/sse`
- 消息回传：`/messages/`

### HTTP Streaming

```bash
boss-mcp --transport http --host 127.0.0.1 --port 8765
```

默认路径：
- HTTP Streaming：`/mcp`

**设计约束**：
- `stdio` 保持为默认行为，不破坏现有集成
- HTTP 传输默认绑定 `127.0.0.1`，远程暴露需用户显式 `--host 0.0.0.0`
- 不内置鉴权 / TLS，需要时通过反向代理处理

## 其他 Agent 宿主接入

```bash
boss schema --format openai-tools
boss schema --format anthropic-tools
boss schema --format mcp-tools
```

然后把 stdout 的 `data.tools` 数组直接喂给对应 SDK 即可。

## 贡献

开发环境：

```bash
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/test_mcp_server.py -v
```

代码风格：tab 缩进，`uv run ruff check src/ tests/` 必须通过。
