# scripts/

仓库辅助脚本目录。

## quality_baseline.py

本地 P0 门禁：一次跑完 `ruff check`、全量离线测试与 `mypy`，给 Agent、真人和 CI 同一份确定性结果。提交前先跑这个，再按需单步定位。

```bash
uv run python scripts/quality_baseline.py
uv run python scripts/quality_baseline.py --json        # 结构化结果
uv run python scripts/quality_baseline.py --skip-mypy   # 只做快速本地迭代，不用于合并前
```

## verify_release.py

维护者发布验证：默认只读检查项目版本、已构建 wheel/sdist 的元数据与文件清单、SHA-256 和 Git 状态，不访问包索引、不执行发布或招聘平台请求。产物目录必须恰好包含目标版本的一份 wheel 和一份源码包；混入旧包会失败，不按修改时间猜测。

```bash
uv build --out-dir dist/release-check
uv run python scripts/verify_release.py --artifacts dist/release-check --json

# 显式允许联网解析依赖：临时 venv 安装本次 wheel[mcp]，不使用 uv.lock
uv run python scripts/verify_release.py --artifacts dist/release-check --fresh-install --python 3.11 --json

# 发布后从 PyPI 精确安装并复验；与 --artifacts 互斥
uv run python scripts/verify_release.py --pypi-version 3.0.0 --python 3.11 --json
```

- 安装验证只执行 CLI schema/平台列表、MCP 握手和工具清单比对，以及不发送的预览和未确认操作拒绝；使用隔离 PATH、临时工作目录与数据目录，不使用真实账户。
- `--require-clean` 把脏工作树视为失败；默认允许验证未提交改动，但报告中的 `git.clean=false` **不能作为发布就绪证据**。`--tag vX.Y.Z` 额外检查已有本地标签的版本及目标提交，不创建或移动标签；正式门禁需同时加 `--require-clean`。
- `--timeout` 指定每个外部步骤的超时秒数，默认 180；`--python` 默认 3.11。Windows 使用临时环境的 `Scripts`，POSIX 使用 `bin`。
- stdout 始终输出一个 JSON 报告（`--json` 可显式传入），stderr 输出步骤进度。`steps` 区分 `passed`、`failed`、`not_run`；没有请求的联网步骤不算通过。检查失败返回非零退出码。
- `_release_probe.py` 是安装验证使用的子进程探针，不是 `boss` 顶层命令。工具不代替 `quality_baseline.py`、smoke dry-run 或独立终端的 fixture eval，也不会修改 GitHub/PyPI。

## smoke_p0.py
P0 冒烟测试：以子进程依次运行 `boss doctor/status/search/detail` 四步，并校验 stdout JSON 信封契约（恰好含 `ok/schema_version/command/data/pagination/error/hints` 七键、exit code 与 `ok` 一致、错误信封含 `code`/`recoverable`/`recovery_action`）。已登录时 search/detail 步骤会真实触网。CI 与本地排障复用。

```bash
uv run python scripts/smoke_p0.py
BOSS_SMOKE_DRY_RUN=1 uv run python scripts/smoke_p0.py   # 只打印步骤不执行，完全离线
```

支持 `BOSS_SMOKE_PLATFORM` / `BOSS_SMOKE_QUERY` / `BOSS_SMOKE_SECURITY_ID` / `BOSS_SMOKE_TIMEOUT` 环境变量定制步骤。

## probe_recruiter_chat_frontend.py
issue #217 — 探测 BOSS 招聘者 chat 页前端 sendMessage JS 入口。脚本注入 WebSocket
spy + Vuex 探测，需在 CDP Chrome 中手动配合操作。

宿主运行时是 Python + Patchright + Chrome CDP；脚本中的 JavaScript 只在已连接的
网页上下文中执行，不依赖 Node.js 或 Electron。Windows 请从 PowerShell 启动，
不要交给 Codex++ 脚本沙箱执行：

```bash
# 1. 启动 CDP Chrome 并登录招聘者账号
boss-chrome

# 2. 跑脚本（friend_id 来自已沟通候选人，可在 boss hr chat 输出中拿到）
uv run python scripts/probe_recruiter_chat_frontend.py --friend-id 12345 --output report.json

# 3. 按脚本提示在 Chrome 中手动发一条「探测消息」
# 4. 把 report.json 内容粘贴到 issue #217 评论
```

Windows PowerShell 等价入口：

```powershell
.\scripts\probe_recruiter_chat_frontend.ps1 --friend-id 12345 --output report.json
```

`--dry-run` 仅打印将执行的 JS payload 用于审阅，不连 CDP。

## capture_request_identity.py

记录 BOSS 直聘页面实际发出的请求身份（完整请求头、请求体、状态码），用来核对 httpx 通道与浏览器通道是否一致——是证据采集，不是平台风控绕过。需要在本地可信环境启动带远程调试端口的 Chrome，并手动完成登录。

```bash
uv run python scripts/capture_request_identity.py --cdp-url http://localhost:9222 --output identity.json
# 或直接指定页面
uv run python scripts/capture_request_identity.py --cdp-url http://localhost:9222 --url "https://www.zhipin.com/web/geek/job?query=Python"
```

`--settle-seconds` 控制页面停留时长，`--max-body-bytes` 限制记录的响应体大小。

## package_portable.py

打包便携发行包（默认 macOS arm64，可用 `--platform` 指定后缀）：CLI、示例与 MCP 配置打进一个目录，不包含浏览器内核。发布脚本与文档演示共用。

```bash
uv run scripts/package_portable.py --platform macos-arm64 --output dist/portable
uv run scripts/package_portable.py --wheel dist/boss_agent_cli-*.whl   # 复用已构建的 wheel
```

## update_contributors.py

生成 `CONTRIBUTORS.svg`（圆形头像 + 姓名，base64 内嵌，无外部依赖）。布局参数与仓库的 `update-contributors` workflow 一致；`GITHUB_TOKEN` 可选，仅用于提高 GitHub API 限额。

```bash
uv run python scripts/update_contributors.py
```
