# 架构

本文面向第一次读源码的贡献者与需要理解契约的 Agent 宿主，说明 boss-agent-cli 由哪些层组成、一次调用如何流动、以及哪些约束不能破坏。命令与参数细节见 [命令参考](commands.md)，能力真源始终是 `boss schema`。

## 设计目标

- **同一套能力，四种入口**：真人终端向导、JSON 信封（管道 / 脚本）、MCP server、Python API 调用的是同一批命令与同一份本地状态。
- **本地优先**：除用户显式发起的平台请求外，数据不离开本机；不包含任何运行时遥测 / 埋点 / 匿名统计。
- **可编排**：stdout 只有一个 JSON 信封，日志与终端渲染全部走 stderr。
- **有限运行**：所有长流程受超时、重试、请求预算、checkpoint 与停止开关约束；平台风控是硬边界。

## 四条入口

| 入口 | 形态 | 适用场景 |
| --- | --- | --- |
| `boss`（TTY） | 纯终端向导，逐步骤询问并渲染结果 | 真人首次使用、跨步骤任务 |
| `boss <命令> --json` / 管道 | 每条命令一个七键 JSON 信封 | Shell、脚本、非交互 Agent |
| `boss-mcp` | 通过 stdio / SSE / HTTP 暴露 MCP 工具 | Claude Desktop、Cursor、Windsurf、Codex 等宿主 |
| `from boss_agent_cli import AuthManager, BossClient` | Python 嵌入 API | 自定义脚本与服务 |

四条入口共享同一套命令实现、同一份 `~/.boss-agent/` 本地状态和同一组错误码。

## 信封契约（不可破坏）

每条命令向 **stdout** 只输出一个 JSON 对象，恰好七个键：

```json
{"ok": true, "schema_version": "1.0", "command": "search", "data": [], "pagination": null, "error": null, "hints": {"next_actions": []}}
```

- `exit 0` ⇔ `ok=true`，`exit 1` ⇔ `ok=false`；`--exit-codes sysexits` 可按错误类别返回语义化退出码。
- 错误信封必须包含 `error.code` / `error.recoverable` / `error.recovery_action`；错误码登记在 `schema/error_codes.py`。
- `hints.next_actions` 是给 Agent 执行的后继命令；`hints.operator_actions` 是给真人的自然语言指引。TTY 只渲染后者到 stderr。
- 输出层会对 `data` / `hints` 中的敏感键做脱敏。

## 能力真源：`boss schema`

`schema/data.py` 描述命令、参数、可用性、全局选项与错误码；`boss schema` 把它渲染为原生 JSON，或转换为 `openai-tools` / `anthropic-tools` / `mcp-tools` 格式。MCP 工具目录同样从这份数据与 `schema/tool_catalog.py` 派生，因此 **schema 与实际命令必须同步**——测试会双向校验。

## 模块分层

```text
main.py                 Click group、全局选项、用法错误的信封化翻译
  └─ commands/register.py   注册 39 个顶层命令与 hr 命令组
       └─ commands/<cmd>.py  Click 命令；只做参数校验 + 调用平台/服务 + 出信封
            ├─ commands/_platform.py / _recruiter_platform.py   按 ctx 取平台实例
            │    └─ platforms/factory.py → Platform / RecruiterPlatform 注册表
            ├─ services/    可复用的纯业务逻辑（导出、快照、联系人查找、job_card 映射）
            └─ api/         传输层
                 ├─ client.py / recruiter_client.py   httpx 客户端、节流、错误映射
                 ├─ browser_client.py                CDP / headless 浏览器通道
                 ├─ browser_source.py                通道策略表（唯一分发点）
                 └─ *.yaml                           端点 / 默认头 / 平台响应码真源
       └─ auth/AuthManager      登录态：Cookie 提取 → CDP 探测 → patchright 扫码；Fernet 加密
       └─ wizard/               可恢复 workflow runner（TTY 向导与 --input-json 共用）
       └─ automation/           招聘者侧浏览器 / CDP 自动化（安全额度、预算、checkpoint）
       └─ crawler/              可恢复批量采集（SQLite 断点、独立 chrome-profile）
  └─ output.py / display.py  JSON 信封 + stderr 渲染 + 错误合同
  └─ mcp_tools.py / mcp_server.py   MCP 工具目录、传输与子进程调用 boss CLI
  └─ cache/store.py          SQLite（WAL）本地缓存
  └─ schema/                 能力数据、错误码、工具目录与格式转换
```

设计约束：

- **命令层不直接 new 客户端**，统一经 `_platform` / `_recruiter_platform` 构造，平台由 `--platform` 或配置决定。
- **`commands/` 只放两类模块**：定义 Click 命令的模块，和 `_` 前缀的 CLI 支撑模块；不依赖 Click / display 的可复用逻辑放 `services/`。
- **wizard 不 import 命令模块**，也不调用 `_` 前缀函数；它通过服务层与 runner 复用能力。
- **MCP server 通过子进程调用 `boss` CLI** 并解析 stdout 信封，而不是 import 命令函数——信封契约因此同时是 MCP 契约。

## 一次 `boss search` 的调用链

1. `main.py` 解析全局选项，校验 `--data-dir` / `--cdp-url` / `--delay`，加载 `config.json`，构造 `ctx.obj`。
2. `commands/register.py` 找到 `search` 命令；命令层校验查询词与筛选参数，必要时展开 `--preset` / `--url`。
3. `_platform.get_platform_instance(ctx, auth)` 按平台注册表构造 `BossPlatform` 与其客户端。
4. 客户端按 `--browser-source` 策略表选择通道（CDP → headless 或 httpx-only），经节流与请求预算发出请求，解析端点 YAML 中声明的响应结构。
5. 命令层把结果映射为 `data` 与 `pagination`，由 `display.handle_output` 在 TTY 渲染、在管道输出信封。
6. 任一层抛出 `PlatformRiskError`（账号 / 环境风控）时立即终止当前 workflow、保存 checkpoint，并由 `display` 统一给出恢复动作。

## 登录与认证

`auth/AuthManager` 维护加密登录态（Fernet + 机器绑定密钥）。登录链路按平台声明依次尝试：浏览器 Cookie 提取 → CDP（复用已登录 context 前做一次只读探测，失效自动改走强制登录）→ patchright 扫码。登录态只保存在本地；CLI 不读取或上传任何与登录无关的浏览器数据。

## 平台抽象

`Platform`（候选人侧）与 `RecruiterPlatform`（招聘者侧）是两个独立注册表，当前唯一注册平台是 BOSS 直聘（`zhipin` / `zhipin-recruiter`）。多平台抽象保留，但新增平台需要端点表、能力可用性、schema 与测试同步接入，详见 [平台抽象](platform-abstraction.md)。

## 长流程、预算与风控

- 批量采集、招聘者自动化、wizard workflow 都受五重约束：timeout、retry、请求 / 详情 / 墙钟预算、checkpoint、stop。
- 命中账号风险、环境风险或安全验证页时，当前 workflow **立即停止并保存 checkpoint**，不做无界重试，也不通过 CDP / patchright / Cookie 提取绕过；恢复必须由用户显式发起。
- 风控错误码与恢复文案的唯一真源是 `schema/error_codes.py` 与 `display.RISK_ERROR_CONTRACTS`。

## 本地数据目录

默认 `~/.boss-agent/`（`--data-dir` 可改）：

```text
config.json          用户配置
auth/session.enc     加密登录态（Fernet，机器绑定）
cache/boss_agent.db  SQLite 缓存：搜索历史、候选池、预设、watch、crawl 断点
chat-export/         聊天快照与导出
resumes/             本地简历
models/              本地模型登记（权重外置）
crawl/               隔离的 Chrome profile 与采集产物
automation/          招聘自动化状态与去重记录
```

这些文件只在本机使用；卸载或重置前请按需备份。

## 扩展新能力

新增命令 / 选项 / MCP 工具时，需要同步的清单见 [CONTRIBUTING](../CONTRIBUTING.md) 的「添加新命令」与「输出契约」两节，以及 [发版清单](maintainer/release-checklist.md) 中与 schema、文档计数、版本同步相关的条目。关键架构决策记录在 [ADR 索引](adr/README.md)。
