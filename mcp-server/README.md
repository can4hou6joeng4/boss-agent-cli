# boss-agent-cli MCP Server

通过已注册的 MCP 工具，将 boss-agent-cli 的候选者、招聘者、本地、AI 和 workflow 能力接入 Claude Desktop / Cursor 等客户端。具体可调用范围以 MCP `tools/list` 为准。历史 assisted/research 配置拥有相同能力访问；平台缺失实现仍返回 `NOT_SUPPORTED`。

相关文档：
- [Agent Quickstart](../docs/agent-quickstart.md)
- [Capability Matrix](../docs/capability-matrix.md)

## 安装

```bash
uv tool install "boss-agent-cli[mcp,crawl]"  # 不需要 crawl 时可只装 [mcp]
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

当前 MCP Server 暴露 **90 个已实现工具**。

### 能力范围与恢复指引

调用 `boss_schema` 时传入 `{"format":"mcp-tools"}` 可查看工具 schema；`native` 格式描述完整 CLI 命令与 `wizard_catalog`。CLI 子命令与 MCP 工具并非逐项对应：本地简历已提供 `boss_resume_list` / `boss_resume_show` / `boss_resume_init` / `boss_resume_delete` / `boss_resume_clone` / `boss_resume_diff` / `boss_resume_link` / `boss_resume_applications` / `boss_resume_export` / `boss_resume_import` / `boss_resume_edit`，职位收藏已提供 `boss_favorites_list` / `boss_favorites_sync`；其余维护步骤仍需通过 CLI 执行。`boss_resume_delete` 删除本机简历文件且不可恢复；`boss_resume_clone` / `boss_resume_link` / `boss_resume_import` / `boss_resume_edit` 只写本机，不产生平台请求，其中 `import` 的 `force=true` 会丢弃本地已有编辑；`boss_resume_diff` / `boss_resume_applications` 只读本机；`boss_resume_export` 只读简历并写本机文件，`pdf` 格式需要本机浏览器内核；`boss_favorites_sync` 只读平台、写入本地候选池。

信封中的 `hints.next_actions` 是后继 CLI 命令，不能直接当作 MCP 工具名调用。仅使用 MCP 的宿主应先查找等价工具或已登记的 `boss_wizard` goal；没有等价入口时，明确告知需要执行的 CLI 步骤，不要声称已完成。`hints.operator_actions` 则保留给扫码、浏览器操作等真人指引。

### 工具注解

`tools/list` 为每个工具附带 MCP `ToolAnnotations`：静态只读工具（如 `boss_schema`、`boss_search`、`boss_resume_show`）标注 `readOnlyHint: true`，供宿主自动放行只读调用；带 `action` 维度（`boss_config` / `boss_hr_jobs` / `boss_wizard`）或本地输出参数（`boss_chat` / `boss_export` / `boss_digest` / `boss_stats`）的工具保持 `false`，避免把参数决定的写入藏进「只读」提示。注解只是宿主提示，不替代工具自身的确认与错误契约；`destructiveHint` / `idempotentHint` / `openWorldHint` 保持规范默认值。

### 共享 workflow

| 工具 | 说明 |
|------|------|
| `boss_wizard` | 按 role/platform/goal 执行 workflow，或用显式 `run_id` 查询、恢复和停止；与真人 `boss wizard` 共用状态 |

### 认证与环境

| 工具 | 说明 |
|------|------|
| `boss_status` | 检查登录态 |
| `boss_doctor` | 诊断环境 |
| `boss_schema` | 只读能力真源（命令、枚举、错误码与 wizard catalog） |
| `boss_config` | 查看和修改配置项 |
| `boss_clean` | 清理过期缓存和临时文件 |

### 职位发现与本地整理

| 工具 | 说明 |
|------|------|
| `boss_search` | 搜索职位（支持城市、薪资、经验、学历、行业、规模、融资阶段、职位类型、福利与活跃度筛选） |
| `boss_detail` | 职位详情 |
| `boss_show` | 按编号查看上次搜索结果中的职位 |
| `boss_export` | 导出搜索结果为 CSV / JSON / HTML（支持 `--url` 复用网页筛选；默认脱敏 job_id/security_id/boss_name） |
| `boss_cities` | 城市列表 |
| `boss_history` | 浏览历史 |
| `boss_shortlist_list` | 查看本地候选池 |
| `boss_shortlist_add` | 加入本地候选池 |
| `boss_shortlist_remove` | 从本地候选池移除 |
| `boss_preset_add/list/remove` | 管理本地搜索预设 |
| `boss_watch_add/list/remove/run` | 管理和执行职位监控 |

### 候选者动作与沟通

| 工具 | 说明 |
|------|------|
| `boss_greet` / `boss_batch_greet` / `boss_apply` | 打招呼、按显式上限批量打招呼、投递或立即沟通 |
| `boss_chat` / `boss_chatmsg` / `boss_chat_summary` | 沟通列表、消息历史和摘要 |
| `boss_mark` / `boss_exchange` | 联系人标签与联系方式交换 |
| `boss_pipeline` / `boss_follow_up` / `boss_digest` | 候选进度、跟进筛选与日报 |

### 已有 crawl 任务

| 工具 | 说明 |
|------|------|
| `boss_crawl_status` | 读取页游标、职位数、详情进度、风险状态和恢复命令 |
| `boss_crawl_results` | 读取一个 run 已持久化的职位，可按页面/详情状态筛选 |
| `boss_crawl_shortlist` | 将一个 run 的职位导入本地候选池，不请求平台 |

细粒度 crawl tools 用于读取或导入已有任务；`boss_wizard` 的 `crawl_start/crawl_resume/crawl_stop` goal 可启动、恢复或停止共享 workflow。默认 Hook 为 `none`；本地 Hook 目录必须包含 `SHA256SUMS`，项目不发布第三方脚本。

操作者明确决定恢复已有采集任务后，可调用 `boss_wizard` 并传入以下参数，将 `<crawl_run_id>` 替换为 `boss_crawl_status` 对应的采集任务 ID：

```json
{
  "action": "run",
  "role": "candidate",
  "platform": "zhipin",
  "goal": "crawl_resume",
  "inputs": {"run_id": "<crawl_run_id>"}
}
```

这里的 `inputs.run_id` 是采集任务 ID；`action="resume"` 的顶层 `run_id` 则用于恢复向导 workflow，两者不能混用。若任务因平台风控停止，应先遵循错误信封的恢复指引，待风险解除后再由操作者显式恢复。

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

### 招聘者工作流

| 工具 | 说明 |
|------|------|
| `boss_hr_whoami` | 查看当前登录态是否处于招聘者侧；identity 为 1 才就绪，不切换身份 |
| `boss_hr_jobs` | 职位列表与上下线管理 |
| `boss_hr_jobs_detail` | 查看招聘者职位详情 |
| `boss_hr_applications` / `boss_hr_candidates` | 投递申请与候选人搜索 |
| `boss_hr_resume` / `boss_hr_exchange` / `boss_hr_request_resume` | 在线简历、联系方式交换与附件简历请求 |
| `boss_hr_chat` / `boss_hr_chatmsg` / `boss_hr_last_messages` / `boss_hr_reply` | 招聘者沟通读取与回复 |
| `boss_hr_accept_resume` / `boss_hr_download_resume` | 同意指定附件简历请求，以及权限检查后下载已收到的附件 |
| `boss_hr_recommendations` / `boss_hr_greet` | 推荐牛人及需明确批准的单次首次招呼，不修改已读状态 |
| `boss_hr_location` | 向已有会话发送工作地点卡片；需操作者明确批准，同一会话不重复发送 |

所有已实现工具都会暴露。`ACCOUNT_RISK`、`ENVIRONMENT_RISK`、`AUTH_REQUIRED`、`RATE_LIMITED` 和 `NOT_SUPPORTED` 仍通过标准 JSON 错误信封返回，Agent 应读取 `error.recovery_action`。

## 使用示例

配置完成后，在 Claude Desktop 中直接说：

> "帮我搜一下广州的 Golang 职位，要双休和五险一金，然后把合适的岗位加入候选池。"

Claude 可调用 `boss_wizard` 运行完整 workflow，也可组合 `boss_search` / `boss_detail` / `boss_shortlist_add` / `boss_apply` 等细粒度工具。

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
