# Agent Quickstart

面向 AI Agent 的最短上手路径：先识别能力，再跑通从搜索到行动的闭环；如果你接的是招聘者场景，也能用同一套 JSON 契约接 `boss hr`。

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
- `boss status` 返回当前登录态
- 若使用 `zhilian`，请显式带上平台：`boss --platform zhilian doctor && boss --platform zhilian login`

如果你不是直接在终端里手动跑命令，而是准备把它接进 Agent 宿主，先看 [Agent Host Examples](agent-hosts.md) 选择对应接入模板。

## 2) 三步跑通 Agent 闭环

```bash
# Step 1: 拉取自描述能力
boss schema

# Step 2: 搜索并定位目标职位
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# Step 3: 查看详情并执行动作
boss detail <security_id>
boss greet <security_id> <job_id>
```

解析约定：
- `stdout` 只读 JSON 信封
- `ok=true` 代表成功，`ok=false` 时读取 `error.code` 与 `error.recovery_action`
- `boss schema` 除了返回 `supported_platforms` / `supported_recruiter_platforms`，还会给每个命令附带 `availability`，可直接按 `role/platform` 做工具路由

### 招聘者最小闭环

如果 Agent 面向 HR / 招聘者角色，建议直接走 `boss hr`：

```bash
# Step 1: 同样先做能力发现
boss schema

# Step 2: 拉取招聘者侧能力
boss hr applications
boss hr candidates "Golang"

# Step 3: 触达候选人
boss hr reply <friend_id> "你好，方便聊一下岗位吗？"
boss hr request-resume <friend_id> --job-id <job_id>
```

建议做法：
- 先把 `boss schema` 里的 `hr` 命令组当作招聘者能力真源
- `boss hr <subcommand>` 会自动切到 recruiter 角色，不需要额外推断 `--role`
- 求职者与招聘者两端都遵守同一套 `stdout JSON / stderr 日志` 契约
- 当前 `hr` 只支持 `zhipin-recruiter`；若当前平台切到 `zhilian`，CLI 会直接拒绝执行 recruiter 子命令

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
- `RATE_LIMITED`：等待后重试
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
