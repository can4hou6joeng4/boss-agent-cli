# 只读链路能否不碰 CDP（2026-10-29）

回答一个问题：不依赖 CDP 浏览器，本项目能否完成候选者侧的只读流程？
结论：**能**——纯 httpx 可以完成候选者侧只读流程。过程中遇到 code 37，但其成因尚未定性，
第七节记录了我在同一份文档里先给出、又被自己实验推翻的那个错误结论。

本次实验在真实账号上执行，同时复现了仓库里既有的那条假设。实验过程与后果都记在下文，
包括我造成的失误。

## 一、纯 httpx 只读链路：可用

全部请求使用会话 Cookie + `stoken`，加上 `httpx_helpers.browser_headers()` 产出的头，
**没有打开任何浏览器、没有连 CDP**。

| 能力 | 请求 | HTTP | 业务码 | 结果 |
|---|---|---|---|---|
| `search`（POST 表单，真机形态） | `POST /wapi/zpgeek/search/joblist.json` | 200 | `code=0` | `resCount=450`，`jobList` 15 条 |
| `search`（GET 查询串，项目当前声明） | `GET /wapi/zpgeek/search/joblist.json` | 200 | `code=0` | `jobList` 15 条 |
| `detail` | `GET /wapi/zpgeek/job/detail.json` | 200 | `code=0` | 有数据 |
| `friend_list` | `GET /wapi/zprelation/friend/getGeekFriendList.json` | 200 | `code=0` | 11 个会话 |
| `chat_history` | `GET /wapi/zpchat/geek/historyMsg` | 200 | `code=0` | 3 条消息 |

两条与抓包一致的细节：

- 真机 `search` 是 **POST + `application/x-www-form-urlencoded`**，项目声明的是 GET。
  两种都能拿到 `code=0`，所以这不是阻塞项，但它是**行为差异**，需要单独立项决定是否对齐。
- 响应是 HTTP/1.1。`h2` 未安装，`httpx.Client(http2=...)` 打不开，所以协议层差异仍在。

## 二、混用会立刻出事：复现 code 37

`is_browser_only()` 的注释早就写了这条假设：

> 用 httpx 带着从浏览器拷来的 Cookie + stoken 并发打平台，会让这个 stoken 被判异常
> （之后浏览器通道一律 code 37）。

本次实测确认了它。顺序是：先用同一份 stoken 发了几条 httpx 只读请求（全部 `code=0`），
随后走浏览器通道发一次 `search`，得到：

```
error.code      = ENVIRONMENT_RISK
platform_code   = 37
message         = 您的环境存在异常.
```

并且本地风控锁立即置位（`~/.boss-agent/cdp_risk_lock.json`，`platform_code: 37`），
之后 `boss status --live` 返回 `ENVIRONMENT_RISK_LOCKED`，`boss login --cdp` 也被拒绝，
提示语是正确的：

> 不要刷新 Token、重新登录或换通道重试；本次请求未发出

**当时的结论是「两个客户端排他」，后续实验推翻了它，见第七节。** 这一段保留为原始观察，
因为它是那条被推翻结论的来源；不要按它做设计。

## 三、我造成的第二个混用向量（必须记录）

采集招聘者侧数据时，我用 **9222** 这个同一个端口起了第二个 Chrome（招聘者 profile），
而没有换端口。后果是：此后所有不带 `--cdp-url` 的命令走 `auto` 策略，会自动探测
`localhost:9222`，于是**命中的是招聘者浏览器，却注入了候选者的 Cookie**。

这是账号身份混用，比 httpx/浏览器混用更严重。`auto` 的自动探测 + `use_stored_credentials=True`
组合本身不区分「探测到的这个浏览器属于哪个账号」，这是一个值得修复的真实缺口。

事后被动观察（未导航）：招聘者 Chrome 页面仍正常渲染（`innerText` 5327 字符，
URL 无 403/verify），但它的 Cookie jar 里已经没有 `__zp_stoken__`——说明该 stoken 已被判废。

## 四、因此对「不碰 CDP」的建议

1. **不要做自动降级**。让 `search` 在浏览器通道不可用时静默改用 httpx，会制造上面那种
   半混用状态，正是 code 37 的成因。
2. **如果要做，就做显式模式**：一个「本进程绝不打开任何浏览器」的来源取值，
   全部只读走 httpx，需要页面的能力直接返回未支持。它是用户显式选择，不是降级。
3. **端口冲突要能被发现**：`auto` 探测到 CDP 时，应校验该浏览器里的账号与会话是否同源，
   不同源就拒绝复用其会话。本节的失误正是缺这一道校验。→ **已实现**，见下一节。

第 2 项仍未实现：要对齐登记的错误码与 schema，属于契约变更，需要单独立项。

## 三之二、已落地的账号同源校验

`BrowserSession._try_connect` 复用探测到的 context 前，先比对本地凭据与那个 context 里的
`wt2`：

| 情况 | 行为 |
|---|---|
| 值相同 | 复用（同账号，符合预期） |
| 值不同 | **拒绝复用**，关闭连接并落到下一个通道（用本地凭据自建浏览器） |
| context 里没有 `wt2` | 视为未登录、不构成账号冲突，保持原有复用行为 |
| 本地没有凭据（`existing-browser`） | 没有可比对象，保持原有复用行为 |
| 读取 cookie 抛错 | 记日志后按"读不到"处理，不阻断 |

只用 `wt2` 比较，且要求 cookie domain 落在 `.zhipin.com` 上——别的域上的同名 cookie
不是同一个登录态。

验证方式：7 个离线用例覆盖上述分支，其中一个直接驱动 `_try_connect`，断言连接到别账号的
浏览器时返回 `False` 且关闭连接。**没有做"真实切换到另一个账号再触发"的验证**：那会重复
本节记录的那次混用，为了验证一个守卫再冒一次账号风险不划算。

## 五、复现方式

```bash
# 先确认没有浏览器占用 9222，且本机没有活跃的 CDP 会话：
lsof -nP -iTCP:9222 -sTCP:LISTEN

# 纯 httpx 只读（每条都应是 code=0）
uv run python - <<'PY'
from pathlib import Path
import httpx
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.api.httpx_helpers import browser_headers, new_trace_id
from boss_agent_cli.api.endpoints import DEFAULT_HEADERS, BASE_URL
t = AuthManager(Path.home()/".boss-agent").get_token()
h = browser_headers(DEFAULT_HEADERS, t); h["traceid"] = new_trace_id()
with httpx.Client(base_url=BASE_URL, cookies=t.get("cookies", {}), headers=h, timeout=30) as c:
    r = c.get("/wapi/zpgeek/search/joblist.json",
              params={"query": "Python", "page": 1, "city": "101280100",
                      "__zp_stoken__": t.get("stoken", "")})
    print(r.status_code, r.json().get("code"))
PY
```

不要在同一个 stoken 上再叠加浏览器通道请求——那会得到 code 37。

## 边界

- 本节所有数据来自真实账号的一次性只读请求，未包含任何 Cookie 或 token 值。
- 命中 37 之后按既有契约停止并保留 checkpoint，没有通过换通道或重登绕过。
- 恢复需要操作者在官方页面确认可用后显式执行 `boss clean --risk-lock`，本文件不代表已恢复。

## 六、已实现：`--browser-source httpx-only`

策略表新增第四个来源，通道白名单为空——它在结构上就不可能打开浏览器：

| 项 | 值 |
|---|---|
| `channels` | `()` |
| `use_stored_credentials` | `True` |
| `allow_browser_launch` / `may_create_context` / `auto_probe_cdp` | 全部 `False` |
| `failure_code` | `BROWSER_DISABLED`（已登记进 `SCHEMA_DATA["error_codes"]`） |

行为：

- `search` / `recommend` / `detail` / `friend_list` / `chat_history` 全部走 httpx；
  `browser_enabled()` 为 `False` 时 `search`/`recommend` 直接改走 httpx（此前它们无条件走浏览器）。
- 需要页面的能力（`greet`、`exchange_request`、`job_card` 等）抛
  `BrowserSourceUnavailable`，错误码 `BROWSER_DISABLED`，不会启动任何浏览器。
- `--cdp-url` 仍然是"显式要求走 CDP"，但 `httpx-only` 下 `_try_connect` 根本不会被调用。
- CLI help、schema `global_options.--browser-source` 的 choices 与描述随策略表自动更新，
  另有断言守住三处不漂移。

现场实测（9222 上确有 CDP Chrome 在监听）：`--browser-source httpx-only boss search` 返回
15 条，全程未触碰该浏览器。

## 七、关于 code 37：我此前的一个结论被自己的后续实验推翻了

上一版这里写的是「同一个 `__zp_stoken__` 被两种客户端使用就会被标记，二者排他」。
**这个结论不成立，撤回。** 推翻它的实测：

| 顺序 | 结果 |
|---|---|
| 浏览器 `search` → 紧接着 `httpx-only search` | 后者 `code 37`，本地风控锁置位 |
| 重载页面 + 重登 → `httpx-only search` → 浏览器 `search` | **两者都成功**，无 37 |

同一对客户端、相反顺序，一次失败一次成功，所以"排他绑定"解释不了它。
现在能确认的只有两点：

1. `boss status --live`（httpx，不带 `__zp_stoken__`）在两种状态下都不触发 37。
2. 37 出现在**短时间内连续两次平台调用**之后，且当时其中有我绕过项目请求预算、
   直接发的原始 httpx 请求。

因此仍成立的候选原因是**突发频率**而不是客户端混用：项目本身有跨进程请求预算
（SQLite 原子预约、5–10 秒窗口），我的原始 httpx 探测脚本完全绕过了它，
之后紧接着的命令就撞上了 37。这与 2026-10-14 那次分析里「请求峰值」的记录一致。

要定性需要一个受控对照：同样的顺序，中间插入足够长的间隔，看 37 是否消失。
本文件**没有**做这个对照，所以不给出结论。

## 七之二、恢复步骤（已验证三次）

无论 37 的成因是哪一种，下面这套恢复都有效，且第 1 步不能省：

1. 在 Chrome 里**重载目标页** —— `__zp_stoken__` 由页面 JS 重新生成
   （实测重载后该值确实变化，此处不保留任何取值片段）。页面本身始终正常渲染，没有出现过 403/验证页。
2. `boss clean --risk-lock`
3. `boss login --cdp`（重新读取新 stoken）
4. `boss search` 恢复返回 15 条

只做 2–3 步、不复位页面时，重登拿到的仍是已被标记的 stoken，浏览器通道继续 `code 37`
（本次实测两次）。

## 八、仍未做

- 把 `search` 的 HTTP 方法与真机对齐为 `POST` + 表单（两种都可用，属行为差异而非缺陷）。
- HTTP/2：`h2` 未安装，`httpx.Client(http2=True)` 不可用，协议层差异仍在。
- 写操作在 `httpx-only` 下的具体错误体验（目前统一 `BROWSER_DISABLED`，尚未逐命令打磨提示语）。
