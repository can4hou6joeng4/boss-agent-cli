# issue #217 工作记录 — 2026-05-13

跟踪 [issue #217](https://github.com/can4hou6joeng4/boss-agent-cli/issues/217)：BOSS 招聘者端 `hr reply` / `hr request-resume` / `hr resume --exchange` 原先都会落到 121 或“只改 UI、不真发”的假成功。

## 最终结论

三条招聘者写操作现在都收口到同一条可工作的聊天页前端代劳链路：

- `boss hr reply <friend_id> <text>`
- `boss hr request-resume <friend_id>`
- `boss hr resume --exchange --friend-id <friend_id> [--type phone|wechat]`

`hr request-resume` 不再需要 `--job-id`，`hr resume --exchange` 也不再需要占位 `geek_id`。

## 实现方式

### 1. 统一复用用户已打开的 recruiter chat tab

不再手写 HTTP 复刻 exchange 协议，也不再依赖已废弃的 `sendReplyMsg` 路径。实现改为：

1. `friend_detail([friend_id])` 拿到候选人基础信息
2. 归一化成聊天页 Vue 需要的 `friendData`（补 `friendId` / `uniqueId`）
3. 在现有 chat tab 中执行 `geekList.geekClick(friendData)`，让 BOSS 自己切会话
4. 等 `editor.conversation$` 指向目标候选人后，再调用页面内真实组件

### 2. 发消息用 WebSocket 真发证据收尾

`hr reply` 仍由页面内 `editor.sendText()` 触发，但 CLI 不再乐观判成功。

新增 raw CDP `Network.webSocketFrame*` 监听后，只有在 3 秒窗口期内看到命中目标文本的真实 chat WS send 帧，才返回成功；只出现 suggestion 或草稿副作用时返回失败。

### 3. 求简历 / 换手机 / 换微信交给页面组件代劳

不再尝试 CLI 手写：

- `zpblock`
- `exchange/test`
- `exchange/request`

而是直接调用页面内组件：

- `ExchangeResume.handleExChange()`
- `ExchangePhone.handleExChange()`
- `ExchangeWx.handleExChange()`

由前端自己处理动态 `securityId`、确认弹窗和状态刷新。

## 代码收口

为了避免 `reply` / `request-resume` / `exchange` 三条链路继续各自漂移，这次额外做了收口：

- 新增 `_require_chat_friend_data()`：统一做 `friend_detail -> friendData` 归一化
- 新增 `_run_chat_frontend_action()`：统一执行 `geekClick -> wait conversation$ -> settle -> page action`
- 抽出共享聊天页 JS helper，避免每个写操作复制一份侦察脚本
- 删除已无调用方的 `_evaluate_request()` 死路径，避免后续再回到 121 的旧实现
- 精简成功路径返回值，只保留 `friendId` / `componentName` / `matched_ws_count` / `log` 等必要字段，不再回传大段调试残留

## CLI / MCP / 文档契约同步

- `hr request-resume`：CLI 和 MCP 都改为仅需 `friend_id`
- `hr resume --exchange`：支持 `phone` / `wechat`，且不再要求占位 `geek_id`
- MCP 新增 `boss_hr_exchange`
- README / Agent Quickstart / Capability Matrix / CHANGELOG 已同步到当前行为

## 当前约束

- 仍要求用户已打开 `https://www.zhipin.com/web/chat/index` 招聘者聊天页
- 仍要求 CDP 模式可连到用户 Chrome
- 旧的 `send_message(gid, content)` / `exchange_request(type, uid, jobId, gid)` 仅保留兼容，不建议新调用方继续使用

## 本地验证

- `tests/test_recruiter_client.py`
- `tests/test_recruiter_commands.py`
- `tests/test_mcp_server.py`
- 文档契约相关测试待与完整矩阵一起跑
