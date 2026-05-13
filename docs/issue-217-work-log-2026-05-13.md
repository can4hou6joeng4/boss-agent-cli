# issue #217 工作记录 — 2026-05-13

跟踪 [issue #217](https://github.com/can4hou6joeng4/boss-agent-cli/issues/217)：BOSS 招聘者端 `hr reply` / `hr request-resume` / `hr resume --exchange` 全部 121。

PR #221（上游 can4hou6joeng4）只做了错误码契约和侦察脚本，**真实通道实现留给本地分支**。

## 今天做了什么

### A' 方案 — 让 BOSS 前端 Vue 代劳协议

CDP 抓包 + Vue 实例反射后确定的路径：

```
boss --role recruiter hr reply <friend_id> <text>
  ↓ friend_detail([friend_id]) via httpx
  ↓ raw CDP Runtime.evaluate(chat tab):
      geekList.geekClick(friendData)     ← BOSS 内部链：bossEnter + historyMsg + geek/info
      wait editor.conversation$.friendId === target
      editor.disabled = false             ← 强制绕 UI 业务规则（服务端不看此值）
      editor.draft[uniqueId] = text
      input.innerText = text
      editor.sendText()                   ← 真发 WS MQTT/protobuf 帧
  ↓ 前端会自动发后续 zpblock 风控报备
```

**核心抓包结论（实证）：**

- 发消息**正文走 WebSocket MQTT 协议**，不是 HTTP；HTTP 那个 `/wapi/zpblock/chat/reply/block/v2` 是**事后**风控报备，不是前置
- 老的 `/wapi/zpchat/fastReply/sendReplyMsg` 端点已弃，**所以原 `send_message(gid, content)` 必然 121**
- `editor.disabled=true` 是客户端 UI 限制，服务端不强制，强制改 false 后发能成功

### 实现改动

**新方法：**

- `BossRecruiterClient.send_message_by_friend(friend_id, content)` — A' 路径
- `BossRecruiterClient.exchange_request_by_friend(friend_id, exchange_type)` — type=1 换手机号 / type=4 求附件简历，触发 zpblock + test×2 + request 四步链路
- `BossRecruiterClient._evaluate_request(method, url, *, data)` — raw CDP fetch 通道，避开 patchright `Frame was detached` race
- `BrowserSession.evaluate_js(script, arg)` — raw CDP `Runtime.evaluate` 入口，不启 patchright

**CLI 改动：**

- `hr reply <friend_id> <text>` 改走 `send_message_by_friend`，旧 `send_message(gid, content)` 标 deprecated
- `hr request-resume <friend_id>` 删 `--job-id` 参数，type 从错误的 3 改为实证的 4
- `hr resume --exchange` 新增 `--friend-id` 参数（旧 `--uid/--gid/--job-id` 路径标 deprecated）

**错误契约：**

- 新增 `RECRUITER_CHAT_TAB_REQUIRED` 错误码：未打开 chat/index tab 时友好提示
- `RecruiterChatTabRequired` 异常类，`handle_auth_errors` 装饰器识别

**侦察脚本（碰运气式调整）：**

- `scripts/probe_recruiter_chat_frontend.py` URL 模板从 `web/boss/chat/index` 修为 `web/chat/index`（原值在当前 BOSS 上不导航到任何聊天页）
- attach 现有 tab 而非新开 tab（新 tab 会触发 BOSS 反爬把现有 tab 踢到首页）
- 删除 spy 注入路径（hook `window.fetch/XMLHttpRequest` 会触发 BOSS 强制刷新登出）

**新增依赖：**

- `websockets>=12.0` — raw CDP WebSocket 客户端用

### 已实证可工作

- `hr reply 741337396 "..."` → 陈若溪真实送达（DOM `送达 抱歉，不是太合适`）
- `hr reply 622138560 "..."` → 李儒真实送达（DOM `14:13 送达 抱歉，不太合适`）
- `hr reply 687844037 "..."` → 王成坤真实送达（CDP 抓到 117B WS 帧含目标文本 + 32B chat ACK）

### 测试

- 1283 tests passed
- mypy 0 issues
- ruff 0 issues
- 新增单测：
  - `test_send_message_by_friend_happy_path`
  - `test_send_message_by_friend_no_friend_returns_error`
  - `test_send_message_by_friend_page_error_propagated`
  - `test_exchange_request_by_friend_full_chain`
  - `test_exchange_request_by_friend_aborts_on_zpblock_failure`
  - `test_send_message_by_friend_delegates`

## 还没完成的事

### 1. `hr request-resume` / `hr resume --exchange` 真账号端到端**仍 121** ⚠️

`hr reply` 已端到端走通 3 次，但 exchange 链路**第二步 `exchange/test` 仍返回 121**：

```
step 1 zpblock     → code=0  ✅
step 2 test (1st)  → code=121 ❌
```

抓包对照分析：

- 我手工在浏览器点求简历时抓到的 4 步全 200
- CLI 复刻同样 4 步，第一步 zpblock 通过，第二步立刻被拒
- securityId/encryptJobId/encryptExpectId/name 都已经从 `editor.conversation$` 读到（一次只读到 friendId 没 securityId 的 race 已修，等所有字段 populated 才返回）
- 仍然 121

**未验证的猜想（按可能性排序）：**

1. **securityId 一次性消费**：BOSS 服务端可能把 securityId 设计成"每次写操作消费一次"。CLI zpblock 调用后 securityId 已被消费，再调 exchange/test 时已失效。手工点不出问题是因为前端可能在每步之间从某个内部接口续 securityId
2. **CLI 与 BOSS 前端并发竞争**：`geekClick` 会触发前端自己跑 `bossEnter` / `brandCard` 等内部请求，可能消费 securityId。CLI 在前端跑这些的同时插入 zpblock+test 序列，撞上同一个 securityId
3. **缺某个 header**：`bx-v` / `traceid` / `sigx` 等指纹 header CLI 没传齐（虽然 reply 不需要这些就能跑通，但 exchange 可能严格点）
4. **type=4 在当前候选人状态下不允许**：袁菲阳 `requestResume=0`（说明没请求过简历），按理应允许；不太可能这条

**已尝试无效的修复：**

- 在 geekClick 之后等 1.5 秒让前端跑完内部请求 → 仍 121
- 等 conversation$.securityId 非空才往下走 → 仍 121

**下一步可以尝试：**

- 在每步 exchange POST 之前重新读 `editor.conversation$.securityId`（验证 securityId 是否动态更新）
- 抓包对比"手工点击触发的 exchange/test 请求 headers" vs "CLI 发的 headers"，找缺漏字段
- 看 BOSS 前端 `exchange/test` 的源码（用 CDP `Debugger.setBreakpointByUrl` 或者 DevTools Performance 录制）

### 2. `hr reply` 缺真发证据校验 ⚠️

当前 `send_message_by_friend` 调完 sendText 后**乐观假设成功** —— 看 CDP WS 帧是否真送出去**没在 CLI 里校验**。

实测中发现 BOSS 前端有时候会**只清 input.innerText 但不清 draft**（业务规则静默拦截），导致 sendText 调用方看起来成功实际没发。

修法（设计完未实施）：在 send_message_by_friend 内同时启 raw CDP `Network.webSocketFrame` 监听，sendText 后 2s 窗口期内必须看到 ≥100B opcode=2 帧含目标 UTF-8，否则返回失败。已经在 PoC 脚本里写过类似机制可移植。

### 3. 老 `send_message(gid, content)` / `exchange_request(type, uid, jobId, gid)` API 保留 ⚠️

签名保留是为了不破坏现有 callers 和测试。但**这两个方法调用必 121**。建议要么：

- 改成内部 redirect 到新方法（但旧签名 information not enough — 没 friend_id，只有 gid）
- 或者明确抛 `EndpointDeprecatedError` 而不是发出 121

### 4. 自动 tab 管理 — 当前是"无则报错" ❓

讨论过的 3 个策略：

| 策略 | 实测结果 |
|---|---|
| 复用已有 chat/index tab | ✅ 工作 |
| 新开 tab `Target.createTarget url=chat/index` | ⚠️ 不崩 patchright 但**触发 BOSS 反爬把现有 zhipin.com tab 踢到首页**（之前还观察到一次完全 Chrome crash） |
| `Page.navigate` 现有 zhipin tab | 没测试 |

当前实现选择**无则友好报错**（`RECRUITER_CHAT_TAB_REQUIRED`），让用户自己打开 chat/index。如果未来要做自动化，建议优先用 `Page.navigate` 路径而不是 `createTarget`。

### 5. patchright `Frame was detached` 是个 Pandora's box ❓

实现 raw CDP `evaluate_js` 完全绕开 patchright，是因为：
- patchright `connect_over_cdp` 在 attach 阶段会**枚举每个 tab 的 frame tree**
- 招聘者 chat tab 上 Vue 持续重渲染，frame tree 不稳定
- 触发 race 后 Node driver 抛 `Frame was detached` 整个进程崩

仓库其他地方（如 `auth/browser.py refresh_stoken_via_cdp`）仍然用 patchright `connect_over_cdp`。**未来如果 token 刷新撞上 chat tab 也可能崩**。当前没碰到是因为 token 还有效；下次 token refresh 触发时可能复现。建议把 `refresh_stoken_via_cdp` 也改 raw CDP 形式，作为另一个 PR。

### 6. 没提 PR

当前所有改动还在本地 `pr-221` 分支上（基于上游 PR #221，未合）。需要：

- 单独起 branch（基于 upstream/master）只带本次改动
- push 到 `qianjunye/boss-agent-cli` fork
- 向 `can4hou6joeng4/boss-agent-cli` master 提 PR
- PR 描述里说明：A' 路径选择、已工作的 reply、未解决的 exchange 121、依赖了上游 PR #221 的错误码契约（推荐先合 #221 再合本 PR）

## 工作流坑（值得记）

### `uv tool install` 和开发 venv 是两个独立环境

CLI 命令 `boss` 默认指向 `uv tool` 安装版本：
- `which boss` → `/Users/junye/.local/bin/boss`
- 指向 `/Users/junye/.local/share/uv/tools/boss-agent-cli/bin/python3`

**改源码后必须 `uv tool install -e . --reinstall`** 才能让 `boss` 命令跑到新代码。否则 CLI 还在跑旧版，但 `uv run python` 跑的是开发版——表面行为不一致。本次调试时第二次 CLI 失败花了不少时间找原因，就是因为忘了 reinstall。

### "draft 没清空" 不能等同于"发送失败"

BOSS 前端 sendText 有时候清 input.innerText 但留 draft（业务规则静默拦截路径），用 draft 状态判定 sendText 真实结果会**假警报**。可靠判定只能靠 WS 帧抓取。

### 修改安装包目录里的 `.bak` 文件

之前发现 `~/.local/share/uv/tools/boss-agent-cli/.../*.bak` 残留文件（推测是之前手动 patch 过装好的包）。`uv tool install` 不会清理这些。本次没处理但属于环境清洁度问题。
