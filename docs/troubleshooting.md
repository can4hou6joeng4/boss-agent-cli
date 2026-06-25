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
| `bridge_daemon` | 本地 Browser Bridge daemon 是否运行 |
| `bridge_extension` | Chrome 扩展是否连接 daemon |
| `bridge_protocol` | CLI 与扩展版本/协议是否兼容 |
| `bridge_workspace` | Bridge 当前 workspace/tab 是否可用 |
| `bridge_exec` / `bridge_fetch` / `bridge_navigate` | 扩展基础执行、浏览器 fetch 与导航能力 |
| `browser_channel` | CDP/Bridge 汇总状态；不得用于规避平台风控 |
| `candidate_search_health` / `candidate_detail_health` | 求职者只读能力前置条件 |
| `recruiter_read_health` | 招聘者只读能力前置条件；智联招聘者侧自动化通过 `agent` browser/CDP adapter 进入 |
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

# Browser Bridge 诊断
python -m boss_agent_cli.bridge.daemon --serve
# 在 Chrome 的 chrome://extensions 中加载并启用 extension/ 后，再运行：
boss doctor

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

**bridge_daemon / bridge_extension 显示 warn**：本地 daemon 未运行或扩展未连接。
先启动 daemon，确认 19826 端口未被占用，再到 `chrome://extensions` 加载并启用
`extension/`。Bridge 只用于本地诊断、用户主动登录兼容和只读辅助；命中平台
风控时应停止自动化访问，不要切换到 Bridge 重试。

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

## 错误码与自动修复

每个错误信封都带 `code`、`recoverable`、`recovery_action`，Agent 可程序化恢复。

| 错误码 | 含义 | Agent 自动修复 |
|--------|------|---------------|
| `AUTH_REQUIRED` | 未登录 | `boss login` |
| `AUTH_EXPIRED` | 登录过期 | `boss login` |
| `RATE_LIMITED` | 频率过高 | 等待后重试 |
| `TOKEN_REFRESH_FAILED` | Token 刷新失败 | `boss login` |
| `ACCOUNT_RISK` | 风控拦截 | 停止自动化访问，回到平台官网手动处理 |
| `COMPLIANCE_BLOCKED` | 默认低风险模式阻断敏感操作 | 回到平台官网手动完成 |
| `INVALID_PARAM` | 参数错误 | 修正参数 |
| `ALREADY_GREETED` | 已打过招呼 | 跳过 |
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
