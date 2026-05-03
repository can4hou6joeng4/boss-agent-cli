# 平台风险边界

boss-agent-cli 自动化访问招聘平台，但不控制平台规则、账号风控、接口变更或第三方浏览器环境。使用者需要理解以下边界。

## 1. 平台接口可能变化

项目依赖 BOSS 直聘、智联招聘等平台的网页、接口、Cookie、登录态和响应结构。平台可能随时调整字段、风控、接口路径或页面行为。

当出现以下现象时，优先按平台漂移处理：

- 之前正常的只读命令突然返回 `NETWORK_ERROR`、`AUTH_EXPIRED`、`TOKEN_REFRESH_FAILED` 或结构异常。
- `search` 有结果但 `detail` 失败。
- `boss schema --format native` 正常，但 live 命令失败。
- 同一命令在 mock 测试中通过，在真实账号中失败。

## 2. 登录和 Cookie 边界

登录链路会使用 Cookie 提取、CDP、QR httpx 或 patchright 浏览器自动化。项目只在本地读取和保存登录态，不要求用户把 Cookie、Token、手机号、微信号、姓名、公司信息或 `security_id` 提交到仓库。

提交 Issue 前必须脱敏：

```json
{
	"security_id": "<redacted>",
	"cookie": "<redacted>",
	"token": "<redacted>"
}
```

## 3. 请求频率和账号责任

默认请求间隔由 `--delay` 控制。不要把本工具用于高频抓取、批量骚扰、绕过平台限制或违反平台条款的用途。写操作，例如 `greet`、`apply`、`exchange`、`hr reply`，应先使用只读命令确认目标和上下文。

## 4. 浏览器自动化边界

patchright、CDP、Chrome 本地 profile、系统钥匙串、浏览器插件和平台风控都会影响登录与访问稳定性。浏览器能打开不代表 httpx 链路一定可用；httpx 链路可用也不代表 patchright 登录链路一定可用。

## 5. 烟测边界

真实流烟测必须显式配置环境变量，不应在普通 CI 中自动访问真实账号：

```bash
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py
BOSS_SMOKE_PLATFORM=zhipin BOSS_SMOKE_QUERY=Golang BOSS_SMOKE_SECURITY_ID=<redacted> uv run python scripts/smoke_p0.py
```

`BOSS_SMOKE_DRY_RUN=1` 只验证计划，不验证真实平台可用性。

## 6. 报告安全问题

如果问题涉及 Cookie、Token、账号、联系方式、私有简历、公司内部信息或可利用的自动化绕过路径，不要公开发 Issue。请按 [SECURITY.md](../SECURITY.md) 使用私密渠道报告。
