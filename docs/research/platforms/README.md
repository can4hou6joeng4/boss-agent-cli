# 多平台适配器研究模板

本目录存放招聘平台适配器研究资料。每份研究文档的目标不是交付
crawler，而是判断某个平台是否适合进入 boss-agent-cli 的
`Platform` / `RecruiterPlatform` 抽象，并把只读能力、认证边界、字段
映射、风险等级、禁止能力和验收样本记录成后续实现可复用的依据。

## 准入原则

- 只研究公开可观察的信息：官方页面、官方文档、浏览器 DevTools Network
  面板、robots.txt、仓库内既有抽象和公开开源项目中的字段形态。
- 默认低风险：本地辅助、只读优先、用户主动触发、不规避风控、不批量
  触达、不抓取平台数据。
- 第三方 scraper、stealth、response interception、自动滚动抓取、批量触达
  示例只能作为风险观察材料，不能直接复制为主线实现。
- 任何研究文档都不得包含真实 cookie、token、账号、手机号、微信号、
  候选人隐私、公司私有信息或真实 `security_id`。
- 研究结论可以是“不建议接入”。这类结论应说明 ROI、维护成本、合规
  风险和未来重启条件。

## 研究模板

每个平台一份 `docs/research/platforms/<name>.md`。新平台文档必须包含
以下章节；已有文档可保留历史 7 项清单，但需要补齐“统一适配器评估”
章节。

### 1. 平台范围

- 平台名称、域名、用户角色：求职者、招聘者、猎头、企业管理员等。
- 当前研究对象：候选者侧、招聘者侧、只读能力、浏览器辅助能力等。
- 不研究或暂不支持的角色和原因。

### 2. 认证方式

- 登录入口：扫码、验证码、账号密码、SSO、浏览器 profile、Cookie 提取。
- 本地凭据字段：只记录字段名和用途，不记录真实值。
- 是否存在 stoken、csrf、设备指纹、请求签名或短期 token。
- `boss status` / `boss doctor` 应如何表达健康状态和恢复动作。

### 3. 只读能力

按 `Platform` 抽象列出可行能力：

| 能力 | 端点或页面证据 | 字段映射 | 风险等级 | 备注 |
|------|----------------|----------|----------|------|
| `search_jobs` | — | — | low / medium / high | — |
| `job_detail` | — | — | low / medium / high | — |
| `recommend_jobs` | — | — | low / medium / high | — |
| `user_info` | — | — | low / medium / high | — |

### 4. 受限能力

列出需要显式 opt-in、浏览器人工确认、官方页面跳转或未来重新评估的能力，
例如简历、投递记录、沟通列表、聊天记录、招聘者侧候选人列表。

### 5. 禁止能力

必须明确以下能力是否禁止进入 CLI 主线：

- 自动打招呼、批量打招呼、自动投递、自动消息回复。
- 绕过验证码、绕过滑块、绕过账号风控、模拟真实指纹规避检测。
- 自动滚动抓取列表、批量导出平台数据、采集候选人隐私。
- 复制第三方 stealth scraper 或 response interception 作为生产实现。

### 6. 端点和字段证据

- 端点域名、路径、HTTP 方法、响应包络、分页字段、错误码语义。
- 只保留字段名、结构片段和脱敏样例。
- 记录与 BOSS 直聘的字段差异，例如 `zpData` / `data` / `content`、
  成功码、职位 ID、城市编码、薪资字段。

### 7. 风险评级

至少覆盖：

- 账号风险：登录频繁、验证码、账号冷却、平台限额。
- 技术漂移：签名变更、前端构建密钥、HTML 结构变化、端点迁移。
- 合规边界：平台条款、个人信息、招聘者数据、写操作。
- 运维成本：token 生命周期、浏览器依赖、测试可复现性。

### 8. 测试样本

- 允许的样本：脱敏 JSON 包络、字段名、mock 响应、dry-run 计划。
- 禁止的样本：真实账号、真实 cookie/token、真实聊天记录、真实候选人
  简历、可复用绕过脚本。

### 9. 验收命令

文档阶段至少运行：

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

实现阶段再补充平台相关单元测试、`uv run ruff check src/ tests/`、
`uv run mypy src/boss_agent_cli` 和必要的 dry-run。

## 已交付调研

| 平台 | 报告 | 当前建议 | 状态 |
|------|------|----------|------|
| BOSS 直聘 | [zhipin.md](zhipin.md) | 已接入基线；继续低风险只读优先 | 基线 |
| 智联 | [zhaopin.md](zhaopin.md) | 候选者侧可作为优先扩展；招聘者侧暂不接入 | 候选 |
| 智联招聘者侧 | [zhaopin-recruiter-evaluation.md](zhaopin-recruiter-evaluation.md) | 只保留评估提纲 | 调研中 |
| 前程无忧 / 51job | [51job.md](51job.md) | 先固化 candidate 侧只读准入门槛；暂不进入真实实现 | Research backlog |
| 拉勾 | [lagou.md](lagou.md) | 不建议近期接入 | 风险占位 |
| 猎聘 | [liepin.md](liepin.md) | 不建议 v2.0 接入 | 风险占位 |

## 平台准入流程

1. 新增或更新 `docs/research/platforms/<name>.md`，按本 README 的模板补齐
   研究结论。
2. 在研究文档中标记 `P0 只读`、`受限`、`禁止` 三类能力，不允许以
   stealth scraper 样例替代主线设计。
3. 若结论允许进入主线，在 [Platform 抽象设计与迁移 SOP](../../platform-abstraction.md)
   中确认抽象契约、注册方式和测试位点仍适用。
4. 若涉及账号、认证、浏览器通道或招聘者数据，在 [平台风险边界](../../platform-risk.md)
   中确认低风险边界仍覆盖新增场景。
5. 平台 stub 或真实现必须单独开任务，先补契约测试，再逐步开放只读能力。

## 第三方样本使用边界

`xunjin58/zp_api` 一类仓库可以帮助观察字段命名、平台列表和历史端点，
但其中的 stealth、response interception、自动滚动抓取、批量提取数据
等做法只能记录为风险信号。主线实现必须回到本项目的低风险契约：
使用平台抽象、JSON 信封、脱敏输出、显式用户触发和默认合规阻断。

## 后续路线

优先维护 BOSS 直聘基线和智联候选者侧能力。前程无忧 / 51job 先保留在
research backlog：只有在证明 candidate 侧只读入口、风险边界、字段映射和
脱敏测试样本都清晰后，才进入平台 stub 或真实现阶段。拉勾、猎聘和其他
平台同样必须先通过准入评估。
