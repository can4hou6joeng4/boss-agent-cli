# boss-agent-cli MCP Server

将 boss-agent-cli 作为 MCP 工具接入 Claude Desktop / Cursor 等客户端。

相关文档：
- [Agent Quickstart](../docs/agent-quickstart.md)
- [Capability Matrix](../docs/capability-matrix.md)

## 安装

```bash
# 1. 安装 boss CLI
uv tool install git+https://github.com/can4hou6joeng4/boss-agent-cli.git
patchright install chromium

# 2. 安装 MCP 依赖
pip install mcp
```

## 配置 Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "boss-agent-cli": {
      "command": "python3",
      "args": ["/path/to/boss-agent-cli/mcp-server/server.py"]
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
    "command": "python3",
    "args": ["/path/to/boss-agent-cli/mcp-server/server.py"]
  }
}
```

## 可用工具

| 工具 | 说明 |
|------|------|
| `boss_status` | 检查登录态 |
| `boss_doctor` | 诊断环境 |
| `boss_search` | 搜索职位（支持城市/薪资/福利筛选） |
| `boss_recommend` | 个性化推荐 |
| `boss_detail` | 职位详情 |
| `boss_greet` | 向招聘者打招呼 |
| `boss_chat` | 沟通列表 |
| `boss_me` | 用户信息 |
| `boss_cities` | 城市列表 |
| `boss_interviews` | 面试邀请 |
| `boss_history` | 浏览历史 |

## 使用示例

配置完成后，在 Claude Desktop 中直接说：

> "帮我搜一下广州的 Golang 职位，要双休和五险一金"

Claude 会自动调用 `boss_search` 工具并展示结果。

## 注意事项

- 首次使用需要先登录：在终端执行 `boss login`
- MCP Server 通过 subprocess 调用 `boss` CLI，确保 `boss` 在 PATH 中
- 不暴露 login/logout 等交互式命令（需要浏览器界面）
