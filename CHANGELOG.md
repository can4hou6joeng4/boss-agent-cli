# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Fixed
- 写请求不再跟随 HTTP 重定向：首跳 3xx 直接按 `ACTION_UNCONFIRMED`（`outcome=unknown`、`replay_safe=false`）收口，跟随后的连接类异常不再把可能已生效的写操作报成可重试的 `NETWORK_ERROR`（Issue #2424）。
- 招聘者端 stoken 过期判断与候选端同口径，兼容字符串业务码 `"37"`，不再漏掉自动刷新（Issue #2425）。
- 平台兜底错误分类先判风控再判 auth，环境风险短语（「环境存在异常」「访问环境异常」「登录状态异常」等）归类为 `ENVIRONMENT_RISK`；英文 `rate` 改为词边界匹配，不再误伤 generate 等普通单词（Issue #2430）。
- 招聘者数值标记与数字字符串业务码的转换先做整串校验：`"--0"`、超长数字串与 `"²"` 等不再抛 `ValueError` 并被兜成可重试的 `NETWORK_ERROR`（Issue #2431）。
- `force_refresh` 在拿到刷新锁后复查风控锁：等锁窗口内落下的 `ACCOUNT_RISK` / `ENVIRONMENT_RISK` 锁不再被绕过，命中即停止且不写回新凭证（Issue #2427）。
- crawl 的职位监听器启动失败不再逃出降级路径：会话跳过监听等待、直接走页面内同源请求，确定性失败不会被记成可 resume 的 `stopped` 并永久重复（Issue #2428）。
- 简历解析在 `zpData` 为空对象时回落到旧格式 `data`，整份简历不再静默变成空结构（Issue #2433）。
- 列表命令在页码达到可信上限（`MAX_TRUSTED_PAGE`）后不再生成无法前进的下一页提示，Agent 不会无界重复同一请求；`has_more` 保留平台原始证据（Issue #2434）。
- wizard 的单请求写步骤（`exchange` / `mark` / hr 职位上下线）在派发前失败（代理配置、内核缺失、导航、预算）时撤销预落的「结果未知」标记：恢复指引与错误码回到原始异常，`--resume` 不再永久阻断一条从未发出的请求（Issue #2441）。
- wizard 断点的 `failed` / `stopped` 步骤带 `result.status=completed` 时按 `CACHE_RECORD_INVALID` 拒绝恢复，与其余非终态同口径：状态漂移不再让已完成步骤被重跑（Issue #2442）。
- `WorkflowControl.poll` 改为只读 run 的原始 `status` 列：断点里任一字段漂移不再吞掉 `--stop` / 截止时间信号，也不会把只读步骤误标成「写请求结果未知」（Issue #2443）。
- 自动化的派发前预检（限额 / stop / 安全告警）移到 `executing` 行与在途标记落盘之前：预检拒绝后的本地写失败不再被误报成「平台动作可能已发出」，不会留下需要人工核对的假在途标记，队列行保持 pending 可安全重试（Issue #2451）。
- 浏览器通道把预算租约 / 节流等待 / 请求前检查的本地状态错误（`sqlite3.Error` / `OSError`）与「请求已派发」区分：写请求不再被误报成 `ACTION_UNCONFIRMED`，原始 `LOCAL_IO_ERROR` 恢复指引保留（Issue #2437）。
- 浏览器通道在解析平台响应后先剥除平台对象上的 `__cli_*` 顶层键，再由 CLI 注入自己的传输标记：平台响应不能再伪造「未发出 / 已中断 / 安全页」改变写请求的重试语义（Issue #2438）。
- 聊天页 CDP 脚本的参数序列化与 WebSocket 握手失败包装成 `ChatTabCdpUnreachable`：`send_message` / `request_resume` / `exchange` 在首个 CDP 帧写出前失败时按「确定未发出、可安全重试」收口，不再报成结果未知（Issue #2439）。
- `response_codes` 在解析期统一归一为整数：YAML 里带引号的 `"37"` / `"31"` 不再让风控码、限流码与 token 刷新静默失效，非整数、bool、null 与超长数字串按带路径的配置错误拒绝；风控码白名单与浏览器通道限流码改用 `endpoints` 常量，配置真正成为单一真源（Issue #2446）。
- API YAML 加载期校验顶层与各子结构的形状：空文件、null 子表、非字符串 header / 端点字段与语法错误都带文件名与路径报配置错误，不再在导入期以裸 `TypeError` / `AttributeError` / `KeyError` 崩溃；`url: ""` 不再静默回落到首页（Issue #2447）。
- `endpoint_for_request` 分别加载候选者与招聘者 spec：单个 YAML 损坏只让该侧端点退回未声明语义（写请求不可重放），不再打断另一侧的请求（Issue #2448）。
- 自动化队列的阶段完成判定与扫描决策共用同一 ISO 校验：`state.json` 的阶段字段漂移成 `"false"` 等真值时，队列行不再被误标已执行而丢掉操作者批准的消息，也不会让扫描路径同轮改发模板问卷；队列处置过的候选人本轮不交给扫描路径补位（Issue #2450）。
- `boss_agent_train` 在 MCP 侧固定演练（参数映射从不生成 `--live`），超时 / 取消不再按写操作报 `outcome_unknown`、不再要求核对并不存在的平台写入；恢复指引改为可直接重试（Issue #2454）。
- 默认 `request_wait_timeout` 提高到 120 秒，并按客户端节流上限 + 15 秒设置有效下限：默认 `request_delay = [30, 60]` 时同进程第 2 次请求不再必然以 `REQUEST_BUDGET_EXHAUSTED`「请求节流等待超时」失败，分页、轮询与冷却重试恢复可用（Issue #2461）。
- 导出 JSON 的孤立代理项改为字面 `\ud800` 文本（合法代理对合并为单个码点）：`backslashreplace` 写出的未配对转义会让 jq 等严格解析器拒绝整份产物（Issue #2457）。
- `-o` 预检在 macOS 上提前拒绝 APFS 无法创建的非法 UTF-8 文件名，参数错误不再留到平台分页与快照更新之后才以 EILSEQ 暴露（Issue #2458）。
- CSV 清洗在判定公式前缀时跳过前导换行 / VT / FF / NUL / BOM，`"\n=..."` 一类单元格不再绕过单引号前缀（Issue #2459）。
- crawl 的 `running` 状态不再被当作「仍在执行」的证据：`crawl status` 探测执行锁并回报 `executor_active` / `running_stale`，执行器被 SIGKILL 后不再永远显示运行中，也不再提示去 stop 一个没人消费的标记（Issue #2465）。
- `crawl stop` 在 queued / running 但没有执行器持锁时直接按断点落 `stopped` 并回报 `stale_recovered`，不再写一条永远没人消费的停止标记后报成功；执行器仍持锁时维持原停止语义（Issue #2465）。
- crawl 恢复的预检与 transport / 预算构造移入终止路径：构造失败或命中风控锁时按断点写回 `stopped`（保留上一轮错误与断点里的预算计数），不再把执行权标记永久留在 `running`、清空错误并让随后的 `crawl stop` 假成功（Issue #2466）。

## [3.4.150] - 2026-11-22

### Fixed
- wizard 断点的非终态步骤（`pending` / `running` / `waiting_input`）带 `completed` 结果时按 `CACHE_RECORD_INVALID` 拒绝恢复，不再重放可能已生效的写步骤；`failed` / `stopped` 的既有语义不变（Issue #2418）。
- wizard 的 ID 字段（`security_id` / `gid` / `job_id` / `geek_id` / `resume` / `name` / `city`）要求字符串：容器不再经 `str()` 变成 repr 发给平台或用于本地查询（Issue #2419）。
- wizard 输入在 CLI / MCP / Python API 统一拒绝不可 JSON 序列化的值（bytes / set / Decimal / 容器键），入口报 `INVALID_PARAM` 而不是落库时抛裸 `TypeError`（Issue #2420）。
- `candidate_watch` 的 welfare 数组元素要求字符串：容器不再被转成 repr 持久化并让后续 `watch run` 静默错筛（Issue #2421）。

## [3.4.149] - 2026-11-22

### Fixed
- 浏览器通道的上下文销毁标记补上 patchright `TargetClosedError` 的实际文案（`Target page, context or browser has been closed`）：复用的页签被用户关闭时，读请求进入既有的有界恢复而不是裸抛（Issue #2379）。
- 浏览器通道区分「确定未发出」：CDP 会话建立与请求载荷序列化失败、JS 侧 `fetch()` 调用前抛出的异常返回可重试的 `NETWORK_ERROR` 并附 `request_sent=false`，写请求不再被误报成不可重放的 `ACTION_UNCONFIRMED`（Issue #2380）。
- 招聘者聊天事件的 WebSocket 帧解析限制扫描 8KB、最多收集 10 个片段，并跳过不可能开启片段的字节：8MB 二进制帧不再让同步解析卡住数十秒、拖垮监听 deadline（Issue #2381）。
- `crawl resume` 的 `--pages` 与断点进度交叉校验提前到 completed 快速路径之前：已完成任务不再静默接受把页数上限缩到断点之前的矛盾参数，前台与后台入口一致返回 `INVALID_PARAM`（Issue #2386）。
- 聊天快照与职位 / 聊天 JSON 导出把十进制超过 4300 位的整数归一为 null，不再因单个数值漂移整体失败并误报 `LOCAL_IO_ERROR`，与 CSV / HTML 通道的降级口径一致；本地状态写入仍按 `EINVAL` 收口（Issue #2387、#2388）。
- 聊天导出的 `from_who` 统计改为与可见分组同源：「总计」与分组计数不再包含被方向过滤掉的好友，避免出现总计 2 条却只渲染 1 行（Issue #2392）。
- 登录降级链在 CDP 探测与扫码兜底前复查本地风控锁：Cookie 提取 / CDP 登录耗时期间另一进程写下的锁不再被忽略，扫码通道不会继续访问平台（Issue #2394）。
- 写请求遇到 `httpx.TooManyRedirects` 时按结果未知收口（`ACTION_UNCONFIRMED`）：已跟随多次重定向的写操作不再逃逸成可重试的 `NETWORK_ERROR`，读请求仍按普通网络错误上抛（Issue #2396）。
- automation 的 state.json 熔断快照缺少 `open` 字段时按状态损坏拒绝：`SafetyGuard` 不再把「停止状态未知」当成未熔断并继续自动化写操作，与 stop.json 的缺字段口径一致（Issue #2397）。
- wizard 断点的 `ACTION_UNCONFIRMED` 即使缺少 `replay_safe` 布尔值也阻断重放：`has_blocked_step` 与 `--resume` 共用同一判定，旧数据 / 外部漂移不会再让结果未知的写步骤被重跑并落成 completed（Issue #2398）。
- MCP 入口对数组元素的 `minLength` / 数值上下界错误与类型错误共用同一上限：十万个空标签只会得到前 3 条明细与一条汇总，错误信封不再随输入规模膨胀到数 MB（Issue #2399）。
- 后台 `crawl resume` 在锁内重读进入交接窗口时同样拒绝显式 `--pages` / `--with-detail` / 停止标记，不再静默返回 queued（Issue #2401）。
- `crawl resume` 的 completed 快速路径补上设置语义校验：空白关键词等损坏断点在前台与后台得到一致结论（Issue #2402）。
- `crawl resume` 区分页数冲突与断点设置损坏的恢复指引：设置类错误改为引导修正断点或新建任务，不再反复提示调整 `--pages`（Issue #2403）。
- 采集 XLSX 导出把超过 1000 bit 的整数按文本写出：openpyxl 保存时不再因整数转 float 抛 `OverflowError` 让整份产物替换失败（Issue #2405）。
- 采集 JSON / CSV 导出与职位 / 聊天导出同口径降级超长整数：JSON 数值降级为 null，CSV 按缺失处理，单个字段漂移不再让整份产物失败（Issue #2406）。
- 采集导出的非对象行按缺失跳过：`row.get` 的 `AttributeError` 不再绕过导出错误包装、让命令层拿到裸异常（Issue #2407）。
- `crawl shortlist` 导入把显式 null 主键按缺失处理：不再生成 `security_id` / `job_id` 都是 `"None"` 的伪记录，空主键行计入 `skipped_count`（Issue #2408）。
- 后台 `crawl resume` 在执行锁被其他执行器占用时，显式 `--pages` / `--with-detail` / 停止标记清理未生效就报可重试的 `CRAWL_BUSY`，不再以 ok:true 的 running 静默丢弃参数（Issue #2410）。
- `crawl shortlist` 的空 `run_id` 按参数错误上报 `INVALID_PARAM`，不再被当成任务不存在并引导重建（Issue #2411）。
- 前台 `crawl resume` 的 `--pages` 冲突与断点设置损坏分别给出精确恢复指引，与后台入口同口径；`crawl configure` 不再被当成修复手段（Issue #2412）。
- MCP 的 `boss_agent_run` 省略 `dry_run`（schema 默认 true）时按只读判定：超时 / 取消不再误报 `outcome_unknown` 并提示核对不存在的平台写入；显式 `dry_run=false` 仍按写处理（Issue #2414）。
- 沟通列表容器漂移成字符串 / 对象时，`find_friend` 首屏即报 `FriendListShapeError`，不再翻满 50 页放大平台请求；空列表容器旁的 junk 兄弟键按空页终止（Issue #2415）。

## [3.4.148] - 2026-11-22

### Fixed
- 登录错误分类只在明确状态码上下文（`HTTP 401` / `status: 401` / 单独 `401` 等）判为 `LOGIN_EXPIRED`：端口 4010、`step 401` 不再被误判并诱导重新登录（Issue #2335）。
- 风控文本判定提前到所有文本类型启发式之前：`timeout + security.html` 混合文案与携带风控词的 `ValueError` 不再降级成可重试的 `LOGIN_TIMEOUT` / `INVALID_PARAM`，安全页命中仍是硬停止（Issue #2336）。
- 登录错误分类入口对底层异常文本统一脱敏，`wt2` / `at` 进入敏感键名单，wizard 预检渲染前同样脱敏：会话 Cookie 明文不再进入信封或终端（Issue #2337）。
- MCP 的 action 互斥把「等于 schema 默认值的参数」按缺省处理（`boss_hr_jobs` 的 `page=1`），并在 `boss_wizard` / `boss_config` 的 inputSchema 中显式禁止 run 带 `run_id` / list 带 `key`，schema 合法输入不再被映射层拒绝（Issue #2339）。
- `boss_crawl_shortlist` 的 `selectors` / `all` 互斥改为按值判断：显式 `all=false` 与 `selectors` 并存不再报错，schema 的 `not` 条件同步改为值条件（Issue #2340）。
- schema availability 的 hr 分支从传入 data 读取 `subcommands`，不再回落全局 `SCHEMA_DATA`；`supported_recruiter_platforms` 非列表时归一为空列表，定制 schema 不会再发布与数据不一致的可用性（Issue #2341）。
- 聊天导出把超大整数 `unread` 按缺失处理：md / html 通道不再因整数转字符串上限抛裸 `ValueError`，与 CSV 及 #2162 口径一致（Issue #2343）。
- Markdown 导出的分组标题与总计行转义 `initiated_by`：换行与链接语法不再能向导出报告插入伪造章节（Issue #2344）。
- 职位 HTML 导出的「共 N 条」只统计实际渲染的对象条目，与表格行数一致（Issue #2345）。
- `wizard` 的 `list_recent` 补上 pending + completed 一致性校验：只能重建的 run 不再出现在恢复列表，与 `--resume` 结论一致（Issue #2346）。
- 招聘者候选人搜索的 `query` 与其余字符串筛选项走严格类型校验：`false` / `0` 不再变成空关键词搜索，对象 / 数组不再按 repr 发给平台（Issue #2347）。
- `ai_assist` 的 `prompt` 经必填字符串校验且先于本地 AI 配置检查：旧断点缺该键时报输入错误，不再落成 `INTERNAL_ERROR` / `AI_NOT_CONFIGURED`（Issue #2348）。
- 无表头面试线索表的多行首记录参与判重：引号字段含换行时不再每次调用重复追加同一线索（Issue #2349）。
- `crawl` 的平台 job id / 兜底标题先做 UTF-8 安全化：含孤立代理项时不再让 SQLite 绑定抛 `UnicodeEncodeError` 并被吞成 ok:true 的 `stopped`，任务不会永久卡住（Issue #2350）。
- `crawl` 的详情阶段把 `CacheBusyError` / `CacheRecordInvalid` 与列表路径同口径重抛：本地缓存锁冲突不再降级成单条 `detail-error` 后把整轮报成 `completed`（Issue #2351）。
- 请求延迟与预算的数值校验对超过 float 上限的整数抛 `ValueError` 而不是 `OverflowError`，`validate_delay` / `RequestBudget` / `cooldown` 与文档承诺一致（Issue #2352）。
- 本地 JSON 读取对含 NUL 的路径归一为带 `filename` 的 `OSError`，读路径与写路径同口径，不再泄露未分类 `ValueError`（Issue #2353）。
- `RequestBudget` 构造与 `for_data_dir` 的目录解析改用 `_resolve_budget_path`：符号链接环归一为 `OSError(EINVAL)`，不再裸抛 `RuntimeError`（Issue #2354）。
- `resume export --format json` 对深嵌套内容归成本地状态错误：不再抛裸 `RecursionError` 让 stdout 为空，命令层仍有信封与修复指引（Issue #2356）。
- 简历名含孤立代理项叠加 portable 清洗字符时不再打断 `list` / `delete` / `edit`：`_path_for` 把无法编码的 legacy 候选视为不可用并回退，列表回退到文件名（Issue #2357）。
- `resume import` 的深嵌套保存失败按输入错误上报（「嵌套层级过深」），不再误报成本地磁盘故障（Issue #2358）。
- `ai local import` 的暂存名改用固定短前缀：合法长文件名的导入不再必然 `ENAMETOOLONG`（Issue #2359）。
- `ai config --base-url` 的非法与超长分支不再回显 query / userinfo 中的凭据，与 query 分支同口径只展示前缀（Issue #2360）。
- `ai local import` 拒绝末段为 `.` / `..` 的源路径：不再把数据目录当目标并在失败前留下空目录（Issue #2361）。
- 自动化决策的面试时间在同一消息内也遵循「最新陈述优先」：「明天下午3点不行，改到周五下午4点」不再把作废时间写进面试线索（Issue #2364）。
- 自动化决策对 `prior` 时间戳只认 ISO 时间字符串：state.json 字段漂移成 `"false"` / `0` 时不再反转「已发送问卷 / 已交换」判定并误跳下一步（Issue #2365）。
- 本地 AI 回复草稿在解析阶段拒绝不可 UTF-8 编码的孤立代理项：不再出现消息已发送、写事件却因 `UnicodeEncodeError` 中止整轮且 action-log 缺记录（Issue #2366）。
- 自动化决策的已交换判定只匹配明确完成措辞：「还没交换微信呢」「可以交换微信吗」不再被当成已交换并跳过候选人（Issue #2367）。
- wizard 空结果面板把恢复命令整段按字面量转义：`--data-dir` 路径里的 `[/bold]` 不再触发 `MarkupError` 打断面板（Issue #2368）。
- wizard 等待面板对类型漂移的 `operator_actions` 按缺失处理：数字不再中断渲染，字符串不再被逐字符拆成多条假指引（Issue #2369）。
- wizard 状态表对类型漂移的 `artifacts` 按缺失处理：数字不再崩溃，单个路径字符串不再显示成十余条假产物（Issue #2370）。
- wizard 采集产物探测改用 `lstat` 并吞掉权限 / 非法路径错误：3.11–3.14 行为一致，状态面板不再被 `PermissionError` 打断（Issue #2371）。
- 锁文件的含 NUL 路径在 `mkdir` / `lstat` 处归一为带 `filename` 的 `OSError(EINVAL)`，不再泄露未分类 `ValueError`（Issue #2373）。
- 私有 salt 的目录准备把非法路径归一为带 `filename` 的 `OSError`；`tighten_private_file` / `tighten_private_directory` 对非法路径静默跳过（Issue #2374）。
- `prepare_output_path` 的父目录创建对含 NUL 的路径归一为带 `filename` 的 `OSError`，与 `inspect_output_path` 同口径（Issue #2375）。
- 候选人动作的「可证明未发出」判定在异常自身无法转成文本时 fail-open：不再用二次异常替换原始错误，也不放宽 pending 预约释放（Issue #2376）。

## [3.4.147] - 2026-11-21

### Fixed
- 候选人写动作的预约释放在 SQLite 写失败时不再替换原始异常：可证明未发出的错误保留原错误码并附注释放失败，平台明确拒绝则报 `LOCAL_STATE_WRITE_FAILED`，不再让下一次调用误判为结果未知（Issue #2315）。
- 候选人写动作把 `NETWORK_ERROR`（HTTP 5xx / 超时 / 连接中断）纳入结果未知集合，5xx 后保留 pending，避免 Agent 重发导致重复打招呼 / 投递（Issue #2316）。
- `boss doctor` 的浏览器缓存探测统一兜底 `OSError` / `ValueError` / `RuntimeError`：cwd 已删除、HOME 无法解析或路径含 NUL 时不再打穿整份自检（Issue #2317）。
- 浏览器内核「已安装」改为要求带 `INSTALLATION_COMPLETE` 标记的目录，悬空链接 / 空目录 / 同名文件不再误报；默认缓存根按驱动平台优先级只探测一个，`browsers.json` 的 `revision` 形状漂移按不可读处理（Issue #2318）。
- `safe_text` 同时捕获 `RecursionError`：深嵌套 `jobType` 按字段缺失处理，不再把整条职位命令落成 `INTERNAL_ERROR`（Issue #2319）。
- wizard 的 `candidate_greet` / `candidate_apply` 在预约前拒绝纯空白 `security_id` / `job_id`，按 `INVALID_PARAM` 提示修正输入，不再误报「上一次预约仍在」（Issue #2320）。
- 聊天导出入口归一非对象 `diff_result`，Markdown diff 摘要按单元格口径转义换行，JSON 分支清洗孤立代理字符，避免 `AttributeError` / 报告行注入 / 严格 UTF-8 写盘失败（Issue #2321）。
- `crawl` 的执行器在逐页 / 最终导出后重读断点：并发 `clean --privacy` 已删除任务行时撤销刚写出的 `jobs.json/csv/xlsx`，不再复活已清理的采集数据（Issue #2323）。
- `crawl` 的 Drission 通道不再把预算 / 断点回调抛出的 `CacheBusyError` / `CacheRecordInvalid` / SQLite 故障吞成通用传输错误；`_with_retry` 对这几类不重试，避免虚增请求预算与重复占用节流槽（Issue #2324）。
- 面试线索 CSV 的表头改为原子写入；只有首行确为表头才跳过判重，0 字节文件补写表头，追加前末缺换行先补 `\n`——崩溃窗口或外部创建的文件不再让同一线索重复追加或与残表头粘连（Issue #2331）。
- automation 的 JSON / JSONL 写入器把 `TypeError`（不可序列化对象 / 混合类型键）与 `ValueError` 同口径归为 `AutomationStateError`，不再裸穿成误导性的 `INTERNAL_ERROR`（Issue #2332）。
- `JobItem.from_api` 的 `job_url` 与 `job_id` 同口径：`encryptJobId` 漂移成容器 / 非有限数时不再拼出带 Python repr 的伪链接（Issue #2326）。
- `JobItem` / `JobDetail` 的 `raw_job_type` 只保留标量，容器与非有限数按缺失处理，信封与本地索引缓存不再出现与 `int | str | None` 契约不符的值（Issue #2327）。
- wizard 的 `job_detail` 成功分支逐字段归一：字段级漂移不再把嵌套对象 / null / 布尔写进 `workflow_steps.result` 与信封，与 `detail` / `show` 同口径（Issue #2328）。

## [3.4.146] - 2026-11-21

### Fixed
- `boss login` 的确定性内部异常（`TypeError` / `KeyError` 等）与本地 IO 失败按 `INTERNAL_ERROR` / `LOCAL_IO_ERROR` 输出，不再被文本启发式兜成可重试的 `NETWORK_ERROR`；内部错误 message 只含类型名，不回显可能夹带凭据的异常文本（Issue #2307）。
- `--curl-file` 导入在平台限流（`RATE_LIMITED`）、代理环境变量非法（`INVALID_PARAM`）与本地 IO 失败时保留专用错误契约，不再一律报成可重试的「导入失败」（Issue #2308）。
- 登录错误文本启发式按词边界匹配状态码与关键词：端口 4030 / 4290、`asterisk` 与路径里的 timeout 不再误判为风控 / 限流 / 超时；非 typed 的限流文本与 `PlatformRateLimitError` 同口径报 `RATE_LIMITED`；启发式风控分支只输出登记表文案，不回显原始异常文本（Issue #2309）。
- `--curl-file` 有界读取（上限 1 MiB）并只接受普通文件或标准输入，`/dev/zero` 等无界输入不再耗尽内存；cURL 文本容忍 UTF-8 BOM 与大小写 scheme，ANSI-C 引号（`$'…'`）给出明确提示（Issue #2310）。
- 登录互斥选项在任何本地副作用之前校验；`AuthManager` 构造失败按 `LOCAL_IO_ERROR` 输出；本地 IO 文案改为中性表述，不再一律断言「登录成功但保存失败」（Issue #2311）。
- `PlatformRiskError` 的 `details` 非对象时归一为空证据，风控信封不再因 `dict(details)` 失败退化为 `INTERNAL_ERROR`（Issue #2312）。
- Cookie 提取失败的 stderr 诊断经统一文本脱敏后再输出（Issue #2313）。

## [3.4.145] - 2026-11-21

### Fixed
- 登录收尾的 stoken 提取与身份头采集同样会驱动页面事件循环：这期间锁存的迟到风控响应（`/wapi/` code 36/37）或主页面跳转到安全验证页，在 `login_via_browser` / `login_via_cdp` 返回成功凭证前复查并立即终止；复用会话探测的 evaluate 失败分支也先复查页面再按 `unverified` 收口，被风控的会话不再被当作「登录成功」落盘（Issue #2301）。
- 身份头探针页的防御分支（`ctx.new_page()` 意外返回用户页签）先摘掉引用再返回，`finally` 不再把用户的页签关掉（Issue #2302）。
- `boss login --timeout` 限定为 1–3600 秒，超界值在解析阶段按 `INVALID_PARAM` 拒绝；Python API 的 `AuthManager.login` 在碰浏览器之前同样校验，超大整数不再让 `time.time() + timeout` 抛 `OverflowError` 被报成可重试的 `NETWORK_ERROR`，也不会让 CDP 轮询变成无上界等待（Issue #2303）。
- `login_via_browser` 在首页已加载但 stoken 提取失败（evaluate 返回空串）时回退 cookie jar 的 `__zp_stoken__`，与 `refresh_stoken` 同口径，不再把空 stoken 当登录成功保存（Issue #2304）。
- wizard 写前标记落盘失败（步骤记录缺失 / 损坏）时的 `CACHE_RECORD_INVALID` 带 `details.request_sent=false`：写请求确实没有发出，候选人写动作的 pending 预约会被释放，不再永久挡住该职位；`replay_safe=false` 继续阻止 `--resume` 重放（Issue #2299）。
- `greet` / `apply` / `batch-greet` 在请求派发之前失败（未登录 `AUTH_REQUIRED`、`TOKEN_REFRESH_FAILED`、代理配置非法、浏览器首页导航超时、浏览器内核缺失、本地风险锁）后释放预约：修好登录 / 代理后同一职位可以重新打招呼或投递，不再被 pending 预约永久挡成 `ACTION_UNCONFIRMED`；本地风险锁错误显式带 `request_sent=false`（Issue #2296）。
- 单次 `greet` / `apply` 在无法证明请求未发出的异常（超时、浏览器驱动中断）时输出 `ACTION_UNCONFIRMED`（`outcome_unknown`、`replay_safe=false`、不可恢复，`details.cause` 保留错误码或类型名），与保留的预约一致；不再先报可重试的 `NETWORK_ERROR`、让 Agent 重试时撞上 pending 预约（Issue #2297）。
- wizard 的 `candidate_greet` / `candidate_apply` 在派发前失败时撤销发送前落盘的「结果未知」写标记，`--resume` 可以重试一次从未发出的写请求；无法证明未发出的异常保持 fail-closed（Issue #2298）。

## [3.4.144] - 2026-11-20

### Added
- `boss chatmsg --friend-uid UID <security_id>`：调用方已有稳定 uid 与本次请求的 security_id 时直接读取消息，跳过从第 1 页开始的沟通列表扫描；翻页提示保留直读形式，security_id 过期时按平台错误返回，不隐式退回全列表查找。MCP `boss_chatmsg` 同步暴露 `friend_uid`（Issue #2277）。

### Fixed
- `boss show` 的元信息与职位改为一次读取索引缓存：并发的 `search` / `recommend` 覆盖缓存时，不再出现「职位来自新快照、`index_snapshot` 来自旧快照」或「共 N 条」与「编号 N 超出范围」自相矛盾的成功信封（Issue #2291）。
- `search` / `recommend` 信封里的 `pagination.index_snapshot` 取本次写入的快照身份，不再在保存后回读可变文件：并发搜索不会再让本次结果挂上另一次搜索的来源与保存时间（Issue #2292）。
- 索引缓存条目的 `security_id` / `job_id` / `lid` 读取时按与元信息同口径校验可编码性：孤立代理字符按缺失处理，`boss show` 在发出平台请求之前按 `INVALID_PARAM` 提示重新搜索，不再带着坏值请求、也不再把损坏缓存兜成带磁盘权限指引的 `LOCAL_IO_ERROR`（Issue #2293）。
- 出口探测（`BOSS_EGRESS_PROBE_URL`）的客户端构造失败（代理环境变量畸形、缺少 socks 依赖）同样返回 `unknown`，不再逃出探测契约变成可重试的 `NETWORK_ERROR`（Issue #2278）。
- 时区必须先是在系统 tzdata 里真实存在的 IANA 名称才参与地区推断：`Asia/ShanghaiBogus` 这类伪装成已知前缀的名称不再被当作与出口一致（Issue #2279）。
- `Accept-Language` 的 3 位数字地区子标签（`es-419` 拉美、`en-001` 世界，UN M.49）不再与 2 位国家码直接比较：正常组合不再被判成出口矛盾而阻断启动，2 位字母地区的矛盾判定不变（Issue #2280）。
- 代理 host 里的反斜杠、`|` 与百分号编码在解析阶段按 `INVALID_PARAM` 拒绝：Chromium 把 `\` 当路径分隔符，放行会让实际出口与校验值不一致（Issue #2281）。
- stoken 注入不再假定 `params` 一定是可变映射：缺省时新建 dict，键值对序列追加且不产生重复键，已编码的字符串 / 字节原样交给 httpx，不再在请求循环里抛 `TypeError`（Issue #2282）。
- 平台响应的 `message` / `zpData` 等字段只在是字符串时进入错误文案：结构化载荷的容器 repr 不再连同其中字段一起进入 `error.message`（Issue #2283）。
- 风控页判定对缺失 / 类型漂移的 `url` / `content-type` / `body` 按空串处理，登录与请求链路不再因为探测字段为 `None` 而崩成未分类异常（Issue #2284）。
- AI 服务的 `httpx.InvalidURL` 按本地参数错误分类：`INVALID_PARAM`、不可恢复、指引重新设置 `--base-url` 并脱敏，不再报成可重试的网络故障（Issue #2285）。
- `-o` / `--output` 的家目录写法在系统里查不到该用户（`~某用户`）时按 `INVALID_PARAM` 拒绝，不再把字面量当路径在当前目录建出一个 `~某用户` 目录并把产物写进去；`resume import` 的同一情形改报「家目录写法无法展开」而不是「文件不存在」；wizard 导出的父目录不可写映射补上 `LOCAL_IO_ERROR`，不再因契约表缺项兜成 `INTERNAL_ERROR`（Issue #2289）。

## [3.4.143] - 2026-11-20

### Added
- `boss chat --raw`：普通输出为每条记录附加 `raw`，保存该联系人的原始平台对象（`relationType`、`lastMessageInfo`、`unreadMsgCount` 等键名与编码原样保留），摘要字段照旧输出；`--from` / `--days` 筛选与分页计数不变，`hints.next_actions` 的翻页命令保留 `--raw`。与 `--export` 合用时导出文件、快照与 diff 仍只含摘要记录（Issue #2250）。

### Fixed
- `hr download-resume` 把请求预算库损坏 / 被同名目录占位等本地确定性故障交回统一分类：报不可重试的 `LOCAL_IO_ERROR` 并给出删除 `request-budget.sqlite3` 的指引，不再兜成可重试的 `NETWORK_ERROR`（Issue #2271）。
- `history_self_message_state` 按平台原始页长度判断会话是否拿全：满页中含形状漂移条目时不再把「拿不准是否发过消息」误判成「确定没发过」，仍返回 `None`（Issue #2272）。
- `crawl` 导出被 Ctrl-C / SIGTERM 打断（`KeyboardInterrupt` / `SystemExit`）时同样回滚已替换的产物并清理 `.jobs.*` 临时与备份文件，三份产物不再落成不同批次、旧采集数据不再长期残留在输出目录；回滚项在替换调用之前登记，中断落在替换生效之后同样能还原；首次导出没有旧产物时只删除本次确实写入的新文件，恢复到导出前的状态，替换没有生效时不动目标位置上的既有内容（Issue #2274）。

## [3.4.142] - 2026-11-20

### Fixed
- 预设与 watch 名称含孤立代理字符（非法 UTF-8 字节经 surrogateescape、JSON 的 `"\udcff"` 转义）时，在 `preset add` / `preset remove` / `watch add` / `watch remove` / `watch run` / `search --preset` 入口按 `INVALID_PARAM` 拒绝，不再一路走到 SQLite 绑定参数才抛 `UnicodeEncodeError`、被兜成不可恢复且指引磁盘权限的 `LOCAL_IO_ERROR`（Issue #2268）。
- 离线冒烟脚本的环境变量与退出码收口：`BOSS_SMOKE_TIMEOUT` 空值回到默认 30，非正整数以明确文案退出 2；`BOSS_SMOKE_DRY_RUN` 大小写不敏感并接受 `on` / `y`，无法识别的取值拒绝执行而不是切到联网；退出码改为全部 `pass` / `dry_run` 才为 0；子进程固定按 UTF-8 交换，解码失败、超长整数与 `RecursionError` 归入 `contract_error` 并附带抹掉 `security_id` 的 stderr 尾部（Issue #2261）。
- 安装探针展开 MCP 阶段的 `ExceptionGroup`：`ProbeError` 的消息（如工具清单漂移）不再被压成 `"ExceptionGroup"`，普通异常报告「类型: 消息」，CLI 阶段失败附带 stderr 尾部与超时命令名（Issue #2262）。
- 发布校验容忍子进程的非 UTF-8 诊断输出，不再把编码差异记成 `UnicodeDecodeError` 失败；JSON 报告在非 UTF-8 stdout 下退回 ASCII 转义，失败项记录「类型: 消息」而不是只有类名（Issue #2263）。
- 便携包构建在删除旧包之前校验外部 wheel：必须是存在的普通文件、`.whl`、版本前缀与 `[project]` 版本一致，不符时保留上一份可用包；打包改为先写临时目录再替换，`uv build` 产物按当前版本挑选；项目版本改用 `tomllib` 只读 `[project]` 表（Issue #2264）。
- 响应 Cookie 合并按名称替换而不是追加，并保留服务端的 `Domain` / `Path`：`httpx.Client` 已按域写入的 Cookie 不再被补一份无作用域副本，`cookies.get` 不再因同名条目抛 `CookieConflict`；写请求发出后不会因合并失败被报成结果未知，`hr location` / `hr greet` 也不会在发送前崩溃并把未发出的请求锁成结果未知；服务端删除的 Cookie 不会被旧副本复活（Issue #2266）。
- 外部 eval 在评分前校验 runner 输出结构：`with_mcp` / `baseline` 缺失、不是对象，或 `tool_calls` / `envelopes` 缺失、不是对象数组时，该场景记为 `contract_error`，不再把缺失的基线算作「基线失败」而判定通过，也不再因类型错误、超长整数或非 UTF-8 输出中止整轮；其余场景照常评分并写入报告。场景夹具加载走同一校验，默认 runner 固定用 UTF-8 交换数据（Issue #2258）。

## [3.4.141] - 2026-11-20

### Fixed
- Rich 外部文本转义改为自包含：相邻字段里的悬空方括号不再跨值组成标签，结尾反斜杠与 `\[` 按字面保留，职位详情不再因招聘者名称等字段组合而丢文本、泄漏样式或抛 `MarkupError`；统计恢复指引同走该入口（Issue #2251）。
- TTY 渲染出口统一剥离非 Rich 控制段里的 C0 / C1 控制字符，覆盖向导的 `Text` 正文与表格；两种菜单驱动统一清洗外部标签与说明、菜单标题单行化，进度计数按字面转义，技能 / 标签字段按列表归一，形状漂移不再让向导崩溃（Issue #2252）。
- 清除自定义 AI 端点后恢复服务商默认地址：`ai config --clear-base-url` 写回 `null`，服务构造同时兼容旧版本写入的空串，不再把官方清除命令产生的配置报成 `CONFIG_INVALID`（Issue #2253）。
- AI 端点拒绝 query / fragment、不可见格式字符与超长地址；请求构造期的代理配置错误及 UTF-8 编码错误收口成确定性的 `INVALID_PARAM` 并给出修复指引，网络异常文本同步脱敏；自动回复读取损坏端点时降级模板话术，不中止整轮自动化（Issue #2254）。
- 本地模型导入在复制前检查目标目录归属：不同模型名折叠到同一个目录（含大小写冲突）时拒绝导入，保留已有权重与登记表；`import.lock` 与登记表文件名同列保留名，同名重新导入仍可更新自己的权重（Issue #2255）。

## [3.4.140] - 2026-11-20

### Fixed
- 自由文本脱敏识别 camelCase 凭据键：`accessToken` / `refreshToken` / `clientSecret` / `sessionId` 等键名中关键字前是小写字母，此前的「非字母数字」边界不成立，凭据会明文进入 `error.message` / `recovery_action` 与 MCP 子进程失败信息；新边界只在小写→大写转折处成立，`token_expires_in` / `max_tokens` 等配置名不被误伤（Issue #2231）。
- 结构化与文本脱敏补上 `passwd` 凭据键：此前只认 `password`，`{"passwd": "..."}` 与 `passwd=...` 都原样输出（Issue #2232）。
- 无请求预算的客户端也把 `Retry-After` 截断到本地冷却上限（1 小时）：Python API / MCP 子进程拿到 `429 + Retry-After: 86400`（或 HTTP-date 的远未来时间）时不再被 `cancellable_sleep` 挂起数小时到数天（Issue #2234）。
- `probe_cdp` 把 `httpx.InvalidURL` 当作探测不可用：Python API 传畸形 `cdp-url` 时不再让裸异常打断登录降级链，继续按契约降级到 patchright 扫码（Issue #2235）。
- Cookie 校验跟随重定向后按最终 URL / 正文做风控判定：平台以 302 送到 `403.html` / verify / security 页时不再被当成「Cookie 失效」，正确抛出风控异常并写入本地风险锁（Issue #2236）。
- Cookie 校验把 429 与业务码 9（整数 / 字符串同口径）识别为限流而非登录态失效：`login` 不再报「Cookie 已失效」并降级 CDP / 扫码，`import_curl` 不再报「未通过验证」，统一报可重试的 `RATE_LIMITED` 并给出等待后重试的指引（Issue #2237）。
- TTY 渲染统一剥离平台文本里的终端控制字符：`escape_markup` 在 Rich markup 转义前去掉 ESC / OSC / CSI 等 C0（保留 `\t` / `\n`）与 C1 控制字符，职位名、公司名、聊天内容与 AI 输出不再能把原始转义序列写进终端（改写标题、伪造可点击链接、清屏）；`display.py` 里直接 `escape(...)` 的外部文本调用点一并改走该入口（Issue #2245）。
- 错误行的错误码按字面渲染：TTY 分支改用 `rich.text.Text` 组装整行，未注册错误码含未配对方括号时不再抛 `MarkupError` 把错误信息与退出码一起吞掉（Issue #2246）。
- TTY 列表 / 详情渲染入口容忍形状漂移：`render_job_table` / `render_simple_list` 跳过非对象条目并按过滤后条目计数，`render_job_detail` 把字符串 `skills` 当单条、其余非列表按空处理，损坏缓存或平台字段变化不再兜成裸 `TypeError`（Issue #2247）。
- 前台恢复在取得执行权后、慢预检之前先把状态落成 `running`：预检（依赖探测、读取登录态）期间到达的 `crawl stop` 不再因旧终态被拒、停止请求丢失，恢复会立即按停止结算；预检失败时原状态 / 错误 / 时间戳原样还原，预检窗口内新到达的 stop 及其时间戳保留（Issue #2240）。
- `clean --privacy` 与 completed 快速路径并发时不再重建已清理的采集产物：快速路径导出前后重读断点，行已消失即放弃写出并删除刚重建的文件；清理改为先删数据库行、再删产物文件，两者共用同一时序契约（Issue #2241）。
- `crawl resume --background` 的父进程与执行器同口径校验 Hook 目录与断点设置：hook 目录损坏、关键词为空白等确定性本地失败在返回 `queued` 之前报出（`LOCAL_IO_ERROR`），不再先报成功再让 worker 静默失败（Issue #2242）。
- 后台恢复交接窗口内的显式 `--pages` / `--with-detail` 与刚到达的停止标记不再被静默忽略：无法生效时返回可重试的 `CRAWL_BUSY`，而不是假装参数已应用（Issue #2243）。

## [3.4.139] - 2026-11-20

### Fixed
- 自动化决策的 `has_exchange` 只看候选人一侧文本：我方问卷 / 追问话术里出现「交换微信」「已交换」时不再判定「已交换」并把候选人静默跳过，与升级检测的 #1945 同口径；我方动作仍以 `exchange_contact_at` 为准（Issue #2227）。
- 面试时间按最近消息优先提取：候选人改期（「明天下午 3 点」→「改到后天上午 10 点」）不再取到已作废的旧时间，历史里的时间也不再每轮命中；有方向信息时以 `ordered_messages` 顺序为准（Issue #2228）。
- 转录没有任何我方消息锚点时不再把触发问卷的旧消息当成候选人回复：自动追问与交换联系方式不再由一条历史消息触发，保守按等待回复处理（Issue #2229）。
- 缓存层的本地 JSON 列统一按 `ensure_ascii=True` 写入、搜索键按 `backslashreplace` 编码：平台 JSON 里的孤立代理字符（`\ud800`）不再让 `put_crawl_job` 抛 `UnicodeEncodeError` 把采集任务永久卡在 `stopped`，`search` / `shortlist` 也不再以 `LOCAL_IO_ERROR` 失败；读回值不变（Issue #2223）。
- `shortlist list` 逐行容错：单条记录字段写坏（如 `created_at` 是 BLOB）不再让整个候选池不可见，坏行跳过并通过 `hints.corrupt` 与操作指引指名删除重建；新增 `list_shortlist_with_corrupt`，与 watch / preset 的 corrupt 名单同口径（Issue #2224）。
- 残留事务不再被误判为缓存库损坏：`BEGIN IMMEDIATE` 前先回滚连接上未提交的隐式事务；`cannot start a transaction within a transaction` 按写入拒绝上报，不再引导用户备份并删除完好的缓存库（Issue #2225）。
- 本地 JSON 序列化边界把 `TypeError` 也归成本地 IO 错误：写入不可序列化的值（元组键、`bytes`、`set`）时不再让裸 `TypeError` 穿出命令层（stdout 无信封、stderr 裸 traceback），按 `LOCAL_IO_ERROR` 报出且保留原文件与既有原子写语义（Issue #2220）。
- 聊天快照服务边界过滤非对象条目：平台列表页混入 `null` / 字符串 / 数字时不再在 `contact_identity` 上抛 `AttributeError` 让整次快照与导出失败；丢弃过条目按不完整处理（不推断「已消失」、不写完整标记），与 `chat_export` 的渲染边界同口径（Issue #2221）。
- 写请求只在「请求可能已发出」时落成 `ACTION_UNCONFIRMED`：`ConnectError` / `ConnectTimeout` / `PoolTimeout` / `ProxyError` / `UnsupportedProtocol` / `LocalProtocolError` 确定发生在连接建立或请求构造阶段，断网、DNS 失败或代理不可达时不再被报成不可恢复的「结果未知、禁止重试」，改为原样上抛、由命令出口按可重试的 `NETWORK_ERROR` 收口（Issue #2216）。
- `job_card()` 在 `httpx-only` 策略下不再回退不存在的浏览器通道：httpx 失败时原样上抛真实错误，不再用 `BROWSER_DISABLED`「该能力需要页面通道」覆盖 5xx / 超时 / 解析失败的原因（Issue #2217）。
- stoken 过期判断与其它业务码消费点同口径，字符串 `"37"` 与整数 `37` 一样触发刷新；平台码序列化类型漂移时不再漏刷新、把过期响应当普通结果返回（Issue #2218）。
- 非 UTF-8 stdout（中文 Windows 的 cp936 重定向 / 自定义 `PYTHONIOENCODING`）下报表与信封不再因 emoji 编码失败：信封在无法按 stdout 编码写出时回退为 `\uXXXX` 转义的 ASCII（仍是合法 JSON，值不变）；`stats --format html` / `digest --format md` / `export --format html` 的直出分支重定向时写 UTF-8 字节、终端时按实际编码归一；报表模板去掉 emoji（`📊` / `⚠️` 改为纯文本），不再误报「确认目标路径可写」的 `LOCAL_IO_ERROR`（Issue #2213）。
- `render_digest_markdown` 对 `new_matches` / `follow_ups` / `interviews` 里的非对象条目按缺失过滤，与 #2162 的导出服务边界同口径：库内直接调用或未来调用方传入 `null` / 字符串 / 数字时不再抛 `AttributeError`，计数与展示只包含对象条目（Issue #2214）。
- 采集执行中断点被 `clean --privacy` 删除后按「已停止」fail-closed：执行器不再把「行不存在」当成「没有 stop」，后续页的职位行不会复活已清理的 `crawl_jobs`、导出目录不会被重建、信封也不再报 `completed` 指向不存在的 run；执行器写职位行改为以 `crawl_runs` 行存在为前提的原子插入（`require_existing_run`），最后一页之后导出前再确认一次断点（Issue #2208）。
- 前台 `crawl resume` 在预检之前清除旧停止标记、失败时还原：预检（依赖探测、登录态读取）期间到达的新 `crawl stop` 不再被预检后的清理当成上一轮残留清掉，采集立即按停止结算；与后台恢复路径同构（Issue #2209）。
- `job_desc_cache.description` 的 BLOB 存储类漂移在读取边界按 UTF-8 解码，解码失败或其它类型按「未命中」处理并重新取详情：漂移值不再进入 payload 让 `put_crawl_job` 的 `json.dumps` 抛 `TypeError`、把任务永久停在 `stopped` 却报 `ok:true`；`search_filters` / `ai` / `agent` 的详情缓存消费点同口径（Issue #2210）。
- 自动化队列认领的会话不再交给扫描路径：本轮队列已尝试过（含被平台拒绝后退回 `pending`）以及仍有 `pending` / `executing` 队列行的候选人，扫描路径不会在同一轮用模板话术顶替、也不会跨轮把同一动作再发一次；同一动作已在会话中完成时（`questionnaire_sent_at` 等）遗留队列行标记为已执行而不是重发（Issue #2211）。
- 登录态校验在任何请求之前先解析代理配置：`HTTPS_PROXY` / `ALL_PROXY` 是 `socks9://`、端口非数字这类坏值时，`login --cookie` 不再把 `ProxyConfigurationError` 当成 Cookie 失效、清掉刚保存的登录态并回退到扫码；按 `INVALID_PARAM` 报出代理配置错误（Issue #2203）。
- AI 密钥派生与登录态侧同口径：`BOSS_AGENT_MACHINE_ID` 按 surrogateescape 解码出孤立代理字符时，AI 加密密钥改用 `surrogatepass` 编码，同一机器 ID 不再在 auth 侧可用、AI 侧抛 `UnicodeEncodeError`（Issue #2204）。
- `ai_base_url` 的读取与写入走同一套校验：配置里缺 scheme 或含空白 / 控制字符的地址（`\t`、空格、DEL）按带修复指引的 `CONFIG_INVALID` 报本地配置错误（与 #1636 的持久化配置契约一致），不再让每条 AI 命令由 httpx 抛 `InvalidURL` / `UnsupportedProtocol` 并被兜成可重试的 `AI_API_ERROR`；`ai local status` 等无认证装饰器的命令路径也由 CLI 兜底出信封（Issue #2205）。
- 收紧登录态与 AI 密钥文件权限时不跟随符号链接：`session.enc` / `salt` / `api_key.enc` 是指向别处的符号链接时不再 `chmod` 目标文件（此前可能改掉用户其它文件的权限），只对普通文件收紧到 0600（Issue #2206）。

## [3.4.138] - 2026-11-20

### Fixed
- `salt` 创建不再硬依赖硬链接：exFAT / 部分 SMB / FUSE 等不支持硬链接的文件系统上回退为独占创建，首次登录不再在保存登录态时确定性失败；回退的半写窗口由读取端有界等待兜住，写入失败会清理半写文件（Issue #2200）。
- 空 `wizard --input-json` 的恢复指引改为提示传入符合向导目录要求的 JSON 对象并查阅 `boss schema`，不再让调用方去补一个创建任务用不到的 `run_id`；其余空选择器仍提示补齐任务 ID（Issue #2190）。3.4.137 的构建不含此修复，条目从已发布段落移回。
- 采集断点的 `updated_at` 是有限但远在未来的值时（时钟快进后校正、手工写坏的库），queued 任务不再被永远当成「刚排队」：后台恢复探测执行锁并拉起 worker，`crawl status` 给出 `crawl resume --background` 提示；轻微时钟偏斜仍按刚排队处理（Issue #2193）。
- `crawl_runs.output_dir` 含控制字符时断点读取边界按 `CACHE_RECORD_INVALID` fail-closed，`crawl status` 与 `crawl resume` 对同一断点同口径，不再把路径字符错误报成 `INVALID_PARAM`（Issue #2194）。
- 后台恢复把状态落成 queued 失败时还原已清除的旧停止标记，保持「失败的调用不改动任务」；不再出现 stop 已清、worker 未起、状态未变的半写结果（Issue #2195）。
- `-o` 输出路径预检增加父目录可写性检查：只读目录下的合法路径不再先消耗一次平台请求、更新当日聊天快照才报 `PermissionError`，改为在任何请求与快照写入之前返回 `LOCAL_IO_ERROR`（Issue #2197）。
- CSV/Markdown 清洗函数的非字符串兜底改为经 `safe_text` 转成字符串并走同一套清洗链：漂移值同样执行公式前缀与 Tab/回车过滤，超大整数不再抛 `ValueError`（Issue #2198）。

## [3.4.137] - 2026-11-20

### Fixed
- wizard 断点里「状态是 pending、已落盘结果却是 completed」的矛盾不再被当成待执行步骤：恢复时按 `CACHE_RECORD_INVALID` 拒绝，可能已生效的写步骤不会被重放（Issue #2183）。
- wizard 自定义动作返回非终态结果并声明写请求在途时，中断后留下的 `pending` 步骤与 `running` 同口径结算为「结果未知」，`--resume` 不再重发可能已生效的写请求（Issue #2184）。
- wizard 恢复前核对断点的请求步骤与已落盘步骤行：`params.requested_steps` 漂移成子集时第一次恢复就拒绝，不再漏跑步骤却把 run 写成 `completed`（Issue #2185）。
- `wizard --input-json` 的输入结构校验增加嵌套深度上限：深层嵌套不再让 `RecursionError` 逃出信封边界，按 `INVALID_PARAM` 返回确定诊断（Issue #2186）。
- 失败 workflow 的顶层 `error` 漂移为空对象时，出口回退到断点步骤里已校验的错误：结果未知的写步骤不再被报成可重试的 `NETWORK_ERROR`，核对指引得以保留（Issue #2187）。
- wizard 计划比较按 JSON 值类型进行：直接调用 `WorkflowRunner` 时 `False` 与 `0` 不再被当成同一份输入，同时保留 NaN 按同值处理的兼容（Issue #2188）。
- 自动化队列的整文件回写保留读取路径会跳过的原始行：`pending-actions.jsonl` / `human-review-queue.jsonl` 里坏编码、坏 JSON 或非 object 的行不再在下一次 `write_pending` / `write_reviews` 时被静默删除（一行可能是一条已批准但因崩溃截断的动作记录，删除后既不执行也无痕迹）；旧文件读不出来时按 `AUTOMATION_STATE_INVALID` 停止回写；追加队列记录补 `flush` + `fsync`，缩小断电只落下半行的窗口（Issue #2172）。
- `state.json` 的 `safety.consecutive_errors` 读取时要求非负整数：外部编辑写入的负值不再让连续失败熔断需要更多次失败才打开，按 `AUTOMATION_STATE_INVALID` 报状态损坏并提示修正方式（Issue #2173）。
- MCP 入口落实 schema 里声明的条件约束：`boss_config` / `boss_hr_jobs` 的未知 `action`（如 `--`、`--help`）在映射层按 `INVALID_PARAM` 拒绝，不再被 CLI 当成默认 `list` 或帮助选项；`config set` 缺 `key` 时不再让 `value` 顶到 KEY 位置、报错误的「缺少 VALUE」；`crawl_shortlist` 同时给 `selectors` 与 `all` 时按互斥约束拒绝；JSON 数字 `1.0`（小数部分为零）按 JSON Schema 语义归一成整数，不再被入口误判为类型错误（Issue #2175）。
- MCP 映射的三个 AI 工具把显式空 `resume` 原样下发，让 CLI 的 `INVALID_PARAM`「简历名称不能为空」诊断生效，不再静默生成无简历参考的结果；子进程超时估算只看 `--` 之前的选项，关键词恰好以 `--dry-run` / `--count=1` 开头时不再把长任务预算错误缩短（Issue #2176）。
- MCP 的 schema 与只读分类对齐：`search.active` 的枚举补空字符串（清除 preset 保存的活跃度筛选）、`shortlist_annotate` 的标签数组要求至少一项；`dry_run` 调用与 `config list/get` 按只读回报中断恢复，不再要求核对不存在的平台写入（Issue #2177）。
- 聊天快照会把混入非对象或缺失可用身份的沟通列表页视为不完整，并保留当天已观察到的联系人。历史快照过滤过条目时不推断联系人新增（Issue #2179）。
- 聊天快照的负未读数按 0 比较，历史记录从 `-1` 恢复到 `0` 时不再误报新增未读（Issue #2180）。
- 聊天导出在同一把日锁下读取历史快照及其完整性标记，跨日并发时不会把不同版本拼成一份基线（Issue #2181）。

## [3.4.136] - 2026-11-20

### Fixed
- 采集注入 Cookie 的失败原因做值级脱敏：`_failure_reason` 先把所有 seed Cookie 值替换成 `[REDACTED]` 再截断，DrissionPage 的 `ValueError` 把整个 cookie 字典（含 `value`）拼进消息时，`AUTH_REQUIRED` 信封的 `error.message` 不再输出真实 Cookie 值，`SessionSeedResult` 恢复「不保留任何原值」的承诺（Issue #2168）。
- crawl 的三处 `Path.resolve()` 把符号链接环的 `RuntimeError` 按确定性本地失败收口：Python 3.11 / 3.12 上配置目录解析失败不再落成 `ok:true, status=stopped` + 无限 `crawl resume`，`crawl start` 的数据目录解析报 `INVALID_PARAM`，输出目录解析报 `LOCAL_IO_ERROR`，不再被兜成可重试的 `NETWORK_ERROR` 或成功信封（Issue #2169）。
- 导出服务的函数边界补上非对象条目与超大整数标量兜底：`chat --export` 的 MD / CSV / HTML 通道对超过 4300 位的整数标量不再抛 `ValueError`（与 `job_export` / `job_card` 同口径按缺失处理）；chat 导出与职位导出的渲染入口跳过非对象条目，`diff` 列表容忍 `null` / 非列表 / 坏元素；`build_job_from_card` 对非对象入参按空卡片处理。CLI 调用方目前都已过滤，这是公共边界的纵深防御，避免本地形状漂移被兜成可重试的 `NETWORK_ERROR` 或裸 traceback（Issue #2162）。
- `saved_searches` 名称的 BLOB 存储类漂移不再让预设 / watch「看得到却运行或删除不了」：`get_saved_search` / `delete_saved_search` / `save_saved_search` 同时匹配 TEXT 与 BLOB 两种存储类，保存时把同内容的 BLOB 漂移行归一成单一 TEXT 行；`watch run --all` 对运行前已消失的记录输出显式条目，不再静默少跑一个 watch（Issue #2164）。
- `favorites sync` 的去重键改用与展示路径同一套 `_decode_text_column` 解码：候选池 ID 以 BLOB 存储类落库时不再被 `str(bytes)` 误读成 `"b'…'"`，同一职位不会被重复插入也不会误报为新导入（Issue #2165）。
- `handle_auth_errors` 补上缓存异常分支：`CacheRecordInvalid` 报 `CACHE_RECORD_INVALID`（登记表恢复动作），`CacheWriteRejected` 报 `INVALID_PARAM` 并明确库本身完好、无需删除，不再兜成可重试的 `NETWORK_ERROR` 诱导 Agent 反复重试（Issue #2166）。

## [3.4.135] - 2026-11-20

### Fixed
- 预算目录解析把 `Path.resolve()` 的符号链接环错误归一成带 `filename` 的 `OSError`：Python 3.11 / 3.12 上 `--data-dir` / `request_budget_dir` 含符号链接环时，`status` / `doctor` 不再 traceback；登录与请求路径记录风控时，后备目录检查抛出的 `RuntimeError` 不再替换正在传播的 `PlatformRiskError`，风控详情与硬边界都保留（Issue #2155）。
- 读请求的 HTTP 状态错误不再携带查询串：`__zp_stoken__` 由 `add_stoken_to_get_params` 注入每个 GET，httpx 的 `HTTPStatusError` 文本原本包含完整 URL，经 `handle_auth_errors` 兜底会把 stoken 写进 `error.message` 与 Agent 记录；现在按状态码、方法与路径重建异常，类型与 `NETWORK_ERROR` 分类不变，写路径的「结果未确认」契约不变；信封与日志的文本脱敏同时覆盖 `__zp_stoken__` 这个键名，此前通用 `stoken` 规则要求键后直接是分隔符，双下划线让它整体漏过（Issue #2157）。
- 启动期组预算目录校验把 `Path.resolve()` 的符号链接环错误按配置错误拒绝：`request_budget_dir` 指向含符号链接环的路径时，Python 3.11 / 3.12 上不再冲出 traceback（没有信封），而是 `INVALID_PARAM` 并点名成员目录的 `config.json`，与同分支的 ValueError 同口径（Issue #2159）。
- HTTP 客户端在同一次查询中最多自动刷新一次登录态。刷新后仍过期时返回现有的 `TOKEN_REFRESH_FAILED`，限流冷却继续按原有预算处理（Issue #2151）。
- HTTP 写请求响应解码失败时报告 `ACTION_UNCONFIRMED`。调用方需先核对平台结果，`replay_safe=false`（Issue #2152）。

## [3.4.134] - 2026-11-20

### Fixed
- 职位去重只把 strip 后非空的字符串 `job_id` / `encryptJobId` 当 ID：纯空白 `job_id` 不再让不同职位共用 `" "` 去重键而被误合并，也不再遮蔽合法的 `encryptJobId`；带空白的真实 ID 与干净值按同一去重键归一，不再分裂（Issue #2140）。
- `digest --format md` 在渲染出口归一孤立代理字符：平台公司 / 职位文本含被截断的 emoji 等代理字符时，`-o` 不再出现「文件已写出却报 LOCAL_IO_ERROR」，`--json` 的 `data.content` 与 md 直写 stdout 也不再编码失败，转义口径与 `stats --format html` 一致（Issue #2142）。
- login 错误分类的风控判定移到 CDP / `ConnectionError` 启发式之前：Playwright / CDP 异常文案同时含「chrome / cdp」与风控词时不再报成可重试的 `CDP_UNAVAILABLE` 并建议重新登录，按硬边界返回不可恢复的 `LOGIN_RISK_CONTROL`（Issue #2145）。
- login 错误分类的限流关键词收窄为完整的 `too many requests`：本地 fd 耗尽的 strerror「Too many open files」不再被误判为不可恢复的平台风控并诱导联系平台客服，回到网络 / 本地失败通道（Issue #2144）。
- cache 的三处读取边界补上类型漂移解码：`shortlist_records.created_at` 与 `resume_job_links.linked_at/updated_at` 的 BLOB / 文本漂移不再让整条 `shortlist list` / `resume applications` 落成固定 `INTERNAL_ERROR`，按 `CACHE_RECORD_INVALID` 确定性上报；`saved_searches.name` 的 BLOB 漂移按 UTF-8 还原，无效行计入 `corrupt` 并跳过，`preset list` / `watch list` 其余项照常返回（Issue #2147）。
- wizard 空 `requested_steps` 的计划不再零步执行后写 `completed`：读取边界把历史断点缺失的字段归一成空元组的路径（#2033）此前会让步骤行全部停留在 `pending`，下一次 `--resume` 撞上 completed 自相矛盾检查后该 run 永久不可恢复；现在入口按 `CACHE_RECORD_INVALID` 确定性拒绝，不执行任何步骤也不改写状态（Issue #2149）。
- `shortlist annotate` 不再忽略 UPDATE 的 rowcount：security_id 以 BLOB 落库（#1885 的读取兼容）或行在 SELECT 与 UPDATE 之间被并发删除时，未写入不再报 `updated: true`，按 `SHORTLIST_ENTRY_NOT_FOUND` 输出错误并指向 `shortlist list` 核对（Issue #2135）。
- `favorites list` 在 `cardList` 非空但没有一个可识别对象时按 `NETWORK_ERROR`「元素不是对象」上报，与 `favorites sync` 的格式错误契约一致，不再静默伪装成空收藏（Issue #2136）。
- favorites 的三处恢复动作改用当前数据目录的 doctor 命令（`boss --data-dir … doctor`），自定义 `--data-dir` 时不再引导去诊断默认目录（Issue #2137）。
- `mark` 回显的 `security_id` 只认字符串并 strip：平台字段漂移成布尔 / 容器时不再把 Python repr（`True` / `{'a': 1}`）当令牌返回给 Agent（Issue #2138）。
- egress 的语言地区解析只识别真正的地区子标签：`zh-Hans` / `sr-Latn` 等 script-only 标签不再被读成伪地区 `HANS` / `LATN` 把正常会话判成 mismatch 阻断启动，`en-US-u-ca-gregory` 等扩展标签取到 `US`；时区映射表的 `America/Honolulu` 纠正为有效的 `Pacific/Honolulu`，夏威夷出口的矛盾不再漏检成 unknown（Issue #2129）。
- `status` / `doctor` 的 session 文件状态改为单次 lstat / stat：`stoken_freshness` 不再在 `exists()` 与 `stat()` 之间被并发 `logout` / 原子替换删除文件时把 `FileNotFoundError` 逃逸成 `LOCAL_IO_ERROR`；session 文件不可读时按 warn「无法读取 session 文件时间」报告而非「未提供时间」。`has_token=False` 分支不再连续三次 `exists()`（消除 `status=error` 却报「未检测到本地登录态」的自相矛盾），3.14 下权限错误也不会被吞成「没登录」（Issue #2131）。
- automation 决策在转录里定位不到配置话术时不再退回 prior 标记判「已回复」：本地 AI 改写问卷 / 追问文本（#1511）或操作者中途改模板后，有方向信息的转录按我方最后一条消息判断候选人是否回复，候选人未回复时保持等待，不再真实自动发出追问与交换联系方式；转录不含我方消息或完全没有方向信息时保留 prior 兜底（Issue #2133）。
- resume 的职位关联统一按文件真实名称落库：`resume link` 走 `stored_name`，`resume applications` 同时查请求名与真实名称并按职位去重，别名（`a/b` ↔ `a_b`）视角不再出现幽灵关联或查询不一致，按规范名删除能清掉别名方向建立的关联（Issue #1804、#2121）。
- `resume import` 的输入路径与 `-o` 输出路径同口径展开 `~`：MCP 子进程 / argv 列表不经 shell 时，`~/cv.json` 不再被报成「文件不存在」（Issue #2122）。
- `agent review reject` 清理 approve 中途失败遗留的 pending 行：尚未派发的关联行与复核状态一起收口，被人工拒绝的动作不再在下一轮被派发；已在执行 / 结果未知（executing、verification-required）的行反而阻断 reject 并提示先核对，不静默删除可能已生效的记录（Issue #2124）。
- automation 的 `allowed_actions` 对数组 / 对象元素按配置错误上报：不再在 set 成员判断处抛裸 `TypeError: unhashable type`，非字符串元素与未知动作走同一条「未知动作」ValueError 文案（Issue #2125）。
- crawl 的 `--hook-profile screenshot-full` 运行期注入读取 `SHA256SUMS` 失败时不再抛裸 `OSError`：清单被换成目录 / FIFO 或变得不可读统一收敛为 `HookRegistrationError`，命令层按 `LOCAL_IO_ERROR` 上报并给出 Hook 目录，不再被 service 兜成 stopped 成功信封与可重试 resume 指引；预检路径行为不变（Issue #2127）。
- 职位导出的标量兜底改走 `safe_text`：超过 4300 位的整数不再在裸 `str()` 抛 `ValueError` 中止整份 CSV / HTML 导出，与容器分支和 job_card 同口径按缺失单元格处理（Issue #2114）。
- job_card 的容器字段改用 `strict_json_text` 序列化：嵌套的 `NaN` / `Infinity` 归一为 JSON `null`，不再把非标准字面量带进列表文本与 wizard 导出产物；深嵌套 / 超大整数仍按缺失处理（Issue #2115）。
- wizard 声明失败的步骤结果与写前标记一次落盘：action 在 `begin_write` 后返回非完成状态时，标记不再被通用 `update_step` 清空，此后任何中断都不会让 `--resume` 重放结果未知的写请求（Issue #2117）。
- wizard 的 completed 终态检查对照 `requested_steps`：断点缺行的步骤不再被忽略，状态自相矛盾时按 `CACHE_RECORD_INVALID` 确定性拒绝，而不是 fail-open 返回 completed（Issue #2118）。
- wizard 中断写结算保留 `run.last_result`：恢复列表 / `--status` 里的职位数与内部采集 run_id 不再因结算写 NULL 而丢失，与 blocked 恢复分支同口径（Issue #2119）。

## [3.4.133] - 2026-11-20

### Fixed
- 登录态 salt 并发首次创建：抢锁失败方不再把「另一进程正在创建」当成损坏直接失败，在有界窗口内等待 salt 落位后复用；绝不生成第二份 salt 的契约不变，窗口耗尽仍缺失才报错并提示稍后重试（Issue #2099）。
- `agent run --limit` 同时约束待执行队列的派发：已批准会话先占用本轮全局上限，剩余预算才用于扫描；预算用满后不再发起扫描请求，未派发的 pending 行保持原状态留待下一轮（Issue #2100）。
- wizard 断点的 `error` 为非空对象时必须含非空字符串 `code`：外部漂移 / 旧数据缺 `code` 时按记录损坏拒绝，信封不再回退成可重试的 `NETWORK_ERROR` +「重试」指引（Issue #2101）。
- doctor 的浏览器内核探测按 lstat 口径读取缓存根：权限错误不再让整份自检报告变成 `LOCAL_IO_ERROR`，改为对不可读缓存输出 `warn`「无法确认是否已安装」，不引导重装；默认缓存目录补上 Linux `XDG_CACHE_HOME` 与 Windows `~/AppData/Local` 回退，与 patchright 驱动同口径（Issue #2103）。
- doctor 的 `auth_salt` 检查与登录路径同口径（`path_mode`）：指向普通文件的 salt 符号链接不再被报成 broken 并提示删除（那会作废已有 `session.enc` 与 AI 密钥）；悬空链接 / 目录 / FIFO 仍按损坏拒绝，悬空链接的提示改为说明目标缺失（Issue #2104）。
- 请求租约的目录创建 / 打开失败不再泄漏裸 OSError：crawl 不再把它裹成 `ok:true` 的 stopped 并给出可重试 resume，入口按不可重试的 `LOCAL_IO_ERROR` 上报（Issue #2106）。
- crawl 导出对非有限浮点使用同一归一规则：CSV 不再写 `nan` / `inf`、XLSX 不再落空数值单元格，与 JSON 的 null 口径一致（Issue #2107）。
- `stats --format html` 的 HTML 产物先按 backslashreplace 归一孤立代理字符（与 #2017 导出口径一致）：数据目录路径含非 UTF-8 字节时 `-o` 不再出现「文件已写出却报失败」，`--json` 与 stdout 分支也不再编码失败，`bytes` 与实际写出一致（Issue #2109）。
- chat 导出改用**原始**联系人字段计算身份（`contact_identity(raw_item)`）：`_text_cell` 的展示归一（布尔 → `"true"`、容器 → JSON 文本）不再让漂移的 `uid` 绕过 #2064 的 fail-closed，映射表 key 恢复回退 `security_id`，`is_new` 与 `added_ids` 重新对齐（Issue #2110）。
- chat 快照 `load_snapshot` 的「零可用对象」守卫与合并路径同口径（`dict` 且带 identity）：`[{}]` 这类无身份数组不再被当作完整基线，下次完整采集不再全员误报「新增」；真正的空数组仍是合法空基线（Issue #2111）。

## [3.4.132] - 2026-11-20

### Fixed
- stoken 刷新不再把刷新前的旧值当新令牌返回：headless 通道的 cookie jar 兜底值必须区别于注入的旧 stoken，`force_refresh` 对两条通道统一拒绝与当前 stoken 相同的返回值，按 `TOKEN_REFRESH_FAILED` 保留旧登录态（Issue #2091）。
- 复用 CDP 登录态的只读探测把限流（码 9 / HTTP 429）按 `unverified` 处理：限流不再被判为登录失效并触发清空用户浏览器里仍有效 Cookie 的强制重登；其余非 0 码维持 #424 的既有契约（Issue #2092）。
- 身份头探针页命中安全验证 / 访问受限页时按风控上抛并锁存，不再吞成空映射后继续完成登录；普通采集失败仍不阻断登录（Issue #2093）。
- 扫码轮询不再吞掉 `cookies()` 异常：浏览器在等待扫码期间关闭时立即报真实连接错误，而不是空转到「扫码登录超时」（Issue #2095）。
- crawl 顺序翻页（已在第 N 页取第 N+1 页）不再在没有导航请求时多记一次 `max_requests` 与节流等待：预算与共享租约只按真实滚动计一次，长翻页不会先于真实请求耗尽预算（Issue #2094）。

## [3.4.131] - 2026-11-19

### Fixed
- MCP server 的默认 `boss` 入口在模块加载时就按运行解释器同目录解析（与 `run()` 启动配置同一口径）：直接调用 `_run_boss()` / handler 的嵌入方与测试不再从 PATH 找 boss，PATH 上缺失或存在旧安装时不会静默驱动错误入口（Issue #2088）。

## [3.4.130] - 2026-11-19

### Fixed
- `hr download-resume` 的覆盖保护改按 `lstat` 判定目标路径：目标存在但元数据读不到时（权限等 `OSError` 被 `exists()` / `is_symlink()` 吞成 False）不再先发出四个平台请求才失败，客户端在发请求前 fail-closed，命令层按本地 IO 错误上报（Issue #2083）。
- crawl 导出的备份判定复用预检的 `lstat` 结果：`is_file()` 吞错时仍会备份旧产物，替换中途失败的回滚不再丢失旧内容（Issue #2085）。

## [3.4.129] - 2026-11-19

### Fixed
- 本地 JSON 序列化失败（深嵌套 / 超长整数）不再被报成可重试的 `NETWORK_ERROR`：无 `filename` 的 `EINVAL` 归为 `LOCAL_IO_ERROR`，Agent 不会反复重试同一份必然再次失败的数据（Issue #2078）。
- 私有原子写（登录态、AI 密钥、salt）与 crawl 导出的临时名只使用截断到 32 字符的目标名作前缀，接近 `NAME_MAX` 的合法文件名不再触发 `ENAMETOOLONG`；残留的临时 / 备份文件仍可辨识归属（Issue #2080）。

## [3.4.128] - 2026-11-19

### Fixed
- 联系人身份只认非空字符串或非 bool 整数 uid：布尔 / 容器 / 超大整数漂移不再让不同联系人共用同一身份或让翻页签名误判重复页；`job_card` 与职位字段的超大整数按缺失处理，容器序列化失败按空单元格降级（Issue #2063–#2065）。
- 招聘者 `hr jobs` 的分页信封与后继提示限制在可信页码内，非法平台回显回退到钳制后的请求页；`hr candidates --select` 作为关键词时按位置参数重放；`hr resume` 按实际缺失的 `geek_id` / `--job-id` / `--security-id` 生成错误与指引（Issue #2067–#2069）。
- automation 队列在派发前拒绝缺少 `candidate_key` 或发送类动作缺 `message` 的记录（错误带实际队列路径，行保持 pending）；JSONL 追加在旧行缺少结尾换行时先补分隔符，避免新旧记录粘成一行一起丢失；孤立代理字符让整文件回写失败时按本地状态错误上报并保留原文件（Issue #2071–#2073）。
- `crawl resume --background` 在派发 worker 前校验 `--pages` 覆盖值与断点进度，矛盾参数直接返回 `INVALID_PARAM`，不再先回报 `queued` 再让 worker 静默失败；`next_page == pages + 1` 保持合法（Issue #2075）。

## [3.4.127] - 2026-11-19

### Fixed
- 职位 / 聊天导出的单元格把 bool 写成 JSON 字面量、深嵌套容器序列化失败按空值、孤立代理字符按转义清洗；`chat --export` 当日快照数组非空但零个可用对象（手工改坏 / 旧格式漂移）时不再当作完整基线，避免下一次 diff 把所有未翻到的联系人误报为新增（Issue #2015–#2018）。
- automation 队列的 pending 行派发前按当前 `allowed_actions` 与置信度阈值门控，收紧配置后不再直接派发遗留 pending（行保持 pending，放开配置后仍可执行）；缺 `status` 字段的队列 / 复核记录按状态损坏拒绝，不再默认成 pending；`--limit` 按整轮全局上限生效，默认两个 tab 时不再处理 2×limit 个会话（Issue #2020–#2022）。
- `boss_search` 与 preset 同用时十个筛选项的显式空串原样下发，CLI 按「关闭该项筛选」覆盖 preset 值；pipeline / follow-up / digest 的估算页数与 collector 真实翻页上限一致并计入追加的面试数据请求，`hr last-messages` 无 `--friend-id` 时计入沟通列表请求；wizard 的 `role` / `platform` / `goal` 空串在 MCP 入口即被拒绝（Issue #2023–#2026）。
- `watch` 命中的保存搜索记录损坏（时间戳列类型漂移等）按 `SAVED_SEARCH_INVALID` 确定性失败上报，不再兜成可重试的 `NETWORK_ERROR`；`history` 达到可信页码上限时不再生成越界 `--page` 后继提示，改为引导缩小筛选条件（Issue #2011–#2012）。
- crawl 断点续跑不再用每次搜索都会轮换的 `securityId` 兜底去重，页内崩溃后 resume 不会把同一职位重复落库并重复导出；`next_page` 超出 `params.pages + 1` 与结果表负页码按记录损坏拒绝；`crawl resume --background` 的 completed 快速路径同样校验断点设置（Issue #2029–#2032）。
- wizard 恢复对 `params.inputs: null` 与省略 `requested_steps` 的合法断点不再误报 `WORKFLOW_PLAN_MISMATCH`；步骤记录缺失时在派发动作前以 `CACHE_RECORD_INVALID` 终止（本次没有平台写入，`replay_safe` 保持 true）；`--stop` 对记录存在但读不出、SQL 判定 completed 的任务报 `WORKFLOW_ALREADY_FINISHED`，不再误导成核对 run_id（Issue #2033–#2035）。

## [3.4.126] - 2026-11-19

### Fixed
- wizard 断点落盘失败不再掩盖原始风控 / 预算停止码；缓存时间戳列漂移成 BLOB / 文本时信封不再退化成固定 `INTERNAL_ERROR`；步骤记录缺失按本地状态损坏报 `LOCAL_STATE_WRITE_FAILED`，不再报可重试的 `NETWORK_ERROR`（Issue #1963–#1965）。
- `ai config` 新增 `--clear-base-url`，`ai_base_url` 写入后可清除，切 provider 后密钥不再发往旧端点；漂移成布尔值的 `ai_base_url` 不再回显为地址；同时改配置与密钥时密钥写失败不再留下半截更新；空白文本参数不再照常发起计费请求（Issue #1967–#1969 #1975）。
- `record_recruiter_greet` 更新 0 行改为报错，不再在本地记录丢失后重复发送招呼；watch 结果写入不再因孤立代理项抛 `UnicodeEncodeError` 并留下半截事务；watch 去重键在缺 `job_id` 时保留账号前缀；`stats` 的 `first_seen_at` 漂移值不再让窗口计数虚高（Issue #1971–#1974）。
- 聊天与职位导出把非有限数按 JSON 分支同口径归一，不再写出 `inf` / `nan` / `Infinity`；`find_friend` 对「成功但无列表容器」的响应返回可恢复的 `NETWORK_ERROR`，不再据此判定联系人不存在（Issue #1976–#1978）。
- `crawl resume` 单个职位详情反复失败不再卡死列表翻页与导出（记为 `detail-error` 并继续），同一轮不重复重试；恢复时的滚动翻页按次计入请求预算与租约；打开浏览器前失败保留断点里的 hook 注入记录（Issue #1981–#1983）。
- `resume list` 以文件自身名称列出副本与缺 name 的信封，不再产生无法点名的幽灵行；原子写与 PDF 导出的临时名改用固定短前缀，合法长文件名不再 `ENAMETOOLONG`；导入时孤立低代理项名称按参数错误拒绝（Issue #1984–#1986）。
- 请求租约异常路径不再用缺失的平台码重写已落盘风险锁（保留 31/32/38 等诊断字段与锁定时间）；共享租约竞争耗尽等待窗口时省略误导性的 50ms `retry_after_seconds`（Issue #1989–#1990）。
- `chat --export` 的元素全坏页不再被当成完整空列表（不覆盖当日快照、不误报全部联系人已消失）；元素全非对象的基线快照按不可用处理；`detail` 的 httpx 通道与浏览器通道对 `skills` / `raw_job_type` 同口径归一（Issue #1992–#1994）。
- `auth/salt` 路径异常时 `doctor` 不再输出 traceback、`status` 不再报可重试 `NETWORK_ERROR`；doctor 的 `auth_salt` 检查按 lstat 判定；`clean --risk-lock` 删除空目录 / FIFO 锁后正确报 `local_lock_removed`；`--privacy` 中途失败时 `remaining` 不漏 crawl；符号链接清理的字节数按链接本身统计；`--live-probe` help 与默认站点连通性检查一致（Issue #1996–#2001）。
- `batch-greet` 限流停批改为 `ok:false` 错误信封（`RATE_LIMITED` / `GREET_LIMIT`），触发停批的候选人保留在 `failed`；`history --page` 加可信页码上限，超长整数不再把确定性本地错误报成可重试的 `NETWORK_ERROR`；坏元素页翻满页数上限时正确标记 `truncated`（Issue #2003–#2006）。
- wizard 在 action 已声明失败后到达的超时 / 停止信号不再丢弃已落盘的写前标记，`--resume` 不再重放结果未知的写请求；`candidate_search` 的 `query` 非字符串（`false` / `0` / 容器）按 `INVALID_PARAM` 拒绝，不再静默执行空关键词或字面量搜索（Issue #2008–#2009）。

## [3.4.125] - 2026-11-19

### Fixed
- automation 决策不再用 prior 时间戳压过会话转录：转录显示候选人未回复时不再自动发追问、也不再发交换联系方式请求；升级检测只读候选人一侧消息，自定义话术不再让候选人被静默跳过；面试时间越界（如「25点99」）不再写入线索；预算停止时队列还原写盘失败不再掩盖原始停止码（Issue #1944–#1947）。
- MCP 参数映射把 `boss_search` 的显式空 `url` / `preset`、AI 工具的空 `jd_text` / `job_id` 原样下发，由 CLI 给出 `INVALID_PARAM` 诊断，不再静默按其余参数成功；子进程 stdout 含超长整数或超深嵌套时仍返回七键信封；参数超过系统 argv 上限（E2BIG）报 `INVALID_PARAM` 并指引缩短参数，不再误报成 CLI 未安装（Issue #1941–#1943）。
- `ai` 命令在登录态 salt 路径状态异常时返回 `LOCAL_IO_ERROR` 信封而不是 traceback；`--resume ""` 返回 `INVALID_PARAM` 且不发起请求；`ai config --api-key` 去掉首尾空白并拒绝非 ASCII / 控制字符；服务商回显密钥时按当前密钥做字面量遮蔽，非 `sk-` 前缀或含点号的形态不再进入 `error.message`（Issue #1950–#1953）。
- wizard 在写请求之后的停止 / 超时信号不再覆盖「结果未知」标记，`--resume` 不再重放可能已生效的写步骤；`stopped` 步骤带 `replay_safe:false` 时同样阻断重放；`bind_inner_run` 保留已落盘的写前标记；已完成步骤的 `result` 漂移成空值不重跑；断点的 `error.code` / `error.message` 类型漂移按记录损坏拒绝（Issue #1956–#1960）。

## [3.4.124] - 2026-11-19

### Fixed
- 缓存文本列（候选池、crawl 任务与结果、workflow 任务与步骤、简历投递）在读取边界只接受文本：BLOB 按 UTF-8 还原、还原失败或其它类型按 `CACHE_RECORD_INVALID` 上报，不再让整个信封退化成固定 `INTERNAL_ERROR` 且 `exit 0` / `ok=false`（Issue #1885）。
- 约束冲突 / 非法绑定值不再被当成缓存库损坏：新增 `CacheWriteRejected`，CLI 按 `INVALID_PARAM` 上报并说明库本身完好、无需删除；迁移期的冲突仍按 `CACHE_DATABASE_INVALID` 处理（Issue #1886）。
- greet / apply 迁移不再丢弃 `job_id` 为空的旧去重记录，升级后仍可按 `security_id` 命中，不再重复打招呼 / 投递（Issue #1887）。
- 超长整数作为 dict 键时脱敏 / 序列化阶段不再抛 `ValueError`，stdout 仍保留七键 `INTERNAL_ERROR` 信封（Issue #1897）。
- `error.details.outcome_unknown` 非布尔漂移时 `replay_safe` 改为一律 `false`（fail-closed），结果未知的写操作不再被标成可重放（Issue #1898）。
- 自由文本脱敏覆盖带引号的 JSON / dict-repr 形态（`{"token": "…"}`）与多段 Cookie 头，后续 `name=value` 段落不再明文残留（Issue #1899）。
- `handle_platform_error_output` 的 `read_only_operation=True` 参与 `UNKNOWN` → `NETWORK_ERROR` 归一，只读分支不再产出「不可恢复 + 可重试」的自相矛盾信封（Issue #1900）。
- 记录风控锁时非法 / 损坏的配置文件不再替换正在传播的 `ACCOUNT_RISK` / `ENVIRONMENT_RISK`：后备预算目录按 best-effort 处理，`risk_lock.details` 始终写入，信封保持风控契约（Issue #1892）。
- 只有 `request_budget_dir` 后备位置写锁成功时 `risk_lock.persisted` 正确报 `true`，不再误报「锁只留在进程内」（Issue #1893）。
- `ioreg` / `reg` 输出格式漂移时机器 ID 解析落到主机名指纹兜底，不再抛 `IndexError`（Issue #1894）。
- `chat --export` 的完整性旁车文件只认显式 `true`：`0` / `"false"` / `null` / 缺键等漂移值按不完整基线处理，不再把上一份不完整快照当成完整并误报新增（Issue #1911）。
- 当日快照解析失败时完整性标记保持 `false`；旁车文件先写保守值、再写快照、最后写真实值，进程在两次原子写之间被杀也不会让部分页快照成为完整基线（Issue #1912）。
- `chat --export` 的「已消失」表格与活动联系人 / CSV 行同口径，容器字段序列化为 JSON 文本而不是 Python repr（Issue #1913）。
- 沟通列表页缺少 `result` / `friendList` 容器时不当作完整空列表，`chat --export` 不再因此清空当日快照并把旧联系人全报为已消失（Issue #1914）。
- `field_text` 对 dict / list / tuple 与非有限数按缺失处理，job_card 的 `job_id` / `title` / `salary` 不再变成 Python repr 并进入后继命令提示；job_card 的列表字段逐元素归一，`raw_job_type` 容器按 None 输出（Issue #1917）。
- 职位导出的 CSV / HTML 单元格统一归一：列表里的 null 与 `nan` / `inf` 写成空值、容器序列化为 JSON 文本，不再出现字面量 `None` 与 Python repr（Issue #1916）。
- 风控锁的 stoken 摘要对孤立代理字符改用 `surrogatepass` 编码，记录风控锁不再因 `UnicodeEncodeError` 替换正在传播的 `ACCOUNT_RISK` / `ENVIRONMENT_RISK`，锁文件与进程内锁都能落盘（Issue #1918）。
- 请求租约在 POSIX 上同时锁住预算目录 inode：租约文件被外部删除 / 替换后仍保持跨进程互斥；加锁后复核 inode 身份并用 `fstat` 读取尺寸（Issue #1919）。
- 代理解析对 scheme 做 http / https / socks4 / socks5 allowlist，并拒绝含空白 / 控制字符的 host；非法代理在解析阶段报 `INVALID_PARAM`，不再推迟到浏览器启动（Issue #1920）。
- `record_risk` 只要主目录或共享目录任一处成功落盘即视为已持久化并清除进程内锁，`persisted` 元数据与磁盘事实一致；两处都失败才抛错（Issue #1921）。
- `agent stats` 对 action-log 的 `status` 先做类型校验，数组 / 对象不再让熔断后的恢复入口抛 `TypeError` 变 `INTERNAL_ERROR`（Issue #1928）。
- automation 状态 / 队列写回把深嵌套与循环引用引发的 `RecursionError` / `ValueError` 归成本地状态错误，不再报成可重试的 `NETWORK_ERROR`（Issue #1929）。
- automation 执行锁 / 控制锁命中本地 IO（只读目录、磁盘满、打不开库）时按 `LOCAL_IO_ERROR` 上报，只有真正损坏才报 `AUTOMATION_STATE_INVALID`（Issue #1930）。
- 复核队列 `status` 增加类型与取值域校验，漂移值不再让复核项永久卡死且不出现在统计中（Issue #1931）。
- `automation_config_from_dict` 只对 `None` 回退默认配置，`[]` / `""` / `0` / `False` 一律按配置错误拒绝（Issue #1932）。
- 面试线索表的表头与追加写入包装 `OSError` / `UnicodeError`，错误信封带 `interview-leads.csv` 路径与修复指引（Issue #1933）。
- crawl 会话在 seed 导航（首个平台文档）之前注册用户 Hook，首页 anti-debug / CryptoJS Hook 从第一个文档起生效（Issue #1935）。
- crawl 传输层 JSON 解析统一捕获裸 `ValueError` / `RecursionError`：超长整数字段不再让监听降级并追加一次页面内请求，也不再让详情路径按瞬时错误重试（Issue #1936）。
- Hook 目录的 SHA256SUMS 非 ASCII 或缺失时按 `HookRegistrationError` 上报，预检与运行期注入同一异常契约（Issue #1937）。
- crawl 整数配置拒绝 bool 与静默截断的 float，与 `crawl_port` 校验同口径（Issue #1938）。
- crawl 导出在安装与回滚都失败时保留旧产物唯一的备份，并在 `CrawlExportError` 消息里给出路径，不再把旧文件删得无迹可寻（Issue #1924）。
- crawl 导出失败路径的临时文件清理改为 best-effort：清理遇到权限 / 只读挂载错误不再替换原本正在传播的 `CrawlExportError` / `CrawlRuntimeUnavailable`（Issue #1925）。
- crawl 导出成功后的备份清理失败不再把已完成的导出报成 `stopped`，三份新产物照常返回路径（Issue #1926）。
- wizard 的 `crawl_start` 恢复时不再让 `plan.inputs` 里的 `run_id: null` 覆盖已绑定的内部 crawl run_id，中断后 `--resume` 能继续原任务（Issue #1903）。
- wizard 的 `crawl_resume` 与 `crawl_start` 同口径校验断点 query / city：冲突或非字符串输入按 `INVALID_PARAM` 拒绝，不再静默续跑旧任务并导出旧数据（Issue #1904）。
- 联系人标签名称与平台 ID 收敛到 `services/labels.py` 单一真源：wizard 接受 CLI 的全部 9 个标签，数字 ID 必须是已知成员，`label: "999"` 不再向平台发出写请求（Issue #1905）。
- wizard 的 message / label 等自由文本输入只接受字符串：容器不再以 Python repr 发给真人，`0` / `false` 不再被吞成平台默认招呼（Issue #1906）。
- `WorkflowActionError` 未显式给出 `recovery_action` 时按错误码登记表兜底，信封不再返回 null；`crawl_status` 缺任务的消息不再回显 `'run_id'`（Issue #1907）。
- wizard 的 `run_id` 与 `crawl_run_id` 同时给出且指向不同任务时报 `INVALID_PARAM`，不再静默取第一个（Issue #1908）。
- wizard 输入类型错误消息统一截断超长原始值，信封与 checkpoint 不再随输入规模膨胀（Issue #1909）。
- `--salary` 搜索遇到超长数字串的 `salaryDesc`（>4300 位）按「无法解析」处理，不再让整条搜索崩成 `INTERNAL_ERROR`（Issue #1889）。
- 职位描述缓存只接受字符串 `encryptJobId` / 描述：类型漂移按未命中处理，`--welfare` 管线不再因 sqlite 绑定 dict 抛 `ProgrammingError`（Issue #1890）。
- `write_json_atomic` / `strict_json_text` 把深嵌套与超长整数引发的 `RecursionError` / `ValueError` 归成本地 IO 错误，CLI 输出 `LOCAL_IO_ERROR` 信封而不是空 stdout 加 traceback（Issue #1891）。
- 沟通列表整页元素全是 null / 数字 / 字符串时继续翻页，不再据此宣称列表完整或联系人不存在（Issue #1878）。
- 职位去重按可用性选择 `job_id` / `encryptJobId`，非字符串 `job_id` 不再遮蔽合法标识（Issue #1879）。
- 分页信封对回退的请求页应用与平台回显页相同的上限，`page + 1` 的下一页提示不再可能撞上整数转换上限（Issue #1880）。
- wizard 的 `INTERNAL_ERROR` 分支（含 `PlatformResponseInvalid`）先读本步骤写前标记，写请求发出后的响应解析失败按「结果未知」上报，`--resume` 不再重放（Issue #1881）。
- 自定义 action 返回 `StepResult(status=FAILED)` 等非 COMPLETED / WAITING_INPUT 状态时，workflow 按失败 / 停止终止，不再继续执行后续步骤并落成 `completed`、`error=None`；写前标记存在时按结果未知处理（Issue #1882）。
- `WizardInput` 入口递归拒绝 NaN / Infinity；含 NaN 的旧断点恢复时改按规范化 JSON 值比较，不再永久命中不可恢复的 `WORKFLOW_PLAN_MISMATCH`（Issue #1883）。

## [3.4.123] - 2026-11-19

### Fixed
- `config set request_budget_dir` 拒绝已存在但不是目录的路径；启动期因此失败时恢复建议改为可直接执行的 `config reset request_budget_dir`（Issue #1822）。
- `config reset operating_mode` 同时清除旧版 `low_risk_mode`，`config list` 把由旧键派生的模式标为用户配置（Issue #1823）。
- 平台返回的页码超出合理范围时不再参与下一页提示，`me` / `chatmsg` 不会因超长整数被报成可重试的网络错误（Issue #1824）。
- `agent crawl` 的简历删除后继命令对以 `-` 开头的简历名前置 `--`，`crawl resume` / `crawl results` 的位置参数统一走同一保护（Issue #1826）。
- `agent crawl` 按标题回填 AI 结果的 selector 时只读取一次 title，空标题不参与匹配，不再因缺 title 抛 KeyError 落成 INTERNAL_ERROR（Issue #1827）。
- `clean` 后继提示里的 `--data-dir` 使用解析后的绝对路径（Issue #1828）。
- `clean --risk-lock` 删除锁路径上的符号链接本身（含悬空链接）；`local_lock_removed` 要求确实删过锁项且清理后读不到锁，不再报成功而锁仍生效，也不再给「无锁目录」误报 true（Issue #1829）。
- `clean` 中途失败时信封带回已完成项与失败项；清锁之后才失败会明确说明锁已清除（Issue #1830 #1831）。
- `search` 的预设重建 / 删除后继命令对以 `-` 开头的预设名前置 `--`（Issue #1832）。
- wizard 的 `crawl_start` 只把缺键与 null 视为未提供 run_id：显式 `0` / `false` / 空数组 / 空串按 `INVALID_PARAM` 拒绝，不再静默新建采集并发出真实请求（Issue #1833）。
- wizard 采集被限流 / 超时 / 停止时，错误详情带回 run_id、完成页数、jobs_seen 与已落盘输出路径（Issue #1834）。
- `crawl resume` / `crawl run` 导出失败时信封带回已知 run_id 与可直接执行的恢复命令，不再只有 `data=null`（Issue #1835）。
- wizard 的 `crawl_status` / `crawl_resume` / `crawl_stop` 只接受非空字符串 run_id，非字符串报 `INVALID_PARAM` 而不是「任务不存在」（Issue #1836）。
- `hr chat` 的页面未读计数跳过 NaN / Infinity，不再整条命令崩溃并报成可重试的网络错误（Issue #1837）。
- `hr location` 逐个尝试 `friendId` / `uid` 等标识键，`uid` 为 null 时仍能按 `friendId` 匹配会话，不再误报 `CONTACT_NOT_FOUND`（Issue #1838）。
- `hr location` 的发送记录存在性判断改用 `stat()`，只把 `FileNotFoundError` 当没有记录，权限类错误不再吞成「未发送」（Issue #1839）。
- `blank_option_names` 对非字符串取值不再直接 `.strip()`，按非法参数返回选项名（Issue #1841）。
- `hr chat` 的时间与消息文本只接受字符串或数值，dict / list 不再被 `str()` 写进输出数据（Issue #1842）。
- `hr request-resume` 在任何请求前检查 `boss agent stop` 停止开关；异常分支不能证明请求未发出时返回不可重试的 `ACTION_UNCONFIRMED`，能证明未发送的异常保持原有可重试契约（Issue #1840）。
- `watch run` 按 `watch add` 同口径校验存量记录的 `active`：未知或非字符串取值报 `SAVED_SEARCH_INVALID`，不再被兜成可重试的 `NETWORK_ERROR` 并中止 `--all`（Issue #1847）。
- 多位置参数的后继命令只前置一次 `--`（新增 `display.positional_args`），`shortlist remove` / `add` 提示不再出现第二个 `--` 而无法重放（Issue #1848）。
- `chat --export json` 与本地 JSON 状态把非有限数归一为 `null`，不再写出裸 `Infinity` / `NaN`；`write_json_atomic` 同步禁止非标准常量（Issue #1851）。
- `chat` 文本导出的容器字段序列化为 JSON 文本、`unread` 只认整数；`job` 的 HTML 导出不再渲染字面量 `None`，`write_export_file` 对未知格式抛错而不是静默不写（Issue #1853）。
- `prepare_output_path` 与 `refuse_symlink_target` 按 lstat 拒绝符号链接 / 目录 / 特殊文件，`digest` / `stats` / `chat --export` 等直接调用方不再把链接目标替换成新文件；`is_symlink()` 不再吞权限错误（Issue #1849）。
- 文件锁只把 `EAGAIN` / `EWOULDBLOCK` / `EDEADLK` 当锁冲突，errno 36 等其余 `OSError` 按本地 IO 错误上抛，不再误报「已被占用」（Issue #1850）。
- `chat --export` 的快照旁车文件记录采集完整性：上一份基线不完整时不再把没翻到的联系人误报为新增，`data.diff` 带 `baseline_complete`，摘要注明「上次采集不完整，未判断新增」；旧快照无标记时按完整处理（Issue #1852）。
- `history` 先把 jobList 里的容器字段收敛成空值、列表字段只保留字符串，`job_id` / `security_id` / `job_url` 不再写入 Python repr（Issue #1855）。
- `favorites` 的 `cardList` 只有 `null` / 缺失按空列表处理：`{}` / `""` / `0` 等假值非列表与真值非列表同口径报可重试 `NETWORK_ERROR`（Issue #1856）。
- `favorites` 页内全为非对象元素时立即报 `NETWORK_ERROR`，不再累计垃圾元素、翻满 50 页后误报 `RESULT_LIMIT_REACHED`（Issue #1857）。
- `stats` 的 `_percent` 对合法大整数抛 `OverflowError` 时按非数值返回占位符（Issue #1858）。
- MCP 的 `search --preset` 与 `watch run` 按详情筛选最坏情况估算超时（900 秒封顶），只读中断文案不再提示这两个命令没有的 limit/count（Issue #1860）。
- MCP 的 `boss_hr_jobs` / `boss_hr_jobs_detail` 登记 `recruiter-jobs-*` 合规命令标识，`boss_hr_jobs` 随 action 选择 list / online / offline（Issue #1861）。
- MCP 的 `boss_ai_chat_coach` 把显式空 `style` 原样下发，不再落回默认沟通风格（Issue #1862）。
- MCP 同步入口拒绝含 NUL / 孤立代理项的参数时明确「子进程未启动、没有平台写入」，不再套用检查 checkpoint 的写中断指引（Issue #1863）。
- MCP server 定位同目录 `boss` 入口时只把「不存在」当未找到，权限等其它错误不再静默回退 PATH 上的另一份 boss（Issue #1864）。
- MCP 的 `hr applications` / `hr chat` / `hr last-messages` 把显式空 `job_id` 原样下发给 CLI，不再静默当作未传（Issue #1865）。
- 自动化队列的 `message` / `platform` / `candidate_key` / `reason` 只接受字符串，类型漂移按状态损坏上报，不再把 `str(None)` 当话术发给候选人（Issue #1867）。
- 自动化在途标记的 `notified` 只有布尔 `true` 才算已通知；`0` / `""` / `"false"` 按未通知处理并重开熔断，不再静默跳过（Issue #1868）。
- Chromium 代理不再使用 `HTTP_PROXY`（平台是 https，httpx 的 https 请求本就直连），`NO_PROXY` 原样转发为 `bypass`，两个通道对同一域名同进同出（Issue #1869）。
- 代理地址里的 IPv6 字面量保留方括号，`http://[::1]:8080` 不再被拼成无法解析的 `http://::1:8080`（Issue #1870）。
- `RequestThrottle` 不再用 `0.0` 当单调时钟哨兵：刚开机时首条请求不会白等一个间隔，也不会误报 `REQUEST_BUDGET_EXHAUSTED`（Issue #1871）。
- `crawl resume` 对已完成任务校验三份产物是否仍在，缺失时用本地缓存重新导出（不发平台请求），不再报成功却给出不存在的文件路径（Issue #1873）。
- `crawl status` 的 `hooks.success` 与 resume 同口径只认真布尔，类型漂移按 `CACHE_RECORD_INVALID` 上报，不再原样透传（Issue #1874）。
- 采集产物替换失败时清理已创建的 `.jobs.*` 备份临时文件，不再每次重试残留一个（Issue #1875）。

## [3.4.122] - 2026-11-19

### Fixed
- `batch-greet` 遇到发送结果未知的异常时保留预约并停止批次，引导用户核对送达状态。确认未发送时，保留预约直至重试结束（Issue #1818）。
- `exchange` 对类型漂移的联系人字段安全处理：`name` 只认非空字符串，`uid` / `securityId` 不再被转成 Python repr 发给平台，后继 `chatmsg` 命令对 `-` 开头的 uid 前置 `--`（Issue #1812）。
- `show` 对索引缓存里非字符串的 `security_id` 报参数无效且不发请求，`lid` 与平台 `jobCard.lid` 只认字符串，不再误报「数据库无法打开」（Issue #1813）。
- `batch-greet --dry-run` 的后继命令在关键词以 `-` 开头时把它排到选项之后并用 `--` 隔开（Issue #1815）。
- `batch-greet` 跳过标识缺失或不是字符串的搜索结果：不再把 Python repr 发给写请求，也不再因一条无标识结果以「结果未确认」停掉整批（Issue #1816）。
- `greet` 拒绝纯空白的 `--message`；`detail` 把纯空白的 `--job-id` 按没传处理，缓存里非字符串的 job_id 按未命中处理（Issue #1817）。

## [3.4.121] - 2026-11-19

### Fixed
- `resume` 全族后继命令对以 `-` 开头的名字与 ID 前置 `--`，带选项的提示把选项挪到 `--` 之前，照抄执行不再报「没有选项」（Issue #1803）。
- `resume edit --field` 拒绝系统托管的 `name` / `created_at` / `updated_at`：不再报成功却值不生效，或把任意字符串写进 `created_at`（Issue #1806）。
- `favorites sync` 把类型漂移的 `securityId` / `encryptJobId` 视为缺主键计入跳过，文本字段与标签归一为字符串，不再把 Python repr 写进候选池（Issue #1807）。
- 按清洗后同名的别名（如 `a_b` 之于 `a/b`）操作简历时，`resume edit` 不再把简历静默改名，`resume delete` 同时清理真实名称下的职位关联（Issue #1804）。
- `resume delete` 在简历路径是目录时先拒绝并保留职位关联，不再 exit 1 却已清掉关联（Issue #1805）。
- `parse_resume` 对类型漂移的文本字段归空串、证书只留字符串名、`zpData` 为 null 时回落 `data`、非对象输入返回空结构（Issue #1808）。

## [3.4.120] - 2026-11-19

### Fixed
- `watch` / `shortlist` 的后继命令对以 `-` 开头的名字前置 `--`，照抄提示不再报「没有选项」（Issue #1797）。
- 存量 watch 的 `query` 缺失或为 null 时 `watch run` 返回 `SAVED_SEARCH_INVALID`，不再把 `None` / 空串当关键词发给平台并报成功（Issue #1798）。
- `shortlist` 的 `tags` 被写成 BLOB 时按损坏数据退化为空标签，一条坏行不再让 `list` / `compare` / `annotate` 全部落成 INTERNAL_ERROR（Issue #1799）。
- `watch run` 对纯空白名字在入口返回 `INVALID_PARAM`，恢复提示不再指向一条必然再次失败的 `watch add`（Issue #1800）。

### Fixed
- `chat --export` 的完整采集直接替换当日快照：同一天重跑时已消失的联系人当次即被报出，不再留在快照里推迟到次日（Issue #1791）。
- `chat --export md` 对漂移成列表 / 对象的单元格同样转义 `|`、换行与链接语法，表格不再被拆列（Issue #1792）。
- `export --format csv` 与 `chat --export csv` 把缺失值写成空单元格而不是字面量 `None`；单独的占位符 `-` 不再被加上单引号前缀（Issue #1793）。
- `chat --export` 的「我主动」小标题只把状态为未读的联系人计入未读，状态未知的不再算入（Issue #1794）。

## [3.4.118] - 2026-11-19

### Fixed
- `stats` / `digest` 的 `-o` 拒绝纯空白路径并返回 `INVALID_PARAM`，不再在当前目录写出名为空格的文件；与 `chat --export`、`export`、`resume export` 口径一致（Issue #1788）。
- wizard 的 `crawl_stop` 目标在任务已结束时返回 `CRAWL_RUN_FINISHED`，不再报告 `stop_requested` 并写入停止标记；与 `boss crawl stop` 口径一致（Issue #1786）。

## [3.4.117] - 2026-11-19

### Fixed
- `ai config --base-url` 拒绝主机或端口无法解析的地址（如 `http://[::1/v1`、`http://127.0.0.1:abc/v1`），返回 `INVALID_PARAM`，不再在写入时崩成 traceback 或存进配置后让每条 AI 命令崩溃；手工改坏的地址在请求时落成 `AI_API_ERROR` 信封（Issue #1778）。
- 信封与日志的自由文本脱敏覆盖 URL 的 userinfo 口令：`https://user:password@host` 形态的地址（如带 Basic Auth 的 AI 代理 `ai_base_url`）不再把口令原样回显（Issue #1779）。
- AI 服务用 HTTP 200 带回错误对象时，错误信息带出服务商的说明，并按其中的状态码给出限流 / 鉴权的恢复建议；缺少 `choices` 时不再把 Python 异常文本当作错误信息（Issue #1780）。
- `hr greet` 在开聊结果的 `chat` / `status` 漂移成字符串或浮点（`"0"`、`0.0`）时仍识别权益拦截页，不再报成已发送并把候选人永久记成已打招呼（Issue #1781）。
- `hr greet` / `hr recommendations` / `hr resume` 的必填 ID 为空或纯空白时返回 `INVALID_PARAM`，不再向平台发请求，也不以空键写下招呼记录（Issue #1782）。
- `hr jobs offline` / `online` / `detail` 的后继命令对职位 ID 加 shell 引用，以 `-` 开头的 ID 前置 `--`（Issue #1783）。

## [3.4.116] - 2026-11-19

### Fixed
- 恢复终态 crawl 任务时状态先落库再执行：前台恢复在打开采集浏览器之前写 `running`，后台恢复在启动 worker 之前写 `queued`，这段时间到达的 `crawl stop` 不再按旧终态被拒而采集照常继续，重复的后台恢复也不再拉起第二个 worker；worker 启动失败时把原因写回，不留无人执行的 `queued`（Issue #1771）。
- wizard `crawl_start` 显式带 `run_id` 且 `query` / `city` 与断点不一致时返回 `INVALID_PARAM`，不再静默按旧关键词续跑并把结果记到新关键词名下；输入一致时续跑行为不变（Issue #1772）。
- `crawl resume` 读到损坏的结果行时，`CACHE_RECORD_INVALID` 的后继命令使用真实 run_id，不再把 job_key 拼进 `crawl status`（Issue #1773）。
- `boss schema --format openai-tools` / `anthropic-tools` 导出的参数 schema 顶层不再带 `anyOf` / `allOf` / `not` / `if` / `then` / `else`：12 个工具的这些约束改为附在工具描述里（JSON Schema 片段，仍由 CLI 校验），避免整份工具定义被接口拒绝；`mcp-tools` 与运行时 `tools/list` 保持完整 schema（Issue #1774）。
- `boss_wizard` 的 schema 声明 run（含省略 `action`）时 `role` / `platform` / `goal` 必填，与实际校验一致（Issue #1775）。

## [3.4.115] - 2026-11-19

### Fixed
- 自由文本脱敏覆盖复合凭据键：`access_token=` / `refresh_token=` / `client_secret=` / `x-api-key=` 等不再因为凭据段前有 `_` / `-` 而把值原样留在错误信封与日志里；字典键 `x-api-key` / `api-key` 同步脱敏（Issue #1765）。
- `--exit-codes` 预扫描在 `--` 处停止，并跳过其他带值全局选项的值：`boss -- --exit-codes sysexits` 不再把位置参数当成全局选项、让默认模式的失败退出码变成 78（Issue #1766）。
- `digest` / `stats` 的「打开文件」后继命令对以 `-` 开头的相对路径加 `./` 前缀，`open` / `xdg-open` 不再把文件名当选项解析（Issue #1767）。
- 简历名含 `* ? | < > "` 或控制字符时，新建简历与默认导出文件名改用可在 Windows 落盘的文件名，不再必然写盘失败并被报成磁盘 / 权限问题；旧版本按原字符写出的简历文件仍按原文件名读取、修改与删除（Issue #1768）。

## [3.4.114] - 2026-11-19

### Fixed
- 文本 / JSON / 私有字节与简历 PDF 的原子写入器在准备临时文件和 `os.replace` 提交前都检查目标：FIFO / 设备等特殊文件不再被静默替换成普通文件，符号链接与目录明确拒绝；拒绝时保留目标、清理临时文件并携带路径供 `LOCAL_IO_ERROR` 分类（Issue #1762）。
- `chat --export` 在认证和保存快照前检查默认导出目标。无效的父路径返回 `LOCAL_IO_ERROR`，非字符串且非 `null` 的 `export_dir` 返回 `CONFIG_INVALID`（Issue #1760）。

## [3.4.113] - 2026-11-18

### Fixed
- `agent resume` 不再绕过 `state.json` 的符号链接保护：显式恢复时直接原子写状态文件会替换链接、留下目标中的旧熔断与 checkpoint（恢复链接后旧状态复活），现在同样报 `AUTOMATION_STATE_INVALID` 且不解除熔断；`stop.json` 的悬空链接原子替换仍按现有设计允许（Issue #1751）。
- `request-budget.lease` 在打开前按 lstat 判定路径类型：符号链接（含悬空）不再把请求租约写进数据目录之外的目标，FIFO 不再让所有平台请求在打开租约时永久阻塞，目录 / 设备也明确拒绝；普通文件与缺失文件自动创建的行为不变（Issue #1753）。
- `crawl` 三份导出产物（`jobs.json` / `jobs.csv` / `jobs.xlsx`）与输出目录在替换前按 lstat 判定：符号链接不再被备份机制移到临时文件后删除、目标也不再保留旧内容，FIFO / 设备不再被 `os.replace` 静默替换；同名目录仍保留既有 `IsADirectoryError` 回滚契约（Issue #1755）。
- `resume export` 与 `chat --export` 检查省略 `-o` 时生成的默认导出文件。文件目标为 FIFO、设备或符号链接时返回 `INVALID_PARAM`，保留原目标（Issue #1757）。

## [3.4.112] - 2026-11-18

### Fixed
- automation 写入侧统一拒绝符号链接与非普通文件：`_append_jsonl` 不再写穿指向普通文件的符号链接目标，`write_jsonl` / `write_state` 不再用 `os.replace` 静默把链接替换成普通文件（恢复链接后旧的 pending / review 行会复活并被重放），FIFO / 目录 / 设备也不再被整文件回写替换；统一报 `AUTOMATION_STATE_INVALID`，普通文件与缺失文件自动创建的行为不变，读取侧仍沿普通文件链接读取（Issue #1746）。
- `append_interview_lead()` 不再沿符号链接把候选人面试线索写到数据目录之外：`interview-leads.csv` 是指向普通文件的链接时也直接拒绝并报 `AUTOMATION_STATE_INVALID`，链接与目标保持原样；普通文件与缺失文件自动建表的行为不变（Issue #1748）。

## [3.4.111] - 2026-11-18

### Fixed
- 缓存库 `cache/boss_agent.db` 与请求节流库 `request-budget.sqlite3` 在打开前按 lstat 判定路径类型：路径是符号链接（含悬空链接）时报 `CACHE_DATABASE_INVALID` / `LOCAL_IO_ERROR` 并给出移除指引，不再让 SQLite 跟随链接把数据库创建或改写进数据目录之外的目标文件；目录 / FIFO / 设备等非普通文件同样明确拒绝，与 `clean` 的库预检（#1712）同口径（Issue #1739）。
- automation 队列追加写入（`pending-actions.jsonl` / `action-log.jsonl` / `human-review-queue.jsonl`）在打开前按 lstat 判定路径类型：队列被换成 FIFO 时不再永久阻塞在 `open("a")`（`agent run` 会在动作已执行后挂死），悬空符号链接不再在目标处新建文件、把动作日志写到数据目录之外；FIFO / 目录 / 设备 / 悬空链接统一报 `AUTOMATION_STATE_INVALID`，指向普通文件的符号链接照常追加。`agent review reject` 的日志追加失败也改为返回信封而不是 traceback（Issue #1741）。
- `stats` 读取缓存库前改为直接 lstat 判定：`cache/boss_agent.db` 是符号链接（含悬空链接）时不再跟随链接读取外部数据库、也不再在链接目标旁创建 / 改写 WAL 的 `-shm` / `-wal`，统一按 `LOCAL_IO_ERROR` 拒绝；只有文件确实不存在才返回「缓存尚未建立」（Issue #1743）。
- automation 的 `.run-lock.sqlite3` / `.control-lock.sqlite3` 在 `sqlite3.connect` 前拒绝符号链接与非普通文件，SQLite 不再跟随链接把锁库写进数据目录之外的目标；普通库与缺失文件自动建库的行为不变（Issue #1743）。

## [3.4.110] - 2026-11-18

### Fixed
- `hr download-resume` 的 `--output` 接入统一路径预检：FIFO / 设备 / 符号链接（含悬空链接）在取登录态与发出 `chat_history` / `friend_detail` / `preview-check` / 下载请求之前按 `INVALID_PARAM` 拒绝，父路径不可用按 `LOCAL_IO_ERROR` 拒绝；不再先花四个平台请求、再把特殊文件报成「输出文件已存在」（Issue #1726）。
- `agent run` / `agent pending list` / `agent stats` 读取 `pending-actions.jsonl` / `action-log.jsonl` / `review-queue.jsonl` 时不再直接 `read_bytes()`：队列路径被换成 FIFO / 设备会永久阻塞在打开阶段，命令既不返回信封也不退出。现在与 `state.json` / `stop.json` 同口径按 lstat 判定，悬空链接与非普通文件报 `AUTOMATION_STATE_INVALID`；只有文件确实不存在才算空队列，指向普通文件的符号链接照常读取（Issue #1728）。
- `chat --export` 读取上一份聊天快照时不再直接 `open()`：`chat-history/` 里按日期命名的 FIFO / 设备会永久阻塞在打开阶段，导出既不返回信封也不退出。现在与当天快照（#1698）同口径先按 lstat 判定类型，非普通文件与悬空链接按「快照不可用」告警并退化为首份快照；指向普通文件的符号链接照常读取（Issue #1730）。
- `boss show` / `recommend` / `search` 读取索引缓存时不再用 `exists()` + `read_text()`：缓存文件被换成 FIFO / 设备会永久阻塞，元数据错误又被当成「没有缓存」并提示重跑搜索。现在按 lstat 判定，只有文件确实不存在才算没有缓存；非普通文件与悬空链接报 `LOCAL_IO_ERROR`，指向普通文件的符号链接照常读取，损坏 JSON 仍按没有缓存处理（Issue #1732）。
- 独占文件锁 `_acquire()` 打开前按 lstat 判定：`refresh.lock` / `import.lock` 与 run 锁被换成 FIFO / 设备时不再永久阻塞登录态刷新、模型导入、聊天导出与 `crawl` / `wizard` 恢复；符号链接、目录与悬空链接按带 `filename` 的 `OSError` 拒绝，由命令层兜底为 `LOCAL_IO_ERROR`。只有文件确实不存在才创建新锁文件，普通锁文件的加锁与释放行为不变（Issue #1734）。
- `crawl --hook-profile screenshot-full --hook-dir` 的 Hook 预检与运行期注入不再直接 `read_bytes()`：脚本被换成 FIFO / 设备时不再永久阻塞，改为按 lstat 判定并进入既有的逐项失败列表；`SHA256SUMS` 也只有文件确实不存在才报「缺少完整性清单」，权限错误与特殊文件给出明确原因（Issue #1736）。

## [3.4.109] - 2026-11-18

### Fixed
- `stats` 把缓存库每次查询的 `sqlite3.OperationalError` 都吞成全零计数：遇到其他进程持有排他锁时返回 `ok:true` 的空漏斗，遇到磁盘 I/O 错误时报表不完整。现在只有旧库缺表返回 0；锁冲突报 `CACHE_BUSY`、磁盘与只读错误报 `LOCAL_IO_ERROR`，其余才按缓存库损坏处理（Issue #1718）。
- `request-budget.sqlite3` 的只读库 / 只读目录 / 磁盘满 / 设备 I/O 错误不再被误判成「库损坏」并提示备份删除：这些失败与库内容无关，删库既修不好又会丢掉冷却时间与节流历史；现在保留原始 `OperationalError`，由命令层给出 `LOCAL_IO_ERROR` 通用指引，只有真正无法解析的库才提示删除重建（Issue #1721）。
- `digest` 的 `-o` 在登录与平台请求之前接入统一路径预检：父路径不可用先报 `LOCAL_IO_ERROR`，不再先翻沟通列表、取面试数据后才失败；`digest` / `stats` / `resume export` 的 `-o` 遇到 FIFO / 设备 / 悬空链接按 `INVALID_PARAM` 拒绝，不再被原子写入替换成普通文件；`resume export -o <目录>` 与 `export` / `chat` 统一为 `INVALID_PARAM`（Issue #1723）。

## [3.4.108] - 2026-11-18

### Fixed
- 非隐私 `clean` / `clean --all` / `--dry-run` 在搜索缓存库、索引缓存、聊天快照、导出目录或数据库表的路径元数据不可读时，不再把「状态未知」当成「没有可清理内容」并返回成功；现在只有 `FileNotFoundError` 才算缺失，其余错误报 `LOCAL_IO_ERROR`，不会静默跳过实际未执行的删除。缓存库为符号链接（含悬空链接）或非普通文件时拒绝打开，避免 SQLite 在链接目标处创建或改写数据目录之外的文件（Issue #1712）。
- `ai @文件路径` 与 `resume import` 对 FIFO / 设备文件不再直接 `read_text()` 永久阻塞：路径类型改为 lstat 判定，非普通文件与悬空链接按 `INVALID_PARAM` 明确拒绝；路径元数据不可读时不再被 `exists()` 吞成「文件不存在」，改报 `LOCAL_IO_ERROR`（Issue #1714）。
- `stats` 在缓存库路径元数据不可读时不再报「缓存尚未建立」并返回全 0：只有 `FileNotFoundError` 才算没有库，目录 / FIFO / 设备等非普通库文件明确报 `LOCAL_IO_ERROR`（Issue #1716）。
- `-o` / `output` 的 `inspect_output_path` 预检改用 lstat 判断目标与每一级祖先：元数据错误不再被当成「路径不存在」而报告可写，避免 `export` / `chat --export` 在翻完平台分页后才失败；符号链接语义保持不变（Issue #1716）。

## [3.4.107] - 2026-11-18

### Fixed
- 本地模型重导入不再先删除旧副本：复制先落同目录临时路径，成功后再替换目标，失败时旧模型与登记表原样保留；导入全程持有跨进程锁，目标为符号链接或源为 FIFO / 设备时拒绝，不会写穿数据目录或并发覆盖 `registry.json`（Issue #1705）。
- AI 配置 JSON 损坏或顶层非对象时，`ai config` / `ai local configure` 不再用默认值整文件覆盖并静默清空其他设置；写入前严格读取，坏文件返回 `CONFIG_INVALID` 且文件字节不变（Issue #1706）。
- `session.enc` / `api_key.enc` 的路径状态不可读、为目录 / FIFO / 悬空链接时不再被当成「未登录 / 未配置」：只有文件缺失才返回 `None`，其余情况报 `LOCAL_IO_ERROR`，避免引导用户重新登录或填 key 后覆盖原密文（Issue #1709）。

## [3.4.106] - 2026-11-18

### Fixed
- `append_interview_lead()` 在 `interview-leads.csv` 的路径状态读取失败时会把它当成新文件并写入表头，截断已有面试线索记录；现在只有 `FileNotFoundError` 才创建表头，目录、FIFO、悬空链接与不可读文件都按 `AUTOMATION_STATE_INVALID` 失败且不改动历史数据（Issue #1694）。
- 聊天快照在当天快照路径元数据不可读、被目录 / FIFO / 悬空链接占用或已有文件不可读时，不再把它当成「今天还没有快照」并用当前页覆盖已合并的分页结果；只有 `FileNotFoundError` 才从空历史开始，坏 JSON 仍按无历史处理（Issue #1698）。
- 共享 salt 只接受 16 字节普通文件：创建改为完整写入后原子落位并在锁内读取，空文件、半截写入、目录 / FIFO / 悬空链接不再被静默接受，避免 PBKDF2 用错误 salt 派生密钥后旧密文永久解不开（Issue #1699）。
- 本地模型登记表在路径元数据不可读、被目录 / FIFO / 悬空链接占用或文件不可读时，`strict=True` 的写入方不再返回空表并用空表覆盖已有登记；只有 `FileNotFoundError` 才算缺失（Issue #1700）。
- `agent stop` 在 `automation/stop.json` 被目录或指向目录的符号链接占用时，不再落到泛化的 `LOCAL_IO_ERROR`；写入前按 `AUTOMATION_STATE_INVALID` 报出实际路径，悬空符号链接仍可写入（Issue #1684）。
- 风险锁路径为悬空符号链接、目录或 FIFO 时，`read_lock()` 会把「锁状态未知」当成「没有锁」；Python 3.14 下路径元数据读取失败也会被 `exists()` 吞掉。现在这些情况统一按损坏锁 fail-closed，且不再直接读取非普通文件（Issue #1686）。
- `AIConfigStore` 在 `auth/salt` 的存在性判断失败时会重新生成共享 salt，覆盖后已有的 `session.enc` 与 `api_key.enc` 永久无法解密；现在与 `TokenStore` 共用 `O_EXCL` + 刷新锁的 salt 生命周期，读取失败时上抛而不是覆盖（Issue #1688）。
- `clean --privacy` 在缓存库路径元数据不可读时会把数据库当成不存在，跳过隐私预检并先删除登录态与简历；现在只有 `FileNotFoundError` 才视为没有库，其他读取失败在删除任何文件前以 `LOCAL_IO_ERROR` 中止（Issue #1690）。
- 简历存在性判断改用 `lstat` / `stat`：路径元数据不可读时不再被 `Path.exists()` 当成「不存在」而绕过 `resume import` / `init` / `clone` 的同名覆盖保护；目录、FIFO、悬空链接按 `RESUME_FILE_INVALID` 报告，悬空链接仍可删除（Issue #1692）。
- 配置文件路径状态不可读时，`config set/reset`、`crawl` 配置写入与 AI 配置不再把读不到当成空配置并整文件覆盖其他设置；新增共享的 `read_bytes_if_regular()`，只有 `FileNotFoundError` 才返回空/默认值，目录、FIFO、悬空链接与元数据错误均上抛（Issue #1695）。

## [3.4.105] - 2026-11-18

### Fixed
- 目录权限等问题导致 `automation/stop.json` 的路径状态无法读取时，停止开关保持熔断；`hr location` 共用只读检查，`agent run --resume` 会在写入状态前报告 `AUTOMATION_STATE_INVALID`（Issue #1680）。
- `automation/state.json` 的路径状态无法读取或被悬空符号链接占用时，不再静默返回空状态，避免丢失会话阶段与在途标记；现在按 `AUTOMATION_STATE_INVALID` 报告（Issue #1682）。

## [3.4.104] - 2026-11-18

### Fixed
- `automation/stop.json` 为目录、指向目录的符号链接或悬空链接时，`read_stop()` 已按熔断 fail-closed，但 `hr location` 的 `is_file()` 预检把它当成「没有停止开关」，仍会预览 / 发送工作地点；现在两个读取器同口径拦截（Issue #1675）。
- `CHANGELOG.md` 的 `[3.4.92]` 段落在 3.4.92 发版时被留在 `[Unreleased]` 上方，版本顺序倒置；现在移回 3.4.93 与 3.4.91 之间，并新增结构测试（Issue #1676）。
- `agent run --resume` 在 `stop.json` 为目录或指向目录的符号链接时先改写 `state.json`、再抛 `IsADirectoryError`，最终只得到泛化的 `LOCAL_IO_ERROR`；现在写任何状态前按 `AUTOMATION_STATE_INVALID` 报出实际路径，悬空符号链接仍可恢复（Issue #1678）。

## [3.4.103] - 2026-11-18

### Fixed
- `hr download-resume --output ""` 被 `click.Path` 归一成当前目录，登录前报 `AUTH_REQUIRED`、登录后报「文件已存在」；不支持的扩展名要等四个平台请求完成后才报错。现在两者都在取凭据与发请求前按 `INVALID_PARAM` 拒绝（Issue #1672）。

## [3.4.102] - 2026-11-18

### Fixed
- `agent stats` 直接统计原始 JSONL，坏 confidence / status 仍报 ok 与虚假待执行量；现在与队列执行路径共用行校验（Issue #1667）。
- 搜索缓存 `response` 列类型漂移时 `json.loads` 抛 `TypeError`，本地坏行升级成 `INTERNAL_ERROR`；现在非文本载荷按未命中处理（Issue #1668）。

## [3.4.101] - 2026-11-18

### Fixed
- 自由文本脱敏依赖英文单词边界，中文紧邻的 `token` / `stoken` / `api_key` 会原样进入错误信封与日志；现在按 ASCII 键边界匹配（Issue #1662）。
- `Authorization: Basic/Token/Digest` 只脱敏方案名、留下真实密钥；现在所有方案整段脱敏（Issue #1663）。
- TTY 的 `operator_actions` 绕过脱敏，真人提示里的凭据明文留在 stderr；现在与 JSON 出口同口径（Issue #1664）。

## [3.4.100] - 2026-11-18

### Fixed
- 导出产物中的 NaN/Infinity 与孤立代理字符会让 JSON/CSV/XLSX 产物失败或生成非标准 JSON；现在非有限数归一为 `null`、不可编码字符转义后落盘（Issue #1653）。
- `boss export` 跨页重复职位 ID 占用导出数量，用户拿到的条目少于 `--count`；现在同一次 workflow 内按非空职位 ID 去重（Issue #1654）。
- 非 zhipin 域名的 verify/security 同名路径被误判成平台安全页，误开风控锁；现在只认平台域名或相对路径（Issue #1655）。
- stoken Cookie 兜底只按名称匹配，异域同名 Cookie 会被写入登录态；现在校验 zhipin 域名（Issue #1656）。
- DevToolsActivePort 自动发现的 CDP 端口未传给裸 CDP 聊天调用，`evaluate_js` 仍探测默认 9222；现在复用实际连接端点（Issue #1657）。
- automation 扫描路径命中未确认写动作时漏标 notified，首次 `--resume` 会重复开熔断；现在与队列路径同口径（Issue #1658）。
- PDF 导出会按导入简历里的头像 URL 出网，link 还可注入 `javascript:`；现在头像仅允许内嵌 data URI、链接限定白名单并阻断渲染期外部请求（Issue #1659）。

## [3.4.99] - 2026-11-18

## [3.4.98] - 2026-11-18

### Fixed
- `hr location` 命中风控后释放预约；风控解除后可能重发地点卡；现在风控异常与响应都保留结果未知状态（Issue #1644）。
- 空 `job_id` 让求职者动作预约键塌缩成全局行，跨职位误报已打招呼；现在缺 ID 条目按结果未知拒绝且不写全局键（Issue #1645）。
- 采集列表字段类型漂移让 XLSX 导出永久失败且 run 无法完成；现在平台字段按文本归一，导出层对未知类型兜底（Issue #1646）。
- 缓存 `created_at` 的远未来值绕过 TTL 判断，过期搜索 / JD 永久命中；现在对称 skew 阈值按坏行删除（Issue #1647）。
- MCP `boss_search` 未声明 CLI 支持的 `url` / `preset`，调用被当未知参数；现在导出契约与参数映射同步（Issue #1648）。
- `request_delay` 文档默认值与 schema / 运行时相差一个数量级；中英文文档已同步（Issue #1649）。
- `boss favorites` 的 TTY 状态列读取错误键，永远显示 `-`；现在显示归一后的职位状态（Issue #1650）。
- `ai local configure` 接受无效 `--base-url` 并落盘，`ai local smoke` 又把确定的配置错误报成可重试网络故障；现在写盘与探测前都按 URL 格式校验（Issue #1636）。
- `--job-id` 读取的缓存 JD 若以 `@` 开头会被误当文件路径展开；现在 `@file` 只适用于显式 `--jd`（Issue #1637）。
- 出口一致性检查把全部 `America/` 时区推断成美国，多伦多 / 圣保罗 / 墨西哥城被静默放行；现在只映射明确的美国时区，其余返回 unknown（Issue #1638）。
- wizard 的 `OSError` / 通用异常分支会把写请求在途标记覆盖成可重试错误，`--resume` 重放消息投递；现在保留结果未知语义（Issue #1639）。
- MCP `boss_config` 无法传空串清空 `export_dir` 等配置；现在与 CLI 一致接受空值（Issue #1640）。
- Windows 下打开文件提示用 POSIX 单引号包裹路径，`cmd.exe` 无法解析；现在用 cmd 可解析的双引号并中和 `%` 展开（Issue #1641）。

## [3.4.97] - 2026-11-18

### Fixed
- 畸形 CDP 标签 URL 的 `urlparse().hostname` 抛 `ValueError`，未派发的聊天写动作被误标为结果未知；现在解析失败按非平台页跳过，并继续扫描其它标签（Issue #1630）。
- 平台 / 认证 / 招聘者客户端残余的 `code == 0` 判断仍把布尔 `False` 当成功码；现在统一走 `is_platform_success_code`（Issue #1631）。
- CDP URL 中的 userinfo 会原样进入错误信封与 stderr 日志；现在配置拒绝带凭据的调试地址，日志只保留 scheme / host / port（Issue #1632）。
- `TokenStore` 首次生成 salt 存在跨进程竞态，可用后到 salt 破坏已保存登录态；现在用 `O_EXCL` 与刷新锁保证首写胜出、后到复用（Issue #1633）。

## [3.4.96] - 2026-11-18

### Fixed
- `ai config --temperature` 接受 `inf` / `nan` / `1e400` 并写入非法严格 JSON；现在解析阶段按 `INVALID_PARAM` 拒绝非有限数，不写配置（Issue #1623）。
- `login --curl-file` 的解析 `ValueError` 把 `INVALID_PARAM` 标成可恢复，诱导 Agent 用同一错误输入重试；现在与登记表统一为不可恢复（Issue #1624）。
- `ai local smoke` 只判真值并把类型漂移的 `ai_model` 字符串化后发请求；现在按配置损坏拒绝，不构造 AI 服务（Issue #1625）。
- `ai local pull --model ""` 启动带空模型参数的子进程；现在参数阶段拒绝，不启动下载（Issue #1626）。
- `me` 把 `TokenRefreshFailed` 误报成 `AUTH_REQUIRED` / 「未登录」；现在按 `TOKEN_REFRESH_FAILED` 返回真实语义（Issue #1627）。

## [3.4.95] - 2026-11-18

### Fixed
- MCP 入口的 minimum/maximum 错误信息直接把超大整数转成十进制文本，在 Python 位数上限下抛 `ValueError` 并让 `tools/call` 丢失 JSON 信封；现在用有界数值表示并在校验边界兜住异常（Issue #1620）。

## [3.4.94] - 2026-11-18

### Fixed
- MCP 同步兼容入口和 HR 请求数估算仍用浮点除法，`hr chat --summary-limit` / `hr last-messages --limit` 的超大整数抛 `OverflowError` 并穿过同步入口；现在统一整数分页封顶并返回标准参数错误信封（Issue #1612）。
- `boss_mark.tag` 的 schema enum 缺少 CLI 明确支持的数字 ID，而实现又接受任意子串模糊匹配；现在 enum 覆盖精确名称与数字 ID，实现只接受精确值，文档与测试同步（Issue #1615）。
- MCP `friend_ids` 等数组元素声明了 `minimum` / `maximum`，入口却只校验类型，非法整数会启动注定失败的 CLI 子进程；现在数组元素按 schema 边界在 spawn 前拒绝并指出下标（Issue #1614）。
- 向导恢复只认 `replay_safe=false`，缺该字段的 `ACTION_UNCONFIRMED` 在途写步骤会被重放；现在与 `_persisted_write_marker()` 统一口径并写回规范化标记（Issue #1617）。
- 采集三份产物逐个 `os.replace`，第三个目标失败时前两个已更新，留下混合代际；现在替换失败回滚已替换文件，全部成功后才清理备份（Issue #1616）。

## [3.4.93] - 2026-11-18

### Fixed
- 向导写请求已落盘 `replay_safe=false` 标记后，平台风控 / 预算异常会用自身的错误信封覆盖标记，`--resume` 因而重放结果未知的写操作；现在控制异常只替换原因，保留未知结果与核对指引（Issue #1604）。
- `hr resume --exchange` 的 UNKNOWN 响应因命令名在只读白名单里被改写成可重试的 `NETWORK_ERROR`，批量流程可能重复发起联系方式交换；现在按写操作分支保留 UNKNOWN，普通简历读取仍可重试（Issue #1605）。
- MCP `boss_chat` 的 schema 未声明 `output_file` 与 `format` 的绑定关系；现在传入文件时按 schema 要求同时传入格式（Issue #1606）。
- MCP `boss_shortlist_annotate` 的 schema 允许三个修改参数全空并通过入口校验；现在至少提供一个修改，空 `note` 仍表示清空备注（Issue #1607）。
- MCP 条件必填字符串（export / AI 工具的 JD 来源）漏掉 `minLength`，空串可穿过 schema 层；现在与顶层必填字符串统一边界（Issue #1608）。
- MCP `boss_export` 的超大 `count` 在超时估算中触发 `OverflowError` 并漏出内部错误；现在用整数分页估算并封顶，异常路径安全停止子进程、返回标准失败信封（Issue #1609）。

## [3.4.92] - 2026-11-18

### Fixed
- `stop.json` 只剩 `reason` 而缺少 `open` 时，`read_stop()` 原样返回，`SafetyGuard` 与 `hr location` 都认为没有停止，`resume()` 也无法解除；现在与字段类型漂移同口径 fail-closed，并允许显式恢复（Issue #1598）。
- 平台响应的 `code=false` 在 Python 中等于 `0`，采集列表 / 详情会把错误响应当成功数据，自动化的写动作会被标为 `executed`；现在统一只允许整数或整数字符串零作为成功码，布尔按形状异常收口（Issue #1599）。
- automation 配置的整数预算字段接受 `1.9`、`"3"` 等值并静默截断 / 转换，实际扫描与动作预算和操作者配置不一致；现在非整数 JSON 值一律按配置错误拒绝（Issue #1600）。
- wizard 的 `pages` 输入接受 `true` / `2.9`，`int()` 会静默变成 1 / 2 并触发真实采集；现在与 `_coerce_int` 同口径拒绝 bool 与非整数浮点（Issue #1601）。

## [3.4.91] - 2026-11-18

### Fixed
- `docs/troubleshooting.md` / `.en.md` 的错误码表未登记 3.4.90 新增的 `WORKFLOW_ALREADY_FINISHED`，按故障表排查的 Agent 会把已完成的 workflow 误判为 `run_id` 写错；现在补充该码与 `wizard --status` 结果读取指引（Issue #1589）。
- `agent run --dry-run --resume` 会在预览前执行 `store.resume()`，把操作者设置的停止标志写成「已恢复」，之后真实运行不再受停止闩锁保护；`hr location` 的恢复指引也指向了该组合。现在 CLI / MCP 都拒绝该组合，恢复指引改为 live `agent run --resume`（Issue #1591）。
- wizard 断点里的 `error.recoverable` / `error.recovery_action` 类型漂移没有被读取边界拦截，`bool("false")` 会把不可恢复错误变成可重试，数字 recovery_action 会进入信封；现在这两处与 `replay_safe` 一样按记录损坏 fail-closed（Issue #1592）。
- MCP 的条件必填字符串（wizard 的 `run_id`、config 的 `key` / `value`、hr jobs 的 `job_id`）只有条件必填声明，没有 `minLength` 边界，空串原样进入 CLI；现在条件必填字符串与顶层必填采用同一边界，入口按 schema 拒绝（Issue #1593）。
- MCP 入口没有校验 `minimum` / `maximum`，`timeout=-5` / `max_retries=100` 等越界值会进入 argv；wizard 的宿主等待也没有上限，`timeout=1e308` 可让 MCP server 近似无界等待。现在入口按 schema 校验数值边界，宿主等待封顶 900 秒（Issue #1594）。
- MCP 的 `boss_shortlist_annotate.add_tags` / `remove_tags` 虽声明了 `items.minLength: 1`，入口却只校验元素类型，空标签被拼成 `--add-tag ""`；现在逐元素按 schema 校验（Issue #1595）。

## [3.4.90] - 2026-11-18

### Fixed
- `boss agent run` 的 automation `tabs` 配置只写 `["新招呼"]`（或为空）时，`scan_conversations` 仍会调用未读列表并把未勾选会话交给执行层，且两个 tab 的候选合并后才截断 `max_per_tab`；现在严格按配置选择数据源，空配置不扫描，每个 tab 各自遵守上限（Issue #1581）。
- wizard 的 `crawl_start` 接受纯空白 `query` 并写出采集任务，而 `boss crawl run` 会拒绝同一输入；现在 `build_plan`、动作层与 `CrawlService` 三层统一拒绝空白关键词，不写库、不启动浏览器（Issue #1582）。
- 采集 profile 的 `SingletonLock` 指向超大 pid 时，`os.kill(pid, 0)` 抛 `OverflowError`，run 被记成可重试的 `stopped`，CLI 返回 `ok:true` 并给出必然再次失败的 `crawl resume` 指引；现在按无法识别的陈旧锁处理并继续（Issue #1583）。
- 列表项缺少 `securityId` 时，`fetch_detail("")` 返回伪成功空卡片，详情被标记已完成且不再出现在 pending 列表与恢复队列；现在保留 `detail_done=false`，状态标记为 `missing-security-id`（Issue #1584）。
- `boss wizard --stop <run_id>` 对已完成的任务误报 `WORKFLOW_NOT_FOUND` 并建议新建 workflow；现在返回不可恢复的 `WORKFLOW_ALREADY_FINISHED`，指引为 `--status` 查看结果（Issue #1585）。
- wizard `crawl_resume` 遇到断点中坏的 Hook 目录返回可重试的 `INVALID_PARAM`，而 CLI 的同一异常返回不可重试的 `LOCAL_IO_ERROR`；现在两条入口统一按本地状态错误收口并停止自动重试（Issue #1586）。

## [3.4.89] - 2026-11-17

### Fixed
- `boss search <关键词> --preset <名称>` 用 `or` 链合并预设，把显式空筛选（如 `--city ""`）当成「没传」并用预设值覆盖，显式传入的空城市反而按预设城市筛选；现在只有未提供（None）的项才由预设补齐，显式空串保持「该项不筛选」（Issue #1573）。
- `boss crawl stop` 对已结束的 run 也返回 `ok:true` 且 `data.status` 为 `stop_requested`，但实际什么都没停、`crawl status` 也不会出现该状态；现在只有 `queued` / `running` 接受停止请求，终态返回不可恢复的 `CRAWL_RUN_FINISHED` 并给出 `crawl status` / `crawl results` 指引（Issue #1574）。
- `boss mark --label 999`（MCP `boss_mark` 的 `tag`）把任意 ASCII 数字原样当作标签 ID 发给平台，而 schema 只列出 9 个标签；现在数字 ID 只接受与标签名一一对应的 1/2/3/4/5/6/7/8/11，其他数字按 `INVALID_PARAM` 拒绝并列出可用 ID（Issue #1575）。
- `boss_ai_resume_optimize` / `boss_ai_cover_letter` 的 MCP schema 只编码了 `jd_text` 与 `job_id` 互斥，没有编码「至少给一个」：不传 JD 来源的调用能通过 schema 但 CLI 必然失败；现在 schema 增加 `anyOf`，native 描述写明「必须且只能给一个」（Issue #1576）。
- `boss_crawl_shortlist` 的 schema 未编码 `selectors` 与 `all` 恰好一个：省略两者或同时给出都能通过 schema，但 CLI 必然返回 `INVALID_PARAM`；现在 `selectors` 带 `minItems: 1`，并增加 `anyOf` / `not` 约束（Issue #1577）。
- 全局 `--delay ""` 与 `--cdp-url ""` 被真值回退当成「没传」，静默使用配置文件里的请求区间与调试地址；现在显式空串按参数错误拒绝，未提供时才回落配置，与 `--platform ""` 同口径（Issue #1578）。

## [3.4.88] - 2026-11-17

### Fixed
- `boss shortlist compare --tag ""`（纯空白同理）被 `strip()` 后当成「没传」，返回 `ok:true` 却比较了全部候选职位；`boss shortlist annotate --add-tag ""` / `--remove-tag ""` 静默丢弃空标签，完全不给修改参数时也返回 `updated:true`。现在这些空参数与空操作都在读取缓存之前返回 `INVALID_PARAM`，`--note ""` 仍表示清空备注；MCP 的 `boss_shortlist_compare` 不再丢弃显式空 `tag`（Issue #1565）。
- `boss schema` 中 `watch run` 的 `name` 仍写「省略时运行全部」，与「必须且只能选一个」的实现矛盾；`crawl configure/run/start` 的 `--chrome-path` / `--hook-dir` 未声明空串拒绝与 profile 配对，`agent crawl` 的 `--run-id` / `--query` 互斥与 `--city` 要求也未写入。现在这些说明与 `minLength` 约束和实现一致（Issue #1566）。
- `boss agent train` 的 `--dry-run` / `--live` 是 Click 配对布尔标志，同时给出时按最后一个生效：`--dry-run --live` 会真实执行，而 schema 声称二者互斥。现在改为两个独立标志，同时给出时在登录检查与 automation 周期之前返回 `INVALID_PARAM`（Issue #1568）。
- `boss search <关键词> --preset <名称>` 中预设保存的关键词会覆盖显式位置参数，与其他筛选条件「显式优先」的方向相反；现在显式关键词优先，预设只补未提供的项（Issue #1569）。
- `boss schema` 的 `clean --privacy` 漏列 crawl 任务与导出、`export -o` 未区分 HTML 直出、`search --welfare` 声称无上限翻页、`hr resume` 的 `geek_id` / `--job-id` / `--security-id` 必填关系、`resume init` 声称可从平台简历初始化、`crawl configure` 声称支持 Hook 配置，均已按实现修正（Issue #1570）。

## [3.4.87] - 2026-11-17

### Fixed
- `boss agent crawl --run-id` 只分析已完成任务，但 `--city`、`--pages`、`--with-detail` 被整组静默丢弃，显式空 `--run-id` 也会被当成「没传」而与 `--query` 同时通过校验；现在这些参数组合在读取任务之前返回 `INVALID_PARAM`，`--run-id` 与 `--query`（含空串）按互斥拒绝，显式空 `--query` 同样在创建任务之前拒绝（Issue #1558）。
- `crawl run` / `crawl start` 的 `--hook-dir` 只有配合 `--hook-profile screenshot-full` 才会生效，但单独给出或显式为空时目录被静默忽略且任务照常创建；现在在创建任务与打开浏览器之前返回 `INVALID_PARAM`（Issue #1559）。
- `crawl configure --chrome-path ""` 经 `click.Path` 归一成当前目录，`"."` 被写入 `crawl.chrome_path`，下一次采集才在浏览器启动时失败；`crawl run --chrome-path ""` 同样把本次任务绑到当前目录。现在两者都在写盘 / 创建任务之前返回 `INVALID_PARAM`，省略选项时仍读取已保存配置（Issue #1560）。
- `boss watch run <名称> --all` 进入 `--all` 分支后静默忽略点名，运行全部 watch 并对每个 watch 发起平台请求；现在点名与 `--all` 同时给出时在打开缓存与发出请求之前返回 `INVALID_PARAM`（Issue #1561）。

## [3.4.86] - 2026-11-17

### Fixed
- `crawl` 的采集浏览器启动失败（Chrome 未安装、`chrome_path` 配错、调试端口被占用或上次保留的窗口已关闭）被 `CrawlService._run` 的通用异常分支吞成 `ok:true` 的 `stopped`，只给一条会反复失败的 resume 指引；现在按确定性本地失败返回 `CRAWL_UNAVAILABLE` 并保存断点，`crawl run/resume`、`crawl start`、`agent crawl` 与 wizard 的恢复指引检查 Chrome / `chrome_path` / 调试端口，不再只提示安装可选依赖（Issue #1553）。
- 登录态刷新锁改用内核文件锁：等待超时不再删除其他进程仍持有的 `refresh.lock`，先持有者退出时也不会删掉后来者的锁；等待期间若另一个进程已完成刷新则直接采纳新 stoken，仍拿不到锁时按「请求未发出」的可重试 `REQUEST_BUDGET_EXHAUSTED` 返回，不误导用户重新登录（Issue #1554）。
- 配置 `BOSS_EGRESS_PROBE_URL` 后出口与客户端声明的语言/时区矛盾时，`crawl run/start/resume` 与 `agent crawl` 会误报 `CRAWL_UNAVAILABLE` 安装指引（`crawl start` 还会落成 `INTERNAL_ERROR`）；现在统一返回新的 `EGRESS_CONTRADICTION`（`recoverable: false`），`error.details.egress` 列出矛盾项（Issue #1555）。
- 本地共享请求预算等待超时（其他进程占用或平台冷却未结束）被记成 `budget_stopped`，CLI 与向导提示「任务预算已用完、需要新建任务」；现在落成 `throttle_stopped`，说明本次没有发出请求并给 `crawl resume` 指引，`budget_stopped` 保留给真正的 run 预算耗尽（Issue #1556）。

## [3.4.85] - 2026-11-17

### Fixed
- `boss stats -o <路径>`（默认 `--format json`）与 `boss digest -o <路径>`（默认 json）把 `-o` 静默忽略却返回 `ok:true`，调用方以为报表 / 日报已经写出；现在在任何平台请求与本地读取之前返回 `INVALID_PARAM`，恢复指引给出 `--format html|md -o <路径>` 的可执行写法。显式空 `-o` 同样按参数错误拒绝，不再落到 `LOCAL_IO_ERROR` 或静默回退；`boss_stats` / `boss_digest` 的工具 schema 用 `if`-`then` 声明同一约束（Issue #1546）。
- `boss clean` 把 SQLite 锁冲突与只读 / 磁盘错误统一归为 `CACHE_DATABASE_INVALID`，恢复指引要求删除缓存库：库本身完好时按指引会丢掉候选池、预设与采集断点；现在锁冲突报可重试的 `CACHE_BUSY`（明确说明库文件无需删除），只读 / 磁盘错误按 `LOCAL_IO_ERROR` 上报，只有真正的内容损坏才报 `CACHE_DATABASE_INVALID`（Issue #1547）。
- `boss clean --risk-lock` 在缓存库损坏时于清锁之前失败：`--risk-lock` 是风控人工恢复入口，锁清理不读缓存库，却被无关的坏库阻断；现在只有 `--privacy` 保留「删除前先确认库可读」的预检，风险锁清理不再依赖缓存库（后续缓存清理步骤仍按真实错误上报）（Issue #1548）。
- 联系人分页遍历超过 50 页是确定性结果，但 `mark` / `exchange` / `chatmsg` / `chat-summary` 与 wizard 的联系人查找返回 `recoverable=true`，按 recoverable 决策的自动重试会再翻 50 页并消耗请求预算；现在按 `recoverable=false` 上报，`next_actions` 只给刷新沟通列表取 uid 的 `boss chat`（Issue #1549）。
- `boss detail` 收到未识别的平台响应时沿用写操作的 `UNKNOWN` 契约，要求核对「本次操作是否生效」；detail 只读，现在与 search / watch 同口径归一为可重试的 `NETWORK_ERROR`，风控码与已知错误码不变（Issue #1550）。

### Changed
- eval 运行产物不再污染工作树：`evals/results/*.json` 加入 `.gitignore`，已入库的两份样例结果保留。

## [3.4.84] - 2026-11-17

### Fixed
- wizard 的 crawl 恢复在读取断点与判断剩余工作之前就构造登录态、校验 crawl 运行时：已完成的任务或写错的 `run_id` 会先被要求登录 / 安装采集依赖，与 CLI `crawl resume` 的行为不一致；现在恢复路径把预检推迟到确认仍有剩余工作之后，新建任务仍在执行前立即预检（Issue #1540）。
- `resume export --format json -o ""` 因真值判断把显式空路径当成「未提供」，返回 `ok:true` 却不写文件；现在在任何读取之前返回 `INVALID_PARAM`，与 chat / export 的空 `-o` 口径一致（Issue #1541）。
- `resume export` 的 html/pdf 默认输出名直接拼接简历名，绕过存储层的文件名清洗：名为 `../x` 的简历会写出到当前工作目录之外，Windows 保留设备名同样不生效；现在默认名与读写路径共用 `_safe_filename`（Issue #1542）。
- `BROWSER_KERNEL_MISSING` 与 `CRAWL_UNAVAILABLE` 仍进入 wizard 的自动重试：内核没装或采集依赖缺失时每次重试都会再起一次浏览器并得到同一结果；现在两者标记 `auto_retry=false`，修复后由调用方手动恢复（Issue #1543）。

## [3.4.83] - 2026-11-17

### Fixed
- `INVALID_PARAM` 同时承载「参数错误，不可重试」与「本地配置损坏，修好后可重试」两种语义：`agent` / `ai` / `ai local smoke` / `chat --export` 与 wizard 的 AI、crawl 配置损坏分支都用 `INVALID_PARAM` + `recoverable=true` 覆写，按 `recoverable` 决策的 Agent 会在未修复配置时反复重跑同一失败；wizard 的 `_classify_action_error` 还把纯参数错误标成可自动重试。现在新增 `CONFIG_INVALID`（`recoverable=true`，指向 `config reset` / `ai config` 等修复命令），`INVALID_PARAM` 保持不可重试，wizard 对确定性失败不再自动重试（Issue #1525）。
- MCP 工具 schema 没有携带 native 已声明的枚举取值域：`boss_mark.tag`、`boss_batch_greet` / `boss_preset_add` / `boss_watch_add` 的 `industry` / `scale` / `stage` / `job_type` 只有类型与描述，MCP-only Agent 只能猜值；现在枚举与 `_LABEL_MAP`、Click `Choice` 逐值相等，并新增守卫测试（Issue #1528）。
- `boss platforms --capability` 的 schema choices 只管实现集合里的 11 个能力，漏掉 `show` / `history` / `interviews`；现在实现与 schema 共用 `capabilities.py` 的能力分组，choices 与实收集合相等（Issue #1530）。
- `boss ai cover-letter` / `ai resume-optimize` 同时传 `--jd` 与 `--job-id` 时静默用缓存 JD 覆盖显式 JD，生成结果与调用方预期不符且无任何提示；现在入口返回 `INVALID_PARAM`（`recoverable=false`），工具 schema 用 `not` 互斥声明同一约束（Issue #1529）。
- `boss agent run --dry-run` 的 MCP 缺省是 `true`（安全演练），与 native schema 声明的 `false` 相反且两处都没说明；现在工具描述、`dry_run` 参数描述与 native schema 都写明「MCP 缺省演练，真实执行需显式 `dry_run=false`」（Issue #1531）。
- MCP 工具的条件必填与互斥只写在描述里：`boss_export` 的 `query` / `url` 至少一个、`boss_hr_jobs` 的 `online` / `offline` 需要 `job_id`、`boss_config` 的 `get` / `set` / `reset` 需要 `key`、`boss_wizard` 的 `resume` / `status` / `stop` 需要 `run_id` 都不在 JSON Schema 里，客户端会先认可一个必然失败的调用；现在用 `anyOf` / `if`-`then` 声明（Issue #1532）。
- MCP 工具里 required 的字符串没有 `minLength`：`boss_greet` / `boss_apply` 的标识、`boss_hr_reply.message`、`boss_search.query`、`boss_preset_add` / `boss_watch_add` 的 `name` / `query` 等空串先通过 schema 校验才由 CLI 拒绝；现在目录统一为 required 字符串派生 `minLength: 1`，入口按同一约束拒绝（`hr greet.lid` 是唯一例外，平台可能给空值）（Issue #1533）。
- MCP 工具漏抄 native schema 的默认值：`boss_agent_review_reject.reason`、`boss_ai_cover_letter.tone` / `lang`、`boss_favorites_list.page`、`boss_wizard.max_retries` 都没有 `default`，客户端无法得知 effective default；`boss_batch_greet.limit` 的 MCP 默认 5 与 native 10 的差异保留并在描述与测试中锁定（Issue #1534）。
- `agent` 的 `availability.recruiter_platforms` 写的是 `zhipin`，与 `hr` 和顶层 `supported_recruiter_platforms` 的招聘者注册表名 `zhipin-recruiter` 混用同一字段语义，Agent 无法据此选择平台标识；现在说明它是自动化适配器键（等于 `--platform` 取值），不是 `--platform` 之外的注册表名（Issue #1535）。
- native schema 内嵌的 `commands.crawl.mcp_tools` 缺少 `additionalProperties: false` 与可用性说明，与 `--format mcp-tools` 导出的同名工具不一致；现在 native 输出阶段复用同一份规范化结果，内嵌副本与导出逐字段相等（Issue #1536）。
- MCP 侧缺少能力真源入口：只接 MCP 的 Agent 无法读到命令、枚举与错误码；新增只读工具 `boss_schema`（MCP 工具数 79 → 80），映射到 `boss schema [--format …]`（Issue #1528）。

## [3.4.82] - 2026-11-17

### Fixed
- 招聘自动化的队列派发不读 `state.json` 的会话在途标记：扫描路径发送后进程被杀留下的未确认动作，会被同候选人的已批准队列行再发送一次。现在队列派发前按与扫描路径相同的 fail-closed 口径检查标记，命中时不发送，`verification-required` 状态只跳过该候选人的队列行，其余情况打开熔断，队列行保持 `pending` 供人工核对后继续（Issue #1514）。
- 队列行发送后被中断、转成 `verification-required` 时只改队列文件，不写 `state.json`；`--resume` 后扫描路径看不到这条结果未知的动作，会按 transcript 再发一次。现在队列行转 `verification-required` 时同步写入同一份会话在途标记，两条路径共享同一份状态（Issue #1515）。
- 队列成功发送后先落 `executed` 再写 state checkpoint：checkpoint 写盘失败（磁盘满 / 控制锁）时队列行已推进、prior 丢失，下一轮扫描路径会再次发送。现在先持久化 state（prior 与在途标记）再推进队列行；state 未落盘时队列行保持 `executing`，下一轮按结果未知转 `verification-required`，不重发（Issue #1516）。
- 扫描路径先把标记推进到 `verification-required` 再打开熔断：两步之间进程被杀或熔断落盘失败时，磁盘上只有标记没有 `stop.json`，后续周期把「已通知操作者」误当成成立并静默跳过。现在标记记录 `notified` 位，只在熔断成功落盘后置位；未通知的标记下一轮会重开熔断（旧版本写下的标记按已通知处理，保持兼容）（Issue #1517）。
- 只读命令（`search` / `chat` 等）拿到无法识别的平台响应时沿用写操作的 `UNKNOWN` 契约：`recoverable=false`、`details.replay_safe=false`，恢复文案要求核对官方页面，Agent 因此拒绝本可安全执行的读取重试。现在按命令的读写属性归一：只读命令报 `NETWORK_ERROR` 并允许重试，写命令与未知命令保持不可重放（Issue #1519）。
- `agent run` 在请求控制点观察到熔断时抛 `WORKFLOW_STOPPED`，错误信封带 wizard 的「创建新 workflow」文案与预算冷却提示。现在按 `CIRCUIT_BREAKER_OPEN` 上报，`next_actions` 给出带 `--data-dir` / `--role recruiter` 的 `agent stats`，真人指引指向核实后 `agent run --resume`（Issue #1520）。
- wizard 的联系人缺少 `securityId` 分支返回 `recoverable=true`，`max_retries≥1` 时会自动重跑同一段确定性失败的查询；现在与命令层一致按不可自动重试上报，恢复指引要求先刷新沟通列表（Issue #1521）。
- 风控信封把「不要通过 CDP 重试」这类真人说明放进了 `hints.next_actions`，而 TTY 只渲染 `operator_actions`，真人在终端反而看不到；现在 `next_actions` 只保留 `boss doctor` 等可执行命令，说明性条目移入 `operator_actions`（Issue #1522）。
- MCP 只读工具白名单漏掉 `boss_chat_summary` / `boss_agent_stats` / `boss_agent_pending` / `boss_agent_review`，超时或取消被标成结果未知的写操作；现在这些纯读工具按可重试处理（Issue #1523）。
- `recovery_action` 里的 `boss agent …` 示例命令经登记表改写后丢失 `--role recruiter`，与同信封 `next_actions` 不一致；现在 agent 命令统一保留当前角色（Issue #1524）。

## [3.4.81] - 2026-11-17

### Fixed
- `clean` 的 `_clean_dir` / `_clean_path` 跟随目录符号链接：`chat-history` / `chat-export` 指向数据目录外时，按 mtime 清理会删掉链接目标里的文件；`--privacy` 先删除登录态、再在目录链接上抛错，留下半完成状态且错误信封 `data=null`。现在 `_clean_dir` 的目录参数、`_clean_path` 的目录链接或目标祖先目录是符号链接时一律跳过，并在结果中用 `skipped_symlinks` 与 `warning` 说明（文件链接仍只删除链接本身，不碰目标）；隐私清理在删除前先做只读预检，失败时错误信封的 `data` 带回已完成 / 失败 / 未处理项（Issue #1497）。
- `doctor` 的写探针固定写 `auth/.doctor-write-test` 再 `unlink`，该路径是符号链接时会写穿目标并删除链接。现在使用唯一临时文件（`mkstemp` 的 `O_CREAT|O_EXCL`，可用时带 `O_NOFOLLOW`），只清理本次创建的文件；写失败按 `LOCAL_IO_ERROR` 出错误信封，并保留已完成的检查项（Issue #1498）。
- `boss ai config` 用真值判断「是否提供了更新」：显式空 `--model` / `--provider` / `--base-url` 被当成没传而退回查看模式，`--api-key ""` 还会把已存密钥覆盖成空串，查看输出却仍报 `api_key_set=true`。现在四类字符串选项一律用 `is not None` 判断，并在任何写盘之前返回 `INVALID_PARAM`；新增 `--clear-api-key` 显式清除凭据（与 `--api-key` 互斥），`api_key_set` 按真实存储状态报告，空串与纯空白视为未设置（Issue #1499）。
- `boss ai local configure --model ""` 会把空模型名写入配置，`ai local status` 随即变成 `configured=false`；`--base-url ""` 被 `or` 静默回退到运行时默认端点。现在两者都在写盘之前返回 `INVALID_PARAM`，只有省略 `--base-url` 才使用默认地址（Issue #1500）。
- 显式空 `-o ""` 被真值判断当成「未提供」：`boss chat --export md -o ""` 静默写出默认导出文件，`boss export --format html -o ""` 直接向 stdout 输出裸 HTML，`--format csv -o ""` 不写文件却返回成功；现在 chat / export 在任何平台请求前返回 `INVALID_PARAM` 并说明省略 `-o` 才使用默认路径 / stdout，MCP 映射层也把空串原样下发给 CLI 校验（Issue #1501）。
- `boss chat -o <路径>` 没配 `--export` 时路径被静默忽略，命令仍返回 `ok:true` 与空列表，调用方以为文件已写出；现在入口返回 `INVALID_PARAM`（`-o` 需要配合 `--export`），schema 中 chat 的选项说明同步标注该约束（Issue #1502）。
- `boss hr download-resume` 把本地写盘 `OSError`（磁盘满、只读目录、权限不足）报成可修正参数的 `INVALID_PARAM`；现在保留 `FileExistsError` 的 `INVALID_PARAM` 分支，其余按不可重试的 `LOCAL_IO_ERROR` 上报，恢复指引包含磁盘空间与目录权限（Issue #1503）。
- 联系人按 uid 命中但缺少 `securityId` 时被报成可重试的 `NETWORK_ERROR` + 「重试」，却没有后继指引；现在与缺少 uid 的分支同口径，按 `recoverable=false` 上报，并在 `hints.next_actions` 给出带 `--data-dir` 的 `boss chat` 刷新命令（Issue #1504）。
- `boss schema` 把 `chat -o` 的默认目录写成不存在的 `~/Documents/files/boss`，而实现未配置 `export_dir` 时回退到 `<data-dir>/chat-export`，且 `-o` 仅在 `--export` 时生效；现在 schema 与 `chat --help` 都按实现描述默认路径与生效条件（Issue #1505）。
- `boss schema` 的 `mark --label` 封闭枚举漏掉实现支持的 `牛人发起`，也未说明接受数字 ID；现在 enum 覆盖 `_LABEL_MAP` 的全部标签，并注明「标签名称或数字 ID」（Issue #1506）。
- 简历文件名清洗只替换 `/`、`\` 与 NUL，Windows 上 `name="D:evil"` 生成的 `D:evil.json` 与 resumes 目录拼接后会脱离 base；现在 `:` 一并替换，`CON` / `NUL` / `COM1` 等保留设备名（含带扩展名形式）与纯点号名称同样改写，`resume import` 对不安全名称直接返回 `INVALID_PARAM`（Issue #1507，待 Windows 实机验证）。
- 招聘自动化直接扫描路径在派发写动作前没有把在途动作落盘：发送后进程被杀或 checkpoint 撞控制锁时没有记录，下一轮凭 transcript 重新决策，本地 AI 改写后的问卷匹配不到模板会再次发送；现在派发前把 `inflight_action`（动作、消息 hash、时间、会话 ref）写进 `automation/state.json`，成功后与阶段字段原子清除，重放时转 `verification-required` 并给出核对指引，只有确定未发出的异常才释放标记（Issue #1511）。
- 求职者 `greet` / `apply` 的去重是跨进程 check-then-act：两个进程同时通过 `is_greeted` / `is_applied` 检查后会各发一次真实请求；现在发送前按 `(账号, job_id, 动作)` 在 SQLite 里原子预约，抢不到时已成功回 `ALREADY_*`、发送中回不可重试的 `ACTION_UNCONFIRMED`，CLI `greet` / `apply` / `batch-greet` 与 wizard 的 candidate_greet / candidate_apply 共用同一收口，旧记录表继续参与去重（Issue #1508）。
- `watch` 命中写入先 `SELECT` 再 `INSERT`，两个进程对同一个 watch 并发记录时第二个会撞主键抛 `IntegrityError` 并被误判为缓存库损坏；现在改为 `INSERT OR IGNORE` + `UPDATE`，新增 / 已见计数语义不变（Issue #1509）。
- `shortlist_records`、`crawl_runs`、`crawl_jobs` 的旧库迁移没有事务，双进程首次打开会各自执行 `ALTER TABLE`，第二个拿到 `duplicate column name` 并被误判为库损坏；现在与去重键迁移一致，先 `BEGIN IMMEDIATE` 拿写锁再重读 `table_info`，真实迁移失败（类型不符、唯一索引冲突）仍原样上抛（Issue #1510）。

## [3.4.80] - 2026-11-17

### Fixed
- MCP 子进程固定 120s 超时让 `batch-greet` 的默认 5 次招呼（每次节流上界 60s）必然超时，SIGINT 后 stdout 为空、结果未知且没有可恢复的部分结果；现在按 argv 估算超时（1 次搜索 + `--count` 次招呼，单次 60s 节流上界 + 5s 抖动 + 30s 余量，封顶 900s），CLI 在批打招呼被中断时输出已完成列表、在途候选人与 `replay_safe=false` 的七键错误信封，并明确不要原样重试（Issue #1491）。
- MCP 只读工具（`search` / `export` / `hr chat` / `hr last-messages` / `pipeline` / `follow-up` / `digest`）同样固定 120s，详情筛选与分页批量调用会在正常执行中被误杀；现在按参数估算超时（单次 65s + 30s 余量，封顶 900s，解析失败回退 120s），只读中断文案改为先降低 limit/count 或改用 active=online 再重试（Issue #1492）。
- MCP 的 `boss_wizard` / `boss_config` / `boss_hr_jobs` 按 action 分派时静默丢弃越界参数：`resume` 的新 `goal`、`config list` 的 `key`、`hr jobs online` 的 `page` 等被忽略后仍返回 `ok:true`，调用方以为过滤 / 恢复 / 写入已经生效；现在映射层按 action → 允许键矩阵返回 `INVALID_PARAM` 并提示正确用法，缺省 action 与显式默认 action 行为一致，`run_id` 必须显式搭配 `resume` / `status` / `stop`（Issue #1493）。
- `boss batch-greet` 的空串 / 纯空白关键词会退化成无关键词搜索，并对默认列表里的职位真实发出招呼；现在在任何平台调用之前返回 `INVALID_PARAM`，工具 schema 的 `query` 同步声明 `minLength: 1`（Issue #1490）。
- `boss batch-greet --count` 超过 10 时被静默截断，CLI 行为与 schema 声明的上界不一致；现在 `--count` 改为 `IntRange(0, 10)` 在解析阶段拒绝越界值，MCP `limit` 越界同样返回 `INVALID_PARAM`，原生 schema 补上 `maximum: 10`（Issue #1494）。

## [3.4.79] - 2026-11-17

### Fixed
- 静默刷新 stoken 成功但登录态落盘失败（权限 / 磁盘满）时，本地 `OSError` 被包装成可恢复的 `TokenRefreshFailed` 并提示重新登录；现在原样上抛，按不可重试的 `LOCAL_IO_ERROR` 上报（Issue #1479）。
- CDP 登录成功但登录态落盘失败时被当成 CDP 失败并继续启动扫码链路，原始本地权限问题被扫码超时覆盖；现在保存动作不再参与降级判定，直接上抛 `LOCAL_IO_ERROR`，CDP 自身不可达时仍降级扫码（Issue #1480）。
- wizard 在写请求发出后按 Ctrl-C 会用 `WORKFLOW_STOPPED` 覆盖已落盘的 `ACTION_UNCONFIRMED` 标记，checkpoint 丢掉 `replay_safe=false` 与该动作的核对命令，`--resume` 会重放结果未知的写操作；现在保留该标记并把步骤落成失败，标记损坏或 `replay_safe` 类型漂移时同样按结果未知处理（Issue #1478）。
- `params.requested_steps` 为空数组的 workflow 断点在 `--resume` 后不执行任何步骤却报 `completed`；现在读取边界按 `CACHE_RECORD_INVALID` 拒绝并提示重新创建任务（Issue #1483）。
- 风险锁文件写入改用统一的原子 JSON 写入（`backslashreplace`），`cdp_url` 含孤立代理字符时不再抛 `UnicodeEncodeError`；记录风控的路径同时按 `(OSError, UnicodeError)` fail-closed，原始 `PlatformRiskError` 不会被本地编码错误替换成可重试的 `NETWORK_ERROR`（Issue #1487）。
- 代理环境变量非空但解析不出 host（如 `HTTPS_PROXY=http://`、`http://:8080`）时，浏览器通道以 `proxy=None` 启动 Chromium 走真实出口，与挂载坏代理的 httpx 通道不一致；现在 `resolve_proxy` 抛 `ProxyConfigurationError`，登录链路、装饰器命令与 doctor 在线探测统一按 `INVALID_PARAM` 拒绝，不再静默回退直连（Issue #1486）。
- 招聘者聊天动作读取 `friend_detail` 失败时丢弃平台码，401 / 9 / 37 全部退化成 `UNKNOWN` 并提示提交 issue；现在保留原响应交给平台 `parse_error`，分别映射 `AUTH_EXPIRED` / `RATE_LIMITED` / `TOKEN_REFRESH_FAILED`。`friend_detail` 成功但没有唯一匹配时返回不可重试的 `CONTACT_NOT_FOUND`，`hints.next_actions` 指向同一数据目录的 `hr chat`（Issue #1485）。
- 采集执行期间另一个进程短暂持有 SQLite 写锁时，`CacheBusyError` 被通用分支吞成 `stopped`，命令却返回 `ok:true`；现在原样上抛，按可重试的 `CACHE_BUSY` 上报（Issue #1477）。
- `crawl` 把 `--data-dir` 相对路径原样存进断点，在另一个目录执行 `crawl resume` 会把产物写到当前工作目录，`output_dir=''` 时同样落到 `.`；现在创建时持久化绝对路径，读取时按 `CACHE_RECORD_INVALID` 拒绝相对或空值（Issue #1481）。
- 断点里的 `profile_path` 含 NUL / 控制字符时 `Path.resolve()` 抛 `ValueError`，被泛化分支记成 `stopped` 并返回 `ok:true`；现在读取边界按 `CACHE_RECORD_INVALID` 拒绝，transport 层同时把 `ValueError` 转成 `CrawlProfilePathInvalid`（Issue #1482）。
- `hook_results.success` 用 `bool()` 归一，字符串 `"false"` 会被报告成成功；现在要求严格布尔，类型漂移按 `CACHE_RECORD_INVALID` 拒绝（Issue #1484）。

## [3.4.78] - 2026-11-17

### Fixed
- `me` 命令把本地缓存锁冲突与数据库错误兜成可重试的 `NETWORK_ERROR`；现在分别按 `CACHE_BUSY`、`CACHE_DATABASE_INVALID`、`LOCAL_IO_ERROR` 上报（Issue #1464）。
- `resume delete` 先删除简历文件再清理职位关联，缓存失败会留下悬挂关联并让重试得到 `RESUME_NOT_FOUND`；现在先清理关联，成功后才删除文件（Issue #1465）。
- 候选池批量导入刷新 `security_id` 时可能撞上 `(security_id, job_id)` 主键并整批回滚；现在冲突时删除旧重复行、保留目标行（Issue #1466）。
- 缓存取数把只读目录、磁盘满与设备 IO 故障归成 `CACHE_DATABASE_INVALID` 并提示删库；现在保留原始错误，由 CLI 与 wizard 按 `LOCAL_IO_ERROR` 上报（Issue #1467）。
- `favorites sync` 遇到成功包络但 `zpData` / `cardList` 形状漂移时报不可重试的 `UNKNOWN`；现在返回可重试的 `NETWORK_ERROR`（Issue #1468）。
- automation 的 `ChatTabCdpUnreachable` / `RecruiterChatTabRequired` / `BrowserSourceUnavailable` 发生在请求发出前，却会把队列行永久留成 `executing`；现在先还原 `pending` 再抛出（Issue #1469）。
- 风控 / 预算闩锁写入撞上控制锁时会覆盖原始异常并误报 `AUTOMATION_BUSY`；现在保留原始错误，详情标记 `risk_latch_persisted=false`，异常周期不再写 state（Issue #1470）。
- MCP server 的显式空 `--data-dir` / `--boss-bin` 被真值判断静默丢弃并回落默认值；现在原样传给 CLI，由参数校验拒绝（Issue #1471）。
- `boss_hr_last_messages` 的 `friend_ids.items` 缺少 `minimum: 1`，与 CLI 的 `IntRange(min=1)` 不一致；现在补上下限（Issue #1472）。
- 登录错误提示的 `next_actions` 含不存在的 `boss-chrome` 命令；现在移除，并把 Chrome 调试端口指引移到 `operator_actions`（Issue #1473）。
- 深层本地数据会让脱敏递归溢出，stdout 变成 0 字节并丢失七键信封；现在脱敏有深度上限，信封出口捕获 `RecursionError` 回退固定错误信封（Issue #1474）。

## [3.4.77] - 2026-11-17

### Fixed
- 扫码登录在最终读取时不再校验主 cookie：页面跳转后读取为空也会报登录成功并覆盖本地登录态；现在空结果直接失败，凭证不落盘（Issue #1454）。
- CDP HTTP 端点不可达时聊天页动作被报成 `ACTION_UNCONFIRMED`（结果未知、禁止重试）；现在抛出可区分的 `ChatTabCdpUnreachable`，按「动作尚未发送」报告 `CDP_UNAVAILABLE`，重新执行是安全的（Issue #1455）。
- 聊天页返回平台码 37（登录态失效）时被改写成 `ENVIRONMENT_RISK` 并提示不存在的风险锁；现在按 `TOKEN_REFRESH_FAILED` 报告并要求重新登录，`page_risk` 保留原始码（Issue #1456）。
- 登录成功但本地登录态保存失败（权限 / 磁盘满）被报成可重试网络错误；现在按 `LOCAL_IO_ERROR` 报告并指向目录权限与磁盘空间（Issue #1457）。
- 只读请求导航恢复超时时返回裸 `code=-1` 并被判为 `UNKNOWN` + 「勿重试」；读取没有副作用，现在返回可重试的 `NETWORK_ERROR`（Issue #1458）。
- `agent run --dry-run` 命中安全提示时会把熔断写进 `stop.json`，一次无害预览冻结后续真实运行；现在预览不落闩锁、不改写待执行队列，报告仍显示 `CIRCUIT_BREAKER_OPEN`（Issue #1459）。
- `crawl resume` 读到损坏的 Hook 目录被报成 `INVALID_PARAM` 并提示无法修复的 `crawl configure`；现在按不可重试的 `LOCAL_IO_ERROR` 收口，指向修复 7 个脚本与 `SHA256SUMS` 或用新 `--hook-dir` 重建任务（Issue #1460）。

### Changed
- `evals/run_eval.py --mode fixture` 不再要求独立终端：该模式纯本地评分，不启动子进程、不访问平台、不需要 TTY，可直接在 Agent 会话内运行；只有 `--mode external` 启动外部 runner 时仍需独立终端。同步修正 CONTRIBUTING、评测 README 与维护者文档。
- 发布节奏改为批量：修复累积到 `[Unreleased]` 后按批次发布，不再每个 PR 合并即发一个补丁；补丁号不设上限。`docs/maintainer/release-checklist.md` 新增发布节奏一节。

## [3.4.76] - 2026-11-17

### Fixed
- 子命令前有带取值的全局选项（`boss --data-dir <dir> detail -<sid>`）时，security_id 保护落在选项取值上并整体失效；CLI 自己生成的 `hints.next_actions` 就是这种形态。现在按 Click 参数元数据跳过选项及其取值、递归定位叶子命令（Issue #1447）。
- 位置参数在选项之前（`boss detail -<sid> --job-id <jid>`）时，补出的 `--` 把其后的选项吞成额外位置参数；现在把位置参数整体后移，选项及其取值保持原顺序（Issue #1448）。
- 选项取值以横杠开头且形似 security_id 时（`--security-id -<sid>`、`export -o -2024-report-…`）被误当位置参数，`--` 插到了选项与取值之间；现在选项取值不参与改写（Issue #1449）。
- `config.json` 的 `operating_mode` 被改成数组 / 对象后 `not in` 抛 `TypeError`，所有命令（含 `config reset`）在启动期失败；现在与 `"bogus"` 等取值同口径回退（Issue #1450）。
- 登录态里的 User-Agent 一直透传到采集会话却从未应用到 `ChromiumOptions`，新起的采集浏览器与后端登录态指纹不一致；现在注入同一 UA（Issue #1413）。
- 联系人分页上限的 message 结尾「请重试」与同一信封的 `recovery_action` 矛盾，只按错误码决策的 Agent 会重放最多 50 页；现在指向稳定 uid 查询（Issue #1451）。

## [3.4.75] - 2026-11-17

### Fixed
- CDP 探测（`/json/version`）返回非对象 JSON（`[]` / `null` / 标量）时 `resp.json().get` 抛 `AttributeError`，在登录降级链之外直接中止；现在按「拿不到调试端口」处理，继续既有降级（Issue #1421）。
- `login_via_cdp` 的握手与 `browser.contexts` 读取在 `try` 之外，失败时泄漏 Playwright driver；现在纳入统一收尾，失败即 `pw.stop()`（Issue #1425）。
- 请求预算与采集节流的有限远未来时间戳让等待永久无法自愈；现在按 `now + interval + 60s` / 1 小时冷却上限判定漂移并写回自愈（Issue #1422）。
- 非法代理端口（`abc` / `99999` / 显式 `:0`）抛裸 `ValueError` 并落到可重试网络错误；新增 `ProxyConfigurationError`，登录 / `me` / wizard / 通用装饰器统一映射 `INVALID_PARAM`，文案不含代理凭据（Issue #1423）。
- CDP `/json` 返回对象或混入非对象元素时，`_pick_chat_target_ws` 抛 `AttributeError` 被当成「结果未知」；现在打开 WebSocket 前收口为 `RECRUITER_CHAT_TAB_REQUIRED`（Issue #1424）。
- `ai_model` 被手改成数组 / 数字后服务层 `rsplit` 崩溃，所有 `boss ai *` 命令落 `INTERNAL_ERROR`；现在构造前校验字符串项，报 `INVALID_PARAM` 并给出 `ai config --model` 修复指引（Issue #1428）。
- `--base-url` 接受空白串 / 缺 scheme / 非 http(s) 地址，之后每条 AI 命令抛 `UnsupportedProtocol` 并被报成可重试错误；现在写入与解析端点两侧都校验（Issue #1431）。
- 简历 HTML/PDF 模板对 rows 自由字段直接迭代：数字 `content` 抛 `TypeError`、字符串 `tags` 被逐字符渲染、字符串 `columns` 崩溃；现在按 `resume_to_text` 同口径归一（Issue #1429）。
- PDF 导出绕过符号链接拒绝，静默替换 `-o` 指向的链接；现在与原子写同口径拒绝（Issue #1434）。
- `resume export --format json` 写出裸 `NaN` / `Infinity`，严格解析器拒绝整份文件；现在与 stdout 信封同口径归一（Issue #1435）。
- 招聘者 `parse_error` 未走通用兜底：HTTP 401 合成响应与数字字符串码（`"9"` / `"121"`）全部退化为 `UNKNOWN`；现在共用 `classify_platform_error`（保留消息关键词关闭，`code=-1` 仍为 `UNKNOWN`），401 → `AUTH_EXPIRED`（Issue #1430）。
- `match_score` 与学历 / 经验阈值对平台字段类型漂移直接消费：`skills` 混入数字、期望字段为数组时抛 `TypeError` 落 `INTERNAL_ERROR`；现在逐项归一、坏字段按无此维度处理（Issue #1432）。
- `ai local import --model registry.json` 会把登记表路径建成目录，登记表自此不可用；现在拒绝与登记表同名的模型名（含大小写变体）（Issue #1433）。
- `mark --label` 的 4301 位以上数字串让 `int()` 抛裸 `ValueError`，被兜成可重试网络错误；现在报 `INVALID_PARAM` 且消息截断（Issue #1437）。wizard `candidate_mark` 与回退菜单同口径（Issue #1442、#1438）。
- `mark` 把漂移的 `friendSource` 原样发给标签写接口，发出注定失败的写请求；现在与向导同口径按 0 处理（Issue #1439）。
- `resume init --template` 接受任意值却固定生成默认模板、成功信封还回显未生效的模板名；现在只接受 `default`（Issue #1440）。
- `digest` / `stats` / `resume export` 的提示写死 macOS 的 `open`；现在按平台生成 `open` / `cmd /c start` / `xdg-open`（Issue #1441）。

## [3.4.74] - 2026-11-17

### Fixed
- 采集浏览器的 Cookie 注入失败被静默吞掉，平台返回登录页后误报 `CrawlRiskError` 并写入 `cdp_risk_lock`，要求人工清锁；现在 `_seed_session_cookies` 返回注入明细，失败时抛本地认证错误并映射 `AUTH_REQUIRED`（指引 `boss login`），不写风险锁。真实安全页仍 `risk_stopped` 并保留浏览器与风险锁（Issue #1405）。
- `crawl resume` 不做 Hook 预检、相对 `--hook-dir` 被原样写入断点，换 cwd 或删改 hook 后要等浏览器导航才报错且 CLI 仍 `ok:true` 提示无限 resume；现在 resume 前校验 hook 清单，断点存绝对路径，`HookRegistrationError` 映射不可恢复的 `LOCAL_IO_ERROR`（Issue #1408）。
- 导出目录被同名文件占用时 `mkdir` 的 `FileExistsError` 落在异常转换之外，被记成普通 `stopped` 并无限 resume；现在转换为 `CrawlExportError` → `LOCAL_IO_ERROR`（Issue #1409）。
- 对已完成的 crawl run 执行 `resume` 仍先要求登录；现在登录态按需读取，`crawl run` 仍提前预检，`resume` 在确认有续跑工作后才预检，已完成 run 直接返回结果（Issue #1412）。
- automation 队列文件不可读（权限/目录/IO）被当作空队列，`agent pending list` 返回 `ok=true items=[]`、`agent run` 静默跳过已批准动作；现在只有 `FileNotFoundError` 视为空队列，其余 fail-closed 报 `AUTOMATION_STATE_INVALID` 并带文件路径与修复方式（Issue #1406）。
- automation 的 pending `status` 为数组/对象时抛 `TypeError` 被兜成 `INTERNAL_ERROR`；现在先校验字符串类型，按状态损坏报 `AUTOMATION_STATE_INVALID`（Issue #1410）。
- `agent crawl` 在校验 `--resume` 之前完成整轮采集与短名单导入，`--query` 与 `--run-id` 两条路径都会先产生平台请求与本地写入；现在简历检查前移，空名/缺失/损坏分别报 `INVALID_PARAM`、`RESUME_NOT_FOUND`、`RESUME_FILE_INVALID`（Issue #1407）。
- `agent crawl` 的简历校验前移后又越过了风险锁检查：已记录 `cdp_risk_lock` 时会先报 `RESUME_NOT_FOUND`，引导用户补完简历后继续触发平台风控；现在 `--query` 路径先检查风险锁（`ENVIRONMENT_RISK_LOCKED`），`--run-id` 只读本地结果与短名单，仍不受锁影响（Issue #1419）。
- 复核/待办 TTY 列表列键用了不存在的 `review_id` / `score`，两列恒显示 `-`；改为 `id` / `confidence` 并加模型字段守卫（Issue #1411）。

## [3.4.73] - 2026-11-17

### Fixed
- wizard 的 run/step `status` 只认 `WorkflowStatus` 枚举：NULL / 非字符串 / 未知值此前能绕过 resume 去重（写操作会被重放），现在 `get_checkpoint` 与 `list_recent` 统一报 `CACHE_RECORD_INVALID`；`SearchPipelinePlatformError` 不再被兜成 `NETWORK_ERROR`，保留 AUTH_EXPIRED / RATE_LIMITED / 风控码的原语义与恢复指引（Issue #1382、#1384）。
- wizard 的 `welfare_conditions` 文档形状（`list[str]`）被逐字符拆包导致静默错误筛选；入口统一归一为 `(label, keywords)`，非法形状报 `INVALID_PARAM`（Issue #1383）。
- wizard 的 `--timeout` 接受 `nan` / `inf`，超时保护被静默关闭；现在要求有限正数并报 `INVALID_PARAM`。`--status` 的 TTY 恢复命令按 `--data-dir` 改写（Issue #1385、#1386）。
- 登录风控信封把 `LOGIN_RISK_CONTROL` 报成 `recoverable=true` + 「重试登录」，`display.py` 又只在字段为 `None` 时按登记表补齐；现在按 `ERROR_CODES` 取 `recoverable=false` 与登记表 recovery，`next_actions` 保持为空（Issue #1390）。
- `boss doctor` 的登录态结论 `stoken=存在/缺失` 被自由文本脱敏替换成 `[REDACTED]`；改为 `stoken_present=...`，真实 token 仍被脱敏（Issue #1393）。
- stdout 与 MCP 输出遇到非有限浮点会写出 `NaN` / `Infinity`，严格 JSON 解析器拒绝整份信封；现在递归归一为 `null`（超长整数同样归一），并以 `allow_nan=False` 兜底，正常输出逐字节不变（Issue #1391）。
- MCP 工具 schema 的 `minimum` / `maximum` 与 Click 参数漂移（`timeout=0`、`count=0`、`days=-1` 先被放行再由 CLI 拒绝）；现在以 Click 为真源补齐边界，并新增双向守卫测试防止再次漂移（Issue #1392）。
- Rich 表格标题直接拼接用户查询词 / 联系人名 / `--data-dir` 路径，`[/bold]` 这类标记会让 TTY 输出崩溃并被兜成可重试 `NETWORK_ERROR`；三个渲染器与空列表路径现在转义标题，`MarkupError` 按不可恢复的 `INTERNAL_ERROR` 分类（Issue #1399）。
- 磁盘满等 fd 级写失败抛出的 `OSError` 没有 `filename`，被 `is_local_io_error` 判成网络错误并提示重试；原子写现在补上目标路径，按 `LOCAL_IO_ERROR` 报告（Issue #1400）。
- Windows 上职位 CSV 导出被文本层二次翻译，每行变成 `\r\r\n`；CSV 分支改用 `newline=""` 落盘（Issue #1401）。
- 联系人接口 `result` 为非空坏元素数组时不再回退合法的 `friendList`，整页被静默当作 0 条，导出还会把空页写成完整快照；现在只要取不出 dict 元素就回退（Issue #1402）。
- 预筛日志把职位薪资与用户期望薪资拼进 stderr，CodeQL 按明文记录私有数据告警；日志原因改为定性描述，数值仍保留在输出 data 里（Issue #1394）。

## [3.4.72] - 2026-11-17

### Fixed
- wizard 遇到缓存库损坏（含取数阶段才暴露的坏页）时被兜成可重试的 `NETWORK_ERROR`，Agent 会在同一坏库上无限重试。`wizard/runner.py` 现在输出 `CACHE_DATABASE_INVALID`（`replay_safe=false`），TTY 补齐对应文案（Issue #1362）。
- 简历 `modules[].rows[]` 的 `content` / `tags` 类型漂移：`content: 123` 抛 `TypeError`，`tags: "abc"` 被拆成 `a, b, c`；`agent crawl` 里该异常被兜成可重试 `NETWORK_ERROR`（重试会重新发起采集），`ai` 落 `INTERNAL_ERROR`。`ai polish` / `agent crawl --resume` 还把损坏简历报成「不存在」。现在行内容按元素归一，损坏简历报 `RESUME_FILE_INVALID` 并给出 `resume delete` 指引，空名报 `INVALID_PARAM`（Issue #1370、#1369）。
- `agent crawl` 把 `CrawlProfilePathInvalid` / `CrawlExportError` 兜成 `CRAWL_UNAVAILABLE`（安装依赖），按提示重试永远复现；现在与 `crawl run` 同口径报 `LOCAL_IO_ERROR`（Issue #1366）。
- AI provider 响应与平台 httpx 响应是极深嵌套 JSON 时 `json.loads` 抛 `RecursionError`：AI 命令 stdout 为空 + traceback，平台命令被兜成可重试 `NETWORK_ERROR`。现在两处都捕获 `RecursionError`，AI 按解析失败报 `AI_API_ERROR`，平台响应按不可恢复的 `INTERNAL_ERROR`（不重试）；CDP 探测与页面风险跟踪器的二次解析同步收口（Issue #1365、#1368）。
- 组预算目录的 `config.json` 损坏（坏 JSON / `request_max_per_minute: 0`）时，平台命令被兜成可重试 `NETWORK_ERROR`；现在启动期校验并输出 `INVALID_PARAM`（不可恢复），指向具体配置文件（Issue #1367）。
- MCP 响应含孤立代理字符（如 `\udcff`）时，工具结果序列化抛 `PydanticSerializationError`，异常穿出 `stdio_server()`，整个 MCP 进程退出；`boss mark --label ""` 的空标签被模糊匹配成「新招呼」并在平台改标签；`boss_digest` / `boss_stats` 的显式空输出路径被吞成 `ok=true` 但不写文件。现在响应文本按 CLI 同口径把代理写成字面转义、空标签在构造平台实例前报 `INVALID_PARAM`、显式空路径原样下发（Issue #1375、#1376、#1377）。
- 认证 Cookie 探测与 CDP 探测的响应解析只捕 `ValueError`：极深嵌套 JSON 的 `RecursionError` 逃出后按网络错误处理；现在两处补捕并按「响应无法解析」走既有降级 / 不可用路径（Issue #1378）。

## [3.4.71] - 2026-11-17

### Fixed
- crawl 列表 `hasMore` 为显式 `null` 时被读成「没有更多」，run 报 `completed` 却只抓第一页；导出 `os.replace` 失败（目标已是目录 / 被占用）逃到 `except Exception`，run 落成 `ok=true` 的 stopped、临时文件残留；断点 `profile_path` 为空串时 `Path("")` 解析到 cwd，resume 会删除当前目录的 Singleton 锁并把 Chrome 配置写进去。现在只有显式布尔 `False` 才结束翻页，替换失败转 `CrawlExportError` 并清理临时文件，`profile_path` 必须是非空绝对路径（写入断点前统一 `resolve()`，相对 `--data-dir` 不受影响）（Issue #1345、#1346、#1347）。
- `crawl run --hook-dir` 的内容校验发生在建任务、开浏览器之后：目录拼错 / 清单缺失时任务已落库，命令返回 `ok=true` 并提示 `crawl resume`，但 resume 没有 `--hook-dir` 参数可以修复。现在建任务前校验目录存在、清单可读、脚本齐全且摘要匹配；同时 `_tolerated_timestamp` 补齐 `OverflowError` 与 `math.isfinite`，`updated_at` 为 `inf` 时不再让 queued 任务永久排队（Issue #1348、#1349）。
- 缓存库数据页损坏但首行可读时，游标 `fetchone` / `fetchall` / 迭代抛裸 `sqlite3.DatabaseError`：`crawl status` 只给通用 IO 指引，crawl 执行中被吞成 `ok=true` stopped + resume，坏库下无限重复。现在所有读取路径统一为 `CacheDatabaseInvalid`，命令层输出 `CACHE_DATABASE_INVALID`（Issue #1350）。
- `friend_list` 的 `result` 为真值非列表时遮蔽合法 `friendList`：`chat` 返回空、`digest` / `pipeline` 全零、`chatmsg` / `mark` / `exchange` 误报 `CONTACT_NOT_FOUND`，`chat --export` 还会把空列表写进当日快照。联系人 `uid` 为 null 时被字符串化成 `"None"` 并发进读写请求，候选人消息被误标「我」；查找超过分页上限被报成可重试的 `NETWORK_ERROR`。现在 `result` 只在非空列表时优先、uid 缺失安全停止并给出带 `--data-dir` 的 `boss chat` 路径、分页上限与 wizard 同口径说明重试不改变结果（Issue #1351、#1352、#1353）。
- HTML 导出把平台 null 字段渲染成字面量 `None`；`-o` 指向符号链接时原子写入替换链接本身，真实目标保持旧内容；当日聊天快照的读-合并-写没有互斥，并发导出互相覆盖、次日 diff 误报 `added`。现在 `esc` 对 `None` 输出空串、符号链接（含悬空链接）按 `INVALID_PARAM` 拒绝、快照读-合并-写在跨进程锁内完成（Issue #1354、#1355、#1356）。

## [3.4.70] - 2026-11-17

### Fixed
- 搜索预筛与详情描述对平台字段类型漂移直接消费：`salary` / `experience` / `education` / `city` 为数字、数组或对象时 `.replace` / `in` 抛 `TypeError`，`search` 与 wizard 共用同一条预筛路径直接崩溃；详情 `postDescription` 非字符串时也会在描述筛选处抛错。现在非字符串值一律视为「预筛不排除、后续详情确认」，`postDescription` 非字符串按空文本处理（Issue #1335）。
- 请求预算库损坏被兜成 `INTERNAL_ERROR`：`<data_dir>/request-budget.sqlite3` 被截断、写入非 SQLite 内容或权限不可读时，`sqlite3.DatabaseError` 直接冒到顶层，恢复指引只说「重试」。现在按 `LOCAL_IO_ERROR` 上报，并给出带数据目录的删除重建指引；`locked` / `busy` 仍走 `CACHE_BUSY`（Issue #1336）。
- automation 队列坏 `status` 与非 UTF-8 字节被静默吞掉：`null` / 数字 / 大小写不符 / 旧版 `dry-run` 的值被 `str()` 成未知状态后静默跳过，`agent pending list` 看起来「没有待办」；混入非 UTF-8 字节（例如 GBK 手工编辑）会让整个队列文件静默变空。现在未知状态报 `AUTOMATION_STATE_INVALID`，坏行按行跳过、合法行保留（Issue #1337）。
- watch 与 wizard 运行存量搜索参数时不重新校验：未知城市在流水线深处抛 `ValueError` 被兜成可重试的 `NETWORK_ERROR`，未知薪资 / 经验 / 学历标签被静默丢弃，`watch run` 仍返回 `ok: true`；参数被手工改成数组 / 数字时落 `INTERNAL_ERROR`。现在 `watch run` / `watch run --all` / wizard `candidate_search` / `candidate_watch` 都在平台请求前按 `watch add` 同口径校验：单条失效报 `SAVED_SEARCH_INVALID` 并给出删除重建指引，`--all` 继续执行其余 watch 并在 `invalid` 中列出坏记录（Issue #1338）。

## [3.4.69] - 2026-11-17

### Fixed
- welfare 筛选对 `welfareList` 元素级漂移直接 `" ".join` 抛 `TypeError`：平台把标签数组混入数字 / null / 对象时，`boss search --welfare` 与 wizard 的 job_search / export / watch 全部落 `INTERNAL_ERROR`（wizard 里可能被兜成可重试的 `NETWORK_ERROR`）。`match_all_welfare()` 现在只拼接字符串元素，非字符串 `description` 按空文本处理（Issue #1329）。
- wizard 自由输入与本地配置边界：`raw_params` 非 JSON 对象时被 `dict()` 抛 `ValueError` / `TypeError`；`candidate_export` 的 `format` / `output` 校验放在平台搜索之后，非法 format 也会先翻页消耗请求预算，非路径类型的 `output` 落 `INTERNAL_ERROR`；`config.json` 的 `crawl` 段非对象时同样被兜成可重试错误。现在三处都在平台请求前校验：参数 / 目录路径报 `INVALID_PARAM`，父路径不可用报 `LOCAL_IO_ERROR`，crawl 配置的恢复指引指向带 `--data-dir` 的 `config reset crawl`（Issue #1330）。
- wizard 本地简历损坏与联系人查找上限的错误语义：`ai_assist` 把损坏简历误报成 `RESUME_NOT_FOUND`（照提示 `resume init` 又撞 `RESUME_ALREADY_EXISTS`），`local_resumes` 静默隐藏坏文件，`FriendLookupLimitExceeded` 让候选人「缩小沟通列表范围」。现在分别返回 `RESUME_FILE_INVALID`（带字段级原因与 `resume delete` 指引）、`corrupt` 名单，以及指向 `boss chat` 取 uid 的恢复路径；TTY 补齐 `RESUME_FILE_INVALID` 文案与 `boss resume delete` 的目录改写（Issue #1331）。

## [3.4.68] - 2026-11-17

### Fixed
- MCP 三个工具把显式空串当成未传：`boss_shortlist_add` 的 `source`、`boss_agent_stop` / `boss_agent_review_reject` 的 `reason` 传 `""` 时，CLI 落到自己的默认值（`manual` / `manual-stop` / `human-rejected`），落库值与调用方参数不一致。现在键存在且非 null 就原样下发，与 `export --url ""` 的口径一致（Issue #1324）。
- crawl 断点读取补齐范围、枚举与文本校验：`browser.cdp_port` 越界会在 `socket.connect_ex` 抛 `OverflowError` 并被吞成 `ok: true` 的 stopped；`pages` / `limits` 的 0 或负数、负的 `next_page` 与计数器、非枚举的 `hook.profile`、BLOB 的 `status` / `output_dir` / `error` 均缺少读取边界校验。现在统一抛 `CACHE_RECORD_INVALID`，`crawl status` 与 `crawl resume` 对同一行口径一致（Issue #1325）。

### Changed
- GitHub 公共仓库默认阻止 `pull_request_target`（2026-11-16 brownout，2026-12-02 起永久生效），三个辅助工作流每个 PR 都 `startup_failure`。`Update Contributors` 改为在 `push` 到 `master` 时运行，`Labeler` 改为 `pull_request`（fork / Dependabot 只读 token 直接跳过），删除从未生效的 `Dependabot auto-merge`；新增离线守卫禁止重新引入 `pull_request_target`（Issue #1322）。

## [3.4.67] - 2026-11-17

### Fixed
- chat 家族三处平台字段漂移：`lastMessageInfo` 非对象时 `boss chat` 落成 `INTERNAL_ERROR`；上一份快照的 `last_ts` 是字符串时 `boss chat --export` 的筛选比较抛 `TypeError`；超过 4300 位的 ASCII 数字 `mid` 让 `boss hr chatmsg` 落成可重试的 `NETWORK_ERROR`。三处现均按「字段不可用」处理（Issue #1317）。
- 搜索缓存命中时 `data` 的元素不是对象，会让 `--sort score` 与索引缓存写入直接 `AttributeError`；`match_score` 漂移成字符串时排序键混合比较抛 `TypeError`。patchright `browsers.json` 形状漂移（顶层数组 / `browsers` 非数组 / 混入 null）也会打断 `boss doctor`。两处本地读取边界现按「不可用」处理（Issue #1318）。

## [3.4.66] - 2026-11-17

### Fixed
- 向导 `candidate_mark` 从平台返回的联系人记录里读 `friendSource` 后直接 `int()`：字段漂移成非数字字符串时 `ValueError` 被兜成可重试的 `NETWORK_ERROR`（重试同一份数据必然复现），漂移成对象 / 数组时落成 `INTERNAL_ERROR`（Issue #1314）。`friendSource` 只是回传给标签接口的定位参数，现在非数字值一律按缺失的默认 0 处理。

### Docs
- ROADMAP 中英「已发布 / Released」列表从 v3.4.54 起把新条目直接拼在上一版条目后面，12 条挤成一整行 3000+ 字符（Issue #1312）。现已按版本拆成独立列表项，并新增文档测试断言防止后续发版继续粘连。

## [3.4.65] - 2026-11-17

### Fixed
- 自动化执行锁 / 控制锁打开阶段的 `sqlite3.OperationalError`（锁文件被目录占位、不可写、磁盘满）原样上抛，命令层要么报成让人去删缓存的 `CACHE_BUSY`，要么丢掉 JSON 信封（Issue #1304）。现在只有锁冲突报 `AUTOMATION_RUN_BUSY` / `AUTOMATION_CONTROL_BUSY`，其余按 `AUTOMATION_STATE_INVALID` 返回；顶层兜底把非 busy 的 `DatabaseError` 收口为 `LOCAL_IO_ERROR`。
- 自动化周期里控制锁冲突一律包装成 `LOCAL_STATE_WRITE_FAILED` + `replay_safe=false`，零派发（如刚启动就被 `agent stop` 抢占）时也提示「平台动作可能已发出」（Issue #1307）。现在只有真正派发过动作后才升级该语义，零派发冲突按普通「没有生效」上报。
- headless 刷新通道返回空 stoken 时被当成功写进登录态，之后所有请求拿空凭证打平台、重试到预算耗尽，`TOKEN_REFRESH_FAILED` 永不产生（Issue #1305）。现在空值按刷新失败处理并保留旧登录态，与 CDP 通道同口径。
- `str.isdigit()` 对 '①' / '²' 等非 ASCII 数字为真但 `int()` 会抛 `ValueError`：向导标签 / 城市、`mark --label`、`crawl` 城市代码、菜单序号、搜索筛选原始代码与 `hr chatmsg` 游标都会崩溃或被兜成可重试的 `NETWORK_ERROR`（Issue #1306）。现在只把 ASCII 数字串当数字，非法输入走各入口的确定性参数错误。

## [3.4.64] - 2026-11-17

### Fixed
- argv 里的非法字节会解码成 U+DCFF 之类的孤立代理：本地 JSON 写入、`-o` 文本导出、信封序列化与 `config set` 输入边界原先都会抛 `UnicodeEncodeError`，命令 exit 1 但 stdout 没有信封（MCP 拿不到结果）（Issue #1294）。现在写入用 `backslashreplace` 落成 JSON 合法转义，信封序列化后保证可编码，`config set` 直接报 `INVALID_PARAM`，顶层网兜把 `UnicodeError` 归为 `LOCAL_IO_ERROR`。
- 平台把纳秒时间戳当毫秒传来（`10**17`）或 JSON 的 `1e400` 变成 `inf` 时，`chat` / `chatmsg` / `hr chat` / `pipeline` / `digest` 的时间格式化抛 `ValueError` / `OverflowError`，被兜成可重试的 `NETWORK_ERROR` 或 `INTERNAL_ERROR`（Issue #1295）。现在统一按「无时间」输出。
- HTML 导出遇到非字符串平台字段（`brandName` 是数字、`skills` 混入非字符串）时 `html.escape` 抛 `AttributeError`，落成 `INTERNAL_ERROR`，而 md / csv 正常（Issue #1296）。现在统一按文本转义。
- pipeline 聚合排序遇到混合类型的 `brandName` / `title` 时跨类型比较抛 `TypeError`，一条漂移记录拖垮整份日报（Issue #1297）。排序键统一转字符串。
- `pipeline` / `digest` 的 `--now-ts-ms 0` 被真值回退吞掉，测试钩子静默失效（Issue #1298）。现在按显式值处理。

## [3.4.63] - 2026-11-16

### Fixed
- 向导的 `crawl_status` / `crawl_resume` 指向本地损坏的采集记录时，`CacheRecordInvalid` 被 runner 通用兜底记成可重试的 `NETWORK_ERROR`（「重试当前 run_id」），照做只会无限重试同一条坏记录（Issue #1286）。现在 runner 统一映射为 `CACHE_RECORD_INVALID`，恢复方式为重建任务（`replay_safe=false`）。
- 向导的 `crawl_start` / `crawl_resume` 遇到导出失败（输出目录是同名文件、无权限、磁盘满）时被兜成 `NETWORK_ERROR`，`--max-retries>0` 还会自动重跑步骤、重新打开采集 Chrome 并再次发出平台请求（Issue #1287）。现在与 CLI 一致返回 `LOCAL_IO_ERROR`（recoverable=false，`replay_safe=false`），指引修复输出目录与权限。
- 采集断点 `params.cdp_port` / `limits.*` 为 `Infinity` / `1e400`，或向导输入 `pages: 1e400` 时，`int()` 抛出的 `OverflowError` 未被捕获，落成 `INTERNAL_ERROR`（「先升级版本」）（Issue #1288）。现在 `CrawlSettings.from_dict` 按记录损坏报 `CACHE_RECORD_INVALID`，向导按 `INVALID_PARAM` 报错。
- `CRAWL_PROFILE_BUSY` 的信封 `next_actions`、登记表恢复文案与向导恢复文案引用不带值的 `boss crawl status`，照抄返回 `INVALID_PARAM`（缺少必填 `RUN_ID`）（Issue #1289）。现在统一使用 `crawl status <run_id>` 占位形式。
- 采集配置目录 `crawl/chrome-profile` 被同名文件占用或不可写时，`CrawlTransport.open()` 的 `OSError` 被 `_run` 兜成 `ok=true` 的 `stopped`，`crawl resume` 永远复现（Issue #1290）。现在抛出 `CrawlProfilePathInvalid`：CLI 与向导都按 `LOCAL_IO_ERROR`（recoverable=false）上报，指引检查路径、权限与磁盘空间。

## [3.4.62] - 2026-11-16

### Fixed
- 采集配置目录被另一个运行中的采集 Chrome 占用时，`CrawlService.create_and_run` 抛出的 `CRAWL_PROFILE_BUSY` 在 `agent crawl`、`crawl run` 与向导路径上被兜底成 `stopped`，调用方拿到 `ok=true` 与永远复现的恢复指引（Issue #1274）。现在三条路径都按 `CRAWL_PROFILE_BUSY` 上报：本次调用未发出请求，指引先用 `crawl status` 确认占用结束。
- 安全页检查把 DrissionPage 未命中时返回的 `NoneElement` 当成命中（`is None` 永远为假），未登录或安全页会被当成空结果继续（Issue #1276）。现在用真值判断，缺职位列表容器时按风险页停止并写风险锁。
- 断点 `params.chrome_path` 的类型漂移（非字符串）原样进 `set_browser_path` 抛 `TypeError`，被记成 `stopped` 并给出永远复现的恢复指引（Issue #1278）。现在 `CrawlSettings.from_dict` 按 `CacheRecordInvalid` 报 `CACHE_RECORD_INVALID`，与其它参数漂移一致。
- 采集因风控停止且风险锁写盘失败时，成功信封没有提示「新进程不再受保护」（Issue #1281）。现在 `risk_stopped` 分支追加与认证错误路径相同的操作者提示。
- 采集 `_run` 的 `finally` 顺序执行 budget 落库、终态落库与关闭浏览器：前一步失败会跳过后续步骤，run 永远停在 `running`、采集 Chrome 继续占用共享 profile，且清理异常会覆盖传播中的原始异常（Issue #1277）。现在三步各自兜底，正常路径上的清理失败仍然抛出（命令层报缓存或 IO 错误），但不覆盖风控 / 中断 / 停止。
- 风险锁主文件写盘失败（主文件被占成目录、权限损坏）时，锁只保留在当前进程内存里，进程退出后新进程读不到阻断（Issue #1270）。现在 `write_lock` 会尝试后备位置 `<data_dir>/cache/cdp_risk_lock.json`：`read_lock` / `raise_if_locked` 任一份存在即 fail-closed 锁定，`clean --risk-lock` 同时清理两处；只有两处都失败才保留进程内锁并在信封提示「新进程不再受保护」。

## [3.4.61] - 2026-11-16

### Fixed
- 不同 crawl run 并发执行时共用同一个采集 Chrome 配置目录（`crawl/chrome-profile`），而端口按 run 随机分配：`CrawlTransport.open()` 只探测自己的端口，空闲就无条件删除 `SingletonLock` 等文件，会破坏另一个 run 正在使用的采集 Chrome（Issue #1266）。现在先读锁里的 `<hostname>-<pid>`，进程仍存活时不删锁、不启动第二个实例，返回 `CRAWL_PROFILE_BUSY`（`recoverable: true`，本次调用未发出请求）；只有进程已不存在、跨主机或格式不可解析的锁才按陈旧锁清理。
- 命中平台风控且主锁与 group 目录的锁都写失败时，本地锁只在当前进程生效：进程退出后新进程读不到锁会继续访问平台（Issue #1270）。现在风控信封在 `details.risk_lock.persisted` 为 `false` 时追加 `operator_actions`：「本地风险锁未能写入磁盘：当前进程退出后，新的 boss 进程不会再被这把锁阻止；先修复数据目录的写权限与磁盘空间，在确认平台已恢复前不要启动新的 boss 进程」。两条风控出口（`handle_auth_errors` 与登录链路的 `classify_login_error`）共用同一份提示。

## [3.4.60] - 2026-11-16

### Fixed
- `boss agent run` / `agent train` 执行周期内 checkpoint 撞上 `automation/.control-lock.sqlite3` 时，命令层原先按 `AUTOMATION_BUSY` 报「本次操作没有生效，可稍后重试」（Issue #1267）：动作派发成功、队列已写回 `executed` 之后平台动作可能已经发出，按此指引重试会让操作者误以为消息没有发送。现在 runner 把周期内的控制锁冲突转换成 `AutomationControlBusyDispatched`，命令层按 `LOCAL_STATE_WRITE_FAILED`（`replay_safe: false`）上报：平台动作可能已发出、本地状态保存失败，恢复指引为先核对官方页面、不自动重发。`--dry-run` 与 `--resume` 入口的锁冲突没有产生平台动作，仍返回 `AUTOMATION_BUSY`。

## [3.4.59] - 2026-11-16

### Fixed
- `boss agent run` / `agent train` 被 Ctrl+C 中断时 stdout 没有任何 JSON 信封（Issue #1262）：runner 打开熔断并保留 checkpoint 后原样重抛 `KeyboardInterrupt`，Click 在 `standalone_mode=False` 下把它转成 `Abort` 上抛，进程以空输出退出；MCP 通过子进程解析 stdout，只能把这次写调用当成结果未知。现在命令层捕获中断并输出 `CIRCUIT_BREAKER_OPEN` 信封（可重试），说明执行被中断、熔断已打开并保留 checkpoint，恢复指引为核对未确认动作后执行 `--resume`。

## [3.4.58] - 2026-11-16

### Fixed
- `boss agent run --dry-run` 遇到上一轮真实运行中断后遗留的 `executing` 待执行行时会打开熔断并改写队列（Issue #1257）：预览的 `process_pending` 把该行转成 `verification-required` 并写入 `stop.json`，之后普通 `agent run` 直接返回 `CIRCUIT_BREAKER_OPEN`，操作者需要额外 `--resume` 才能继续，违反「预览不改写待执行队列」的契约。现在 dry-run 只追加一条 `DRY_RUN` 事件，说明真实运行会把它转为 `verification-required` 并要求人工核对、不会自动重发，不改写队列也不打开熔断；真实运行保持既有行为。
- `boss agent crawl --run-id` 读取到损坏的本地 crawl run 记录时误报 `INVALID_PARAM`（Issue #1259）：`CacheRecordInvalid` 是 `ValueError` 子类，被参数错误的 `except ValueError` 接住，恢复指引让人去修关键词、城市或 crawl 配置——调整任何参数都不可能修好一条写坏的本地记录。现在单独捕获并返回 `CACHE_RECORD_INVALID`，`next_actions` 指向 `crawl start`（重新创建任务）与 `crawl status`（核对 ID），与 `crawl status` / `crawl resume` 的恢复契约一致。

## [3.4.57] - 2026-11-16

### Fixed
- automation 执行周期没有跨进程互斥（Issue #1253）：待执行队列按整文件回写，两个 executor 同时处理同一数据目录时，后写者会用旧快照覆盖先写者的 `executing` 在途标记（Issue #1245），同一条已批准动作可能被发送两次，被覆盖回 `pending` 的动作下一轮还会再发；并发的 `agent review approve` 也可能被整文件回写吞掉。现在整个周期持有 `automation/.run-lock.sqlite3` 的跨进程执行锁，第二个 executor 在发出任何请求前返回新登记的 `AUTOMATION_RUN_BUSY`（可重试，附 `agent stats`），显式 `--resume` 也在锁内执行；`agent review approve` / `reject` 与执行周期互斥，冲突时不入队、复核项保持 `review`。执行锁独立于控制锁，`agent stop` 仍能在运行期间立即写入熔断。

## [3.4.56] - 2026-11-16

### Fixed
- automation 控制锁冲突不再被误报成 `CACHE_BUSY`（Issue #1251）：`automation/.control-lock.sqlite3` 被其他 boss 命令占用时，`BEGIN IMMEDIATE` 抛出的 `database is locked` 穿透到兜底层后被归为缓存库错误，恢复指引让人去等 `cache/boss_agent.db` 的写入者，而 `agent stop` 实际上没有生效。现在控制锁冲突返回新登记的 `AUTOMATION_BUSY`（可重试），指引指向真正的锁文件并说明锁文件完好、不需要删除；`agent stop` 与 `agent run` 的恢复/写状态路径都按此上报。非锁类 `OperationalError` 原样上抛，不伪装成可重试的占用。

## [3.4.55] - 2026-11-16

### Fixed
- `boss agent review approve` 先写复核队列再追加待执行队列（Issue #1248）：第二步失败（磁盘满、权限、中断）时复核项已是 `approved`、pending 里却没有对应行，重试因为找不到 `review` 记录直接报「不存在或已处理」，已批准的动作永久丢失。现在改为先入队、再标记复核项；入队失败可直接重试，两步之间失败时按 `approved_review_id` 去重，只补写复核队列。

## [3.4.54] - 2026-11-16

### Fixed
- 招聘自动化的待执行动作在写请求发出后中断时仍保持 `pending`，`agent run --resume` 或下一次普通运行会把可能已生效的动作再发一遍（Issue #1245）。现在派发前先落盘 `executing` 标记：中断、进程被杀、响应风控与本地回写失败都停在 `executing`，下一轮转成 `verification-required` 并打开熔断，提示先核对官方页面；只有预算 / 时限在请求发出前抛出（`request_sent=false`）时才恢复 `pending` 可安全重试。`docs/commands.md` 同步补充该语义。

## [3.4.53] - 2026-11-16

### Fixed
- 招聘自动化的熔断开关 `circuit_breaker.open` 只按 JSON 真值读取（Issue #1242）：`stop.json` 为 `{"open": 0}` / `{"open": null}` 时停止开关被错误关闭、自动化照常发送写操作，且该轮 checkpoint 会把 `state.json` 里原本的 `open: true` 覆写成 `0`；`{"open": 1}` / `{"open": "true"}` 时 `SafetyGuard` 与 `boss hr location` 的读取口径相反。现在 `read_stop` 对非布尔 `open` fail-closed 并标记损坏，`read_state` / `write_state` 对类型漂移报 `AUTOMATION_STATE_INVALID`，操作员仍可通过显式 `agent resume` 解除熔断。

## [3.4.52] - 2026-11-16

### Fixed
- `boss crawl` 的停止状态机与导出顺序（Issue #1238）：transport 透传的 wizard 控制信号（`WORKFLOW_STOPPED` / `WORKFLOW_TIMEOUT`）原先落成普通 `stopped`，命令层会给出可重试的 `NETWORK_ERROR` 与会清除 stop 标记的 `crawl resume`；现在按错误码落成 `workflow_stopped` / `timeout_stopped`。最后一页导出移到 `completed` 落库之前，导出失败后再次 `resume` 仍能补齐产物，而不是带着 `completed` 报成功却无文件。
- `boss crawl` 导出失败原先被兜成 `CRAWL_UNAVAILABLE`（指引用户安装 crawl 依赖），现在映射为不可重试的 `LOCAL_IO_ERROR`；`budget_stopped` / `workflow_stopped` 的 `next_actions` 改为只读的 `crawl status`，`operator_actions` 说明预算已用尽需新建任务、stop 后需要显式 resume。

## [3.4.51] - 2026-11-16

### Fixed
- `boss wizard --input-json` / MCP `boss_wizard` 的 `requested_steps` 传空数组时，原先会被折叠成「执行 goal 全部步骤」——对 `candidate_greet` 这类含真实平台写入的 goal，调用方本意是不执行任何步骤，却会执行全部写操作（Issue #1236）。现在空数组以 `INVALID_PARAM` 拒绝，只有省略该字段才表示全量执行；工具 schema 同步声明 `minItems: 1`。
- MCP 工具参数含 NUL 或孤立代理项字符时，原先在派生 `boss` CLI 的子进程编码阶段抛 `UnicodeEncodeError`，被兜成不可读的 `INTERNAL_ERROR`（Issue #1236）。现在在调用 `boss` CLI 前统一校验 argv，返回可读的 `INVALID_PARAM` 信封，且不会生成子进程。

## [3.4.50] - 2026-11-16

### Fixed
- 平台成功包络里的 `zpData` 返回非对象真值（字符串 / 数组 / 数字 / 布尔）时，`unwrap_data(...) or {}` 只挡 `null` 与空容器（Issue #1233）：`boss wizard --goal last_messages` 读 `friendList` 抛 `AttributeError`、整轮 wizard 落不可重试的 `INTERNAL_ERROR`；`boss hr download-resume` 在附件已经落盘后用 `**` 展开抛 `TypeError`，用户拿到失败信封但文件已下载；`boss hr jobs detail` 把畸形值放进 `ok:true` 的 `data`；`boss favorites list` 走写操作口径的 `UNKNOWN`（「先核对是否生效」），对只读列表不可重试且误导。现在按期望容器类型兜底：wizard 按空页继续、download-resume 报下载成功、jobs detail 的 `data` 归一为空对象、favorites list 返回可重试的 `NETWORK_ERROR`；列表命令与 `me` 各 section 的 `data` 保持既有原样透传契约。

## [3.4.49] - 2026-11-16

### Fixed
- 本地数值转换的溢出边界一次收口（Issue #1230）：`json.loads` 能正常解析 4300 位以内的大整数与 `1e400`（→ `inf`），但 `float()` / `int()` / `math.isfinite()` 在溢出时抛 `OverflowError`——`BossCliGroup` 把它归入不可重试的 `INTERNAL_ERROR`（指引「升级版本」），搜索缓存路径则被兜成可重试的 `NETWORK_ERROR`。现在：搜索缓存里的坏行按未命中处理、继续正常搜索；`config.json` 的 `request_delay` / `request_wait_timeout` 大整数与 `config set automation|crawl` 的 `1e400` 返回 `INVALID_PARAM` 并给修复入口；`ai_temperature` 大整数返回 `INVALID_PARAM`；本地模型输出的 `confidence` 大整数或深嵌套走模板话术降级并要求人工复核，不再中止整轮自动化；AI fit 输出的 `match_score: 1e400` 按 0 分处理并继续返回结果。

## [3.4.48] - 2026-11-16

### Fixed
- 本地 JSON 读取补齐「超长整数」与「深嵌套」两类确定性解析异常（Issue #1227）：Python 3.11+ 的 `json.loads` 对超过 4300 位的整数字面量抛裸 `ValueError`（不是 `JSONDecodeError`），深嵌套数组在 3.11–3.13 抛 `RecursionError`；只捕 `JSONDecodeError` 的读取边界会让它们穿透成 traceback 或可重试的 `NETWORK_ERROR`。`boss agent stats` / `boss resume show` 之前直接 traceback、stdout 没有信封；`automation/storage.py` 的熔断 latch（`stop.json`）读不出来时没有 fail-closed。现在所有本地解析边界统一按各自契约兜底：熔断 latch 返回 `open: true` / `corrupt: true`，`state.json` 与队列记录报 `AUTOMATION_STATE_INVALID`，简历文件报 `RESUME_FILE_INVALID` / `ResumeImportError`，聊天快照按不可用降级，SQLite 断点报 `CACHE_RECORD_INVALID` / `SavedSearchParamsError`，坏行跳过，配置文件与 `--input-json` / `--set` 结构化值按损坏输入处理。

## [3.4.47] - 2026-11-16

### Fixed
- 索引缓存的读路径补齐解析、自洽与展示边界（Issue #1224）：写入合法 JSON 的超长整数（Python 3.11+ 的 4300 位转换上限）或深嵌套数组时，`json.loads` 抛裸 `ValueError` / `RecursionError`，`boss show` 被兜成可重试的 `NETWORK_ERROR`；现在损坏缓存按「没有缓存」返回 `INVALID_PARAM` 并提示重新搜索。缓存里的 `count` 与 `jobs` 不一致时越界提示会自相矛盾（「共 5 条」却拒绝编号 5），现在 `count` 始终等于已校验的 `len(jobs)`。`source` 含孤立代理字符（`"\ud800"` 转义）会让错误信封在 stdout 编码时抛 `UnicodeEncodeError`、同样落成可重试的网络错误，现在按空串归一。`count` / `page` 不再绕过溢出防护（超大整数按实际条数 / `None`），`saved_at` 限定在合理的 Unix 秒区间（负值与 2100 年以后按 0 处理）。

## [3.4.46] - 2026-11-16

### Fixed
- `index_cache.json` 的元信息字段类型漂移不再原样进入 `index_snapshot` 信封（Issue #1221）：`_load_cache` 只校验顶层与 `jobs`，`source` / `page` / `count` / `saved_at` 被写成字符串、布尔或 `inf` 时，`boss show` 的 `data.index_snapshot` 与 `search` / `recommend` 的 `pagination.index_snapshot` 会带出违反 schema 的类型（`count: "many"`、`saved_at: "yesterday"`），Agent 按 schema 解析失败。现在读路径按容错口径归一：非字符串 `source` 按空串、非法 `page` 按 `None`、非法 `count` 回退 `len(jobs)`、非有限 `saved_at` 按 0，`jobs` 可用时不整体丢弃缓存。

## [3.4.45] - 2026-11-16

### Fixed
- `boss_hr_last_messages` 显式传空 `friend_ids` 数组不再退化为抓取当前页候选人（Issue #1218）：映射层的 `for friend_id in arguments.get("friend_ids") or []` 把空数组与未传混为一谈，CLI 的 `--friend-id` 是 `multiple=True`，于是进入 page 模式发起平台请求并返回非 null 的 `pagination`。现在 schema 声明 `minItems: 1`，MCP 入口统一校验 schema 声明的 `minItems` 并返回 `INVALID_PARAM`；缺省仍走 page 模式，合法非空数组行为不变。

## [3.4.44] - 2026-11-16

### Fixed
- 缓存 TTL、风险锁时间戳与令牌刷新前的登录态读取收口（Issue #1214）：`search_cache` / `job_desc_cache` 的 `created_at` 被写成文本或 `inf` 时，TTL 比较抛 `TypeError`——`boss search` 兜成不可重试的 `INTERNAL_ERROR`，crawl 详情阶段吞成没有错误码的 `stopped` 且重跑仍读同一坏行。现在坏时间戳按已过期处理并删除坏行，后续请求正常重取。风险锁的 `locked_at` 写成超长整数时 `float()` 抛 `OverflowError`，绕过 fail-closed 分支并把 `ACCOUNT_RISK` / `ENVIRONMENT_RISK` 替换成 `INTERNAL_ERROR`；写成 `inf` 时 `lock_status` 的 `time.localtime` 崩溃。现在只接受有限且落在合理区间的时间戳，两种坏值都按「时间未知」展示，锁的阻断语义不变。刷新 stoken 前先校验 `cookies` 是对象，结构漂移时直接提示重新登录，不再先发起刷新再误报成泛化的 `TOKEN_REFRESH_FAILED`。

## [3.4.43] - 2026-11-16

### Fixed
- 本地记录数值与容器漂移在四处读取边界收口（Issue #1212）：`crawl_budget.last_request_at` 与 `request_state` / `request_attempts` 的时间列被写成非数值文本时，`reserve_crawl_budget` 抛 `ValueError`（被 `handle_auth_errors` 兜成可重试的 `NETWORK_ERROR`），`RequestBudget.attempt` 抛 `TypeError`（全部平台请求落成不可重试的 `INTERNAL_ERROR`）；坏行还会永久占满每分钟额度并让等待超时。现在调度元数据按无历史 / 0 容错，`typeof` 漂移的行在下一轮清理，坏 `cooldown_until` 在写入与读取两侧归一（此前 SQLite 的 `MAX(text, real)` 会选中文本，新冷却被静默吞掉）。`crawl_jobs.page_no` / `detail_done` 与 `crawl_runs` 一样在读取边界校验，`crawl start` / `resume` 把 `CacheRecordInvalid` 作为确定性失败重抛，入口返回 `CACHE_RECORD_INVALID` 而不是 `ok:true` 的 stopped。向导状态面板与结果菜单对历史 `last_result.data.jobs_seen` / `jobs` 的漂移值降级展示，不再 traceback。`_record_cdp_risk_lock` 在登录态 `cookies` 不是对象时按缺失处理，保证风险锁照常写入、原始 `ACCOUNT_RISK` / `ENVIRONMENT_RISK` 异常不被 `AttributeError` 替换。

## [3.4.42] - 2026-11-16

### Fixed
- `boss ai` 各命令、`boss agent crawl` 与向导 AI 步骤不再把坏 AI 配置原样发给服务商（Issue #1209）：`ai_temperature` / `ai_max_tokens` 被手改成字符串、数组或 `bool` 时，构造 `AIService` 时裸读，坏值进入请求 payload，由服务商返回 400，用户只看到泛化的 `AI_API_ERROR`。现在与非向导消费点（#1196）同口径：`ai/config.py` 新增 `ai_float_setting`，两个消费点在发请求前校验，命令返回 `INVALID_PARAM` 并把 recovery 指向 `boss ai config --max-tokens 4096 --temperature 0.7`；向导步骤抛同码的 `WorkflowActionError`。`null` 与缺键继续按默认值。

## [3.4.41] - 2026-11-16

### Fixed
- `crawl resume --background` 在断点的 `updated_at` 列被写成非数字文本时不再返回可重试的 `NETWORK_ERROR`（Issue #1206）：`_prepare_background_resume` 用裸 `float()` 读取该列（存储层按 #1192 的既定口径故意不校验时间戳），`ValueError` 被 `handle_auth_errors` 兜成「重试」，Agent 会拿同一条命令无限重试。现在与状态排序、排队探测共用同一容错读数，漂移按 0 降级，后台恢复照常排队/启动，预检失败还原时写回归一后的 0.0。

## [3.4.40] - 2026-11-16

### Added
- MCP 补齐两个 CLI 已有参数的映射（Issue #1202）：`boss_greet` 新增可选 `message`（自定义打招呼语，缺省使用平台默认招呼），`boss_apply` 新增可选 `lid`（与 `security_id` 同一列表项的访问上下文，取自 search 结果）。两者缺省 / `null` 不下发，自由文本空串按未传处理（与 `boss_detail` 的 `lid` 同口径）。

## [3.4.39] - 2026-11-16

### Fixed
- `boss_hr_chatmsg` 的显式游标 0 不再被 MCP 映射层静默吞掉（Issue #1199）：`max_msg_id` 此前用真值判断，传 `0` 时 argv 不含 `--max-msg-id`，工具却返回 `ok=true`，调用方以为翻页生效、实际拿到默认最新一页。现在键存在即下发（显式 `null` 仍在入口剥离），由 CLI 的 `IntRange(min=1)` 返回 `INVALID_PARAM`；schema 标注 `minimum: 1`。
- `boss_exchange` 可以通过 MCP 请求微信交换（Issue #1200）：工具描述承诺「手机号或微信」，但 schema 没有 `type`、映射固定走默认 phone，显式传 `type` 会被未知参数拒绝。现在 schema 暴露 `type`（enum `phone|wechat`，默认 `phone`），映射省略 `type` 或传 `phone` 时不带选项、`wechat` 下发 `--type wechat`、空串原样交给 CLI 枚举诊断（与 `boss_hr_exchange` 同口径）。

## [3.4.38] - 2026-11-16

### Fixed
- crawl 记录的数值列与 `params` 类型漂移不再让 `crawl status` 破坏信封契约（Issue #1192）：`crawl_runs` 的 `next_page` / `requests_attempted` / `elapsed_seconds` / `stop_requested` 等列被写成文本时，此前 stdout 为空、stderr 只剩 `ValueError` traceback（`BossCliGroup` 兜底不含 `ValueError`）。现在缓存读取边界逐列校验并抛 `CacheRecordInvalid`（消息带列名），命令层返回 `CACHE_RECORD_INVALID` 信封；`crawl status` 复用 `CrawlSettings.from_dict` 校验 `params`，同一份损坏记录与 `crawl resume` 判定一致，`limits` 回显归一后的有效预算而不是原始坏值；任务列表排序与后台排队探测对坏时间戳按降级值处理，不再 traceback。
- 上一份聊天快照里的 `unread` 类型漂移不再打断 `chat --export`（Issue #1194）：快照对比用 `curr_unread > prev_unread` 直接比较，上一份快照被手工改过或来自旧格式（如 `"unread": "3"`）时 `str` 与 `int` 比较抛 `TypeError`，整次导出落成 `INTERNAL_ERROR`。现在快照对比只认真正的整数，其余值（字符串、`bool`、`None` 等）按 0 参与比较——与「快照不可用按首次快照处理」的既有降级一致；`bool` 与 `int_code` 同口径排除。
- `ai_max_tokens` 配置类型漂移不再崩成 `INTERNAL_ERROR`（Issue #1196）：`ai/config.py` 的 `load_config` 只合并默认值、不校验类型，`ai local smoke` 与招聘者自动回复里的裸 `int(config.get("ai_max_tokens") or 512)` 在配置被手改成数组 / 字符串时抛 `TypeError`——健康检查落成 `INTERNAL_ERROR` 且提示「不要重试」，自动回复则让整轮 `hr automation` 中止。现在两处共用 `ai_int_setting`：非整数（含 `bool`）与小于 1 的值给出明确配置错误；smoke 返回 `INVALID_PARAM` 并把 recovery 指向 `boss ai config --max-tokens`，自动回复按既有 `AIServiceError` 路径退回模板话术（不中止自动化）。`ai local configure` 写入的合法值行为不变。

## [3.4.37] - 2026-11-16

### Fixed
- 简历 JSON 字段类型漂移的读取判定统一（Issue #1189）：`name` 写成对象、`personal_info` 写成字符串、`rows` 元素写成字符串等坏文件，此前 `resume list` 当正常条目、`resume show` 原样返回错误类型、`resume export --format html` 把 `'str' object has no attribute 'get'` 兜成 `EXPORT_FAILED`。现在模型读取边界按字段校验类型（消息给字段路径与期望类型），list 与按名读取共用同一判定：坏文件进 `hints.corrupt`，show/export 返回 `RESUME_FILE_INVALID` 并指出字段，`resume import` 仍是 `INVALID_PARAM` 字段级提示；合法旧格式不变。

## [3.4.36] - 2026-11-16

### Fixed
- `wizard --stop` 不再把已生效的停止报成失败（Issue #1186）：停止标记先落盘、展示读取随后解析断点 JSON 列，断点写坏（如 `params` 不是合法 JSON）时整次停止返回 `ok=false` + `CACHE_RECORD_INVALID`，并引导「重新创建任务」。现在两级回退都失败时返回最小可用记录（`run_id` + `status: "stopped"`），确认停止生效；`--status` 仍按展示契约报告损坏。

## [3.4.35] - 2026-11-16

### Fixed
- wizard 任务列表不再把可恢复任务标成断点损坏（Issue #1181）：`wizard --status` 与交互式恢复选择此前把「读步骤 + 校验重放标记」和「叠加实时采集」放在同一个 `try`，绑定的 crawl 记录写坏时整条任务被丢进 `corrupt_run_ids`，明明能恢复却被提示重建。现在两层读取分开：断点或重放标记损坏仍按损坏跳过，只是叠加数据坏掉时保留任务（少显示 jobs / live_status，不影响恢复）。

## [3.4.34] - 2026-11-16

### Fixed
- MCP 枚举筛选的显式空串不再被静默吞掉（Issue #1180）：`boss_preset_add` / `boss_watch_add` / `boss_batch_greet` 的 `industry` / `scale` / `stage` / `job_type` 在 CLI 层是 Choice 校验的枚举，映射层此前用真值判断，`industry=""` 被化装成「没传」并返回 `ok=true`。现在这些键登记进 `_ENUM_KEYS` 并原样下发，由 CLI 返回 `INVALID_PARAM` 与可选值；`None` / 缺键仍等价于未传。

## [3.4.33] - 2026-11-16

### Fixed
- `wizard --resume` 不再被损坏的内部采集记录挡住（Issue #1177）：断点与步骤完好、但绑定的 crawl 记录写坏时，`--resume` 此前经 `WorkflowStore.get()` 的展示用实时采集叠加读取，直接返回 `CACHE_RECORD_INVALID` 并提示重新创建任务。现在恢复与执行器内的控制读取只读断点本身；执行结果优先带实时采集叠加，叠加数据损坏时退回断点。`--status` 仍按展示契约报告损坏。

## [3.4.32] - 2026-11-16

### Fixed
- 缓存表结构漂移时不再直接 traceback（Issue #1174）：`shortlist_records` 缺列（旧版本建的库、迁移中断）时，`shortlist list` / `shortlist add` 等走 `CacheStore` 的命令此前把 `sqlite3.OperationalError` 抛到顶层，stdout 为空并带 traceback。现在 `CacheStore` 的连接把执行期数据/结构错误归一为 `CACHE_DATABASE_INVALID` 信封（备份后删除重建的恢复动作），锁冲突仍是可重试的 `CACHE_BUSY`，代码写错的 `ProgrammingError` 保持原样上抛。

## [3.4.31] - 2026-11-16

### Fixed
- `digest --format md -o`、`stats --format html -o`、`chat --export -o` 与 `resume export -o` 改为原子写入（Issue #1171）：`Path.write_text` / `open("w")` / Chromium 直写目标路径会先截断或留下半截文件，写入中途被中断（Ctrl-C、进程被杀、磁盘写满）就毁掉上一次的完整导出。现在与 `export`（#1137）共用 `write_text_atomic`，PDF 也先写临时文件再替换：失败时保留旧文件、不残留临时文件，写入失败仍返回 `LOCAL_IO_ERROR` 信封。

## [3.4.30] - 2026-11-16

### Fixed
- `config.json` 的 `role` / `platform` / `browser_source` / `cdp_url`（类型）坏值不再把 `boss config reset` 也挡在启动期（Issue #1168）：此前这些键被手改成非法取值后，所有命令（含修复入口 `config reset <键>`）都在启动阶段返回 `INVALID_PARAM`，只能手工编辑文件。现在 `config` 子命令把坏值归一成默认值后放行，其余命令仍按参数错误拒绝；显式 `--role` / `--platform` / `--browser-source` / `--cdp-url` 覆盖保持严格校验。

## [3.4.29] - 2026-11-15

### Fixed
- 历史列表与向导职位详情在非对象平台载荷上不再崩溃（Issue #1162）：`history` 的 `zpData` 漂移成字符串 / 数组 / 数字时，此前在 `hasMore` 读取处抛 `AttributeError` 并报成 `INTERNAL_ERROR`，现在按空页返回且分页证据保持「未知」；wizard 候选人详情在 `zpData` 或 `jobInfo` 不是对象时，此前抛未捕获的 `AttributeError`，现在与 `detail` / `show` 同口径改走职位卡片，两个通道都读不出职位时返回 `JOB_NOT_FOUND` 而不是 `NETWORK_ERROR`。合法响应行为不变。

## [3.4.28] - 2026-11-15

### Fixed
- 断点里的重放标记类型漂移时不再重放结果未知的写步骤（Issue #1159）：`error.replay_safe` 被写成 `0`、`"false"`、`null` 等非布尔值时，`wizard --resume` 此前按可重放处理并再次执行该步骤。现在 `--resume` / `--status` 与任务列表在读取边界返回 `CACHE_RECORD_INVALID`，不执行动作、不改写断点，并提示先核对平台实际状态；布尔值与缺省标记的行为不变，`--stop` 仍照常落盘并传播停止。

## [3.4.27] - 2026-11-15

### Fixed
- 工作流停止改为幂等操作（Issue #1156）：外层停止已经落盘、向内部采集传播停止时中断或遇到临时写锁后，再次 `wizard --stop` 会补齐内部停止标记，不再误报 `WORKFLOW_NOT_FOUND`；断点损坏时仍先保存外层停止，不存在和已完成任务的拒绝契约保持不变。

## [3.4.26] - 2026-11-15

### Fixed
- `show` / `me` / `status` 的平台数据形状漂移不再报 `INTERNAL_ERROR`（Issue #1148）：`jobCard` 或 `zpData` 不是对象时，`show` 按 `JOB_NOT_FOUND` 返回（与 `jobCard: null` 一致），`me --section user` 输出空字段，`status --live` 的 `user_name` 回退为「未知用户」；合法响应的字段值与类型保持原样。
- 招聘者简历的求职期望与联系方式交换响应形状漂移不再报 `INTERNAL_ERROR`（Issue #1150）：`showExpectPosition` 不是对象时按空期望处理，其余简历信息继续返回；交换动作已获平台成功响应时，可选 `zpData` 容器漂移不再把已成功动作误报为失败，合法对象字段继续保留。
- 推荐列表与评分期望的非对象成功响应不再让 `recommend` 报 `INTERNAL_ERROR`（Issue #1152）：推荐载荷漂移时按空列表返回并保留未知分页标记；`--with-score` 的期望载荷漂移时按空偏好评分，合法职位继续返回。

## [3.4.25] - 2026-11-15

### Fixed
- 沟通 / 面试列表的元素与容器漂移不再让 `chat` / `pipeline` / `digest` 报 `INTERNAL_ERROR`（Issue #1143）：`result` / `friendList` / `interviewList` 混入 `null`、数字、字符串时跳过坏元素，容器漂移成字符串或对象时按空处理，其余条目照常返回；wizard 的 pipeline / digest 步骤与联系人查找共用同一口径。
- `friend_detail` 的 `zpData` 形状漂移按「未找到唯一匹配候选人」拒绝执行聊天动作（Issue #1144）：此前非对象真值会让 `.get` 抛 `AttributeError` 并以 `INTERNAL_ERROR` 中断，回复 / 交换联系方式等动作都不会执行；合法响应行为不变。
- 聊天消息数组的元素与字段漂移不再让 `chatmsg` / `chat-summary` 报 `INTERNAL_ERROR`（Issue #1145）：`messages` / `historyMsgList` 里的非对象元素被跳过，`from` / `body` 漂移按空处理，`text` 统一转文本；招聘者聊天历史的游标分页共用同一入口。

## [3.4.24] - 2026-11-15

### Fixed
- `boss clean` 在搜索缓存表结构漂移时返回 `CACHE_DATABASE_INVALID` 信封（Issue #1139）：本地 SQLite 的 `search_cache` 表缺列（例如旧版本建库）时，清理逻辑直接执行 `SELECT ... response`，sqlite 抛出的 `no such column` 此前穿透到通用兜底，stdout 为空并以 traceback 退出，`--dry-run` 同样如此。现在这类 `sqlite3.DatabaseError` 归一成指明缓存库路径的信封，恢复指引为重建缓存库。
- 简历解析对平台返回的 `null` 与漂移 section 按空处理（Issue #1140）：`hr resume` 在 `geekDetailInfo`、`geekBaseInfo`、`geekWorkExpList`、`geekCertificationList` 等字段为 `null` 或元素混入非对象时，此前抛 `TypeError` / `AttributeError` 并报成 `INTERNAL_ERROR`。现在按空 section 解析并照常输出其余字段；`workEmphasis` 等标量字段也走宽松读取。
- 平台列表接口里的非对象元素被跳过而不是让整个命令失败（Issue #1141）：`history`、`interviews`、`recommend`、`greet`、`export` 与联系人去重此前对 `jobList` / `interviewList` 里的 `null`、数字、字符串元素直接读键，抛出异常并让整次调用失败。现在这些元素被跳过，其余条目照常返回；新增 `api.models.dict_items` 作为统一入口。

## [3.4.23] - 2026-11-15

### Fixed
- `export -o` 不再在失败时破坏已有文件（Issue #1134）：序列化 / 写入此前直接以 `"w"` 打开目标文件，平台字段里的列表混入非字符串元素时 `join` 抛错，用户上一次的导出已被截断。现在整份内容先在内存组装并写同目录临时文件，成功后才原子替换；`skills` / `welfare` 列表元素按文本拼接；写入失败返回 `LOCAL_IO_ERROR`，不再报 `INTERNAL_ERROR`。
- 采集导出的三份输出要么一起更新要么都不更新（Issue #1135）：`jobs.json` / `jobs.csv` / `jobs.xlsx` 此前顺序非原子写入，XLSX 因控制字符序列化失败时其余文件已被覆盖；同一失败还被兜成可重试的 `NETWORK_ERROR`，或让运行以 `ok=true` 停在必然失败的断点上。现在各写临时文件、全部成功后替换；XLSX 写入前去掉 openpyxl 不接受的控制字符（JSON 保留原文）；标签 / 福利的非字符串元素按文本拼接；导出失败以 `LOCAL_IO_ERROR`（不可重试）回报并保存断点。
- 搜索缓存的 `pagination` 形状漂移按未命中处理（Issue #1136）：缓存条目的 `pagination` 不是对象或 `null` 时，`boss search` 此前原样把它放进成功信封，下游按对象读取会出错。现在这类缓存视为未命中并重新请求平台。

## [3.4.22] - 2026-11-15

### Fixed
- `boss wizard --stop` 不再被损坏的内部采集结果挡住（Issue #1128）：`WorkflowStore.request_stop` 此前先读取叠加了实时采集结果的 run，绑定的 crawl 任务里只要有一条 `crawl_jobs.payload` 解码失败，就在写入停止标记之前以 `CACHE_RECORD_INVALID` 退出，workflow 仍是 `running`、内部 crawl 也收不到停止请求。现在先落盘停止标记，再只读取 workflow 自己的步骤记录把停止传给内部 crawl；展示用的实时结果读不出来时退回断点本身，仍返回已停止的状态。workflow 自己的断点列损坏时停止标记同样会落盘，长动作轮询停止信号也不再读取实时采集结果。`--status` 对损坏记录的报告不变。
- `boss wizard --resume` 不再把只落盘一半的写失败报成执行中（Issue #1129）：写步骤以 `ACTION_UNCONFIRMED` 失败时，步骤与 run 的失败状态分两次提交；执行器在两次提交之间消失后，断点停在 run=`running`、step=`failed`，恢复原样返回它，命令 exit 0、`ok=true`、`error=null`。现在恢复会把 run 归并为 `failed` 并带上该步骤的错误与核对指引，命令 exit 1；这次写操作仍然不会重放。
- 招聘自动化的队列与状态文件类型漂移统一返回 `AUTOMATION_STATE_INVALID`（Issue #1130）：`pending-actions.jsonl` / `human-review-queue.jsonl` 里某条记录的 `confidence` 不是数字时，`boss agent pending list`、`agent review list / approve / reject` 此前 stdout 为空并以 `ValueError` traceback 退出；`state.json` 的 `conversations` / `autonomy` / `safety` 或 `autonomy.circuit_breaker` 不是 object 时，`agent stats` 与 `agent run` 被报成 `INTERNAL_ERROR` 并让用户升级或提 issue。现在两类情况都给出指明文件与字段的信封，队列损坏时 `recovery_action` 指向实际出问题的文件；校验失败不改写队列，也不执行其中的动作。同时覆盖 `conversations` 的条目不是 object、`safety.consecutive_errors` 不是整数，以及待执行或待复核记录的 `action` 不是已知动作（此前 `agent run` 报成可重试的 `NETWORK_ERROR`）。数字、数字字符串和缺省的 `confidence` 照常读取；布尔值、非有限数和超出浮点范围的整数按损坏处理。


## [3.4.21] - 2026-11-15

### Fixed
- 后台采集恢复在启动 worker 前校验断点参数（Issue #1125）：`CrawlSettings.from_dict` 已能识别缺键与类型漂移，但后台父进程此前跳过它，以 `ok=true / queued` 回报、清除旧 stop 并启动随后立即失败的 worker。现在持有执行锁重读记录后，在修改 stop、刷新时间戳和探测依赖/登录态之前返回 `CACHE_RECORD_INVALID`；原记录保留，已有执行器与交接中的队列维持幂等处理。

## [3.4.20] - 2026-11-15

### Fixed
- `crawl run` / `crawl start --hook-profile screenshot-full` 缺少 `--hook-dir` 时在创建任务之前返回 `INVALID_PARAM`（Issue #1116）：此前任务会先落盘，打开浏览器并导航到平台之后才在注入阶段失败，被记成 `status=stopped` 的成功信封；`crawl resume` 没有 `--hook-dir` 参数，重试只会重复同一错误并白开一次有头 Chrome。schema 与文档早已写明两者必须成对，现在入口按同一规则拒绝，不创建 run、不启动浏览器。
- 缓存库写锁冲突不再以 traceback 退出或被报成 `NETWORK_ERROR`（Issue #1118）：SQLite 头正常时，`CacheStore` 初始化与写操作里的 `database is locked` 现在归一成新登记的 `CACHE_BUSY`。此前 `crawl stop` 没有异常处理，stdout 为 0 字节并带 traceback；`crawl resume` 被统一装饰器兜成可重试的 `NETWORK_ERROR`，`boss wizard` 的通用兜底同样如此。现在 `crawl stop` / `status` / `results` / `shortlist`、`crawl resume` 与 wizard 动作都返回单个可重试信封，恢复动作是「等待写入结束后重试」，不再提示删除缓存库或重试平台请求。
- crawl 断点参数缺键或类型漂移按记录损坏返回 `CACHE_RECORD_INVALID`（Issue #1117）：`CrawlSettings.from_dict` 原来直接下标读取 `query` / `city_code` / `pages` / `with_detail`，缺键的 `KeyError` 被命令层报成「未找到 crawl run: <字段名>」并把调用方引向新建任务；`pages="abc"` 被报成 `INVALID_PARAM`（修正 crawl 配置），`pages=null` 落到 `INTERNAL_ERROR`。现在在读取边界校验必填字段与类型（`pages=true` 也按类型漂移拒绝），错误信息点名具体字段，恢复指引为重建任务；合法记录行为不变。
- crawl 监听翻页按请求页码匹配响应包（Issue #1114）：DrissionPage 的 `Listener.wait()` 返回队列头，而断点恢复先导航到列表第 1 页、再滚动到断点页，队列里依次是 1..N 页的响应。此前直接取队头，会把第 1 页数据记成断点页、断点照常前进并以 `ok:true` 结束。现在按监听请求 URL 的 `page` 参数匹配目标页，跳过导航/滚动带出的早期页；所有响应在筛选页码前先检查风险码、安全页和 HTTP 403，早期页的限流响应也保留并交给冷却处理，避免跳过停止信号。页码读不出的包只在请求第 1 页时接受，超时仍走页面内同源请求兜底。
- 后台恢复在预检之前清除旧 stop 标记（Issue #1115）：预检窗口（可选依赖探测、读取登录态）内到达的 `crawl stop` 会被随后的清理当成启动前残留一并清掉，worker 继续采集而停止请求丢失。现在旧 stop 先清、预检失败时以单条更新恢复旧标记和时间戳，保留期间新到的停止请求；成功时只刷新交接时间、不清新 stop，既保留 worker 的停止指令，也避免无旧 stop 的过期队列被重复拉起。

### Added
- 错误码 `CACHE_BUSY`（缓存库被其他连接写入，可重试）：登记在 `ERROR_CODES`，`docs/troubleshooting` 中英文补充说明。

## [3.4.19] - 2026-11-15

### Fixed
- 发布验收的子进程失败保留 stderr 原因（Issue #1107）：`scripts/verify_release.py` 只报「uv 失败，退出码 1」，把 stderr 整个丢掉——刚发布后 PyPI 索引尚未传播与真实依赖冲突在报告里完全一样，维护者只能重跑碰运气（3.4.17 发布时 `--pypi-version` 首跑失败、数分钟后通过，原因没有留下任何证据）。现在失败时的 `steps[].error` 带上 stderr 的有界尾部（末尾 12 行、单行 300 字符）；凭据先抹成 `[REDACTED]` 再截断（URL userinfo、无 scheme 的 `user:pass@host`、`token=` / `password=` 等键值与 Bearer 令牌），避免 `@` 落在截断点之外时漏出前缀；超时与 `allow_failure` 探针路径不变，无输出时保持原来的一句话。
- MCP 显式 `null` 参数与缺键等价（Issue #1108）：文档与 CHANGELOG 自 3.3.x 起承诺「传 `null` 与不传等价于省略」，映射层也为 `None` 留了判断，但入口的类型检查会先把 `null` 判成 `INVALID_PARAM`（如 `boss_wizard.action: null` 得不到文档承诺的默认 `run`，`sort` / `section` 等可选参数同样被拒）。现在 `call_tool` 在类型检查与参数映射之前剔除值为 `null` 的键；必填参数传 `null` 按缺参返回「缺少必填参数」，不会把 `str(None)` 拼成 `"None"` 发给 CLI。数组内部的 `null` 元素、未知参数与原有类型校验不变。

## [3.4.18] - 2026-11-15

### Fixed
- MCP 数组参数按 `items.type` 逐元素校验（Issue #1105）：入口此前只检查参数的顶层 JSON 类型，`add_tags: [false, {"k": "v"}]` 会被映射层用 `str()` 拼成 `--add-tag False --add-tag "{'k': 'v'}"` 并写进本地标签，信封仍是 `ok:true`；`friend_ids` 里的错误元素要到 CLI 才得到一条不指明位置的参数错误，数字字符串则被隐式接受。现在 `boss_shortlist_annotate.add_tags` / `remove_tags`、`boss_crawl_shortlist.selectors`、`boss_wizard.requested_steps` 与 `boss_hr_last_messages.friend_ids` 的元素类型不符时，在进入参数映射和 CLI 之前返回 `INVALID_PARAM`，错误带下标（如 `add_tags[1] 应为 string，收到 bool`）；单个数组最多逐条列出 3 处，其余只报数量。合法数组与空数组的行为不变。

## [3.4.17] - 2026-11-15

### Fixed
- MCP 位置参数保留以连字符开头的文本（Issue #1102）：搜索词、预设名、消息或标识符为 `-synthetic` / `--help` / `--` 时，原映射会让 Click 将其解析为选项，导致参数错误或意外输出帮助。现在把真实选项保留在前，并用 `--` 分隔位置参数；普通输入的参数顺序不变。按工具目录遍历全部位置参数和 action 分支，验证文本原值、附加选项及 CLI 成功 / 错误信封。

## [3.4.16] - 2026-11-15

### Fixed
- 恢复命令不再重复改写路径或引号内的名称（Issue #1100）：数据目录含 `boss chat` / `boss doctor` 等文字时，逐项替换会再次改写刚插入的路径并破坏命令。错误信封、风控契约、浏览器来源提示与向导恢复文案统一只扫描原文一次，保留已有命令中的路径参数，前文名称或路径中的词内撇号也不会打乱引用边界；身份诊断省略可选上下文时继续返回通用 `boss hr whoami` 示例。
- 采集结果与错误信封的 `message` / `data.error` 保留自定义 `--data-dir`（Issue #1097）：`CrawlRiskError` 在 `crawler/` 层构造，文案里嵌的 `boss clean --risk-lock` 是裸命令；`error.message` 之前只做脱敏不做改写。现在 `handle_error_output` 在出口统一按运行上下文改写 `message`，`crawl start` / `crawl resume` / `crawl status` 的 `data.error` 同样指向本次运行的命令；不含命令的文案与无上下文的纯函数调用保持原文。
- `boss doctor` 检查项的恢复文案保留自定义 `--data-dir`（Issue #1095）：`checks[].hint` 与 `checks[].recovery_action` 仍写死裸 `boss doctor --probe-cookies` / `boss login` / `boss hr whoami`，而同一信封的 `hints.next_actions` 已按运行上下文改写。自定义 `--data-dir` 的用户照抄检查项里的命令，诊断或核对的仍是默认目录。现在这些检查项同样指向本次运行的 `boss [--platform ...] --data-dir <dir> ...`；无上下文时保持通用示例。
- 命令层与信封的恢复文案保留自定义 `--data-dir`（Issue #1092）：错误码登记表里 42 个错误码的 `recovery_action` 写死裸 `boss resume list` / `boss crawl status` / `boss doctor` / `boss shortlist add` 等，而 `handle_error_output` 只对认证类与风控码按运行上下文改写，其余错误码直接透传登记表原文——自定义 `--data-dir` 的用户按提示操作时，命令会落到默认目录，看不到自己的简历、采集记录或 workflow，容易以为任务丢了而重建。现在登记表恢复文案在信封出口统一改写为本次运行的 `boss [--platform ...] --data-dir <dir> ...`（TTY 与 JSON 两条路径一致），认证类错误仍优先给登录命令；调用方显式传入的恢复文案、无上下文的 `boss schema` 文档与纯函数调用保持原文。新增守卫测试：登记表里出现的每条 `boss …` 命令都必须有改写条目。

## [3.4.15] - 2026-11-15

### Fixed
- 向导 TTY 恢复文案的其余命令保留自定义 `--data-dir`（Issue #1090）：`AI_NOT_CONFIGURED`、`CONTACT_NOT_FOUND`、`ALREADY_APPLIED`、`EXCHANGE_NOT_AVAILABLE`、`SAVED_SEARCH_INVALID`、`CRAWL_BUSY`、`UNKNOWN`、`RESUME_NOT_FOUND` 等静态恢复文案仍写死裸 `boss ai config` / `boss chat` / `boss doctor` / `boss resume list` 等；actions 已算好带数据目录的恢复句子（如 watch 失败给 `watch remove`）时还会被静态标签覆盖。空简历面板与 AI 辅助的输入提示同样只给裸 `boss resume import`。现在向导失败面板与空态提示把命令改写为本次运行的 `boss [--platform ...] --data-dir <dir> ...`，动态指引优先采用，无上下文时保持通用示例；另有守卫测试要求静态文案里出现的每条 `boss …` 命令都有改写条目。

## [3.4.14] - 2026-11-15

### Fixed
- TTY 风控恢复提示保留自定义数据目录（Issue #1087）：`boss status` 的本地风控锁定提示与向导交互式失败面板仍写死裸 `boss clean --risk-lock`，照抄时会在默认目录空转、本次 `--data-dir` 的锁仍在；runner 早已算好带 `--data-dir` 的恢复动作（Issue #978），却在向导静态文案里被覆盖。现在 `status` 传入当前上下文的解锁命令并按字面转义，向导文案把动态命令替换进面向真人的措辞。

## [3.4.13] - 2026-11-15

### Fixed
- `boss status` 的未登录提示按字面显示登录命令（Issue #1083）：提示命令保留当前 `--data-dir`，数据目录含 `[green]` 等方括号目录名时会被 Rich 当作样式吞掉，终端显示的路径不完整。现在转义该命令文本，并覆盖终端渲染回归。

## [3.4.12] - 2026-11-15

### Fixed
- 向导不再因扫码内核缺失而阻断 Cookie / CDP 登录（Issue #1075）：登录门此前在整条认证链之前检查 patchright 内核，最后一级扫码降级的前置条件也被套到浏览器 Cookie 提取与已有 CDP 登录上，直接 `boss login` 能成功的环境在向导中反而无法继续。现在先走既有认证链，仅在实际报告扫码内核缺失后读取内核诊断并展示安装指引；可复用登录态时无需扫描内核，风控错误仍立即停止。双语排障文档同步。
- 向导的内核安装指引按字面显示解释器路径（Issue #1079）：路径含 `[green]` 等方括号目录名时，详情此前被当作 Rich 样式解析，终端显示的安装命令会丢失部分路径。现在转义详情文本，保留面板自身的样式；POSIX 与 Windows 命令均覆盖终端渲染回归。

## [3.4.11] - 2026-11-15

### Fixed
- Windows 上的浏览器内核安装恢复命令可以直接执行（Issue #1072）：3.4.9 起该命令用运行 boss 的解释器路径拼出，但引用规则按 POSIX 处理，反斜杠不在安全字符集内，Windows 上任何路径都会被包上单引号——PowerShell 报 `Unexpected token '-m'`，cmd.exe 把整段当成文件名。现在 Windows 上路径不含需转义字符时原样输出（PowerShell 与 cmd 均可执行），含空格等字符时按 PowerShell 写成 `& '<路径>' -m patchright install chromium`；`BROWSER_KERNEL_MISSING` 信封、`boss doctor` 提示、向导文案与 PDF 导出失败的恢复文案同步生效，POSIX 行为不变。
- 浏览器内核预检遵循 `PLAYWRIGHT_BROWSERS_PATH`（Issue #1073）：patchright 会按该环境变量安装和启动浏览器，`boss doctor` 与向导登录门此前却只扫描默认缓存，自定义目录已安装时误报缺失、默认缓存有旧安装时又可能误报正常。现在显式配置时只检测驱动实际使用的目录，支持相对路径的 `INIT_CWD` 解析与 `0` 指定的包内 `.local-browsers`；未配置时保持原有默认探测。双语排障文档同步。

## [3.4.10] - 2026-11-14

### Fixed
- 补装一个可选依赖不再顶掉另一个（Issue #1068）：`uv tool install` 每次按给定的依赖组重建工具环境，而 `CRAWL_UNAVAILABLE` 的恢复指引只写 `boss-agent-cli[crawl]`、`boss-mcp` 缺 SDK 的启动提示只写 `boss-agent-cli[mcp]`——已装 `[mcp]` 的用户照前者执行后 MCP SDK 被移除，再照后者执行又把采集依赖移除，两条指引互相循环，通过 MCP 使用采集的用户正好落在其中。现在两处指引（含 `crawl` 各入口、`agent crawl --query`、向导采集步骤与向导终端文案）给出的安装要求都会带上当前解释器里已经可用的依赖组，例如 `boss-agent-cli[crawl,mcp]`；只装了某个组的一部分时不算已安装。双语命令参考与排障文档补充说明。
- MCP 主机用绝对路径启动 `boss-mcp` 时工具调用不再全部 `CLI_ERROR`（Issue #1066）：底层 CLI 此前只按 MCP server 进程的 `PATH` 查找 `boss`，而桌面端主机通常不继承登录 shell 的 `PATH`，`uv tool install` / `pipx install` 的入口目录不在其中——`initialize` 与 `tools/list` 正常，每一次 `tools/call` 都返回「无法启动底层 boss CLI」；`PATH` 上若另有一份旧版 `boss`，还会被静默驱动。现在未传 `--boss-bin` 时优先使用与 `boss-mcp` 同一安装的 `boss` 入口（运行 server 的解释器旁边），该入口不存在时才回退到按 `PATH` 查找；显式 `--boss-bin` 行为不变。双语排障文档同步。
- 便携包的内核安装改用运行 boss 的解释器（Issue #1064）：`install.sh` 此前用 `uv tool run patchright install chromium`，它会独立解析一个 patchright 版本，可能与 wheel 内的浏览器修订版不一致，装完仍报内核缺失。现在从 `boss` 入口脚本取工具环境解释器并调用 `-m patchright install`，手动重试命令同样打印完整可执行写法；便携包说明的 Windows 段改为指向 `boss doctor` 给出的命令。

## [3.4.9] - 2026-11-14

### Fixed
- 工具式安装下浏览器内核安装指引不可执行（Issue #1060）：按推荐方式 `uv tool install` / `pipx install` 安装后，只有 `boss`、`boss-mcp` 进入 PATH，patchright 留在工具环境里，而信封的 `error.recovery_action`、`hints.next_actions`、向导文案与文档都让用户执行裸 `patchright install chromium`——实测直接 `command not found`（rc=127），Agent 照抄必然失败。现在所有内核安装指引改用运行本 CLI 的同一解释器（`<python> -m patchright install chromium`，缺 headless shell 时为对应变体），版本与启动浏览器时一致；`uv run` / 源码环境同样成立。
- 向导登录门的内核预检从不拦截（Issue #1060）：`_browser_kernel_status()` 把缓存根目录传给按条目名精确匹配的判定函数，永远匹配不上，`status == "error"` 分支不可达，内核真缺失时仍会去开浏览器再失败；同时该函数此前只会返回 `ok` / `warn`，`boss doctor` 把内核缺失报成 `warn`、摘要停在 `degraded`。现在预检展开 `chromium-<修订版>` 条目再比对，缺内核判定为 `error`，`doctor` 的 `patchright` 检查也改为按解释器导入判断，不再因 `uv tool install` 未把 patchright 暴露到 PATH 而误报缺失。
- 新命令贡献指南与当前工程结构对齐（Issue #1057）：中英文清单改为在 `mcp_tools.py` 定义工具、在 `mcp_args.py` 映射参数，说明兼容导出和协议工具目录核对；移除对本地忽略文件的必改要求，补齐新增模块 mypy 严格登记、能力计数与变更记录同步步骤。

## [3.4.8] - 2026-11-14

### Fixed
- 默认安装未包含 MCP SDK 时，`boss-mcp` 不再只输出 `ModuleNotFoundError` 堆栈（Issue #1053）：安装后的命令改走轻量启动入口，只有顶层 `mcp` 包缺失时向 stderr 输出 `[mcp]` 的 pip / uv tool / 源码安装说明并以 1 退出，stdout 保持为空；SDK 可用时原样转发全部参数，SDK 内部其他依赖错误保留原始诊断。双语排障文档同步，stdio 回归逐名核对新旧入口的工具列表。

## [3.4.7] - 2026-11-14

### Fixed
- 未安装 `crawl` 可选依赖时采集不再返回成功信封（Issue #1050）：默认安装不带 DrissionPage 与 openpyxl，依赖缺失此前要到打开浏览器才被发现，执行器把它当普通异常落成 `stopped`——`crawl run` 返回 `ok: true` 与 `crawl resume` 指引，照做得到同样的结果，可以无限重复，每次还新建一条执行不了的任务；`crawl start` 返回 `queued` 后由后台进程落成 `stopped`，`agent crawl --query` 先建任务再失败，向导采集步骤落成 `NETWORK_ERROR` 加「恢复当前任务」。登记表里为此准备的 `CRAWL_UNAVAILABLE` 在真实路径上到不了。现在 `crawl run` / `start` / `resume`、`agent crawl --query` 与向导采集步骤在创建任务、启动后台进程、读取登录态之前确认依赖可导入，缺失时返回 `CRAWL_UNAVAILABLE`（`exit 1`），恢复动作指向安装 `boss-agent-cli[crawl]`，不给出恢复命令；模块可定位但导入失败时，执行器保存断点后同样按 `CRAWL_UNAVAILABLE` 回报。`crawl status` / `results` / `stop` / `shortlist` 不受影响。文档补上 pip / uv tool 的安装方式。

## [3.4.6] - 2026-11-14

### Fixed
- 排队任务的后台进程消失后可用后台恢复重新启动（Issue #1045）：`crawl resume <run_id> --background` 把 `queued` 一律视为原 worker 即将接手，不探测执行锁也不启动进程。worker 若不经过清理路径就消失（被强制终止、机器重启），任务永远停在没有错误的 `queued`，后台恢复每次都返回 `queued`，`crawl status` 的后继动作只有 `results` 与 `stop`。现在这个保护只在 30 秒启动窗口内生效；排队更久的任务与遗留的 `running` 一样先探测执行锁：仍被占用返回 `running`、不改动任务，空闲则做登录预检、解除旧停止请求并启动 worker。`crawl status` 对排队过久的任务额外给出 `crawl resume <run_id> --background`。执行锁仍保证同一任务只有一个执行器，后到者以 `CRAWL_BUSY` 退出且不改写任务。中英文命令参考同步。
- `agent crawl --query` 向采集浏览器注入登录态（Issue #1044）：`crawl run` / `start` / `resume` 与向导采集步骤都会把 `boss login` 的 Cookie 与 User-Agent 注入独立采集浏览器，只有 `agent crawl --query` 直接用了不带登录种子的传输。采集浏览器的配置目录与日常 Chrome 隔离，没有可用会话时页面是登录页，采集按未登录落为 `risk_stopped` 并写下本地风险锁——已登录的用户也会被要求手动执行 `clean --risk-lock`。现在该入口与其他采集入口共用同一份登录种子；本地登录态缺失、没有 Cookie 或缺少 `wt2` 时在打开浏览器之前返回 `AUTH_REQUIRED` 与登录指引，不创建任务，参数错误仍先报出；`agent crawl --run-id` 只读本地结果，不受登录态影响。中英文命令参考同步。
- 未登录时启动后台采集不再留下永久排队的任务（Issue #1042）：`crawl start` 只创建任务并拉起后台进程，登录态由 worker 检查；worker 的信封写进空设备，未登录时它以 `AUTH_REQUIRED` 退出却不改写任务，调用方拿到 `ok` 与 `queued`，之后 `crawl status` 一直是没有错误的 `queued`，`crawl resume --background` 又把 `queued` 视为原 worker 即将接手而不再启动。现在 `crawl start` 与需要启动 worker 的 `crawl resume --background` 先按采集实际使用的口径检查本地登录态，缺失、没有 Cookie 或缺少 `wt2` 时返回 `AUTH_REQUIRED` 与登录指引，不创建任务、不解除停止请求、不启动进程；参数错误仍先报出，已完成或排队中的任务不受影响。worker 在接手前退出而任务仍显示 `queued` 或 `running` 时，把它落为 `stopped` 并写明原因与错误码（风险锁只记错误码）；任务已处于终态、另一个执行器持有执行权或本次是被 `CRAWL_BUSY` 拒绝时不改写；前台恢复失败不改动任务。中英文命令参考同步。

## [3.4.5] - 2026-11-14

### Fixed
- 采集进程突然退出后可从遗留的 `running` 状态后台恢复（Issue #1038）：此前 `crawl resume <run_id> --background` 只看持久化状态，worker 已退出、执行锁已释放时仍回报 `running` 而不启动恢复，任务持续空转。现在父进程用现有执行锁判断是否仍有执行器，空闲时重读断点并允许恢复遗留的 `running`；持锁任务不启动第二个 worker、不修改 checkpoint 或清除 stop。探测锁在启动前释放，worker 再自行取锁；锁不可用时返回 `LOCAL_IO_ERROR`。真实子进程突然退出的离线回归验证只续抓未提交页面、保留累计请求预算，中英文命令参考同步。
- 已停止采集任务的显式后台恢复会解除旧停止请求（Issue #1036）：此前 `crawl resume <run_id> --background` 返回 `queued`，但子进程因 `--from-queue` 保留旧 `stop_requested`，刚开始执行便再次停止，无法续跑。现在父进程在启动 worker 前清除旧标记，worker 仍保留启动期间新收到的 stop；对已有 `queued` 任务及仍在执行的 `running` 任务，重复恢复不清除停止请求、不拉起新进程。离线回归执行真实队列入口，覆盖正常续跑与启动途中再次停止。
- Agent 快速上手补上 `CRAWL_BUSY`（Issue #1033）：3.4.4 的采集执行互斥已写进命令参考与排障文档，但 Agent 快速上手的错误码清单只有同类的 `WORKFLOW_BUSY`，中英文都缺这一条——Agent 遇到并发恢复被拒时找不到处理说明，容易重试或 `--stop` 让位，正是该错误码要阻止的行为。现在两份快速上手的错误码清单与采集编排一节都写明「查 `crawl status <run_id>`、等当前执行结束再恢复，`status` / `results` / `stop` 不受影响」，并新增文档守卫要求两个并发码在中英两侧同时出现。
- HTML / JSON 导出的通用失败不再提示安装浏览器内核（Issue #1031）：`resume export` 的失败分流在内核缺失与本地 IO 之外，第三条「渲染 / 模板错误」仍直接复用错误码登记表里拆分前的 `EXPORT_FAILED` 恢复文案「检查 patchright 安装」——HTML / JSON 导出只用本地模板与文件写入，从不启动浏览器，照做只会装一个没用到的内核。现在 PDF 保留内核提示（浏览器确实参与渲染），HTML / JSON 改为提示检查简历文件内容与导出选项，并说明该格式不使用浏览器内核；新增回归要求 HTML 渲染失败的信封里不出现 `patchright`，PDF 的通用失败仍保留原指引。
- Node 回归脚本的子进程超时统一为 30 秒（Issue #1029）：`test_fetch_script_marks_non_json_body` 等四条用例的预算原为 5–15 秒，相对脚本实测的约 40 毫秒本有余量，但 CI 跑步机在四个矩阵任务与 lint 并发时会出现秒级冷启动卡顿，`Test (Python 3.14)` 因此把必需检查打成假红一次，重跑才恢复。现在四条用例与其余 Node 回归一样使用 30 秒预算；脚本真的挂起仍会在 30 秒内失败，断言与重试策略不变。

## [3.4.4] - 2026-11-14

### Fixed
- 采集执行器收到退出信号时不再把未完成任务误记为完成（Issue #1024）：`SystemExit`、`GeneratorExit` 等非普通异常此前会跳过错误分支，再由 `finally` 写回默认的 `completed`，导致普通恢复直接返回而不再抓取剩余页面或补全详情。现在保存 `stopped` 与现有页游标、待补详情和请求预算，完成清理后原样传播退出信号；显式恢复只续跑未完成工作，已提交页面不重抓。回归覆盖浏览器打开、列表抓取和详情补全三个阶段，并验证真实子进程保留原退出码。
- 同一采集任务的重叠恢复不再重复抓页（Issue #1023）：执行器现在先取得该 `run_id` 的跨进程独占执行权，再读取页游标、待补详情与预算，持有至本次运行结束。后到者返回 `CRAWL_BUSY`（`recoverable: true`，`--exit-codes sysexits` 下为 75），不发出请求、不推进断点、不清除停止请求；`hints.next_actions` 只给同一数据目录的状态查询，向导失败信封与失败任务的状态查询也遵守这一口径。新建、后台 worker 与向导采集共用这条执行路径，创建回调发布编号后也会重读最新断点。状态、结果与停止请求继续可用；进程崩溃后内核自动释放执行权，无法建立锁时返回 `LOCAL_IO_ERROR` 并拒绝执行。Python API 可捕获 `CrawlBusyError`，当前执行结束后再恢复。
- 阻塞的失败 run 不再被建议 `--resume`（Issue #1021）：失败步骤的结果无法重放（`replay_safe: false`，如 `ACTION_UNCONFIRMED`、平台已成功但本地记账失败）时，runner 的恢复只会原样返回同一个失败，但 `boss wizard --status` 的 `hints.next_actions` 一直把 `--resume` 放在第一条，照做的 Agent 很容易把它读成「再试一次」而反复空转；同一状态下 TTY 的「恢复已有任务」列表却已经排除该 run。现在这类 run 只给 `--status` 与新建 workflow，步骤级的核对命令仍随状态返回；可重放的失败保留 `--resume`。阻塞判定从 TTY 菜单收进 `wizard/runner.has_blocked_step`，命令层与 TTY 共用同一口径。
- wizard 执行进程在写步骤进行中被终止后，恢复不再重放同一个写操作（Issue #1019）：执行器在写请求发出后、结果确认前被 kill / 关闭终端 / 断电（或 MCP 超时后宽限期内没退出被强制回收）时，步骤与 run 都停在 `running`，没有任何「结果未知」的记录，`boss wizard --resume <run_id>` 会把同一动作再执行一遍——离线复现中 `recruiter_reply` 对同一会话发送了两次，恢复后 run 显示 `completed`，与「结果未知的写操作不得重放」的契约相悖。现在 10 个写动作（求职者的投递 / 打招呼 / 联系方式交换 / 联系人标签，招聘者的查看简历 / 回复 / 请求附件简历 / 交换联系方式 / 职位上线 / 职位下线）在平台请求发出之前，先把 `ACTION_UNCONFIRMED`（`replay_safe: false`）与该动作的核对命令落到当前步骤上；结果确认后由实际结果覆盖。恢复时取得执行权后发现步骤仍是 `running` 却带着这条记录，说明上一个执行器没有记录结果就消失了：不执行动作，把步骤与 run 记为失败并返回 `ACTION_UNCONFIRMED` 与核对指引，之后的恢复同样不重放。只读步骤、crawl 续跑、尚未走到写请求的中断照常重跑；动作明确报告的可重放失败（如限流）会作废上一次尝试落盘的记录，自动重试之间被终止不影响恢复。记录落不了盘时不发出写请求。执行期间 `--status` 看到 `running` 步骤带 `ACTION_UNCONFIRMED` 即表示写请求在途。
- 同一 workflow 的重叠恢复不再重复执行写操作（Issue #1017）：两次 `boss wizard --resume <run_id>` 重叠时（两个终端、Agent 重试，或 MCP 超时后的再次调用），后到者读到的是旧执行快照，会把先到者已保存的 `completed` 步骤覆盖回 `running`，再把同一个动作执行一遍——离线复现中 `recruiter_reply` 对同一会话发送了两次，两次调用都返回 `completed`。现在执行器在读取快照之前取得该 `run_id` 的独占执行权（操作系统文件锁，不等待），持有到本次运行返回或抛出；取不到时返回新错误码 `WORKFLOW_BUSY`（`recoverable: true`，`--exit-codes sysexits` 下为 75），不执行任何步骤、不改动 checkpoint，`hints.next_actions` 只给同一数据目录的 `wizard --status <run_id>`。`--status` / `--stop` 不取执行权，执行期间照常可用；不同 `run_id` 互不阻塞。锁由内核持有，进程崩溃或被 kill 后自动释放，没有需要手工清理的陈旧锁；锁文件位于 `<data_dir>/cache/workflow-locks/`，以 `run_id` 的摘要命名，释放时删除。加不了锁（锁目录不可用、数据目录所在的文件系统不支持文件锁）时返回 `LOCAL_IO_ERROR` 并拒绝执行，不会退化成无锁执行；`--status` 此时仍可用。Python API 的 `WorkflowRunner.run()` 在同样情况下抛出 `WORKFLOW_BUSY` 的 `WorkflowActionError`。请求预算租约与执行权共用 `services.file_lock` 里的同一套平台加锁原语。
- 输出路径前置检查覆盖全部祖先目录（Issue #1015）：`export` / `chat --export` 的 `-o` 若形如 `blocker/nested/report.csv`，且 `blocker` 是普通文件，此前仅检查紧邻父目录，会漏检并先请求平台，沟通导出还会写入会话快照。现在向上检查至已有目录，遇到被文件或悬空符号链接占用的任意一级父路径即返回 `LOCAL_IO_ERROR`；有效目录链接与可创建的缺失目录继续允许，检查本身不创建目录或文件。
- 命令组兜底的本地 IO 错误补上可执行指引（Issue #1013）：没有 `@handle_auth_errors` 的本地命令（`stats` / `cities` / `config set` 等）写文件失败时由 `BossCliGroup` 的 `except OSError` 统一出 `LOCAL_IO_ERROR`，但信封的 `hints` 一直是 `null`——`recovery_action` 里的「确认目标路径是可写目录、磁盘空间正常」只是自然语言，Agent 读到的是空动作列表。现在 `LOCAL_IO_ERROR` 补 `hints.operator_actions`，`NETWORK_ERROR` 保持不补（「检查磁盘」对网络异常是误导）。
- `export` / `chat --export` 的 `-o` 参数错误在第一次平台请求之前暴露（Issue #1011）：此前 `-o` 指向目录时，`export --count 40` 会先翻 5 页平台数据、`chat --export` 会先取完整沟通列表并写 JSON 快照，之后才由 `prepare_output_path` 报一个与真正原因无关的信封——既消耗请求预算，也可能触发风控，而该做的只是换个文件名。现在新增 `services.output_path.inspect_output_path`（无副作用，不建目录）与 `commands/_output_path.validate_output_path_or_emit`，两条命令在调用平台之前完成校验：目标是目录或非普通文件 → `INVALID_PARAM`，父路径不是目录 → `LOCAL_IO_ERROR`。
- `resume export` 的失败信封按原因分流，不再一律指向浏览器内核（Issue #1009）：该命令没有统一异常装饰器，导出的三层失败此前都归成 `EXPORT_FAILED` +「检查 patchright 安装」。但 `--format html` / `json` 只用本地模板与文件写入、完全不碰 patchright：`-o` 指向目录或父目录不可写时，用户被告知去装浏览器内核，与真正该做的「换个路径」无关；PDF 导出命中内核缺失时又把整段 patchright 安装横幅和本机绝对路径塞进 `error.message`。现在内核缺失返回 `BROWSER_KERNEL_MISSING`（确定性失败，`next_actions` 附带本次数据目录的 `doctor`，异常文本只留首行进 `details.cause`），本地文件读写失败返回 `LOCAL_IO_ERROR`（不可自动重试，附可执行路径建议），其余渲染错误保持 `EXPORT_FAILED`；`prepare_output_path` 建父目录的失败也纳入同一出口，不再落进顶层网兜被报成「resume 本地文件读写失败」。
- 非登录命令不再把浏览器内核缺失报成可重试的 `NETWORK_ERROR`（Issue #1007）：patchright 升级后所需修订版 Chromium 未安装时，`sync_playwright().start()` / `chromium.launch()` 抛 `Executable doesn't exist` 或 `Playwright was just installed`，命令装饰器、`me` 的兜底与 wizard runner 此前都把它归成 `NETWORK_ERROR` +「重试 / 重试当前 run_id」。这是确定性失败，重试（含 `--resume` 与 runner 自动重试）永远复现，且每次都白起一个浏览器进程。登录链路早已识别该错误，现在识别逻辑收敛到 `services/browser_runtime.is_browser_kernel_missing` 供四处共用；`search` / `detail` / `greet` / `chat` 等装饰器命令、`me` 与 wizard 统一返回 `BROWSER_KERNEL_MISSING`，恢复动作为 `patchright install chromium`，`next_actions` 附带本次数据目录的 `doctor`，异常文本只保留首行进 `error.details.cause`。

## [3.4.3] - 2026-11-14

### Fixed
- `boss_wizard.action` 的非法取值不再静默新建运行（Issue #1002）：映射层原先把 `resume` / `status` / `stop` 之外的一切（`resum`、`STOP`、空串）都送进 `run` 分支，调用方以为恢复或停止已生效，实际却新建了一次 workflow 并丢弃 `run_id`。现在进入 CLI 前返回 `INVALID_PARAM` 并列出合法取值；缺省或 `null` 仍落回默认 `run`。新增覆盖全部工具枚举参数的守卫，禁止非法值被静默替换。
- 创建失败且同时被取消时，MCP 恢复指引改为指向安装 / `--boss-bin`（Issue #1000）：此前该分支沿用写操作中断文案，让 Agent 去核对并不存在的平台 checkpoint。子进程从未运行，现在与同步启动失败路径一致，保留 `WORKFLOW_STOPPED` 错误码与 `outcome_unknown=false`。
- MCP 在创建子进程期间被取消时不再丢失恢复标识（Issue #998）：该分支会回收底层 CLI，却丢弃其 stdout，`error.details` 缺少 `checkpoint_result`，与运行期间取消和超时路径不一致。现在两条中断路径共用同一信封构造逻辑，子进程输出的 checkpoint / `run_id` 在 `error.details.checkpoint_result` 保留；写工具仍标结果未知，只读工具可直接重调。
- 离线评估改为验证当前的平台风控停止契约（Issue #996）：过时场景曾将已取消的模式级 `COMPLIANCE_BLOCKED` 当作通过条件。现在用当前 `ACCOUNT_RISK` 信封验证不可自动恢复、单次模拟搜索后停止及恢复指引；评分器拒绝额外工具调用和未在信封中确认的风险码，回归覆盖账号与环境风险，双语评估说明同步。
- 发布后 PyPI 安装验收定向刷新目标包索引（Issue #994）：新虚拟环境仍会复用 uv 的旧索引缓存，刚发布的版本此前可能被误报为不存在，导致验收假失败。`verify_release.py --pypi-version` 现在为目标包指定 `--refresh-package boss-agent-cli`，依赖包继续复用缓存；本地 wheel 验收保持原语义。

## [3.4.2] - 2026-11-13

### Fixed
- 浏览器通道的非 JSON 写响应不再引导或触发重复写入（Issue #990）：此前这类响应被归为 `UNKNOWN`，单次 `greet` / `apply` 提示重试，`batch-greet` 与 wizard 会自动重发；招聘者交换动作抛错后也可能在下一轮自动化中重发。现在写请求返回 `ACTION_UNCONFIRMED` 与 `replay_safe: false`，未识别的写响应同样禁止自动重试，批量与 wizard 就地停止，恢复时不重跑该写步骤；读取仍允许按通道规则重试。单次命令、批量与 wizard 的求职者恢复指引分别通过同一数据目录的 `chat` 与 `me --section deliver` 核对招呼和投递；招聘者按会话或职位状态核对。副作用声明未知的招聘者简历端点也不再重放未识别的结果；交换动作抛错明确标记结果未确认，提示先核实。

- 结果未知的写操作信封统一声明 `replay_safe`（Issue #988）：`hr greet` 的 `GREET_RESULT_UNKNOWN`、`hr location` 的 `LOCATION_RESULT_UNKNOWN`、`hr accept-resume` 的 `RESUME_ACCEPT_RESULT_UNKNOWN` 以及 `apply` / `greet` / `hr location` 的 `LOCAL_STATE_WRITE_FAILED` 表示写操作可能已生效，但 `error.details` 原先只有 `sent: null` / `accepted: null`，没有快速入门文档承诺的 `replay_safe`；Agent 读不到判定，只能凭错误码文案猜，与本仓库「未知结果禁止自动重试」的契约脱节（wizard 路径一直正确携带）。现在错误码登记表为这些码声明 `replay_safe: false`，`output.envelope_error` 按登记表补齐；前三个「结果未确认」码同时声明并补 `outcome_unknown: true`，`LOCAL_STATE_WRITE_FAILED` 的平台结果已确认、只补 `replay_safe`。调用方给出的更精确值优先。新增守卫：文案承诺「禁止自动重发 / 重试」的码必须声明该字段。

- 子命令选择错误的下一步提示改为帮助命令（Issue #986）：`boss resume`、`boss resume bogus` 等缺少或写错子命令的调用，此前直接返回裸子命令，`resume clone` 会再次缺参，`resume init` 则会直接创建默认简历。现在两类错误都返回各子命令的 `--help`，供 Agent 读取用法后选择动作；回归逐组重放提示并拦截业务回调，覆盖嵌套组及两类错误。

- 批量招呼对结果未知的发送自动重发，并以 `ok:true` 回报（Issue #984）：`batch-greet` 把所有非风控异常统一「sleep 后重试一次」，包括 `ACTION_UNCONFIRMED`——该码表示请求已发出但响应中断（平台层 `__cli_error_details__` 明确 `replay_safe: false`、`outcome: unknown`）。第一次结果未知时再发一次招呼，就是在不知道是否已送达的情况下重复写操作；两次都未知后还输出 `ok:true` + `failed[0].error` 字符串，Agent 无从知道「这条可能已经发出」。现在 `ACTION_UNCONFIRMED`（响应字典与异常两种形态）与风控、预算停止同级：立即停批、不 sleep、不重试，返回 `ok:false`，`error.details` 带 `outcome_unknown: true` / `replay_safe: false` 与该未知项的完整详情，已成功项仍随 `details.greeted` 带回。普通网络错误（请求未发出）的一次重试语义不变。

- MCP 工具对枚举参数保留显式空串，CLI 的取值诊断不再被静默吞掉（Issue #982）：`mcp_args._build_args` 对 `sort` / `detail_status` / `from_who` / `format` / `section` / `tone` / `lang` / `type` 用真值判断，调用方传 `{"format": ""}`（模板渲染失败、拼写留空、编排器把缺失值序列化成空串）时被当成「没传」：`boss_stats(format="")` 静默按默认 `json` 执行、`boss_search(sort="")` 静默按默认相关性排序，信封仍是 `ok:true`，Agent 没有信号。现在这些枚举参数的空串原样下发，CLI 返回 `INVALID_PARAM` + 可选值列表；`null` 与缺键仍等价于未传（与 #958 的 `query` / `url` 一致），自由文本参数（`city` / `note` / `tone` 之外的 `context` 等）语义不变。新增结构门禁：schema 里声明 enum 的字符串参数必须登记在 `_ENUM_KEYS`，新增枚举参数漏登记时测试失败。

- 浏览器来源耗尽信封的恢复命令保留数据目录与平台（Issue #980）：`api/browser_source.py` 的 `POLICIES` 是 `BrowserSourceUnavailable` 信封的文案真源，其中内嵌的 `boss doctor`、`boss status --live` 与 `httpx-only` 指引里的 `boss clean --risk-lock` / `boss login --cdp` 都是裸命令。自定义 `--data-dir` 下 `doctor` 诊断的是默认目录的登录态、缓存与风险锁，`clean --risk-lock` 走错目录还会静默返回 `local_lock_removed: false`（同 #978）。现在 `display.handle_auth_errors` 与 `wizard/runner.py` 在拿到运行上下文时把策略文案里的命令改写成带平台与按 shell 参数转义的绝对目录的形式（`browser_policy_text` / `browser_policy_text_for_ctx`）；策略表本身保持无上下文的通用文案。新增结构门禁：策略文案里出现的 `boss <cmd>` 字面量必须登记在 `display._BROWSER_POLICY_COMMANDS`，新增来源漏登记会在测试现形。

- 风控停止契约的恢复命令保留数据目录与平台（Issue #978）：`display.RISK_ERROR_CONTRACTS` 是所有风控信封（`ACCOUNT_RISK` / `ENVIRONMENT_RISK` / `ENVIRONMENT_RISK_LOCKED`）的唯一真源，其中 `recovery_action` 与 `hints.next_actions` 嵌的 `boss clean --risk-lock` / `boss doctor` 是裸命令。风险锁写在 `<data_dir>/.cdp_risk_lock`：使用自定义 `--data-dir` 时，照抄提示的 Agent / 用户会在默认目录执行 `clean --risk-lock`，命令回 `ok` 且 `local_lock_removed: false` 不报错，原目录的锁原样保留，随后每次重试都撞同一个锁；`doctor` 诊断的也是另一份目录。现在 `risk_error_contract` / `platform_error_contract` 接受触发错误的数据目录（命令层传 `ctx`，wizard 传 `ActionContext`，无上下文的纯函数调用保持登记表通用示例），`doctor` 的 `cdp_risk_lock` 检查项与解锁指引同样定位本次诊断目录；风险判定、锁存语义与「必须由用户确认后显式解锁」的硬边界不变，静态 `boss schema` 仍展示通用示例。

- 向导动作层的恢复命令保留数据目录与平台（Issue #976）：`wizard/actions.py` 里 AI 未配置时的 `boss ai config ...`、AI API 报错时 `recovery_action_for` 生成的 `ai config` 建议、watch 参数损坏时的 `boss watch remove` / `boss watch add` 重建命令、不支持的 `hook_profile` 指引与 `boss config reset crawl` 都是裸命令。wizard 的 headless / MCP 调用可带 `--data-dir`：照抄提示会把新 AI 密钥、配置重置、watch 删除重建或 crawl 重建落到默认目录，原目录的问题原样保留（与 #913 / #919 等命令层迁移同一接缝，动作层此前漏迁）。现在这些命令统一经本地 `_hint_command` 包装携带按 shell 参数转义的绝对目录与平台；`_crawl_outcome_result` 的兜底恢复命令同样保留 `CrawlOutcome.data_dir`。

## [3.4.1] - 2026-11-13

### Fixed
- 向导自动重试读取当前步骤刚保存的 checkpoint（Issue #972）：`crawl_start` 创建内部采集并绑定后遇到可恢复错误，runner 此前仍把重试前的旧结果传给动作，导致另建任务、重复抓取已完成页面并使用新的计数预算。现在重试前刷新当前步骤的持久化结果，同时保留已完成前置步骤，恢复原内部任务的进度与预算。
- 采集在提交断点前校验列表与详情结构（Issue #970）：缺失或类型异常的 `zpData.jobList` 此前可能被当作空页，`zpData.jobCard` 则被当作空详情，任务仍标为完成，恢复也不会补取。现在结构异常进入既有有限重试，失败后保留当前页或待补详情；正常响应到来后显式恢复可继续补取，详情失败不重抓已提交的列表。
- MCP 只读白名单里带 action 维度的工具漏登记写模式时按写处理（Issue #968）：此前这类工具默认被判为只读，超时 / 取消提示可能引导 Agent 直接重试。现在从 schema 识别带 `action` 的工具，缺少该工具的写模式登记时保守标记可能产生写入；新增守卫要求显式登记，并验证漏登记时的判定。
- `ai local status` 的 TTY 渲染按字面显示配置值（Issue #964）：provider / model / base_url 是自由文本（`ai local configure --model` 不限格式，配置文件也可手改），直接拼进 Rich 时含 `[/bold]` 的值会抛 `MarkupError` 让整条 TTY 输出甩 traceback，含可解析标签的值会被吞成样式。现在这些值与推荐模型表格经 `display.escape_markup()` 转义后再进入 Rich；JSON 信封不变。同步补渲染级回归（含 `[link=...]` 不得渲染成 OSC 8 超链接）。
- MCP `CLI_ERROR` 的恢复指引按失败阶段与读写区分（Issue #962）：子进程没能启动（`boss` 不在 MCP server 的 `PATH` 中、`--boss-bin` 路径有误、文件不可执行）或 stdout 不是 JSON 信封时，信封一律回「检查本地 checkpoint 与平台实际结果，再显式恢复；未确认的写操作不要直接重试」——进程从未运行时不存在可核对的平台结果，只读工具也被描述成可能有写入，真正要处理的安装 / 路径问题只字未提。现在启动失败给出专用指引（未产生平台操作，确认安装或用 `boss-mcp --boss-bin` 指定路径）并带 `outcome_unknown=false`；输出不可解析时只读工具给只读指引且 `outcome_unknown=false`，写工具保留原指引并补 `outcome_unknown=true`，与超时 / 取消路径的详情字段一致。`CLI_ERROR` 仍为 `recoverable=false`。
- MCP `boss_config` 漏传 required 的 `action` 不再静默默认成 `list`（Issue #960）：schema 声明 `required: ["action"]`，映射层却用 `arguments.get("action", "list")` 私自补默认值，绕过客户端校验的调用（手写 JSON-RPC、宽松 SDK）拿到 `ok:true` 的全量配置，Agent 无从得知自己漏了参数；与其余 required 工具（漏传一律 `INVALID_PARAM` + `缺少必填参数`）行为漂移。现在 `action` 漏传走既有 KeyError 分支；需要默认行为的工具（`boss_hr_jobs` / `boss_wizard`）在 schema 声明非 required + default，映射层才允许补默认。新增逐工具守卫：任何工具漏传全部 required 参数必须返回 `INVALID_PARAM`，schema / 实现再漂移会在守卫现形。
- MCP `boss_export` 不再丢弃显式空串的 `query` / `url`（Issue #958）：参数映射用真值判断，`query:""` / `url:""` 被静默丢弃，Agent 收到的是误导性的「未提供 query，请传入搜索关键词或 --url」——明明传了值，读到的却是「你没传」。CLI 对 `export --url ""` 本有专门诊断「--url 的值不能为空」（为「flag 后面少了值」的真实场景设计），映射层吞掉空值后这条正确错误永远到不了 Agent。现在空串原样下发，未传与 `null` 仍等价省略；`boss_search` 的空 `query` 下发行为同步钉住。

## [3.4.0] - 2026-11-13

### Added
- 扫码登录风险错误补充本地脱敏诊断（Issue #863）：`error.details.login_diagnostic` 记录失败阶段、页面检查或响应监听来源、白名单顶层路径和可用的页签数量；响应事件另附状态码、资源类型、导航标识与主/子框架归属。仅读取已有事件属性，未知信息省略或标记未知，不输出完整网址、查询参数、凭据或页面正文；风险判定、锁存、停止与窗口清理行为保持不变。

### Fixed
- crawl 配置与后继提示保留数据目录（Issue #955）：`crawl configure` 成功后提示裸命令 `boss crawl run <关键词> --city <城市>`，缺配置项时的 `recovery_action` / `next_actions` 与 `crawl run` / `start` / `resume` / `status`、`agent crawl` 的 `crawl configure --help` 指引也不带 `--data-dir`。使用自定义目录时，照抄 `crawl run` 会落在默认目录：刚设置的 Chrome 路径 / 端口 / 请求预算不生效，采集断点与产物写进默认目录，随后在原目录 `crawl status <run_id>` 报 `CRAWL_RUN_NOT_FOUND`；照抄 `crawl configure` 恢复则配置了另一份目录，原目录命令仍报未配置。`CRAWL_UNAVAILABLE` 的恢复文案同样改为携带目录。现在这些提示统一经 `boss_command_for_ctx(..., include_data_dir=True)` 构造，保留按 shell 参数转义的绝对目录。
- TTY 渲染按字面显示平台外部字符串（Issue #953）：`display.py` 的职位表 / 职位详情 / 状态 / 通用列表 / 消息面板 / 分段记录与 `wizard/renderer.py` 的职位卡、列表预览、日报、采集摘要等 Rich 面板直接拼接公司名、职位名、职位描述、聊天内容与昵称，而 Rich 默认把字符串当 markup 解析——含未配对闭合标签（`[/bold]`）的值会抛 `MarkupError` 让整条 TTY 输出崩掉（不在 `BossCliGroup` 的任何 `except` 分支里，直接甩 traceback），含 `[link=https://...]` 的值会把终端渲染成可点击超链接（OSC 8）。现在新增 `display.escape_markup()`，所有平台 / 外部来源字符串在进入 Rich 前按字面转义；代码自身的样式标签、JSON 信封与 Agent 契约不变。
- Markdown 导出转义链接与原始 HTML 语法（Issue #951）：`escape_md_cell` 只转义表格分隔符 `|` 与换行，公司名、职位名、联系人名与聊天内容里的 `[点我领奖](https://...)`、`<https://...>`、`<img src=x onerror=...>` 会原样落进 `boss digest --format md` 与 `boss chat --export md` 的输出——这两份 Markdown 的定位是直接转发到邮件 / 飞书，在支持内联 HTML 的渲染器里就成了可点击链接或原始标签，而聊天消息由对方撰写。现在额外转义 `[`、`]`、`<`（反斜杠先转义，避免与已有 `\|` 叠加成双重转义），正常文本的渲染结果不变；代码自身生成的 `<details>` 等合法 HTML 不受影响。`digest.py` 删除分叉的本地实现，统一使用 `services/chat_utils.escape_md_cell`。
- 数据根目录与 AI 密钥文件收紧为仅当前用户可读（Issue #949）：`--data-dir`（默认 `~/.boss-agent`）用默认 `mkdir()` 创建、`AIConfigStore` 用 `Path.write_bytes` 写 `salt` 与 `api_key.enc`，权限跟随 umask（实测目录 0755、文件 0644）。`cache/boss_agent.db` 里的聊天记录、候选池、简历与投递记录，以及 AI 服务商密钥（加密密钥只由机器 ID 与同目录 salt 派生，不涉及用户口令）因此对本机其他用户可读，是 #947 在 `auth/` 之外的同类问题。现在数据根目录按 0700 创建、已存在的目录在启动时 best-effort 收紧（世界可写的共享目录如 `/tmp` 不动），`AIConfigStore` 的 `ai/`、`auth/` 与 `api_key.enc`、`salt` 分别按 0700 / 0600 创建与写入；`services/private_files.py` 统一承载私有原子写与权限收紧，`TokenStore` 改为复用同一实现。
- 本地登录态文件收紧为仅当前用户可读（Issue #947）：`auth/token_store.py` 用 `Path.write_bytes` 写 `session.enc` 与 `salt`、用默认 `mkdir()` 建 `auth/`，权限跟随 umask（实测目录 0755、文件 0644）。Fernet 密钥由机器 ID 与同目录 salt 派生，不涉及用户口令，同机其他本地用户能读文件即可解出 Cookie / stoken。现在 `auth/` 以 0700 创建，`salt` / `session.enc` 经同目录临时文件（`mkstemp`，0600）原子替换写入；历史安装的 0755 / 0644 在打开时 best-effort 收紧。Windows 上 chmod 语义有限，保持尽力而为，不影响读写。
- wizard 布尔输入只接受 JSON 布尔，字符串不再反向生效（Issue #945）：`candidate_mark` 的 `remove`、`candidate_export` 的 `include_private`、`recruiter_candidates` 的 `select` 与 crawl 的 `with_detail` 直接 `bool(inputs.get(...))`，而 wizard `inputs` 来自 `--input-json` / MCP，不经过 Click 类型检查。Agent 把布尔写成 `"false"` / `"0"` 时 `bool()` 结果为真：标签被删除而不是添加、导出保留 `security_id` 等明文、平台搜索带上 `select=true`、采集额外请求详情页。现在 `null` 与缺省使用默认值，其余非布尔值在**任何平台请求或文件写入之前**返回 `INVALID_PARAM` 并点名字段；真正的 `true` / `false` 行为不变。
- `stats` / `interviews` / `cities` / `platforms` 的后继提示保留数据目录（Issue #943）：`stats` 的 `_build_hints` 硬编码 `boss search` / `boss apply` / `boss pipeline` / `boss follow-up`，HTML 报表内的建议与 `stats --format html -o <path>` 的重导出示例也不带目录；`ai optimize` 的操作建议、`interviews` 的 `search`、`cities` 的 `search --city` 同样如此。`platforms` 的逐个平台 `--platform <名称> status` 与兜底 `status` / `schema` 也不带 `--data-dir`。使用自定义数据目录时，照抄提示会读写默认目录的另一份缓存、登录态与索引。现在这些提示统一保留按 shell 参数转义的绝对数据目录；`stats` 的 HTML 报表内提示同样携带目录并保持转义。
- 招聘者命令的后继与恢复提示保留数据目录（Issue #941）：`hr whoami` / `applications` / `resume` / `chat` / `chatmsg` / `last-messages` / `jobs` / `candidates` / `recommendations` / `reply` / `request-resume` / `accept-resume` / `download-resume` / `location` 的 `hints.next_actions`、TTY `next_steps`、失败与空结果的 `recovery_action`，以及 `hr last-messages` 空结果、`greet` 失败核对与空 `job_id` / `enc_job_id` 指引中嵌入的命令此前都是裸 `boss hr ...`。使用自定义 `--data-dir` 时照抄提示会落到默认目录，轻则 `AUTH_REQUIRED`，重则读取另一个账号的招聘者登录态与缓存。现在整个命令族统一携带按 shell 参数转义的绝对目录（`hr` 组自身设置 recruiter 角色，无需额外 `--role`）；`PLATFORM_NOT_SUPPORTED` 的恢复命令同时固定 `--platform zhipin` 并保留原目录。`test_cli_invariants` 的字面量扫描同步覆盖 `hint_command` 包装。
- AI 命令的后继与恢复提示保留数据目录（Issue #939）：`ai config` / `ai local status|configure|pull|import|smoke` 的 `hints.next_actions` 与 TTY `next_steps`，`ai analyze-jd` / `polish` / `optimize` / `suggest` / `fit` / `reply` / `interview-prep` / `suggest-keywords` / `resume-optimize` / `cover-letter` 的后继命令与 `operator_actions`，以及 AI 未配置、API 报错（401/403/400/404/422/截断）、缺 JD、`CACHE_MISS`、候选池为空、下载未确认时的 `recovery_action` 此前都是裸 `boss ...`。使用自定义 `--data-dir` 时照抄提示会落到默认目录：读的是另一套 AI 配置与简历，`ai config --api-key` 把新密钥写进默认目录，`shortlist add` / `detail` 读写另一个账号的缓存，`ai local` 的下载与导入登记也认错目录。现在整个 `ai` 命令族统一携带经过 shell 参数转义的绝对数据目录。
- 招聘自动化命令的后继与恢复提示保留数据目录与角色（Issue #937）：`agent run` / `train` / `stats` / `control` / `stop` / `review list|approve|reject` / `pending list` 的 `next_steps` 与 `_agent_hints` 只拼了 `boss --role recruiter agent ...`，`agent control` 的 `data.commands`、复核项缺失的 `recovery_action`、`automation` 配置损坏时的 `config reset automation`，以及 `agent crawl` 的参数示例、`ai config` 恢复与 AI 匹配失败重试命令也不带 `--data-dir`。使用自定义目录时，照抄提示会读取默认目录中另一套 `automation/` 状态或另一份简历，`ai config` 还会把密钥写错位置。现在招聘者命令统一携带按 shell 参数转义的绝对目录与 `--role recruiter`，候选人侧的 `agent crawl` 后继只保留目录、不强行加角色。
- 简历命令的后继与恢复提示保留数据目录（Issue #935）：`resume init` / `list` / `show` / `edit` / `delete` / `export` / `import` / `clone` / `diff` / `link` / `applications` 的 `hints.next_actions`、TTY `next_steps`、错误的 `recovery_action` 与损坏文件的 `operator_actions` 此前都是裸 `boss resume ...`（`resume link` 后继的 `apply`、取参用的 `search` 与 `init` 后的 `me` 同样如此）。本地简历全部存放在 `--data-dir` 的 `resumes/` 下，使用自定义目录时照抄提示会落到默认目录：`show` / `edit` / `export` 直接 `RESUME_NOT_FOUND`，`init` / `import` 另建一份简历并被 `RESUME_ALREADY_EXISTS` 挡住，`link` / `apply` 还会写进另一个账号的缓存。现在这些提示统一携带按 shell 参数转义的绝对目录与平台。
- 导出命令的后继与恢复提示保留数据目录（Issue #933）：`export` 成功后的 `search` / `recommend`、CSV/stdout 与 `--format html --json` 的重导出示例，以及空 query、空 `--url`、URL 解析失败时的 `recovery_action` 与 `next_actions` 此前都是裸 `boss ...`（空 query 的恢复命令还是硬编码字符串）。使用自定义 `--data-dir` 时照抄提示会落到默认目录，轻则 `AUTH_REQUIRED`，重则用另一个账号的登录态重新抓取。现在这些提示统一携带按 shell 参数转义的绝对目录与平台。
- 浏览历史的后继提示保留数据目录（Issue #931）：`history` 的 `detail` / `apply` / `greet` 与下一页命令、终端表尾的 `more:` 提示此前都是裸 `boss ...`，使用自定义 `--data-dir` 时照抄会落到默认目录，轻则 `AUTH_REQUIRED`，重则读取另一个账号的浏览记录与缓存。现在这些提示统一携带按 shell 参数转义的绝对目录与平台。
- 配置命令的后继与恢复提示保留数据目录，终端「下一步」按字面显示（Issue #929）：`config list` / `get` / `set` / `reset` 的 `hints.next_actions`、未知配置项与非法取值的 `INVALID_PARAM` 恢复提示此前都是裸 `boss config ...`，照抄会在默认目录读写另一份 `config.json`——原目录的坏值仍在，之后的命令继续在启动阶段失败。现在这些提示统一携带经过 shell 参数转义的绝对 `--data-dir` 与平台。补上目录后暴露了 `display.render_next_steps` 未做 Rich 转义：目录名含 `[bold]` 时标签被吞掉，含未配对的 `[/bold]` 时 `console.print` 抛 `MarkupError`，整段「下一步」提示都打不出来；现在命令文本按字面渲染。
- 投递、招呼与个人记录的后继提示保留数据目录（Issue #927）：`apply` / `greet` 的成功、空编号恢复与重复操作指引，`batch-greet` 完成后的会话提示及记账失败后的核对命令，以及 `me` 的搜索、推荐与投递记录翻页命令，均保留原 `--data-dir` 与平台。重复操作的恢复正文同步带目录，避免照抄后读取默认目录的其他登录态；回归直接重放提示，不再在测试调用方手动补回目录。
- 职位表的编号查看提示只在写入编号索引后出现（Issue #925）：终端职位表统一打印 `use: boss show <#> to view details`，但只有 `search` / `recommend` 会把本次结果写进编号索引。`history`、`watch run`、`batch-greet --dry-run` 与未指定 `-o` 的 `export` 照提示执行 `show 1` 时打开的是上一次搜索留下的职位（此前没有搜索过则报「没有缓存的搜索结果」），随后的 `apply` / `greet` 也会落到错误职位上。`render_job_table` 的编号提示改为可选，仅由已写入索引的调用方传入；`search`（含缓存命中）与 `recommend` 在索引写入失败时同样不再提示。
- 收藏与候选池的后继命令保留数据目录并转义标识（Issue #923）：`favorites` 的翻页、同步、读取候选池及详情提示，以及 `shortlist` 的增删、标注、比较与错误恢复提示，现在保留原 `--data-dir` 与平台，避免照抄提示后读取其他账号或默认目录的候选池。删除与缺失条目重建命令中的两个标识按 shell 参数转义，含空格、引号时仍能操作原条目。
- 职位推荐与详情的后继提示保留数据目录（Issue #921）：`recommend` 的翻页及后继操作、`show` / `detail` 的错误恢复与成功提示现在沿用原 `--data-dir` 与平台。`search`（含缓存命中）与 `recommend` 的终端表尾也使用带目录的 `show` 命令，避免编号误查默认目录的另一份索引。终端里的命令文本（成功提示与错误恢复）按字面显示，路径含 `[bold]` 等 Rich 标签时不再被渲染器改写或抛出 `MarkupError`。
- 搜索与预设的后继提示保留数据目录，缓存命中不再复用别次调用的提示（Issue #919）：`search` 的空关键词 / 空 `--preset` / 空 `--url` / `SAVED_SEARCH_*` 指引、成功信封的 `detail` / `apply` / `greet` 与下一页命令，以及 `preset add` / `preset list` / `preset remove` 的 `next_actions`、TTY `next_steps` 和损坏条目的 `operator_actions` 都构造为裸 `boss ...`。使用自定义 `--data-dir` 时，刚保存的预设照抄提示会落到默认目录并立刻 `SAVED_SEARCH_NOT_FOUND`，随后的「重建」提示还会在默认目录另建一份，`detail` / `greet` 也可能换到另一个账号。现在这些提示统一经 `boss_command_for_ctx(..., include_data_dir=True)` 构造，保留原数据目录（按 shell 参数转义）与平台；`preset_origin` 正文与 `SAVED_SEARCH_NOT_FOUND` 的 `recovery_action` 同步带目录。搜索缓存只保存结果与分页证据，命中时按本次调用参数重建提示——缓存键不含 `--sort`，旧实现会把上一次 `--sort score` 的翻页命令原样返回给未排序的调用。
- 沟通类命令的后继提示保留数据目录（Issue #917）：`chat` / `chatmsg` / `chat-summary` / `mark` / `exchange` 的 `hints.next_actions`（`detail`、`chatmsg`、下一页等）以及 `chat --export` 的 `config reset export_dir` 恢复命令此前都构造为裸 `boss ...`，`commands/_contact_lookup.py` 的 `CONTACT_NOT_FOUND` 分支更直接硬编码 `["boss chat"]`（同文件空标识分支已携带目录）。使用自定义 `--data-dir` 时，照抄提示会回到默认目录：轻则 `AUTH_REQUIRED`，重则读取另一个账号的沟通列表与缓存。现在这些提示统一经 `boss_command_for_ctx(..., include_data_dir=True)` 构造，保留原数据目录（按 shell 参数转义）与平台；翻页提示继续保留 `--count` / `--raw`。
- 沟通视图不再把 JSON 布尔当成平台数值编码（Issue #915）：`chat` 的 `lastTS` / `relationType` / `lastMessageInfo.status` / `unreadMsgCount`、`chatmsg` 的消息 `type`、`pipeline` 的沟通项都用整数值直接查表或计数，Python 的 `bool` 是 `int` 子类，`{1: "未读"}.get(True)` 会命中编码 1——平台返回布尔时沟通列表会补出「对方主动」「未读」，`unread` 变成 `true`，一条本无未读的会话在 `pipeline` 里被判成 `reply_needed`，消息类型 `true` 显示成「文本」。新增共享 `int_code()` 只接受真正的整数，布尔 / 字符串 / `null` 一律不携带编码语义：相应字段回退为「未知」、`0` 或 `其他(true)`，`reply_needed` 不再被布尔触发（与 Issue #909 的性别编码同因）。
- 流水线、监控与日报的后继命令保留数据目录（Issue #913）：`pipeline` / `follow-up` / `digest` 的 `hints.next_actions` 与 `watch` 的 `next_actions`、TTY `next_steps`、嵌入命令的 `operator_actions` 都硬编码 `boss watch ...` / `boss follow-up` 等不带 `--data-dir` 的命令。使用自定义数据目录时，照抄「运行刚创建的 watch」只会得到 `SAVED_SEARCH_NOT_FOUND`，修复坏记录的 `watch remove` 也删不到原目录，`follow-up` 的 `mark` 还会额外因默认目录没有登录态而失败。现在这些提示统一经 `boss_command_for_ctx(..., include_data_dir=True)` 构造，保留原数据目录与平台；`watch run` 找不到记录时的 `recovery_action` 也改为指向同一目录的 `watch list`。
- 聊天筛选导出不再把未选中的联系人标成已消失（Issue #911）：`chat --from boss --export md` 把筛选后的列表交给当日快照，与上次快照做 UID 差集；未被 `--from` / `--days` 选中的联系人、以及只采集了第 1 页的结果，都会在 Markdown 和 `data.diff.removed` 里被报成“已消失”，即使同一次平台响应仍在返回它们。现在快照记录本次平台页面返回的全部联系人，筛选只影响导出正文与变化摘要；只有第 1 页且平台明确 `hasMore=false` 才判断消失，否则 `removed` 为空、`complete=false`，摘要注明“本次采集不完整，未判断消失”。
- 招聘者结构化简历保留未知性别（Issue #909）：`hr resume` 原先把所有非数值 `1` 的性别字段都输出为“女”，接口省略字段或返回 `null` 时会补出来源中不存在的个人属性。现在只转换已识别的整数编码，缺失或未知值输出空字符串，布尔值与字符串不冒充数值编码。
- `export` 达到内部页数上限时显式报告截断（Issue #907）：`--count 10` 在平台每页只返回少量职位（或本地筛选剔除大部分结果）时会静默返回 6 条，信封只有 `count` / `format` / `jobs`，调用方无法区分「平台只有这些」与「本地扫描到上限」。现在 `data` 新增 `truncated` 与 `stop_reason`（`count_reached` / `platform_has_more_false` / `empty_page` / `page_budget_exhausted`），只有最后一种 `truncated=true` 并在 `hints.operator_actions` 提示缩小筛选条件；`--output` 文件、`--format html --json`、stdout 数组三种信封一致，HTML 直出的提示写 stderr。
- wizard 交换联系方式拒绝非法 `type`，不再静默改发手机号请求（Issue #905）：`candidate_exchange` 与 `recruiter_exchange_contact` 用 `2 if str(inputs.get("type") or "phone") == "wechat" else 1` 映射类型，`"wecaht"`、`"PHONE"`、`"phone "` 等拼错值都会按手机号真实发出交换请求；wizard `inputs` 来自 `--input-json` / MCP，不经过 Click `Choice`，catalog 也只检查必填项。现在只有精确的 `phone` / `wechat` 合法（未提供时默认 `phone`），其余值返回 `INVALID_PARAM`，与 CLI 及 schema enum 对齐。
- `export` 在平台省略 `hasMore` 时继续翻页而不是静默截断（Issue #903）：分页循环用 `.get("hasMore", False)` 把「平台没说」当成「没有下一页」，`--count 50` 只导出第一页（最多 15 条）就 `exit 0`，信封里也没有任何截断证据。现在只有平台明确返回布尔 `false` 才停止，缺失或类型不明确时继续，仍受 `--count`、`max_pages` 与空 `jobList` 三重约束。
- `resume link` 跨轮换 `securityId` 不再重复关联同一职位（Issue #901）：`resume_job_links` 主键与冲突更新都用 `(resume_name, security_id, job_id)`，而 `securityId` 每次搜索请求都会轮换。同一简历重新关联同一职位时新 `security_id` 对不上旧键，会多出一行 `prepared` 记录，旧行仍是 `applied`，`boss resume applications` 把同一职位显示两次并给出矛盾状态。现在关联前先按稳定的 `job_id` 找到既有行，把该行迁到本次的新标识，保留 `status` / `notes` / `linked_at`，再刷新标题与公司。
- crawl 导入候选池按稳定 `job_id` 去重（Issue #899）：`import_crawl_shortlist` 用 `(security_id, job_id)` 复合键判断职位是否已导入，而 `securityId` 每次搜索请求都会轮换——同一职位重新采集后再 `--all` 导入，永远对不上旧键，候选池里就多出一条重复记录，`shortlist list/compare`、`stats` 和 `ai fit` 都会把它算两次。导入现在只按稳定的 `job_id` 判重，命中计入 `existing_count`，已有标签 / 备注 / 首次加入时间保持不变。
- crawl 断点恢复不再从页签第 1 页重抓列表（Issue #897）：`DrissionCrawlerSession` 只记录「是否导航过」，不记录页签当前显示第几页；恢复任务从 `next_page>1` 开始时，新页签仍停在第 1 页，第一页响应被当成断点页记入 SQLite 与 `jobs.json` / `jobs.csv` / `jobs.xlsx` 的 `page` 列，已见页被重复请求消耗预算，游标还会跳过从未采集的页。现在会话维护 `_page_no`，等待监听响应前先把可见列表导航并滚动到请求页；同一页重试重新导航，滚动中途中断则把位置标记为未知，下次重新对齐。
- 沟通列表 Markdown 导出的联系人映射表转义外部名称（Issue #895）：正文主表格、已消失列表与 HTML 版本都用 `escape_md_cell` / `html.escape` 处理联系人、公司等平台外部输入，唯独「联系人标识映射表」直接拼接 `label` 与 uid——名称含 `|` 时 uid 落到错误列，含换行时可凭空插入一行伪造映射（`| S2 | 伪造公司 | 99999 |`），而这张表正是告诉 Agent 用哪个 uid 调用 `boss chatmsg` 的依据。现在映射表两列同样转义，一行只对应一个联系人。
- 招聘自动化本地 AI 回复草稿不再把畸形字段当话术发给候选人（Issue #893）：`parse_reply_draft` 直接 `str()` / `float()` 模型 JSON，`reply: null` 变成 `"None"`、`reply: 123` 变成 `"123"`，且置信度沿用规则分数、动作判为 `AUTO_EXECUTED`，畸形文本被真实发出；`confidence: "high"` 的 `float()` 抛出的 `ValueError` 不在调用方捕获列表里，一个确定性的格式问题会终止整轮自动化并被 CLI 报成可重试的 `NETWORK_ERROR`；`2.0` / `NaN` 置信度也被静默接受。现在 `action` / `reply` 只接受字符串，`confidence` 必须是 `[0, 1]` 内的有限数值（兼容 `"0.9"` 这类可无损转换的字符串），结构错误统一走既有「local ai parse failed」降级：退回模板话术、要求人工复核、不发送任何消息，且不中断同轮其余候选人。
- 招聘自动化面试时间解析不再产出畸形时刻或静默丢分钟（Issue #891）：`_extract_interview_time` 把「点」一律替换成 `:00`，于是「明天下午3点30」变成 `3:0030`、「3点半」截成 `3:00`——操作者会按 15:00 赴约而候选人说的是 15:30，`interview-leads.csv` 里的时间不可直接使用。现在显式解析 `H点MM` / `H点半` / `H点一刻` / `H点三刻` / `H:MM` 等形态，识别不出时返回空串（不生成线索），`周二 14:00` 这类既有格式保持不变。
- 招聘自动化 `--dry-run` 不再污染业务状态与待执行队列（Issue #889）：演练路径在 `DRY_RUN` 状态下仍调用 `update_prior()` 把 `questionnaire_sent_at` / `follow_up_sent_at` / `exchange_contact_at` 写进 `automation/state.json`，下一次真实运行据此误判对话阶段——问卷从未发出却直接发送跟进话术；同时 `_next_pending_status(DRY_RUN)` 返回 `"dry-run"`，操作者批准过的待执行动作在预览一次后永久退出重放队列。现在只有 `AUTO_EXECUTED` 才更新对话阶段与线索；`DRY_RUN` 的待执行项保持 `pending`。`interview-leads.csv` 同步修复：预览不再落盘线索，写入走 `csv` 模块与共享 `sanitize_csv_cell`（候选人昵称含逗号 / 引号 / 换行不再破坏行结构，`=` 开头不再成为 Excel 公式），并按 `(candidate_key, interview_time)` 去重。
- `boss search` 在平台未给 `hasMore` 时不再谎报 `has_more=false`（Issue #887）：`run_search_pipeline` 用 `.get("hasMore", False)` 同时承载「是否继续翻页」与「对外分页证据」，平台省略该字段（或返回非布尔值）时信封里的 `has_more` 变成 `false`，Agent 会读成「平台确认没有下一页」。同仓库 `recommend` / `chat` / `history` / `favorites` / `hr jobs` 五处都刻意区分「平台说 false」与「平台没说」。现在 `SearchPipelineResult` 新增 `has_more_reported` 承载平台原值（缺失/非布尔为 `None`），控制流仍用 `has_more`；`search` 与向导 `candidate_search` 的信封按 `has_more_reported` 输出，并补齐 `has_more_source` / `total_kind`；`null` 时在 `hints.operator_actions` 提示不能仅凭本页条数判断。搜索缓存 schema 同步升到 6，避免修复前写入的旧条目继续返回布尔 `has_more`。
- 沟通列表聚合到页数上限时显式报告截断（Issue #885）：`collect_friend_list_items` 在翻满 50 页而平台仍声称有下一页时静默返回已收集部分，`pipeline` / `follow-up` / `digest` 及其向导版本都把它当完整统计，计数只会偏小且无法察觉。新增 `collect_friend_list_page` 返回 `(items, error, pages_scanned, truncated)`；`pipeline` / `follow-up` 在信封 `pagination` 里给出 `pages_scanned` / `truncated`，`digest` 与向导在 `data` 里给出 `chat_pages_scanned` / `truncated`，`digest --format md` 也会在正文里注明只覆盖前 N 页。原 `collect_friend_list_items` 保留为兼容包装。
- 向导超时或中断后恢复不再新建第二份采集（Issue #882）：runner 收集恢复用的既有结果时只认 `completed` / `waiting_input` 步骤，`failed` / `running` 步骤里的 `inner_run_id` 绑定被忽略；重跑该步骤前又以不传 `result` 的方式把状态写回 `running`，顺带把绑定清成 NULL。于是 `crawl_start` 超时后 `wizard --resume` 会再 `create_and_run` 一份新采集，旧 run 的页游标与已抓详情全部弃用。现在 runner 保留失败/运行中步骤的结果并传给 action，`_fail_controlled` 落失败状态时不再清空已有结果，`_crawl_start` 认出 `inner_run_id` 后走 `crawl resume` 续跑原任务。
- 向导 pipeline / digest 聚合全部沟通列表页（Issue #881）：`_candidate_pipeline_data` 只请求 `friend_list(page=1)` 并忽略 `hasMore`，而顶层 `boss pipeline` / `boss digest` 早已用 `collect_friend_list_items` 翻完全部页。同一账号下向导会静默漏掉后续页里待跟进的沟通。现在复用同一聚合函数，任一页失败按平台错误传播。
- TTY 错误输出与 JSON 路径一样脱敏（Issue #880）：`handle_error_output` 的终端分支直接打印 message 与 recovery_action，异常文本里夹带的 `token=` / `cookie=` 会明文出现在终端，而 `--json` 路径经 `output.redact_sensitive_text` 遮盖。现在两个分支使用同一脱敏。
- `wizard --input-json` 拒绝重复 `requested_steps`（Issue #879）：步骤表主键是 `(run_id, step_name)`，重复项在建 plan 后写库抛未捕获的 `sqlite3.IntegrityError`，stdout 没有任何信封，Agent 只能看到 `CLI_ERROR`。现在在 `build_plan` 阶段以 `INVALID_PARAM` 拒绝，与「步骤不属于 goal」同一路径。
- `export` 的 CSV 清洗改用与 chat 导出同一实现（Issue #876）：`services/job_export.py` 自带一份弱化副本，不剥 Tab 与回车，同一段文本走两种导出会得到不同结果，未剥的 `\r` 还可能让解析器把一列拆成两行。现在直接复用 `services/chat_utils.sanitize_csv_cell`，保留模块内私有别名以兼容既有引用。
- `wizard --stop` / `--status` 对 stopped run 不再给 `--resume`（Issue #870）：stopped 是终态，runner 直接返回原 run 且 `preserve_stopped` 保证状态无法离开 stopped，旧 hints 会让 Agent 得到一个永远 `ok:true` 却什么都不做的空转循环。现在给「新建 workflow」，并在 run 绑定了内部 crawl 时优先给出保留原数据目录的 `crawl resume <inner_run_id>`；`failed` / `waiting_input` 仍给 `--resume`。
- 采集导出的 CSV / XLSX 对平台原文做公式注入防护（Issue #871）：`crawler/exporter.py` 直接把职位名、公司、地址和 JD 正文写入文件，JD 常见的 `=====岗位职责=====` 分隔线以 `=` 开头时，openpyxl 会写成 `<f>` 公式节点、CSV 被 Excel 当公式求值。现在 CSV 复用共享的 `sanitize_csv_cell`（前置单引号），XLSX 保留原值并显式声明文本类型，避免单元格里出现可见单引号。
- MCP `boss_shortlist_annotate` 传空备注时真正清空备注（Issue #872）：参数映射用真值判断，`note:""` 被丢弃、旧备注保留，信封却仍返回 `updated:true`。CLI 的「替换备注」语义是显式传值即可替换，映射层现在只区分「未传 / 传了值」，空串原样下发。
- MCP `boss_hr_jobs` 的写判定改为按 `action` 参数区分（Issue #869）：该工具在只读白名单里，但 `action=online|offline` 会 POST 平台上线/下线。子进程超时或被取消时，旧逻辑返回 `outcome_unknown=false` / `replay_safe=true` 并提示「可直接重新调用」，Agent 会在状态未知时重发一次状态变更。现在 `boss_hr_jobs` 只对 `list` 保持只读语义，`online` / `offline` 一律按写操作处理（须先核对平台实际结果）；新增回归同时校验写模式必须来自 schema 的 `action` 枚举且不含默认值。
- 扫码登录采到 User-Agent 后先复查安全页再导航首页（Issue #863）：headless 与 CDP 两条扫码路径在 `_safe_user_agent()` 之后直接调用首页预热，而 evaluate 会让页面继续处理排队中的响应，监听器可能刚好在此之前锁存了 `security.html` 风险——命中风控后仍自动发出一次首页导航请求，与「风控即停止、不继续自动化」的边界不符。现在两条路径都在采集后、预热前复查风险与安全页，命中即抛原风险。新增离线回归把页面 URL 造在 evaluate 之后才变成安全页，去掉复查即失败，断言登录页之后不再有第二次 `goto`。

## [3.3.0] - 2026-11-12

### Added
- `hr chatmsg` 返回游标分页证据：聊天历史用 `--max-msg-id` 向更早消息翻页，响应顶层已有 `hasMore`，但此前信封 `pagination` 为 `null`。现在 `pagination` 给出 `has_more`（仅平台布尔值非 `null`）、`has_more_source`、本页消息条数 `total`，以及本页最旧的可用 `mid` 作为 `next_max_msg_id`；仅 `has_more=true` 且存在游标时附加保留本次 `--count` 的下一页命令，未知时用 `operator_actions` 说明不能凭本页条数判断。游标分页没有页码，因此不返回 `page`。消息数组与 `mid` 解析容错，畸形载荷不会被当成可翻页数据。同步 CLI / MCP 能力说明与双语命令参考，补测三态、数字字符串 `mid`、畸形字段、下一页执行和错误路径。
- 新增 `boss hr location <friend_id>`：向已有候选人会话发送该职位的工作地点卡片。上游此前没有这条写路径。请求契约为单次 `POST /wapi/zpchat/message/sendJobLocation`，表单字段 `jobId`、`toUserId`、`securityId`；`securityId` 与内部 `jobId` 只取本次 `friend_detail` 的会话级值，不接受推荐卡片上的 `securityId`。默认不发送：无 `--yes` 时返回 `CONFIRMATION_REQUIRED`，`--dry-run` 只预览且不登录、不请求平台。`boss agent stop` 写入的 `automation/stop.json` 在任何请求之前拒绝（`CIRCUIT_BREAKER_OPEN`）。同一 `friend_id` 用本地缓存表 `recruiter_location_records` 去重：已发送为 `LOCATION_ALREADY_SENT`，结果未确认保留预约并返回 `LOCATION_RESULT_UNKNOWN`，明确失败（含限流与非零业务码）会撤销未发出的预约。成功信封给出 `sent`、`job_id`、`job_name`，不回显 `securityId`。MCP 工具 `boss_hr_location` 同步暴露，`yes` 默认不传。测试全部离线。
- `chatmsg` / `chat-summary` 返回所读消息页的分页证据：复用共享分页逻辑，`page` 优先取平台有效页码，`has_more` 仅在平台给出布尔 `hasMore` 时非 `null`，`total` 是本页消息条数（`total_kind=returned_page_count`）。仅确认还有下一页时附加保留本次 `--count` 的翻页命令，未知时用 `operator_actions` 说明不能凭本页数量判断。消息数组的读取统一走 `services/chat_utils.message_list`：`messages` 为空或类型不符时回退 `historyMsgList`，畸形载荷按空页处理而非崩溃。同步 CLI / MCP 能力说明、双语命令参考与 CHANGELOG，补测三态、畸形字段、平台页码、下一页执行和错误路径。
- `me --section deliver` 返回投递记录分页证据：复用共享分页逻辑，仅将平台布尔 `hasMore` 映射为 `pagination.has_more`，缺失或类型不明确时为 `null`；`total` 只计本页 `list` 数组，无法识别时为 `null`。仅确认还有下一页时附加翻页命令；默认汇总查询与其他 section 的顶层 `pagination` 仍为 `null`。同步 CLI / MCP 能力说明与双语命令参考，补测三态、畸形字段、平台页码、下一页执行和错误路径。
- MCP `boss_stats` 新增 `format` / `output_file`：此前只能拿到 JSON 统计，无法让 CLI 生成自包含 HTML 报表并写到指定路径。参数守卫继续覆盖其余单命令工具，把 MCP 侧与 Click 参数名不同的别名（`show_raw` ↔ `raw`、`fmt` ↔ `format`、`output` ↔ `output_file`、`search_url` ↔ `url`、`output_format` / `output_path`、`resume_name` ↔ `resume`、`label` ↔ `tag`）显式登记，`digest` 的测试钩子 `--now-ts-ms` 同样登记为不暴露。变异验证：删掉 `boss_stats` 的 `format` 后守卫立即失败。
- MCP `boss_clean` 新增 `days`（保留天数，0–36500，显式 0 原样下发），此前 Agent 只能按默认 30 天清理，无法收紧或放宽快照 / 导出文件的保留窗口。同时把本地维护与自动化工具纳入参数完整性守卫：`boss_agent_run` 的 `dry_run` / `limit` / `resume` 与 CLI 对齐；`boss_clean` 的 `--privacy`（清登录会话与简历）与 `--risk-lock`（清除本地风控锁）、`boss_agent_train` 的 `--live` 会绕过默认演练或删除敏感本地数据，显式登记为不暴露，未来改动必须经过这层判断。变异验证：删掉 `boss_clean` 的 `days` 后守卫立即失败。
- MCP 只读工具补齐 CLI 已有但 schema 未声明的参数：`boss_status` 新增 `live`（发一次只读在线验证），`boss_me` 新增 `deliver_page`，`boss_chat_summary` 新增 `page` / `count`，`boss_detail` 新增 `lid`，`boss_chat` 新增 `format` / `output_file`（映射到 `--export` / `--output`），`boss_pipeline` 新增 `days_stale`。此前 Agent 无法在线复核登录态、不能翻投递记录、看摘要时无法控制请求预算与上下文长度，也不能通过 MCP 导出沟通列表。`tests/test_mcp_args_contract.py` 新增 `test_read_tool_schema_lists_every_cli_parameter` 守卫 7 个只读工具的参数集合；`pipeline` / `follow-up` 的测试钩子 `--now-ts-ms` 显式登记为不暴露。变异验证：删掉 `boss_me` 的 `deliver_page` 后守卫立即失败。
- MCP 本地搜索工具补齐与 CLI 的参数差距：`boss_preset_add` 新增 `industry` / `scale` / `stage` / `job_type`，`boss_watch_add` 新增 `experience` / `education` / `industry` / `scale` / `stage` / `job_type` / `welfare`，`boss_shortlist_add` 新增 `title` / `company` / `city` / `salary` / `source`。CLI 一直支持这些字段，但工具 schema 未声明、`_build_args` 不映射，MCP 保存的预设和监控会静默丢掉筛选条件，候选池条目也只能留下空标题与空公司。现在按真值语义原样下发（`job_type` → `--job-type`），未传时保持 CLI 默认；`tests/test_mcp_args_contract.py` 新增 `test_local_tool_schema_lists_every_cli_parameter` 守卫 preset / watch / shortlist 五个工具——`shortlist annotate` 的可重复选项 `--add-tag` / `--remove-tag` 登记为 `add_tags` / `remove_tags` 别名。变异验证：从 `boss_watch_add` 的 schema 删掉 `scale` 后守卫立即失败。
- MCP `boss_batch_greet` 补齐搜索筛选参数：新增 `salary` / `experience` / `education` / `industry` / `scale` / `stage` / `job_type` 并映射到同名 CLI 选项（`job_type` → `--job-type`），`limit` 声明 0–10 上限。此前 MCP 只能按关键词和城市批量打招呼，筛选缺口会把范围放大到整页搜索结果。核心工具参数完整性守卫扩展到 `boss_batch_greet`（`count` ↔ `limit` 别名），新增筛选透传测试；变异验证：从映射循环删掉 `industry` 后测试立即失败。
- MCP `boss_search` / `boss_recommend` 补齐与 CLI 的筛选参数差距：`boss_search` 新增 `industry` / `scale` / `stage` / `job_type` / `no_cache` / `with_score`，`boss_recommend` 新增 `with_score`。CLI 早已支持这些筛选与开关，但工具 schema 未声明、`_build_args` 不映射，Agent 只能拿大结果集自行过滤，或无法跳过缓存 / 获取匹配分。现在按真值语义原样下发，未传时保持 CLI 默认；`tests/test_mcp_args_contract.py` 新增 `test_core_tool_schema_lists_every_cli_parameter` 守卫两个核心读取工具的 schema 与 Click 参数集合，`search_url` / `preset` 因与必填 `query` 互斥而显式登记为不暴露。变异验证：删掉 `industry` 声明后守卫立即失败。
- 招聘者四个列表命令（`hr applications` / `chat` / `candidates` / `recommendations`）与 `hr last-messages` 统一返回分页证据：共享 `services/pagination.py` 把平台布尔 `hasMore` 映射为信封 `pagination.has_more`（true / false），缺失或类型不明确时为 `null` 并标 `has_more_source=unknown`；`total` 是本页返回条数（`total_kind=returned_page_count`），`page` 取平台返回值、不可信时回显请求页。此前这几个命令能翻页却不带任何页码或 `hasMore`，Agent 只能凭本页是否满额猜测。只有 `has_more=true` 时 `hints.next_actions` 才附加保留筛选条件的下一页命令；`null` 时 `hints.operator_actions` 说明不能仅凭本页条数判断是否还有记录。`hr last-messages` 用 `--friend-id` 直查时没有列表页，`pagination` 为 `null`。新增 18 条测试覆盖三态、筛选条件透传、畸形字段与直查路径。
- `hr jobs list` 把平台分页事实映射到信封 `pagination`：`page` 优先取平台返回值（否则回显请求页），`has_more` 仅在平台给出布尔 `hasMore` 时为 `true` / `false`（否则 `null`，`has_more_source=unknown`），`total` / `total_kind` 是本页条数，`total_available` 对应平台 `totalSize`。此前这些字段只藏在 `data` 嵌套载荷里，按七键信封契约只看 `pagination` 的 Agent 拿到的是 `null`。`data` 原样保留；仅 `has_more=true` 时附加下一页命令。
- `chat --page N` 现在返回 `pagination`：`page` 是本次请求页，`has_more` 只在平台响应给出布尔 `hasMore` 时取 `true` / `false`（否则 `null`，`has_more_source=unknown`），`total` 是本页经 `--from` / `--days` 筛选后的条数（`total_kind=returned_page_count`），并记录 `has_more_source`。此前 `--page` 能翻页但信封不带任何页码或翻页证据，Agent 只能靠「本页是否满额」猜测，短页也可能是筛选后的结果而非最后一页。只有 `has_more=true` 时 `hints.next_actions` 才附加下一页命令并保留筛选参数；`has_more=null` 时 `hints.operator_actions` 说明不能仅凭本页条数判断是否还有记录。与 `recommend` 的分页语义一致，新增覆盖 `true` / `false` / 缺失三态的测试。
- 新增信封 `command` 词汇表守卫（Issue #809）：成功 / 错误路径（含 `@handle_auth_errors` 装饰器）逐个 AST 收集 command 字面量，要求每个取值都是 `boss schema` 的 39 个一级命令之一或显式登记的 53 个遗留值；新增第四种命名风格会在本地门禁失败。遗留清单同时做双向校验——不得与一级命令重名，也不得留下没有调用点再产出的死条目。词汇表本身仍留给 Issue #809 拍板，迁移落地时逐项删除。
- 索引快照增加页码：`search --page N` / `recommend --page N` 写入索引时记录 `page`，`boss show <#>` 的 `data.index_snapshot` 与 `search` / `recommend` 的 `pagination.index_snapshot` 都返回该字段，TTY 详情行显示「第 N 页」。此前的快照只能靠 `saved_at` 区分，Agent 从第 1 页记下的编号在翻到第 2 页后无法仅凭 `source` 判断已失效；旧版本写入的缓存没有该字段，返回 `null`。
- `search` / `recommend` 的成功信封在 `pagination.index_snapshot` 返回本次写入的索引快照身份（`source` / `count` / `saved_at`），与 `boss show <#>` 的 `data.index_snapshot` 使用同一结构：Agent 拿到「结果 #3」时可以当场记下它属于哪份快照，而不必先 `show` 一次才知道编号是否已被后续搜索覆盖。索引写入失败时不带该字段，避免把陈旧编号归到本次结果。缓存命中路径同样重新读取快照；快照身份不写入搜索缓存，避免缓存命中后返回过期时间。新增单元测试与 search 两条路径、recommend 的集成断言。
- `boss show <#>` 的输出新增 `data.index_snapshot`：编号只对最近一次 `search` / `recommend` 写入的索引快照有效，而缓存每次整份覆盖——重跑搜索后，Agent 手里的「结果 #3」会静默指向另一个职位，原输出只有 `index`，无法察觉引用已过期。现在成功信封返回 `source`（`search:<关键词>` 或 `recommend`）、`count` 与 `saved_at`（Unix 秒），TTY 详情面板下方同步显示编号来源与保存时间；命令参考与 agent-quickstart 中英说明「编号随快照失效、需重新定位」。新增 JSON 与 TTY 两条测试。
- 新增「结果未知必须声明重试安全性」守卫 `tests/test_output.py::test_unknown_outcome_details_always_declare_replay_safety`：扫描源码里 `outcome="unknown"` 的 details 字面量，缺少 `replay_safe` 时失败——只写 outcome 时 `envelope_error` 补不出 `outcome_unknown`，Agent 会误以为可以重试。变异验证：删掉 `_base_client._unconfirmed_http_response` 的 `replay_safe` 即触发失败。
- 新增向导渲染标签守卫 `tests/test_wizard_renderer.py`：`STEP_LABELS` / `ERROR_LABELS` / `RECOVERY_LABELS` 三张表此前没有任何测试覆盖，新增 wizard 动作或错误码却漏配标签时，TTY 会静默退化成「处理任务步骤」或「请返回主菜单后重试」，JSON 与 Agent 路径完全看不出问题。现在按 AST 收集 wizard 包内出现的全部已登记错误码（33 个），要求每个都有错误文案与恢复文案；步骤标签必须与 `DEFAULT_ACTIONS` 双向相等；错误与恢复文案必须成对且都在 `ERROR_CODES` 里。变异验证：删掉 `AI_API_ERROR` 标签或把 `candidate_apply` 改名，3 条守卫立即失败。
- 文档补齐 `boss schema --format mcp-tools`：能力真源支持四种导出格式，但命令参考（中英）与两份 README 只列了 `native` / `openai-tools` / `anthropic-tools`，照文档读会以为没有 MCP Tools 导出（实际输出 78 个工具，与 `mcp_tools.TOOLS` 逐名一致）。现在命令参考示例与 README 的 schema 说明都补上 `mcp-tools`；文档命令可解析守卫的扫描范围同时扩展到 `mcp-server/README.md` / `.en.md`，受检示例由 245 条增至 253 条（共收集 269 行）。
- 新增 eval 场景工具引用守卫 `tests/test_eval_harness.py::test_eval_scenarios_only_reference_shipped_tools_and_declared_arguments`：fixture 模式只重放脚本里的 `tool_calls` 并按同名断言，不经过工具解析——MCP 工具或参数改名后 fixture 仍 4/4 通过，只有人工跑外部 eval 才会暴露。现在逐个校验 `required_tools` / `required_arguments` / fixture `tool_calls` 的 13 处引用：工具名必须存在于 MCP 工具目录，参数名必须在对应工具的 `inputSchema.properties` 里；`forbidden_tools` 允许写不存在的「反面工具」（如 `browser_scrape`），不参与断言。变异验证：工具名改成 `boss_searchh`、参数名改成 `welfares` 均触发失败。
- 新增 `boss_command_for_ctx` 字面量守卫 `tests/test_cli_invariants.py::test_boss_command_for_ctx_literals_are_accepted_by_the_cli`：`hints` / `recovery_action` 里的命令大多由 `boss_command_for_ctx(ctx, "...")` 拼出，实参不含 `boss` 前缀、也不经过现有 `next_actions` 字面量扫描，选项写错时只有 Agent 真正执行 hint 才会失败。现在按 AST 收集全部 195 个可静态求值的调用点（f-string 片段与 `<占位符>` 掩码成合法取值），跳过 `.split(" ")` 取前缀再拼接的 `batch-greet` 调用，逐条追加 `--help` 交给 CLI 解析（不执行命令）。变异验证：把 `digest --format md` 改成 `--formats` 即触发失败。
- 新增文档命令可解析守卫 `tests/test_open_source_docs.py::test_documented_boss_commands_parse_with_click`：README 与 `docs/` 下 shell 代码块里以 `boss ` 开头的示例命令，会被提取并按 CLI 的 Group 树下钻到目标命令跑一次参数解析（不执行回调）；`<security_id>` 这类角度占位符掩码成合法取值后继续解析，JSON 片段、变量、PowerShell 续行与管道行整行跳过，另设 150 条检查下限防止提取失效——受检示例 245 条，占位符行里的选项拼写错误同样会被发现。审查当期即发现两处真实缺陷：中英文 README 的 `boss config reset` 缺少必需的 `KEY` 参数（照抄会收到 `Missing parameter: key`），已改为 `boss config reset log_level`。变异验证：还原旧示例、或把 `boss shortlist add <security_id> <job_id> --tags` 的 `--tags` 写成 `--tagz`，守卫均立即失败。
- 新增 MCP 参数映射契约守卫 `tests/test_mcp_args_contract.py`：`_build_args` 是 MCP 与 CLI 之间唯一的手写映射层，参数名与选项名不一致（`tag` → `--label`、`limit` → `--count`）、枚举拼错或漏写 `--` 前缀时，工具目录与 schema 都看不出问题，只会在真实 MCP 会话里变成 Click 用法错误。现在按 78 个工具的 `inputSchema` 自动生成样例参数，解析到目标命令后跑一次完整的 Click 参数解析（不执行命令回调），映射写错会在本地门禁直接失败。变异验证：把 `mark` 的 `--label` 改成 `--tag` 即触发失败。
- 新增 mypy 严格清单完整性守卫（Issue #785）：`CLAUDE.md` 要求新模块加入 `[[tool.mypy.overrides]]` 严格列表，但此前没有守卫，只能靠人肉发现；#783 收尾后仍有 8 个包的 `__init__.py` 漏登记。现在这 8 个包补入清单，`tests/test_quality_gates.py::test_mypy_strict_overrides_cover_every_source_module` 按 mypy 语义（`foo.bar` 不覆盖子模块，须写 `foo.bar.*`）双向断言：源码里每个模块都被严格条目覆盖，且清单里没有已不存在的模块。变异验证删条目 / 加假模块均能触发失败。
- 新增 `docs/README.md` 与英文版作为文档索引（Issue #769）：`docs/` 下 20+ 篇文档此前没有导航页，GitHub 浏览时只能看到平铺文件列表；索引按入门 / 核心参考 / Agent 接入 / 边界与安全 / 集成 / ADR / 维护者 / 研究分类，只链接 git 跟踪的文件，双语文档守卫与相对链接守卫自动覆盖。同时把相对链接守卫扩展到尚未 `git add` 的新文件（`--others --exclude-standard`），本地新建文档里的死链也能在提交前发现。
- 新增两条双语文档守卫（Issue #751）：`tests/test_open_source_docs.py::test_bilingual_docs_share_the_same_heading_structure` 要求 12 对中英对照文档的标题层级序列一致，一侧新增章节而另一侧没跟上就失败；`::test_troubleshooting_error_tables_list_the_same_registered_codes` 要求两个排障文档的错误码表列出同一组码，且每个码都在 `ERROR_CODES` 里登记。
- 修复 Markdown 相对链接死链并加守卫（Issue #761）：`CHANGELOG.md` 曾链到从未提交的 `docs/research/platforms/zhaopin.md`，读者点开是 404；该历史条目改为纯文本路径。新增 `test_markdown_relative_links_resolve`，扫描 git 跟踪的全部 `.md`，去掉代码围栏后检查每个相对链接目标存在，并设 200 个链接的检查下限防止正则失效后空转。
- README 中英「支持」节补上安全报告入口（Issue #759）：此前 `SECURITY.md` 只被 SUPPORT 与 issue 模板引用，README 正文没有链接；现在明确「安全漏洞请私密报告，不要公开提 Issue」，并把 README 的 `SECURITY.md` 可达性并入 `test_support_docs_route_every_request_channel` 守卫。
- 行为准则接入入口文档（Issue #757）：`CODE_OF_CONDUCT.md` 此前在仓库根目录但全仓库没有任何链接，GitHub 社区健康页之外不可达；README 中英「贡献者」段落、CONTRIBUTING 中英开头均已加入口，并新增 `test_code_of_conduct_is_reachable_from_entry_docs` 守卫。
- 新增 [SUPPORT.md](SUPPORT.md) 与英文版 [SUPPORT.en.md](SUPPORT.en.md)（Issue #755）：集中说明先看哪些文档、使用问题 / 可复现 Bug / 安全漏洞各自该走哪个渠道、提问要附的信息与脱敏要求、响应预期与 stale 策略；README 中英「支持」一节与 CONTRIBUTING 的「有问题？」段落加入口。双语文档标题结构守卫扩展到 SUPPORT（13 对），新增 `test_support_docs_route_every_request_channel` 守卫四类入口齐全且 README 可到达。

### Changed
- 配置默认值不再留「能配不生效」的键：`boss config list` 展示的 `automation.default` 改为从 `AutomationConfig()` 派生，与运行时解析器逐字段一致——此前是手写的五字段副本，`platforms` 删除后仍被展示成可用配置，而 `tabs` / `reply_strategy` 等七个真实字段反而看不到；`crawl.default` 删除 `hook_profile` / `hook_dir`，采集只能通过 `boss crawl run --hook-profile ... --hook-dir ...` 显式指定，config.json 里的同名键从未被读取。新增两条守卫：默认值交给正式解析器必须等于全新构造的默认配置，且 `crawl` 默认值只允许采集命令真正读取的六个键。
- 删除自动化配置里两个从未有消费点的字段：`automation.platforms` 与 `automation.stop_on_page_text`。用户配置它们不会产生任何效果——实际平台由全局 `--platform` 决定，页面风控由 `api/zhipin_errors.py` 的 URL / HTML 内容检测负责。旧 `config.json` 中的这两个键继续被忽略、不会报错；解析结果里不再保留空字段，`AutomationConfig` 字段集合有测试锁住，防止死配置被重新加回。
- CI 测试矩阵补回 Python 3.13（Issue #791）：`pyproject.toml` 声明支持 3.11–3.14，但矩阵自 #462 重写 CI 起就只有 `["3.11", "3.12", "3.14"]`——3.13 在原来 3.10/3.11/3.12/3.13 的矩阵里被无声丢掉，classifier 却一直保留。现在四个声明版本都在 CI 实测，并新增守卫 `test_ci_test_matrix_covers_declared_python_classifiers` 要求矩阵覆盖 classifiers 里每个 Python 3.x 版本，防止「声明了但不测」回归。CONTRIBUTING、branch-protection 与发布故事中的矩阵描述同步更新。
- mypy 严格检查白名单补入 15 个遗留模块（Issue #783）：`api.cdp_risk_lock`、`api.egress`、`api.proxy`、`auth.page_identity`、`commands.wizard`、`services.json_files` 与 `wizard/` 下 9 个模块此前没进 `[[tool.mypy.overrides]]`，只做默认检查，而它们正处登录降级链、工作流执行与出口策略这些关键路径。打开严格检查后暴露两处 `no-any-return`，分别用 `_FollowUpBuilder` 类型别名与显式 `cast` 收敛，未使用 `type: ignore`；纯类型层改动，无运行时行为变化。
- 本地提交门禁与 CI 统一到同一份 ruff（Issue #749）：pre-commit 的 ruff 钩子从 `ruff-pre-commit` v0.8.6 改为 `repo: local` + `uv run ruff check --fix`，直接用 `uv.lock` 里的版本；此前钩子自己装一份，与 CI 的 `uv run ruff check`（0.17.4）长期是两个 linter。`.github/dependabot.yml` 增加 `pre-commit` 生态，`pre-commit-hooks` 升到 v6.0.0；新增 `tests/test_quality_gates.py` 锁住「ruff 只在项目环境里跑」这一约束。
- 加固 GitHub 工作流（Issue #747）：全部 18 处 `uses:` 从 `@v7` 这类可移动标签改为完整提交 SHA 并在行尾注明精确版本；`release.yml` 的标签名改用 `$GITHUB_REF_NAME`，不再在 `run:` 脚本里展开 `${{ github.ref_name }}`；发布构建关闭 `setup-uv` 缓存，避免 `master` 上 CI 写入的缓存被带进持有 PyPI 令牌的环境；不需要推送的任务（CI、Pages、发布）的 `actions/checkout` 设 `persist-credentials: false`。各工作流的触发条件与行为不变。`tests/test_open_source_docs.py` 新增 6 条离线守卫（SHA 钉版与版本注释、`run:` 不展开表达式、`pull_request_target` 不检出 PR head、发布不吃缓存、checkout 凭据逐任务登记），`docs/maintainer/branch-protection.md` 新增「Workflow rules」一节。

### Fixed
- 发布校验拒绝源码包里未被 git 跟踪的文件（Issue #864）：构建后端只读取项目根的 `.gitignore`，不读 `.git/info/exclude`，本地被排除的演示素材、研究草稿与本地测试会被静默打进源码包——v3.3.0 准备发版时本地构建的源码包因此从 6.5 MB 涨到 78 MB，而 `--require-clean` 用 `git status` 判断，看不到这些被忽略的文件，门禁照常通过。现在 `scripts/verify_release.py` 逐项核对产物成员与 `git ls-files`，`PKG-INFO` 作为构建生成项例外；本地构建与 CI 发布链路同时覆盖，发版清单与脚本索引说明本地应从干净 worktree 构建。
- 登录恢复与登录后的后继操作保留原数据目录（Issue #861）：此前自定义 `--data-dir` 下的认证错误仍提示裸 `boss login`，照提示登录会写入默认目录，重试原命令仍然未登录。现在通用认证错误的恢复动作、Agent 后继命令和真人登录指引显式携带经过 shell 参数转义的绝对目录；登录失败分类、登录成功后的状态 / 搜索 / 推荐、状态检查后继操作、向导等待登录与续跑指引、认证失败与超时 checkpoint、任务不存在 / 记录损坏的恢复提示，以及 `status` / `doctor` 信封内 `auth_health` 的全部诊断恢复动作一并同步。静态 schema 保留通用示例，写操作的人工核对要求保持原有语义。离线回归实际执行登录、状态验证、原操作重试和向导恢复，确认切换工作目录后仍操作原会话，默认目录的登录态与同名任务保持完整。
- 采集任务的后继命令和 `checkpoint.resume_command` 保留原数据目录（Issue #859）：此前使用自定义 `--data-dir` 时，状态、结果、停止和恢复提示会退回默认目录，可能读取或停止同名的其他任务。现在任务指引显式携带经过 shell 参数转义的绝对目录，任务不存在或记录损坏时的核对提示同样保留目录；风险停止的人工解锁步骤与候选池后继操作、采集服务、向导和 `agent crawl` 一并同步。风险停止仍只提供人工恢复说明，解锁后显式恢复。离线回归实际执行结果、停止、状态、断点恢复和 `clean --risk-lock`，确认默认目录的同名任务与风险锁未被修改。
- `crawl results` 的后继导入命令补齐本次返回职位的 `--selector` 并保留当前数据目录（Issue #857）：此前只提示 `crawl shortlist <run_id>`，缺少必需的选择范围，照抄必然返回 `INVALID_PARAM`。现在仅导入本次结果中的职位，保持页码与详情状态筛选的范围；空结果改为查看同一任务状态。路径、run_id 与 selector 按 shell 参数转义，终端与信封复用同一条命令；回归测试实际执行提示并核对落库范围。双语命令参考同步说明本地 selector 与导出文件的区别。
- 恢复本地记录错误码守卫的独立测试声明（Issue #855）：此前插入相邻测试时误删函数头，原断言附在提示命令守卫末尾，无法独立收集与运行，相邻测试提前失败也会跳过这项检查。恢复 `test_local_record_lookups_do_not_report_job_not_found`，保持原断言不变。
- 简历文件损坏时的 `resume delete` 提示按 `shell_argument` 转义简历名：简历名是自由文本（`resume init --name '我的 简历'` 合法），此前只有这一条提示直接拼名字，带空格时按 shell 解析会拆成两个参数，Agent 照抄只会换回「No such command」。回归测试用空格、单引号与 `$HOME*` 三种名字触发 `RESUME_FILE_INVALID`，断言 `shlex.split` 还原同一个名字。双语命令参考补充简历名转义说明。
- `chatmsg --raw` 的翻页提示保留 `--raw`：此前下一页命令只带 `--page` / `--count`，翻页后 `type_code` / `timestamp` / `body` / `links` / `security_id` 等保真字段全部消失，Agent 会以为这些字段只在第一页存在。回归测试断言提示含 `--raw` 且可被 CLI 解析，未请求 `--raw` 时提示保持原样。
- `search` 的下一页提示保留本次调用条件：此前只重建关键词与页码，城市 / 薪资 / 经验 / 学历 / 行业 / 规模 / 融资阶段 / 职位类型、`--url` 网页筛选、`--with-score`、`--sort score`、`--no-cache` 全部丢失，Agent 照提示翻页会拿到另一组结果却以为在继续上一页。现在下一页命令与本次调用同条件、只换页码，`--preset` 展开为等价显式筛选，值按 POSIX shell 规则转义；终端 `more:` 提示与信封 `next_actions` 使用同一条命令。带 `--welfare` / `--active` 的搜索分页语义不同，仍不提供下一页提示。搜索缓存 schema 相应升级，旧缓存条目不再复用。回归测试解析提示并交给 CLI 校验，覆盖中文关键词、多筛选、URL、引号与无下一页路径。
- `recommend` 的下一页提示保留 `--with-score`：此前只写 `--page N+1`，带匹配分翻页后 `match_score` / `match_reasons` 静默消失。
- `batch-greet --dry-run` 的执行提示保留全局上下文：此前只重建职位筛选与数量上限，丢失自定义数据目录和显式的 `--browser-source` / `--cdp-url` / `--delay`，照提示执行会改用默认目录的登录态与去重缓存，并静默回退到默认浏览器通道和节流区间。现在提示始终携带当前数据目录，命令行显式给出的通道、CDP 地址与节流区间原样保留，路径与关键字继续按 shell 规则转义；配置文件中同名的值随数据目录重新读取，不重复下发。新增断言锁定显式全局选项与选项顺序。
- `clean --dry-run` 的后继命令保留预演范围：此前固定返回 `boss clean`，丢失自定义数据目录、`--all` / `--privacy` 与 `--days`，照提示执行会转向默认目录和默认清理范围。现在只去掉 `--dry-run`，保留数据目录与有效选项，并转义含空格、单引号等字符的路径；清理后的 `login` / `doctor` 提示同样保留目录。`--risk-lock` 的预演命令继续只交给真人，并携带完整范围。回归测试实际执行返回命令，验证预演目录按原范围清理、另一个默认目录保持完整；同步双语命令参考。
- 职位描述缓存 `job_desc_cache` 新增 1,000 条容量上限：此前只在再次读取某个过期职位时懒删除，长期不再访问的缓存会持续累积。现在 `put_job_desc()` 写入后按保存时间淘汰最旧条目，同一时间按写入顺序处理；更新已有职位会刷新其保存时间，不额外占用容量。已有超限缓存会在下一次有效写入时收敛到上限，TTL 与显式 `clean --all` 保持原有语义。新增离线回归测试覆盖超限淘汰、最近条目保留与更新后的淘汰顺序。
- `clean --all` 补齐两张从未被清理路径触及的表：`recruiter_greet_records`（招聘者侧首次招呼去重记录，与求职者侧 `greet_records` 对称，此前执行全量清理后同一候选人仍在同一职位上永久不可再次招呼）和 `job_desc_cache`（职位描述缓存，只在读取到过期条目时懒删除，冷门职位写入后没有出口、也没有容量上限）。信封 `results` 各自新增「招聘者打招呼记录」「职位描述缓存」两行；默认 `clean`（不带 `--all`）行为不变，旧库缺表按没有可清理项处理。同步 `--all` 的 help、`boss schema` 描述与双语命令参考。
- 文档补齐 `hr location` 的排障与 MCP 工具说明：双语排障表新增 `LOCATION_ALREADY_SENT` / `LOCATION_RESULT_UNKNOWN`，`CONFIRMATION_REQUIRED` 的预览示例补上 `hr location --dry-run`；`mcp-server/README` 中英工具表补上 `boss_hr_location`，使新写路径在错误码与 MCP 文档中同样可达。
- `clean --all` 现在会清理 `recruiter_location_records`：`hr location` 引入的工作地点发送记录此前不在全量清理范围，执行 `boss clean --all` 后仍残留会话与职位标识。同时不再把候选人姓名写进该表（输出使用本次 `friend_detail` 的姓名，去重只需要 `friend_id` / `job_id` / `status`），避免本地留下无用途的个人信息；旧库中的历史姓名随 `--all` 一并删除。新增清理项测试与存储字段断言。
- MCP `boss_hr_chat` / `boss_hr_last_messages` 补齐请求预算参数：CLI 的 `--summary-limit`（0–300，0=不补最近消息）与 `--limit`（1–300，每次 50 个会话串行请求）一直可用，但工具 schema 未声明、`_build_args` 也不映射，MCP 调用方无法关闭或收紧补充请求，只能接受默认最多各 2 次请求。现在两个工具都暴露对应参数并原样下发；未传时不下发，保持 CLI 默认。新增三条参数映射测试，以及 `tests/test_mcp_args_contract.py::test_hr_tool_schema_lists_every_cli_parameter` 守卫：12 个单命令 hr 工具的 `inputSchema` 属性必须与 Click 参数逐一相等，其余 4 个结构特殊（resume/exchange 拆分、jobs 合并与位置参数）显式登记，新增 `boss_hr_*` 工具未归类也会失败。变异验证：删掉 `summary_limit` 声明后守卫立即报出 CLI 有但 schema 未声明的参数。
- MCP `boss_history` 补齐 `page` 参数：CLI 的 `history --page N` 一直可用，但工具 schema 是空对象、`_build_args` 也把它归入无参数命令，MCP 调用方无法翻页，只能反复读取第 1 页。现在 schema 声明 `page` 并映射为 `--page`；显式传入的 `0` 原样下发交给 CLI 的 `IntRange(1)` 拒绝，不静默改写。
- `history` / `favorites list` 不再把缺失的 `hasMore` 伪造成 `false`：此前 `platform_data.get("hasMore", False)` 让「平台没说」和「平台说没有下一页」输出同一个值，Agent 会据此停止翻页。现在与 `recommend` / `chat` 同一口径——仅平台布尔值映射为 `true` / `false`，缺失时为 `null` 并记录 `has_more_source=unknown`；`total` 补齐 `total_kind=returned_page_count`，缺失 `hasMore` 时给出 `operator_actions`，且不提示下一页。
- MCP 超时 / 取消不再把所有工具都标成「结果未知」：`_run_boss_async` 过去无论工具是什么都写 `outcome_unknown=true`，Agent 按统一契约读到 `replay_safe=false`，连 `boss_search` / `boss_show` / `boss_status` 这类只读命令超时也被要求「禁止自动重试」，与 agent-quickstart 的「读取被中断时 `replay_safe=true`」直接矛盾。现在按显式只读白名单（36 个工具）判定：只读工具超时 / 取消返回 `outcome_unknown=false`、`replay_safe=true`、`recoverable=true`，恢复指引改为「确认子进程已退出后可直接重新调用」；未列出的工具（含未来新增）继续保守按写操作处理。新增四条测试锁定白名单只引用真实工具、不得包含已知写工具、读写超时字段与 `call_tool` 的传递。
- 错误信封统一「结果未知」的重试安全性字段：CLI 传输层写 `error.details.outcome="unknown"` + `replay_safe=false`，MCP 强制终止写 `outcome_unknown=true`，同一语义两种键名，Agent 按文档读 `outcome_unknown` 时在 CLI 信封上读不到。现在 `envelope_error` 为两种形状互相补齐（结果未知且不可重放时补 `outcome_unknown=true`；MCP 形状补 `replay_safe=false`），读取被中断的 `replay_safe=true` 保持不误标；agent-quickstart 中英补充统一判定方式。
- 向导 TTY 补齐 15 个错误码的中文错误 / 恢复文案：这些码要么没有 `ERROR_LABELS`，要么恢复建议是英文命令（`boss login`、`boss resume list`）或为空，`recovery_message` 会统一兜底成「请返回主菜单后重试」，真人照做仍然失败——`AUTH_EXPIRED` 应重新登录而不是重新选任务，`RESUME_NOT_FOUND` 应先用 `boss resume list` 而不是重选，`WORKFLOW_STOPPED` 应恢复已有任务，`ACTION_UNCONFIRMED` / `ALREADY_APPLIED` / `ALREADY_GREETED` / `LOCAL_STATE_WRITE_FAILED` 的「先核实、勿重试」约束也会丢失。现在 33 个 wizard 可产生的错误码都有可执行的恢复指引，并由新增守卫锁定。
- 启动期不再静默忽略 `config.json` 里形状错误的 `log_level` 与 `request_delay`：`Logger` 对未知级别用 `_LEVEL_ORDER.get(level, 3)` 回退到 error 阈值，用户以为打开了 debug 却什么都看不到；`request_delay` 写成字符串 / 数字会被静默换成默认 30–60 秒区间，节流配置看起来生效、实际按默认跑。现在两者在启动期按与 `cdp_url` / `browser_source` 相同的规则报 `INVALID_PARAM`（带字段名与收到的值），并保留 `boss config reset <key>` 这条修复路径——config 子命令仍放行。
- 自动化配置的 `tabs` 与话术字段不再静默变形：`tabs` 旧实现直接迭代给定值，用户把 `tabs` 写成字符串时会被逐字符拆成 `("新", "招", "呼")`——执行层匹配不到任何 tab，自动化空转却报成功；写成 `null` / 数字则抛未包装的 `TypeError`。`questionnaire_message` / `follow_up_message` 旧实现用 `str(...)` 强转，`null` 会变成 `"None"`、空串会发送空消息，而这两个值会原样发给候选人。现在三者都按形状校验：`tabs` 必须是字符串数组，话术必须是非空字符串，错误信息带字段名与收到的值。
- `boss digest` 的终端输出不再把三段列表塞进面板：`new_matches` / `follow_ups` / `interviews` 都是完整结果项，直接插值进面板会渲染成一大段 dict repr，日报一天几十条时既撑爆面板，也没法扫读。现在面板只保留计数与摘要，三节分别用与 `pipeline` 一致的表格展示（公司 / 职位 / 关系 / 未读 / 最近时间 / 最近消息；面试节为面试时间与状态），空小节不打印占位。JSON 信封输出不变。
- `boss watch run <名称>` 的终端输出不再把新增职位塞进面板：`new_items` 是完整搜索结果项的列表，一次几十条新职位时整段 dict repr 会撑爆面板，也没法扫读。现在面板只保留 `new_count` / `seen_count` / `total_count` 三个计数，新增职位改用与 `search` 一致的表格展示（标题 / 公司 / 薪资 / 城市）。JSON 信封输出不变。
- `boss config get/set/reset` 的终端输出在遇到结构化配置时改用格式化 JSON：`automation` / `crawl` 的 Python repr 把两段十一字段字典挤进一个面板，单引号与折行让当前值、「默认值」和「上一次的值」都难读。现在面板内显示占位，完整 JSON 打印在下方；相同内容只展开一次，不同时并列便于对比。JSON 信封输出不变。
- `boss config list` 的结构化配置不再被截断：`automation` 的字典 repr 有二十多行，塞进 25 宽的值列后挤满表格，两条话术还被截成省略号，用户恰恰看不到自己配了什么。现在表格内显示「（JSON，见下方）」，完整 JSON 以格式化形式打印在表格下方；JSON 信封输出与 `config get` 不受影响。
- 自动化配置的 `allowed_actions` 不再 fail-open：旧实现把未知动作名静默过滤后 `actions or DEFAULT_ALLOWED_ACTIONS` 回退到默认全集，用户拼错一个枚举值（或写空列表想禁用自动动作）反而放开全部默认写动作。现在未知值与非数组（如字符串）一律报 `ValueError` 并列出有效值；空列表保持为空集合——执行层 `status_for_decision` 会把每个动作判为 `SKIPPED`，与消费点「不在白名单即跳过」的既有语义一致。新增三条测试，变异回旧实现时全部失败。
- 错误码登记表给两个从未产生的码补上「当前版本不主动产生」标注：`RESUME_NOT_SHARED` 与 `RECRUITER_NOT_AUTHORIZED` 自招聘者模式引入（#379 之前的 f249908）起就登记在 `ERROR_CODES` 里，但源码中没有任何路径产生它们，`boss schema` 却把二者当作普通可恢复码暴露给 Agent。现在 message 与 `COMPLIANCE_BLOCKED` / `CRAWL_PERMISSION_REQUIRED` 等同状态码的措辞一致，并新增 `test_schema_error_codes_mark_never_emitted_codes` 守卫：登记表中未被任何源码字符串引用的码必须带该标注，再出现「登记了但没接线」会直接失败（`UNKNOWN` 这类单词码按完整字面量判断，避开错误码正则的漏判）。
- 文本脱敏不再吞掉全角标点与后续中文（Issue #789）：`redact_sensitive_text` 用 `[^\s,;]+` 匹配凭据值，全角逗号 / 句号 / 分号 / 括号不是终止符，`stoken=abc）` 会把右括号一起替换掉，`Bearer x，然后` 连后续中文说明也消失——`boss status` 的就绪详情里因此出现过缺右括号的文案。现在值在全角标点处结束，标点与后续文本保留；引号包裹的值仍照常脱敏。新增四类场景测试（旧实现下先红后绿）。
- 消除出口预检地区表的双真相源（Issue #787）：`api/egress.py` 的 `_REGION_EXPECTATIONS` 把国家映射到 `(语言地区, 时区前缀)`，但时区检查实际查的是 `_TZ_REGION_HINTS`，表里的时区前缀解包后从未使用（ruff RUF059）；两侧未来可能漂移，新增国家漏配时区时检查会静默跳过。现在表只保留语言地区，时区映射以 `_TZ_REGION_HINTS` 为唯一真源，并新增守卫断言两张表的覆盖国家集合一致。同类清理：删除 `wizard/prompts.py` 一处结果从未使用的 `_menu_label_widths` 调用。
- `boss_favorites_list` 的 `page=0` 不再被静默丢弃（Issue #781）：`_build_args` 用真值判断页码，类型校验放行的 `page=0` 被换成默认第 1 页，调用「成功」却返回了没请求的数据；其他工具对 `page` 都用 `is not None`，非法页码由 CLI 的 `IntRange(1)` 返回 `INVALID_PARAM`。现在 favorites 与其余工具一致，新增两条参数映射测试覆盖 `page=0` 与未传两种情况。
- MCP 工具的 `inputSchema` 声明 `additionalProperties: false`（Issue #779）：#776 让 `call_tool` 拒绝 `inputSchema.properties` 之外的顶层参数，但 78 个工具的 schema 仍默认允许额外字段，客户端按 schema 校验时不会拦下拼错的参数，Agent 只能靠一次失败的调用才知道契约。现在共享工具目录在构造时统一补上顶层声明，`tools/list` 与 `boss schema --format openai-tools / anthropic-tools / mcp-tools` 同步生效；嵌套对象（如 wizard 的 `inputs`）保持开放。`docs/agent-quickstart` 中英补充说明，并新增三条测试覆盖 MCP 工具目录、嵌套对象边界与三种导出格式。
- `boss platforms` 的 TTY 表格改走 stderr（Issue #777）：`_render_platforms` 用 `click.echo` 没有 `err=True`，交互式终端下表格进入 stdout，与「stdout 只输出七键 JSON 信封」及 `display.console = Console(stderr=True)` 的约定不一致；管道场景因 `is_json_mode` 判断侥幸正常。两条终端渲染测试同时收紧为断言 `stdout == ""`。
- MCP 服务端按 `inputSchema` 校验参数类型（Issue #775）：`_build_args` 用 truthiness / `is True` 判断布尔参数，客户端把 `dry_run` / `yes` 传成字符串时，标志位会被静默丢弃或反向生效——`boss_hr_greet` 同时传 `"true"` 会既不预览也不确认，`boss_batch_greet` 传 `"false"` 反而变成预览。现在 `call_tool` 在进入 CLI 前校验 boolean / integer / number / string / array / object，类型不符返回带参数名与期望类型的 `INVALID_PARAM`；未知工具名同样直接返回 `INVALID_PARAM`（提示 `tools/list`），不再落到 `boss <name>` 的 Click 用法错误。`docs/agent-quickstart` 中英同步说明，并新增三条测试覆盖类型拒绝、正常布尔翻译与未知工具。
- 修正 ROADMAP 中已取代的运行模式描述（Issue #773）：中英「治理与合规」把「敏感命令默认阻断」（ADR 0001）与「schema / MCP 模式过滤」（ADR 0002）写成现行能力，而两份 ADR 顶部已标注 Superseded by the 2026-08 open-capability design，`compliance.py` 也明确两种模式权限一致。现在两项保留为历史完成项并注明取代关系与当前边界（平台风控 + 有界长流程），新增 `test_roadmap_describes_current_mode_semantics` 禁止旧表述回归。
- 移除 README 残留的 MCP Toplist badge 并对齐政策（Issue #771）：两个 README 首屏的 `mcptoplist.com` badge 由外部提交于 2026-07-28 加入，与 2026-05-03 制定的 `docs/maintainer/readme-badge-policy.md`「第三方目录站导流 badge 默认不允许」冲突，且当时未走准入评审。现在删除 badge，政策文档新增「当前首页 badge 清单」记录五个真源 badge 与该移除项，并新增 `test_readme_badges_come_from_approved_sources` 守卫 README 中英的外链图片只能来自 `img.shields.io` / `github.com` / `codecov.io`，同时要求政策文档维持该清单。
- 补全 `scripts/README.md` 的脚本索引（Issue #767）：`quality_baseline.py`、`capture_request_identity.py`、`package_portable.py`、`update_contributors.py` 此前没有条目，只能靠读源码发现；新增 `test_scripts_readme_indexes_every_script` 守卫，`scripts/` 下的非下划线脚本必须出现在索引里。
- 对齐 CONTRIBUTING 的本地验证矩阵（Issue #765）：中英版本此前只列手搓的 pytest / ruff / mypy，没有仓库实际推荐的一键门禁 `scripts/quality_baseline.py`，也没提 `scripts/smoke_p0.py` 与 `evals/run_eval.py --mode fixture`；现在先给一键门禁，单步命令降为备选，并补上 MCP eval 必须独立终端运行的约束。新增守卫检查两份文档都提到这三个入口。
- 对齐 `SECURITY.md` 的运行边界描述（Issue #763）：原文称「默认启用低风险辅助模式……不批量触达、不抓取平台数据」，与 `compliance.py` 的「两种模式权限一致」及实际 `batch-greet` / `crawl` 能力不符；现改为说明模式仅为兼容保留、真正边界是平台风控硬边界与有界长流程，保留原有 Cookie/Token、本地数据与导出防护条目。新增守卫禁止该过时表述回归。
- 修复中英排障文档的结构漂移（Issue #751）：中文版此前只有 8 个 H2，缺英文版已有的「登录问题」「浏览器 / patchright 问题」「搜索 / 接口错误」「术语表」分节，以及 `JOB_NOT_FOUND`、`ALREADY_APPLIED`、`AI_API_ERROR`、`AI_PARSE_ERROR` 四个错误码；英文版缺 `PLATFORM_NOT_SUPPORTED` 与 Windows CDP 启动脚本。两版现按同一标题结构组织，`JOB_NOT_FOUND` / `ALREADY_APPLIED` 的恢复建议对齐 `ERROR_CODES` 登记表。

## [3.2.2] - 2026-11-09

### Added
- `ACTION_UNCONFIRMED` 的历史回读诊断再补漏斗计数 `history_new_count` / `history_self_count` / `history_recipient_count`：能直接看出失败卡在「没有新消息（mid/时间判定）」「新消息不是自己发的（from 缺失）」「收件人不匹配」还是「文案不匹配」，并新增 `new_messages()` 供匹配与诊断共用（Issue #735）。排障表与命令参考（中英）同步。
- 命令参考 `docs/commands.md` / `docs/commands.en.md` 新增章节目录（11 个锚点），300+ 行的速查表可以直接跳转；新增 `tests/test_open_source_docs.py::test_commands_doc_toc_matches_headings` 守卫，目录锚点与真实标题不一致时失败。
- 新增 ADR 索引 `docs/adr/README.md` 与三份架构决策记录：0003（浏览器来源冻结策略表）、0004（七键信封与错误码登记契约）、0005（MCP server 通过子进程适配 CLI）；0001/0002 标注为历史上下文。架构文档与中英 CONTRIBUTING 加入 ADR 入口，新增 `tests/test_open_source_docs.py::test_adr_index_covers_every_record` 守卫（索引覆盖每份记录、必需章节、入口可达）。
- 新增公开架构文档 `docs/architecture.md` / `docs/architecture.en.md`：四条入口、七键信封、`boss schema` 真源、模块分层与调用链、登录链路、平台抽象、预算与风控边界、本地数据目录；README 顶部导航与 CONTRIBUTING 加入架构入口，新增 `tests/test_open_source_docs.py::test_architecture_docs_exist_and_are_linked` 守卫（中英存在、链接可达、相对链接有效）。
- `boss hr reply` 的 `ws_evidence` 新增 `ws_send_without_text`：统计有发送帧但提取不到文本（二进制 / 压缩 / 新编码）的条数，把「没有发送帧」与「有帧但读不出文本」区分开，供 Issue #735 定位。排障表与守卫同步。

### Changed
- ROADMAP 结构整理：全部完成的「近期（当前主线）」改为「近期（已完成）」，「中期（v2.0）」改为「中期（v2.x–v3.x）」，Web UI 明确标注「未排期」（中英同步）。
- 维护者文档归位：`docs/smoke-testing.md` 与 `docs/readme-badge-policy.md` 实际是单语的维护者文档（未被 README 链接，也没有 `.en.md` 对照），已移到 `docs/maintainer/` 并在中英 CONTRIBUTING 的「维护者文档」清单登记；两处测试引用同步，链接守卫扩展为覆盖全部五份维护者文档。

### Fixed
- 营销文档 `docs/marketing/awesome-submissions.md` 顶部声明只覆盖「计数与低风险定位」，但正文的投稿模板与 Checklist 仍写着 `38/9/50`、`latest release v2.0.0`；未来投稿者可能直接照抄。现在声明明确覆盖发布版本与投稿文案，Checklist 标题标注为 2026-08-27 快照并要求提交前按 README / PyPI / `boss schema` 复核；历史投稿记录保持原样。
- ROADMAP 的「已发布」列表停在 v2.0.0，补上 2.0.1 / 3.0.0 / 3.0.1 / 3.1.0 / 3.2.0 / 3.2.1 的摘要（中英同步）。

## [3.2.1] - 2026-11-06

### Added
- `boss hr reply` 返回 `ACTION_UNCONFIRMED` 时，`error.details` 现在同时带上 `history_checked` / `history_matched_count` / `history_attempts` / `history_error`：此前只有 `ws_evidence`，无法区分「历史回读没跑」与「回读了但没命中」，社区反馈的 `matched_ws_count: 0` 只能靠猜。现在失败信封能直接看出历史回读执行了几次、是否命中；排障表与命令参考（中英）同步说明这四个字段。`tests/test_recruiter_client.py` 的未命中用例补上对应断言，`tests/test_agent_docs.py` 守卫排障文档包含这些字段。
- 发版工作流在 `uv build` 之后、发布到 PyPI 之前新增 `scripts/verify_release.py --artifacts dist --tag "$GITHUB_REF_NAME" --require-clean --json`：`uv build` 本身不校验 tag 与 `pyproject.toml` 版本是否一致，也不检查产物里是否混入本地文件，tag 打错时 `uv publish` 会照常把错误版本推上 PyPI。校验脚本早已实现这些检查（含 `--tag` / `--require-clean`），此前只有维护者本地手动运行。新增 `tests/test_open_source_docs.py::test_release_workflow_verifies_artifacts_before_publishing` 守卫（校验步骤必须在 `uv publish` 之前），发版清单同步说明。
- 把已有的离线 MCP 评测提升为可发现的公开能力面（Issue #717）：`CONTRIBUTING.md` / `CONTRIBUTING.en.md` 新增「离线 MCP 评测」一节，说明四个场景、`uv run python evals/run_eval.py --mode fixture` 的预期输出（`4/4 passed`）与「必须在该独立终端运行、不要嵌套在 Agent 会话里」；`README.md` / `README.en.md` 的贡献入口加上指向 `evals/README.md` 的链接；`evals/README.md` 新增「场景 → 通过信号 → 失败信号」判据表。新增 `tests/test_agent_docs.py::test_contributing_documents_offline_mcp_evals` / `test_readme_links_offline_evals` / `test_evals_readme_documents_scenario_criteria` 守卫，文档再次漂移会直接失败。

### Fixed
- `boss hr reply` 的发送前就绪判定改为有上限轮询：Issue #724 指出页面在自己的 input 回调里异步清 `editor.disabled`，固定等待 150ms 在慢页面 / 慢机器上会误判「编辑器不可用」并放弃发送（消息其实可以发）。现在最多等 3 秒（每 50ms 检查一次）直到编辑器就绪，仍不强制清除 `disabled`；并在调用 `sendText` 前复查会话加载状态与 `conversation$.friendId`，会话被切走时放弃而不是发到错误的候选人。新增 `tests/test_recruiter_send_js.py`（Node 可用时运行）覆盖慢编辑器、持续 disabled、切走会话、加载中与多行 `<br>` 渲染五种情形。
- `boss schema` 里 `crawl run` 与 `crawl start` 的 `--hook-profile` 默认值不一致：run 写 `"none"`，start 写 `null`（且 null 不在该选项的 enum 取值域内），而两个命令省略时都按 `hook_profile or "none"` 执行——Agent 会以为 start 的「未指定」另有含义。现在两者统一为 `"none"`，新增 `tests/test_schema_contract.py::test_crawl_run_and_start_share_hook_profile_default` 守卫。
- README 中英双语的「命令」分组自称列出全部 39 个顶层命令，实际漏了 `mark`（联系人标签）、`interviews`（面试邀请）、`chat-summary`（聊天摘要）、`follow-up`（跟进提醒），中文版还漏了 `recommend`；`chat*` 这种通配写法也让人无法确认覆盖范围。现在逐个列出并补齐，新增 `tests/test_agent_docs.py::test_readme_command_groups_cover_every_top_level_command`（双语 README 必须出现每个顶层命令名），新增命令后漏写会直接失败。
- 中英 README 的「Schema 驱动 + JSON 信封」条目把信封写成五键 `{ok, data, pagination, error, hints}`，漏掉 `schema_version` 与 `command`——README 是最常被 Agent / 用户引用的契约描述，照此构造解析器会与真实 stdout 对不上；`docs/commands.md` 与实现一直是七键。现在改为完整七键，并新增 `tests/test_agent_docs.py::test_readme_documents_full_envelope_keys`（同时禁止五键子集再次出现）。
- 全局选项写错位置时给出的「下一步」不可执行：`boss schema --delay 1-2` 的 `hints.next_actions` 是 `boss --delay <value> schema --help`——既含 `<value>` 占位符，又顺手追加 `--help` 改变了命令意图，Agent 原样执行只会换回 `INVALID_PARAM`；`recovery_action` 也只有带 `...` 的示例。现在不带取值的全局选项（如 `--json`）直接给出完整可执行修正命令 `boss --json schema`（`recovery_action` 与 `next_actions` 同值，并有测试真跑一次），带取值的选项退回 `boss --help`、修正示例只留在 message 里。新增 `tests/test_cli_invariants.py::test_flag_global_option_hint_is_a_runnable_command`，并补 `test_valued_global_option_hint_includes_placeholder` 的 `next_actions` 断言。
- 公开文档里两处指向已不存在内容的失效引用：`ROADMAP.md` / `ROADMAP.en.md` 的 Agent Skill 条目链接到 `github.com/can4hou6joeng4/boss-skill`（该仓库已不可访问，返回 404），`.github/CODEOWNERS` 仍为已迁出的 `/SKILL.md` 指定负责人。现在 ROADMAP 保留历史表述但去掉失效超链接，CODEOWNERS 删除失效规则；新增 `tests/test_open_source_docs.py::test_codeowners_paths_exist` 守卫（CODEOWNERS 中每个具体路径必须存在）。CHANGELOG 里的历史链接属于发布记录，保持原样。
- GitHub Pages 首页与站点 `llms.txt` 的事实停留在 3.0.0：`demo/index.html` 页脚版本链接写 `v3.0.0`、MCP 工具指标写 **77**（实际 78），`demo/llms.txt` 也写 `Current version: 3.0.0`——这是项目对外门面，用户和 Agent 会直接读它。现在同步到 3.2.0 / 78，并新增 `tests/test_agent_docs.py::test_site_copy_matches_current_version_and_counts`：版本、顶层命令数、MCP 工具数与 `demo/llms.txt` 的三项计数都对照代码校验；发版清单补上同步要求（历史发布素材如 `demo/keynote-animation` 不随发版修改）。
- `SECURITY.md` 的「Supported Versions」表停在 `1.17.x`，而项目已到 3.2.0——公开的安全策略告诉用户当前版本不再接收安全修复，报告漏洞前还可能先降级到旧版本。现在更新为 `3.2.x`（`< 3.2` 不再支持），新增 `tests/test_open_source_docs.py::test_security_policy_supported_versions_match_current_minor` 对照 `__version__` 的 minor 校验，发版清单补上同步要求。
- Agent 入口索引 `llms.txt` 的版本号停在 3.0.0，而当前已是 3.2.0——它没有像 README / capability-matrix 那样的计数守卫，跨两个 minor 版本都没人发现；文件里硬编码的顶层命令 / 一级招聘者子命令 / MCP 工具计数同样只能靠手工同步。现在 `llms.txt` 更新到当前版本，新增 `tests/test_agent_docs.py::test_llms_txt_reports_current_version_and_counts`（版本与三项计数对照代码动态校验）与 `test_llms_txt_doc_links_resolve`（所有 `docs/*.md` 链接必须存在），发版清单补上同步要求。
- `boss crawl resume <不存在的 run_id>` 在未登录时返回 `AUTH_REQUIRED`「未登录，请先执行 boss login」：`_run_service` 先注入登录 Cookie，写错 ID 的人被送去登录，登录回来才发现任务根本不存在。run 记录只存在本地缓存，现在 `crawl resume` 在要求登录之前先查一次记录，缺失时直接返回 `CRAWL_RUN_NOT_FOUND` 与 `boss crawl start <query>` / `boss crawl status <run_id>` 的下一步；存在性检查不会吞掉「恢复需要登录」这条前置条件（已有 run 仍未登录时继续返回 `AUTH_REQUIRED`）。新增 `tests/test_crawl_cli.py::test_resume_missing_run_reports_not_found_before_requiring_login` / `test_resume_existing_run_without_login_still_requires_auth` 覆盖，既有 service 映射测试补上 run 记录。
- `--data-dir` 指向一个已存在的文件（或文件下面的子路径）时，`mkdir` 的 `FileExistsError` / `NotADirectoryError` 被组层网兜成 `LOCAL_IO_ERROR`「status 本地文件读写失败: [Errno 17] File exists」——把 `--data-dir` 的参数错误说成命令的本地 IO 失败，正文里夹着英文 `[Errno …]`，`hints` 为 `null`，Agent 没有下一步。现在 `_resolve_data_dir` 创建目录时把这两种错误转成 `INVALID_PARAM`「必须是目录路径，'/etc/hosts' 已存在且不是目录」/「… 的上级路径不是目录」，恢复指引与 `next_actions` 指向 `boss --help`；权限等真正的环境问题仍按 `LOCAL_IO_ERROR` 返回。新增 `tests/test_agent_error_contract.py::test_data_dir_pointing_at_a_file_is_param_error` / `test_data_dir_under_a_file_is_param_error` 覆盖。
- `boss config set cdp_url ":::"` 返回 `ok: true` 并落盘，之后每条命令（`status`、`doctor`、`search`……）都在启动期按 `--cdp-url` 规则失败——写入阶段不校验，等于让用户把自己的 CLI 配置锁死；`ftp://…`、`localhost:9222`、端口越界等写法同样如此。现在 `config set cdp_url` 用与启动期相同的规则校验（`config.is_valid_cdp_url` / `CDP_URL_REQUIREMENT`，`main.py` 也改为引用同一份实现），非法值返回 `INVALID_PARAM`、不写盘，并保留 `config get cdp_url` / `config list` 的下一步；`null` 与合法 http(s) 地址照常写入。新增 `tests/test_config_cmd.py::test_config_set_rejects_invalid_cdp_url` / `test_config_set_accepts_valid_cdp_url` 覆盖。
- `boss schema` 的错误码登记表里有三条从未被任何代码路径产生的条目：`APPLICATION_NOT_FOUND`、`JOB_POST_LIMIT` 是已移除平台的遗留，`recovery_action` 为 `null`；`AUTO_EXECUTED` 其实是招聘自动化事件的成功状态（`EventStatus.AUTO_EXECUTED`），却以 `recoverable: false`、`recovery_action: null` 混在错误码里——Agent 枚举「可能收到的错误」会看到三条永远收不到、也没有恢复动作的条目。现在移除这三条；新增 `tests/test_schema_contract.py::test_schema_error_codes_have_recovery_action_text`（每个错误码必须有非空恢复文案）与 `test_schema_error_codes_exclude_success_event_statuses`（成功状态不得登记成错误码）守卫，真正的失败状态（如 `CIRCUIT_BREAKER_OPEN`）保持不变。
- `~未知用户` 写法的路径在三处仍然让程序崩掉或报错归因错误：`config.json` 里 `request_budget_dir: "~不存在的用户/x"` 时启动期抛 `RuntimeError`，stdout 没有任何信封；`boss config set request_budget_dir "~不存在的用户/x"` 同样只有 traceback；`boss crawl run <关键词> --city <城市> --hook-profile screenshot-full --hook-dir "~不存在的用户/hooks"` 被兜成**可重试的 `NETWORK_ERROR`「重试」**，真正原因是参数值无法展开。现在新增 `config.expand_budget_directory`，把预算目录的展开失败归一成中文 `INVALID_PARAM`（启动期与 `config set` 都不再 traceback），`crawl` 的 `--hook-dir` 在同一处给出「无法展开 --hook-dir 的家目录写法」并保留 `crawl configure --help` 的恢复指引。新增 `tests/test_config_cmd.py::test_unknown_home_in_request_budget_dir_returns_envelope` / `test_config_set_unknown_home_budget_dir_is_rejected` / `test_budget_directory_unknown_home_is_value_error` 与 `tests/test_crawl_cli.py::test_run_rejects_unknown_home_hook_dir_before_touching_the_browser` 覆盖。
- `--data-dir` 在启动期不校验，两种写法会静默改变数据落点：`boss --data-dir "" status`（脚本里变量为空时很常见）把 `Path("")` 解析成当前目录，登录态、缓存和配置直接写进 cwd，下一个命令还会因为找不到 `~/.boss-agent` 的登录态而回「未登录」；`--data-dir "   "` 会在 cwd 下建一个名字是空白的目录。现在空值与纯空白按 `INVALID_PARAM` 拒绝，并说明省略该选项时使用 `~/.boss-agent`；首尾带空白的路径同样拒绝。`boss --data-dir "~不存在的用户/data" status` 原来更糟——`Path.expanduser` 抛 `RuntimeError`，stdout 没有任何信封、只有 traceback，MCP 侧只能看到「输出不可解析」；现在返回 `INVALID_PARAM`「无法展开家目录写法」，提示改用绝对路径，恢复指引指向 `boss --help`。新增 `tests/test_agent_error_contract.py::test_blank_data_dir_is_param_error_not_current_directory` / `test_data_dir_with_surrounding_whitespace_is_rejected` / `test_unknown_tilde_user_is_param_error_not_traceback` 覆盖。
- 非法的 `--cdp-url` 在启动期不被拒绝：`boss --cdp-url ::: status`、`boss --cdp-url localhost:9222 status`、`boss --cdp-url http://127.0.0.1:99999 status` 都能通过全局选项解析，直到浏览器通道真的连上去才失败，而 `boss doctor` 会把配置文件里的同一个坏值原样放进 `hints.next_actions`，Agent 照抄只会反复得到同一份失败报告。现在入口在解析配置之后按「http(s) + 主机名 + 合法端口」校验 CDP 地址，不合法时返回 `INVALID_PARAM`「必须是形如 http://127.0.0.1:9222 的 http(s) 调试地址」并给出 `boss --help` 的恢复指引；显式传入的 `--cdp-url` 任何时候都校验，`config.json` 里的坏值则放行 `boss config` 子命令，`boss config reset cdp_url` 仍可修复。新增 `tests/test_agent_error_contract.py::test_invalid_cdp_url_is_rejected_at_startup` / `test_invalid_config_cdp_url_does_not_block_config_repair` 覆盖。
- 回调自定义的中文参数校验没有可执行的恢复指引：`boss --platform bogus status`、`boss --delay -3 status`、`boss --role admin status`、`boss platforms --capability bogus` 的正文已经正确，但 `recovery_action` 停在「修正参数」、`hints` 为 `null`；`boss mark <uid> --label 不存在的标签` 更严重——命令体里抛出的 `BadParameter` 被 `handle_auth_errors` 的兜底包成**可重试的 `NETWORK_ERROR`「重试」**，Agent 会拿同一个非法标签无限重试。现在主入口对四类格式之外的 `BadParameter` 也按「`boss <命令> 的 <参数> 取值无效：…`」出正文，并给出 `用 boss <命令> --help 查看参数说明` 与 `next_actions`；`handle_auth_errors` 遇到 `click.ClickException` 直接放行给组层，`mark` 的非法标签回到 `INVALID_PARAM`。新增 `tests/test_agent_error_contract.py::test_custom_parameter_errors_point_at_help` / `test_invalid_delay_reports_chinese_message` 与 `tests/test_platform_cli.py` 断言覆盖。
- 用户可控的 `run_id`、`security_id`、`job_id` 被原样拼进可执行提示，含 shell 元字符时提示不可直接执行甚至可注入：`boss crawl shortlist 'run; echo pwn'` 的 `hints.next_actions` 给出 `boss crawl results run; echo pwn`，Agent 照抄会把后半段当成独立命令执行；`crawl status` 的 `checkpoint.resume_command`、`crawl resume`/`agent crawl`/`wizard` 的 `--resume` / `--status` / `--stop` 提示、wizard 超时与等待登录的 `recovery_action` / `next_action`、`resume link` 后的 `boss apply`、`ai fit` 缺详情提示、`ai resume-optimize` / `ai cover-letter` 的 `boss detail --job-id`、招聘者 `recommendations` / `greet` 的 `--job-id` 同样未转义。现在这些位置统一经 `display.shell_argument` 加 POSIX 单引号，正常编号的输出保持不变，含空白 / 引号 / `;` / `$` 等字符的值可安全复制执行；新增覆盖见 `tests/test_crawl_cli.py`、`tests/test_crawl.py`、`tests/test_agent_crawl.py`、`tests/test_wizard.py`、`tests/test_resume_commands.py`、`tests/test_ai_fit.py`、`tests/test_ai_resume_optimize.py`、`tests/test_ai_cover_letter.py`、`tests/test_recruiter_recommendations_and_read.py`。
- 带中文的参数值让 Click 的英文骨架漏出来：`main.py` 原以「消息含中文」判断是不是自家文案，但 Click 自己生成的校验消息也会含中文——`boss ai reply --tone 不存在` 回 `参数 '--tone' 的值无效: '不存在' is not one of '简洁专业', …`，`boss ai local import --path /tmp/不存在的模型目录` 回 `参数 '--path' 的值无效: Path '…' does not exist.`，`boss login --curl-file /tmp/不存在的cookies.txt` 回 `'…': No such file or directory`；三者 `recovery_action` 都停在「修正参数」、`hints` 为 null。现在枚举 / 数值 / 路径 / 文件四类校验只按消息格式匹配，与消息是否含中文无关，中文候选值与中文路径同样得到中文正文、`boss <cmd> --help` 的恢复指引和 `next_actions`；新增 `tests/test_agent_error_contract.py::test_chinese_choice_values_are_still_translated` / `test_chinese_paths_are_still_reported_in_chinese` 覆盖。
- `boss ai local import` 的源目录与目标互相包含时会把副本当成源一路复制：`--path` 指向数据目录本身（例如 `--data-dir ~/.boss-agent --path ~/.boss-agent`，目标 `models/<模型名>/~/.boss-agent` 就在源目录里面）时，`shutil.copytree` 递归到 `[Errno 63] File name too long`，被顶层兜成「可重试」的 `NETWORK_ERROR`「重试」——拿去重试只会得到同一个错误，真正的原因是路径关系不成立；源目录恰好等于目标目录时旧实现还会先 `rmtree` 掉源目录再复制失败。现在复制前按 resolve 后的路径判断包含关系，互相包含时返回 `INVALID_PARAM`「源目录与导入目标互相包含…」且不写登记表；源已在目标位置时按幂等登记处理，不做复制。新增 `tests/test_ai_local_models.py::test_import_local_model_rejects_source_containing_target` / `test_import_local_model_registers_source_already_at_target` / `test_local_import_cli_reports_recursive_copy_as_param_error` 覆盖。
- Click 的路径校验同样是英文正文：`boss ai local import --path ""` 回 `Invalid value for '--path': Path '' does not exist.`，`recovery_action` 只有「修正参数」、`hints` 为 null。现在输出「`boss ai local import` 的 --path 无效：路径 '' 不存在」，并按 `is a directory` / `not readable` / `not writable` 等情况分别说明，`recovery_action` 与 `hints.next_actions` 指向 `boss <cmd> --help`；新增 `tests/test_agent_error_contract.py::test_invalid_path_value_is_reported_in_chinese` 覆盖。
- 选项值为空时被说成「你没传」：`boss search --preset ""` / `boss search --url ""` / `boss export --url ""` 回「未提供 query，请传入搜索关键词、--preset 或 --url」——调用方明明传了 flag，读到的却是缺参数，只能再猜一次；`--url "   "` 更被当成「仅支持 zhipin.com 的职位搜索 URL」。这些「选择器」参数本来就二选一，空值是可判定的参数错误。现在 `--preset` / `--url` 为空白时返回 `INVALID_PARAM`「--preset 的值不能为空」/「--url 的值不能为空」，hints 指向 `boss preset list` 或 `boss search <关键词>`；`boss resume import ""` 也有同一问题：空路径被 `click.Path()` 解析成当前目录，绕过「文件不存在」检查后在打开时抛 `IsADirectoryError`，被兜成 `LOCAL_IO_ERROR`「本地文件读写失败」让人去查磁盘与权限。现在空路径返回「文件路径不能为空」、目录返回「'…' 是目录，不是简历 JSON 文件」，都指向 `boss resume import <file>`；新增 `tests/test_search_filter_validation.py::test_blank_selector_values_are_param_errors` 与 `tests/test_resume_commands.py::test_import_rejects_blank_path` / `test_import_rejects_directory_path` 覆盖。
- Click 自带校验的英文正文仍原样透出：`boss search golang --page 0` 回 `Invalid value for '--page': 0 is not in the range x>=1.`，`boss export golang --format pdf` 回 `'pdf' is not one of 'html', 'csv', 'json'.`，`boss hr reply u1 hi` 回 `'u1' is not a valid integer.`——错误码是 `INVALID_PARAM`，但 `recovery_action` 只有「修正参数」、`hints` 为 null、正文是英文，Agent 读不出参数该改成什么（前几轮已把 `No such option` / `No such command` / 缺参数 / 多余参数换成中文，数值与枚举校验仍漏着）。现在按 Click 的消息内容分别输出「`boss search` 的 --page 取值无效：0 不在范围 x>=1 内」「`boss export` 的 --format 取值无效：'pdf' 不在可选值 'html', 'csv', 'json' 中」「`boss hr reply` 的 FRIEND_ID 取值无效：'u1' 不是整数」，`recovery_action` 与 `hints.next_actions` 指向 `boss <cmd> --help`；正文格式不认识（例如已被本地化）时保持原样。新增 `tests/test_agent_error_contract.py::test_invalid_option_value_is_reported_in_chinese` 覆盖。
- 缺必填参数与多给参数时信封仍回 Click 的英文原文：`boss mark <uid>` 漏掉 `--label` 报 `Missing option '--label'.`、`boss resume diff <旧名>` 漏掉第二个名字报 `Missing argument 'NAME2'.`、`boss search golang extra` / `boss resume init a b` 报 `Got unexpected extra argument (extra)`，`recovery_action` 只有笼统的「修正参数」、`hints` 为 null——Agent 看不出缺的是选项还是位置参数，也读不出「多写了一个参数」这个原因（前几轮已把 `No such option` / `No such command` / 缺子命令换成中文，这三类仍漏在英文里）。现在缺必填项按类型输出「`boss mark` 缺少必填选项 '--label'」/「`boss resume diff` 缺少必填参数 'NAME2'」，多给参数输出「`boss search` 收到了多余的参数 'extra'」，两者都给出 `boss <cmd> --help` 的 `recovery_action` 与 `hints.next_actions`；新增 `tests/test_agent_error_contract.py::test_missing_required_parameter_points_at_command_help` 与 `test_unexpected_extra_argument_is_reported_in_chinese` 覆盖。
- `boss crawl` 与 `boss wizard` 把空 `run_id` 说成别的问题：`boss crawl status/stop/results ""` 返回 `CRAWL_RUN_NOT_FOUND`「未找到 crawl run: 」（冒号后是空的），`boss crawl resume ""` 因为先做登录校验而报 `AUTH_REQUIRED`，把 argv 少传一个值的人送去重新登录；`boss wizard --resume/--status/--stop ""` 只看值是否为 `None`，空串被当成「没选任何模式」，回 `WIZARD_INPUT_REQUIRED`「非交互模式需要 --json」，让调用方重复刚做过的动作。现在传了 flag 但值为空时统一按 `INVALID_PARAM` 拒绝：`crawl` 的四个入口在查缓存 / 校验登录之前返回「run_id 不能为空」并提示 `boss crawl status <run_id>`，`wizard` 返回「{flag} 的值不能为空」；新增 `tests/test_crawl_cli.py::test_empty_run_id_is_invalid_param_not_run_not_found` 与 `tests/test_wizard.py::test_empty_selector_value_is_invalid_param` 覆盖。
- 两处「下一步」绕过 `display.shell_argument()` 直接拼用户输入：`boss search` 终端表尾的翻页提示手写双引号（`more: boss search "a"b" --page 2` 引号不配对、`$HOME` 在双引号里照样展开、`全天*` 会被 glob），`boss resume edit --field <拼错的字段>` 的错误消息正文里直接拼简历名（`完整结构见 boss resume export 我的 简历 --format json` 会被拆成两个参数）。两处现在统一走 `shell_argument()`，带元字符的查询词与带空格的简历名都能从提示里原样复制执行；新增 `tests/test_search_more_hint.py` 与 `tests/test_resume_commands.py::test_edit_error_message_quotes_resume_name_with_spaces` 覆盖。
- 「TTY 下一步必须同时进入信封」的静态守卫只扫 `commands/*.py`，漏掉整个 `commands/recruiter/` 子包：招聘者侧的 `boss hr jobs offline` / `online` 同样是 `render=` 带 `next_steps` 的 `handle_output`，在那里把 `hints` 删掉不会有任何测试失败——同一条漂移（终端提示「下一步执行 boss hr jobs online X」，管道 / MCP 的信封却是 `hints: null`）在候选人侧会被拦下、在招聘者侧不会。同时守卫只认裸调用 `handle_output(...)`，`display.handle_output(...)` 这种带模块前缀的写法会被静默跳过。现在守卫对 `commands` 递归扫描并两种调用写法都识别，新增 `test_success_hints_guard_covers_recruiter_package` 锁住覆盖面；失败信息也带上相对包路径，不再只回一个可能重名的文件名。
- `boss platforms` 的下一步是一条跑不通的命令：`hints.next_actions` 固定写成 `boss --platform <name> status`，而 `--platform` 是受约束的取值（当前只接受 `zhipin`），Agent 原样照抄只会换回 `INVALID_PARAM`「不支持的平台 '<name>'」，提示看起来是命令却没有任何可执行路径。现在按本次列出的平台逐个生成 `boss --platform <真实平台名> status`，能力筛选把所有平台都过滤掉时退回当前平台的 `boss status`；同时新增 `tests/test_cli_invariants.py::test_next_actions_are_accepted_by_the_cli` 静态守卫：每一条字面量 `next_actions` 都要能被 CLI 接受（占位符只允许出现在位置参数上）。
- 招聘者侧三条沟通类路径成功时没有下一步：`boss hr greet` 发出招呼后 `hints` 为 `null`（Agent 不知道要回会话列表跟进），`boss hr last-messages` 在没有会话时只回一个空列表（分不清「没有会话」还是筛选太窄，也没有任何后继命令），`boss hr download-resume` 落盘后既不提示文件位置也没有下一步。现在 `greet` 给出 `boss hr chat` / `boss hr last-messages`，`last-messages` 空结果给出 `boss hr chat` 与真人指引，`download-resume` 给出 `boss hr chatmsg <friend_id>` 并在 `operator_actions` 里写明附件保存路径。
- 招聘者侧 `boss hr jobs list` / `offline` / `online` / `detail` 成功时没有下一步：信封 `hints` 为 `null`，终端也不渲染任何内容（无 `render` 时退回打印 JSON）。Agent 把职位下线之后看不出可以核对列表或重新上线，看完详情也没有上下线与返回列表的入口。现在四条成功路径都带上可执行的后继命令（上下线互指、列表指向详情、详情指向上下线与列表），`offline` / `online` 在终端渲染结果面板并与信封同口径。
- `display.shell_argument()` 只在有空白时加引号，漏掉其它会被 shell 解释的字符：简历 / 预设 / watch 名里出现落单单引号时（`boss resume init --name "it's"`），提示是 `boss resume show it's`——引号没闭合，照抄到终端直接语法错误；名字里带 `*`、`$HOME` 或以 `~` 开头时则会被 glob / 变量 / 家目录展开成完全不同的参数。现在空白与 shell 元字符（引号、`$`、`*?[]{}`、`|&;#<>` 等）以及开头的 `~` 统一按 POSIX 单引号转义，普通中文名与 `qwen3:14b` 这类名字的提示保持不变。
- `shell_argument()` 仍有两条自由文本路径直接拼名字：`boss search --preset <名>` 的筛选项校验错误消息里写着 `boss preset remove 我的 列表`（预设名带空格时删不到正确的条目），wizard 的 `candidate_watch` 在本地 watch 参数损坏时 `recovery_action` 为 `boss watch remove 我的 监控 后重新 watch add 我的 监控 <关键词>`（Agent 照抄两个命令都会拆错名字）。现在两处统一走 `display.shell_argument()`，并把恢复指引写成两条完整命令。
- `shell_argument()` 还漏了两处「下一步」：`boss search --preset <名>` 在预设参数损坏或预设不存在时，`hints.next_actions` 仍原样拼预设名（`boss preset remove 我的 列表` / `boss preset add 我的 列表 <关键词>`），`boss ai local pull` 的 `recovery_action` 与 `boss ai local import` 的下一步同样直接拼模型名（`--model 我的 模型`）——名字带空格时 Agent 照抄会把一个名字拆成两个参数，删除/重建命中错误的名字、下载与配置的也不是导入的那个模型。现在这四处统一走 `display.shell_argument()`。
- 两条「下一步」提示与实际情况相反：`boss shortlist compare --tag <未命中的标签>` 在候选池非空时仍提示 `boss shortlist add`——池子里明明有条目，缺的只是标签或一次不带过滤的比较，现在改提示 `boss shortlist compare` 与 `boss shortlist list`；`boss config get/set/reset <未知键>` 的错误信封 `hints` 为 `null`，消息里列了可用项却不再给出能查看当前值的 `boss config list`，现在三者统一带上。
- `boss resume edit --field` 只校验路径存在、不校验目标是不是文本叶子：`--field personal_info --value x` 会把整个子对象换成字符串，`dict_to_resume` 随后抛 `AttributeError`，被兜成不可重试的 `INTERNAL_ERROR`「resume 失败: 'str' object has no attribute 'get'」——参数错误看起来像程序缺陷；`--field name` 更糟：写入后立即被 `updated.name = name` 覆盖，返回 `ok: true` 而内容没变。现在容器 / null 字段按 `INVALID_PARAM` 拒绝并提示点到具体子字段，`name` 改指 `boss resume clone <旧名> <新名>`，字段不存在时的消息与 hints 给出 `boss resume export <name> --format json` 查看完整结构。
- `hints.next_actions` 里的用户输入没有 shell 转义：简历名 / 预设名 / watch 名都是自由文本，`boss resume init --name "我的 简历"` 之后的提示是 `boss resume show 我的 简历`，Agent 照抄会把一个名字拆成两个参数换回 `INVALID_PARAM`；`boss ai` 系命令的 `resume_name`、`digest` / `stats` / `resume export` 的 `open <文件路径>` 同样受影响（路径里有空格时打不开）。现在新增 `display.shell_argument()`（只按空白判断，中文名不加引号），所有插入用户输入的 hint 与 TTY `next_steps` 统一走它；`greet` 原有的私有实现改为复用同一函数。
- `boss search " "` 与 `boss export " "` 把纯空白的关键词当成合法输入：只有空字符串会被「未提供 query」拦下，`strip()` 后为空的一串空格照样进入一次没有关键词的搜索 / 导出——既消耗请求预算（`export` 还会翻页抓取），也把账号放在更宽的风控面里，`boss crawl start ""` 早已拒绝同类输入。现在两个命令在解析 preset / `--url` 之前把纯空白 query 归一成空值，按 `INVALID_PARAM` 返回。
- `boss shortlist remove "" ""` 把空标识当成「候选池里没有」静默成功：返回 `ok: true` 与 `removed: false`，Agent 只看 `ok` 会以为已经删除；同一组里的 `shortlist add "" ""` 早已按 `INVALID_PARAM` 拒绝空值；`shortlist annotate "" ""` 更把空标识说成 `SHORTLIST_ENTRY_NOT_FOUND`「候选池中没有该职位:  / 」，让 argv 少传参数看起来像条目不存在。现在两者在打开缓存之前按 `strip()` 判空，返回 `INVALID_PARAM`「security_id 与 job_id 不能为空」。
- `boss apply "" <job_id>` 不校验空参数：投递是写操作，空 `security_id` / `job_id` 既不可能命中职位，又会带着空参数真的发起一次投递请求，失败后看起来像登录过期或职位失效，真正的原因（argv 拼错）被掩盖。`greet` / `detail` / `mark` 等命令都已拒绝空标识，`apply` 补齐同一类校验——在查本地已投递记录和调用平台之前返回 `INVALID_PARAM`，`hints.next_actions` 指向 `boss search <关键词>`。
- 招聘者侧 `boss hr reply <friend_id> ""` 与 `boss hr jobs offline/online/detail ""` 不校验空参数：空白回复会作为一条空消息真正发到候选人会话里（不可撤回），空 `job_id` 的上下线请求也会照常发给平台。`hr greet` 早已拒绝空招呼内容，现在回复与职位管理补齐同一类校验，均返回 `INVALID_PARAM` 并指向 `boss hr chat` / `boss hr jobs list`。
- `boss mark` / `exchange` / `chatmsg` / `chat-summary` 传空联系人标识时不校验：`resolve_friend_or_emit` 会先把整个沟通列表翻一遍，再报 `CONTACT_NOT_FOUND`——既多一次受限流的请求，又让真正的原因（argv 少了参数）看起来像「联系人不在这份列表里」。现在共用解析入口直接返回 `INVALID_PARAM`，hints 指向 `boss chat` 取 uid / security_id。
- `boss detail ""` 与 `boss greet "" <job_id>` 不校验空参数：空 `security_id` / `job_id` 会照常查缓存并发起平台请求，错误看起来像网络或登录问题，真正的原因（argv 拼错）被掩盖，还白白消耗一次请求。`watch` / `preset` / `shortlist` / `resume` 早已拒绝空参数，这两个核心命令补齐后同一类错误都以 `INVALID_PARAM` 返回并给出取编号/搜索关键词的下一步。
- `ResumeStore.list_all_with_corrupt` 在 `return results, corrupt` 之后还留着一行不可达的 `return results`：它永远执行不到，却让人以为列表分支还有别的出口，且一旦未来调整代码顺序会让该函数在不同路径上返回 `list` 而不是 `tuple`，调用方解包时直接报错。ruff 规则集（E4/E7/E9/F）与 mypy 都不检查不可达语句，因此新增 `tests/test_code_hygiene.py::test_no_unreachable_statements` 静态守卫：`src/` 里任何终止语句（return/raise/continue/break）之后再有同块语句都会失败。
- `boss schema` 中 72 个子命令没有 `description`：`watch` / `crawl` / `preset` / `shortlist` / `config` / `resume` / `ai` / `agent` / `hr` 下的 `add`、`run`、`resume`、`analyze-jd`、`jobs list` 等——schema 是 Agent 的能力真源，只列参数和选项而不说该子命令做什么，Agent 就无法据此选择能力（`boss <cmd> --help` 里有说明，但只对读 CLI 帮助的调用方可见）。现在按 Click 帮助补齐全部 72 处描述，并新增 `tests/test_schema_contract.py::test_schema_subcommands_have_descriptions` 守卫：新增子命令漏写描述会直接失败。
- `boss --help` 里 10 个公开选项没有说明：顶层 `--log-level`，`crawl run` / `crawl start` 的 `--hook-profile`，以及 `ai local configure` / `pull` / `import` 的 `--runtime`、`--model`、`--base-url`、`--confirm-download`、`--path`——帮助里只剩裸开关名（`--hook-profile` 连 `screenshot-full` 需要配 `--hook-dir` 都没写），用户和 Agent 只能靠猜，猜错就落到 `INVALID_PARAM` 上。现在补齐 help 文本，`boss schema` 中 `crawl.run` / `crawl.start` 的 `--hook-profile` 也补上对应 `description`，并新增 `tests/test_cli_invariants.py::test_every_public_option_has_help_text` 静态守卫：新增公开选项漏写说明会直接失败。
- `boss --help` 与多个命令组的帮助里有 18 个命令没有任何描述：`chat-summary`、`pipeline`、`follow-up`、`digest`，`watch` / `shortlist` / `preset` 的全部子命令，以及 `agent review list` / `agent pending list`——帮助里只剩一个命令名，真人看不出它做什么，按 `--help` 逐个探索能力时会直接跳过。同样的说明在 `boss schema` 里本来就有，但 CLI 帮助不该只对读 schema 的调用方可见。现在按 `boss schema` 的既有描述补齐 docstring，并新增 `tests/test_cli_invariants.py::test_every_command_has_help_text` 静态守卫：新增命令漏写描述会直接失败。
- `boss batch-greet --dry-run` 与真实批量打招呼成功后都没有下一步：dry-run 只列候选、`hints` 为 `null`，终端表格下方也没有任何提示，Agent 预览完一批候选人不知道去掉 `--dry-run` 就能真发；真实执行成功后同样为 `null`，而 `boss me` 并没有 greet 分区，Agent 只能自己猜 `boss chat`。现在 dry-run 的 `hints.next_actions` 给出与本次调用同参数、只去掉 `--dry-run` 的命令（关键词含空格时加引号），`operator_actions` 说明「确认候选名单后去掉 --dry-run 重新执行」；真实执行成功后给出 `boss chat` 与到会话列表跟进的提示。
- `boss wizard --status <run_id>` / `boss wizard --stop <run_id>` 成功后没有任何下一步：两处 `handle_output` 都不带 `hints`，Agent 查完状态、或停掉一个运行中的 workflow 之后，看不出该恢复、继续等待还是新建（主执行路径跑完反而会提示 `boss wizard --status <run_id>`）。现在按 run 状态给出可直接执行的后继命令：`stopped` / `failed` / `waiting_input` 给出 `boss wizard --resume <run_id>` 与 `--status <run_id>`，`running` / `pending` 给出 `--status` 与 `--stop`，`completed` 给出 `boss wizard` 新建与 `--status` 复检。
- `boss status --live` 在线验证通过后没有任何下一步：非 live 分支的 `hints.next_actions` 就是 `boss status --live`（`_status_next_actions` 的 complete 分支），而真正跑到 `--live` 且验证成功的那一端 `hints` 为 `null`、终端只打印一行 `logged in as …`——Agent 做完健康检查拿不到任何可执行的业务命令，真人也不知道可以开始浏览职位了。现在 live 成功的信封带上 `boss recommend` 与 `boss search <关键词>`，TTY 额外说明「登录态在线验证通过，可以直接开始浏览推荐职位或搜索职位」。
- `boss greet` 命中平台额度时 `recovery_action` 为 `null`：候选人侧的 `GREET_LIMIT` 由 `handle_platform_error_output` 按登记表补齐，而登记表里这个码的 `recovery_action` 是 `null`——Agent 只看到「今日打招呼次数已用完」，不知道能不能重试、要不要换渠道（招聘者侧的 `hr greet` 在 `_send_error` 里有自己的文案，不受影响）。现在补成「今日主动沟通额度已用完：停止自动打招呼，等平台权益恢复后再试；不要自动重发本次招呼」。
- `boss greet` / `boss apply` 的「已打过招呼 / 已投递过」只给错误码不给恢复指引：`ALREADY_GREETED` / `ALREADY_APPLIED` 的登记表元数据 `recovery_action` 是 `null`，而这两个调用点只传了 `hints`——Agent 读到 `recovery_action: null` 会当成不可恢复的死胡同，实际上会话已经建立、投递记录已经存在，下一步是跟进而不是停住。现在 `ALREADY_GREETED` 的 `recovery_action` 为「用 boss chat 查看会话，或用 boss search <关键词> 换一批职位」，`ALREADY_APPLIED` 为「用 boss me --section deliver 查看投递记录，或用 boss chat 跟进」；招聘者侧的 `hr greet` 仍用命令自己的更具体文案，不受影响。
- `boss show` / `boss detail` 遇到职位下架时给出一个死胡同：`JOB_NOT_FOUND` 的登记表元数据 `recovery_action` 是 `null`，两个调用点又不传 `hints`，Agent 只拿到「职位不存在或已下架」，既没有恢复指引也没有可执行的下一步，只能停在这里；`boss show 0` 的索引错误在同一命令里早已给出 `boss search <关键词>` / `boss recommend`。现在 `JOB_NOT_FOUND` 的 `recovery_action` 改为「该职位已不可用：用 boss search <关键词> 重新搜索，或 boss recommend 查看推荐职位」，两处调用点同时补上 `hints.next_actions`（`boss search <关键词>` / `boss recommend`），TTY 与信封同口径。
- 未知选项只回 Click 的英文原文与误导性拼写建议：`boss search golang --limit 10` 返回 `No such option '--limit'. Did you mean '--city'?`——`--limit` 与 `--city` 只差编辑距离，Click 的猜测与「限制结果数」的意图毫无关系，Agent 照建议改成 `--city` 只会换回另一个参数错误；`error.message` 是英文、`hints` 为 null，`recovery_action` 只有笼统的「修正参数」（写错子命令的 `No such command` 已在上一轮换成中文 + 可选值）。现在 `NoSuchOption` 统一换成 `boss search 没有选项 '--limit'`，`recovery_action` 给出 `用 boss search --help 查看可用选项`，`hints.next_actions` 为可直接执行的 `boss search --help`；全局选项误写在子命令之后（`boss search --json`）仍走原有更具体的指引。
- `boss watch remove ""` / `boss preset remove ""` 把空名字当成「不存在」静默成功：返回 `ok: true` 与 `{"removed": false}`，Agent 只看 `ok` 会以为已经删掉了；同一个命令组里 `watch add "" x` / `preset add "" x` / `watch run ""` 早已按 `INVALID_PARAM` 拒绝空值，唯独删除路径不校验。现在两者在打开缓存之前按 `strip()` 判空，返回 `INVALID_PARAM`「watch 名称不能为空」/「预设名称不能为空」；真正不存在的名字仍保持幂等的 `ok: true` + `removed: false`。
- 写错子命令时信封只回 Click 的英文原文：`boss resume bogus`、`boss crawl bogus`、`boss hr bogus`、`boss ai local bogus`、`boss agent review bogus` 返回 `INVALID_PARAM`，但 `error.message` 是 `No such command 'bogus'.`——Agent 看不出这是哪个命令组、有哪些可选值，`hints` 为 null，`recovery_action` 只有笼统的「修正参数」，只能再猜一次。这与同一组里「只写了命令组、没给子命令」的处理口径相反（那里早已给出中文说明 + 子命令清单 + `hints.next_actions`）。现在 `NoSuchCommand` 同样换成 `boss resume 没有子命令 'bogus'（可选：…）`，`recovery_action` 给出可直接照抄的 `boss resume <子命令> --help`，`hints.next_actions` 按命令组层级（含 `boss ai local` / `boss agent review` 这类二级组）列出全部子命令；Click 8.0–8.3 没有 `NoSuchCommand` 类，按同一条英文消息兼容。
- `boss crawl run` 跑完后没有可执行的下一步：completed 分支的 `hints.next_actions` 是空数组，只有一句 `operator_actions` 让人「查看 output_paths 中的文件」——Agent 拿到 `run_id` 却不知道该用哪条命令把结果读回来，而 `boss crawl status` 的 completed 分支、`boss crawl resume --background` 早已给出 `boss crawl results <run_id>`，启动后跑完的主路径反而断在这里。现在 completed 成功信封带上 `boss crawl results <run_id>`，TTY 也会显示这条下一步。同时 `boss crawl start` 的终端 `next_steps` 与信封 `hints` 口径不同：终端只提示 `status` / `stop`，信封只提示 `status` / `results`，真人看不到结果在哪、Agent 不知道可以中止；现在两条路径统一给出 `status` / `results` / `stop`。
- `boss crawl start "" --city <城市>`（以及 `boss crawl run "" --city <城市>`、`boss agent crawl --query " "`）把空关键词当成合法任务：`_settings_from_context` 不校验 query，空串直接进 `CrawlSettings`，`start` 还会建 run 并拉起后台采集——空关键词等于不筛选，会按城市默认列表逐页抓取，消耗请求预算并把账号暴露在更宽的风控面里，而 `watch add` / `preset add` 早已在写盘前拒绝空关键词、`boss search ""` 也报参数错误。现在 `_settings_from_context` 在构造设置之前按 `strip()` 判空并返回 `INVALID_PARAM`「搜索关键词不能为空」；`run` / `start` / `agent crawl --query` 三个入口共用这一处校验，纯空格同样拒绝，`start` 不再留下 run 或拉起后台进程。
- `boss crawl status` 的下一步只存在于 TTY 渲染：非 `risk_stopped` 时 `render` 里写了 `boss crawl results <run_id>` / `boss crawl stop <run_id>`，信封 `hints` 却是 `null`——`--json` / 管道 / MCP 拿到的下一步为空，Agent 轮询完状态后没有任何后继动作（`risk_stopped` 的解锁指引不受影响）。同时终端对所有状态都提示 `boss crawl stop`：已结束（completed / stopped）的 run 上这条命令仍返回 `stop_requested`，但什么都不会停。现在非风险状态的成功信封带上 `hints.next_actions`，并按状态区分：`queued` / `running` 给出 `boss crawl results <run_id>` 与 `boss crawl stop <run_id>`，已结束的 run 只提示读取结果，TTY 的下一步与信封同一口径。
- 选项缺取值时用法错误把 `command` 报成根命令 `boss`：`boss search --city`、`boss wizard --resume`、`boss crawl start --city` 返回的信封是 `"command": "boss"`，而同一条命令缺位置参数或写错选项时都正确落在外层子命令（`search` / `wizard` / `crawl`）——Click 对「缺位置参数 / 未知选项」会在异常上挂 context，对「选项缺取值」则直接在 parser 层抛 `UsageError`，`exc.ctx` 为 `None`，旧逻辑只能退回根命令名。Agent 按 `command` 归档或路由时会把这三类参数错误归到不存在的 `boss` 条目上（`boss` 在能力清单里是根命令，不是可重试的子命令）。现在 `exc.ctx` 缺失时从 argv 解析第一个子命令（跳过带值的全局选项），`boss --data-dir` 这类根级选项错误仍报 `boss`。
- `boss config get` / `boss config set` / `boss config reset` 在终端里静默成功：三个子命令的 `render` 都是空 lambda，`set` 改完配置、`reset` 恢复默认值之后终端一个字都不打（`--json` / 管道下信封正常），真人只能怀疑命令没生效或按漏了回车，再去跑一次 `boss config list` 确认；同时这三个成功信封的 `hints` 为 `null`，Agent 改完配置也拿不到下一步。现在三者都按动作类命令渲染结果面板（`config get` 显示键 / 当前值 / 默认值，`config set` 显示键 / 新值 / 原值，`config reset` 显示键 / 恢复值与 `restored`），并按实际键名给出 `boss config set <键> <值>` / `boss config get <键>` / `boss config list` 的 `hints.next_actions`。
- `boss show <编号>` 对 0 与负数的处理顺序反了：先查索引缓存，再回 `INVALID_PARAM`「没有缓存的搜索结果，请先执行 boss search 或 boss recommend」——编号是 1-based 的，照提示先搜索一次回来仍是同一个错误，Agent 被送进一个必然失败的循环；现在在查缓存之前就按参数错误拒绝（消息点名收到的编号），`hints.next_actions` 给出 `boss search <关键词>` 与 `boss recommend`。`boss watch run --all` 在一个 watch 都没跑成时（首次使用，或只有参数损坏的条目被跳过）仍给出 `boss watch list` 与 `boss detail <security_id>`：这时没有任何 `security_id` 可填，照抄只会换回一个越界编号；现在空结果的 `next_actions` 改为 `boss watch add <name> <关键词>`，有条目时保持原提示，`hints.corrupt` 的删除重建指引不变。
- 本地简历文件损坏时整条恢复链断掉：手写坏或字段类型写错的 `resumes/<name>.json` 被 `boss resume list` 静默跳过，`boss resume show` / `edit` / `export` / `diff` 报 `RESUME_NOT_FOUND`「简历不存在」（`recovery_action` 是指向那份空列表的 `boss resume list`），`boss resume clone` 更是把内部的 `FileNotFoundError` 漏给顶层，变成 `LOCAL_IO_ERROR`「本地文件读写失败」并让人去查磁盘和权限；而按提示执行 `boss resume init <name>` 又会被同名文件挡住（`RESUME_ALREADY_EXISTS`）——文件明明在，用户却没有任何一步能发现或修复它。现在 `boss resume list` 在 `hints.corrupt` 里列出读不出来的文件名并给出修复 / 删除指引；`show` / `edit` / `export` / `clone` / `diff` 在文件存在但读不出来时返回新增的 `RESUME_FILE_INVALID`（可恢复，`recovery_action` 点名 `resumes/` 下的文件和 `boss resume delete <name>`），`hints.next_actions` 给出可执行的删除与列表命令；`boss resume delete` 对损坏文件仍可直接删除。`docs/troubleshooting.md`（中英）同步登记。
- 多条命令的下一步提示只存在于 TTY 渲染，信封 `hints` 为 `null`：`boss preset remove` / `boss watch remove` / `boss crawl configure` / `boss crawl stop` / `boss crawl results` / `boss ai local pull` / `boss ai local smoke` 的 `render` 里写了 `next_steps`（终端会显示「boss preset list」「boss crawl status <run_id>」等），但 `--json` / 管道 / MCP 拿到的 `hints` 是 `null`——Agent 没有任何后继动作可执行，同一条命令在两条路径上的口径相反。现在这些成功信封的 `hints.next_actions` 与 TTY 提示逐条一致，并新增 `tests/test_cli_invariants.py::test_success_hints_match_tty_next_steps` 静态守卫：`handle_output` 的 render 出现 `next_steps` 却没传 `hints` 时直接失败。
- `boss resume list` / `boss resume applications` 的空结果提示在没有对象时仍给出依赖名字的命令：列表为空时 `hints.next_actions` 是 `boss resume show <name>`（没有简历名可传），`resume applications <name>` 没有关联职位时仍是 `boss resume show <name>`（看不出关联状态）——而同一命令的 TTY 下一步早已按空结果给出 `boss resume init` / `boss resume link <name> <security_id> <job_id>`，两条路径口径相反。现在信封提示跟随结果状态切换，有条目时保持原提示。
- `boss shortlist annotate` 对不在候选池里的职位静默成功：`boss shortlist annotate <security_id> <job_id> --add-tag 重点 --note 等回复` 在候选池里找不到对应条目时返回 `ok: true` / `updated: false` / `item: null`，标签和备注一条都没写进去，Agent 会以为标注已完成；提示还指向与本次操作无关的 `boss shortlist compare`。现在按新增的 `SHORTLIST_ENTRY_NOT_FOUND` 返回（不可恢复），消息点名两个 ID，`hints.next_actions` 给出可直接执行的 `boss shortlist add <security_id> <job_id>` 与 `boss shortlist list`；`docs/troubleshooting.md`（中英）同步登记。同时修正空列表的下一步：`boss shortlist list` 与 `boss shortlist compare` 在没有条目时仍提示 `boss detail <security_id> --job-id <job_id>` / `boss shortlist annotate <security_id> <job_id>`，而这时没有任何 ID 可传，照抄只会换回参数错误或 `SHORTLIST_ENTRY_NOT_FOUND`；现在改为 `boss shortlist add <security_id> <job_id>`，有条目时保持原提示。
- `boss preset list` / `boss watch list` 的 `hints.next_actions` 不随列表状态切换：空列表时仍给出 `boss search --preset <name>` / `boss preset remove <name>`（watch 为 `boss watch run <name>` / `boss watch remove <name>`），而这时没有任何名字可传——Agent 照抄只会把一份从没存在过的预设 / watch 名交给命令，再换回 `SAVED_SEARCH_NOT_FOUND`。同一命令的 TTY 下一步提示（`next_steps`）早已按空列表给出 `preset add` / `watch add`，两条路径口径相反。现在空列表的 `next_actions` 改为 `boss preset add <name> <关键词>` / `boss watch add <name> <关键词>`，有条目时保持原提示。
- 本地 / 会话侧记录缺失被复用成 `JOB_NOT_FOUND`（登记表文案「职位不存在或已下架」、`recovery_action` 为 `null`）：`boss search --preset <写错的名字>`、`boss watch run <写错的名字>`、`boss crawl status/resume/stop/results/shortlist <写错的 run_id>`、`boss agent crawl`、`boss chatmsg/mark/exchange/chat-summary <已轮换或不在列表里的联系人>`、`boss wizard --status/--stop/--resume <写错的 run_id>` 全都返回这个码。Agent 读到它会以为职位被撤下而跳过，既看不出真正错在哪，也拿不到下一步（`recovery_action: null`、`hints: null`）。现在按记录类型拆出四个错误码：`SAVED_SEARCH_NOT_FOUND`（预设 / watch，指引 `boss preset list` / `boss watch list` 并给出重建命令）、`CRAWL_RUN_NOT_FOUND`（采集任务，指引 `boss crawl start` / `boss crawl status <run_id>`）、`CONTACT_NOT_FOUND`（联系人，指引 `boss chat`，并保留原有的「改用 uid」提示）、`WORKFLOW_NOT_FOUND`（workflow 任务，指引 `boss wizard`）；`hints.next_actions` 同时给出可执行命令，wizard 的 TTY 面板与 `docs/troubleshooting.md`（中英）同步。`JOB_NOT_FOUND` 此后只表示平台职位不存在（`boss show` / `boss detail`），并新增静态守卫测试防止本地查找再复用它。
- 按名字引用简历的子命令把「漏填名字」报成「简历不存在」：`boss resume show ""` / `edit ""` / `delete ""` / `export ""` / `clone ""` / `diff "" x` / `diff x ""` / `link "" sid jid` / `applications ""` 返回 `RESUME_NOT_FOUND`「简历 '' 不存在」（不可恢复，`recovery_action` 原为 `null`），而同一类空值在 `resume init --name ""` / `resume clone <源> ""` 里是 `INVALID_PARAM`——Agent 从搜索结果或模板拼出空名时看到的是「这份简历不存在」，会去 `resume list` 找一份从未存在过的简历，而不是意识到参数没填上。现在这九个入口在查 store 之前按 `strip()` 判空，返回 `INVALID_PARAM`「简历名称不能为空」；`RESUME_NOT_FOUND` 的 `recovery_action` 也从 `null` 补成可执行的 `boss resume list`，真正的「简历不存在」不再是死胡同。
- `boss ai config --provider <未登记的名字>` 与 `boss ai local import` 会把 AI 配置写成一个永远用不了的端点：前者（如 `--provider openai-cn` 这类笔误）返回 `ok: true` 并保存 `api_key`，端点解析结果是 null，之后每条 AI 命令都报「AI 服务未配置」——用户会去重填 key，而真正要改的是 provider 名；后者不只写 `provider=custom` / `ai_base_url=None`，还会把 `ai_model` 换成导入名并覆盖 key，把刚配好的 ollama / vLLM 端点整个清空（`ai local status` 仍按 `provider+model+key` 报「已配置」，smoke 却说「未配置」，同一个配置两种结论）。现在 `ai config` 在写入前解析端点：provider 未登记且没有 `--base-url`（含 `provider=custom` 缺 URL）时返回 `INVALID_PARAM`、列出已登记的 provider、不落盘；`is_configured()` 把端点纳入判断，`ai local status` / `smoke` / 所有 AI 命令口径一致；`ai local import` 只登记模型文件、不再改动 AI 配置，`hints` 给出加载该模型后应执行的 `boss ai local configure`。已有坏配置（provider 已填、无端点）再跑 AI 命令时，错误消息与 `recovery_action` 会点名 provider 与 `--base-url`，而不是笼统的「未配置」。
- `boss wizard --input-json` 的输入错误只说自己错、不说该填什么，Agent 拿到的是死胡同：未知 `goal` 报「角色 candidate 不支持 goal 'bogus'」、`requested_steps` 里混进非法步骤报「requested_steps 不属于 goal 'job_search': bogus_step」，两条消息都不列合法取值，Agent 只能再调一次 `boss schema` 自己比对；同一组错误在非交互模式下的信封 `hints` 还是 `null`（`_wizard_error` 从不传 hints），而 `wizard` 正是「跨步骤、可恢复」的那类命令，提示缺失意味着恢复链在这里断掉。现在两条报错都追加「可选: …」（`goal` 按角色列全量、步骤列出该 goal 的合法步骤，与 `require_supported_platform` 的口径一致），非交互错误的 `hints.next_actions` 给出可直接执行的 `boss schema`。
- 天数值无界时，`boss stats --days` / `boss clean --days` / `boss chat --days` / `boss digest --days-stale` 能把 CLI 直接打崩或误报成网络错误：`time.time() - days * 86400` 与 `timedelta(days=…)` 都要把天数转成 float/有界整数，传 `10**400` 这类整数时前者抛 `OverflowError`、后者超范围，而 `boss stats` 没有 `handle_auth_errors` 装饰器、也不在 `main.py` 的 OSError 网兜里，stdout 只有一个 traceback，违反「每条命令只输出一个信封」的契约；有装饰器的命令则被 `except Exception` 兜成可重试的 `NETWORK_ERROR`，Agent 会对着必然复现的输入反复重试。现在四个选项都限制在 0–36500 天（100 年，超出返回 `INVALID_PARAM`），并把 `OverflowError` 归入 `INTERNAL_ERROR_TYPES`（确定性异常、不可重试），`main.py` 也补了同类兜底，未装饰的本地命令不会再漏出 traceback。
- `hints.next_actions` 里混着说明文字，Agent 照抄执行换回 `INVALID_PARAM`：契约说这个字段是「Agent 直接执行的后继命令」（`docs/commands.md`），但 `boss search` / `boss recommend` / `boss favorites list` / `boss history` / `boss detail` / `boss show` / `boss chat` / `boss chatmsg` / `boss mark` / `boss exchange` / `boss greet` / `boss status` / `boss login` / `boss logout` / `boss config` / `boss platforms` / `boss cities` / `boss clean` / `boss stats` / `boss digest` / `boss export` / `boss crawl` / `boss agent review` / `boss resume init` / 多个 `boss ai` 与 `boss hr` 命令给出的却是 `boss xxx — 说明`、「使用 boss xxx 查看…」、「A 或 B」、「boss export <query> -o file.csv — 导出到文件」这类不可执行文本，`search` 这类高频命令的提示甚至全部如此。现在 `next_actions` 只保留逐条可执行的命令（平台前缀统一由 `boss_command_for_ctx` 生成），说明性文字移到 `hints.operator_actions`；风控契约（`display.RISK_ERROR_CONTRACTS`）里的停止指令本就是禁止性指引，保持原样。新增 `tests/test_cli_invariants.py::test_next_actions_hold_executable_commands_only` 静态扫描命令源码，`next_actions` 字面量出现 ` — `、「使用 」前缀或「A 或 B」组合时直接失败。
- 三处提示文案把 `preset` / `watch` 的关键词写成了 `--query` 选项：`boss preset list` 空列表、`boss watch list` 空列表、`boss ai suggest-keywords` 的 `hints.next_actions` / 下一步提示都给出 `boss preset add <name> --query <关键词>` / `boss watch add <name> --query <关键词>`，而 `query` 是位置参数，照抄执行只会换回 `INVALID_PARAM`「No such option '--query'」。现在统一成 `boss preset add <name> <关键词>` / `boss watch add <name> <关键词>`，与 `boss search --preset` 报错时的提示、`docs/commands.md` 的写法一致。
- 只写命令组、没给子命令的调用（`boss resume` / `boss shortlist` / `boss hr` / `boss agent pending`）把整份英文帮助塞进信封的 `error.message`：Click 的 `NoArgsIsHelpError` 消息就是 usage 全文（「Usage: …」「Options: …」几百字符），`recovery_action` 只有「修正参数」，`hints` 为空，Agent 既读不到「缺子命令」这个原因，也拿不到可执行的下一步。现在改成一句中文说明（点名完整的命令路径与可选子命令），并在 `hints.next_actions` 里列出每条可直接执行的 `boss <命令> <子命令>`；`boss config` 这类 `invoke_without_command` 的命令组与根命令 `boss`（默认进入向导）不受影响。
- 子命令的用法错误在信封 `command` 字段里报成叶子子命令名而不是命令名：`boss resume show`（缺参数）报 `show`、`boss shortlist list --bogus` 报 `list`、`boss hr whoami --bogus` 报 `whoami`，而同一命令成功时的信封是 `resume` / `shortlist` / `hr`；这些叶子名不在 `boss schema` 的能力清单里，Agent 按 `command` 归档或路由时会看到一个不存在的命令（`resume show` 是 Agent 最常踩的缺参数路径）。Click 把用法错误记在出错那一层的 context 上，现在沿 context 链上溯到根 context 的直接子级，用法错误统一归到最外层子命令；全局选项层面的错误（如 `boss --bogus schema`）仍报 `boss`，与既有契约一致。
- 本地写入类命令接受空字符串参数并写入无法使用的记录：`boss shortlist add "" ""`、`boss resume link default "" ""`、`boss watch add w ""`、`boss preset add p ""`、`boss resume init --name ""`、`boss resume clone base ""` 都返回 `ok: true`，把空 `security_id` / `job_id` / `query` / 简历名原样写进本地状态——之后 `shortlist list` / `resume applications` 多出一行空数据，`resume list` 多出一份没有名字、`resume show ` 无法点名的简历，`watch list` / `preset list` 存下一条空关键词（等于不筛选）的条目，而提示里的 `boss shortlist remove ` / `boss apply  ` 都没有可执行参数。Agent 从搜索结果里取字段失败、或模板拼接出空值时最容易走到这里。现在这六个命令在写盘前校验（按 `strip()` 判空，纯空格同样拒绝），返回 `INVALID_PARAM`，本地不留记录。
- `boss preset add` 不校验薪资 / 经验 / 学历筛选：`--salary 天价` 这类未知取值被原样存进预设并返回 `ok: true`，之后每次 `boss search --preset <名>` 都在参数解析处失败（`INVALID_PARAM`「未知薪资范围」），而错误消息不提预设名、`recovery_action` 只说「修正参数」——用户这次没传任何筛选条件，看不出该删的是哪条本地记录（同组的 `watch add` 早已用 `resolve_search_code_params` 在保存时校验）。现在 `preset add` 与 `watch add` 用同一套校验（城市、薪资、经验、学历、行业、规模、阶段、职位类型），非法取值在写盘前返回 `INVALID_PARAM`、不落库；旧版本已存下的非法预设在 `search --preset` 报错时追加「来自 preset '<名>'，可用 boss preset remove <名> 删除后重建」。
- 预设 / watch 的参数是合法 JSON 但值类型漂移时，命令报成 `INTERNAL_ERROR` 或 `AUTH_REQUIRED` 而不是本地记录损坏：`saved_searches.params` 的已知键（`query` / `city` / … / `welfare` / `active`）应当是字符串或 null，写成数组或对象都能通过 `json.loads`，直到 `_parse_watch_filters` 调 `.split()` 才抛 `AttributeError`，被顶层兜成「不要重试」的内部错误——而唯一的修复方式是删掉这条本地记录重建。现在 `_decode_saved_params` 在读取边界校验这些键的类型，按已有的 `SAVED_SEARCH_INVALID` 返回，消息点名具体字段与收到的类型；`watch run --all` 也不再静默跳过坏条目：`data.skipped` 与 `hints.corrupt` 会列出本次未执行的 watch。同时 `boss wizard` 的 `candidate_watch` 不再把 `welfare` 按数组写进缓存（正是这条路径会写出 `watch run` 无法读取的记录）：写入时按 CLI `--welfare` 的逗号分隔写法归一，其它参数类型不对在写入时返回 `INVALID_PARAM`；早期版本留下的数组形式福利条件在读取时自动归一，旧记录仍可直接 `watch run`。
- `boss resume import <file>` 在文件里的 `name` 与已有简历同名时静默整份覆盖：`import_file` 解析后直接 `save()`，用户在当前简历上做的编辑全部丢掉，命令仍返回 `ok: true`、`action: "import"`，看不出发生了替换（`resume init` / `resume clone` 遇到同名都是 `RESUME_ALREADY_EXISTS`）。现在默认拒绝同名导入，返回 `RESUME_ALREADY_EXISTS` 并提示改 `name` 字段或加 `--force`；`--force` 显式覆盖时才写盘，`boss schema` 同步声明该选项。
- `boss resume delete` 只删简历文件、不清理该简历的职位关联：`resume_job_links` 以简历名为业务键且没有外键约束，删掉 `default` 后用同一个名字重建（`resume init` 的默认名就是 `default`），`boss resume applications default` 会把旧简历投过的职位连同 `applied` 状态和备注一起报成新简历的记录。现在删除时同步清理该简历的全部关联，信封里的 `data.removed_links` 给出清理条数；其它简历的关联不受影响。
- `boss resume link` 重复关联同一职位会静默清空投递状态和备注：`link_resume_to_job` 用 `INSERT OR REPLACE` 整行替换，主键 `(resume_name, security_id, job_id)` 命中时先删后插，已经推进到 `applied` 的 `status` 打回 `prepared`、`notes` 清空、`linked_at` 改成当前时间；第二次关联没带 `--title` / `--company` 时还会用空串覆盖原有的职位名和公司名。现在冲突时改为按字段更新：本次显式给出非空值的 `job_title` / `company` 才覆盖，`status` / `notes` / `linked_at` 始终保留，只刷新 `updated_at`。
- `boss agent run`（以及 `agent train --live`）在未登录时抛出 `AuthRequired` traceback，stdout 没有任何信封：`run_cmd` / `train_cmd` 没有挂 `handle_auth_errors`（同组的 `agent crawl` 挂了），自动化适配器扫描会话时抛出的登录异常一路冒到顶层，`exit 1` 却只有 stderr 的堆栈，MCP 子进程解析失败后统一回成 `CLI_ERROR`，Agent 完全看不到「先登录」这个真正原因。现在两者与其它命令走同一个装饰器，返回 `AUTH_REQUIRED` 信封（可恢复，`recovery_action` 为 `boss login`），`agent train` 默认的 dry-run 行为不变。
- `boss shortlist add` 重复加入同一职位会静默清空本地标签、备注和首次加入时间：`add_shortlist` 用 `INSERT OR REPLACE` 整行替换，主键 `(security_id, job_id)` 命中时先删后插，用户在 `shortlist annotate` 里打的标签、写的备注和 `created_at` 一起没了，命令仍返回 `action: "add"` 和一份看起来正常的记录——脚本重跑一次 `shortlist add`、或换设备后再导一次就会遇到。现在冲突时改为按字段更新：本次显式给出非空值的列才覆盖（`title` / `company` / `city` / `salary` / `source` / `tags` / `note` / `lid`），`created_at` 保留首次加入时间；响应改为回显存储后的整行，重复 add 时能看到保留下来的标注。
- `boss resume export <name> --format json` 在简历文件损坏（手写坏、字段类型写错）时报成 `LOCAL_IO_ERROR`「本地文件读写失败」：这条路径先用 `exists()` 判断、再让 `export_json` 里的 `get()` 返回 `None` 后抛 `FileNotFoundError`，被顶层 `OSError` 网兜接走，恢复指引让人去查磁盘与权限；同一份文件在 `resume show` / `resume export --format html|pdf` / `resume clone` 里都是 `RESUME_NOT_FOUND`。现在 json 导出同样先 `get()` 再分支，四种格式与其它子命令口径一致。
- 未登录时的调用会占用共享请求预算，把下一条命令卡住 30 秒并报错成限流：`_request` 在预算窗口内才读取凭据，所以一次根本不会发出请求、只会得到 `AUTH_REQUIRED` 的调用，也记了一个名额并把 `next_at` 推后一个 interval（`request_delay` 下限 30 秒）。例如 `boss pipeline`（未登录，`AUTH_REQUIRED`）之后 30 秒内再跑 `boss digest`，会先阻塞到 `request_wait_timeout` 超时，再以「请求节流等待超时，请求未发出」的 `REQUEST_BUDGET_EXHAUSTED` 失败——原因不对，还白等半分钟。现在在申请预算之前先取一次凭据（成功时命中缓存），未登录直接返回 `AUTH_REQUIRED`，不登记名额、不影响后续命令。
- `boss history` 与 `boss favorites list` 在 `hasMore` 为假时仍在 `hints.next_actions` 里建议 `--page N+1`：Agent 按提示执行只会拿到空页，再看到同样的提示，形成空转循环；同一命令的终端渲染（`hint_next`）早已按 `hasMore` 隐藏「more」，两条路径口径相反。`search` / `recommend` 一直是对的。现在两处都只在 `pagination.has_more` 为真时追加下一页提示，`history` 的 TTY 守卫也改用同一个变量。
- `boss ai` 系列命令在模型返回合法 JSON 但不是对象时抛 `AttributeError` traceback：`_call_ai` 只处理了 `JSONDecodeError`，对顶层是数组 / 字符串 / 数字的结果直接 `cast` 成 dict；`ai fit`、`ai keywords`、`ai cover-letter` 随后的 `result.setdefault(...)` 会崩掉，stdout 没有信封。现在非对象结果同样按 `AI_PARSE_ERROR` 返回（可恢复，提示重试），消息里点名收到的类型。
- `boss stats` 与 `stats --format html` 在缓存库损坏时抛 `sqlite3.DatabaseError` traceback：`_collect_stats` 直接 `sqlite3.connect` 打开库，`_safe_count` / `_count_since` 只吞缺表的 `OperationalError`，文件被别的格式占位时 `SELECT` 抛出的 `DatabaseError` 一路冒到顶层，stdout 没有信封（`--format html -o` 同样如此）。现在在读取边界把 `sqlite3.DatabaseError` 归一成 `CacheDatabaseInvalid`，由 `main.py` 统一返回 `CACHE_DATABASE_INVALID` 与备份重建指引；损坏库不会留下半份 HTML 报表。
- `boss clean` 绕过 `CacheStore` 直接操作 SQLite，遇到旧版本数据目录或损坏的库时抛 traceback 而不是信封：`clean --privacy` 在只有 `search_cache` 的旧库上会因 `crawl_runs` 不存在抛 `sqlite3.OperationalError`，`--all` 同理；库文件损坏时则更糟——登录态、简历、聊天快照已经先被删掉，命令才在 `sqlite3.execute` 处崩掉，用户既看不到错误码也不知道哪些文件已清理。现在打开库时把 `sqlite3.DatabaseError` 归一成 `CACHE_DATABASE_INVALID`（命令启动先做一次只读探测，删除动作之前就失败），缺表按「没有可清理项」处理，`clean` 在旧库上正常返回结果。
- `boss stats --format html` 的 `data.bytes` 报的是字符数而不是字节数：同一份中文报表实际落盘 12KB，信封里却写 4KB 左右，与 digest / export 的 UTF-8 字节数口径不一致，Agent 据此判断载荷大小时会低估 3 倍。现在 `-o` 与 `--json` 两条路径都按 `len(html_text.encode("utf-8"))` 返回，并与文件实际大小一致。
- `boss export <query> --format html` 在未指定 `-o` 时不输出 HTML，而是返回 `{"format": "html", "count": N, "jobs": [...]}` 的职位记录数组：`html_file_output = bool(output and fmt == "html")` 把 HTML 渲染限定在落盘路径，`--format html > jobs.html` 拿到的是 JSON，MCP `boss_export` 传 `format: "html"` 不传 `output_file` 时同样拿到非 HTML 载荷。现在 `--format html` 始终按 HTML 渲染：无 `-o` 且未强制 `--json` 时直出 HTML（便于重定向），显式 `--json` 时放进信封 `data.content`（附 `bytes` 与 `count`）；`-o` 行为不变。
- `boss ai local import --path <目录> --model ..` 会把模型复制到数据目录根并可能删掉同名目录：把模型名折成 `models/` 下单层目录的 `_safe_model_dir` 允许 `.`（`qwen3.5` 这类名字需要），于是 `..` 原样通过，目标变成 `models/..`（即 `~/.boss-agent` 根）；源目录同名时先 `shutil.rmtree(target)` 再复制，`auth` / `resumes` / `cache` 这类目录会被直接删除并换成模型文件。`.` / `---` / 空名同样会落回 `models/` 自身或数据目录根。现在只由点与连字符组成的模型名按 `INVALID_PARAM` 拒绝（不复制、不改登记表），正常带点的名字（`qwen3.5`）行为不变。
- `boss ai local pull --model <x> --confirm-download` 在没装 Ollama 的机器上返回 `LOCAL_IO_ERROR`（不可恢复，指引是「确认目标路径是可写目录、目录权限与磁盘空间正常」）：`subprocess.run(["ollama", ...])` 抛的是 `FileNotFoundError`，被顶层 `OSError` 网兜按本地文件读写失败处理，而这正是最需要「先装 Ollama」提示的路径。现在在命令内捕获 `OSError`，按已登记的 `LOCAL_MODEL_PULL_FAILED` 返回（可恢复，消息点名 ollama，指引确认已安装且在 `PATH` 中后重试）。
- `boss digest --format md` 与 `boss stats --format html` 在未指定 `-o` 时无条件把正文写到 stdout，连显式 `--json` 也不例外：stdout 不再是 JSON 信封，`exit 0` 却没有任何 `data`。人工路径（TTY 或 `> report.md` 重定向）需要这种行为，但 `--json` 是硬契约——MCP 子进程总是带 `--json` 调用 CLI，`boss_digest` 传 `format: "md"`、不传 `output` 时拿到的是裸 Markdown，`_parse_cli_output` 解析失败后统一回成 `CLI_ERROR` /「命令执行失败」，Agent 完全看不见日报正文；`boss --json stats --format html` 同样破坏「stdout 只有一个信封」。现在区分两者：仅当 stdout 被管道接走（未强制 `--json`）时保持直出，显式 `--json` 则把正文放进信封的 `data.content`（附 `format` 与 `bytes`）；`-o` 行为不变。
- `boss schema` 只校验并声明了数值选项的下界：`wizard --max-retries` 的上限 10、`crawl configure` / `crawl run` 的 `--port` 上限 65535、`hr chat --summary-limit` 与 `hr last-messages --limit` 的上限 300 在 Click 层都会被拒绝，schema 里却只有 `minimum`。Agent 照 schema 传 `--port 70000` / `--summary-limit 999` 只会换回 `INVALID_PARAM`。现在补齐 `maximum`，并把契约测试扩展为 `minimum` / `maximum` 双向一致。
- `-o` / `--output` 的输出路径语义在各命令间不一致：`boss chat` 会展开 `~` 并创建父目录，而 `boss digest` / `boss stats` / `boss resume export` / `boss export` / `hr download-resume` 直接把字面量路径交给写入函数，`wizard candidate_export` 虽然建父目录却不展开 `~`。MCP 通过子进程调用 CLI、参数不经 shell，`boss_digest` 的 `output` 传 `~/digest.md` 会在当前目录建出一个 `~` 目录；`-o reports/2026/jobs.csv` 这类父目录尚不存在的路径则直接失败。现在统一走 `services/output_path.prepare_output_path`：展开 `~`、按需创建父目录；创建失败仍按各命令原有的文件错误契约上报（CLI 导出为 `LOCAL_IO_ERROR`，`hr download-resume` 为 `INVALID_PARAM`）。
- `boss mark` / `chatmsg` / `exchange` 等服务通过 `services/contact_lookup.find_friend` 分页定位联系人，重复页守卫用整页条目的稳定标识做签名，而条目缺少 `uid` 与 `securityId` 时签名恒为空：第二页一进来就被判成「平台又返回了同一页」，遍历提前终止，后续页里的联系人被上层报成「联系人不存在」。现在签名全为空时不再据此早停，继续翻页，直到命中、`hasMore` 为假或达到页数上限（与 `services/friend_list_pages` 的聚合守卫一致）。
- `boss schema` 的能力清单漏了多个已实现的子命令：`watch list` / `preset list` 在 `options` 与 `subcommands` 里都不存在（`preset` 的描述却宣称有 add/list/remove），`shortlist` / `config` / `resume` 的 `list`、`agent` 的 `control` / `pending` / `stats`、`ai local` 的 `status` / `smoke`、`hr whoami` 则只在 `subcommands` 描述里出现或完全缺席。Agent 按 `boss schema` 枚举能力时会以为这些命令不存在。现在无参数子命令以空对象占位补齐，`watch` / `preset` 补上结构化的 `subcommands`，并新增两条契约测试：结构化 `subcommands` 的键集合必须与实际一级子命令一致，`options` 必须覆盖每个已注册子命令。
- `boss chat` 的跨天 diff 找「上一次快照」时只看 `.json` 后缀再按文件名倒序：同目录里手动备份出来的 `backup.json`（`"b…"` 排在 `"2026-…"` 之后）会被选中，读到非数组内容后整次对比静默退化成「首份快照」，`added` / `removed` / `new_unread` 全是空；未来日期的快照文件（从别处拷入的、系统时钟回拨留下的）同样会被当成「上一次」。现在只接受 `YYYY-MM-DD.json` 命名、日期合法且早于今天的文件，非快照 JSON 与未来日期一律跳过。
- `boss schema` 里有两处与可执行行为不符：全局 `--delay` 的默认值仍写着 `1.5-3.0`，而实际生效的是 `config.DEFAULT_REQUEST_DELAY`（30-60），`--delay` 的帮助与错误示例也停在低节奏取值；`boss digest --format` / `boss stats --format` 在 Click 层只接受 `json` / `md` 与 `json` / `html`，schema 却没声明 `enum`，Agent 照 schema 可能构造 `--format markdown` 并换回 `INVALID_PARAM`。现在默认值与示例统一为 `30-60`，两个 `--format` 补上 `enum`，并新增契约测试：Click 用 `Choice` 限制的选项必须在 schema 里声明同一组取值，全局 `--delay` 默认值必须由 `DEFAULT_REQUEST_DELAY` 推导。
- `boss login --timeout` 由 `default=120` 让 Click 推断成整数，没有下界：`--timeout 0` / `--timeout -5` 会直接进入登录流程，扫码等待的 `range(timeout)` 一次都不循环，立即以「扫码登录超时（0秒）」收尾（负数原样进文案），用户看到的是登录超时而不是参数错误。现在用 `IntRange(min=1)` 在解析阶段拒绝，返回 `INVALID_PARAM` 且不构造 `AuthManager`、不启动浏览器；`boss schema` 的 `minimum` 同步补齐。
- 多页聚合的重复页守卫会误伤缺字段的条目：`boss digest` / `pipeline` / `follow-up` 用 `securityId` 序列判断「平台又返回了同一页」，但 `securityId` 每次请求都会轮换（`services/contact_lookup.py` 早已据此改用 `uid`），签名永远命中不了，平台一旦不认 `page` 参数就会把同一页抓满 50 次，结果里全是重复联系人；而条目里没有 `securityId` 时签名恒为空元组，第二页会被当成重复页直接丢掉，`hasMore` 仍为 true 也不再翻页。`boss favorites list` / `sync` 的 `encryptJobId` 签名有同样的空签名误判。现在沟通列表按 `uid`（缺失时回退 `securityId`）生成签名，两处签名全为空时都不再据此早停，交给 `hasMore` / 页数上限收尾；新增 `tests/test_friend_list_pages.py` 与收藏页回归用例固定这些情况。
- `boss schema` 只声明了选项名和类型，没有声明取值下界：35 个已在 Click 层用 `IntRange` / `FloatRange` 约束的选项（`--page`、`--count`、`--days`、`--days-stale`、`--limit`、`--message-id`、`wizard --timeout`、`crawl` 的预算选项等）在 schema 里仍是任意整数，Agent 照 schema 会构造 `--page -1` 这类必然被 `INVALID_PARAM` 拒绝的 argv。现在这 35 处补上 `minimum`，并新增契约测试`test_schema_option_minimums_match_click_ranges`：双向比对 schema 的 `minimum` 与 Click 的 `IntRange.min` / `FloatRange.min`，缺下界、多下界、数值不一致都会失败。
- 招聘者命令的 `friend_id` 参数仍是裸 `int`：`boss hr chatmsg 0`、`hr reply 0 <消息>`、`hr request-resume 0`、`hr last-messages --friend-id 0`、`hr resume --exchange --friend-id 0` 会把 `friendId=0` 发给平台（同组的 `accept-resume` / `download-resume` 早已是 `IntRange(min=1)`）。现在统一为 `IntRange(min=1)`，非法值在解析阶段返回 `INVALID_PARAM` 且不发出请求。另外 `hr resume --uid` / `--gid` 自 #217 起就已被忽略，但 help 与 `boss schema` 仍写着「交换联系方式时需要」：Agent 照 schema 构造 argv 会先传这两个参数，再收到「需要 --friend-id」的参数错误白跑一次。现在两处都标明已弃用、请改用 `--friend-id`。
- `tests/test_system_proxy.py` 的模块文档字符串里有一处编码事故：「CI runner 环境干净」被写成「环境」+ 三个 U+FFFD +「净」，终端里只显示成方块，门禁一直全绿。已修正，并新增`tests/test_text_encoding.py` 作为全仓文本编码守卫：`src` / `tests` / `docs` / `scripts` / `mcp-server` / `evals` / `.github` 与根目录文档中任一文本文件含 U+FFFD 或不是合法 UTF-8 即失败，并点名文件与行号。
- 仍有一批「页码 / 标签 / 游标 / 数量」选项是裸 `type=int`：`boss me --deliver-page -3` 把负页码原样发给投递记录接口；`hr applications` / `hr chat` / `hr last-messages` 的 `--label-id` 与 `hr chatmsg --max-msg-id` 接受负数（`--max-msg-id 0` 还会在客户端被 `if max_msg_id:` 静默丢弃，翻页变成重取最新一页）；`ai interview-prep "<jd>" --count 0` 生成「0 道题」的 prompt；`ai config --max-tokens -1` / `--temperature -0.5` 会写进本机配置，之后每次 AI 调用都把非法参数发给服务商，失败信息来自网络层，看不出根源是配置。现在这些选项分别用 `IntRange(min=1)`（页码、消息游标、题量、令牌数）、`IntRange(min=0)`（标签 id，0 表示全部）与 `FloatRange(min=0)`（温度）在解析阶段拒绝，返回 `INVALID_PARAM` 且不发出请求、不落盘；`boss schema` 里这些选项及 `hr` 组其余数值选项的 `minimum` 同步补齐。
- 平台返回无法归类的失败时，信封里的 `error.code` 是 `UNKNOWN`，而它没有登记在错误码表里：`boss schema` 的 `error_codes` 查不到这个码，`recovery_action` 直接是 `null`，Agent 拿到一个「不可恢复但没有任何下一步」的错误（`boss search` / `recommend` / `detail` / `greet` / `apply` 等走 `handle_platform_error_output` 的命令都会这样）。现在 `UNKNOWN` 已登记（不可恢复；指引先在官方页面核对本次操作是否生效、用 `boss doctor` 检查登录态与网络，确认后再执行一次，仍失败再提交 issue），并新增守卫测试：平台层分类器与各平台 `_ERROR_CODE_MAP` 能产出的每个码都必须出现在 `boss schema` 的错误码表里；`docs/troubleshooting.md` / `.en.md` 的错误码表同步补上该行。
- `boss export` / `boss batch-greet` 的筛选参数没有校验：未知城市（`--city 火星`）被原样交给 `search_jobs`，在请求构造阶段抛 `ValueError` 后兜底成可重试的 `NETWORK_ERROR` +「重试」，拿同一个非法城市重试永远修不好；`batch-greet` 的 `--salary` / `--experience` / `--education` 未知取值被静默丢弃，等于不筛选就直接打招呼（写操作）；`boss export golang --count 0`（或负数）一行不导，却仍以 `ok: true`、`count: 0` 宣告成功。现在搜索类命令共用 `search_filters.ensure_supported_city` 校验城市（`watch add` 的文案随之一致），`batch-greet` 与 `watch add` 同时校验薪资 / 经验 / 学历标签（`watch add` 存下的就是每次 run 的筛选条件，先存下未知取值等于之后每次 run 都在不筛选的结果上报告新职位），非法值在发出任何请求之前返回 `INVALID_PARAM`；`export --count` 用 `IntRange(min=1)` 拒绝 0 与负数。
- 页码 / 条数 / 窗口天数类 CLI 选项没有取值域校验，非法值会原样进入请求或计算：`boss search golang --page -1` 把 `page=-1` 发给平台、`boss chatmsg <uid> --count 0` 请求 0 条消息、`boss chat --days -1` 把 cutoff 算到未来（永远返回空结果且 `ok: true`）、`boss stats --days -1` 与 `boss digest` / `pipeline` / `follow-up --days-stale -1` 把所有记录当成过期，`hr applications` / `candidates` / `chat` / `chatmsg` / `last-messages` 的 `--page` / `--count` 同样如此。现在这些选项统一用 `click.IntRange`（`--page` / `--count` 至少 1，`--days` / `--days-stale` 至少 0），非法值在解析阶段返回 `INVALID_PARAM` 信封，不会发出任何请求。
- `workflow_runs.params` 只校验了顶层是 JSON object，嵌套结构漂移同样会把 `--resume` 打成 traceback：`inputs` 被写成数组时 `_plan_from_run` 在 `dict(...)` 上抛 `TypeError: cannot convert dictionary update sequence element #0 to a sequence`，stdout 没有任何信封；`requested_steps` 被写成字符串 `"auth_status"` 时 `tuple(...)` 静默把步骤名拆成单个字符，恢复任务返回 `WORKFLOW_PLAN_MISMATCH`（「run_id 已绑定到不同的 workflow plan」），而不是说明记录已损坏。现在在缓存库读取边界统一校验这两个字段，结构漂移按 `CACHE_RECORD_INVALID` 返回（可恢复，指明是 `params.inputs` / `params.requested_steps` 无法解析，指引重新创建任务）；任务列表遇到这类记录同样跳过并在 stderr 点名。
- `boss wizard --input-json '{"inputs": {"max_pages": -3}}'` 会让 `run_search_pipeline` 的 `range(max_pages)` 一次都不循环：workflow 以 `completed` 收尾，结果却是空列表，看起来像「没搜到职位」；`page` 为 0 / 负数时页码被原样作为 `page` 参数发给平台；`count: 0` 又被 `if not raw` 当成缺省悄悄换成默认的 20 条。现在 wizard 的整数输入除类型校验外还校验下界（`page` / `max_pages` / `count` / `friend_id` 至少为 1，`label_id` / `days_stale` / `max_msg_id` 至少为 0），越界按 `INVALID_PARAM` 返回并点名字段与收到的值；显式写的 0 不再被当成缺省值（`count: 0` 报错而不是取 20），`bool` 与小数同样按类型错误拒绝（`int(True)` 会静默变成 1、`int(2.5)` 会截断成 2）。
- `boss wizard` 的失败信封仍是中英混排：缺输入时 `error.message` 是 `workflow input 'gid' is required`，Ctrl-C 中断时是 `workflow interrupted; verify any unfinished operation before starting another run`，标签 / watch 动作 / 城市写错时分别是 `unknown label: ...`、`unknown watch action: ...`、`unknown city: ...`，紧邻的 `recovery_action` 却都是中文。现在这些文案统一为中文（`workflow 缺少输入 gid`、`workflow 被中断；开始新的运行前请先核对未完成的操作`、`未知标签: ...（可选: …）` 等），并新增源码扫描测试覆盖 `wizard/` 下所有 `WorkflowActionError` / `WorkflowInputError` 的静态文案（`INTERNAL_ERROR` 保留异常原文用于排障）。
- `boss wizard --input-json '{"inputs": {"page": "abc"}}'` 把输入类型写错时，action 层的裸 `ValueError` 落到 runner 的通用兜底，被映射成 `NETWORK_ERROR` + 「重试当前 run_id」——同一条输入重试多少次都是同一个错误，Agent 会反复重跑。现在 `page` / `max_pages` / `count` / `label_id` / `days_stale` / `friend_id` 等整数输入统一经 `_int_input` / `_required_int` 转换，类型错误抛 `WorkflowInputError`，runner 按登记表映射为 `INVALID_PARAM`（消息点名具体字段与收到的值），不再被当成平台故障。
- `config.json` 里 `automation` 的数值字段没有范围校验，写坏后直接改变写操作行为：`auto_execute_threshold` 为负数时所有通过初筛的动作都自动执行（发问卷、交换联系方式），`human_review_threshold` 为负数时连低置信度决策也放行，`max_per_tab` 为负数会让 `items[:max_per_tab]` 反转成「去掉末尾 N 个」。现在两个置信度阈值必须落在 [0, 1] 且 `human_review_threshold <= auto_execute_threshold`，`max_per_tab` / `max_actions_per_run` / `max_consecutive_errors` 必须是非负整数（0 仍表示「不扫描 / 不执行 / 首次错误即熔断」）；`config set automation` 写入时即返回 `automation 配置无效: ...` 且不落盘，`agent run/train` 读到坏配置时给同一个修复入口。
- `boss agent run --limit -1` 会处理除最后 1 个以外的全部会话：`--limit` 透传到适配器的 `items[:max_per_tab]`，负数切片语义反转成「去掉末尾 N 个」，而这是会自动发问卷、交换联系方式的写操作路径；`--limit 0` 又被 `limit or config.max_per_tab` 静默放大成默认的 20 个。现在 `agent run` / `agent train` 的 `--limit` 用 `IntRange(min=1)` 拒绝 0 与负数（MCP 工具 schema 同步加 `minimum: 1`），`run_automation_cycle` 按 `is None` 判断默认值并把负数归一为 0，Python API 直接传 `limit=0` 也不会回退。
- `boss batch-greet --count 0` 仍会真实发出 1 条招呼：候选收集先 `append` 再判断 `len(candidates) >= count`，上限为 0 时第一个候选已经进列表，非 dry-run 下就是一次写操作；负数上限永远不成立，同样会走完整批。现在上限在下一次收集之前判断（`--count 0` 收集到 0 个候选），负数按参数错误返回 `INVALID_PARAM` 信封且不发任何请求。
- `boss clean --days -1` 会把 cutoff 算到未来（`time.time() - days * 86400`），目录里所有文件都被当成「超过 -1 天」，一次执行删光 `chat-history` 与 `chat-export` 下的全部快照和导出文件。现在 `--days` 用 `IntRange(0)` 拒绝负数，按参数错误返回信封且不动任何文件。
- `boss config set log_level verbose` 报成功，而 `Logger` 对未知级别静默回退到 `error` 阈值：配置里写着 `verbose`，实际什么日志都不显示。现在 `config set` 校验 `log_level`（与 `--log-level` 的 `Choice` 共用 `output.LOG_LEVELS`），非法值返回 `log_level 必须是以下值之一: error, warning, info, debug` 且不落盘。
- `boss config set role admin` 返回 `ok: true` 并把值写进 `config.json`，而启动期校验只接受 `candidate` / `recruiter`：之后每条命令都在启动阶段返回 `INVALID_PARAM`，连 `boss config reset role` 也一样，只能靠 `boss --role candidate config reset role` 脱困。现在 `config set` 与 `--role` 的 `Choice`、启动期校验共用 `config.ROLES`，写入非法值直接返回 `role 必须是以下值之一: candidate, recruiter` 且不落盘。
- 招聘者聊天页写操作的失败信封是中英混排：`boss hr chat` 发消息未确认时 `error.message` 以 `send_message_by_friend unconfirmed:` 开头，交换联系方式失败 / 未确认时是 `exchange_request_by_friend failed:` / `unconfirmed:`，紧跟着的却是中文恢复指引；页面返回结构异常时的默认文案同样是英文（`unexpected page result`、`unexpected result: ...`）。现在统一为中文（`发送消息未确认: ...`、`交换联系方式请求失败: ...`、`页面未返回可用的结果`），并新增源码扫描测试，防止新增的聊天页失败文案再混入英文。
- 顶层文件读写兜底把所有子命令的 `OSError` 都记成 `command: "boss"`：`boss stats --format html -o /不存在/x.html` 的信封是 `{"command": "boss", "error": {"message": "boss 本地文件读写失败: ..."}}`，按 `command` 过滤日志或分发的调用方认不出是哪个子命令。现在与启动期的缓存库兜底一样，从 argv 取实际子命令名（`command: "stats"`、`stats 本地文件读写失败: ...`）。
- `boss chat --export json -o <路径>` 只接受当前目录或数据目录下的文件：`-o ~/Desktop/chat.json`、`-o /tmp/chat.json` 这类绝对路径被 `os.path.relpath` 算出的 `..` 判成「输出路径不安全」并拒绝（`recoverable: false`），与 `digest` / `stats` 的 `-o` 行为不一致。现在显式给出的 `-o` 一律照写，同时补上 `~` 展开——旧代码对 `-o ~/chat.json` 会在当前目录建一个名叫 `~` 的目录。
- `config.json` 的 `export_dir` 写成数字 / 数组时，`boss chat --export json` 在 `os.path.expanduser` 上抛 `TypeError` traceback，stdout 没有任何信封。现在按参数错误返回中文信封（`export_dir 必须是目录路径字符串或 null，收到 9`），`recovery_action` 直接给出只修这一项的 `boss config reset export_dir`。
- `config.json` 里的 `role` 写成数字或无效值（如 `"admin"`）时没有校验：`boss doctor` 把它拼进诊断命令时 `shlex.quote` 抛 `TypeError: expected string or bytes-like object, got 'int'`，其它命令则静默按求职者处理，与 `--role` 只接受 `candidate` / `recruiter` 不一致。现在启动期统一按参数错误返回中文信封（`参数 --role 的值无效: 不支持的角色 5，可选: candidate, recruiter`），`--role` 覆盖后仍可执行 `boss config reset role` 修复；`--role` 的 `Choice` 与该校验共用同一取值域常量。
- `config.json` 里的 `cdp_url` 不是字符串（手工改成数字，或旧版本写坏结构）时，`boss doctor` 在拼接诊断命令时对值执行 `shlex.quote`，直接抛 `TypeError: expected string or bytes-like object, got 'int'`，stdout 没有任何信封。现在启动期就按参数错误返回中文信封（`参数 --cdp-url 的值无效: 必须是调试地址字符串或 null，收到 9`），`--cdp-url` 覆盖后仍可执行 `boss config reset cdp_url` 修复。
- `config.json` 里的 `browser_source` 不是字符串（手工改成数字、数组，或旧版本写坏）时，启动阶段直接对值调用 `.strip()`，所有命令都抛 `AttributeError: 'int' object has no attribute 'strip'` traceback，连用来修复它的 `boss config reset browser_source` 也跑不了。现在非字符串值按参数错误返回中文信封（`不支持的浏览器通道 5，可选: auto, stored-cookie, httpx-only, existing-browser`），`--browser-source auto` 覆盖后仍可执行修复命令。
- `boss platforms` 的过滤参数与 wizard 的平台校验仍有英文：`platforms --platform zhilian` 返回 `unknown platform 'zhilian', supported: zhipin`，`platforms --capability nope` 返回 `unknown capability ...`，`boss wizard` 对已移除平台的旧 run 返回 `... supported: zhipin`，与 `--platform` 全局选项已改好的中文文案不一致。现在统一为「不支持的平台 'zhilian'，可选: zhipin」「不支持的能力 ...」「角色 candidate 不支持平台 ...，可选: ...」。
- 启动期与 `config set` 的参数校验把英文原文漏进信封：`boss --delay 5-1 status` 的消息是 `Invalid value for --delay: delay must be a finite range like 1.5-3.0 with 0 <= low <= high`，`boss config set request_delay abc` / `request_max_per_minute abc` 会直接透出 `could not convert string to float: 'abc'`，既不中文也没点名配置项；`--platform` 写成已移除的平台时消息同样以 `unknown platform ...` 开头。现在这些自写文案统一为中文（`参数 --delay 的值无效: 必须是形如 1.5-3.0 的数值区间，且满足 0 <= 下限 <= 上限`、`request_max_per_minute 必须是整数，收到 'abc'`、`不支持的平台 'zhilian'，可选: zhipin`），Click 自带的英文校验文案保持原样、不会被拼上中文前缀。
- 本地缓存库文件损坏时所有命令都 traceback：`cache/boss_agent.db` 被写坏、被别的文件占位（如复制中断、磁盘异常）后，`CacheStore` 初始化抛 `sqlite3.DatabaseError: file is not a database`，而异常发生在命令自己的 `try` 之外，`boss preset list`、`boss watch list`、`boss shortlist`、`boss crawl`、`boss wizard` 等几乎全部本地状态命令都没有信封。现在启动期统一兜底为新登记的 `CACHE_DATABASE_INVALID`（可恢复，指明库文件路径，备份后删除即可自动重建，并说明本地数据会一起作废），`envelope.command` 取实际子命令名；并发锁（`database is locked`）等可重试错误仍按原错误上抛，不会被误报成库文件损坏。
- 本地 crawl / workflow 记录损坏时直接 traceback：`crawl_runs.params` / `hook_results`、`crawl_jobs.payload`、`workflow_runs.params` / `last_result` / `error`、`workflow_steps.result` 被写坏（手工改库、磁盘异常、旧版本中断）后，`boss crawl status` / `crawl results` / `crawl resume` / `crawl shortlist` 与 `boss wizard --status` / `--resume` / `--stop` 都抛 `JSONDecodeError`，stdout 没有任何信封；`boss wizard` 的任务列表遇到一条坏记录就整体不可用。现在这些读取统一走类型化异常并返回新登记的 `CACHE_RECORD_INVALID`（可恢复，指明重建任务的方式），合法 JSON 但容器类型漂移（如 `params` 不是 object、`hook_results` 不是数组）同样在读取边界拦下；任务列表跳过坏记录并在 stderr 点名，交给用户重建而不是静默消失。
- 本地保存的预设 / watch 参数（`saved_searches.params`）被写坏后，`boss preset list` / `boss watch list` 直接抛 `JSONDecodeError` traceback、stdout 没有任何信封；`boss search --preset` / `boss watch run` 被兜成 `NETWORK_ERROR` +「重试」，Agent 会针对一条本地数据问题无限重试，wizard 复用 watch 时也落进通用兜底。现在列表命令跳过坏条目并在 `hints.corrupt` / `operator_actions` 里点名；按名称使用坏条目时返回新登记的 `SAVED_SEARCH_INVALID`（可恢复，提示删除后重建），wizard 返回同一错误码，`watch run --all` 只跑参数可用的条目。
- 预算 / 时限停止的默认文案仍是英文，会原样进入信封 `error.message`：`RequestBudgetExceeded` 的 message 形如 `request pacing exhausted its wait timeout before dispatch`，紧邻的 `recovery_action` 却是登记表里的中文，Agent 与真人看到的是混排文案；`boss wizard` 的 `WORKFLOW_TIMEOUT`、`boss ai local import` 的来源缺失、招聘者自动化的熔断默认原因（`circuit breaker is open`）同样如此。现在这些默认文案统一为中文（如「请求节流等待超时，请求未发出」「workflow 超过 60 秒未完成」「模型路径不存在」「熔断已打开」），并新增源码扫描测试，保证 `RequestBudgetExceeded` 的新抛错点不会把英文文案漏进信封。
- `boss agent review approve/reject` 处理不存在的复核项时用 `raise click.ClickException`，被组层兜底成 `command: "boss"`、英文消息（`review item not found or not reviewable: ...`），与其它子命令的信封不一致。现在按实际命令名输出 `INVALID_PARAM` 与中文消息，并给出 `boss agent review list` 的后继指引。
- 搜索缓存条目损坏时 `boss search` 直接 traceback：`search_cache.response` 被写坏（手工改库、磁盘异常）时 `json.loads` 抛 `JSONDecodeError`，结构漂移（缺少 `data` 或 `data` 不是数组）时 `result["data"]` 抛 `KeyError`，同一条查询会一直失败且没有清理指引。现在坏条目按缓存未命中处理，重新搜索后用新结果覆盖同键条目。
- 招聘自动化状态文件损坏时没有信封：`automation/state.json` 被手工编辑写坏或在旧版本留下半截内容后，`boss agent run` / `agent stats` 直接抛 `JSONDecodeError` traceback；`action-log.jsonl` 等日志里有一行坏数据就会让 `stats` / `pending list` 整体失败。更严重的是 `automation/stop.json`（独立熔断开关）读不出来时被当作「没有熔断」，自动化会在状态未知的情况下继续向平台发送消息。现在状态文件损坏返回新登记的 `AUTOMATION_STATE_INVALID`（可恢复，指明文件与备份建议），单行损坏的 JSONL 跳过该行，熔断开关读不出来一律按已熔断处理并带上 `corrupt` 标记与原因。
- `boss ai` 各命令的 `@文件路径` 参数只检查文件是否存在：GBK 保存的 JD / 聊天记录、误选的 PDF 或图片会抛 `UnicodeDecodeError` traceback，传目录抛 `IsADirectoryError`，权限不足抛 `PermissionError`，stdout 没有任何信封。现在八条支持 `@文件` 的命令（`analyze-jd`、`optimize`、`suggest`、`reply`、`interview-prep`、`chat-coach`、`resume-optimize`、`cover-letter`）共用同一个读取入口：非 UTF-8 给 `INVALID_PARAM` 并提示转存为 UTF-8，目录给 `INVALID_PARAM`，读取失败给 `LOCAL_IO_ERROR`。
- 本地 JSON 状态仍是「直接覆盖写目标文件」：`config.json`、简历、模型登记表、索引缓存、聊天快照在写入中途被 Ctrl-C、进程被杀或磁盘写满时，会留下半截文件或空文件——`config.json` 坏掉会让之后每条命令都卡在启动阶段，简历/登记表/缓存坏掉则要人工修复。现在这些写入统一走 `services/json_files.write_json_atomic`：同目录临时文件写满并 `fsync` 后再 `os.replace` 原子替换，失败时原文件保持不变、临时文件被清理；`config set/reset` 写盘失败仍返回 `LOCAL_IO_ERROR`。
- 搜索/推荐结果缓存 `cache/index_cache.json` 结构损坏（顶层不是对象、`jobs` 不是数组、条目不是对象、非 UTF-8）时，`boss show <编号>` 把 `AttributeError` 兜成 `INTERNAL_ERROR`，恢复指引是「先升级到最新版本；仍出现时提交 issue」——真正该做的是重新搜索一次。现在这类缓存按「没有缓存」处理：`show` 返回 `INVALID_PARAM` 并提示先执行 `boss search` / `boss recommend`。
- 本地模型登记表 `models/registry.json` 损坏（`ai local import` 写文件时被中断、手工编辑写坏）后，`boss ai local status` 与 `ai local import` 都直接抛 `JSONDecodeError` traceback；写入方还会用空表覆盖原文件，把还能人工修复的登记条目丢掉。现在 `ai local status` 按空表降级显示，`ai local import` 在复制模型文件前先校验登记表，损坏时返回新登记的 `MODEL_REGISTRY_INVALID`（可恢复，指明文件与重建方式）且不改动原文件；登记表改为先写临时文件再 `os.replace`，中断不会留下半截 JSON。
- `boss resume import` 对上不合法的输入文件会直接抛 traceback、stdout 没有任何信封：GBK 保存的 JSON（或误选的 PDF / 图片）触发 `UnicodeDecodeError`，顶层是数组或字符串触发 `AttributeError`，`name` 写成数字时崩在 `_safe_filename` 的 `int.replace` 上；`boss resume list` / `resume show` 遇到非 UTF-8 或字段类型写错的简历文件同样 traceback。现在导入失败返回 `INVALID_PARAM` 信封并给出文件路径与具体原因（UTF-8 转码、顶层必须是 JSON 对象、缺少字符串 `name`、JSON 语法错误的行列），列表跳过读不出来的文件，单份读取出错按 `RESUME_NOT_FOUND` 处理。
- `config.json` 内容损坏（写一半被中断、手工编辑写坏语法）时报错只说 `Invalid value for config.json: Expecting value: line 1 column 14`：既没有文件路径，也没有说明「所有命令都卡在启动阶段，`config set/reset` 也用不了」。现在启动阶段的错误消息给出配置文件路径、出错行列与修复指引（修正或删除该文件，删除后配置回到默认值）；文件不是 JSON 对象时同样带上路径。
- 本地文件读写失败被当成可重试的网络错误：`boss chat --export json` 的 `export_dir` 指向同名文件时信封是 `NETWORK_ERROR` + `recoverable:true`，Agent 会无限重试同一条命令；`boss config set` 遇到只读数据目录、`resume export` 写文件失败则直接抛 traceback；`config.json` 本身读不出来（权限不足、路径被同名目录占用）时，异常发生在 CLI 启动阶段，所有命令都拿不到信封，连 `boss config reset` 也跑不了。现在新增错误码 `LOCAL_IO_ERROR`（`recoverable:false`，提示检查路径、权限与磁盘空间）：文件系统异常按它返回，`ConnectionError` / `TimeoutError` 等套接字错误仍按 `NETWORK_ERROR`；`config.json` 读取失败在启动阶段返回 `INVALID_PARAM` 并给出文件路径；没有统一装饰器覆盖的写文件路径由 CLI 顶层兜底成信封。
- 本地「已打招呼 / 已投递」去重以每次请求都会轮换的 `securityId` 为主键，跨请求必然失效：`batch-greet` 换个 `securityId` 就会把同一职位再打一次招呼，`search --skip-greeted` 与职位结果里的 `greeted` 标记也查不到上次的记录。现在去重键改为「账号指纹 + 稳定 `encryptJobId`」：账号指纹取登录 `wt2` Cookie 的 SHA-256 前 16 位（不落盘 Cookie 本身），`greet` / `apply` / `batch-greet` / `search` / `detail` / `show` / `recommend` 与求职者 wizard 共用同一作用域。同一台机器上换账号不会误命中另一个账号的记录；没有登录态时保留旧的 `securityId` 匹配行为。旧缓存库在打开时自动迁移，迁移在单个写事务里完成；旧记录没有账号归属，仍可按原 `securityId` 查到，但不会当作当前账号的去重依据，升级后同一职位最多重新记录一次。
- `search` 的 24 小时结果缓存键不含账号：同一台机器换账号后会直接看到上一个账号缓存的结果与 `greeted` 标记；缓存命中又不重算 `greeted`，打完招呼的职位在缓存过期前仍然显示未打招呼。现在缓存键带上账号指纹（搜索缓存 schema 升到 4，旧缓存自然失效），命中后按当前账号、稳定 `encryptJobId` 重新计算每个职位的 `greeted`，其余字段仍复用缓存、不额外请求平台。
- `watch` 的已见记录仍以 `securityId` + `jobId` 复合键保存，而 `securityId` 每轮搜索都会轮换：同一职位每次 `watch run` 都被当作新职位重复通知。现在登录态下按「账号指纹 + 稳定 `encryptJobId`」去重，换账号后各自的已见记录互不影响；没有登录态时保留旧的复合键行为。旧记录没有账号归属，升级后每个职位会重新通知一次。
- 单条 `boss greet` 在平台返回风控码（如 code 36）时仍输出 `recoverable: true` 和「重试」，与「命中风控立即停止、不无界重试」的硬边界相反，Agent 可能据此重试写操作。现在与 `apply` / `batch-greet` 一样走 `RISK_ERROR_CONTRACTS`：风控时 `recoverable: false`，`recovery_action` 与 `hints` 给出停止和人工处理指引；无法识别的业务失败仍按 `NETWORK_ERROR` 提示重试。
- `export` 搜索失败、`status --live` 用户信息失败与 `batch-greet` 搜索失败三条路径把 `recoverable` 硬编码为 `false`，也不给 `recovery_action`：登录过期（`AUTH_EXPIRED` / `AUTH_REQUIRED`）或限流（`RATE_LIMITED`）时 Agent 被告知不可恢复，风控码则缺少停止指引。现在统一走 `handle_platform_error_output`，按错误码登记表给出 `recoverable` / `recovery_action`，风控码的 `next_actions` 取自 `RISK_ERROR_CONTRACTS`；`status` 的 `auth_health` / `checks` 诊断字段随 `extra_hints` 保留，`detail` 的风控信封也补上同一份停止指引。
- `boss wizard` 的导出动作（`candidate_export`）没有把账号指纹传给搜索管线，登录态下 JSON 导出里的 `greeted` 全部是 `false`，与 `search` / `candidate_search` 的结果不一致。现在与其它求职者动作一样传入当前账号作用域。
- `search` 与 `watch run` 捕获 `SearchPipelinePlatformError` 后同样把 `recoverable` 硬编码为 `false` 且不给 `recovery_action`：响应字典形态的风控码（如 code 36）没有停止指引，登录过期也无法提示重新登录。`watch run --all` 还会把单个 watch 的风控收进汇总结果继续跑下一个 watch，向平台继续发请求。现在两条命令都按错误契约输出；`--all` 遇到风控立即停批，已完成的 watch 结果随 `error.details` 带回。
- 招聘者侧的 `hr download-resume` / `hr accept-resume` / `hr greet` 只捕获 `AccountRiskError`，而 `EnvironmentRiskError` / `EnvironmentRiskLockedError` 与其平级：环境风控会掉进兜底分支——`download-resume` 回 `NETWORK_ERROR` 并提示“检查网络后重试”，`accept-resume` 误报成“结果未知”，`hr greet` 也把风控当成未确认结果。现在三条写路径都按 `PlatformRiskError` 基类捕获，风控码的 `recoverable` / `recovery_action` / `next_actions` 统一取自 `RISK_ERROR_CONTRACTS`；响应字典形态的风控码与异常形态给同一份停止指引，非风控写失败仍保留“先核对、勿重试”的原有约束。
- `me` 没有 `@handle_auth_errors` 包裹，平台在获取个人信息 / 简历 / 求职期望 / 投递记录时抛出的风控异常会落到 `NETWORK_ERROR` 兜底：`recoverable: true` +「重试」。现在按 `PlatformRiskError` 单独输出，`recovery_action` / `next_actions` 取自 `RISK_ERROR_CONTRACTS`，不再提示重试。
- `boss doctor --live-probe` 的在线探测命中风控时，异常会落进通用兜底被记成「探测异常」并原样带出平台响应文本，随后还继续向另一个身份发招聘者接口请求；报告里的 `cdp_risk_lock` 仍是探测前的快照，`hints.next_actions` 还会建议再跑 `status --live` / `doctor`。现在 `PlatformRiskError` 与响应字典形态的风控码都在第一条命中处停止，检查项与 `hints` 按 `RISK_ERROR_CONTRACTS` 给出停止指引，不再透传异常文本；探测后重读本地风险锁，`data.live_probe_risk` 标记命中码，并抑制后续任何会触发平台请求的建议。
- `hr chat` 读取聊天页快照（`chat_list_snapshot`）时把包括风控与预算停止在内的所有异常都当成「增强失败」吞掉，随后继续发 `userLastMsg` 请求；`boss wizard` 的登录门在 `login` 命中风控后也只显示失败面板，然后回到「现在登录 / 仅看本地功能 / 退出」菜单，等于邀请用户重试已被风控拦截的登录。现在两处都按 `is_request_stop` / `PlatformRiskError` 立即停止：`hr chat` 直接输出风控信封且不再请求最近消息，登录门展示停止指引后退出向导。
- 共享错误装饰器与 `boss login` 的风控信封仍用 `str(exc)` 作为 `error.message`，而风控异常的消息来自平台响应原文（`raise_platform_risk_response` 直接取平台 `message`），可能带临时凭据。现在两处都改用 `risk_error_message()`：`error.message` 只含错误码登记表文案，停止指引仍由 `recovery_action` / `hints` 承载，与 `me`、`hr download-resume` 等命令一致。
- 风控信封的消息脱敏只覆盖了异常路径：`search`、`watch`、`detail` 与共用 `handle_platform_error_output` 的三十余条命令在平台以响应字典返回风控码时，仍把平台响应原文当作 `error.message`。现在由 `platform_error_message()` 统一决定文案：风控码只给错误码登记表内容，普通错误码继续保留平台消息或命令兜底文案。
- 预算 / 时限停止（`RequestBudgetExceeded`、请求控制停止、`WORKFLOW_STOPPED` / `WORKFLOW_TIMEOUT`）在四条命令里被当成普通失败：`detail` 的 httpx 快速通道失败后会改用浏览器通道再发一次请求；`batch-greet` 会 sleep 后重试并向下一批候选人继续打招呼；`hr download-resume` 回 `NETWORK_ERROR` 并提示“检查网络后重试”；`me` 同样落进 `NETWORK_ERROR` 兜底。停止信号本应终止当前 workflow、不产生新请求。现在四处都按 `is_request_stop` / `RequestBudgetExceeded` 立即停止：`batch-greet` 输出 `ok:false` 信封并带上已完成的 `details`，`detail` 不再降级通道，`hr download-resume` 与 `me` 按错误码登记表给出 `recoverable` / `recovery_action`。
- 风控信封的消息脱敏还漏了三处会进入 JSON 输出的路径：`boss wizard` 运行器把 `PlatformRiskError` 的 `str(exc)` 写进 `error.message`；招聘者自动化适配器把同类文本存进健康检查的 `warning`（进而成为 `hr agent run` 事件原因）；爬虫的运行结果 `error` 字段与 `CrawlRiskError.from_response()` 直接拼接平台响应 `message`。这些文本来自平台响应原文，可能带临时凭据。现在四处都改用 `risk_error_message()`，只保留错误码登记表文案与停止指引；爬虫本地产生的风险提示（如未登录）不受影响。
- 招聘者侧还有两条风控路径会把平台原文或成功信封带出去：`hr greet` 在平台以响应字典返回风控码时把 `platform_message` 原样写进 `error.details`；`hr chat` 的 `userLastMsg` 补充摘要在命中风控码时只记成 `partial_error` / `last_messages_error`，仍输出 `ok:true`，审计 `hr last-messages` 也继续返回部分结果。现在 `hr greet` 的风控错误不再保留平台原文，`userLastMsg` 的风控码与异常形态一样立即终止，输出风控信封并停止后续请求。
- `boss wizard` 的动作层 `_unwrap()` 在平台以响应字典返回风控码时，虽然已经按 `RISK_ERROR_CONTRACTS` 给出不可恢复与停止指引，但 `WorkflowActionError.message` 仍是 `parse_error()` 返回的平台原文，最终写进 workflow 的 `error.message`。现在风控码改用 `risk_error_message()`，只保留错误码登记表文案；非风控码（参数错误、鉴权失败等）仍保留平台说明。
- `hr accept-resume` 与 `hr greet` 把「请求在调度前就被预算 / 通道挡下」也报成结果未知：`RequestBudgetExceeded` 明确标注 `request_sent: false`，前者却回 `RESUME_ACCEPT_RESULT_UNKNOWN` 让人去官方页面核对一个不存在的操作，后者还保留 pending 预约，把这个候选人永久锁成「结果未知」——通道恢复、预算冷却结束后也无法重试。现在两条写路径都按停止契约输出 `REQUEST_BUDGET_EXHAUSTED` / `WORKFLOW_TIMEOUT`；`hr greet` 在预算停止与 `BrowserSourceUnavailable`（通道全部不可用）时撤销尚未发出的预约（只删 pending，已发送与结果未知的记录保持不动）。
- 求职者侧的 `greet` / `apply` 在平台已确认成功、随后本地记账（`record_greet` / `record_apply`）失败时，会把整个命令降级成 `NETWORK_ERROR`（`recoverable: true`、「重试」）：重跑会绕过 `is_greeted` / `is_applied` 检查，把同一次打招呼或投递再提交一遍。`batch-greet` 更直接——记账异常落在打招呼的重试分支里，1～2 秒后就会把同一个招呼再发一次。现在本地记账失败输出新错误码 `LOCAL_STATE_WRITE_FAILED`（不可恢复，说明操作已生效、禁止自动重发）；`batch-greet` 停止剩余候选人，已发出的条目带 `recorded: false`，不再重试或继续。
- 招聘者自动化的页面写动作返回 `ACTION_UNCONFIRMED`（动作可能已生效、发送未确认）时，适配器把它当成普通 `blocked`：本轮事件落成 `STOPPED_BY_SAFETY`，已登记的 `PLATFORM_VERIFICATION_REQUIRED` 状态从未产生；对应的待执行项又保留为 `pending`，下一轮 `agent run` 会对同一候选人自动重发招呼或换联系方式，`--resume` 之后也一样。现在未确认结果记为 `PLATFORM_VERIFICATION_REQUIRED` 并打开熔断，待执行项标记为 `verification-required`、不再回到待执行队列，`agent stats` 的近期错误也包含该状态。
- `boss wizard` 的 `candidate_apply` / `candidate_greet` 在平台已确认成功、随后本地记账（`record_apply` / `record_greet`）失败时，异常落进 runner 的通用兜底，workflow 报成 `NETWORK_ERROR`（`recoverable: true`，「重试当前 run_id」）：重试或 `--resume` 会绕过 `is_applied` / `is_greeted` 检查，把同一次投递或打招呼再提交一遍。现在与单次命令一致，输出 `LOCAL_STATE_WRITE_FAILED`（不可恢复，说明操作已生效、禁止自动重发），`error.details` 带 `applied` / `greeted` 与 `recorded: false`。
- `boss wizard --resume <run_id>` 会重跑失败步骤，包括结果未知的写动作：`candidate_apply` / `candidate_greet` 的本地记账失败，以及招聘者的 `recruiter_reply` / `recruiter_request_resume` / `recruiter_exchange_contact` 返回 `ACTION_UNCONFIRMED`，恢复时会再次执行同一动作。现在这类步骤错误带 `replay_safe: false`，`--resume` 保持原失败状态并返回原核对指引，不再重跑；该标记同时阻止 runner 的步骤内重试。
- `boss schema` 的错误码登记表漏了三个实际会出现在信封里的错误码：钩子阻止 `HOOK_BLOCKED`、模型下载确认 `CONFIRM_DOWNLOAD_REQUIRED` 与 Ollama 拉取失败 `LOCAL_MODEL_PULL_FAILED`；其中 `HOOK_BLOCKED` 的 `recovery_action` 还是 `null`。现在三码都已登记，钩子阻止时按登记表返回可执行的恢复指引。
- `boss ai local import` 的 `MODEL_SOURCE_NOT_FOUND`（模型源路径不存在）会出现在错误信封里，却未登记在 `boss schema` 的错误码表。同时 `tests/test_schema_contract.py` 的覆盖率断言依赖一份手工错误码清单，新增码不会自动被检查——上一轮的 `HOOK_BLOCKED` 等三码正是这样漏掉的。现在该码已登记，`ai.local.import` 的 `recoverable` / `recovery_action` 取自登记表；测试改为扫描源码中形如错误码的字符串字面量，任何未登记的新码都会让测试失败。
- 两条恢复指引指向了无效操作：`crawl start` 后台任务拉起失败（OSError）时要求「设置 operating_mode=research」，但该模式门槛已不再限制任何能力，照做不会改变结果；`resume export --format pdf` 失败时提示 `uv run playwright install chromium`，而 PDF 导出实际用 patchright 渲染。现在 crawl 的指引改为检查磁盘空间与数据目录权限后按 run_id 恢复（没有 run_id 时重新 `boss crawl start`），PDF 导出的 `recoverable` / `recovery_action` 取自错误码登记表，提示 `patchright install chromium`。
- `boss logout` 的异常路径把本地登录态清除失败（如磁盘满、权限不足）报成 `NETWORK_ERROR`，还标记 `recoverable: false` 且不给 `recovery_action`：Agent 既不会重试也不知道该检查什么。现在改用新登记的 `LOGOUT_FAILED`（可恢复，指引检查数据目录权限与磁盘空间后重试）。`boss export` 未提供 query / `--url` 时返回的 `INVALID_PARAM` 却被标成 `recoverable: true`，Agent 原样重试只会再次失败；现在与登记表一致标为不可恢复，保留修正参数的恢复指引。
- 命令层大量错误信封只传 `code` / `message`，`recoverable` 与 `recovery_action` 因此落成 `false` / `null`，与 `boss schema` 的错误码登记表矛盾：例如 `INVALID_PARAM` 已登记「修正参数」，Agent 却拿不到任何指引；联系人查找的网络错误被标成不可恢复；招聘者 `accept-resume` / `download-resume` 的平台不支持同样被标成不可恢复。现在 `envelope_error` 与 `handle_error_output` 在调用方未显式声明时从登记表补齐元数据，TTY 与 JSON 两条路径看到同一份恢复契约；显式传入的 `recoverable: false`（如写操作结果未知）仍优先，不会被登记表覆盖。
- `boss wizard` 的 TTY 菜单只要存在失败任务就显示「恢复已有任务 / 重试失败任务」，包括失败步骤标记 `replay_safe: false`、动作可能已生效的 run：选中后 `--resume` 只会返回原失败，用户得不到任何进展。现在这类 run 不再进入恢复 / 重试选择器（`boss wizard --resume <run_id>` 仍按原规则返回核对指引），只在「查看任务状态」里标注「结果未确认，需人工核对」。
- 错误码登记表的 `recovery_action` 文案会随命令演进失效（#540 修过两条指向无效操作的指引），但此前没有测试守住。现在 `tests/test_schema_contract.py` 把恢复指引引用的 `boss ...` 命令与反引号片段中的选项钉在 Click 命令树上：登记表写了不存在的命令或已改名的选项，测试直接失败。
- `boss wizard` 的风控恢复文案与错误码登记表矛盾：`ACCOUNT_RISK` / `ENVIRONMENT_RISK` / `ENVIRONMENT_RISK_LOCKED` 只说「检查账号状态」「等几分钟后再继续」，没有提到本地风险锁需要由用户执行 `boss clean --risk-lock` 才会解除——用户照做会在锁未解除时反复重试同一个必然失败的步骤。现在三条向导文案都给出显式解锁命令。登记表里 `CIRCUIT_BREAKER_OPEN` 的「人工确认平台状态后恢复」和 `WORKFLOW_TIMEOUT` 的「使用返回的 run_id 恢复 workflow」也没有给出入口，现在分别指向 `boss agent run --resume` 与 `boss wizard --resume <run_id>`。
- `boss schema` 中 `crawl` 的子命令声明漏了 `shortlist`：Agent 读 schema 时看不到「把 crawl 结果导入本地候选池」这条能力，只能从 `boss crawl --help` 或文档里发现。现在声明与实际注册的 8 个子命令一致，并新增测试把 description 里「子命令：a/b/c」的声明与 `hr.subcommands` 结构化字段都钉在 Click 命令树上，防止后续增删子命令时再次漂移。
- `boss crawl resume --background` 在后台上拉失败（`OSError`，通常来自磁盘满或数据目录权限）时只提示「执行 boss crawl resume 重试」，同一环境下重试大概率再次失败；`crawl start` 的同类失败已经改为先检查磁盘空间与目录权限。现在两条路径给出同一份恢复指引。
- `boss crawl resume --background` 对已完成的 run 仍会拉起后台进程并报告 `status: queued`：后台进程发现没有更多页和待补详情后立即退出，调用方却以为任务重新排队。现在与 `CrawlService.resume` 的前置条件一致——没有更多页、也没有 `--with-detail` 待补详情时直接返回 `status: completed` 并指向 `boss crawl results`，不拉起进程；请求更多页或仍有待补详情时照常恢复。
- `boss schema` 中 `agent` 把 `agent run` 的 `--resume` / `--dry-run` / `--limit` 写成命令级选项：Agent 按 schema 构造 `boss agent --resume` 会被 Click 直接拒绝。现在与 `crawl` 一样放进 `run` 分组。同时 `chat` 的输出路径键写成 `-o/--output`，与 `digest` / `stats` 的 `-o, --output` 不一致，转 JSON Schema 时会得到错误的参数名；现在统一。新增测试校验 schema 声明的每个选项都落在实际命令或子命令上，防止再次声明不可执行的用法。
- `boss schema` 的选项声明只覆盖了部分命令：`login --cookie-source`、`search --preset`、`export --include-private`、`clean --privacy`、`shortlist add` 的职位字段、`crawl configure/start` 与 `hr greet` 的开聊参数等公开能力没有进入能力真源，Agent 读 schema 时看不到；`hr jobs --page` 又平铺在 `jobs` 分组上，按 schema 构造 `boss hr jobs --page` 会被 Click 拒绝。现在 `boss schema` 递归声明各层子命令选项（`hr jobs list --page` 落到 `jobs.list`），并加测试双向校验：声明的选项必须可执行，可执行的公开选项必须声明（测试专用、历史兼容与内部隐藏项显式豁免）。
- `boss crawl status` 对 `risk_stopped` 的 run 只给出 `checkpoint.resume_command`，而风险锁会先于浏览器构造阻断 `resume`（`raise_if_locked`）：Agent 照 checkpoint 重试只会再次撞锁，用户也看不到解锁入口。现在 `checkpoint.resume_requires_operator_unlock` 标记该前置条件（不把解锁命令写进机器可执行的 `resume_command`），`hints.operator_actions` 给出 `boss clean --risk-lock` 等人工解锁指引与恢复后的显式 resume 命令；TTY 的下一步也不再展示无效的 `crawl stop`。
- `boss schema` 的位置参数只在部分顶层命令声明：`watch add/remove/run`、`preset add/remove`、`config get/set/reset`、`crawl run/start/resume/status/stop/results/shortlist`、`shortlist add/annotate/remove`、`resume show/edit/delete/export/import/clone/diff/link/applications`、`ai analyze-jd/polish/optimize/suggest/reply/interview-prep/chat-coach/resume-optimize/cover-letter`、`agent review approve/reject` 以及 `hr` 的简历、沟通、候选人、职位管理子命令都没有 `args` 声明，Agent 读 schema 时看不到必填的位置参数，照 schema 构造的 argv 会被 Click 拒绝。现在全部补齐（含 `hr jobs` 与 `agent review` 这种不带 `options` 的分组），并新增测试双向校验声明的参数名、顺序与 `required` 都必须与 Click 命令树一致。
- `boss ai cover-letter` 在推荐语生成后给出的后继命令是 `boss ai interview-prep --jd <jd_text> --resume <name>`，而 `interview-prep` 的 JD 是位置参数、没有 `--jd` 选项：Agent 照 `hints.next_actions` 执行会被 Click 以参数非法拒绝。现在改为 `boss ai interview-prep <jd_text> --resume <name>`，并在 `tests/test_ai_cover_letter.py` 钉住该后继命令可执行。
- MCP 的 `boss_mark` 工具把 CLI 的 `--label` 选项写成 `--tag`：任何一次真实调用都会被 Click 以 `NoSuchOption` 拒绝，工具自发布起不可用。原先的测试只断言 `args[0]` 是已注册命令、参数断言又照抄了错误选项，所以一直没发现。现在映射改为 `--label`，并新增测试用 Click 命令树实际解析全部 78 个工具生成的 argv（覆盖未知选项、缺失参数与非法取值），不再只看首个子命令名。
- 另外两处 MCP 工具 schema 与 CLI 不一致：`boss_batch_greet` 的 `limit` 被映射到不存在的 `--limit`（CLI 选项是 `--count`），调用方只要传 `limit` 就会被 Click 拒绝，且不传时会落到 CLI 默认的 10、比工具声明的 5 多一倍；`boss_me` 的 `section` 枚举把 `user` 写成 `info`，客户端按 schema 取第一个值同样被拒绝。现在 `limit` 落到 `--count` 并显式下发默认 5，`section` 枚举改为 `user`；`docs/commands.md` 与 `.en.md` 中 batch-greet 的 `--limit` 同步改为 `--count`，并新增测试逐个校验工具 schema 声明的枚举值都能被对应 CLI 接受。
- MCP 客户端漏传条件必填参数时（如 `boss_wizard` 的 `action=resume/status/stop` 少了 `run_id`、`boss_hr_jobs` 的 `online/offline` 少了 `job_id`），参数映射直接按下标取值抛 `KeyError`，SDK 把它变成 JSON-RPC 的 `Internal server error`：Agent 既看不到缺了哪个参数，也拿不到恢复指引。现在 `call_tool` 捕获该异常并按 CLI 错误信封返回工具错误（`INVALID_PARAM`、缺失参数名、`recoverable` / `recovery_action` 取自错误码登记表）。同时所有工具结果的 `is_error` 改为镜像信封的 `ok`：以前 CLI 返回 `ok:false`（登录过期、风控停止、命中确认门等）时 MCP 客户端看到的是一次成功的工具调用。
- MCP 工具会静默丢弃 inputSchema 之外的参数：`boss_apply` / `boss_greet` 没有 `dry_run` 属性，调用方多传这个参数时它被忽略，命令照常真实提交投递或打招呼——把「预览」变成写操作。现在 `call_tool` 按 `tools/list` 声明的属性校验参数，未知参数直接返回 `INVALID_PARAM` 工具错误并列出参数名，不再触达 CLI。
- 工具描述把可用性说明拼成「可用性: 可用性: roles=...」：`availability_note()` 返回值已带前缀，`build_tool_catalog()` 与 `_decorate_tool_descriptions()` 又各自加了一层。MCP `tools/list` 与 `boss schema --format openai-tools/anthropic-tools/mcp-tools` 导出给 Agent 的描述都带着重复前缀，按前缀解析可用性元数据会出错。现在两处都只拼接一次；兼容入口 `_decorate_tool_descriptions()` 改为幂等，重复调用不再叠加说明。
- `boss config set automation foo` / `boss config set crawl foo` 会把结构化配置项写成字符串并返回 `ok:true`，随后 `boss agent run` / `train` 在 `automation_config_from_dict()` 的 `.get()` 上抛 `AttributeError`，直接以 traceback 退出、stdout 没有任何信封。现在结构化配置项只接受 JSON object（如 `boss config set automation '{"mode": "training"}'`），非法值返回 `INVALID_PARAM` 且不写配置文件；手工编辑 `config.json` 造成该键不是对象时，`agent run` / `train` 按信封返回 `INVALID_PARAM` 并给出 `boss config reset automation` 修复入口。
- `boss config set crawl` 只校验 JSON 对象形状：`{"max_requests": 0}`、`{"cdp_port": 70000}`、`{"chrome_path": 5}` 都会返回 `ok:true` 并写进 `config.json`，之后每次 `crawl run` / `start` 或向导采集都报 `INVALID_PARAM`。现在 `config set crawl` 与采集路径共用 `crawl_int_setting` / `crawl_cdp_port` / `crawl_chrome_path` 校验，非法值在写入时即被拒绝且不落盘。
- `boss wizard` 的采集动作不校验 `hook_profile` 输入：向导没有 `hook-dir` 输入，`screenshot-full`（或任意拼写错误）会在 `create_and_run` 内部抛 `ValueError`，被 runner 兜底成可重试的 `NETWORK_ERROR`，恢复提示还让 Agent 重跑同一个 run。现在向导只接受 `none`，其它值返回 `INVALID_PARAM` 并指向带 `--hook-dir` 的 `boss crawl run`；`crawl_resume` 的 `pages` 输入同样按 ≥ 1 的整数校验，不再静默忽略 `0` 或把非数字落成 `NETWORK_ERROR`。
- `boss wizard` 的采集动作仍按 `int(cfg.get(...) or default)` 读取 `crawl` 配置：`boss crawl configure --max-retries 0` 在向导或 `boss wizard --input-json` 的 `crawl_start` 里被改回 `1`，与 `crawl run` / `start` 不一致；手工编辑写入错误类型或越界值时，`ValueError` / `TypeError` 落进 runner 兜底，workflow 报成可重试的 `NETWORK_ERROR`，重试永远修不好配置。现在向导与 CLI 共用同一组配置校验（`max_retries` 保留显式 `0`，其余值域同 `run` / `start`），非法配置返回 `INVALID_PARAM` 并指向 `boss config reset crawl`；`pages` 输入同样要求 ≥ 1 的整数。
- `boss crawl configure --max-retries 0` 会把 `0` 写进配置，但 `crawl run` / `start` 用 `int(cfg.get(...) or 1)` 读取，把 `0` 当成缺省值悄悄改回 `1`——用户显式要求的「不重试」从未生效；`max_requests` 等写入 `0` 时同样被默认值覆盖。手工或 `config set crawl` 写入错误类型 / 越界值时，`TypeError` 落在通用兜底，返回 `INTERNAL_ERROR` 并建议升级 CLI。现在整数配置按类型与范围校验：`max_retries` 保留显式 `0`，`max_requests` / `max_details` / `max_seconds` 要求 ≥ 1，`cdp_port` 限制在 1-65535，`chrome_path` 必须是字符串或 `null`；非法值返回 `INVALID_PARAM` 并指出配置项。
- `boss config set automation '{"mode": "nonsense"}'` 通过了对象形状校验照样落盘，随后 `boss agent run` / `train` 在 `AutomationMode(...)` 处抛 `ValueError` 并以 traceback 退出；非数值阈值、不可迭代的 `allowed_actions` 等也走同一条路径。现在 `config set` 用正式解析器 `automation_config_from_dict()` 校验 `automation` 内容，非法值返回 `INVALID_PARAM` 且不写文件；手工写入的非法内容由 `agent run` / `train` 按信封返回 `INVALID_PARAM`，同样给出 `boss config reset automation` 修复入口。

## [3.2.0] - 2026-11-01

**升级须知**

- `--industry` 改用平台行业码，14 个对不上平台的旧标签已移除：保存过这些标签的预设和 watch 会返回 `INVALID_PARAM`，报错会给出相近的可选值；也可以直接传数字行业码，或用 `--url`。
- `--experience`、`--salary` 的码已按网页筛选器校正，同样的标签现在搜到的是正确的档位，结果会和以前不同。预设和 watch 存的是标签，升级后自动使用新码。
- CLI 缺陷与平台数据结构异常改报 `INTERNAL_ERROR`（不可恢复），不再是可重试的 `NETWORK_ERROR`。按错误码决定是否重试的 Agent 需要相应调整。
- 依赖下限改为 `browser-cookie3>=0.20.0`，`pip install -U boss-agent-cli` 会一并升级它。

### Added
- `--city` 新增「全国」（平台的不限城市码 `100010000`）和嘉兴、徐州、香港，共 44 个城市。「全国」的码在 jackwener/boss-cli 与 ThisZhouS/jobwork 中一致，后者在真实搜索请求里用过；另外三个取自 jackwener/boss-cli，其城市表与本项目共有的 38 项完全一致。本地预筛与匹配评分把「全国」当作没有城市条件：此前就算平台支持，本地预筛也会因为职位城市名不含「全国」而把结果全部排除，评分里也会给每个职位记一条「城市不匹配」。
- `boss login --cookie-source <浏览器>` 没提取到时，stderr 会说明是读取失败（附异常类型与原因）还是该浏览器没有登录 Cookie。拿不到解密密钥时按平台说明原因（macOS 钥匙串未授权、Windows 上新版 Chrome / Edge 的 App-Bound 加密、Linux 密钥环未解锁），并提示改用 `--cdp` 或 `--curl-file`。自动探测全部落空时，只汇总无法解密的浏览器。参考 jackwener/boss-cli 的提取诊断及其 #9、#15。

### Changed
- 命令兜底不再把 `TypeError` / `AttributeError` / `KeyError` / `IndexError` 报成 `NETWORK_ERROR`（`recoverable: true`、建议「重试」）。这类错误来自 CLI 缺陷或平台返回了未预期的数据结构，同样的输入必然得到同样的结果；重试的若是平台请求，还会白白消耗请求预算。现在归为新错误码 `INTERNAL_ERROR`（不可恢复，`error.message` 带异常类型），`--exit-codes sysexits` 下退出码为 70（EX_SOFTWARE）。命令装饰器、wizard 运行器与 `me` 的兜底都已区分；其他异常仍为 `NETWORK_ERROR`，行为不变。
- Agent 集成文档（`docs/agent-quickstart.md`、Claude Code、Codex、Cursor、Windsurf、Shell Agent）默认改为只读流程：搜岗、看详情、整理本地候选池、导出与基于已确认事实写草稿。`boss apply` / `boss greet` / `boss batch-greet` 等平台写操作，只在用户当前对话点名具体岗位、并在操作前再次确认时执行一次；不建外发队列、不无人值守批量、不复用同一话术、不重试结果不明的写操作；遇到风控、验证页或限流时保存 checkpoint 并停止。`tests/test_agent_host_examples.py` 断言各宿主文档都写明这条边界。(#480)

### Fixed
- `--experience` 的经验码整体错位一档：`1年以内` 原发 `101`（平台含义为「经验不限」），`1-3年` 原发 `103`（实为「1 年以内」），依此类推。现按 zhipin.com 搜索页 `experience=` 参数改为 `1年以内=103` … `10年以上=107`，`应届=102`，并新增 `在校生=108`、`经验不限=101`。参考 jackwener/boss-cli#6。
- 已装 browser-cookie3 0.20.0 以前版本的环境里，`boss login` 与 `boss doctor --probe-cookies` 的 Cookie 提取直接抛 `AttributeError`：3.0.1 起加入的 Arc 加载器在 0.20.0 才出现，旧版缺失，登录因此走不到 CDP / 扫码降级。`pip install -U boss-agent-cli` 默认不升级已满足旧下限 `>=0.16.2` 的依赖，老用户升级后会命中这个问题。依赖下限改为 `>=0.20.0`；加载器改为按名字获取，缺失的浏览器在自动检测中跳过，显式指定时提示升级。
- macOS 上 `boss login` 的自动探测不再为没有登录记录的 Chromium 系浏览器逐个弹出钥匙串授权框。browser-cookie3 在找 Cookie 文件之前就读钥匙串，而且不设超时，装了几个 Chromium 浏览器就可能弹几次。现在先以只读方式查看 Cookie 库中明文的域名和名称两列，确认没有 `zhipin.com` 的 `wt2` 时跳过该浏览器，并在 stderr 说明跳过了哪些；找不到库或无法读取时保持原行为。参考 jackwener/boss-cli#35。
- `--salary` 的低三档错位一档：`3K以下` 原发 `401`（不是网页档位），`3-5K` 原发 `402`（实为「3K以下」），`5-10K` 原发 `403`（实为「3-5K」），`10-15K` 原发 `404`（实为「5-10K」，且网页没有 10-15K 档）。现按网页筛选器改为 `3K以下=402`、`3-5K=403`、`5-10K=404`，`10-20K=405`、`20-50K=406`、`50K以上=407` 不变；`10-15K` 保留给旧预设，按包含它的 10-20K 档请求，本地预筛仍按 10K 下限过滤。`--education` 补上 `初中及以下=209`、`中专/中技=208`、`高中=206`：这三档此前在本地阈值比较里存在，却不能作为筛选条件。多选时同码标签只发一次。依据为公开爬虫项目 ThisZhouS/jobwork 从筛选器 `ka` 属性取值并做过在线探测的码表，学历码与 jackwener/boss-cli 一致。
- `--industry` 的行业码表大面积错误：25 个标签里只有「互联网」对得上平台，例如「人工智能」发出的 `100901` 实为「机械设备/机电/重工」，「金融」发出的 `100101` 实为「广告/公关/会展」，整百的类目码则与标签整体错开一个大类。现改为平台行业码：职位数据里 `industry` 与 `brandIndustry` 成对出现的 96 条取值，`industry=` 搜索参数接受同一套码，取自 ThisZhouS/jobwork 从真实职位列表整理并在线探测过的结果。无法对应到这套码的 14 个旧标签（金融、软件/信息服务、区块链、证券/基金、教育培训、房地产、汽车、物流/运输、广告/传媒、消费品、制造业、能源/环保、政府/非营利、农业）已移除，用到它们的预设与 watch 会返回 `INVALID_PARAM`，而不是继续静默搜出别的行业；仍可直接传数字行业码或用 `--url`。`search` / `batch-greet` 在 schema 中的 `--industry` 可选值改为直接取自码表；`batch-greet` / `preset` / `watch` 的 help 不再展开整张表。
- 未知的筛选标签（行业、薪资、经验等）报错时附上字面最接近的可选值，例如 `未知行业类型: 汽车，相近的可选值: 汽车零部件、汽车经销商…`。
- `boss login` 在 CDP 不可用时不再先走「QR 纯 httpx」通道。该通道只在终端打印二维码 ID，从不展示可扫描的二维码，任何一次登录都不可能经由它完成，却会先向登录服务发出 randkey / getqrcode 请求；过期提示里提到的 `boss login --qr` 选项也并不存在。纯 httpx 扫码即使走通，也拿不到页面 JS 生成的 `__zp_stoken__`（参考 jackwener/boss-cli#2、#16）。现在 CDP 不可用时直接打开浏览器扫码；`auth/qr_login.py` 随之删除，YAML 中的 QR 端点保留为声明。
- 按文档配置 `boss ai config --provider openai --model gpt-5` 后，`boss ai` 的请求全部被拒（HTTP 400）：请求体固定带 `max_tokens` 和 `temperature: 0.7`，而 OpenAI 推理模型（o 系列、gpt-5 及之后）要求 `max_completion_tokens`，temperature 只接受默认值。现在按模型名切换：这些模型改发 `max_completion_tokens`、不发 `temperature`；Kimi K2.5 及之后的型号（temperature 由平台固定）也不再发送 `temperature`；经 OpenRouter 的 `vendor/model` 形式仍发 `max_tokens`。HTTP 错误的 `error.message` 带上服务端返回的原因（遮掉疑似密钥、截断到 200 字），`recovery_action` 按 401/403、400/404/422、429、5xx 给出对应的下一步，不再一律提示检查网络和密钥。(#499)
- 模型返回 `content: null` 时（例如 vLLM 开 `--reasoning-parser` 后思考阶段就用完输出上限），`boss ai` 各命令与 `boss ai local smoke` 抛出 traceback、stdout 没有 JSON 信封；招聘自动化的 `run_automation_cycle` 被 `AttributeError` 打断，排在后面的会话都没有处理。现在 `AIService.chat` 只返回字符串：正文为 null，或因 `finish_reason=length` 为空时抛 `AIServiceError`，命令输出 `AI_API_ERROR` 信封，自动化按「本地 AI 不可用」回退模板、在原因里写明，并继续处理后面的会话；返回 200 但响应体不是 JSON 时同样输出 `AI_API_ERROR`。`ai local smoke`、自动化回复与 wizard 的 AI 步骤改为使用 `boss ai config` 配置的输出上限（smoke 原先写死 32、自动化写死 512，wizard 还忽略了 temperature 配置），「调大 max_tokens」的建议才对它们生效。(#500)
- macOS 上只在非默认浏览器配置（如 Chrome 的 Profile 1）里登录 BOSS 时，Cookie 提取永远读不到：browser-cookie3 只读每个浏览器的第一个配置（通常是 Default）。#494 的预检已经确认其他配置有登录记录，loader 却仍读 Default，`--cookie-source` 还会误报「没有登录 Cookie」。现在把含登录记录的 Cookie 库直接交给 loader：按 browser-cookie3 的顺序（稳定版先于测试版，Default 先于 Profile N）取第一个，Default 有登录记录时行为不变；钥匙串仍只读一次，成功时 stderr 注明读的是哪个配置。参考 jackwener/boss-cli#12、#14。
- 平台响应里的字段为显式 null 时（如 `"jobList": null`、`"welfareList": null`、`"bossInfo": null`），多个命令会崩溃，并被兜底成提示「重试」的 `NETWORK_ERROR`：`search`（含 `--welfare`、`--with-score`）、`recommend`、`history`、`interviews` 在列表为 null 时报错；`detail` 遇到 `bossInfo` 或 `brandComInfo` 为 null 时，会吞掉快速通道的解析异常、改走浏览器通道再请求一次平台，最后误报 `JOB_NOT_FOUND`。现在平台数据在边界处统一归一化（新增 `api.models.field_text` / `field_list` / `field_dict`）：null 列表当作空结果，null 文本字段输出空串，输出信封里的职位字段保持字符串 / 列表类型。

## [3.1.0] - 2026-10-30

### Added
- 新增 `--browser-source httpx-only`：通道白名单为空，结构上不打开任何浏览器；`search` / `recommend` / `detail` / `friend_list` / `chat_history` 全部走 httpx，需要页面的能力返回 `BROWSER_DISABLED`。线上稳定性尚未证实，只作为显式选择，不做默认也不做自动降级；失败时 `hints.operator_actions` 提示回退 `auto`。
- 新增 `api/proxy.py` 的 `resolve_proxy()`：httpx 与每个 Chromium 启动点共用同一出口。此前 Chromium 完全忽略 `HTTPS_PROXY`/`ALL_PROXY`，登录走真实 IP 而请求走代理 IP。
- 新增 `auth/page_identity.py`：从真实页面请求采集 `token` / `zp_token` 并随会话加密落盘，httpx 与浏览器两条通道回放同一组值。
- 新增 `api/egress.py` 与 `BOSS_EGRESS_PROBE_URL`：出口地区与声明的语言/时区矛盾时 `CrawlService.create()` 拒绝开跑；未配置时不发任何探测流量，判为 `unknown` 且不阻断。
- `boss doctor` 新增 `request_delay` 检查：请求间隔下限低于 30 秒时 warn 并给出建议值。
- 新增三份研究文档：`docs/research/zhipin-request-identity-2026-10-29.md`（147 条业务请求的逐 header 规则）、`zhipin-cdp-free-read-path-2026-10-29.md`（只读链路脱离 CDP 的可行性、code 37 受控对照与恢复步骤）、`upstream-antidetection-evaluation-2026-10-29.md`（ruyijs / Safarier 的评估与不集成理由）。

- 搜索结果新增 `job_url`：由 `encryptJobId` 拼出 `https://www.zhipin.com/job_detail/<id>.html`，不额外请求平台。导出脱敏时与 `job_id` 一起替换为 `[REDACTED]`。
- 新增 `boss hr whoami` / MCP `boss_hr_whoami`：只读 `getUserInfo`，用 `identity` 判断当前登录态是否在招聘者侧。`identity` 为 1 时 `ready` 为 true；账号资料字段 `recruit` 只以 `profile_recruit` 回显，不参与判断。双身份账号停在求职者侧时返回 `ready: false` 和官方页面切换指引，命令本身不切换身份。
- 新增跨实例、CLI/MCP 子进程与 crawl 的本地共享请求预算：默认单个主动操作持锁至响应分类完成，滑动 60 秒最多 20 次，总等待上限 30 秒；`request_budget_dir` 可显式分组，组内采用统一限制并共享风险阻断。HTTP 429 / code 9 和有效 `Retry-After` 持久化冷却，进程退出只释放执行锁；预算超限返回 `REQUEST_BUDGET_EXHAUSTED`，保留 checkpoint 后显式恢复。主动操作计量不覆盖浏览器自主资源、轮询、自动重定向或认证内部的每条网络请求，不按公网 IP 自动合并任务。
- 招聘者 `agent run --resume` / MCP `boss_agent_run.resume` 提供显式恢复入口，保留确认结果与队列，普通运行不清除停止状态。
- 招聘者 `hr chat` 的 hints 新增 `total_unread`：聊天页显示的未读总数，读自页面 geek-list 组件的 `uncountTab$` 第 1 项（实测与页面 UI 一致，覆盖全部会话，不限于已加载的部分）；同时给出原始分项计数 `unread_by_tab` 和来源 `total_unread_source`（`page.uncountTab$[1]`）。只读页面内存，不额外发请求；页面不可用（非 CDP 或没打开聊天页）时三项均为 `null`（Refs #456）。
- `boss search` 新增 `--active <档位>`，按 HR 活跃度筛选职位（#442）。档位 `online` / `today` / `3d` / `week` / `2w` / `month` / `half-year`，也认 `3day`、`3日` 等写法；平台文案按「在线/刚刚活跃 > 今日活跃 > 3日内活跃 > 本周活跃 > 2周内活跃 > 本月活跃 > 近半年活跃 > 半年前活跃」比较。列表带 `activeTimeDesc` 或 `bossOnline` 为在线时直接判断，否则查 job_card；和 `--welfare` 同用时一次详情请求同时判断两项。启用后跳过搜索缓存，无法识别的文案被排除并写进 `hints.active_filter`。职位结果新增 `boss_active_desc` 字段（`boss_active` 不变，搜索缓存 schema 升到 3），`preset add`、MCP `boss_search` / `boss_preset_add` 与向导求职流程同步支持。
- 替换仓库 README 与项目站点的展示动画为 48 秒极光发布会成片，提供 MP4、轻量动图、新封面和可交互源工程；站点播放期间保持当前画面，切换到终端演示时暂停视频。
- 新增维护者发布验证脚本：默认离线核对版本、wheel/sdist 元数据与分发文件，显式启用后可在临时环境全新解析依赖、验证 CLI/MCP，或从 PyPI 安装指定版本复验；输出结构化步骤结果，不执行发布或真实平台请求。
- 新增仓库根 `llms.txt`：面向 LLM 的单页能力入口（能力真源、信封契约、运行边界与链接），指向 `boss schema` 与关键文档；不引入会与 schema 漂移的静态工具清单。
- `boss login` 的浏览器 Cookie 提取从 6 个扩展到 10 个：新增 Vivaldi / Arc / LibreWolf / Safari，自动检测优先级不变、新浏览器作为后备；macOS Safari 需要完全磁盘访问，读取失败时静默尝试下一个浏览器。
- 新增 `docs/research/opencli-benchmark-2026-10-15.md`（含英文对照）：对标 OpenCLI / boss-cli / opencli-mcp / xhs-cli 的差距矩阵、已确认缺陷（文档漂移）与分批改进方案；分层规则复核更正见方案 §3.3。
- 新增全局 `--exit-codes {envelope,sysexits}`（默认 envelope，0/1 契约不变）：sysexits 模式按错误类别返回语义化退出码（77 认证；75 限流/预算/超时；78 参数/配置；69 风控/平台不支持；其余 1），usage error 通过解析前预扫描同一选项；stdout 信封不受影响。

### Changed
- **行为变更：默认请求间隔 `--delay` 从 `1.5-3.0` 改为 `30-60`**（`config.DEFAULT_REQUEST_DELAY`，原先 10 处字面量收敛为一个常量）。受控对照：15 秒间隔连续 3 次 `search` 会在第 3 次命中 code 37「您的环境存在异常」，30 秒起两轮共 6/6 通过。单条只读命令只发一次请求、首次调用不等待（`boss status` 仍约 0.3 秒）；受影响的是分页、批量详情与 crawl。该下限来自本机观测，不是平台公布阈值。
- 请求身份对齐真实页面抓包：`User-Agent`、`sec-ch-ua*`、`Accept-Language`、`Accept-Encoding`、`priority`，补上页面自带的 `token`、`zp_token`、`traceid` 与 `X-Requested-With`；`sec-ch-ua-platform` 改由 User-Agent 推导（原先按宿主操作系统，Windows 上会发出「UA 说 macOS、Client Hint 说 Windows」的矛盾组合）。
- `search` 改为真机的 `POST` + `application/x-www-form-urlencoded` 表单体，字段集合与顺序按抓包固定，缺省筛选项发空串而非省略；`recommend` 保持 GET。
- httpx 通道改用 HTTP/2（依赖改为 `httpx[socks,http2]`，新增 `brotli`、`zstandard`），`Accept-Encoding` 的声明与可解码能力保持一致。
- Chromium 启动加入 `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` 与 `--enforce-webrtc-ip-permission-check`，避免 WebRTC 用真实地址发送 UDP。

- `boss doctor` 默认不再读取浏览器 Cookie，避免 macOS 钥匙串授权弹窗。需要核对浏览器登录态时使用 `boss doctor --probe-cookies`。`boss status` 与 `boss search` 本来就只看本地凭据。
- `hr jobs list` 改为读取职位管理页的 `data/list` 接口，并用 `--page` 分页。MCP `boss_hr_jobs` 的 `page` 只在 list 时传给该分页。`boss schema` 同步登记该选项。
- 招聘者 `chat_geek_info` 改为沟通页当前参数：`uid`、`securityId`，以及调用方提供时的 `geekSource`。没有命令调用这个方法。
- `boss doctor --live-probe` 复用 `hr whoami` 的身份判断：`identity` 为 0 时先记下求职者侧登录态，不继续请求招聘者接口；`identity` 为 1 时才做招聘者只读探测。命令不切换身份。
- MCP、OpenAI 和 Anthropic 工具格式统一使用不依赖 MCP SDK 的共享目录，工具导出由旧的不完整命令映射改为与运行时一致的 77 个工具；原生 schema 保留 39 个顶层命令。保留工具参数结构的脱敏例外仅限 schema 明确声明的内置参数路径，真实业务数据和敏感默认值/示例仍脱敏。
- 恢复面向 PR 的 `CI` 工作流（参照 Wholiver/metis 的 Test 工作流）：`pull_request` 与推送 `master` 时运行 ruff、mypy、pre-commit、CLI/schema 冒烟和 Python 3.10 / 3.12 / 3.14 测试矩阵；只读权限、同一 PR 新推送取消旧运行、不使用任何 secret，外部贡献者的 fork PR 可直接运行。pre-commit 的 end-of-file-fixer 排除 star-history 自动生成的 SVG，并补上 `wizard/renderer.py` 结尾换行，使 `pre-commit run --all-files` 在 master 上可以通过；贡献指南同步更新 CI 说明，去掉已失效的 Codecov 描述。
- 同步项目站点中英文正文与 `llms.txt` 的 v3.0.0 版本、命令与 MCP 数量、双角色工作流及平台风险停止／显式恢复说明，移除过时的模式级阻断宣传；修正发布会源工程的本地预览服务目录。
- 文档对齐：`CLAUDE.md` 与 `docs/maintainer/branch-protection.md` 同步 2026-10-09 已恢复的 PR CI（lint + Python 3.10 / 3.12 / 3.14 测试矩阵；required status checks 仍未配置）；`pyproject.toml` 注释改指本地 `scripts/verify_release.py --fresh-install`；中英文 README 与 `CLAUDE.md` 的测试计数更新为 2900+。

### Fixed
- 复用自动探测到的 CDP 浏览器前，先比对 `wt2` 与本地凭据：不同账号时拒绝复用其会话，改用本地凭据自建浏览器，避免拿 A 账号的登录态执行 B 账号的请求（实测会立刻得到 code 37）。
- `security_id` 可以合法以 `-` 开头（实测 328 字符的 base64url 取值），此前会被 Click 当成未知选项，`boss detail` 直接传时报 `No such option`；现在为未声明的横杠开头取值补 `--`。
- 候选者侧 referer 由 `/web/geek/job` 更正为真实页面 `/web/geek/jobs`（`web_pages` 与 5 处 referer 一并修正）。
- 抓包脚本判定目标页改用 host 解析，不再用子串匹配（CodeQL `py/incomplete-url-substring-sanitization`）。

- `boss login` 的中间提示不再宣称「检测到登录成功」：扫码确认到 `wt2` 出现只是**识别**，之后还要校验页面、等 3 秒、回首页提取，任一步命中官方验证页都会停下。现在统一为「已识别到扫码确认，正在校验并保存凭证…」，真正的「登录成功（…）」仍由命令层在凭据落盘后输出（#488）。
- `security_page` / 访问受限页的错误详情补上 `risk_source` 命中的页面路径（`error.details.path`）：此前只有 `risk_source`，用户无法区分 `verify.html`（安全校验）与 `security.html`（风险页），维护者也拿不到定位依据（#488）。
- `boss search` 等浏览器通道在冷启动时只对首页做一次 15 秒 `page.goto`，首页卡住就以 `NETWORK_ERROR` 原样抛出 `Page.goto Timeout 15000ms`（Discussion #250）。现在与登录预热对齐：超时后若不是安全验证页，就跳到 `about:blank` 再试一次；仍然失败则返回 `BROWSER_NAV_TIMEOUT`，说明要先打开官网首页或刷新登录态。`--url` 仍只解析筛选条件，搜索请求之前还是要先打开首页。把 `--json` 等全局选项写在子命令后面时，`INVALID_PARAM` 会提示该选项必须写在子命令之前。
- 修正节流完成时间覆盖后续预约、高斯采样突破间隔下限及 crawl 分类别预算边界；使用单调时钟保持预约，crawl 首页种子导航、分页、页面 fetch 和 CDP fallback 分别申请预算。等待及发送前检查停止、风险和截止时间，停止信号不触发重试或通道降级；写后确认受阻保留 `ACTION_UNCONFIRMED` 和 `confirmation_stop`，失败下载仍记节流，429 下载不读正文或创建文件。crawl 在中断或清理失败前保存进度，清理异常保留原风险或预算停止状态。
- 校验 `--delay`、延迟范围和共享预算配置的有限数值、正整数及绝对路径约束；非法配置不落盘，保留配置修复入口。预算组有效风险状态进入 `status` / `doctor`，显式清风险锁保留预算及冷却，风险写盘失败仍阻断当前实例。
- 浏览器风险识别续查：聊天页 `/wapi/` 的 HTTP 403 在响应头事件立即停止，不等正文；错误/缺失 Content-Type 的受限 HTML 仍生成脱敏分类元数据。业务 JSON 先解析原码，避免成功响应中的 HTML 说明触发误锁；纯文本业务响应同样检查风险，外站和普通静态资源不误锁。
- 补齐 code 31（IP/环境限制）、32（账号限制）及官方 `403.html` 访问受限页：API、浏览器、认证与 crawl 在刷新、重登清 Cookie 或继续导航前停止并保存原码/未知状态；隐藏文案不用于猜测当前原因。当前网页 code 31 已只读观察，32 仅有静态与离线证据；页面展示时间和移动端可用均不触发自动解锁。
- 风险锁改为显式本地恢复：code 35/36、环境类或含糊的 code 37、HTTP 403 和官方 verify/security 安全页在候选、招聘者与认证通道锁存原始风险原因及可用业务码；httpx、headless、裸 CDP、令牌变化和历史 `--ignore-risk-lock` 均不能绕过已记录锁。用户确认目标业务页恢复后执行 `boss clean --risk-lock` 再显式恢复任务，清理不代表平台解封，再次风险立即重锁。code 37 刷新要求凭据与明确过期语义同时存在，风险/安全验证优先；code 35/security 路径与含糊 token 分类为静态证据和离线回归，未声称真实账号线上解除已验证。
- 独立 crawl 与 API 共用同 `data_dir` 风险锁，在浏览器构造、连接和后台启动前阻断已锁任务；新风险持久化原始原因/业务码，未知码保持 `null`，code 38 明确属于身份资料流程。风险 outcome、已采集队列和 checkpoint 保留，人工恢复放在 `operator_actions`，resume 不清锁；本地结果与已完成 run 导入仍可用。
- 候选人搜索、推荐、批量沟通与 watch 在同次结果/分页流程中按非空职位 ID 去重，保留首条完整 `security_id/lid`，不按职位 ID 合并不同账号的历史记录。候选池可选 `lid` 经兼容 SQLite 扩展保存，CLI/MCP/schema、crawl/favorites 导入同步；favorites 更新访问上下文时不拼接旧 `lid`。
- `recommend` 保留缺失/不明确的分页标记为 `has_more=null`，只有平台明确返回布尔 `true` 才给出下一页提示，避免上层无依据继续请求；兼容 `total` 仍是本页条数，新增 `total_kind` 明确其范围。
- 风险响应在刷新、登录降级或采集 fallback 前停止：原始 code 36、环境类/不明确 code 37、HTTP 403 和安全页共享错误契约；Cookie、二维码各阶段、CDP 探测/刷新、招聘者动作前后回读与自动化读响应均原样传播风险，保存可用 checkpoint，不继续请求或更换通道。
- 端点显式声明读写与可重放性质，GET 变更和未知操作不再按方法推断可重试；fetch/响应体与 CDP renderer 加有界截止。浏览器上下文销毁、httpx 断链/损坏响应等未知写结果保留 `ACTION_UNCONFIRMED`，候选人与招聘者均不提示自动重发；招聘者准确绑定目标好友，不强制开启禁用编辑器，不以 outgoing WS 或未知手机号动作编号确认成功。
- 自动化使用独立原子停止标志并在动作边界重新读取，逐项保存已确认防重结果，风险和中断保留进度；wizard 成功结果先落盘再处理超时，旧更新不能覆盖 stop。MCP handler 改用异步子进程，长运行期间控制工具仍可响应，取消/超时有界中断并回收进程，保留副作用未知标记与 checkpoint 宽限。
- 搜索缓存命中重建本次排序的编号索引，`lid` 随编号、show/detail/job-card 传递，crawl 候选池仅从同 run/job/security 的既有本机缓存恢复 `lid`，兼容旧数据。福利/活跃度筛选先跳过已沟通过职位，有限需求按需串行并在结果足够时停止；apply 原始风险分支不再提示自动重试。
- 招聘者 `hr chat` 打开聊天页后页面来源仍是 0 条、`unread` 全为 `null`（#456 后续）。实测聊天页（Vue 2 + vue-rx）的会话列表挂在 geek-list 组件实例的 `list$` 和父组件 chat 的 `allList$` 订阅上（`$observables`），不在原快照扫描的 `$data` / computed / `$store` 里；列表项自带 `newMsgCount`、`lastText`、`lastTS`、`lastMsgStatus`、`lastIsSelf`。快照脚本现在先读 vue-rx 订阅（仍只读页面内存、字段白名单），页面已加载的会话直接给出未读数、最后一条消息和时间，没覆盖到的会话再走 `userLastMsg`；`hints.summary_sources.page_snapshot` 给出数据来源路径、已加载条数和页面是否还有更多（`has_more`）。页面只保存已滚动加载的会话，未加载的会话拿不到未读数，仍为 `null`。另外修正快照脚本从未真正执行的问题：`evaluate_js` 不带参数时只把函数表达式求值成函数对象而不调用，快照因此一直为空，现在带参数调用。
- 招聘者 `hr chat` 的最近消息补全实测全部失败（`last_messages_error: 未知的非法参数`，356 条都退回 `friend_list`，#456 后续）。对照前端代码和 3 个 friendId 的实测：`userLastMsg` 参数本身没错（表单 `src=0&friendIds=a,b,c`），问题是把整页 356 个 friendId 一次塞进去，前端自己每批不超过 100 个，并按 friendSource 分组（店长直聘好友 `src=1`）。现在按 friendSource 分组、每批 50 个串行请求，默认只补前 100 个会话（最多 2 次请求），`hr chat --summary-limit`（0–300，0=不请求）可调，任一批失败立即停止并在 `hints.summary_sources` 记录 `last_messages_error` / `last_messages_error_code` / `last_messages_requests` / `last_messages_skipped`；`hr last-messages` 同样分批并新增 `--limit`。按实测返回形状（`uid`、`lastTS`、`lastMsgInfo.showText/fromId/status/msgTime`）解析：时间优先用毫秒时间戳而不是「12:00」展示串，候选人发来的最后一条消息不再显示成「发送中」（`msg_status` 只对自己发出的消息有值）。该接口不返回未读数，`unread` 仍只来自聊天页快照。
- 风控锁（#453）补齐聊天页动作和登录覆盖。招聘者 `hr reply` / `hr request-resume` / `hr resume --exchange` 在执行前仅检查本地锁，不为核对令牌建立 CDP 连接；未解除时不执行页面动作。页面的 code 35/36/37 或安全页会锁存并保留原始原因，`error.details.page_risk` 保留接口路径和码。登录、导入和刷新也在平台请求前检查同一锁；历史 `--ignore-risk-lock` 参数保留兼容但不放行，用户确认官方目标业务页恢复后须执行 `boss clean --risk-lock` 显式清除本地阻断。
- 招聘者 `hr chat` 的未读数、最后一条消息、时间不再全是 `null` / `未知` / `-`。实测沟通列表接口（filterByLabel）只返回 friendId、姓名、updateTime 等基础字段，原实现的补全又没对上数据。现在 CDP 模式下先读聊天页 geek-list 组件里已加载的会话列表（只读页面内存、不发请求，字段按白名单挑选）；页面没覆盖到的会话才调用一次 `userLastMsg`（单次、串行），并兼容消息对象（按 from/to uid 对应候选人）和 `{friendId: 消息}` 映射等返回形状；都拿不到时字段为 `null`，`last_time` 退回 `updateTime` 并标注 `last_time_source`。每条记录新增 `summary_source`，`hints.summary_sources` 给出来源统计和最近消息接口错误。`hr last-messages` 同步改为未知字段输出 `null`。
- 招聘者 `hr resume --exchange` 在换电话/换微信按钮还没解锁时不再走一遍页面动作、最后报含糊的 `ACTION_UNCONFIRMED`。实测平台要求招聘者先在会话里发过消息才解锁这两个按钮（求简历不受限），未解锁时 `handleExChange` 照常返回但什么都没发。现在交换前先检查按钮组件的禁用状态（`disabled` / `aria-disabled` 属性、`disabled`/`gray` 等 class、根元素 `pointer-events: none`、组件 props/data 的禁用标记）；页面判断不了时，用动作前读到的完整聊天记录里是否有自己发出的消息兜底。确定未解锁时不调用 `handleExChange`，返回新错误码 `EXCHANGE_NOT_AVAILABLE`（`recoverable=true`，提示先 `boss hr reply` 或 `boss hr request-resume` 后再试），`error.details` 带 `availability`、`availability_signals`、`history_self_message`；信号拿不准时不拦截。按钮看起来可用但聊天记录里没有自己的消息时仍按原流程执行，若最终未确认，`ACTION_UNCONFIRMED` 的提示会点出按钮可能尚未解锁。
- 招聘者 `hr resume --exchange --type wechat` 实际已发出换微信请求却返回 `ACTION_UNCONFIRMED`（#443 后续）。实测这类请求在聊天记录里是不带文案的动作卡片（`body.type=4`、`action.aid=32`），原判定只认文案。现在聊天记录和 WS 帧都按动作卡片的 aid 判定：aid 32 = 换微信，aid 37 = 求附件简历；换手机号的 aid 尚无样本，暂时接受除这两个之外任意 aid 的卡片。卡片必须是自己发出、动作后新出现（mid 不在动作前快照里，且时间不早于动作开始 60 秒以上）。没有 WS 证据时聊天记录回读改为轮询（首读加 3 次重试，共约 6 秒），`error.details` 新增 `history_attempts`；换微信时出现的 aid 37 卡片也计入 `resume_request_detected`。
- CDP 模式不再用 httpx 带着浏览器登录态访问平台（#452 / #451 后续）。实测 `--cdp-url` 下 `job_card` 先走 httpx（Cookie 从 Chrome 拷出、`__zp_stoken__` 作查询参数），与浏览器通道并发，Chrome 里的 stoken 随即被判异常，之后 CLI 的浏览器请求一律 code 37，而手动浏览因页面重新校验换了 stoken 仍正常。现在配了 `--cdp-url`、显式 `--browser-source existing-browser` / `stored-cookie`，或本进程已接上 CDP Chrome 时，`BossClient` / `BossRecruiterClient` 的所有平台读写都只走浏览器通道（`job_card` 不再 httpx 优先，`detail`、向导详情、收藏、投递记录、聊天等读取一并改走浏览器），没有浏览器实现的操作（招聘者附件下载）返回 `NOT_SUPPORTED`；`search` / `watch` / 向导的福利、活跃度补详情在 CDP 下改为串行并按 `CrawlBudget` 间隔；`crawl` 的列表兜底和详情改为在采集页签内同源请求，去掉 httpx 回退。风险锁沿用历史 `cdp_risk_lock` 名称，摘要仅作诊断；锁存后所有 BOSS 请求在本地拒绝，账号风险保留 `ACCOUNT_RISK`，环境锁保留 `ENVIRONMENT_RISK_LOCKED`。`boss doctor` / `boss status` 展示本地状态，只有用户确认目标业务页恢复并执行 `boss clean --risk-lock` 后才能显式继续；令牌变化不会自动解锁，HTTP/headless 也不能绕过已有锁。`detail` 命中风控后不再换通道补请求。
- 收紧 `boss search --active` 的请求量（#451 回归，#442）。实测列表项不带 `activeTimeDesc`，原实现会固定翻 5 页并对离线 HR 的职位 3 线程并发取 job_card，`--active 3d` 跑约 28 秒后触发平台风控 code 37。现在 `--active online` 只看列表的 `bossOnline`，不查详情、只翻 1 页，请求量与普通搜索相同；其余档位先看 1 页，结果不足 10 条（或 `limit`）且本页仍有职位要查详情才继续，最多 3 页；在线行直接通过任何档位。需要查详情时改为串行，每次请求前按列表请求的节奏（5–10 秒）间隔，CDP / 指定浏览器来源时只走浏览器 job_card，不再 httpx 与浏览器混用；与 `--welfare` 同用时同样保守，单独 `--welfare` 行为不变。`bossOnline` 统一按 `True` / `1` / `"1"` 判为在线（`JobItem.boss_active` 与活跃度判断共用），`hints.active_filter` 新增 `detail_lookups` 提示实际查了多少个详情。`boss watch` 和向导求职流程的默认翻页同步调整。
- 招聘者 `hr request-resume` / `hr resume --exchange` 不再把已生效的动作误报为 `unexpected page result`，换微信也不再顺带发出求附件简历请求（#443）。确认按钮只在当前组件和本次动作新弹出的可见弹层里按文字「确定」查找，没有新弹层就不点；求简历同时认新旧两种文案（含「想要一份您的附件简历」），并在动作后只读回查聊天记录。页面动作执行但找不到发送证据时返回新错误码 `ACTION_UNCONFIRMED`（`recoverable=false`，先 `boss hr chatmsg <friend_id>` 核实、勿直接重试），页面日志、确认状态和 WS 统计放进 `error.details`；同时修正 CDP 文本帧被当作 base64 解码的问题。
- 定向更新安全依赖：`[mcp]` 的 PyJWT 锁定到 2.15.1、`[crawl]` 的 urllib3 锁定到 2.8.0、`[dev]` 的 virtualenv 锁定到 21.7.13，并仅连带更新 python-discovery 到 1.6.1。在对应 extra 中声明安全版本下界，避免全新安装回退到已知风险版本，不扩大基础运行依赖；补齐依赖作用域和 JWT 调用者选项隔离/过期声明校验回归。
- `boss config set platform` 在写入前按当前注册表拒绝已移除、未知或空平台值；失败不创建配置文件，也不改写已有内容，保留显式 `--platform zhipin` 修复旧配置的入口。
- `boss doctor` 将可执行后继命令与真人操作指引分别放入 `next_actions` / `operator_actions`，命令保留数据目录、平台、浏览器来源和 CDP 上下文；TTY 显示真人指引。源码质量工具检查和维护脚本建议仅面向本项目源码运行，安装包用户不再被要求拥有开发工具链。

## [3.0.1] - 2026-10-23

### Fixed
- 招聘者在线简历接口 `/wapi/zpjob/view/geek/info` 返回「旧简历接口已下线」时，`hr resume` 与 wizard 改为 `ENDPOINT_DEPRECATED`（不可恢复，停止重试），并在 `error.details.tracking_issue` 指向 #472。聊天发送旧端点 #217 仍走同一错误码，跟踪链接改到各自的 `details`，不再写死在 `recovery_action`。`recovery_action` 文案改为查看 `error.details.tracking_issue`，错误码本身不变。
- CDP 回读在线简历时，已解开的 `resumeInfo` 在弹层内部的 `new-chat-resume` 上，外层 `transition` 的 `$children` 为空。读取改为同时扫描弹层里的 Vue 实例；`geekBaseInfo` 只保留解析器会用到的字段。教育经历同时认 `degreeName` / `startDateDesc` / `endDateDesc`。
- `hr resume` 的在线简历请求改为 `uid`、`encryptJid`、`securityId`，并带 `X-Requested-With: XMLHttpRequest`。不再发送插件时期的参数名，也不生成 `sigx`。登录态沟通页的完整正文由页面自己请求 `GET /wapi/zpjob/view/geek/info/v2`（含 wasm 的 `challenge`）；CLI 不复刻该请求。CDP 下若旧接口仍返回下线文案，且已打开 `/web/chat/index`，再调用页面自己的 `newChatResumeViewer`，只读取 securityId 匹配的白名单字段并关闭弹层。没有聊天页或没有可读正文时，仍返回 `ENDPOINT_DEPRECATED`。

## [3.0.0] - 2026-10-02

### Changed（含 Breaking Change）
- **移除 Browser Bridge。** 删除本地 daemon、Chrome 扩展、Python `boss_agent_cli.bridge` 包与 `[bridge]` extra，移除 `boss doctor` 的七项 `bridge_*` 诊断；不再探测或连接旧 Bridge 服务。浏览器来源 `auto` 的通道顺序改为 CDP → headless，`existing-browser` 仅复用已有 CDP 浏览器中的目标页；`stored-cookie` 仍只连接显式指定的 CDP 端点，来源约束和风控停止契约不变。
  - `chat` / `chatmsg` 默认继续使用本地凭据的 httpx 通道；需要不读取本地凭据的现有浏览器会话时，请显式使用 `boss --browser-source existing-browser --cdp-url http://localhost:9222 chat`，并在该浏览器中手动打开和登录 BOSS 直聘。
  - **升级 CLI 不会停止旧 daemon，也不会卸载浏览器扩展。** 请手动停止旧 daemon（包括其他虚拟环境中的实例），在 `chrome://extensions` 禁用或移除旧扩展，并从安装配置中删除 `[bridge]` extra。CDP 调试端口仅限本机可信环境使用，不得用于规避平台风控。
- **移除智联招聘（`zhilian`）平台。** 求职者侧适配器（`ZhilianPlatform` / `ZhilianClient` / `zhilian.yaml`）、招聘者侧 `agent` 浏览器 / CDP 自动化（`automation/zhilian_*`）、`ZhilianPlatform` 包级公开导出、schema 与 MCP 描述中的平台可用性、能力矩阵、向导标签、研究文档和演示内容一并删除；BOSS 直聘（`zhipin`）成为唯一注册平台，多平台抽象（`Platform` / `RecruiterPlatform` 注册表与 `--platform` 选项）保留。`boss --platform zhilian ...`（含 `--role recruiter agent run`、`status --live`、`hr ...`、`platforms --platform zhilian`）以及用 `boss-mcp --platform zhilian` 启动的 MCP 宿主，现在都返回七键 `INVALID_PARAM` 信封（`unknown platform 'zhilian', supported: zhipin`）。
  - 迁移：若 `config.json` 里写着 `platform: zhilian`，所有命令都会被拒绝，运行 `boss --platform zhipin config set platform zhipin` 改回默认平台。
  - `boss doctor` 不再额外探测 zhaopin.com（`network_zhilian` 检查项移除）；招聘者 `status --live` 的智联 selector 健康检查及其 `SELECTOR_HEALTH_FAILED` 错误、`selector_health` 字段随之移除。
  - 升级前以 `zhilian` 保存的 wizard run（含 MCP `boss_wizard` 恢复）在恢复时同样返回 `INVALID_PARAM`，不再读取遗留登录态；请以 `zhipin` 新建 run。
  - 便携包不再附带 `examples/zhilian-recruiter.sh`。
  - 本地遗留的 `~/.boss-agent/auth/zhilian/` 登录态与自动化存储中的智联条目不再被读取，也不会被 zhipin 运行执行，可手动删除。

### Changed
- 内部重构：`boss schema` 的能力描述、错误码登记表与格式转换从命令模块 `commands/schema.py` 拆到独立的 `boss_agent_cli.schema` 包（`data` / `error_codes` / `availability` / `formats`），`display` 与 `mcp_tools` 不再反向依赖命令层；CLI 输出、`boss schema` 四种格式与 MCP 工具面均逐字节不变。
- 内部重构：`commands/` 只保留 Click 命令与 `_` 前缀的 CLI 支撑模块；聊天导出 / 快照、联系人查找、职位导出、job_card 映射、浏览器内核探测等可复用逻辑移入 `boss_agent_cli.services`，平台实例构造移入 `platforms/factory.py`，wizard 不再调用命令模块的私有函数；行为不变。
- 内部重构：CDP 默认地址与 zhipin / zhilian 域名校验移到 `api/browser_urls.py`，`api/browser_client.py` 不再 import `auth.browser` 的私有符号，打断 api ↔ auth 运行时 import 循环；行为不变。
- 清理无运行时调用方的代码：`automation/zhilian_browser_actions.py`（与智联自动化平台 `execute_action` 重复的动作分派，#379 后已无调用方）、`automation.events.stable_action_id`、Bridge daemon 的 `start_daemon_background` / `stop_daemon` 与未使用的 `BridgeNotRunning` / `BridgeExtensionDisconnected`、`ai.local_models.parse_model_manifest`，以及 wizard renderer 中三个未被调用的渲染函数。原先经 `execute_browser_action` 覆盖智联浏览器 session 的用例改走运行时真实路径 `ZhilianRecruiterAutomationPlatform.execute_action`；`compliance` 中的历史兼容函数按设计保留。

### Fixed
- `boss chat` 现在公开跨请求稳定的联系人 `uid`，`chatmsg` / `chat-summary` / `mark` / `exchange` 优先按 `uid` 重新解析联系人并使用本次沟通列表返回的动态 `securityId`；沟通快照、diff 与导出映射同步改用 `uid` 主键，避免轮换令牌导致联系人查找必然失败和快照重复。既有 `security_id` 参数名与无 `uid` 的旧快照仍兼容，但旧令牌仅作兜底且可能已经失效。
- 移除通用 CI / Docs workflow 后，同步清理失效的 README 状态徽章、远端门禁文档与工作流存在性断言；PR 和发版改为明确执行维护者本地质量门禁，避免主线测试固定读取已删除文件。

## [2.0.1] - 2026-09-29

### Fixed
- `boss chat` 现在公开跨请求稳定的联系人 `uid`，`chatmsg` / `chat-summary` / `mark` / `exchange` 优先按 `uid` 重新解析联系人并使用本次沟通列表返回的动态 `securityId`；沟通快照、diff 与导出映射同步改用 `uid` 主键，避免轮换令牌导致联系人查找必然失败和快照重复。既有 `security_id` 参数名与无 `uid` 的旧快照仍兼容，但旧令牌仅作兜底且可能已经失效。

## [2.0.0] - 2026-09-15

### Changed（含 Breaking Change）
- **移除未完成的前程无忧 / 51job（`qiancheng`）占位平台。** 候选者平台注册表、`51job` 别名、`QianchengPlatform` 包级公开导出、schema 可发现性、能力矩阵、向导标签、研究文档和演示内容一并删除；`boss platforms` 同时删除闲置的 `placeholder_only` 图例和 `placeholder` 空分组。`boss --platform qiancheng ...` 与 `boss --platform 51job ...` 现在返回七键 `INVALID_PARAM` 信封，不再返回占位 `NOT_SUPPORTED`。**迁移**：将配置中的 `platform` 改为 `zhipin` 或 `zhilian`；从 Python 导入 `QianchengPlatform` 的下游代码需要移除该依赖。
- **移除 README 的赞助与推广展示位，并移除 `atlas` provider 接入。** README 两语顶部的 Atlas Cloud 展示位（含 UTM 追踪链接与 logo 资产）和 Doloffer 推广块整体删除；`PROVIDER_BASE_URLS` 移除 `atlas`，`--provider` 帮助、`docs/integrations/ai-models.md` 两语与 CONTRIBUTING 的相关表述同步。这是维护者主动移除赞助关系，不是 CONTRIBUTING「端点不可用」条款下的移除，所以按变更通知记录。**迁移**：已保存 `ai_provider=atlas` 的配置在 `boss ai config` 里会看到 `resolved_base_url` 为空，请改为 `boss ai config --provider custom --base-url https://api.atlascloud.ai/v1 --model <model> --api-key <key>`，服务本身仍可作为自定义 OpenAI 兼容端点使用。
- **code 37 改为按响应语境分类（对外契约变更）。** 此前所有 code 37 一律全局映射为 `TOKEN_REFRESH_FAILED`
  （`recoverable=true`，恢复动作 `boss login`）。现在只有文案明确指向 token/stoken 过期的 code 37 保持该行为；
  环境风险文案及语义不明确的 code 37 一律发新的 `ENVIRONMENT_RISK`（`recoverable=false`），立即停止且不刷新、
  不重试、不提示重新登录。候选人与招聘者的 httpx 刷新判定与平台 adapter 使用同一分类器
  （`boss_agent_cli.api.zhipin_errors.classify_code_37`）；候选人浏览器通道额外在环境风控上直接终止。
  按错误码分支的下游 Agent 请新增 `ENVIRONMENT_RISK` 终止分支，绝不对其自动登录/刷新/重试。

### Added
- **`login --cdp` 复用现有登录态前做一次只读有效性探测（Issue #424 第二步）。** 复用命中后在同一页面里用浏览器自身会话请求用户信息接口（不读本地凭据，与 `existing-browser` 约束一致）：通过才落盘；服务端已失效则抛 `ReusedSessionStaleError`，`AuthManager` 自动改走 `--force` 路径重登，不再把死凭证当「登录成功」保存；探测命中 code 36 / 环境类 code 37 直接抛 `AccountRiskError` / `EnvironmentRiskError`，`login` 命令按与其他命令相同的风控契约返回 `ACCOUNT_RISK` / `ENVIRONMENT_RISK`（`recoverable=false`），且不再被降级到 QR / patchright 兜底再登一次。页面未就绪或探测网络失败按「未验证」继续并提示。目前只探测 zhipin，智联复用仍只看 cookie 存在性。
- `boss login --force`：不复用任何既有登录态（Issue #424 第一步）。跳过本地浏览器 Cookie 提取；CDP 路径不扫描已登录 context，清掉当前 context 内目标平台域的 cookie（其他站点与其他 context 不动）后打开登录页重新扫码，堵住「服务端已失效的 `wt2` 被复用路径当成已登录、用户没有重登入口」的死循环。与 `--cdp` 正交，不能与 `--curl-file` / `--cookie-source` 混用；`login` 的选项互斥错误现在按 `INVALID_PARAM` 返回而不是 `NETWORK_ERROR`。
- `boss login --curl-file` 支持从浏览器 cURL 文本导入 BOSS 登录态，验证后复用原生加密存储，不执行原请求或保存业务请求体。
- 招聘者模式新增推荐牛人完整卡片和需明确批准的首次招呼；使用单次直连请求及候选人／职位原子防重，不建立 MQTT 连接或发送已读回执。
- 新增 `hr accept-resume` / `hr download-resume` 及对应 MCP 工具：核对候选人与消息后同意请求，检查权限并下载已收到附件；复用原生认证，不自动重试写请求、不覆盖已有文件。
- **公开 `--browser-source` 浏览器来源选项**（取值 `auto` / `existing-browser` / `stored-cookie`，默认
  `auto` 行为不变）。`stored-cookie` 为 fail-closed 严格模式：只连接 `--cdp-url` 指定的 CDP 端点，
  不自动探测 `localhost:9222`/`DevToolsActivePort`、不降级 Bridge/headless、空浏览器不新建 context
  注入 Cookie，不可用即发 `CDP_UNAVAILABLE`；`existing-browser` 只复用现有浏览器（Bridge/CDP），
  不读本地凭据、不启动浏览器，失败发 `BROWSER_SESSION_NOT_FOUND`。stoken 静默刷新同样遵守该策略
  （#418）。CLI / config / `boss schema` / MCP / wizard 全链路透传；`schema` 的 `global_options`
  声明该选项并标注 `stability: experimental`，输出带 `current_browser_source`。非 `auto` 来源用于
  无浏览器通道的平台（当前为 zhilian）时返回 `NOT_SUPPORTED`。由 @iqjiy 提出并发现相关问题，
  策略表与契约见 Issue #387 / PR #410。
- **公开职位 lid 与浏览器职位卡片接口。** `JobItem` 新增 `lid` 字段（解析自列表响应
  `raw.lid`，缺失时为空串，并随 `to_dict()` 序列化）——取 JD 全文需要 `securityId` + `lid`
  两个参数，此前走 CLI 的调用方拿不到 `lid`。`BossClient` 新增 `job_card_browser(security_id, lid)`，
  强制走浏览器通道获取职位卡片；既有 `job_card()` 的 httpx 优先行为完全不变。

### Fixed
- **复用用户页签时撞上手动导航不再挂起（Issue #423）。** `BrowserSession.request()` 的 `evaluate(fetch)` 捕获 patchright 的 `Execution context was destroyed` / `Target closed` 类异常后，做一次有界 `wait_for_load_state` 恢复并重试同一请求**一次**；仍不就绪返回 `code: -1` 错误信封，重试再撞上同类异常原样上抛。健康路径零额外往返（不加等待），其他 evaluate 错误不重试。风控响应不经此路径，#419 契约不受影响。
- `--browser-source` 的取值域在 `boss schema` 与 `boss-mcp` 两处不再手写三元组，统一从 `api/browser_source.py` 的策略表推导（与 CLI / `config set` 一致，`schema` 里 `choices` 的顺序随之改为策略表顺序），新增来源时不会再漂移；schema 描述补上 `existing-browser` 候选耗尽发 `BROWSER_SESSION_NOT_FOUND`。补齐 config.json 非法 / 带大小写空白的 `browser_source` 值与 MCP 透传的测试。
- 招聘者写操作在 #403 的 code 37 分类下仍不刷新、不重试；招呼遭遇环境风控时仅提示官方页面人工处理，不再建议调用聊天接口。附件同意的未知结果改用已声明的 `RESUME_ACCEPT_RESULT_UNKNOWN`，确认提示复用通用恢复契约。
- 附件下载的登录与网络错误采用 schema 恢复指引，二进制 HTTP 401/403 映射为 `AUTH_REQUIRED`；以排他创建替代硬链接落盘，保留不覆盖与写入失败清理，补齐推荐牛人 CLI 成功和失败测试。
- 修复招聘者首次招呼将 `code=0` 的权益拦截页误判为成功：识别开聊业务拒绝，返回 `GREET_LIMIT`、`sent=false` 及脱敏平台提示；保留拒绝状态并阻止重复发送，不影响其他端点的成功判断。
- 招聘者招呼与附件操作保留登录失效、令牌刷新失败和账号风控分类，未知发送结果停止重试；最近消息未知未读数不覆盖会话真实计数，支持 `lastMsgInfo.showText`。
- **复用已登录 CDP 会话并消除首次导航竞态。** `login --cdp` 现在跨所有 browser context
  按精确 hostname / cookie domain 校验搜索已有 BOSS 登录态（`wt2`/`at`）：命中时复用该
  context 与既有平台页签，不导航登录页、不轮询等待；cookie 中已含 `__zp_stoken__` 时
  优先读取 cookie jar，仅在缺失且页面经有界 `wait_for_load_state` 确认可加载后才做页面
  提取。UA 采集按路径 gate：未登录时在已加载的登录页上提前采集（沿用 #390 顺序），复用
  新建页签时在首页确认加载后采集，复用既有页签时与 stoken 共用同一次有界就绪检查——
  导航卡住的页面 UA 与 stoken 都不会被 evaluate（避免 patchright 永久挂起）。清理时只
  关闭本次调用新建的页面。智联未登录时复用既有 recruiter 页签不再被导航走；智联已登录
  但无 recruiter 页签时新建页签也会回各自 home（修复 `x-zp-client-id` 在 about:blank 上
  读取失效导致的 `TokenRefreshFailed`）。API 浏览器会话（`BrowserSession._try_connect`）
  新建 CDP 页面改为等待 `domcontentloaded`，修复首次搜索出现 `execution context destroyed`
  的竞态；并为该路径补「卡住不 evaluate」门禁——goto 超时不再被 `except` 吞掉后直接
  `return True`，而是标记 `_page_ready=False`，`request()` 在 `evaluate(fetch)` 前先做有界
  `wait_for_load_state` 恢复，仍不就绪则返回错误信封而非永久挂起（与 #390 同一规则）。
  多 context 复用时在终端打出选中的 context 序号与「账号指纹」（登录态 cookie 值的不可逆
  哈希前缀，不泄露 cookie），缓解多窗口/多 profile 下的账号歧义。#390 的导航超时/重试、
  `_safe_user_agent`、`_warm_home_for_runtime` 与 cookie-jar 兜底全部保留。
- **风控类异常在三个执行器中的终止语义收敛（Issue #419）。** `AccountRiskError` / `EnvironmentRiskError`
  现在共享基类 `PlatformRiskError`（带稳定 `code`），新增风控码只需登记子类。此前三处执行器会反转或吞掉终止语义：
  福利筛选线程池的 `except Exception` 会吞掉浏览器通道抛出的风控异常并继续扫描整页；`batch-greet` 靠
  「频率」字样判停批，code 36 会被重试一次后继续下一个候选人，code 37 环境风控则被打成 `ok:true` +
  `stopped_reason="RATE_LIMITED"`；wizard runner 的兜底把两者降级成 `NETWORK_ERROR` + 「重试当前 run_id」。
  现在三处都立即停止、不重试、不继续下一项：线程池取消尚未开始的详情请求并原样上抛；`batch-greet` 命中风控时
  信封改为 `ok:false` + 对应错误码 + `recoverable=false`，已成功项随 `error.details.greeted` 带回；
  wizard runner 走专门分支，`recoverable=false`，恢复动作与单次命令路径逐字一致（`display.RISK_ERROR_CONTRACTS`
  为唯一真源）。wizard 的 `_classify_action_error` 对风控码不再落到「稍后重试」兜底；runner 同时把
  `BrowserSourceUnavailable` / `BrowserSourceUnsupported` 映射为策略错误码 / `NOT_SUPPORTED`，不再兜成 `NETWORK_ERROR`。
- **stoken 静默刷新现在遵守 `browser_source` 策略表（Issue #387 seam 收尾）。** #410 把浏览器通道
  选择收进策略表后，httpx 通道的 stoken 刷新（`_base_client._request` → `AuthManager.force_refresh`）
  仍绕过策略：`stored-cookie` 下 stoken 过期照样会「CDP 不可用，降级到 headless」，起一个 headless
  Chromium 带着本地 Cookie 访问平台。现在 `force_refresh` 接收 `browser_source`：fail-closed 来源
  不再自动探测默认 CDP 端口（没给 `--cdp-url` 直接失败）、CDP 不可用时不再降级 headless，
  抛带策略错误码（`CDP_UNAVAILABLE`）的 `BrowserSourceUnavailable`；智联显式来源下本地 Cookie 失效时
  同样 fail-closed，不再打开登录页等扫码。`auto` / 未传参行为逐字不变。该参数尚未暴露到 CLI
  （由 PR #404 接线），当前只影响程序化调用；信封层把该异常映射为策略错误码的 `display` 分支
  随 PR #388 落地，在此之前兜底仍为 `NETWORK_ERROR`。
- 修复 Bridge 已连接日常浏览器时 `chat` / `chatmsg` 仍先要求 CLI 本地 `wt2` 的问题（#386）。
  Bridge 连接作为本切片的现有浏览器使用信号，走 `existing-browser` 策略的 Bridge → CDP
  白名单，不读取本地凭据、不新建 context、不启动或降级到 headless；候选耗尽返回
  `BROWSER_SESSION_NOT_FOUND` + `boss doctor`，真人浏览器指引放在 `operator_actions`。
  Bridge 未连接时保留原 httpx、stoken 刷新和限流重试语义；本地无凭据仍返回
  `AUTH_REQUIRED` + `boss login`。中英文风险边界与能力矩阵同步说明：连接存在不等于已验证目标页登录。

### Changed
- ROADMAP 中 `BrowserSessionProvider` 条目那句「绝不启动新实例、绝不触发登录、绝不静默回退
  CDP/headless」按 #387 的决定限定为**仅在显式来源下**成立，`auto` 保持既有降级链；中英同步。

## [1.20.0] - 2026-09-03

> **升级提示（仅影响 `mcp` extra）**：`mcp` 依赖的**下界**从 `1.0.0` 提到 `2.1.0`
> （区间 `mcp>=2.1.0,<3.0.0`）。主推的 `uvx --from boss-agent-cli[mcp] boss-mcp`
> 每次现解析临时环境，不受影响；受影响的只有「把 `boss-agent-cli[mcp]` 装进一个
> 还钉着 `mcp<2` 的长期共享环境」这种场景——请一并放开该环境里的 `mcp` 约束。
> **MCP 线格式与工具集合一字未变**，宿主侧无需任何改动。

### Changed
- **适配 mcp 2.x Server API（Issue #398），依赖改为 `mcp>=2.1.0,<3.0.0`。** mcp 2.0 移除了
  `@server.list_tools()` / `@server.call_tool()` 装饰器，改为
  `add_request_handler(method, params_type, handler)`，且 handler 签名与返回类型一并改变
  （`(ctx, params)` → `ListToolsResult` / `CallToolResult`）。SSE 与 streamable HTTP 两条传输
  实现原样保留——本次只做适配，换实现是独立决策。
  **MCP 线格式一字未变**：`Tool` 的字段名内部从 `inputSchema` 改成 `input_schema`，但 pydantic
  别名保留，序列化仍输出 `inputSchema`，宿主侧无感知；源码里 71 处构造 kwarg 随之改名（mypy 的
  pydantic 插件按字段名生成 `__init__`，不认别名）。
  **上界保留**：上一次声明无上界导致所有全新安装解析到新大版本并崩溃，而 CI 锁着旧版本永远看不到。
- `CONTRIBUTING.md` / `.en.md` 新增「新增 AI provider」一节，把 provider 的准入与移除写成明规则：
  文档只写可用一次 API 调用证伪的陈述（不接受安全姿态、定价承诺、评级与会静默过期的规模数字）；
  服务商自荐需提供可验证归属（企业域名邮箱或官方公开双向引用，不追溯）；端点连续两个 minor
  版本不可用即直接移除，不走弃用周期、不算破坏性变更。第三条是前两条能够宽松的前提。
- 按上述规则对齐既有条目：`docs/integrations/ai-models.md` / `.en.md` 的 Atlas Cloud 段落移除
  59 个模型的全量清单（会随服务商上下线静默失真且无测试可守）与链接上的 UTM 追踪参数，
  定性措辞压回与 `openrouter` 同一语域，并补回「可用模型以服务端实际支持为准」。
  README 的赞助展示位不受影响——那是已披露的赞助关系，与 provider 文档条目是两回事。
- **浏览器通道选择收敛为策略表驱动的单一分发点**（Issue #387 seam 第 2 片的前置改造，无对外行为变更）。
  `BrowserSession._ensure_started` 原本是硬编码的 Bridge → CDP → headless 三级降级链，
  中间只有两处插入缝隙；两个并行 PR 各自在其中一处插模式短路时，git 三方合并会干净通过、
  不留冲突标记，合并后类上会出现两个互不感知的模式布尔，谁生效取决于插入位置且失效方向是
  fail-open。现新增 `api/browser_source.py`：一张冻结的 `BrowserSourcePolicy` 表声明
  「通道白名单 / 是否读本地凭据 / 能否新建 context / 能否启动浏览器 / 是否自动探测 CDP /
  失败发什么码」，`_ensure_started` 变成对 `policy.channels` 的单个循环，类上只保留一个
  `_policy` 属性。此后新增来源 = 表里加一行，物理上没有第二个插入点。
  **`auto` 为默认且行为逐字不变**（三级降级链、headless 兜底、失败仍走既有 `NETWORK_ERROR` 兜底），
  本次不暴露任何 CLI 选项、不新增任何错误码。新增 22 条结构门禁锁定上述性质。

### Fixed
- **修复设了 SOCKS 系统代理时每一条 httpx 命令都失败**（#412）。全仓四处 `httpx.Client`
  都用默认 `trust_env=True`，即刻意尊重用户的系统代理；但 httpx 只有装了 `socksio` 才支持
  `socks5://` scheme，否则在**构造 client 时**就抛 `ImportError`。于是设了
  `ALL_PROXY=socks5://...` 的用户，`boss status` / `detail` / `favorites` 等每一条只读命令
  都会失败——而那个 ImportError 被 `display.handle_auth_errors` 的兜底转成
  `NETWORK_ERROR` + `recovery_action="重试"`，错误码与恢复动作双错，且这是个永远不会
  因重试而改变的本地环境问题。依赖改为 `httpx[socks]`（拉 `socksio`，35KB、纯 Python、
  零传递依赖），让代理**能用**而不是失败得好看。
  CI runner 环境干净、没有代理变量，这条路径在门禁上永远是绿的，故补
  `tests/test_system_proxy.py` 显式注入代理环境变量把它变成可观测的。
- `boss ai config` 查看与写入现在都回显 `resolved_base_url`（解析后真正生效的端点）。此前查看模式
  直出 `load_config()`，用户只设 `--provider` 时 `ai_base_url` 为空，实际地址来自
  `PROVIDER_BASE_URLS` 查表却从不显示；而 `--provider` 没有 `click.Choice` 校验（自由字符串），
  名字相近的 provider 记混或拼错时，API key 与简历全文会被发到另一家而界面上看不出任何差别。
  该命令此前零测试覆盖，一并补齐 7 条。
- 修复 `BrowserSession` 在 `_start_headless()` 或 playwright driver 启动抛错时泄漏 driver 进程：
  `_pw` 已 `start()` 却永远不会 `stop()`，下一次 `request()` 会再起一个。现在启动失败路径统一
  释放 driver。
- 修复 `BrowserSession.close()` 之后通道标志残留：原实现只置 `_started = False`，`_is_cdp` /
  `_is_bridge` 保留上一轮的值，导致 close 后重启时一个 headless 轮次可能走进 CDP 拆卸分支
  而不关闭 browser。`close()` 同时改为真正幂等（已关闭时直接返回，不重复拆卸）。
- 修复 TTY 下节流等待期间进度条静默不动的观感问题（#400）：`CrawlBudget.wait()` 在等待前把剩余秒数报给 `SearchProgress`，进度条显示一次性「节流等待 Ns…」提示；非 TTY / `--json` 路径与等待 0 秒场景输出完全不变。
- 修复扫码登录在「首页预热」阶段卡住时永久挂起：BOSS 直聘首页在自动化下偶发长时间不完成加载，`page.goto` 超时后若仍对页面执行 `page.evaluate`（取 UA / stoken），patchright 会因执行上下文无法建立而无限阻塞，导致 `boss login` 卡死且不落盘凭证。现在 UA 改在已加载的登录页上提前采集；`_warm_home_for_runtime` 返回首页是否真正加载完成，仅在确认加载后才对页面 evaluate 提取 stoken，否则回退读取 cookie jar；并对首页导航超时增加「跳 `about:blank` 重置卡死导航后重试一次」，显著降低风控验证页/加载缓慢导致的 `Page.goto: Timeout 15000ms exceeded` 误报。`login_via_cdp` / `refresh_stoken` / `refresh_stoken_via_cdp` 同步加固；CDP 登录/刷新 stoken 也改为优先页面 evaluate、失败回退 cookie jar（安全验证跳转期间 `domcontentloaded` 可能未触发，但 `__zp_stoken__` 已写入 cookie jar）。
- 修复 BOSS 收藏接口在 `isActive=true` 下仍混入失效职位时的静默误判：`favorites list` 现输出规范化 `job_status` 与状态计数，`favorites sync` 仅导入明确有效职位并报告 active/inactive/unknown 数量；缺失或未知状态不再默认有效。

## [1.19.1] - 2026-08-27

### Fixed
- **修复全新安装的 `boss-mcp` 完全起不来。** `mcp` 此前声明为无上界的 `mcp>=1.0.0`，
  而 mcp 2.0 重写了 `Server` API——`@server.list_tools()` / `@server.call_tool()` 全部移除，
  改为 `add_request_handler`。`mcp_server` 在模块层就用这些装饰器，因此任何解析到 2.x 的
  全新安装都直接 `AttributeError: 'Server' object has no attribute 'call_tool'`，
  `boss-mcp` 连启动都做不到。已收紧为 `mcp>=1.0.0,<2.0.0`。
  这不是 1.19.0 引入的：1.18.0 在 mcp 2.x 下有完全相同的崩溃，只是此前没人从 PyPI
  全新装一次验过。issue #377 的报告人当时用了 `--with 'mcp<2.0'`，所以他们看到的是
  `-32601` 而不是崩溃。
  mcp 2.x 的适配另开 issue 跟踪，放开上界之前不做。

### Added
- CI 新增 `fresh_install` job：**刻意不走 `uv.lock`**，装构建产物、让依赖按 `pyproject`
  的约束重新解析，再真跑一次 MCP 会话。此前所有 job 都锁着 1.x 依赖，因此
  「CI 全绿、用户全崩」可以长期共存——这个 job 守的就是**门禁看到的与用户拿到的**
  之间那道缝。红灯验证：移除 `mcp` 上界后该 job 以「initialize 没有收到响应」失败。

## [1.19.0] - 2026-08-27

### Fixed
- 修正 CI docker job 的 `tools/list` 断言存在竞态：`printf` 写完三行就关闭 stdin，
  MCP 服务器可能在回 `tools/list` 之前就因 EOF 退出，读端拿到空串报 `JSONDecodeError`
  （已在 CI 上真实发生）。改为写完后保持 stdin 一段时间，并按 JSON-RPC `id` 认响应而非
  依赖行序；`tools/list` 无响应时给出明确断言而不是解析错误。**一道会随机变红的门禁
  等于没有门禁**——它会挡住无关的合并，正如同批修掉的 PTY 用例那样。
- 修正 Docs 工作流的 `Check whitespace` 对**落后于 master 的 PR 一律假红**：
  `git fetch --no-tags --depth=1 origin <base>` 只取到 base 的 tip 并留下浅克隆边界，
  落后的 PR 分支算不出 merge base，`git diff --check A...B` 直接以
  `fatal: origin/master...HEAD: no merge base` 退出 128——报出来像空白字符违规，
  实际是这一步崩了。#382 / #383 / #390 三个外部 PR 的 `docs` 红灯都是这个原因，
  且恰好是等得最久、落后最多的贡献者最容易撞上，还会被当成他们自己的问题。
  去掉 `--depth=1` 即可（checkout 已用 `fetch-depth: 0`，这里只是补 `origin/<base>` 引用）。
- **修复 MCP `tools/list` 从未注册：v1.18.0 的 MCP 入口对任何 host 都不可用。**
  `mcp_server.list_tools` 缺失 `@server.list_tools()` 装饰器，`initialize` 握手正常，但 client
  第一次列工具即 `-32601 Method not found`——73 个工具一个都拿不到。回归由 1.18.0 的
  `mcp_server` 拆分引入，该拆分合并 33 分钟后即发版，没有留下暴露窗口。
  四道门禁同时失效，故本次连门禁一起补（见下方 Added）：`tests/test_mcp_server.py` 用 mock 模块
  顶替整个 `mcp` 包，装饰器在不在原理上观测不到；CI 的 stdio 握手只断言 `initialize`；
  eval 跑离线 fixture 绕开协议层；`__all__` + `mcp-server/server.py` 的 re-export 让这个
  未注册的孤儿函数在 ruff / mypy 眼里完全正常。感谢 @xie-good 报告、@LiuLin1220 修复。
- 脱敏放宽：`_SENSITIVE_KEY_PARTS` 是子串匹配，`ai_max_tokens` / `token_expires_in` 只因键名含
  `token` 就被替换成 `[REDACTED]`。凭据必然是字符串或容器，故把 bool 豁免补完整为
  `(bool, int, float, None)`，并加护栏测试锁定 `stoken` / `api_key` / `cookies` 等仍被脱敏。
- 修正三个终端呈现缺陷：结果列表菜单每页重绘任务摘要框（此前被 `clear_before` 清掉）；
  `render_run` 新增 `with_preview` 开关，浏览列表时职位不再被摘要框与可选菜单列两遍；
  `search` 长任务在 TTY 下注入 `SearchProgress` 显示实时进度（管道 / `--json` 仍传原 logger）。
- 修正 `tests/test_wizard.py` 的 PTY 用例假红：启动同步循环 5s 超时后**照样发送按键**，
  此时 prompt_toolkit 尚未接管输入，按键被丢弃，最终报成「没退出」——把「启动慢」误诊为
  「交互坏」，且不留任何线索。现在必须确认菜单真渲染出来才发按键，否则以「菜单未渲染」
  失败并附上已捕获输出；期限放宽到 30s / 20s 并可用环境变量覆盖（命中同步标记即跳出，
  快机器仍约 1s）。此前它曾在 CI 的 P0 baseline job 上超时并挡住合并，而同一 commit 的
  五个测试矩阵 job 全过。

### Added
- MCP handler 注册守卫 `tests/test_mcp_handler_registration.py`：不查装饰器语法，直接问运行时
  注册表 `server.request_handlers`。「装饰器名 → request 类型」的映射**从 SDK 自身反推**
  （在全新 `Server` 上应用装饰器后 diff 注册表键），因此 SDK 将来新增 `list_prompts` /
  `read_resource` 等 handler 时守卫覆盖面自动跟上，不需人工维护。同时断言 `tools/list` 经协议
  发出的工具集合与 `mcp_tools.TOOLS` **逐名相等**（不引入硬编码计数）。两条测试刻意在子进程里
  跑：`tests/test_mcp_server.py` 会 `sys.modules.setdefault("mcp", <mock>)`，一旦拿到 mock，
  `request_handlers` 就是 MagicMock，守卫会静默变成恒真。
- CI 的 docker job 在同一个 stdio 会话里补做 `tools/list`，断言工具列表非空且含 `boss_status`。
  只断言 `initialize` 正是让上面那个回归发出去的原因。
- 开放 assisted / research 下全部已实现能力，移除模式级 `COMPLIANCE_BLOCKED` 执行门
  （错误码保留作历史协议兼容）；平台未实现的能力仍返回 `NOT_SUPPORTED`。
- 新增共享 Workflow 持久化与纯终端 `boss wizard`：无子命令时默认进入向导，TTY 向导与
  `--input-json` / `--resume` / MCP 共用同一个确定性 runner，状态落 SQLite 的
  `workflow_runs` / `workflow_steps`。`schema` 与 MCP 同步暴露 `wizard` 与 `wizard_catalog`。
- 增强 crawl 传输：登录态注入、页面内请求与回退，命中风控时保留浏览器便于续跑。
- 信封 `hints` 拆为双受众通道：`next_actions` 给 Agent 执行的后继命令，`operator_actions` 给真人的
  自然语言指引（扫码、去浏览器操作等）。判定标准是「是否需要人离开终端」，不是「是否需要确认」。
  TTY 下 `display.render_operator_actions` **只渲染 `operator_actions`** 到 stderr——此前 TTY 分支
  把 `hints` 整个丢弃，真人从来看不到任何提示。无 `operator_actions` 时零输出，既有命令的 TTY
  行为逐字节不变。`SCHEMA_DATA["conventions"]` 新增 `hints` 与 `command_vs_wizard` 两块。
- 新增向导入口登录门 `wizard/preflight.py`：登录检查从 goal 的第一个 step 前移到收集角色 /
  平台 / 目标 / 参数之前——此前未登录时这五层选择全部白做。已登录时零输出零开销（本地文件读，
  不预检浏览器内核）；未登录时可内联扫码登录并无缝进向导；浏览器内核缺失时先给
  `patchright install chromium` 指引，不再开完浏览器才失败。headless 路径保持 `WAITING_INPUT`
  不变（TTY 下人就在终端前，阻塞等扫码是刻意的；Agent 不能卡在子进程里空等）。
- 向导未登录时 run 状态由 `failed` 改为 `WAITING_INPUT`，可 `--resume` 且保留已选的角色 /
  平台 / 目标，不必从头重走。
- 补齐候选者侧九个命令组（`stats` / `preset` / `watch` / `clean` / `ai-local` / `resume` / `ai` /
  `crawl` / `agent`）共 63 处 `handle_output` 的 TTY 渲染分支，真人不再需要自己读 JSON 信封。
  `display.py` 新增 `render_next_steps` / `render_action_result` / `render_list_result` /
  `render_record_result` / `render_ai_result` 五个通用件；`render_ai_result` 不假设键名、只按值类型
  呈现，并对所有值做 escape，避免 AI 输出里的方括号被 Rich 当标记解析。stdout 仍只有单行信封。
- 向导整个多轮交互会话跑在 alternate screen（`\033[?1049h` / `\033[?1049l`）里：进入切、退出还原，
  终端原有内容完好，不再往 scrollback 留残框——`clear_wizard_screen` 的 `\033[2J` 只清可视屏、
  清不掉历史，这是「上滚看到多个框」的根因。用 context manager 保证还原（`commands/wizard.py`
  有 59 处 `return` 且内部会 `raise SystemExit`，逐点还原必然漏）。单次 flag 路径
  （`--status` / `--resume` / `--stop`）刻意不进备用屏，否则渲染完立刻被抹掉。菜单包进 `Frame`，
  框标题为当前步骤；菜单支持 `←` / `Esc` 返回。

## [1.18.0] - 2026-07-29

### Added
- 容器化落地：仓库自带的 `Dockerfile` 重写为多阶段 uv 构建（`uv sync --frozen` 严格按 `uv.lock` 安装，依赖不再漂移）+ 非 root 运行 + `.dockerignore` + `docker-compose.yml` + [Docker 接入文档](docs/integrations/docker.md)，并在 CI 新增 `docker` job 每次构建并验证 CLI 信封、MCP stdio 握手与非 root 身份。镜像定位刻意收窄为**只跑 MCP server 与只读 / 本地命令**：不含浏览器内核，`boss login` 仍在宿主机完成后挂载 `~/.boss-agent`（容器内 `HOME=/data`，故默认数据目录解析为 `/data/.boss-agent`）。不发布 registry 镜像。
- P0 质量门禁新增离线 MCP 评测：`evals/run_eval.py --mode fixture`，产物写 CI 临时目录，避免污染入库的 `evals/results/`。
- CI 矩阵补齐 Python 3.14；`scripts/` 纳入 ruff lint 门禁，并补齐 CI 冒烟路径。

### Fixed
- 修正 MCP 工具 `boss_favorites_list` 的 `page` 参数 schema：类型写成了 JSON Schema 不存在的 `"int"`（其余 105 个参数均为合法值），严格校验 schema 的 MCP 宿主可能因此拒掉该工具。同时给 `boss_crawl_status` / `boss_crawl_results` / `boss_crawl_shortlist` 的 9 个参数补上缺失的 `description`——Agent 依赖它判断该传什么值。新增三条契约测试守住这一整类问题（type 合法性、参数必须有 description、顶层结构与 required/items 完整性）。
- `RecruiterPlatform` 抽象基类补齐两个已被命令层实际调用、却从未写进契约的方法：`send_message_by_friend`（`hr reply` 在用）与 `job_detail`（`hr jobs detail` 在用）。此前它们只存在于 `BossRecruiterPlatform` 实现里，任何新写的招聘者平台按 ABC 实现完都能通过类型检查，却会在这两条命令上运行时抛 `AttributeError`。
- MCP 的 HTTP streaming 传输改用 `@asynccontextmanager` 声明 lifespan：此前是裸 async generator，会走 Starlette 的弃用路径并触发 `DeprecationWarning`。
- MCP `_run_boss` 现在只接受 JSON 对象形式的 CLI 信封：解析结果非 dict 时（契约违例）返回 `CLI_ERROR` 信封，而不是把非 dict 结果透传给调用方。
- GitHub Release 资产过滤改为只上传 `dist/*.whl` / `dist/*.tar.gz`，避免构建后端生成的 `dist/.gitignore` 被当成 `default.gitignore` 垃圾资产挂上 Release。

### Changed
- 将 1600+ 行的 `mcp_server.py` 拆为 `mcp_tools`（工具目录与合规过滤）、`mcp_args`（工具名 → CLI 参数映射）与精简后的服务器/传输层；公开符号原样再导出，`mcp-server/server.py` wrapper 与既有测试导入路径不变。
- mypy 逐模块严格化白名单 120 → 130：纳入 `crawler/` 全部子模块、`commands/crawl.py`、`commands/_recruiter_platform.py`、`mcp_server.py`，以及拆分后的 `mcp_args.py` / `mcp_tools.py`。
- `pyproject.toml` 显式钉死 ruff 规则集 `select = ["E4", "E7", "E9", "F"]`。此前未配置 `[tool.ruff.lint]`，门禁范围实际由所装 ruff 版本的隐式默认值决定——升级到 0.16.0 时默认集扩展到 I / BLE / RUF / UP / DTZ / S / PYI 等家族，同一份代码凭空多出 344 条报错。新增规则家族改为需单独提 PR 并连同修复一起评审。
- 依赖刷新（`uv.lock`）：rich 14.3.3 → 15.0.0、uvicorn 0.46.0 → 0.51.0、pytest 9.0.3 → 9.1.1、ruff 0.15.8 → 0.16.0、websockets 16.0 → 16.1.1 等共 20 个包。rich 为大版本跨越，`display.py` 是全仓唯一使用方，已在加强断言后的渲染测试下逐项验证无行为变化。
- 补齐渲染层断言并收紧 crawl / 招聘者平台实例化覆盖；codecov 项目门槛同步收紧。
- README 首页 badge 行增加 MCP Toplist live rank（中英对称）。

## [1.17.0] - 2026-07-27

### Added
- 新增 `boss favorites list/sync`：读取 BOSS 职位收藏（geekGetJob?tag=4）并同步到本地候选池，让 `boss ai analyze-jd/fit/resume-optimize/chat-coach` 能处理用户真正感兴趣的职位。默认只读、assisted 放行、用户主动触发；sync 远端只读拉取后本地 upsert（按稳定 job_id 去重，刷新动态 security_id 并保留首次收藏时间），list 单页预览不落库且输出脱敏 `security_id`/`job_id` 为 `[REDACTED]`（完整值落库后从 `shortlist list` 取，遵循现有导出脱敏约定）。MCP 仅暴露 `boss_favorites_list`（只读预览），sync 不对 MCP 暴露以保持 assisted 主动触发边界。查看职位详情需 sync 落库后用 `boss --json shortlist list` 取完整 `security_id`/`job_id`，再 `boss detail <security_id> --job-id <job_id>` 查看（list 预览已脱敏；detail 以 sid 为必填位置参数，httpx 快速通道只用 job_id）。
- 搜索结果与详情输出新增原始岗位类型、规范化用工类型、每周实习天数、最短实习周期等字段；导出与索引缓存同步保留这些元数据。
- 新增受限 Research Mode 的可恢复 BOSS crawl 工作流：独立 Chrome profile、固定请求/详情/墙钟/重试预算、SQLite checkpoint、停止开关、外置 Hook 完整性校验，以及脱敏 selector 导入本地候选池。MCP 保持 assisted-only，仅可读取或本地导入已有 crawl run。

### Fixed
- 修复 BOSS 直聘“实习”筛选错误映射为兼职参数的问题：请求参数改为 `jobType=1902`；明确仅搜索实习时，搜索、导出和批量打招呼会按响应中的真实岗位类型失败关闭，避免把全职、兼职或类型缺失的岗位误当作实习岗位。搜索缓存结构同步升级，避免复用旧版本产生的混合岗位缓存。

## [1.16.0] - 2026-07-17

### Added
- 新增显式 `operating_mode` 双模式契约：默认 `assisted` 保持既有低风险阻断；用户主动设置 `research` 后，可启用策略注册表声明的浏览器协议、反调试、风控适配和受控采集研究能力。`boss schema` 同步暴露当前模式、可选模式和逐命令风险/数据分类。

### Changed
- 合规护栏升级为不可变能力策略注册表，CLI、schema 与默认 MCP 过滤从同一真源派生；旧内部配置 `low_risk_mode=false` 兼容映射到 `research`，但不再作为公开配置入口。

## [1.15.0] - 2026-07-14

### Added
- 新增 `boss ai cover-letter <resume> [--jd <文本|@文件>|--job-id <id>] [--tone 简洁专业|热情积极|谨慎稳重] [--lang zh|en]`：基于本地简历与目标岗位起草求职信/自我介绍草稿，纯本地 + AIService、零平台请求，draft-only（只产出文本、不发送、不进敏感命令）；MCP 同步新增 `boss_ai_cover_letter`。

### Removed
- 移除 `config.json` 中 6 个从未被任何命令消费的死配置键（`default_city` / `default_salary` / `batch_greet_max` / `login_timeout` / `resume_default_template` / `resume_export_format`）：此前它们可经 `boss config set` 设置却静默无效，现改为按未知键明确拒绝；同时消除 `resume_export_format` 默认 `pdf` 与 `resume export` 命令默认 `json` 的自相矛盾。
- 移除 `interviews` 命令中恒为假的 `_stub` 占位分支：`interview_data` 端点对 zhipin/zhilian 均已接通、无任何 client 再写入 `_stub`，故删除该死码及其 `capability: stub` hint（纯内部清理，正常输出不变）。

### Changed
- 命令层错误信封统一改用既有 `handle_platform_error_output`：当平台响应携带 `error.details`（主要是 `qiancheng` 占位响应）时，错误信封现在会带上 `details` 字段（新增字段，与已在用该 helper 的 `me` / `history` / hr `jobs` 命令口径一致）；BOSS 直聘 / 智联的真实响应不含该字段，输出不变。

### Fixed
- 修复智联招聘 CDP / 浏览器页面选择的域名校验：此前用 `"zhaopin.com" in url` 子串判断，可能被 `zhaopin.com.evil.example` 等伪造 hostname 误判为可信页面；改为 `urlparse().hostname` 精确匹配 `zhaopin.com` 及其子域（CodeQL `py/incomplete-url-substring-sanitization`，两处页面查找共用同一 host 判断）。
- 修复 #334：CDP 模式下每次搜索/推荐等高风险操作都会在用户 Chrome 新开一个 BOSS 首页标签页并导航（观感为“反复自动新开/刷新页面”）。现优先复用用户已打开的 zhipin 标签页（精确 hostname 校验），不再多余新开与导航，`close()` 也不再关闭被复用的用户标签页；无已打开 zhipin 页时回退原新建行为。风控即停（code 36）逻辑不变。

## [1.14.0] - 2026-06-25

### Added
- `boss shortlist` 新增本地标签、备注与离线对比能力：`shortlist add --tags/--note` 可在加入候选池时记录本地整理信息，`shortlist annotate` 支持增删标签和更新备注，`shortlist compare [--tag]` 仅从 SQLite 候选池读取并按标签本地过滤；MCP 同步新增 `boss_shortlist_annotate` / `boss_shortlist_compare`，默认低风险工具数 33 → 35。
- 新增 `boss ai fit --resume <name> [--limit N]`：基于本地简历与候选池已缓存职位详情生成逐岗匹配度、能力缺口、关键词命中和建议；缺少详情的岗位只报告本地缺口并提示先执行 `boss detail`，不发起任何新增平台请求。
- 新增 `boss ai suggest-keywords [--limit N]`：基于候选池职位分析推荐搜索关键词组合，零平台请求，纯本地 AI 辅助；MCP 同步新增 `boss_ai_suggest_keywords`。
- 新增 `boss ai resume-optimize <name> --jd <text>|--job-id <id>`：基于目标岗位优化简历措辞（仅建议，不修改简历），支持从缓存读取职位描述，零平台请求；MCP 同步新增 `boss_ai_resume_optimize`，默认低风险工具数 35 → 37。

### Changed
- 转向 MCP-first：README / landing / docs 将 MCP（`uvx --from boss-agent-cli[mcp] boss-mcp`，33 个默认低风险 MCP 工具）列为 Agent 首选接入，移除 `npx skills add` 引导；MCP server 新增 server-level `instructions` 承载低风险与错误恢复 doctrine；独立 Agent Skill 仓库 boss-skill 退役。CLI 命令、JSON 信封与错误码不变，仍以 `boss schema` 为真源。
- `boss search` 结果新增纯本地 `match_score`，福利筛选结果可用 `--sort score` 按匹配分降序输出；评分只使用已抓取字段与福利匹配注解，不改变翻页、详情补抓、节流或并发上限。
- 修正 ROADMAP 多平台条目的智联状态漂移：父行此前仍写"智联为 v2.0 优先接入候选（2-3 周）"，与自身已勾选的 Week 2-3 子项（只读 + 写操作已实现）及 CHANGELOG 1.12.0「候选者侧已接通」矛盾；改为如实声明"智联候选者侧已接入"，保留拉勾/猎聘不接入与 51job backlog 结论。

### Changed
- 修正 `docs/marketing/awesome-submissions.md` 投稿模板内自相矛盾且陈旧的数字（命令数 34/33、招聘者子命令 7、MCP 工具 49/31、测试 1315、release 1.11.0），统一为当前准确值：35 顶层命令 / 9 招聘者子命令 / 33 默认低风险 MCP 工具 / 1467 测试 / release 1.13.1（其中"默认低风险 MCP 工具"取合规过滤后实际导出的 33 个，与 README.en 受测口径一致）。

### Changed
- 修正贡献者文档中指向已迁出路径 `skills/boss-agent-cli/SKILL.md` 的陈旧引用（`CONTRIBUTING.md` / `CONTRIBUTING.en.md` 添加新命令清单第 5 步改为 `docs/commands.md`、PR 模板指向独立仓库 boss-skill），消除迁出 SKILL 后遗留的误导。
- 合并 `docs/blog/` 进 `docs/marketing/`：发布/推广文案归拢到单一目录，去除同类内容的双目录冗余。

## [1.13.1] - 2026-06-16

### Changed
- `--welfare` 搜索的职位描述详情新增本地缓存（按稳定的 `job_id`/`encryptJobId` 键，TTL 24h）：重复或微调条件的 welfare 搜索复用已取描述，跳过对应 `job_card` 请求，减少平台请求量并加速重跑；不改变请求间隔与并发上限（合规姿态不变）。
- `RequestThrottle` 改为线程安全：welfare 详情线程池并发调用下，加锁并预占下一次发送时刻，杜绝竞态导致的请求突发（突发更像爬虫，是合规风险）。

### Fixed
- `boss search` 浏览器通道冷启动削减两处无效等待（CDP 自动探测超时 3s→1s、headless 首页 networkidle 宽限 3s→0.8s），普通搜索实测 14.3s→10.0s（约 -30%），惠及全部搜索；零新增请求、不触碰风控节流。
- `boss export` 缺少关键词时的 `INVALID_PARAM` 信封补充可执行 `recovery_action`，Agent 可程序化恢复。

## [1.13.0] - 2026-06-11

### Added
- `boss platforms` 新增 `--platform` 单平台过滤，支持 `qiancheng` 与 `51job` 别名，仅读取本地能力元数据并保持未知平台的 `INVALID_PARAM` JSON 包络。
- `boss platforms` 输出新增 `capability_status_legend`，解释 `available` / `not_supported` / `placeholder_only` / `low_risk_blocked` 的清晰语义，避免 Agent 将占位或低风险阻断误读为真实能力。
- `boss schema` 错误码枚举补齐 login 链路 6 码（`LOGIN_TIMEOUT` / `CDP_UNAVAILABLE` / `BROWSER_KERNEL_MISSING` / `LOGIN_RISK_CONTROL` / `LOGIN_EXPIRED` / `LOGIN_CREDENTIAL_EXTRACTION_FAILED`），错误码总数 26 → 32，Agent 可经 schema 发现全部登录错误的恢复动作。

### Changed
- 补强 `boss config` 未知配置项错误路径的 stdout 单行 JSON 包络契约测试，确保 Agent 可稳定解析 `INVALID_PARAM`。
- PR 模板补充无 `Co-authored-by` 尾注或 AI 署名行检查项，对齐贡献规范。
- 51job/前程无忧占位适配器补齐候选者侧全量能力的稳定 `NOT_SUPPORTED` 包络，避免未启用真实适配前落入默认 `NotImplementedError`。
- 补强 51job/前程无忧占位包络的 `capability` 明细契约，确保 Agent 可稳定区分具体未支持能力。
- 双语 README 重构为导航型主页：命令参考与诊断排障下沉到 `docs/commands(.en).md` 与 `docs/troubleshooting(.en).md`，中英结构对齐；根目录演示资产归位 `demo/`，推广物料归位 `docs/marketing/`。
- Agent Skill 安装路径统一为 `npx skills add can4hou6joeng4/boss-skill`：CLI 的 Agent Skill 迁至独立仓库 [boss-skill](https://github.com/can4hou6joeng4/boss-skill)（仓库本身即 Skill），与 CLI 发版节奏解耦；CLI 能力与错误码继续以 `boss schema` 自描述为真源。

### Fixed
- `boss doctor` 的 `patchright_chromium` 检查改为按 patchright 自带 `browsers.json` 声明的修订版精确校验，修复"缓存存在其他版本即报 ok 但 `boss login` 启动浏览器失败"的假绿灯。
- `boss login` 浏览器内核缺失时不再误报 `CDP_UNAVAILABLE`：新增 `BROWSER_KERNEL_MISSING` 分类，`recovery_action` 修正为可执行的 `patchright install chromium`。
- 隔离简历导出默认路径测试，避免每次全量测试向仓库根目录写入临时文件。

### Removed（含 Breaking Change）
- 根目录 `SKILL.md` 迁移至独立仓库 [boss-skill](https://github.com/can4hou6joeng4/boss-skill)；**此前经 `npx skills add can4hou6joeng4/boss-agent-cli` 安装的用户，请改用 `npx skills add can4hou6joeng4/boss-skill` 获取更新**。CLI 命令行为、JSON 信封与既有错误码全部向后兼容。
- `skills/boss-resume-downloader` 招聘者简历批量同步工作流退役下线（与默认低风险合规姿态存在张力）；历史版本可在本仓库 git 历史中找到。

## [1.12.0] - 2026-06-09

### Added
- MCP server 新增 `stdio` / `sse` / `http` 三种传输模式，`boss-mcp --transport http --port 8765` 与 `boss-mcp --transport sse` 可直接启动，默认仍保持 `stdio`。
- `mcp-server/README.md` 补齐 SSE / HTTP Streaming 启动示例、默认路径和自定义路径说明。
- MCP 新增 `boss_export`：协议服务对齐 CLI `boss export`，Agent 可直接导出搜索结果为 CSV / JSON / HTML（默认脱敏 job_id/security_id/boss_name），工具计数 31 → 32。
- 增加前程无忧 / 51job（`qiancheng`）平台占位适配器，统一返回 `NOT_SUPPORTED`，为后续多平台能力矩阵保留低风险扩展入口。

### Changed
- 招聘者写操作收口到同一条 chat-tab Vue/CDP 链路：`boss hr reply` 现在用真实 WebSocket 帧校验发送成功，`boss hr request-resume` 不再需要 `--job-id`，`boss hr resume --exchange` 支持 `phone|wechat`。
- MCP 新增 `boss_hr_exchange`，并同步修正 `boss_hr_request_resume` 的输入契约为仅需 `friend_id`。
- 测试依赖 `pytest` 最低版本提升到 `9.0.3`，并同步更新锁文件，修复 Dependabot 报告的临时目录处理漏洞告警。
- ROADMAP / README / README.en 对齐当前主线事实：`#48` 已完成，智联候选者侧登录与读写链路已接通，招聘者侧仍保持显式拒绝。
- ROADMAP Week 4 招聘者侧能力评估落槌为「暂不接入」（接入条件 0/4 满足，保留 `RecruiterPlatform` 骨架待社区信号重启；详见 `docs/research/platforms/zhaopin-recruiter-evaluation.md`）。
- README 与设计文档同步前程无忧占位状态，并明确 RPA / 浏览器自动化风控边界。

### Fixed
- `boss hr chat --label-id` 兼容招聘端沟通列表接口直接返回数组的响应形态，避免成功响应被误处理为 `NETWORK_ERROR`。
- `boss interviews` 占位实现现在经 hints 如实声明能力状态（`capability=stub` + `note`），避免 Agent 把空集合占位误读为「无真实面试邀请」。
- 改善登录错误诊断，并补强登录合规与隐私清理契约。

### Tests
- 补强低风险合规、求职流程只读、智联客户端生命周期、招聘者平台生命周期、终端渲染、统计、简历命令、联系人查找、招聘端点和招聘端低风险阻断等回归契约。

### Packaging
- 显式排除本地虚拟环境、缓存和构建产物，避免本地 `.venv*` 目录污染 sdist。

## [1.11.0] - 2026-04-23

### Added
- **招聘者模式**（`--role recruiter`）— 全套招聘者 CLI 命令组：
  - `boss hr applications` — 查看/筛选候选人投递申请
  - `boss hr resume <id>` — 查看/请求候选人简历
  - `boss hr chat` — 与候选人沟通列表
  - `boss hr jobs list/online/offline` — 职位发布管理
  - `boss hr candidates` — 候选人池
- `BossRecruiterClient` 双通道客户端（httpx 低风险 + 浏览器高风险），复用 `AuthManager`/`BrowserSession`/`RequestThrottle`
- `RecruiterPlatform` ABC + `BossRecruiterPlatform` 适配器，遵循现有 Platform 抽象模式
- `api/recruiter.yaml` 招聘者端点定义（11 个 wapi 端点，Phase 0 待确认）
- `schema` 输出新增 `current_role`、`--role` 选项、`hr` 命令组、4 个招聘者错误码
- `CacheStore` 新增 `recruiter_applications`/`recruiter_jobs` 表
- 测试 998→1021（+23），覆盖客户端、平台适配器、命令、端点加载
- **ZhilianClient 内部 HTTP 客户端骨架**（Issue #140 Week 2 起步）— 新增 `src/boss_agent_cli/api/zhilian_client.py`：
  - 类结构 + `__init__(auth_manager, *, delay, cdp_url)` 签名完全对齐 `BossClient`
  - `close()` / `__enter__` / `__exit__` 资源生命周期支持
  - `atexit` 自动清理未显式关闭的实例
  - 所有 P0 方法（search_jobs / job_detail / recommend_jobs / user_info）抛 `NotImplementedError` 附 Issue #140 链接
- **`get_platform_instance` 支持按平台分发 client**（`_platform.py`）：
  - zhipin → BossClient
  - zhilian → ZhilianClient
  - 新增 `_build_client(name, ...)` helper 封装分发逻辑
- `tests/test_zhilian_client.py` 13 条契约测试覆盖类结构 / 上下文管理器 / stub 方法 / 平台路由分发

### Changed
- **`boss batch-greet` 迁移到 Platform**（Week 1c 第 3 个命令）— 从 `BossClient` 直用切换到 `get_platform_instance(ctx, auth)`，内部 `client.search_jobs` / `client.greet` 改为 `platform.search_jobs` / `platform.greet`。删除 `greet.py` 对 `BossClient` 的直接引用。
- mypy 严格白名单扩到 72（新增 `api.zhilian_client`）
- README、Agent Quickstart、Codex / Claude Code 接入文档统一到 recruiter workflow + MCP / skill 导向的对外表述
- GitHub 仓库设置补齐：自动合并、update branch、security fixes、master 分支保护增强（加入 `test (3.13)` / `typecheck` / `GitGuardian`）

## [1.10.1] - 2026-04-21

### Added
- **Week 1c 100% 完成**（PR #139）— 剩余 6 个命令迁移到 Platform 抽象：`search` / `export` / `chat_summary` / `history` / `status` / `watch`。`run_search_pipeline` 接受 `client: Any` 保持兼容，直接传 Platform 实例。
- Platform ABC 新增 `job_history(page=1)` 方法（history 命令所需）。
- **`docs/platform-abstraction.md`** — Platform 抽象设计与迁移 SOP 文档化 Week 1 全经验，包含 ABC 方法契约、迁移模板、测试 mock 位点规则、ZhilianPlatform 自证经验。

### Changed
- 至此 `commands/` 目录下除 `_platform.py`（桥接层）外**无任何命令直接引用 BossClient**。
- ZhilianPlatform stub 的 `NotImplementedError` 消息从"详见 Issue #129"更新为"追踪进度见 Issue #140"（PR #141）。

### Fixed
- CHANGELOG 中 [1.10.0] 的"14 命令 + search 留待独立 PR"描述，实际在本版本完成全量 20 命令迁移。

## [1.10.0] - 2026-04-21

### 🎯 核心里程碑：Platform 抽象架构落地（Issue #129 Week 1 全量收口）

从 v1.9.1 到 v1.10.0，本版本沿着 C → A → B 三阶段路线把 Platform 抽象从"只有骨架"推到"两个平台注册 + 14 个命令走抽象"的稳定状态，为 v2.0 多平台（智联真实现）打平底盘。

#### Platform ABC 完整设计（PR #131 / #132 / #133 / #136）
- `src/boss_agent_cli/platforms/` 新子包定义 Platform ABC + 注册表 + BossPlatform adapter
- P0 只读（`search_jobs` / `job_detail` / `recommend_jobs` / `user_info`）+ P1 写操作（`greet` / `apply`）+ P2 沟通（`friend_list`）
- ABC 扩展 P0+：`resume_baseinfo` / `resume_expect` / `deliver_list` / `job_card` / `interview_data` / `chat_history` / `friend_label` / `exchange_contact`
- ABC 统一 `__init__(client: Any)` 签名 + `with` 上下文管理器（`__enter__` / `__exit__` / `close()`）
- `Platform` / `BossPlatform` / `ZhilianPlatform` / `get_platform` / `list_platforms` 导出到 `from boss_agent_cli import ...` 公共 API 面

#### CLI 与注册表（PR #132 / #134）
- `--platform` 全局 CLI 选项（默认 `zhipin`），未知平台 exit code 2
- `~/.boss-agent/config.json` 支持 `"platform": "zhipin"` 字段持久化
- `boss schema` 输出 `current_platform` + `supported_platforms`
- Zhilian stub 接入注册表，`boss --platform zhilian schema` 端到端可用

#### 命令层迁移（PR #133 / #135 / #136 / #137，共 14 个命令）
| 命令 | PR | 使用的 Platform 方法 |
|------|-----|--------------------|
| `greet` / `apply` | #133 | `greet` / `apply` |
| `batch-greet` | #135 | `search_jobs` + `greet` |
| `interviews` / `detail` / `show` / `me` / `recommend` | #136 | P0 + P0+ 方法 |
| `chat` / `chatmsg` / `mark` / `exchange` / `pipeline` / `digest` | #137 | `friend_list` / `chat_history` / `friend_label` / `exchange_contact` / `interview_data` |

唯一留待独立 PR 的是 `search`，因为通过 `run_search_pipeline(client, ...)` 间接耦合 BossClient 签名需单独重构。

#### ZhilianPlatform 自证（PR #134）
- `src/boss_agent_cli/platforms/zhilian.py` — 智联招聘 stub 实现
- 包络适配按 `docs/research/platforms/zhaopin.md` §4 完整实现（`code==200` / `data` key / 401+403+429 映射；该研究文档未随仓库发布，故不加链接）
- P0/P1/P2 方法抛 `NotImplementedError("Week 2 待实现")`
- 27 条契约测试覆盖元信息 / 包络适配 / stub 行为 / CLI 集成

#### Research 闭环
- Issue #90 关闭（拉勾 + 智联 + 猎聘 API 调研全部归档到 `docs/research/platforms/`）

### Tests & Quality
- 测试 927 → **998**（+71 新增：25 platform ABC + 27 zhilian + 10 CLI + 5 context manager + 4 contract）
- mypy 严格模块 66 → **71**（新增 4 个 platforms 子模块 + `commands._platform`）
- mypy `--strict` 0 错误 / 80 源文件全覆盖
- 7 PR 连合（#131/#132/#133/#134/#135/#136/#137），全部 CI 7/7 绿

### Breaking Changes
**无**。命令行为、信封格式、错误码枚举、CLI 接口全部向后兼容。Platform 抽象为纯重构，新增能力均在基类默认 `NotImplementedError` 形式下兼容老代码。

## [1.9.1] - 2026-04-20

### Changed
- **🎯 mypy 严格模式全量接入达成 100%**（#124）— 本轮收尾 4 个 patchright / aiohttp 外部依赖深水区（`api/browser_client` / `auth/browser` / `bridge/client` / `bridge/daemon`），白名单 61 → **66（业务代码 100%）**
- ROADMAP v2.0「架构演进」分区 2/3 完成（mypy 严格化 ✅、类型 stubs 导出 ✅）
- 所有业务模块强制 `disallow_untyped_defs + disallow_any_generics + warn_return_any`

### 里程碑
从 R1（#86）到 R14（#124），14 轮连续推进，严格模块 0 → 66。CI `typecheck` job 阻塞式，新代码零容忍。

## [1.9.0] - 2026-04-20

### Summary
1.8.x 系列连发 19 个 patch 完成以下核心里程碑，按 SemVer 规范 bump 到 1.9.0 标记这个里程碑节点。

### 🎯 核心里程碑（从 1.8.0 到 1.9.0）

#### 严格类型体系（从 0 到 81%）
- mypy 严格类型检查模块：3 → **61** 个（81% 覆盖率）
- CLI 命令层 **32/32 = 100%**
- api / auth / cache 核心基础层全部进入严格保护
- `typecheck` CI 从 non-blocking 升级为阻塞式门禁
- Python 嵌入 API：canonical `__all__` 导出 14 个核心符号 + 16 条契约测试 + `py.typed` marker

#### 智能能力扩展
- `boss ai interview-prep` — 基于 JD 生成模拟面试题
- `boss ai chat-coach` — 基于聊天记录给出沟通技巧建议
- AI Provider 扩至 **8 家**：OpenAI / DeepSeek / Moonshot / OpenRouter / Qwen / Zhipu / SiliconFlow / Custom
- 支持 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 / GLM-4.6 等最新模型

#### 数据输出
- `boss digest --format md` — 邮件/飞书可直接发送的 Markdown 日报
- `boss stats --format html` — 自包含交互式漏斗报表

#### Agent 集成
- Cursor / Windsurf 专用接入示例（MCP 推荐 + 规则文件兜底）
- `boss schema --format openai-tools / anthropic-tools` 直出 SDK 可用格式
- `docs/integrations/ai-models.md` 推荐模型配置表

#### 工程卫生
- 测试覆盖率 80% → **85%**
- 测试数 802 → **927**
- `CONTRIBUTING.en.md` 英文贡献者指南
- research Issue 模式建立：#90（多平台适配器）+ #96（Bridge gRPC）

### 🗂️ ROADMAP 进度对齐
- v1.8.x 数据可视化分区：**3/3 ✅**
- v1.8.x 智能能力分区：**3/3 ✅**
- v1.8.x Agent 集成分区：**2/3**（剩 MCP HTTP Streaming，Issue #48 外部认领）
- v2.0 架构演进：类型 stubs 导出 ✅，mypy 严格化 81%（进行中），Bridge gRPC 调研中

## [1.8.18] - 2026-04-20

### Changed
- **严格类型检查覆盖 api/client 核心请求客户端**（#119）— 37 error → 0，白名单 60 → 61
- 用 `TYPE_CHECKING` 避免 BrowserSession / AuthManager 循环导入
- `_request` / `_browser_request` 返回值用 `cast` 修 no-any-return
- `__exit__` 精确 `TracebackType` 签名
- 连锁修复 `commands/exchange.py` 类型传播问题

### 里程碑
- api/ 层覆盖达 6/7（剩 api/browser_client，patchright 外部依赖最重）
- 严格模块累计 61 / 75（81%）

## [1.8.17] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 60 个模块**（#117）— 新增 `auth/manager` / `auth/qr_login` / `commands/chat_summary`
- auth/ 层严格覆盖达到 4/5（剩 `auth/browser` 外部依赖）
- 核心认证状态机（AuthManager）进入严格保护

## [1.8.16] - 2026-04-20

### Changed
- **严格类型检查覆盖 cache/store 模块**（#115）— 白名单 56 → 57，SQLite 存储层首次严格化
- `__enter__` / `__exit__` 补精确 `TracebackType` 类型签名
- `get_search` 返回值用 `cast("str", ...)` 修复 `no-any-return`
- 所有 SQL 参数/返回 dict 补泛型 `dict[str, Any]`

### Progress
- cache/ + api/ 两个基础层全部严格化完成
- 剩余 6 个硬骨头（外部依赖 playwright / aiohttp）：api/client / api/browser_client / auth/manager / auth/browser / bridge/daemon / bridge/client

## [1.8.15] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 56 个模块**（#113）— 新增 `api/models` / `api/throttle` / `api/endpoints` / `api/endpoints_loader` / `commands/stats`
- 所有 api/* 基础模块首次全部进入严格白名单
- SQL 聚合查询结果（`_safe_count` / `_count_since`）用 typed 中间变量避免 `no-any-return`

## [1.8.14] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 51 个模块**（#111）— 新增 8 个非 CLI 模块：`ai/config` / `ai/service` / `ai/prompts` / `resume/templates` / `resume/export` / `auth/token_store` / `auth/cookie_extract` / `main`
- `token_store.load()` / `ai/service.chat()` 使用 `cast()` 精确声明 JSON 返回类型
- `main.cli` 重命名局部变量避免 `str → Path` 类型冲突
- `ai/config.get_base_url` 显式 `str()` 包装解决 `warn_return_any`

## [1.8.13] - 2026-04-20

### Changed
- **CLI 命令层严格类型覆盖首次达 100% (32/32)**（#109）— 新增 `ai_cmd` / `resume_cmd` 两个大模块，白名单 41 → 43
- `cache/store.CacheStore.close` 补 `-> None` 类型注解（解锁 resume_cmd 对其的调用）
- `ai_cmd._call_ai` 使用 cast 精确声明 JSON 解析结果类型
- 22 个 AI + Resume 子命令签名升级为完整类型化

### 里程碑
- 从 R3 `handle_auth_errors` 装饰器解锁开始，6 轮推进（#99→#109）
- 每轮都不堆积模块，而是修系统级障碍带一批模块严格化

## [1.8.12] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 41 个模块**（#107）— 新增 `schema` / `chat_export` / `config_cmd`
- 本轮单日三连发（#103 → #105 → #107），严格类型模块从 24 → 41，**净增 17 个**

## [1.8.11] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 38 个模块**（#105）— 新增核心 CLI 命令 6 个：`search` / `greet` / `chat` / `detail` / `doctor` / `export`
- CLI 命令层严格覆盖率从 66% (21/32) 提升至 **84% (27/32)**
- `detail._detail_via_httpx` / `_detail_via_browser` 补全 BossClient / Path 类型
- `doctor.add_check` 的 `hint` 参数支持 `str | None`

## [1.8.10] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 32 个模块**（#103）— 新增 8 个 CLI 命令：`me` / `show` / `mark` / `clean` / `shortlist` / `chat_snapshot` / `preset` / `watch`
- CLI 命令层严格覆盖率从 40% (13/32) 提升至 **66% (21/32)**
- `preset` / `watch` 的参数构造器给 10 个参数逐个精确标注 `str | None`
- `chat_snapshot` 使用 `boss_agent_cli.output.Logger` 替代裸 `logger` 参数

## [1.8.9] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 24 个模块**（#101）— 本轮新增 CLI 命令层 7 个：`apply` / `chatmsg` / `digest` / `exchange` / `interviews` / `pipeline` / `status`
- 所有 7 个命令签名升级为 `def cmd(ctx: click.Context, ...) -> None`
- `pipeline._collect_pipeline_items` 返回类型精确标注为 `list[dict[str, Any]]`

## [1.8.8] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 17 个模块**（#99）— 首次覆盖 CLI 命令层：`commands/cities` / `commands/chat_utils` / `commands/history` / `commands/login` / `commands/logout` / `commands/recommend`
- `display.handle_auth_errors` 装饰器补全类型注解，解锁下游 11+ 个命令的严格化路径
- 所有目标命令的 `def cmd(ctx, ...)` 签名升级为 `def cmd(ctx: click.Context, ...) -> None`

## [1.8.7] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 11 个模块**（#97）— 新增 `chat_summary` / `search_filters` / `resume/models` / `resume/store`，所有这些模块现在强制 `disallow_untyped_defs` + `disallow_any_generics` + `warn_return_any`
- 所有白名单模块的裸 `dict` / `list` 补上泛型参数
- `search_filters._check_details_parallel` 修正 `welfare_conditions` 参数类型为 `list[tuple[str, list[str]]]`

### Added
- Issue #96「Bridge 协议 HTTP/WS → gRPC 升级调研」— 按多平台适配器（#90）的调研先行模式，为 v2.0 架构演进剩余一项锁定调研清单

## [1.8.6] - 2026-04-20

### Added
- **包级公开接口**（#94）— `boss_agent_cli.__all__` 导出 14 个核心符号（AuthManager / BossClient / CacheStore / JobItem / JobDetail / AIService / ResumeData 等），下游项目可直接 `from boss_agent_cli import X` 使用，不再需要深路径 import
- `tests/test_public_api.py` 16 条契约测试守护 public API（identity 一致性 / 异常继承 / SemVer 版本格式 / py.typed marker 存在）
- `README.md` 新增「方式三：Python 直接嵌入」章节，给出 canonical 使用示例

### Changed
- `src/boss_agent_cli/__init__.py` 补完整 docstring 说明包的公开 API 契约

## [1.8.5] - 2026-04-20

### Added
- `research` issue label + Issue #90「多平台适配器 API 调研：拉勾 / 智联 / 猎聘」— 对齐顶层设计：API 调研优先、实现 PR 必须基于调研报告
- ROADMAP「生态扩展」条目补入调研 issue 引用（#91）

### Changed
- 严格类型检查白名单从 **3 个扩到 7 个**（#92）— 新增 `digest` / `match_score` / `pipeline_state` / `index_cache` 四个 100% 覆盖率纯函数模块
- 启用的严格选项：`disallow_untyped_defs` + `disallow_any_generics` + `warn_return_any`
- 所有白名单模块的裸 `dict` / `list` 均补上泛型参数

## [1.8.4] - 2026-04-20

### Changed
- **类型检查门禁升级为阻塞式**（#88）— mypy 12 baseline errors 全部清零，CI `typecheck` job 去掉 `continue-on-error`，新代码必须零 mypy 错误才能合入 master

### Fixed
- `api/browser_client.py` × 8：patchright 类属性加 `Any` 类型注解
- `auth/manager.py`：登录方法 `token` 变量显式声明为 `dict | None`
- `bridge/daemon.py`：Popen `kwargs` 显式声明为 `dict[str, Any]`
- `commands/chat_utils.py`：`RELATION_LABELS` key 类型放宽为 `object`
- `resume/models.py`：for 循环变量 `item` 改名 `ji_item` 避免类型混淆

## [1.8.3] - 2026-04-20

### Added
- 英文版贡献者指南 `CONTRIBUTING.en.md`（#84） — 对齐中文版全量章节，并明确说明 commit message 纯中文描述约定
- 静态类型检查接入（#86）— `mypy` 依赖 + 宽松基线配置 + `typecheck` CI 非阻塞 job；`output` / `config` / `hooks` 三个纯工具模块启用严格模式（`disallow_untyped_defs`）
- `docs/integrations/ai-models.md` 作为 ROADMAP v2.0 社区建设的英文贡献者入口被纳入索引

### Changed
- `CONTRIBUTING.md` 首行补 `CONTRIBUTING.en.md` 链接
- `CONTRIBUTING` 双语版增加 mypy 本地跑法提示
- `output.py` / `hooks.py` 补齐类型注解（`emit_success` / `emit_error` / `Logger.*` / `SyncHook.__init__` / `BailHook.__init__`）

### 测试覆盖率二次冲刺（#85）
- `commands/logout.py` 86% → 100%
- `commands/show.py` 85% → ~98%
- `commands/mark.py` 86% → ~95%
- `commands/me.py` 88% → 100%
- `match_score.py` 93% → 100%
- `pipeline_state.py` 93% → 100%
- 全量测试 **893 → 911**（+18）

### Fixed
- （无 bug 修复）

## [1.8.2] - 2026-04-20

### Added
- AI Provider 扩展 4 家，覆盖主流国内外聚合入口：
  - `openrouter` — Anthropic Claude 4.7 / OpenAI GPT-5 / Google Gemini 等全家桶聚合
  - `qwen` — 通义千问 DashScope OpenAI 兼容入口
  - `zhipu` — 智谱 GLM-4.6 开放平台
  - `siliconflow` — 硅基流动聚合推理
- 新建 `docs/integrations/ai-models.md` 推荐模型与入口表，给 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 / GLM-4.6 等最新模型最短接入路径

### Changed
- `ai config` 命令 `--provider` / `--model` 帮助文案同步最新 provider 列表
- `docs/agent-hosts.md` 索引补入 AI 模型入口文档链接
- `README.md` AI 命令表下方加推荐模型引用

### Fixed
- （无 bug 修复）

### 测试覆盖率冲刺（独立主题）
- `commands/display.py` 30% → **100%**（+21 测试）
- `commands/status.py` 73% → **95%**（+2 测试）
- `commands/ai_cmd.py` 77% → **84%**（+30 测试，剩余是 ctx.exit 后的防御性死代码不做硬刷）
- `tests/test_ai_config.py` 新增 4 条 provider base_url 断言
- 整体测试 **835 → 893**（+58），覆盖率 **85% → 88%**

## [1.8.1] - 2026-04-19

### Added
- `boss digest --format md [-o <path>]` — 每日摘要 Markdown 输出，可直接贴邮件/飞书发送；核心指标表 + 新匹配 / 待跟进 / 面试三段落，空数据写「暂无」占位
- `docs/integrations/cursor.md` — Cursor Composer Agent 接入示例（MCP 推荐 + `.cursor/rules` 兜底）
- `docs/integrations/windsurf.md` — Windsurf Cascade Agent 接入示例（MCP 推荐 + `.windsurfrules` 兜底）
- `docs/agent-hosts.md` 宿主索引表补入 Cursor / Windsurf 两条
- `.gitignore` 精准忽略本地专属的社区发帖草稿（`docs/blog/linuxdo-promo.md`）
- 协议服务 `boss_digest` 工具新增 `format` / `output` 参数
- `tests/test_digest_command.py` 新增 5 条 md 输出路径测试、`tests/test_mcp_server.py` 新增 2 条 `_build_args` 测试（828 → 835，+7）

### Changed
- ROADMAP v1.8.x「数据可视化」和「Agent 集成」分区各勾选一项完成
- `digest` 命令描述同步更新说明支持 JSON / Markdown 两种输出
- `tests/test_agent_host_examples.py` meta 测试覆盖新增两份集成示例

### Fixed
- （无 bug 修复）

## [1.8.0] - 2026-04-19

### Added
- `boss ai interview-prep <jd_text>` — 基于目标职位生成模拟面试题与准备建议，支持 `--resume` 参考简历、`--count` 控制题量；返回分类（技术/行为/情景）、参考回答框架、考察点、难度、高优先级准备项
- `boss ai chat-coach <chat_text>` — 基于聊天记录诊断沟通状态并给出下一步建议，支持 `--resume`、`--style` 偏好；输出阶段判断、招聘者意图、优劣势、可直发消息模板、需避免的雷区
- 协议服务新增 `boss_ai_interview_prep` / `boss_ai_chat_coach` 两个工具（41 → 43）
- Prompt 模板新增 `INTERVIEW_PREP_PROMPT` / `CHAT_COACH_PROMPT`

### Changed
- ROADMAP v1.8.x 智能能力分区两项勾选完成
- README / capability-matrix / SKILL 同步新增两条 AI 能力
- schema 中 `ai` 命令子命令列表由 6 → 8

### Fixed
- （无 bug 修复）

## [1.7.2] - 2026-04-19

### Added
- `docs/integrations/python-sdk.md` — Python SDK 直调集成样例（OpenAI + Anthropic 两套 ~150 行可运行代码），Agent 宿主索引表同步补入口
- README 中英文版均链接认可 LINUX DO 社区

### Changed
- 测试覆盖率大幅提升：总体 **80% → 84%**（+4 点）
  - `api/client.py` 63% → **97%**（+37 测试）
  - `commands/greet.py` 65% → **97%**（+8 测试）
  - `commands/export.py` 47% → **98%**（+13 测试）
  - `auth/manager.py` 74% → **96%**（+14 测试）
  - `auth/token_store.py` 81% → **100%**（+14 测试）
  - `commands/login.py` 83% → **100%**（+6 测试）
- 总测试数 **710 → 802**（+92）

### Fixed
- 移除误跟踪的 `.coverage` artifact 并扩展 `.gitignore` 忽略覆盖率文件
- Dependabot 批量升级 5 个 GitHub Actions 版本（`checkout`/`upload-pages-artifact`/`configure-pages`/`deploy-pages`/`gh-release`）

## [1.7.1] - 2026-04-17

### Added
- `boss schema --format openai-tools` 和 `--format anthropic-tools` — 一键导出 OpenAI Functions / Claude Tool Use 兼容的 JSON Schema，免手动转换即可喂给 SDK
- `boss stats --format html -o <path>` 输出自包含交互式漏斗报表（纯 CSS + SVG，无外部 CDN）
- 协议服务文档补齐传输层章节（stdio 当前支持 / SSE 规划中）和贡献指引
- Issue / PR 模板全面升级：bug_report 强制 doctor 输出和版本号，feature_request 新增贡献意愿字段，新增 documentation 专用模板，PR 模板新增 Closes 关联和 Breaking Change 声明
- 英文版说明补齐 Troubleshooting 章节（诊断清单 / 登录 / 浏览器 / 搜索 / 错误码 / 术语表），对齐中文版
- Codecov 覆盖率追踪接入，基线 80%，每次 PR 自动上报
- 开发容器（`.devcontainer/devcontainer.json`）支持 GitHub Codespaces 一键启动
- `ROADMAP.md` + 4 个 Issue（含 2 个 good-first-issue）招募外部贡献者
- 本地提交质量门禁 `.pre-commit-config.yaml`（ruff + 通用 hooks）

### Changed
- CI 支持 `workflow_dispatch` 手动触发
- 协议服务 `awesome-mcp-servers` 投稿材料和多语言投稿模板整理

### Fixed
- 清理 `tests/test_qr_login.py` 未使用 import，修复 ruff lint 失败

## [1.7.0] - 2026-04-17

### Added
- 新增 `boss ai reply` 命令 — 基于招聘者消息生成 2-3 条回复草稿，支持简历参考和语气偏好
- 新增 `boss stats` 命令 — 投递转化漏斗统计，只读聚合打招呼/投递/候选池/监控数据
- 协议服务扩展：新增 18 个工具覆盖简历管理、智能能力、状态管理增删（23→41）
- 元测试：main.py 注册命令与 SCHEMA_DATA 对齐的防漂移校验
- 本地提交质量门禁：新增 `.pre-commit-config.yaml`（ruff check + 通用 hooks）
- 新增英文版 README（`README.en.md`），README 首屏加入语言切换
- 能力矩阵文档补齐简历管理、智能能力、数据洞察三大分区
- README 加入 CHANGELOG 导航入口

### Changed
- 能力矩阵命令总数对齐到当前状态

### Fixed
- 清理 `tests/test_qr_login.py` 未使用 import，修复 ruff lint 失败

## [1.6.0] - 2026-04-14

### Added
- 新增 `resume` 命令组 — 本地简历管理，支持初始化、列表、查看、编辑、删除、导出、导入、克隆、版本比对
- 新增 `ai` 命令组 — 智能简历优化，支持配置、JD 分析、润色、优化、建议五个子命令，覆盖 OpenAI/Claude/Gemini/通义千问/DeepSeek 多模型
- 简历数据模型、本地存储、模板渲染、多格式导出（HTML/Markdown/PDF/DOCX）
- AI 服务模块：多模型配置、密钥加密存储、提示词模板、对话补全
- 模型上下文协议服务从十一个工具扩展至二十三个，覆盖全部命令

### Changed
- 协议服务文档按功能分类并补齐全部工具说明
- 能力矩阵补齐配置管理和缓存清理命令

## [1.5.0] - 2026-04-14

### Added
- 新增 `clean` 命令 — 清理过期缓存和临时文件，支持预览和全量模式
- 模型上下文协议服务新增二十九个测试覆盖工具定义和参数构建

### Changed
- 收窄八处网络和解析模块的异常捕获为具体类型
- 统一调试协议默认地址为单一常量引用

## [1.4.0] - 2026-04-14

### Added
- 新增 `config` 命令组 — 查看、设置、重置配置项，支持类型自动推断
- 新增类型标记文件，下游项目可获得类型检查支持
- 新增版本查询选项，终端输入即可查看当前版本
- 缓存模块新增保存搜索、增量监控、投递记录、候选池四表扩展测试
- 浏览器桥接模块新增协议结构和客户端重试逻辑测试
- 测试数量从三百六十八增至四百二十七

### Changed
- 安装指引统一覆盖三种安装方式
- 能力矩阵文档按功能分类并补齐全部命令
- 清理仓库内部开发计划文档

## [1.3.0] - 2026-04-13

### Added
- 新增 `watch` 命令组 — 保存搜索条件并执行增量监控，自动标出新职位
- 新增 `pipeline` 命令 — 汇总沟通和面试数据，构建求职流水线全景视图
- 新增 `follow-up` 命令 — 筛选超时未推进的联系人，生成跟进提醒
- 新增 `apply` 命令 — 发起投递/立即沟通动作，幂等设计防止重复投递
- 新增 `shortlist` 命令组 — 管理职位候选池，支持添加/列表/移除
- 新增 `chat-summary` 命令 — 对沟通消息生成结构化摘要
- 新增 `preset` 命令组 — 管理可复用搜索预设，保存常用参数组合一键复用
- 新增 `digest` 命令 — 每日摘要，综合流水线、跟进、统计信息
- 搜索结果新增匹配分和匹配原因输出
- 快速入门文档和冒烟测试框架
- 多宿主集成示例文档（Claude Code / Codex / Shell Agent）
- 接口合约和错误码一致性测试
- 高风险链路测试覆盖补齐

### Changed
- 开源仓库元信息优化，补充英文摘要和变更记录

### Fixed
- 检测风控状态码并输出明确错误标识（此前静默失败）
- 调试协议模式复用用户上下文，规避自动化检测
- 扩展优先复用已有招聘页而非空白自动化页
- 职位详情快速通道失败时自动降级到浏览器通道（此前误报"职位不存在"）
- 搜索分页边界条件修正

## [1.2.0] - 2026-04-09

### Added
- CI 新增 ruff lint 质量门禁步骤
- CI 矩阵新增 Python 3.13 支持
- 新增 bridge/display/endpoints_loader/index_cache 四模块测试（123→182 用例）
- SKILL.md 命令速查表补全至 19 个命令

### Changed
- chat.py 拆分为 chat_export/chat_snapshot/chat_utils 三子模块（655→227 行）
- 浏览器超时从裸数字提取为命名常量
- search_filters 异常捕获从 Exception 收窄为具体类型
- client.py 根据运行平台动态设置请求头
- daemon.py 文件句柄改为 with 语句防泄漏
- 安装命令改为从 GitHub 源码安装

### Fixed
- CLAUDE.md 缩进规范、模块索引、技术栈、架构图与代码对齐
- README 配置文档补全 cdp_url/export_dir 字段
- .gitignore 排除 .trellis/.agents 目录

## [1.1.0] - 2026-04-03

### Added
- 新增 `boss me` 命令 — 获取当前登录用户的基本信息、简历、求职期望、投递记录
- 跨平台 Agent Skill 体系 — 支持 Codex / Claude Code / Gemini CLI / OpenCode / OpenClaw
- `.agents/skills/` symlink 供 Codex / OpenCode 发现 skill
- pyproject.toml 补全 authors、keywords、classifiers、urls 元数据

### Fixed
- 修复 `boss me` 命令 AuthManager 路径拼接和 emit_error 参数问题
- 消息模板标准化 — hints 补全 + 参数引用修正 + recovery_action 统一

### Changed
- SKILL.md 重构为 AgentSkills 标准格式
- skill 目录从 `skills/SKILL.md` 迁移到 `skills/boss-agent-cli/SKILL.md`

## [0.1.0] - 2026-03-20

### Added
- 核心 CLI 框架（Click）+ JSON 信封输出协议
- `boss login` — patchright 反检测浏览器扫码登录 + 本地浏览器 Cookie 自动提取
- `boss status` — 检查登录态
- `boss search` — 职位搜索（支持城市 / 薪资 / 经验 / 学历 / 规模筛选）
- `boss search --welfare` — 福利精准筛选（双休、五险一金等，逗号分隔 AND 逻辑，自动翻页）
- `boss recommend` — 基于简历的个性化职位推荐
- `boss detail` — 职位完整详情（描述、地址、招聘者信息）
- `boss greet` — 向招聘者打招呼
- `boss batch-greet` — 批量打招呼（上限 10，支持 dry-run 预览）
- `boss export` — 导出搜索结果为 CSV / JSON
- `boss cities` — 列出 40 个支持城市
- `boss schema` — 工具能力自描述 JSON（Agent 调用入口）
- Token 加密存储（Fernet + PBKDF2 机器绑定密钥）
- SQLite WAL 缓存（搜索历史 100 条上限 + 已打招呼记录）
- 高斯分布请求延迟 + 指数退避 403 重试
- GitHub Actions CI（多 OS + 多 Python 版本）
