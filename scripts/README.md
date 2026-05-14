# scripts/

仓库辅助脚本目录。

## smoke_p0.py
P0 模块可导入性冒烟脚本，CI 与本地排障复用。

```bash
uv run python scripts/smoke_p0.py
```

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
