# Agent Quickstart

面向 AI Agent 的最短上手路径：先识别能力，再跑通搜索到行动的闭环。

## 1) 安装与环境准备

```bash
uv tool install git+https://github.com/can4hou6joeng4/boss-agent-cli.git
patchright install chromium
boss doctor
boss login
boss status
```

完成标准：
- `boss doctor` 返回 `ok=true`
- `boss status` 返回当前登录态

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

能力映射明细见 [Capability Matrix](capability-matrix.md)。
