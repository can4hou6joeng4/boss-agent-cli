# 命令参考

> 能力真源是 `boss schema`（机器可读的完整自描述：命令、参数、平台支持与错误码）。
> 本页是面向人类的速查表；当两者不一致时，以 `boss schema` 实际输出为准。
> 英文版见 [commands.en.md](commands.en.md)。

```bash
boss schema                            # 完整能力 JSON（Agent 首先调用）
boss schema --format openai-tools      # 导出 OpenAI Functions / Tools 定义
boss schema --format anthropic-tools   # 导出 Claude Tool Use 定义
boss <命令> --help                      # 查看单个命令选项
```

兼容配置：`boss config set operating_mode assisted|research`。两种模式均可调用全部已实现能力；schema 仍提供风险和数据分类，平台缺失能力返回 `NOT_SUPPORTED`。

## 命令还是 wizard

顶层命令和 `boss wizard` 是两套并行的能力面，分工写在 `boss schema` 的 `conventions.command_vs_wizard`：

- **单次、无状态的能力调用** → 顶层命令（`boss search` / `boss detail` / `boss greet` …）
- **需要跨步骤状态、可恢复、或中途要把指引递给真人** → `boss wizard`（goal 取值见 `wizard_catalog`）

信封的 `hints` 同样按受众分成两条通道（`conventions.hints`）：

| 字段 | 受众 | 形式 |
|------|------|------|
| `hints.next_actions` | AI Agent | `boss xxx` 命令，Agent 直接执行 |
| `hints.operator_actions` | 真人操作者 | 自然语言，通常需要离开终端完成（扫码、在浏览器里调整筛选、处理风控验证） |

TTY 下只把 `operator_actions` 渲染到 stderr；`next_actions` 是纯 Agent 通道，不渲染。Agent 拿到 `operator_actions` 时应转述给操作者，而不是自己编措辞。

## 基础操作

| 命令 | 说明 |
|------|------|
| `boss` / `boss wizard` | TTY 下启动纯向导；`--input-json` 供 Agent 执行共享 workflow，`--status/--resume/--stop <run_id>` 管理持久化任务 |
| `boss schema` | 输出完整工具能力描述 JSON（39 个顶层命令 + hr 分组展开，Agent 首先调用） |
| `boss platforms` | 本地平台注册与能力状态（不触网；支持 `--platform` 单平台过滤与 `--capability` 反查，附 `capability_status_legend`） |
| `boss login` | 用户主动登录（按平台走 Cookie / CDP / QR / 浏览器降级链路） |
| `boss logout` | 退出登录 |
| `boss status` | 检查登录态（默认仅本地；`--live` 才执行低频只读验证） |
| `boss doctor` | 诊断环境、依赖、凭据完整性和网络；默认仅本地诊断，`--live-probe` 才执行低频只读探测 |
| `boss me` | 我的信息（用户/简历/期望/投递记录） |

### 从浏览器 cURL 导入登录态

浏览器 Cookie 自动提取不可用时，可将自己已登录的 `https://www.zhipin.com` 请求通过 **Copy as cURL (bash)** 复制为原始文本，保存为仅自己可读的文件：

```bash
boss login --curl-file /path/to/private-request.txt
# macOS：从剪贴板通过标准输入导入，避免把凭据写入命令参数
pbpaste | boss login --curl-file -
```

仅支持常见 POSIX cURL 格式，不支持 PowerShell/cmd 或 Markdown 转义文本；不能与 `--cdp` / `--cookie-source` 混用。只提取内联 Cookie、User-Agent 和 Cookie 中的 stoken；不会执行 cURL、读取其中引用的文件或重放请求体。使用现有用户信息接口做一次只读验证，验证通过后**替换当前 BOSS 原生加密会话**，不合并不同账号的旧 Cookie；验证失败不会覆盖原会话或自动打开浏览器。

导入是登录态的另一种输入方式，不是绕过登录或风控，也不能复制浏览器指纹。后续过期刷新仍遵循原有 AuthManager / 浏览器来源策略，不能保证永远无需浏览器。`zp_token` 请求头不单独保存；招聘请求仍从当前 `bst` Cookie 派生。原始 cURL 文件和剪贴板含登录凭据，CLI 不会替你清除，请妥善处理，不要提交到仓库或发送到聊天中。

## 职位搜索

| 命令 | 说明 |
|------|------|
| `boss search <query>` | 搜索职位（支持 `--url` 网页筛选、逗号多选、`--welfare` 筛选、`--sort score` 本地排序、`--preset` 预设） |
| `boss recommend` | 获取个性化推荐职位 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss cities` | 40 个支持城市 |

## 可恢复批量采集

`crawl` 是用户显式触发的有界 Chrome 任务。首次使用需安装 `uv sync --extra crawl`；采集器只启动 `<data-dir>/crawl/chrome-profile` 这个独立 profile，不接管日常 Chrome。请求数、详情数、墙钟时间和重试由固定预算控制，每一步持久化 checkpoint，并可用 `stop` 停止。

```powershell
boss crawl configure --max-requests 20 --max-details 50 --max-seconds 600 --max-retries 1
boss crawl run "AI" --city 杭州 --pages 3 --with-detail `
  --hook-profile screenshot-full --hook-dir E:\boss-agent-cli-local-hooks\AntiDebug_Breaker
boss crawl resume <run_id>
boss crawl stop <run_id>
```

| 命令 | 说明 |
|------|------|
| `boss crawl configure [--chrome-path PATH] [--port N] [--max-* N]` | 设置 crawl 专用 Chrome 和请求、详情、墙钟、重试预算；profile 固定为 `<data-dir>/crawl/chrome-profile` |
| `boss crawl run <query> --city <城市或代码> [--pages N] [--with-detail]` | 串行采集；`--pages` 默认 `5` 且必须为正数；`--with-detail` 串行补全职位详情 |
| `boss crawl start <query> --city <城市或代码> [...]` | 创建后台任务并立即返回 `run_id`；供本地任务调度使用 |
| `boss crawl status <run_id>` / `boss crawl results <run_id>` | 仅读取 SQLite 中的页游标、风险状态、详情进度和已持久化职位；不打开浏览器 |
| `boss crawl resume <run_id> [--pages N] [--with-detail] [--background]` | 从页游标、已见职位和待补详情队列恢复；`--background` 立即返回以便轮询；可提高正数页数上限并补全详情，不重复写入已完成项 |
| `boss crawl stop <run_id>` | 请求运行中任务在下一个安全点停止并保留 checkpoint |
| `boss crawl shortlist <run_id> (--all \| --selector <csel_...>)` | 将 crawl 结果导入原项目的本地候选池；不请求平台，保留内部职位关联和详情缓存供 `boss ai fit` 使用 |

默认 Hook 为 `none`。`screenshot-full` 仅在用户明确选择 `--hook-profile screenshot-full --hook-dir <目录>` 时启用；目录必须由用户拥有相应授权，并提供原始 7 个脚本及 `SHA256SUMS`。项目不再发布这些第三方脚本，运行前逐文件校验 SHA-256 并记录脚本标识与摘要；不记录 Cookie、请求头或完整请求体。

候选人侧可用 `boss agent crawl --run-id <run_id> --resume <简历名>` 执行“完成的 crawl → shortlist → ai fit → 按匹配分排序”，不会启动浏览器；`boss agent crawl --query <关键词> --city <城市> --resume <简历名>` 会新建真实采集。遇到 `risk_stopped` 或 `budget_stopped` 时 Agent 只返回 `run_id` 和恢复命令，不会无限重试或重开会话。

每页完成后更新 `<data-dir>/crawl/runs/<run_id>/jobs.json`、`jobs.csv` 和带筛选/冻结首行的 `jobs.xlsx`。XLSX 保留完整值但所有数据行固定为单行和统一行高，长内容仅在表格中截断显示。JSON/CSV/XLSX 和 `crawl results` 默认不包含 `security_id`、职位 ID、selector、招聘者姓名或职位；这些仅保留在受限本地 SQLite 状态，`boss clean --privacy` 会删除 crawl 运行、预算和导出。风险码 `37` / `38`、安全页、职位列表容器异常、预算耗尽或 stop 请求都会保存断点并停止；stdout 始终只输出 JSON 信封和恢复命令。

## 求职动作

| 命令 | 说明 |
|------|------|
| `boss greet <sid> <jid>` | 向指定招聘者打招呼；重复记录返回 `ALREADY_GREETED` |
| `boss batch-greet <query>` | 搜索后按显式 `--limit` 批量打招呼，支持 `--dry-run` |
| `boss apply <sid> <jid>` | 发起投递或立即沟通；重复记录返回 `ALREADY_APPLIED` |
| `boss exchange <sid>` | 请求交换手机号或微信 |

## 沟通跟进

| 命令 | 说明 |
|------|------|
| `boss chat` | 查看沟通列表，支持分页和来源筛选 |
| `boss chatmsg <sid> [--raw]` | 查看聊天历史；`--raw` 保留结构化 body、链接和职位卡片字段 |
| `boss chat-summary <sid>` | 基于聊天历史生成结构化摘要 |
| `boss mark <sid> --label X` | 添加或移除联系人标签 |
| `boss interviews` | 面试邀请 |
| `boss history` | 浏览历史 |

## 流水线监控

| 命令 | 说明 |
|------|------|
| `boss pipeline` | 聚合会话和面试数据生成候选进度 |
| `boss follow-up` | 筛选需要跟进的会话和面试项 |
| `boss digest` | 汇总新增职位、待跟进会话和面试项 |
| `boss watch add/list/remove/run` | 保存、列出、删除或执行增量职位监控 |
| `boss shortlist add/list/annotate/compare/remove` | 本地候选池：支持标签、备注和离线对比 |
| `boss favorites list/sync` | 读取 BOSS 职位收藏并呈现有效状态；sync 只导入明确有效职位（按职位去重、刷新动态访问 ID，保留首次收藏时间） |
| `boss preset add/list/remove` | 搜索预设 |

## 招聘者模式

| 命令 | 说明 |
|------|------|
| `boss hr applications` | 查看候选人投递申请 |
| `boss hr resume <geek_id> --job-id <id> --security-id <id>` | 查看候选人在线简历 |
| `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | 请求交换手机号或微信 |
| `boss hr chat` | 查看候选人沟通列表 |
| `boss hr chatmsg <friend_id>` | 查看候选人聊天记录 |
| `boss hr last-messages [--friend-id <id>]` | 批量查看候选人最近消息摘要 |
| `boss hr jobs list/offline/online` | 职位列表与上下线管理 |
| `boss hr candidates <keyword>` | 搜索和筛选候选人 |
| `boss hr reply <friend_id> <message>` | 回复候选人消息 |
| `boss hr request-resume <friend_id>` | 请求候选人分享附件简历 |
| `boss hr recommendations --job-id <encJobId>` | 读取推荐牛人完整卡片和首次开聊参数 |
| `boss hr greet ... --message <话术> --yes` | 建立候选人会话、发送首次招呼，并在需要时处理会话红点 |

先在同一组候选人参数和话术后加 `--dry-run` 预览；操作者明确批准该候选人和话术后，才改为 `--yes` 发送。MCP 的 `yes` 默认不传，Agent 不得自行确认；预览本身不构成人工批准。

`greet` 按候选人和招聘职位的加密 ID 在本地原子预约，成功后记录已发送；响应丢失、进程退出或限流时保留预约，禁止自动重发。使用 `boss hr chat --job-id <id>` 核对会话，必要时在官方页面处理，不要删除记录来重发。

清红点仍是 `greet` 的收尾，不是独立命令。已知未读数为零时返回 `not_needed`；未授权 MQTT 时返回 `deferred`。授权后，即使未读数缺失，也会根据准确匹配的会话和最新消息 ID 发送一次已读回执；无法确定目标或消息 ID 时返回 `unknown`，不发送。`chat` / `last-messages` 中未知未读数仍保留为 `null`，不当作零。

独立 MQTT 会话可能挤掉正在使用的网页连接。只有操作者单独批准此风险，才可在发送前加 `--allow-mqtt-session`（MCP：`allow_mqtt_session=true`）；`--yes` 仅批准候选人和话术，不包含这项授权。随机客户端 ID 不保证同账号连接共存，网页退出的具体原因尚未确定，不应以真实账号自动探测。

MQTT 参数参考网页 v11308：使用 `ws-` 加 16 位大写十六进制客户端 ID、MQTT 3.1、`cleanSession=true` 和 10 秒连接超时配置；密码及 WebSocket 子协议来自 `get/wt`，Cookie 则由原生 jar 按服务器域名和 `/chatws` 路径选择，不覆盖 `wt2`。上线包的 `uniqid` 来自 `__a` 第 2 段与第 1 段拼接，`model` 来自 `sid`，缺失时留空。UA 沿用原生认证信息，缺失时仍使用 CLI 默认值；已导入的 Cookie 若没有域名/路径元数据，不能宣称与浏览器实际请求完全一致。本次不实现连接复用，不改变心跳计算；参数对齐不代表已解决网页退出。

`--read-receipt-timeout` 默认为 25 秒（1–60）。收尾使用私有短生命周期子进程和原生认证存储，总预算覆盖启动、查询及回执；超时后终止并回收进程，避免后台继续请求，但不能撤回已送达平台的操作。收到 MQTT QoS 1 的发布确认后返回 `read_state.status=published`、`partial_success=false`，结束收尾，不再回读红点。此结果表示回执已发送，不表示已核验网页未读数归零；不再返回 `cleared`。普通收尾失败返回 `partial_success=true`；认证、风控或限流返回错误信封并在 `error.details.sent=true` 保留已发送事实，操作者指引在 `hints.operator_actions`，不可因收尾失败或补授权重发招呼。

## 简历与 AI

| 命令 | 说明 |
|------|------|
| `boss resume init/list/show/edit/delete/export/import/clone/diff/link/applications` | 本地简历管理 |
| `boss ai config` | 配置 AI 服务 |
| `boss ai local status` | 查看本地模型配置、推荐模型和导入登记 |
| `boss ai local configure --runtime ollama --model qwen3:14b` | 配置本地 Ollama OpenAI 兼容服务 |
| `boss ai local pull --model qwen3:14b --confirm-download` | 显式下载本地模型权重 |
| `boss ai local smoke` | 调用本地模型做一次健康检查 |
| `boss ai analyze-jd` | 分析岗位要求 |
| `boss ai polish` | 润色简历 |
| `boss ai optimize` | 针对目标岗位优化 |
| `boss ai suggest` | 求职建议 |
| `boss ai reply` | 生成招聘者消息回复草稿 |
| `boss ai interview-prep` | 基于 JD 生成模拟面试题 |
| `boss ai chat-coach` | 基于聊天记录给沟通建议 |
| `boss ai cover-letter` | 基于本地简历与目标岗位起草求职信/自我介绍（仅草稿，不发送） |

> 支持 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 等最新模型，详见 [推荐模型与入口](integrations/ai-models.md)。

## 系统管理

| 命令 | 说明 |
|------|------|
| `boss config list/set/reset` | 配置管理 |
| `boss clean` | 清理缓存 |
| `boss stats` | 投递转化漏斗统计（greeted/applied/shortlist） |
| `boss export <query>` | 导出结果（CSV/JSON/HTML，支持 `--url` 网页筛选） |

## 搜索筛选参数详解

```bash
boss search "golang" \
  --city 广州 \             # 城市（40 个可选）
  --salary 20-50K \         # 薪资范围
  --experience 3-5年,5-10年 \ # 经验要求（支持逗号多选）
  --education 本科,硕士 \    # 学历要求（支持逗号多选）
  --scale 100-499人 \       # 公司规模
  --industry 互联网 \       # 行业
  --stage 已上市 \          # 融资阶段
  --welfare "双休,五险一金" \ # 福利筛选（AND 逻辑）
  --sort score              # 按本地 match_score 降序
```

也可以先在 BOSS 直聘网页上手动选好筛选条件，再复制搜索页 URL 给 CLI：

```bash
boss search --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100&experience=104,105'
boss export --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100' --count 50 -o jobs.csv
```

**福利筛选工作原理**：

1. 先检查职位福利标签（`welfareList`）
2. 标签不匹配时自动获取职位描述全文搜索
3. 自动翻页（最多 5 页）
4. 每个结果带 `welfare_match` 说明匹配来源，并带 `match_score` 供 `--sort score` 本地排序

支持关键词：`双休` `五险一金` `年终奖` `餐补` `住房补贴` `定期体检` `股票期权` `加班补助` `带薪年假`
