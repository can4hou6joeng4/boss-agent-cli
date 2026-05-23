# BOSS 直聘（Zhipin）适配器基线研究

> **结论先行**：BOSS 直聘是 boss-agent-cli 的既有基线平台。后续扩展
> 应以它的 `BossPlatform` 行为、JSON 信封、错误映射、缓存和低风险合规
> 边界为对照，而不是把第三方自动化脚本当成实现模板。
>
> 调研日期：2026-05-24 · 调研人：can4hou6joeng4 · 信息来源：仓库现有实现、
> 测试契约、公开页面字段观察和历史平台研究文档

## 1. 平台范围

| 项 | 结论 |
|----|------|
| 平台 | BOSS 直聘 / Zhipin |
| 主要域名 | `www.zhipin.com` |
| 当前角色 | 求职者侧为主；招聘者侧通过独立 `zhipin-recruiter` 适配器受限开放 |
| 当前实现 | `BossPlatform` 委托 `BossClient`；招聘者侧委托 `BossRecruiterPlatform` |
| 研究用途 | 作为其他平台的字段、错误、能力分层和风险边界基线 |

本基线不引入新端点，不扩大招聘者侧权限，也不放宽默认低风险模式。

## 2. 认证方式

| 机制 | 当前处理 | 适配器影响 |
|------|----------|------------|
| Cookie 登录态 | 本地加密存储，只输出脱敏健康状态 | `AuthManager` 统一读取，不在命令层解析 |
| `wt2` | 主 Cookie 字段之一 | 只记录存在性，不输出原值 |
| `__zp_stoken__` | 页面 JS 生成的动态字段 | 缺失时属于部分登录态，需用户主动浏览器恢复 |
| CDP / Bridge | 用户主动启动的浏览器辅助通道 | 用于登录兼容，不作为风控重试通道 |
| patchright | 浏览器兼容依赖 | 命中风控时停止自动化，不切换通道继续重试 |

`boss status` 默认只做本地健康诊断；`boss status --live` 和
`boss doctor --live-probe` 才能做显式只读在线探测。

## 3. 只读能力

| 能力 | 当前状态 | 字段映射 | 风险等级 | 备注 |
|------|----------|----------|----------|------|
| `search_jobs` | 已支持 | `zpData.jobList` → 统一职位列表 | low | 支持关键词、城市、福利、多选筛选和用户复制搜索 URL |
| `job_detail` | 已支持 | `zpData` → 职位详情 | low | 需要 `security_id` 或等价职位标识 |
| `recommend_jobs` | 已支持 | `zpData` → 推荐列表 | low | 只读 |
| `user_info` | 已支持 | `zpData` → 用户信息摘要 | medium | 只输出必要字段并依赖脱敏 |
| `resume_baseinfo` / `resume_expect` | 已支持 | 简历字段 → 结构化摘要 | medium | 涉及个人资料，禁止提交真实样本 |
| `deliver_list` / `interview_data` / `job_history` | 已支持 | 平台列表 → 结构化记录 | medium | 只读，但需注意个人数据 |
| `chat_history` | 受限只读 | 平台消息 → compact / raw 输出 | high | 默认低风险模式下受合规门控 |

## 4. 受限能力

- 招聘者侧候选人搜索、简历、最近消息摘要和消息回复默认被低风险模式阻断。
- `chat_history --raw` 是输出保真选项，不得绕过合规门控。
- 联系方式交换、标签修改、沟通相关动作需要用户明确理解风险；默认应回到
  官方页面手动完成。
- 真实流烟测必须使用 dry-run 或显式环境变量，不进入普通 CI 自动访问真实账号。

## 5. 禁止能力

- 不实现自动打招呼、批量打招呼、自动投递、自动消息回复的默认放行路径。
- 不通过 CDP、patchright、Bridge 或其他通道重试已经被平台风控拦截的请求。
- 不复制 stealth、response interception、自动滚动抓取或批量导出脚本。
- 不在文档、测试、日志或 JSON 信封中保存真实 cookie、token、手机号、微信号、
  真实聊天记录、候选人简历或真实 `security_id`。

## 6. 端点和字段证据

| 维度 | BOSS 基线 |
|------|-----------|
| 成功码 | `code == 0` |
| 数据包络 | `zpData` |
| 常见风控码 | `code=36` 账号风险、`code=37` stoken 过期、`code=9` 限流 |
| 职位 ID | `encryptJobId`、`securityId` 等加密标识 |
| 薪资字段 | `salaryDesc` |
| 城市字段 | 数字 code |
| 输出契约 | 命令层统一转成 `{ok, schema_version, command, data, pagination, error, hints}` |

字段证据来自仓库内 `src/boss_agent_cli/api/`、`src/boss_agent_cli/platforms/`
和对应测试，不依赖外部 scraper 的运行结果。

## 7. 风险评级

| 风险 | 等级 | 控制方式 |
|------|------|----------|
| 登录态漂移 | medium | `auth/health.py` 分层诊断，恢复动作只输出脱敏提示 |
| stoken 缺失 | medium | 标记部分登录态，引导用户主动浏览器恢复 |
| 搜索/详情端点漂移 | medium | mock 测试保护结构，真实失败按平台漂移处理 |
| 招聘者数据 | high | 默认低风险模式阻断 |
| 写操作和联系方式交换 | high | 默认阻断，回到官方页面手动完成 |
| 第三方 stealth scraper 诱导 | high | 仅作为风险观察，不进入实现路径 |

## 8. 测试样本

允许：

- mock 响应包络和脱敏字段名。
- dry-run 计划，例如 `BOSS_SMOKE_DRY_RUN=1`。
- 只包含 `<redacted>` 的错误报告样例。

禁止：

- 真实 cookie、token、stoken、手机号、微信号、公司私有信息。
- 真实聊天消息、真实简历、真实候选人或招聘者资料。
- 可复用的风控绕过、stealth 或批量抓取脚本。

## 9. 验收命令

基线文档变更至少运行：

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

若改动影响平台抽象或命令行为，还需运行：

```bash
uv run pytest tests/test_platform_base.py tests/test_commands.py -q
uv run ruff check src/ tests/
uv run mypy src/boss_agent_cli
```

## 与其他研究文档的关系

- 智联候选者侧研究：[zhaopin.md](zhaopin.md)
- 拉勾风险占位：[lagou.md](lagou.md)
- 猎聘风险占位：[liepin.md](liepin.md)
- 平台准入模板：[README.md](README.md)
