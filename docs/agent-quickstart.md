# Agent Quickstart

面向 AI Agent 的最短上手路径：先识别能力，再通过细粒度命令或 `boss wizard --input-json` 执行候选者、招聘者和长任务 workflow。

历史 `assisted` / `research` 配置均开放全部已实现能力。Agent 必须调用 `boss schema`，按 role、platform、availability 和 goal catalog 路由；平台未实现分支返回 `NOT_SUPPORTED`。

## 1) 安装与环境准备

```bash
# 推荐方式（三选一）
uv tool install boss-agent-cli   # uv（秒级，自动隔离）
pipx install boss-agent-cli      # pipx（隔离环境）
pip install boss-agent-cli       # pip

# 安装浏览器（用于登录）
patchright install chromium

# 环境自检 + 登录
boss doctor
boss login
boss status
```

完成标准：
- `boss doctor` 返回 `ok=true`
- `boss status` 返回本地登录态的分层健康状态；如需真实只读验证，显式运行 `boss status --live`
- 若使用 `zhilian`，请显式带上平台：`boss --platform zhilian doctor && boss --platform zhilian login`

如果你不是直接在终端里手动跑命令，而是准备把它接进 Agent 宿主，先看 [Agent Host Examples](agent-hosts.md) 选择对应接入模板。

## 2) 三步跑通 Agent workflow

```bash
# Step 1: 拉取自描述能力
boss schema

# Step 2: 搜索并定位目标职位
boss search "Golang" --city 广州 --welfare "双休,五险一金"
# 复杂筛选可复用用户在网页上选好的 URL
boss search --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100&experience=104,105'

# Step 3: 查看详情并推进后续动作
boss detail <security_id>
boss shortlist add <security_id> <job_id>
boss apply <security_id> <job_id>
```

也可一次提交共享 workflow，并用返回的 `run_id` 查询或恢复：

```bash
boss --json wizard --input-json '{"role":"candidate","platform":"zhipin","goal":"job_search","inputs":{"query":"Golang","welfare_conditions":["双休"]}}'
boss --json wizard --status <run_id>
boss --json wizard --resume <run_id>
```

解析约定：
- `stdout` 只读 JSON 信封
- `ok=true` 代表成功，`ok=false` 时读取 `error.code` 与 `error.recovery_action`
- `boss schema` 除了返回 `supported_platforms` / `supported_recruiter_platforms`，还会给每个命令附带 `availability`，可直接按 `role/platform` 做工具路由

### 候选人 crawl 编排

安装 `uv sync --extra crawl` 后，crawl 只使用 `<data-dir>/crawl/chrome-profile` 独立 profile。可用细粒度 CLI 创建任务，再使用 MCP 读取或本地导入已有任务；也可通过 `boss_wizard` 直接推进共享 crawl workflow：

```text
boss crawl start <query> --city <city> --pages <n>
→ 得到 run_id
→ boss_crawl_status(run_id)
→ boss_crawl_results(run_id)
→ boss_crawl_shortlist(run_id, all=true)
→ boss_ai_fit(resume)
```

CLI 中，`boss agent crawl --run-id <run_id> --resume <简历名>` 只处理已完成任务并完成 shortlist 与 ai fit。使用 `--query` 和 `--city` 可新开真实采集：

```bash
boss agent crawl --query "AI 工程师" --city 杭州 --pages 3 --with-detail --resume <简历名>
```

默认不注入 Hook。只有拥有相应授权时，才可在 CLI 显式传 `--hook-profile screenshot-full --hook-dir <含 SHA256SUMS 的目录>`；项目不随包发布第三方脚本。需要立即终止时执行 `boss crawl stop <run_id>`。当 `crawl_status` 返回 `risk_stopped` 或 `budget_stopped` 时，不要重新建任务或循环重试；保留 `run_id`，由用户处理后执行 `boss crawl resume <run_id>`。

### 招聘者 workflow

招聘者命令覆盖候选人搜索、投递申请、简历、聊天、联系方式交换、消息回复和职位管理：

```bash
# Step 1: 同样先做能力发现
boss schema

# Step 2: 搜索候选人、查看沟通并管理职位
boss hr candidates "Python" --city 101010100
boss hr chat --page 1
boss hr jobs list
```

建议做法：
- 先把 `boss schema` 里的 `hr` 命令组当作招聘者能力真源
- `boss hr <subcommand>` 会自动切到 recruiter 角色，不需要额外推断 `--role`
- 求职者与招聘者两端都遵守同一套 `stdout JSON / stderr 日志` 契约
- 当前 `hr` 只支持 `zhipin-recruiter`；智联招聘者侧自动化请使用 `boss --platform zhilian --role recruiter agent ...`
- 平台返回 `ACCOUNT_RISK` 或 `RATE_LIMITED` 时停止当前批次，按 `error.recovery_action` 处理，不要无界换通道重试

## 3) 失败恢复与排障

推荐顺序：

```bash
boss doctor
boss logout
boss login
boss status
```

常见恢复动作：
- `AUTH_REQUIRED` / `AUTH_EXPIRED` / `TOKEN_REFRESH_FAILED`：重新执行 `boss login`
- `wt2` 存在但 `stoken` 缺失：通常为部分登录态；使用 Chrome CDP 远程调试端口后运行 `boss login --cdp`，或重新执行 `boss login`
- `RATE_LIMITED`：等待后重试
- `ENVIRONMENT_RISK`：立即停止；不要刷新 Token、重新登录或自动重试，保留同一专用 Chrome profile 并降低访问频率
- `NOT_SUPPORTED`：切换 schema catalog 中支持该 goal 的平台或 workflow
- `WORKFLOW_TIMEOUT`：保留 `run_id`，调整超时后执行 `boss wizard --resume <run_id>`
- `INVALID_PARAM`：校正参数（城市、福利、页码等）

## 4) 工具协议直出

不同 Agent host 需要不同形态的工具定义，`boss schema --format` 一次产出：

```bash
boss schema --format openai-tools     # OpenAI Functions / Tools API
boss schema --format anthropic-tools  # Claude Tool Use API
boss schema --format mcp-tools        # Model Context Protocol Tools
```

输出可直接喂给对应 host，无需手写适配。

延伸阅读：
- [Agent Host Examples](agent-hosts.md)
- [Capability Matrix](capability-matrix.md)
