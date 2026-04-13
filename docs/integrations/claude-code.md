# Claude Code Integration Example

适用版本：`boss-agent-cli` 当前 CLI 契约（2026-04-13）

## 适用场景

- 团队通过 skill 分发统一的求职能力
- 希望 Agent 在规则文件里固定 BOSS 直聘操作顺序
- 需要把 `boss` 命令当成稳定 shell 能力暴露给 Claude Code

## 最小接入流程

优先安装 skill：

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

如果你走规则文件方式，可以直接放入以下约束：

```markdown
当用户要求搜索职位、查看职位详情、打招呼或推进招聘沟通时：
1. 先运行 boss schema
2. 再运行 boss status
3. 未登录时运行 boss login
4. 搜索使用 boss search
5. 查看详情使用 boss detail
6. 执行动作使用 boss greet
7. 只读取 stdout JSON，不解析 stderr
```

最小命令链路：

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

接入建议：

- 把 `boss schema` 结果当成能力真源
- 把 `boss detail` 返回结果拼回上下文，再决定是否 `boss greet`
- 遇到 `ok=false` 时优先消费 `error.recovery_action`

## 失败恢复

推荐恢复顺序：

```bash
boss doctor
boss status
boss login
boss search "Golang" --city 广州
```

常见恢复动作：

- 登录失效：重新执行 `boss login`
- 参数错误：回到 `boss schema` 或 `boss cities`
- 环境异常：先跑 `boss doctor`，再决定是否继续 `boss greet`
