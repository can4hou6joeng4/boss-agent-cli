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

默认不注入 Hook。只有拥有相应授权时，才可在 CLI 显式传 `--hook-profile screenshot-full --hook-dir <含 SHA256SUMS 的目录>`；项目不随包发布第三方脚本。需要立即终止时执行 `boss crawl stop <run_id>`。当 `crawl_status` 返回 `risk_stopped` 时，同 `data_dir` 的风险锁会在浏览器构造前阻断所有新采集和恢复；保留 `run_id`，由用户确认官方目标业务页恢复后执行 `boss clean --risk-lock`，再显式 `boss crawl resume <run_id>`。恢复步骤属于 `operator_actions`，Agent 不自行清锁。`budget_stopped` 需处理预算后恢复；两者均不应重新建任务或循环重试。本地状态、结果和导入仍可用。

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
- 当前 `hr` 与招聘者侧 `agent` 自动化只支持 `zhipin-recruiter`
- 平台返回 `ACCOUNT_RISK`、`RATE_LIMITED` 或 `ENVIRONMENT_RISK` 时停止当前批次，按 `error.recovery_action` 处理，不要无界换通道重试

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
- `ACCOUNT_RISK` / `ENVIRONMENT_RISK`：停止自动化访问并锁存本地状态；code `31`（IP/环境限制）/ `32`（账号限制）/ `35` / `36`、环境类或含糊的 code `37`、HTTP 403 和官方 `403.html` 访问受限页及 `verify.html` / `security.html` 都进入停止分支，`error.details.risk_lock` 保留原始原因、可用业务码和 `explicit_resume_required`
- `ENVIRONMENT_RISK_LOCKED`：历史环境锁尚未显式解除，请求未发出；令牌变化、httpx / headless / CDP 通道切换与 `login --ignore-risk-lock` 都不放行。由用户在原会话的官方目标业务页确认恢复，执行 `boss clean --risk-lock` 后显式恢复任务；主页正常不代表目标页恢复，本地清锁不代表平台解封，再次命中风险立即重锁。Agent 不应自行清锁
- 访问受限页的恢复时间只作平台提示，不自动刷新或清锁；移动端可用不能代替网页目标页验收
- code `37` 只有凭据语义和明确过期/登录失效语义同时存在时才允许有限刷新；裸 `token` / `stoken`、非法或安全验证文案均停止，安全验证和环境异常优先于过期文案
- `wt2` 存在但 `stoken` 缺失：通常为部分登录态；使用 Chrome CDP 远程调试端口后运行 `boss login --cdp`，或重新执行 `boss login`
- `RATE_LIMITED`：已声明可重放的读取可有限冷却；写操作先核对是否生效
- `REQUEST_BUDGET_EXHAUSTED`：共享执行锁、60 秒预算或冷却超出本次等待时限，本次操作未发出；保留 checkpoint，检查并发与 `retry_after_seconds` 后显式恢复，不自动重试或清锁。同 `data_dir` 默认共享，可用 `request_budget_dir` 显式分组；见 [共享预算配置](commands.md#共享请求预算与冷却)。写后确认中断仍为 `ACTION_UNCONFIRMED`，先核对结果
- `NOT_SUPPORTED`：切换 schema catalog 中支持该 goal 的平台或 workflow
- `WORKFLOW_TIMEOUT`：正常 wizard 超时保留 `run_id`，调整超时后显式 `boss wizard --resume <run_id>`；若 MCP 返回 `outcome_unknown=true`，先核对未确认的动作，禁止直接重试
- `INVALID_PARAM`：校正参数（城市、福利、页码等）

## 4) 工具协议直出

招聘者自动化的停止状态不会被普通 `agent run` 清除。操作者在原浏览器完成官方验证、核对受阻页面及未确认动作后，可明确调用 `boss --role recruiter agent run --resume`；MCP `boss_agent_run.resume=true` 同样需要该明确恢复指令。恢复复用已确认动作的防重记录。

不同 Agent host 需要不同形态的工具定义，`boss schema --format` 一次产出：

```bash
boss schema --format openai-tools     # OpenAI Functions / Tools API
boss schema --format anthropic-tools  # Claude Tool Use API
boss schema --format mcp-tools        # Model Context Protocol Tools
```

三个工具格式均来自运行时共享的 78 个 MCP 工具目录，名称与输入参数保持一致；原生格式继续按 39 个顶层命令描述 CLI。

输出可直接喂给对应 host，无需手写适配。

延伸阅读：
- [Agent Host Examples](agent-hosts.md)
- [Capability Matrix](capability-matrix.md)
