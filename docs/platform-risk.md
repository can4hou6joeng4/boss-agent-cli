# 平台风险边界

boss-agent-cli 保留 `assisted|research` 配置兼容性，两种模式均可调用全部已实现能力。项目不控制平台规则、账号风控、接口变更或第三方浏览器环境；开放能力不取消运行预算、checkpoint、停止、脱敏和数据保管要求。

## 0. 开放能力与运行控制

候选者写操作、批量触达、招聘者候选人数据和浏览器采集均可进入各自业务层。调用方必须：

- 使用显式输入、数量、timeout、retry 和 stop 条件；
- 保存并复用 workflow/crawl `run_id`，不得通过扫描“最新任务”隐式恢复；
- 对 Cookie、Token、`security_id`、联系方式、简历和聊天数据持续脱敏；
- 遇到 `ACCOUNT_RISK`、安全页或预算耗尽时 checkpoint 并停止，不无界换通道重试。

平台未实现的能力返回 `NOT_SUPPORTED`，不得为了完成 workflow 伪造成功。`COMPLIANCE_BLOCKED` 只作为历史错误码保留，当前执行路径不主动产生。

## 0.1 历史模式兼容

```bash
boss config set operating_mode research
boss schema --format native
```

两种模式共享以下运行约束：

- 抓取页数、详情请求、重试和运行时间必须有默认上限；不得以 `0` 隐式表达无限运行。
- 命中风险页或风险码时先保存 checkpoint；继续需要新的用户动作。
- 浏览器 adapter 应使用独立 profile，不能关闭或污染用户原有标签页/profile。
- Cookie、Token、`__zp_stoken__`、`security_id` 和真实个人信息不得进入日志、错误、导出、fixture 或公开 PR 证据。
- vendored hook 必须记录上游 repository、commit、license 和内容 hash，并提供逐项开关。
- 输出数据必须有本地清理和 retention 机制；不得默认全量导入 shortlist 或其他工作流。

## 1. 平台接口可能变化

项目依赖 BOSS 直聘、智联招聘等平台的网页、接口、Cookie、登录态和响应结构。平台可能随时调整字段、风控、接口路径或页面行为。

当出现以下现象时，优先按平台漂移处理：

- 之前正常的只读命令突然返回 `NETWORK_ERROR`、`AUTH_EXPIRED`、`TOKEN_REFRESH_FAILED` 或结构异常。
- `search` 有结果但 `detail` 失败。
- `boss schema --format native` 正常，但 live 命令失败。
- 同一命令在 mock 测试中通过，在真实账号中失败。

## 2. 登录和 Cookie 边界

登录链路会使用 Cookie 提取、CDP、QR httpx 或浏览器兜底。项目只在本地读取和保存登录态，不要求用户把 Cookie、Token、手机号、微信号、姓名、公司信息或 `security_id` 提交到仓库。任何 adapter 都不得把登录兼容能力升级为风控重试通道，并必须遵守本页 0.1 节约束。

`boss status` 默认只检查本地加密凭据和分层健康状态，不请求真实平台；需要确认在线只读接口是否可用时，必须显式运行 `boss status --live` 或 `boss doctor --live-probe`。命中风控时停止当前 workflow 并保留 checkpoint。`wt2` 存在但 `__zp_stoken__` 缺失时属于部分登录态，通常需要通过真实页面 JS 生成；可在用户主动操作下以 Chrome CDP 远程调试端口启动浏览器后运行 `boss login --cdp`，但不得把 CDP 当成风控绕过通道。

提交 Issue 前必须脱敏：

```json
{
	"security_id": "<redacted>",
	"cookie": "<redacted>",
	"token": "<redacted>"
}
```

## 3. 请求频率和账号责任

默认请求间隔由 `--delay` 控制。采集、写操作和批量触达必须使用有限预算、显式用户输入与停止条件；不得把模式配置解释为取消账号、数据和运行责任。

BOSS 职位列表请求还共享 SQLite 中的持久预算：普通 `boss search` 与 crawler
跨 CLI 进程串行预留 5–10 秒请求窗口。搜索缓存命中不会占用请求窗口；删除
本地缓存或并行启动多个进程不得用于绕过该预算。

## 4. 浏览器自动化边界

patchright、CDP、Chrome 本地 profile、系统钥匙串、浏览器插件和平台风控都会影响登录与访问稳定性。浏览器能打开不代表 httpx 链路一定可用；httpx 链路可用也不代表浏览器自动化链路一定可用。任何模式命中风控时都停止；声明的 adapter 不能无界切换通道或无限重试。

Windows 客户端、可见浏览器、CloakBrowser、RPA 工具或指纹浏览器都必须遵守相同的预算、脱敏、checkpoint 和停止策略。普通 CI 不得自动运行真实风控研究。

第三方仓库中的 stealth、response interception、自动滚动抓取、批量提取或模拟真实用户指纹实现，只有在来源、license、commit、hash、数据路径和停止行为完成审计后，才能进入声明的 adapter；不得进入真实账号 CI。新平台接入仍必须先通过 [多平台适配器研究模板](research/platforms/README.md) 的准入评估。

## 5. 烟测边界

真实流烟测必须显式配置环境变量，不应在普通 CI 中自动访问真实账号：

```bash
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
BOSS_SMOKE_PLATFORM=zhipin BOSS_SMOKE_QUERY=Golang BOSS_SMOKE_SECURITY_ID=<redacted> uv run python scripts/smoke_p0.py
```

`BOSS_SMOKE_DRY_RUN=1` 只验证计划，不验证真实平台可用性。

## 6. 报告安全问题

如果问题涉及 Cookie、Token、账号、联系方式、私有简历、公司内部信息或可利用的自动化绕过路径，不要公开发 Issue。请按 [SECURITY.md](../SECURITY.md) 使用私密渠道报告。
