# scripts/

仓库辅助脚本目录。

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

```bash
# 1. 启动 CDP Chrome 并登录招聘者账号
boss-chrome

# 2. 跑脚本（friend_id 来自已沟通候选人，可在 boss hr chat 输出中拿到）
uv run python scripts/probe_recruiter_chat_frontend.py --friend-id 12345 --output report.json

# 3. 按脚本提示在 Chrome 中手动发一条「探测消息」
# 4. 把 report.json 内容粘贴到 issue #217 评论
```

`--dry-run` 仅打印将执行的 JS payload 用于审阅，不连 CDP。
