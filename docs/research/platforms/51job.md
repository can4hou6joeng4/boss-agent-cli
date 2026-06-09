# 前程无忧（51job）适配器准入调研

> **结论先行**：**暂不直接进入真实适配器实现**。51job 具备明确用户价值，但 #230 同时覆盖岗位同步、简历投递、数据抓取和统一接口，范围过大且写操作/批量抓取风险不清。当前只建议进入 **research backlog**：先固化候选者侧只读 MVP、禁止能力和接入门槛；若后续能证明稳定、低风险、可测试的只读入口，再单独拆分 `51job candidate read-only search MVP`。
>
> 调研日期：2026-06-08 · 信息来源：Issue #230 需求、仓库平台抽象、公开页面/文档可观察边界与既有多平台研究模板。

## 1. 平台范围

- 平台名称：前程无忧 / 51job。
- 主要域名：`www.51job.com`、`m.51job.com` 以及登录/投递相关子域名（具体 API 网关需后续 DevTools 观察确认）。
- 用户角色：求职者、招聘者、企业管理员、猎头等。
- 当前研究对象：**求职者侧 candidate read-only** 能力，包括岗位搜索、岗位详情和有限的用户登录健康状态表达。
- 暂不支持招聘者侧能力：候选人搜索、简历库、批量联系、企业管理员权限均涉及更高隐私和平台风控风险，不能作为 #230 的第一阶段。
- 暂不支持写操作：自动投递、批量投递、简历同步、主动沟通、联系方式交换均需单独评估。

## 2. 认证方式

51job 的登录链路需要后续用浏览器 DevTools 在用户主动登录场景下确认。文档阶段只记录边界，不记录真实凭据：

- 可能入口：账号密码、手机验证码、扫码登录、浏览器 profile 复用。
- 本地凭据字段只能以字段名占位表达，例如 `cookie`、`token`、`csrf`、`session_id`，不得记录真实值。
- 若存在短期 token、设备指纹、请求签名、验证码或风控挑战，CLI 不得生成绕过参数，也不得把 stealth 或 response interception 作为认证路径。
- `boss status` / `boss doctor` 的未来表达应优先返回可恢复状态，例如 `AUTH_EXPIRED`、`NOT_SUPPORTED`、`PLATFORM_RISK`，并引导用户在官方页面完成登录或人工确认。

## 3. 只读能力

P0 只读能力只在存在稳定、低风险、可脱敏测试样本时进入实现：

| 能力 | 端点或页面证据 | 字段映射 | 风险等级 | 备注 |
|------|----------------|----------|----------|------|
| `search_jobs` | 待 DevTools 观察确认 | `job_id`、`title`、`company`、`city`、`salary`、`url` | medium | 仅用户主动查询；禁止自动翻页抓取 |
| `job_detail` | 待 DevTools 观察确认 | `description`、`requirements`、`company_info`、`source_url` | medium | 只读取用户指定岗位；必须脱敏日志 |
| `recommend_jobs` | 未确认 | — | high | 个性化推荐可能依赖登录态和行为画像，暂不作为 MVP |
| `user_info` | 未确认 | `display_name`、登录健康状态 | medium | 仅用于 status/doctor，不输出隐私字段 |

候选者侧 MVP 只应覆盖 `search_jobs` 与 `job_detail`。如果端点不稳定或只依赖 DOM/RPA 抓取，应保持 `NOT_SUPPORTED`，而不是降级为 scraper。

### 3.1 字段映射草案

`51job` 进入真实实现前，必须先用脱敏 fixture 证明字段能落到既有 `Platform` 抽象；字段缺失时只能留空或返回结构化错误，不得临时新增未评审字段。

| 统一字段 | 51job 候选来源 | 必需性 | 备注 |
|----------|----------------|--------|------|
| `job_id` | 职位详情 URL、页面 data 属性或只读 response 中的职位标识 | P0 必需 | 必须稳定；不得用列表序号或分页位置代替 |
| `title` | 职位标题 | P0 必需 | 去除广告/推荐标签后写入 |
| `company` | 公司名称 | P0 必需 | 不展开公司隐私或联系人字段 |
| `city` | 工作城市/地区 | P0 必需 | 保留原始城市粒度，避免推断通勤位置 |
| `salary` | 薪资展示文本 | P0 可选 | 原样保留平台展示；不推断月薪/年薪 |
| `url` / `source_url` | 官方职位 URL | P0 必需 | 仅保留用户可打开的官方链接 |
| `description` | 职位描述正文 | P0 detail 必需 | 必须脱敏日志，避免保存联系方式 |
| `requirements` | 任职要求/经验/学历文本 | P0 detail 可选 | 无稳定字段时并入 description |
| `company_info` | 公开公司简介/规模/行业 | P1 可选 | 不抓取工商、联系人或非公开信息 |

### 3.2 Readiness checklist

进入 `51job candidate read-only search MVP` 前，PR 描述必须逐项勾选：

- [ ] `search_jobs` 和 `job_detail` 均有用户主动触发入口，默认 CI 不访问真实网络。
- [ ] 脱敏 fixture 覆盖成功、空结果、登录过期、限流/风控和字段漂移。
- [ ] 错误响应保持 JSON 信封，并覆盖 `NOT_SUPPORTED`、`AUTH_EXPIRED`、`RATE_LIMITED` / `PLATFORM_RISK`、`PLATFORM_DRIFT`。
- [ ] 输出字段只包含职位与公开公司信息，不包含联系人、手机号、cookie、token、简历或聊天内容。
- [ ] 文档、schema/能力矩阵和测试同时说明：写操作、批量抓取、自动投递仍处于禁止或受限状态。

## 4. 受限能力

以下能力只有在用户显式 opt-in、官方页面人工确认和单独风险评估后才可重新讨论：

- 简历查看、简历同步、附件上传或附件解析。
- 自动投递、批量投递、重复投递、投递状态批量同步。
- 主动沟通、打招呼、聊天、交换联系方式。
- 招聘者侧候选人搜索、简历库访问、候选人详情读取。
- 任何需要验证码、滑块、设备指纹或请求签名生成的流程。

受限能力不得混入 #230 的第一阶段。后续若要做写操作，必须另开 issue，并证明平台条款、用户确认、速率限制、错误映射和脱敏审计都可控。

## 5. 禁止能力

- 禁止批量抓取岗位、公司、候选人或简历数据。
- 禁止自动化绕过验证码、滑块、短信、人机校验、设备指纹或登录风控。
- 禁止复制第三方 scraper 的 stealth、response interception、自动滚动抓取或代理池方案。
- 禁止保存、提交或打印真实账号、密码、cookie、token、手机号、身份证、微信号、简历、聊天记录和候选人隐私。
- 禁止把 Windows app、RPA 工具、CloakBrowser、stealth profile 作为降低风险或绕过平台策略的理由。

## 6. 错误映射与 JSON 信封

51job 在未进入真实实现前应显式表达不可用状态，而不是静默回退：

| 场景 | 建议错误码 | 说明 |
|------|------------|------|
| 未实现 51job adapter | `NOT_SUPPORTED` | 返回清晰文案：51job blocked pending API research |
| 登录态过期或用户未登录 | `AUTH_EXPIRED` | 引导官方页面重新登录，不自动绕过 |
| 平台触发验证码/风控/限流 | `PLATFORM_RISK` 或 `RATE_LIMITED` | 停止自动流程并提示人工确认 |
| 字段包络变化 | `PLATFORM_DRIFT` | 保留脱敏样本后修复字段映射 |

所有未来实现都必须继续遵守 JSON 信封、脱敏日志和本地优先原则。

## 7. 统一适配器评估

### 当前判定

- 平台价值：中等。51job 与智联同属传统招聘平台，能补齐多渠道覆盖。
- 技术确定性：低到中。当前缺少稳定公开 API、response envelope 和字段样本。
- 合规风险：中到高。Issue #230 的“数据抓取、简历投递”容易越过项目低风险边界。
- 当前状态：**research backlog / blocked**，不进入 runtime path。

### Adapter admission gate

进入 `Platform` 注册表或真实实现前必须同时满足：

1. 有稳定的 candidate-side 只读入口，且不依赖 stealth、response interception、自动滚动抓取或验证码绕过。
2. `search_jobs` 与 `job_detail` 有脱敏 fixture，字段能映射到既有 `Platform` 抽象。
3. 未登录、登录过期、限流、风控、字段漂移都有 JSON 信封错误映射。
4. 文档明确 P0 只读、受限、禁止三类能力，且 docs/platform-risk.md 的边界仍覆盖新增场景。
5. 测试至少覆盖文档契约、包络适配、错误映射和 registry 行为；真实网络访问不得进入默认 CI。

### 推荐拆分

- PR 1：调研文档 + ROADMAP/索引 + 文档契约（当前阶段）。
- PR 2：可选 `qiancheng`/`51job` adapter stub，默认返回 `NOT_SUPPORTED`。
- PR 3：若 API 证据充分，再做 candidate-side read-only `search_jobs` / `job_detail` MVP。

## 8. 测试样本要求

允许的样本：

- 公开页面结构、robots.txt、官方帮助文档和脱敏后的 Network response 字段形态。
- 人工构造的最小 JSON fixture，用于字段映射和错误包络测试。
- dry-run 输出和 registry 行为测试。

禁止的样本：

- 真实 cookie/token、真实手机号、真实简历、真实聊天记录、真实候选人资料。
- 可复用绕过脚本、stealth 配置、代理池或 response interception 代码。
- 大规模岗位列表或企业数据快照。

## 9. 验收命令

文档阶段至少运行：

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

若未来进入实现阶段，再补充平台相关单元测试、`uv run ruff check src/ tests/`、`uv run mypy src/boss_agent_cli` 和必要 dry-run。

## 参考资料

- Issue #230：前程无忧（51job）支持需求。
- 多平台适配器研究模板：[README.md](README.md)。
- 平台风险边界：[../../platform-risk.md](../../platform-risk.md)。
- 平台抽象设计：[../../platform-abstraction.md](../../platform-abstraction.md)。

---

> 本报告仅作 **准入评估**。在缺少稳定只读 API、脱敏 fixture 和错误映射前，51job 不应进入真实运行路径。
