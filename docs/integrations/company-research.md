# 使用两个 MCP 服务研究公司

运行 [Python 示例](../../examples/company_research.py)，在面试准备时把本地职位候选池与公开公司资料放在一起查看。
示例通过 stdio 调用 `boss_shortlist_list`，通过 Streamable HTTP 调用
[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)。搜索使用 Fast 模式；匿名端点无需账号或 API key，但限流额度较低。可选 URL 参数用于抓取网页。

## 安装与运行

在源码目录中创建独立环境并安装示例依赖：

```bash
uv venv .venv-research
uv pip install --python .venv-research/bin/python ".[mcp]" -r examples/company-research-requirements.txt
export PATH="$PWD/.venv-research/bin:$PATH"
.venv-research/bin/python examples/company_research.py \
  "Mozilla company products careers" --data-dir ./.boss-agent
```

Windows 上将两个 Python 路径替换为 `.venv-research\Scripts\python.exe`；PowerShell 中将
`$PWD\.venv-research\Scripts` 放到 `$env:PATH` 前面。MCP 服务会调用 CLI，因此安装后的 `boss` 必须在 `PATH` 上。

需要抓取指定公开页面时：

```bash
.venv-research/bin/python examples/company_research.py \
  "Mozilla company products careers" --data-dir ./.boss-agent \
  --url https://www.mozilla.org/en-US/about/
```

读取本地候选池不需要登录 BOSS。使用保存职位时的数据目录；空目录返回空候选池。
现有 `boss` 命令、AI provider 和 MCP 宿主配置不变。

## 输出与边界

示例直接调用工具，不使用模型或自主 Agent 循环。stdout 只输出一个 JSON 信封：
`data.shortlist` 是本地职位，`data.search` 是含来源 URL 与摘录的 MCP 搜索结果，
仅提供 `--url` 时才有 `data.fetch`。保留 MCP content 和结构化结果。
成功退出码为 0；连接或工具失败时为 1，错误信封含恢复动作，诊断信息写入 stderr。

只有用户显式提供的公开查询和可选 URL 会发给 Parallel。示例不会从候选池自动生成查询，
也不会发送简历、备注、职位标识、Cookie 或保存的 API key。不要在参数中放入私密信息。
合并后的 stdout 含本地候选池字段，分享前请脱敏。
单次请求超时为 60 秒，整个流程最多 180 秒。失败即停止，不自动重试；匿名端点限流时稍后重试。
使用网页结果前请核对来源页面。
