# BOSS 直聘请求身份一致性（2026-10-29）

本文件记录一次本机真实抓包：同一账号下，页面自己发出的业务请求带什么身份信息，
CLI 的 httpx 通道又带了什么，以及两者差距如何收敛。

抓包脚本：[`scripts/capture_request_identity.py`](../../scripts/capture_request_identity.py)。
原始产物（含 Cookie 头与响应体）写在 `.capture/`，已被 `.gitignore` 排除，不入库。

## 结论

`search` 与 `recommend` 走浏览器通道（页面 JS 发请求），`detail` 等低风险读走 httpx 通道。
两条通道此前是**两个互不相同的客户端身份**：浏览器带着页面生成的 `token`、`zp_token`、
`traceid`，httpx 一个都不带。同一个 `wt2` Cookie 在几十秒内交替出现两种实现形态。

本轮已闭合 `token` / `zp_token` 两项，并把 `Accept-Language`、`sec-ch-ua`、UA 版本对齐到
实测值。`traceid` 与 HTTP/2 协议层差异未闭合，见“未解决”。

## 采集条件

| 项 | 值 |
|---|---|
| 时间 | 2026-10-29 |
| 浏览器 | 真实 Chrome 156.0.8078.48（macOS），CDP 端口 9222，专用 profile |
| 页面 | `/web/geek/jobs?query=Python&city=101280100` |
| 业务请求样本 | 36 条 `/wapi/` 请求 |
| 记录粒度 | 完整请求头与响应体（本地留存，未入库） |

## 逐 header 规则总表（四份抓包，147 条业务请求）

| Header | 观察到的规则 | 类型 | 本项目处理 |
|---|---|---|---|
| `User-Agent` | 固定 `Chrome/156.0.0.0` + macOS 10_15_7 | 常量 | `boss.yaml`/`recruiter.yaml` 已对齐 |
| `sec-ch-ua` | `"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"` | 常量，版本随 UA | 由 UA 主版本重写，greasing 品牌保留 |
| `sec-ch-ua-mobile` | `?0` | 常量 | 由 UA 是否含 `Mobile` 推导 |
| `sec-ch-ua-platform` | `"macOS"` | 常量，随 UA | 由 UA 推导（不再用宿主 OS） |
| `Accept-Language` | `zh-CN,zh;q=0.9`，无 `en` | 常量 | 两条通道同源 |
| `Referer` | 发起页完整 URL，含查询串 | 每请求 | 沿用 endpoint→referer 映射 |
| `X-Requested-With` | `XMLHttpRequest`（聊天页有 1 条 `XMLHttpRequest, XMLHttpRequest`） | 常量 | 默认头 + 浏览器 fetch 都发送 |
| `traceid` | `F-` 前缀，请求级唯一，长度 16–36，多种生成器 | 每请求 | 见下一节 |
| `token` | 16 字符，会话级常量；**仅候选者侧出现**（招聘者 0/40） | 会话 | 采到则回放，采不到不伪造 |
| `zp_token` | 67 字符，两段各以 `~` 结尾、`\|` 连接；两侧同形 | 会话 | 同上 |
| `Content-Type` | 仅 POST：`application/x-www-form-urlencoded`（16）、`application/json`（9+5）、`text/plain;charset=UTF-8`（4） | 随请求体 | 沿用现有实现 |
| `Sec-Fetch-Dest` | `empty` | 常量 | 默认头 |
| `Sec-Fetch-Mode` | `cors` | 常量 | 默认头 |
| `Sec-Fetch-Site` | `same-origin`（131）/ `same-site`（12，跨子域如 `shink.zhipin.com`、`apm-fe.zhipin.com`） | 随目标域 | 默认 `same-origin`；跨子域请求由浏览器自行决定 |
| `priority` | `u=1, i` | 常量 | 未发送（HTTP/2 语义，见未解决） |
| `Accept-Encoding` | `gzip, deflate, br, zstd` | 常量 | 未改，见未解决 |

## 观察：页面业务请求携带的头

单位是“出现该头的请求数 / 本页业务请求总数”。

| 头 | 出现 | 示例形态（已脱敏） |
|---|---|---|
| `Referer` | 36/36 | 完整页面 URL，含查询串 |
| `User-Agent` | 36/36 | Chrome 156 / macOS |
| `sec-ch-ua` | 36/36 | `"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"` |
| `sec-ch-ua-mobile` | 36/36 | `?0` |
| `sec-ch-ua-platform` | 36/36 | `"macOS"` |
| **`traceid`** | 34/36 | `F-` 前缀，长度 16–24 不等 |
| **`zp_token`** | 32/36 | 两段，各以 `~` 结尾，用 `\|` 连接 |
| `Accept` | 34/36 | `application/json, text/plain, */*` |
| **`X-Requested-With`** | 33/36 | `XMLHttpRequest` |
| **`token`** | 25/36 | 16 字符定长 |
| `Content-Type` | POST 请求 | `application/x-www-form-urlencoded` |
| `Origin` | POST 请求 | `https://www.zhipin.com` |
| `Accept-Language` | 全部 | `zh-CN,zh;q=0.9` |
| `Accept-Encoding` | 全部 | `gzip, deflate, br, zstd` |

会话内稳定性：

- `token`：全部样本同一个值，16 字符 → **会话级常量**。
- `zp_token`：全部样本同一个值（67 字符）→ **会话级常量**。
- `traceid`：每请求不同，且存在多种生成器（实测至少三种前缀形态：八位十六进制、十位十六进制、
  十六位大小写混合），长度 16–36 不等 → **请求级，多来源**。

## 观察：CLI httpx 通道此前发出的头

`detail` 走 httpx，实测缺 `token`、`zp_token`、`traceid`、`X-Requested-With`；额外多出
`DNT: 1`（真实 Chrome 不发）；`Accept-Language` 多了 `en;q=0.8`；`sec-ch-ua` 的品牌列表与
版本号停留在旧模板（`Not(A:Brand` / v=145），与 UA 声称的版本也不一致。

另有一处与主机相关的不一致：`sec-ch-ua-platform` 曾按 `sys.platform` 推导。UA 恒为 macOS，
于是在 Windows 上会发出 `UA=macOS` + `sec-ch-ua-platform: "Windows"`。这条已改为从 UA 推导。

## 改动

| 文件 | 改动 |
|---|---|
| `api/boss.yaml` | UA / `sec-ch-ua` 对齐 Chrome 156 实测值；`Accept-Language` 去掉 `en`；删除 `DNT`；补 `X-Requested-With` |
| `api/httpx_helpers.py` | `sec-ch-ua-platform` 改由 UA 推导；`sec-ch-ua` 品牌版本随 UA 主版本重写（greasing 品牌保留原版本）；回放 `token` / `zp_token` |
| `auth/page_identity.py` | 从真实页面请求中采集 `token` / `zp_token`；早期请求只带其一时继续累积 |
| `auth/browser.py` | CDP 与可见浏览器登录路径接入采集；已有页签空闲时用一个临时页签在同一 context 载入首页再采集 |

采集得到的两个值随会话一起加密落盘，`boss detail` 等 httpx 请求直接回放，不再另造。

## 未解决

| 项 | 状态 | 说明 |
|---|---|---|
| `traceid` | 未实现 | 请求级、多生成器、长度不一。未确认服务端是否校验；不伪造 |
| HTTP/2 | 未实现 | httpx 默认 HTTP/1.1，真实页面是 h2。`connection`、`host` 与 `:authority` 等结构差异仍在 |
| `Accept-Encoding` | 未改 | 浏览器含 `br`/`zstd`；httpx 只有在装了对应解码器时才敢声明 |
| `Referer` 路径 | 未改 | CLI 用 `/web/geek/job`，实测页面是 `/web/geek/jobs`（带 s） |
| `search` 方法 | 未改 | 页面用 `POST` + 表单体；CLI 走浏览器通道，由页面决定，暂不受影响 |

这些项目在未取得证据前保持现状：改动 HTTP 方法或伪造 `traceid` 都是猜测，可能把当前可用的
链路弄坏，也不构成可验证的收益。

## 复现

```bash
# 1. 以远程调试端口启动 Chrome 并在其中登录
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.boss-agent/cdp-chrome-profile"

# 2. 抓包（原始产物写入 .capture/，不入库）
uv run python scripts/capture_request_identity.py --query Python --city 101280100

# 3. 让会话带上页面身份
uv run boss login --cdp

# 4. 只读验证
uv run boss status --live
```

## 边界

- 本文件不含 Cookie、`token`、`zp_token`、`secId` 的真实值，只有形态与长度。
- 抓包只用于对齐身份，不用于生成或绕过任何平台凭证。
- 命中 `code 31/32` 或安全页时仍按既有契约 checkpoint 并停止，不因本轮改动而放宽。

## 跨页面复核（2026-10-29 追加）

在 `/web/geek/chat`（36 条业务请求）与 `/web/geek/resume`（35 条）上重复同一次采集，
用于判断此前单页得出的「会话级常量」是不是偶然。

| 头 | 搜索结果页 | 聊天页 | 简历页 | 判定 |
|---|---|---|---|---|
| `token` | 25/36，1 个唯一值，16 字符 | 5/36，1 个唯一值，16 字符 | 23/35，1 个唯一值，16 字符 | **会话级常量**（三页同一个值） |
| `zp_token` | 32/36，1 个唯一值，67 字符 | 28/36，1 个唯一值，67 字符 | 31/35，1 个唯一值，67 字符 | **会话级常量**（同上） |
| `traceid` | 34/36，每请求不同，16–24 字符 | 30/36，30 个唯一值，18–36 字符 | 33/35，33 个唯一值，18–26 字符 | **请求级，多生成器** |
| `X-Requested-With` | 33/36，`XMLHttpRequest` | 28/36，`XMLHttpRequest` | 29/35，`XMLHttpRequest` | 固定值；聊天页有 1 条异常取值 `XMLHttpRequest, XMLHttpRequest` |
| `Content-Type` | 仅 POST | 仅 POST | 仅 POST | 与是否有请求体一致 |

结论：`token` / `zp_token` 在三个互相独立的页面上都表现为同一会话常量，回放策略成立；
`traceid` 每请求不同且长度分布 18–36，进一步支持「不伪造」的决定。

三个页面的方法分布：搜索页 18 GET / 3 POST，聊天页 29 GET / 6 POST，简历页 27 GET / 7 POST。
GET 用查询串、POST 用 `application/x-www-form-urlencoded`，与 `boss.yaml` 现有映射一致。

## 出口与协议一致性（2026-10-29 追加）

身份不只在头部，也在"从哪出去"和"用什么协议出去"。

| 项 | 改前 | 改后 |
|---|---|---|
| 出口 | httpx 走环境代理；Chromium 完全忽略 `HTTPS_PROXY`/`ALL_PROXY`，直连真实 IP | `api/proxy.py` 的 `resolve_proxy()` 同时供给 httpx 与 4 个 Chromium 启动点 |
| WebRTC | 未处理；即使配了代理，STUN 仍用真实地址发 UDP | `WEBRTC_LEAK_GUARD_ARGS`：`--force-webrtc-ip-handling-policy=disable_non_proxied_udp` 等两个公开启动参数 |
| 出口/语言/时区矛盾 | 无检查 | `api/egress.py`：`BOSS_EGRESS_PROBE_URL` 配置后 `CrawlService.create()` 在矛盾时抛 `EgressContradictionError` 拒绝开跑；未配置时不发任何探测流量、判为 unknown、不阻断 |

`resolve_proxy()` 的行为：按 `HTTPS_PROXY` → `ALL_PROXY` → `HTTP_PROXY`（含小写）取第一个非空值，
解析 scheme/host/port/认证，未配置时返回 `None` 表示直连——不凭空造代理。

`evaluate_egress()` 只在"出口地区已知且两侧都有可比信息"时才判矛盾，未知一律 `unknown`。
它**不会**自动改写 `Accept-Language`：把中文站的声明语言改成外语本身就是另一种异常。

## 浏览器通道补齐（2026-10-29 追加）

上一节只修了 httpx 通道，其实不一致是双向的：`search` / `recommend` / `greet` 走浏览器通道，
而浏览器通道的 `fetch()` 只设了 `Accept` / `Referer` / `X-Requested-With`，同样不带
`token` / `zp_token`。

现在 `BrowserSession` 接受 `identity` 参数，`_base_client` 从会话凭据里取
`PAGE_IDENTITY_HEADERS` 传入，`fetch` 的 `headers` 用 `Object.assign` 合并。
两条通道都回放同一组值。

## `traceid`：按请求生成，不假装复现页面生成器

先记录一个**已推翻的早期判断**：本条最初写成"不发送"，理由是两条通道都不发 `traceid` 时
`detail` 与 `search` 仍返回正常数据，说明平台不校验它。

推翻它的是"缺席率"这个更直接的事实：真实页面在 147 条业务请求里发了 130 次 `traceid`
（约 88%）。也就是说，不发 `traceid` 的客户端在这件事上比真实浏览器更显眼，而不是更自然。
"平台不拒绝"与"平台看不出差别"是两件不同的事，把前者当成后者是错的。

实现方式与其边界：

- `new_trace_id()` 只保证**形态**一致：`F-` + 8 位 0-9a-z + 16 位 0-9a-z，共 26 字符，
  落在实测 16–36 的区间内，且每次调用都不同。
- 它**不**声称复现页面生成器。实测存在至少三种不同前缀与长度（本次不保留具体取值），
  说明页面侧本身就有多个来源，
  因此"与某一个生成器逐位对齐"既不可得也无意义。
- httpx 通道与浏览器通道都按请求生成，位置一致：前者在 `_base_client` 的每请求头，
  后者在 `fetch` 的 `headers`。客户端级默认头里不放 `traceid`，否则同一会话会重复同一个值。

## 招聘者侧边界：当前账号不具备

- 浏览器导航 `https://www.zhipin.com/web/chat/index` 会被重定向回
  `https://www.zhipin.com/web/geek/jobs?_security_check=1_...`，说明该浏览器会话是求职者身份，
  招聘者页面渲染不出来。
- 因此招聘者页面侧没有任何可采的 Header 证据。本文件中所有规则都来自求职者侧三个页面。
- `boss --role recruiter hr jobs list` 曾返回 `{"ok": true, ..., "totalSize": 0}`。在未登录招聘者
  账号的前提下，这个"成功但空"的响应**不能**当作招聘者链路可用的证据，也不能当作不可用的
  证据——它只是没有区分度。
- 结论：招聘者侧的请求身份未验证。恢复条件是用真正的招聘者账号登录后重跑
  `scripts/capture_request_identity.py`，并在本文件补一节。

## 一处需要留意但未定性的现象

同一会话中访问招聘者页面后，求职者页面的 URL 带上了 `_security_check=1_<ts>` 查询参数。
此前三次抓包的 `final_url` 都没有这个参数。业务接口当时全部返回 200，因此目前只能记录现象，
不能断言它是风控标记还是实验参数。记录在此供后续对照。

## 招聘者侧（2026-10-29 追加，真实招聘者账号）

用户提供招聘者账号后，用独立 profile（`~/.boss-agent/cdp-chrome-profile-recruiter`）
扫码登录，采集 `/web/chat/index`（40 条业务请求）。候选者登录态在采集前已备份，
采集全程未写入 CLI 的 token store。

### 与候选者侧的差异

| 头 | 候选者（搜索/聊天/简历） | 招聘者（沟通页） | 说明 |
|---|---|---|---|
| `token` | 24–25/36，16 字符，会话常量 | **0/40** | 招聘者侧页面**不发** `token` |
| `zp_token` | 28–32/36，67 字符，会话常量 | 34/40，67 字符，1 个唯一值 | 形态与生命周期完全一致 |
| `traceid` | 每请求不同，18–36 字符 | 35/40，35 个唯一值，20–36 字符 | 一致 |
| `X-Requested-With` | `XMLHttpRequest` | 33/40，`XMLHttpRequest` | 一致 |
| `with-common-headers` | 无 | 1/40（仅 `set/zpToken`，值 `true`） | 招聘者独有的单点头 |

其余 24 个 header 名集合两侧相同（`accept*`、`cookie`、`origin`、`priority`、
`referer`、`sec-ch-ua*`、`sec-fetch-*`、`user-agent`）。

### 结论

1. `zp_token` 两侧同形同义，回放策略对两侧都成立，`status --live` 已验证。
2. `token` 只在候选者侧出现。因此 `PAGE_IDENTITY_HEADERS` 保持为"有则回放、无则不伪造"：
   招聘者会话里采不到 `token` 时，回放集合自然只剩 `zp_token`，不会补一个假值。
3. `with-common-headers: true` 只出现在 `set/zpToken` 单条请求上，是那一个接口的局部参数，
   不是会话级身份，未纳入回放。
4. `recruiter.yaml` 此前仍停在旧模板（UA Chrome/145、`Not(A:Brand` v=145、
   `Accept-Language` 多 `en`、多发 `DNT: 1`），与 `boss.yaml` 改前的问题完全相同，已一并修正。

### 边界更新

上一节记录的"招聘者侧未验证"到此解除：招聘者沟通页的身份头已有一手证据。
仍未采集的招聘者页面是职位管理与推荐牛人页；方法学相同，需要时重跑同一脚本即可。
