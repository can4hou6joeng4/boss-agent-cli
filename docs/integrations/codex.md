# Codex Integration Example

适用版本：`boss-agent-cli` 当前 CLI 契约（2026-04-13）

## 适用场景

- Agent 直接运行终端命令
- 需要多步串联 `schema -> status -> search -> detail -> greet`
- 需要把 stdout JSON 结果继续喂给后续决策

## 最小接入流程

先让 Agent 遵守这条工作习惯：

```text
当任务涉及 BOSS 直聘搜索、查看详情、打招呼或推进候选流程时：
1. 先运行 boss schema 获取能力与参数
2. 再运行 boss status 检查登录态
3. 未登录时执行 boss login，并提示用户完成扫码
4. 搜索时优先调用 boss search
5. 命中目标后调用 boss detail
6. 需要主动触达时调用 boss greet
7. 招聘者场景优先使用 boss hr applications / candidates / reply / request-resume
8. 只解析 stdout JSON，ok=false 时读取 error.code 与 error.recovery_action
```

最小命令链路：

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

招聘者最小链路：

```bash
boss schema
boss status
boss hr applications
boss hr candidates "Golang"
boss hr reply <friend_id> "你好"
```

推荐解析字段：

- `ok`: 判断命令是否成功
- `data`: 读取职位、详情或动作结果
- `hints.next_actions`: 决定下一条命令
- `error.code`: 做恢复分流
- `error.recovery_action`: 告诉 Agent 如何修复

## 失败恢复

优先按这个顺序恢复：

```bash
boss doctor
boss status
boss login
boss search "Golang" --city 广州
```

常见分流：

- `AUTH_REQUIRED` / `AUTH_EXPIRED`: 重新执行 `boss login`
- `INVALID_PARAM`: 回退到 `boss schema` 校验参数
- `RATE_LIMITED`: 等待后重试，不要盲目连发 `boss greet`
