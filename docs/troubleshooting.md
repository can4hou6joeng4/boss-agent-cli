# 诊断与排障

> 遇到问题先跑 `boss doctor` 和 `boss status`——绝大多数故障的恢复动作会直接写在
> 错误信封的 `error.recovery_action` 字段里。英文版见 [troubleshooting.en.md](troubleshooting.en.md)。
> 涉及 Cookie、CDP、patchright、真实账号、请求频率或平台接口漂移的问题，
> 请先阅读 [平台风险边界](platform-risk.md)。

```bash
boss doctor
boss status
# 可选：执行一次低频只读平台验证
boss status --live
boss doctor --live-probe
```

## doctor 检查项

`hints.next_actions` 只提供可执行的后继命令，保留当前数据目录、平台、浏览器来源和已指定的 CDP 地址。扫码、官方页面操作、条件性重建登录态与风控停止提醒放在 `hints.operator_actions`；TTY 将这些真人指引显示到 stderr，Agent 应转述而不是自动执行。

`quality_baseline` 与 `quality_tool_*` 仅在当前 CLI 从本项目源码运行时检查。普通安装包用户不需要源码仓库或 ruff/pytest/mypy，也不会收到运行仓库相对路径脚本的建议。源码维护者指引会明确应进入的仓库根目录；其余浏览器和认证检查的语义不变。

| 检查项 | 说明 |
|--------|------|
| `python` | Python 版本 >= 3.10 |
| `patchright` | CLI 已安装 |
| `patchright_chromium` | patchright 所需的 Chromium 与 headless shell 修订版已安装；Windows 同时检查 `%LOCALAPPDATA%\ms-playwright` |
| `windows_uv_tool_path` | Windows 全局 `uv tool` 命令目录是否在 PATH 中 |
| `quality_baseline` | 源码仓库内的 P0 本地质量基线入口是否可用 |
| `quality_tool_ruff` / `quality_tool_pytest` / `quality_tool_mypy` | 本机质量工具可用性；缺失时可通过 `uv run` 或 `uv sync --all-extras` 使用项目环境 |
| `cookie_extract` | 本地浏览器 Cookie 可提取 |
| `credential_file` | 登录态文件是否存在且可读取 |
| `auth_session` | 登录态存在且可解密 |
| `cookie_presence` / `wt2_presence` | Cookie 与核心 Cookie 是否存在 |
| `stoken_presence` / `stoken_freshness` | `__zp_stoken__` 是否生成、是否可能过期 |
| `auth_token_quality` | 核心凭据（wt2 / stoken） |
| `cookie_completeness` | 辅助凭据（wbg / zp_at） |
| `cdp` | Chrome 调试端口可连 |
| `cdp_risk_lock` | 是否记录了 CDP code 37 风控锁（只读本地文件，不连浏览器、不访问网络） |
| `browser_channel` | CDP 兼容通道状态；不得用于规避平台风控 |
| `candidate_search_health` / `candidate_detail_health` | 求职者只读能力前置条件 |
| `recruiter_read_health` | 招聘者只读能力前置条件 |
| `network` | zhipin.com 可访问 |

## 常见问题修复

```bash
# 安装浏览器内核
patchright install chromium
# 全局 tool 环境提示缺 headless shell 时再执行
patchright install chromium-headless-shell

# 重建登录态
boss logout && boss login

# CDP 诊断
boss --cdp-url http://localhost:9222 doctor

# 默认 status 只检查本地凭据；需要真实只读验证时显式加 --live
boss status --live
```

**`AUTH_REQUIRED` 不代表 CLI 故障**：它表示当前数据目录没有可用登录态。真实平台
`search`、`detail`、`status --live` 验证必须先执行 `boss login`；登录前只验证 CLI
本地命令、schema、MCP 和 doctor。

**Windows 全局 `boss` 命令找不到**：如果 `uv tool update-shell` 超时，可先临时修复：

```powershell
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

永久修复仍建议在网络稳定时重跑 `uv tool update-shell`，或手动把
`C:\Users\<你>\.local\bin` 加入用户 PATH。

**Windows 中文系统跑测试**：默认 GBK 终端可能导致 UnicodeDecodeError。使用：

```powershell
$env:PYTHONUTF8='1'
uv run python scripts/quality_baseline.py
```

**auth_session 显示"损坏"**：登录态来自旧机器指纹或文件损坏 → `boss logout && boss login`

**auth_token_quality 各状态含义**：

- `wt2/stoken 均存在`：完整，可正常使用
- `wt2 存在，stoken 缺失`：部分可用，通常是二维码或 Cookie 提取只拿到部分登录态；建议以 Chrome CDP 远程调试端口启动浏览器后运行 `boss login --cdp`，或重新执行 `boss login`
- `wt2 缺失`：无效 → `boss logout && boss login`

## v3.0.0：从 Browser Bridge 迁移

Browser Bridge daemon、Chrome 扩展及 `[bridge]` 安装 extra 已移除。`boss doctor` 不再探测旧 daemon，也不再输出扩展检查项。

**升级 CLI 不会停止旧 daemon，也不会卸载浏览器扩展。** 请手动停止旧 daemon（包括其他 Python 环境中仍运行的副本），并在 `chrome://extensions` 中禁用或移除旧扩展；从安装命令中去掉 `[bridge]`，不要重新启动已移除的服务。迁移不要求删除凭据或日常浏览器 profile。

如果需要不读取本地凭据的只读访问，请先在已运行的本机 CDP 浏览器中手动打开并登录 BOSS 直聘，保留该页签，然后显式选择：

```bash
boss --browser-source existing-browser --cdp-url http://localhost:9222 chat
```

该来源只复用已有目标页，不读本地凭据、不新建 context/页面、不导航或启动浏览器；不可用时返回 `BROWSER_SESSION_NOT_FOUND`，不降级。`chat` / `chatmsg` 在 `auto` 下仍走 httpx，其他显式浏览器来源遵循各自策略。CDP 调试端口仅用于本机可信环境，不应暴露到公网。命中平台风控时停止 workflow 并保存 checkpoint，不要切换通道重试。

## CDP 启动示例

macOS：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Linux：

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/boss-chrome
```

Windows PowerShell：

```powershell
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "Google Chrome executable was not found" }

& $chrome `
  --remote-debugging-port=9222 `
  --remote-allow-origins=* `
  --user-data-dir="$env:LOCALAPPDATA\boss-agent-cdp-profile"
```

启动后在另一个终端使用 CDP 登录：

```bash
boss --cdp-url http://localhost:9222 login --cdp
```

`login --cdp` 会先扫描 CDP Chrome 中所有浏览器 context：若已存在带 BOSS 登录态
（`wt2`）的 context，则直接复用它和既有 zhipin 页签，不导航登录页、不轮询等待；
只在找不到登录态时才打开登录页扫码。页签清理只作用于本次调用新建的页面，
用户已打开的页签不会被关闭。

复用时会在终端打出选中的 context 序号与「账号指纹」（登录态 cookie 值的不可逆
哈希前缀，不泄露 cookie 本身），例如 `context 2/2，账号指纹 c26a7f01`。同时开多个
浏览器窗口/无痕页或多个 profile 各登不同 BOSS 账号时，复用的是「第一个带登录态的
context」；若指纹对应的账号不是你要的，请关闭多余窗口或只保留目标账号的登录态后重试。

复用命中后会在同一页面里用浏览器自身的会话做**一次**只读探测（用户信息接口，不读本地凭据）：
通过则落盘并提示「已通过只读验证」；服务端已失效则不落盘，自动改走下面的强制重登路径；探测命中
`ACCOUNT_RISK` / `ENVIRONMENT_RISK` 立即停止，不重试、不换通道。页面未就绪或探测本身失败
（网络 / 超时）时按「未验证」继续复用并在 stderr 提示。

需要跳过复用时（切换账号、或未验证的复用之后命令仍报 `AUTH_REQUIRED`），用
`boss login --cdp --force` 强制重登——它不扫描、不复用任何已登录 context，先清掉**当前 context
内目标平台域**的 cookie（其他站点与其他 context 不动，但这意味着该 Chrome 里的平台账号会被登出），
再打开登录页重新扫码。

## 锁定浏览器通道：`--browser-source`

`--browser-source stored-cookie --cdp-url <地址>` 是 fail-closed 的严格模式：把浏览器通道锁定为你指定的那个 CDP 端点并禁止降级到 headless，不可用时立即返回 `CDP_UNAVAILABLE`。该端点可以是你日常 Chrome 的调试端口，也可以是长期复用的专用调试 profile——**它只保证「锁定通道」，不保证复用你日常浏览器的登录会话**。若你要的是后者，请用 `--browser-source existing-browser`。

> 注：`CDP_UNAVAILABLE` 的 `recovery_action` 依上下文而定，信封里的值是权威值，`boss schema` 里声明的是默认建议。

三类来源对比：

| 来源 | 通道 | 读本地凭据 | 自动探测 9222 | 启动浏览器 | 失败错误码 |
|---|---|---|---|---|---|
| `auto`（默认） | CDP→headless | 是 | 是 | 允许 | NETWORK_ERROR |
| `existing-browser` | 仅已有 CDP | 否 | 是 | 禁止 | BROWSER_SESSION_NOT_FOUND |
| `stored-cookie` | 仅指定 CDP | 是 | 否 | 禁止 | CDP_UNAVAILABLE |

## CDP 模式下一直报 code 37（`ENVIRONMENT_RISK` / `ENVIRONMENT_RISK_LOCKED`）

现象：配了 `--cdp-url`（或 `--browser-source existing-browser` / `stored-cookie`）后，CLI 的每个浏览器请求都返回
code 37「访问环境存在异常」，但在同一个 Chrome 里手动浏览 BOSS 直聘一切正常。

原因：Chrome 里的 `__zp_stoken__` 由 BOSS 前端页面的安全脚本维护。旧版本在 CDP 模式下，`job_card` 等读取会先用
httpx 带着从 Chrome 拷出来的 Cookie，把 `__zp_stoken__` 当查询参数发出去，同时浏览器通道也在发请求。同一个 stoken
从两种不同的客户端环境并发出现，平台会把它标成异常；之后用这个 stoken 的请求一律 code 37。手动浏览之所以正常，是因为
页面会重新跑安全校验、换出新的 stoken，而 CLI 自己没有这一步。

现在的行为：

- CDP / 显式浏览器来源下，所有平台读写都只走浏览器通道（页面内 `fetch`，用 Chrome 自己的 Cookie），不再用 httpx
  带着浏览器登录态访问平台；没有浏览器实现的操作（如招聘者附件下载）直接返回 `NOT_SUPPORTED`。
- CDP 模式下详情请求不并发（福利 / 活跃度补详情都改为串行），并按 `CrawlBudget` 间隔。
- 浏览器请求一旦拿到环境类 code 37，会在数据目录写 `cdp_risk_lock.json`，里面只有当前 `__zp_stoken__` 的 SHA-256
  摘要和时间戳，不存原值。之后每次 CDP 浏览器请求前，CLI 通过本机 CDP 读 Chrome 的 cookie 计算摘要：
  - 摘要没变：本地拒绝，返回 `ENVIRONMENT_RISK_LOCKED`（`recoverable=false`），请求不会发出；
  - 摘要变了：说明页面已经换了新 stoken，自动删除锁并继续。

解除步骤：

1. 在这个 CDP Chrome 里手动打开一个 BOSS 直聘职位列表页（如 `https://www.zhipin.com/web/geek/jobs`），确认能正常加载；
2. 等几分钟，让页面完成安全校验并换出新的 `__zp_stoken__`；
3. 重新执行命令。stoken 变化后锁会自动解除；`boss doctor` / `boss status` 可以离线查看锁状态（`cdp_risk_lock`）。
4. 确认已经在页面里恢复、但锁文件损坏或记录时没读到 stoken 导致仍被拦时，再执行 `boss clean --risk-lock` 手动解除。
   这一步应由用户决定，Agent 不要自行执行。

## 错误码与自动修复

每个错误信封都带 `code`、`recoverable`、`recovery_action`，Agent 可程序化恢复。

> **Breaking change（code 37 按语境分类）：** 此前所有 code 37 都发 `TOKEN_REFRESH_FAILED`（`recoverable=true`，恢复动作 `boss login`）。
> 现在只有文案明确指向 token/stoken 过期的 code 37 保持该行为；环境风险文案及语义不明确的 code 37 一律发 `ENVIRONMENT_RISK`
> （`recoverable=false`），且不会刷新、重试或提示重新登录。按错误码分支的 Agent 应新增 `ENVIRONMENT_RISK` 终止分支：
> 绝不对其自动登录、刷新 Token 或重试，先停止自动化访问，保留当前专用 profile，在官方页面确认后再由用户手动发起。

| 错误码 | 含义 | Agent 自动修复 |
|--------|------|---------------|
| `AUTH_REQUIRED` | 未登录 | `boss login` |
| `AUTH_EXPIRED` | 登录过期 | `boss login` |
| `BROWSER_SESSION_NOT_FOUND` | 已选择现有浏览器来源，但 CDP 会话或已打开的目标页面不可用 | 运行 `boss doctor`；在目标本机 CDP 浏览器中手动打开并登录 BOSS 直聘，确认端点可连接后重试 |
| `RATE_LIMITED` | 频率过高 | 等待后重试 |
| `TOKEN_REFRESH_FAILED` | Token 刷新失败 | `boss login` |
| `ENVIRONMENT_RISK` | 访问环境存在异常 | 停止自动化访问；保留当前专用 profile，在官方页面确认并降低访问频率 |
| `ENVIRONMENT_RISK_LOCKED` | CDP Chrome 的 stoken 此前命中 code 37 且尚未更新，本次请求未发送 | 停止自动化；由用户在该 Chrome 打开职位列表页确认能正常加载，等几分钟后再重试（见上文） |
| `ACCOUNT_RISK` | 风控拦截 | 停止当前 workflow，保留 run ID/checkpoint；处理登录或安全页后再显式恢复 |
| `COMPLIANCE_BLOCKED` | 历史版本模式策略阻断 | 升级当前版本后重试；当前版本不主动产生此错误 |
| `WIZARD_INPUT_REQUIRED` | headless workflow 缺少 role/platform/goal/inputs | 按 `boss schema` catalog 补齐 `--input-json` |
| `WORKFLOW_TIMEOUT` | workflow 超过显式 timeout | 保留 `run_id`，调整 timeout 后执行 `boss wizard --resume <run_id>` |
| `WORKFLOW_PLAN_MISMATCH` | 现有 `run_id` 与请求 plan 不一致 | 使用原 plan 恢复，或不传 `run_id` 创建新任务 |
| `INVALID_PARAM` | 参数错误 | 修正参数 |
| `ALREADY_GREETED` | 已打过招呼 | 跳过 |
| `CONFIRMATION_REQUIRED` | 未确认操作目标与内容 | 先用 `hr greet --dry-run` 或 `hr accept-resume <friend_id> --message-id <mid> --dry-run` 预览；操作者明确批准后才加 `--yes`，Agent 不可自行补确认 |
| `RESUME_ACCEPT_RESULT_UNKNOWN` | 同意附件简历请求的结果未确认 | 保留 `accepted=null`，在官方页面核对请求状态；禁止自动重试同意操作 |
| `ACTION_UNCONFIRMED` | 招聘者交换联系方式或求简历的页面动作已执行，但未确认是否发出 | 动作可能已生效，先用 `boss hr chatmsg <friend_id>` 核实，勿直接重试；`error.details` 里有页面日志和 WS 统计 |
| `GREET_RESULT_UNKNOWN` | 已预约发送，但结果未确认 | 用 `boss hr chat --job-id <id>` 核对会话，禁止自动重发；本地保留预约，必要时在官方页面处理 |
| `GREET_LIMIT` | 今日次数用完 | 告知用户 |
| `NETWORK_ERROR` | 网络错误 | 重试 |
| `AI_NOT_CONFIGURED` | AI 未配置 | `boss ai config` |
| `PLATFORM_NOT_SUPPORTED` | 当前平台不支持该角色或子命令 | 切换到支持的平台 |
| `BROWSER_KERNEL_MISSING` | patchright 浏览器内核缺失或版本不匹配 | `patchright install chromium`；缺 headless shell 时运行 `patchright install chromium-headless-shell` |

## Windows smoke checklist

```powershell
boss --version
boss doctor
boss status
boss login
boss status --live
boss search "Python" --page 1
boss detail <security_id>
```

未登录时 `boss status` 返回 `AUTH_REQUIRED` 属于预期；不要把未登录状态计为真实平台功能失败。
