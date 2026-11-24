# Roadmap

本文档记录 boss-agent-cli 的中长期规划。欢迎对任何方向提 Issue 或 PR。

## 已发布

v3.4.185（2026-11-24）：向导 / MCP 的 watch add 在写库前拒绝含孤立代理项的关键词 / 福利等参数，搜索 / 导出也在平台请求前按 CLI 同一口径校验关键词可编码性，不再存下永远跑不通的记录（Issue #2651）。

v3.4.184（2026-11-24）：向导 / MCP `boss_wizard` 的 `export` goal 支持 `count`，显式传入时按顶层 `boss export` 同一页数预算取满即停，`data` 增加 `requested` 与 `truncated`；不传 `count` 时保持原有导出范围（Issue #2648）。

v3.4.183（2026-11-24）：向导 / MCP `boss_wizard` 保存监控时，非字符串或纯空白的 `active` 不再按「不筛选」静默保存，改为在写库前返回参数错误（Issue #2645）。

v3.4.182（2026-11-24）：`crawl` 详情缓存不再把断点里显式 null 的 job_id 当成共享键；后台恢复的预检失败不再因还原写库失败丢掉原始错误；执行器已持锁的 queued 任务不再报排队超时（Issue #2642）。

v3.4.181（2026-11-24）：`clean --risk-lock --privacy` 在隐私清理失败时带回本地风控锁已清除的真实状态；`clean --privacy --dry-run` 不再重复统计旧快照与导出文件，预览与实际释放量一致（Issue #2639）。

v3.4.180（2026-11-24）：向导 / MCP `boss_wizard` 的投递动作拒绝非字符串 lid，在本地预约与平台调用之前按参数错误返回（Issue #2636）。

v3.4.179（2026-11-24）：向导 / MCP `boss_wizard` 的 pipeline 与 digest 在任何平台请求之前校验 days_stale / now_ts_ms，digest 的 days_stale 上限与顶层命令一致；招聘者简历动作拒绝非字符串 security_id（Issue #2631、#2633）。

v3.4.178（2026-11-24）：向导 / MCP `boss_wizard` 的职位详情在未显式传 job_id 时复用缓存值走 httpx 快速通道，并收紧 job_id / lid 的类型、空白与编码校验，与顶层 `boss detail` 同口径（Issue #2628）。

v3.4.177（2026-11-24）：向导 / MCP `boss_wizard` 的聊天记录游标拒绝显式 `max_msg_id: 0` / 负数，打招呼拒绝显式空白 `message`，求职者分页动作拒绝超过 `MAX_TRUSTED_PAGE` 的页码，三处均与顶层命令同口径（Issue #2621 / #2623 / #2625）。

v3.4.176（2026-11-24）：向导 / MCP `boss_wizard` 的招聘者动作对显式空白筛选项按 `INVALID_PARAM` 拒绝，与顶层 `hr ...` 命令同口径（Issue #2618）。

v3.4.175（2026-11-24）：MCP 参数映射不再吞掉写工具的显式空串（`boss_greet` 消息、`boss_batch_greet` 筛选项、`boss_hr_candidates` 城市 / 职位），空值由 CLI 在平台调用前按 `INVALID_PARAM` 拒绝（Issue #2615）。

v3.4.174（2026-11-24）：招聘写入口统一读取 `stop.json` 与 `state.json` 的熔断状态；持久化熔断仍打开、状态损坏或 wizard / MCP 写动作都会在派发前返回 `CIRCUIT_BREAKER_OPEN`（Issue #2612）。

v3.4.173（2026-11-23）：风控失败的向导断点不再把 `--resume` 当后继命令或直接重放；写请求结果未知的判定覆盖 run 级错误、等待输入步骤与错误码漂移（Issue #2608 / #2609）。

v3.4.172（2026-11-23）：`batch-greet` 的空筛选项与 `greet --message` 的空正文在任何平台调用前按参数错误拒绝，不再无范围群发或静默改用默认招呼语；`agent crawl` 的空城市给出准确提示（Issue #2602 / #2603 / #2604）。

v3.4.171（2026-11-23）：招聘者写请求确定未发出时释放本地预约并按可重试网络错误上报，同意简历的前置只读失败按只读契约输出；显式传空的筛选项按参数错误拒绝，不再静默改成全量筛选（Issue #2596 / #2597 / #2598）。

v3.4.170（2026-11-23）：`chat --export` 遇到单个联系人含深嵌套字段时按落盘栈深逐条降级，不再让整份导出失败并给出误导性的磁盘指引（Issue #2593）。

v3.4.169（2026-11-23）：读请求响应体解码失败按确定性的 `INTERNAL_ERROR` 上报，不再让 Agent 因可重试的网络错误反复请求（Issue #2590）。

v3.4.168（2026-11-23）：同源在途动作结果未知时，追加式队列不会再派发同一候选人的新消息；pending 保持原状等待人工核对（Issue #2586）。

v3.4.167（2026-11-23）：招聘自动化按平台真实的聊天记录结构读取会话，系统消息不再被误当成候选人回复；无法确认的候选人会话标识不再转成 `friend_id=0` 发往平台，待执行动作保持原状（Issue #2580、#2582）。

v3.4.166（2026-11-23）：招聘自动化在写入「执行中」标记前检查已有本地和共享预算目录的风控锁。锁定时仍熔断，待执行队列保持原样，已在途动作仍受重放保护（Issue #2577）。

v3.4.165（2026-11-23）：隐私清理预览会明确报告不可读子目录；递归统计只计算嵌套文件链接自身的大小；采集拒绝异常去重键并补全断点页的详情标识；聊天快照不再被日期同名目录遮蔽（Issue #2572、#2574、#2575）。

v3.4.164（2026-11-23）：风控信封的 `error.details.risk_lock.persisted` 与实际落盘一致（主锁或组目录任一写成功即报 true）；读请求确定性 4xx 不再被报成可重试的 `NETWORK_ERROR`（参数类 4xx 报 `INVALID_PARAM`，其余报 `INTERNAL_ERROR`，5xx 与 408 保持可重试）；导出 / 快照只把真正超过 4300 位、无法转字符串的整数归 null；`resume list` 与向导的简历枚举在目录不可读时按 `LOCAL_IO_ERROR` 收口，不再静默报告为空（Issue #2566、#2567、#2568、#2569）。

v3.4.163（2026-11-23）：`chat` 的分页页码钳制到可信上限、时间戳边界不再让整条命令失败；联系人身份判定在 CLI 与向导之间统一收口，`preset add` / `watch add` 与读取侧共用同一份校验；`detail` / `show` 的通道降级与标识渲染按确定性失败收口；`recommend` / `chatmsg` / `chat-summary` 的页码与数量钳制到可信上限；MCP 按向导动作区分重试指引，校验参数边界、CLI 信封与退出码一致性，在取消后回收 CLI 子进程并在启动前校验宿主追加参数；`doctor` 的浏览器缓存探测区分「不可读」与「未安装」，缓存根与 patchright 驱动同口径读取环境变量；自动化复核关联字段漂移按状态损坏拒绝、`crawl resume` 还原失败不再顶替原始错误，执行器陈旧快照不再复活已清除的停止闩锁（Issue #2542、#2543、#2545、#2546、#2548、#2549、#2550、#2552、#2553、#2555、#2556、#2558、#2559、#2562）。

v3.4.162（2026-11-23）：`agent` 的输入编码与聊天快照锁超时按真实原因收口——不可编码的 `--reason` / `--run-id` 不再被误报为状态文件损坏，并发导出争用快照锁时提示稍后重试而不是磁盘故障（Issue #2537、#2538）。

v3.4.161（2026-11-23）：请求参数的编码失败统一按输入错误收口——孤立代理项不再被误报为磁盘故障，写操作不会再留下无法清理的预约；`watch` / `preset` 坏记录的恢复指引改为可执行（Issue #2528、#2529、#2533）。

v3.4.160（2026-11-23）：向导的 AI 辅助、联系人动作与候选人简历动作补齐空白输入校验——空白提示词不再发起一次计费 AI 请求，空白联系人 / 职位标识不再发出注定失败的平台请求，均与 CLI 同口径报 `INVALID_PARAM`（Issue #2523、#2525）。

v3.4.159（2026-11-23）：缓存层按可逆方式绑定无法编码的平台标识，招呼 / 投递记录与职位描述缓存不再拖垮搜索与招呼流程（Issue #2509）。向导的 watch / search 动作与 `search --preset` 补齐输入与读取校验：空白名称 / 关键词、不可编码名称、失效参数与空白标识在发起平台请求前报 `INVALID_PARAM` / `SAVED_SEARCH_INVALID`，并给出可执行的删除重建指引（Issue #2511、#2512、#2513、#2514、#2516、#2519）。

v3.4.158（2026-11-23）：候选池导入跳过无法编码的职位标识，标签参数按 UTF-8 校验（Issue #2502、#2506）。简历命令支持文件名含非 UTF-8 字节的现有简历，按原始名称查询和清理关联（Issue #2503）。

v3.4.157（2026-11-23）：`crawl` 与 `wizard` 的 `run_id` 在入口校验可编码性，含孤立代理项的输入报 `INVALID_PARAM` 而不是带磁盘指引的 `LOCAL_IO_ERROR`（Issue #2499）。

v3.4.156（2026-11-23）：断点里不可编码的内部 `run_id` 不再拖垮向导状态 / 列表 / 停止控制路径，沟通导出的 md / html 返回值与 JSON 同口径归一孤立代理字符（Issue #2495、#2496）。

v3.4.155（2026-11-23）：采集导出在替换前复核目标仍是普通文件（链接 / FIFO 拒绝替换），备份不再预建空文件、目标消失时按首次导出安装完整产物，XLSX 容器字段的 `str()` 溢出按缺失处理（Issue #2490、#2491、#2492）。

v3.4.154（2026-11-23）：采集附着浏览器前核对配置目录归属；向导与 MCP 的 `crawl_stop` / `crawl_status` 按执行器状态结算与回报，待处理的停止请求不再被 resume 引导清除；本地断点空值与向导输入键在读取 / 入口边界 fail-closed（Issue #2464、#2478、#2479、#2482、#2484、#2485）。

v3.4.153（2026-11-23）：职位列表监听按关键词、城市和页码匹配响应。`crawl status` 用同一份任务记录生成状态、游标和执行器提示（Issue #2464、#2474）。

v3.4.152（2026-11-23）：招聘自动化在人工核对完成前跳过相关会话的写入，并给出按发送结果处置的指引。采集状态识别已退出的执行器，构造失败会保存断点，并发停止保留最新游标与终态。恢复深层断点前检查剩余请求预算（Issue #2452、#2463、#2465、#2466、#2470）。

v3.4.151（2026-11-22）：写请求不再跟随 HTTP 重定向、首跳 3xx 按结果未知收口；浏览器通道区分预派发与已派发失败，并剥离平台响应中的内部传输标记；wizard 的单请求写步骤在派发前失败可安全恢复，断点读取与停止信号不再被字段漂移影响；平台兜底错误分类风控优先、招聘者数值转换、空简历回落与分页上限提示收口；API YAML 加载期形状校验与 response_codes 整数归一，风控码 / 限流码以配置为单一真源；自动化队列阶段判定与派发前预检对齐会话证据，MCP 演练工具按只读判定；导出 JSON 的孤立代理项改为字面转义，`-o` 预检在 macOS 上提前拒绝非法 UTF-8 文件名，CSV 前导控制字符纳入公式前缀判定；请求等待上限默认值与节流间隔对齐，连续请求不再被刻意节流自身误杀；crawl 职位监听器启动失败改走页面内同源请求（Issue #2424、#2425、#2427、#2428、#2430、#2431、#2433、#2434、#2437–#2439、#2441–#2443、#2446–#2448、#2450、#2451、#2454、#2457–#2459、#2461）。
v3.4.150（2026-11-22）：wizard 非终态步骤带完成结果的断点拒绝恢复、ID 字段与不可 JSON 序列化输入的入口校验、watch 福利筛选项的逐元素类型校验（Issue #2418–#2421）。
v3.4.149（2026-11-22）：浏览器写前失败证据与有界扫描、采集分页与恢复参数校验、导出/快照超长整数降级、聊天统计同源、登录风控锁复查与写请求重定向分类，automation 熔断快照与 wizard 断点标记的缺字段收口，MCP 数组取值错误信封上限与 dry-run 只读判定，采集导出的大整数 / 非对象行与 shortlist 主键归一，crawl 前台/后台恢复指引与执行锁占用时的显式参数收口，以及沟通列表容器漂移时的首屏报错（Issue #2379–#2403、#2405–#2408、#2410–#2412、#2414–#2415）。
v3.4.148（2026-11-22）：登录错误分类的 401 上下文、风控优先级与凭证脱敏，MCP action 默认值与 shortlist 互斥口径、availability 入参收口，导出边界与 wizard 状态一致性，crawl / 请求预算 / 简历与本地模型导入的边界收口，以及自动化决策证据（改期时间、prior 时间戳、已交换语义）、本地 AI 草稿编码、wizard 渲染与产物探测、NUL 路径归一的加固（Issue #2335–#2376）。
v3.4.147（2026-11-21）：候选人写动作的预约释放与网络错误口径收口，5xx 不再触发重发；职位字段归一（`job_url` / `raw_job_type` / wizard 详情）、doctor 浏览器内核探测、聊天导出边界、采集导出竞态与 crawl 缓存错误分类，以及面试线索 CSV 与 automation 序列化错误分类（Issue #2315–#2332）。
v3.4.146（2026-11-21）：修复登录失败分类与 cURL 导入边界：确定性内部 / 本地错误不再被报成可重试的网络故障，限流、代理配置与本地 IO 保留专用错误契约，文本启发式按词边界匹配，cURL 文件有界读取并兼容 BOM / 大小写（Issue #2307–#2313）。
v3.4.145（2026-11-21）：修复候选人写请求派发前失败后预约被永久占用、结果未知被报成可重试错误，以及 wizard 在这种失败后拒绝 `--resume` 的问题（Issue #2296–#2298）。
v3.4.144（2026-11-20）：新增 `boss chatmsg --friend-uid` 直读消息；修复出口探测 / 代理 / stoken / 平台错误文案边界、`-o` 未知用户路径与索引快照的一次性读取。
v3.4.143（2026-11-20）：新增 `boss chat --raw` 保留原始联系人对象；修复请求预算库故障的错误分类、会话完整性判定与导出中断回滚。
v3.4.142（2026-11-20）：修复外部 eval 的评分契约、维护脚本的门禁退出码与诊断、响应 Cookie 合并的写请求边界、预设与 watch 名称的错误分类。
v3.4.141（2026-11-20）：修复终端外部文本转义的跨字段组合与向导渲染边界、AI 端点清除与校验、本地模型导入的目录冲突。
v3.4.140（2026-11-20）：修复登录态校验的限流与风控判定、采集恢复断点竞态与交接窗口控制、TTY 渲染的终端控制字符清洗与形状漂移。
v3.4.139（2026-11-20）：修复采集断点竞态、自动化重复派发、候选池与快照边界、非 UTF-8 终端输出与 API 客户端错误分类。
v3.4.138（2026-11-20）：修复采集控制路径、导出路径预检、清洗函数边界和向导空输入指引。
v3.4.137（2026-11-20）：修复自动化队列回写、MCP 参数契约、聊天快照完整性和向导断点恢复。
v3.4.136（2026-11-20）：审计批次收口——导出函数边界防御、本地缓存 BLOB 存储类漂移与装饰器错误分类、采集 Cookie 失败原因脱敏、crawl 路径解析的符号链接环收口（Issue #2162–#2169）。
v3.4.135（2026-11-20）：HTTP 与风控边界收口——同一次查询只刷新一次登录态、写请求解码失败保留结果未知、符号链接环不再替换风控异常或让启动期 traceback、读请求错误文本与文本脱敏不再携带 stoken、组预算目录配置错误按参数错误拒绝（Issue #2151–#2159）。
- ✅ v3.4.134（2026-11-20）：审计批次收口——登录错误分类、向导写入与恢复、缓存记录校验、摘要与职位导出、简历关联、收藏与职位去重（Issue #2114–#2149）。
- ✅ v3.4.133（2026-11-20）：审计批次收口——登录态 salt 并发创建等待、agent --limit 约束本轮派发会话数、wizard 断点校验 error.code、doctor 浏览器缓存可读性与 salt 符号链接判定、请求租约本地错误分类、crawl 导出非有限浮点归一、stats HTML 代理字符归一、chat 导出身份与快照基线的 fail-closed（Issue #2099–#2111）。
- ✅ v3.4.132（2026-11-20）：登录会话完整性收口——stoken 刷新拒绝旧值、限流探测不再触发强制重登、探针页风控上抛、扫码轮询错误立即传播；crawl 顺序翻页的预算与租约只按真实滚动请求计数（Issue #2091–#2095）。
- ✅ v3.4.131（2026-11-19）：MCP server 的默认 boss 入口在模块加载时按安装位置解析，嵌入调用与测试不再依赖 PATH 上的 boss（Issue #2088）。
- ✅ v3.4.130（2026-11-19）：简历下载与 crawl 导出的路径状态判定改用 lstat，元数据错误不再被吞成「不存在」而绕开覆盖保护与备份（Issue #2083–#2085）。
- ✅ v3.4.129（2026-11-19）：本地 JSON 序列化失败按不可重试的本地 IO 分类；私有原子写与 crawl 导出的临时名截断目标名，接近 NAME_MAX 的合法文件名不再 ENAMETOOLONG（Issue #2078–#2080）。
- ✅ v3.4.128（2026-11-19）：联系人稳定身份与 job_card 文本对漂移值 fail-closed；招聘者分页钳制、位置参数重放与缺失参数提示；automation 队列派发前字段校验与 JSONL 写入完整性；后台 crawl 恢复在派发 worker 前校验 --pages（Issue #2063–#2075）。
- ✅ v3.4.127（2026-11-19）：导出单元格与聊天快照、automation 队列门控、MCP 筛选空串与请求估算、watch/history 提示口径收口；crawl 断点身份与页码边界、wizard plan 归一化与派发前记录校验按同一失败契约处理（Issue #2011–#2035）。
- ✅ v3.4.126（2026-11-19）：wizard 断点落盘、AI 配置、招聘者记录与 watch/stats 写回收口类型漂移与半截事务；聊天 / 职位导出、doctor/clean、batch-greet 与 history 分页按错误信封契约归一；控制信号不再丢失写前标记，搜索 query 增加类型校验（Issue #1963–#2009）。
- ✅ v3.4.125（2026-11-19）：automation 决策回归转录证据、MCP 显式空值与参数过长、AI 密钥校验与错误脱敏、wizard 恢复重放阻断收口（Issue #1941–#1960）。
- ✅ v3.4.124（2026-11-19）：缓存读取、输出脱敏与风控锁记录收口类型漂移；聊天导出完整性、职位导出单元格与 crawl 导出回滚按同一归一规则；请求租约、代理解析、automation 状态/锁与 crawler Hook/JSON 解析在失败路径保留确定性错误与旧产物（Issue #1878–#1938）。
- ✅ v3.4.123（2026-11-19）：`request-resume` 补停止开关与未知结果保护；导出、聊天快照与采集产物校验完整性，`crawl resume` 不再指向缺失文件；MCP 超时估算、合规映射与参数边界对齐 CLI；自动化队列拒绝类型漂移，代理与节流按 https / NO_PROXY 语义对齐 httpx（Issue #1822–#1875）。
- ✅ v3.4.122（2026-11-19）：`exchange` 与 `show` 只认字符串标识，`batch-greet` 的预演后继命令与写请求不再携带类型漂移的字段，结果未知的招呼保留预约并停止批次；`greet` / `detail` 拒绝纯空白参数（Issue #1812–#1818）。
- ✅ v3.4.121（2026-11-19）：`resume` 后继命令对以 `-` 开头的名字与 ID 前置 `--`；`resume edit` 拒绝系统托管字段且按别名编辑不再改名；`resume delete` 预检目录路径并清理真实名称的关联；`favorites` 与简历解析不再把类型漂移的字段写成 Python repr（Issue #1803–#1808）。
- ✅ v3.4.120（2026-11-19）：`watch` / `shortlist` 的后继命令对以 `-` 开头的名字前置 `--`；存量 watch 的空关键词报参数无效；`shortlist` 的坏 `tags` 行不再拖垮整个列表；`watch run` 拒绝纯空白名字（Issue #1797–#1800）。
- ✅ v3.4.119（2026-11-19）：`chat --export` 的完整采集替换当日快照，同日重跑即报已消失的联系人；Markdown 导出转义漂移成列表 / 对象的单元格；CSV 把缺失值写成空单元格并保留 `-` 占位符；「我主动」未读数不再计入状态未知的联系人（Issue #1791–#1794）。
- ✅ v3.4.118（2026-11-19）：wizard 的 `crawl_stop` 拒绝已结束的任务；`stats` / `digest` 的 `-o` 拒绝纯空白路径（Issue #1786 #1788）。
- ✅ v3.4.117（2026-11-19）：`ai config --base-url` 拒绝无法解析的主机 / 端口；脱敏覆盖 URL 里的口令；AI 服务以 HTTP 200 返回的错误对象带出说明与恢复建议；`hr greet` 识别标记类型漂移的权益拦截页；招聘者命令拒绝空 ID；`hr jobs` 后继命令对职位 ID 加引用（Issue #1778–#1783）。
- ✅ v3.4.116（2026-11-19）：恢复 crawl 任务时状态先落库，恢复窗口内的停止请求不再被拒；wizard `crawl_start` 拒绝与断点不一致的 `query` / `city`；损坏结果行的后继命令使用真实 run_id；`openai-tools` / `anthropic-tools` 导出的参数 schema 顶层不再带组合与条件关键字；`boss_wizard` 声明 run 的必填字段（Issue #1771–#1775）。
- ✅ v3.4.115（2026-11-19）：自由文本脱敏覆盖 `access_token=` / `x-api-key=` 等复合凭据键；`--exit-codes` 预扫描在 `--` 处停止；以 `-` 开头的「打开文件」提示路径加 `./` 前缀；含 Windows 非法字符的简历名改用可移植文件名并兼容旧文件（Issue #1765–#1768）。
- ✅ v3.4.114（2026-11-19）：沟通默认导出路径在认证和快照前预检；共享原子写入器在临时文件写入前后检查目标，FIFO / 设备等特殊文件不再被静默替换（Issue #1760、#1762）。
- ✅ v3.4.113（2026-11-18）：本地写入边界继续收口——`agent resume`、请求租约、采集导出与简历/沟通默认导出路径统一拒绝符号链接 / FIFO / 特殊文件，不再静默替换用户文件或写到数据目录之外（Issue #1751、#1753、#1755、#1757）。
- ✅ v3.4.112（2026-11-18）：automation 写入侧统一拒绝符号链接——队列追加不再写穿链接目标，state / 队列整文件回写不再静默替换链接，面试线索表也不再把候选人信息追加到数据目录之外（Issue #1746、#1748）。
- ✅ v3.4.111（2026-11-18）：SQLite 打开前统一拒绝符号链接——缓存库、请求节流库、`stats` 与 automation 锁库不再跟随链接写穿数据目录；automation 队列追加对 FIFO / 悬空链接 fail-closed，`agent run` 不再在动作执行后挂死（Issue #1739、#1741、#1743）。
- ✅ v3.4.110（2026-11-18）：本地路径状态 fail-closed 扩展到 FIFO / 设备族——automation 队列、聊天快照、索引缓存、独占文件锁与 crawl Hook 目录在特殊文件上不再永久阻塞，`hr download-resume` 的 `--output` 也在平台请求前预检（Issue #1726、#1728、#1730、#1732、#1734、#1736）。
- ✅ v3.4.109（2026-11-18）：本地 SQLite 错误分类与 `-o` 预检覆盖——`stats` 遇到锁冲突 / 磁盘错误不再返回全零报表，`request-budget.sqlite3` 只读 / 磁盘满不再被误判成库损坏并提示删库，`digest` 的 `-o` 在登录与平台请求前预检、`digest` / `stats` / `resume export` 不再把 FIFO / 设备目标原子替换成普通文件（Issue #1718、#1721、#1723）。

- ✅ v3.4.108（2026-11-18）：`clean` 非隐私清理、`ai @文件`、`resume import`、`stats` 与 `-o` 预检统一按路径状态 fail-closed，路径元数据不可读或文件为非普通文件时不再静默跳过、误报成功或阻塞读取（Issue #1712、#1714、#1716）。

- ✅ v3.4.107（2026-11-18）：本地模型重导入先复制到临时路径再原子替换，失败不再丢失旧副本；AI 配置损坏时写入方拒绝用默认值覆盖；`session.enc` / `api_key.enc` 路径状态不可读时不再误报未登录 / 未配置（Issue #1705、#1706、#1709）。

- ✅ v3.4.106（2026-11-18）：本地状态、配置与登记表读取统一 fail-closed：停止标记、风险锁、配置、简历、聊天快照、面试线索 CSV、共享 salt 与模型登记表在路径元数据不可读、非普通文件或空 salt 时不再被当成缺失，也不会覆盖已有历史（Issue #1684、#1686、#1688、#1690、#1692、#1694、#1695、#1698、#1699、#1700）。

- ✅ v3.4.105（2026-11-18）：停止标记与状态文件的路径状态读取统一 fail-closed，权限错误、悬空链接与非普通文件不再被当成「标记缺失」或空状态（Issue #1680、#1682）。

- ✅ v3.4.104（2026-11-18）：招聘自动化的停止标记读取与显式恢复统一按路径类型 fail-closed，`stop.json` 为目录或符号链接时不再绕过停止开关或在恢复中途崩溃；CHANGELOG 增加版本顺序结构测试（Issue #1675–#1676、#1678）。

- ✅ v3.4.103（2026-11-18）：`hr download-resume` 在取凭据与平台请求前校验 `--output` 的空值和扩展名，空值不再被当成当前目录（Issue #1672）。

- ✅ v3.4.102（2026-11-18）：自动化统计与搜索缓存统一按校验后的状态计数与解析，坏队列与列类型漂移不再伪装成正常结果（Issue #1667–#1668）。

- ✅ v3.4.101（2026-11-18）：自由文本与 TTY 真人提示的脱敏口径统一，中文紧邻凭据键与所有 Authorization 方案不再泄漏密钥（Issue #1662–#1664）。

- ✅ v3.4.100（2026-11-18）：第七轮导出与渲染审计——导出产物的非有限数与非法字符、跨页去重、安全页域名、stoken Cookie 域名、CDP 端点复用、在途标记与简历 PDF 外部请求均已收口（Issue #1653–#1659）。
- ✅ v3.4.99（2026-11-18）：第六轮子系统审计——`stop.json` 缺少 `open` 与平台 `code=false` 不再被误判为「未停止 / 成功」，automation 整数预算与向导 `pages` 拒绝小数和布尔值（Issue #1598–#1601）。
- ✅ v3.4.91（2026-11-18）：第五轮子系统审计——`agent run --dry-run --resume` 不再解除操作者停止闩锁；wizard 错误元数据漂移在读取边界 fail-closed；MCP 校验条件必填字符串、数值上下界与数组元素长度，并封顶 wizard 宿主等待（Issue #1591–#1595）。
- ✅ v3.4.90（2026-11-18）：第四轮子系统审计——automation 按勾选项扫描并独立限制每个 tab 的会话数（Issue #1581）；wizard 与 CLI 同口径拒绝空白采集关键词（Issue #1582）；超大锁 pid 与空 `securityId` 不再伪造成功或可重试状态（Issue #1583、#1584）；`wizard --stop` 区分已完成与不存在，坏 Hook 目录统一按本地状态错误收口（Issue #1585、#1586）。
- ✅ v3.4.88（2026-11-17）：显式参数与 schema 说明继续收口——候选池 `compare --tag ""` 与 `annotate` 的空标签、空操作在读取缓存之前返回 `INVALID_PARAM`（Issue #1565）；`watch run` / `crawl` / `agent crawl` 的 schema 补上互斥、必填与空串约束（Issue #1566）；`agent train` 的 `--dry-run` 与 `--live` 改为独立标志并拒绝同时给出（Issue #1568）；`search` 的显式关键词不再被预设覆盖（Issue #1569）；`clean` / `export` / `search` / `hr resume` / `resume init` / `crawl configure` 的六处描述按实现修正（Issue #1570）。
- ✅ v3.4.87（2026-11-17）：显式参数不再被静默丢弃——`agent crawl --run-id` 拒绝只对新采集有意义的 `--city` / `--pages` / `--with-detail` 与显式空值（Issue #1558）；`crawl run` / `start` 的 `--hook-dir` 缺 `screenshot-full` 或为空时在创建任务之前拒绝（Issue #1559）；`--chrome-path ""` 不再被归一成当前目录写入配置（Issue #1560）；`watch run` 的点名与 `--all` 互斥，不再静默运行全部（Issue #1561）。
- ✅ v3.4.86（2026-11-17）：采集运行环境与本地并发的错误分类——采集浏览器启动失败按确定性本地失败返回 `CRAWL_UNAVAILABLE` 并保存断点（Issue #1553）；登录态刷新锁改用内核文件锁，等待超时不再误删他人的锁、按可重试的共享等待超时返回（Issue #1554）；出口与声明的语言/时区矛盾统一返回 `EGRESS_CONTRADICTION`（Issue #1555）；共享请求预算等待超时落成 `throttle_stopped`，不再提示新建任务（Issue #1556）。
- ✅ v3.4.85（2026-11-17）：`stats` / `digest` 的 `-o` 在默认 json 下被静默忽略，现在返回 `INVALID_PARAM`，工具 schema 同步声明格式约束（Issue #1546）；`clean` 把 SQLite 锁冲突与只读 / 磁盘错误分别归为 `CACHE_BUSY` / `LOCAL_IO_ERROR`，不再误导删库（Issue #1547）；`clean --risk-lock` 不再被损坏的缓存库阻断（Issue #1548）；联系人分页上限按不可重试上报（Issue #1549）；`detail` 的未识别响应按只读归一为可重试 `NETWORK_ERROR`（Issue #1550）。
- ✅ v3.4.84（2026-11-17）：wizard 的 crawl 恢复改为惰性预检——已完成任务与写错的 `run_id` 不再先要求登录或安装采集依赖，与 CLI `crawl resume` 口径一致（Issue #1540）；`resume export` 的显式空 `-o` 返回 `INVALID_PARAM`、默认 html/pdf 输出名与存储层共用文件名清洗（Issue #1541 / #1542）；`BROWSER_KERNEL_MISSING` 与 `CRAWL_UNAVAILABLE` 不再进入 wizard 自动重试（Issue #1543）。
- ✅ v3.4.83（2026-11-17）：配置损坏改用独立错误码 `CONFIG_INVALID` 并不在同一轮自动重试（Issue #1525）；工具契约与 native schema 对齐——MCP 工具补枚举取值域、条件必填、required 字符串 `minLength` 与 CLI 默认值，`ai cover-letter` / `resume-optimize` 同时传 `--jd` 与 `--job-id` 不再静默覆盖，`agent` 的 availability 说明适配器键语义，新增只读工具 `boss_schema`（Issue #1528–#1536）。
- ✅ v3.4.82（2026-11-17）：招聘自动化的队列与扫描路径共享 `state.json` 在途标记，成功先落盘再推进队列，未通知的熔断标记下一轮重开（Issue #1514–#1517）；错误码契约审计——只读命令的未识别响应归一为可重试的 `NETWORK_ERROR`、`agent run` 熔断改报 `CIRCUIT_BREAKER_OPEN`、wizard 缺失联系人不再自动重试（Issue #1519–#1521）；风控 hint 按 Agent 命令与真人指引分通道输出，MCP 只读白名单补齐，agent 恢复命令保留角色（Issue #1522–#1524）。
- ✅ v3.4.81（2026-11-17）：清理与体检的文件系统边界收紧——`clean` 跳过目录符号链接并在失败时带回部分结果、`doctor` 写探针改用独占临时文件（Issue #1497 #1498）；AI / 聊天 / 导出拒绝显式空值与不安全文件名，新增 `--clear-api-key`（Issue #1499–#1507）；SQLite 写入与旧库迁移并发安全，`watch` 命中写入幂等（Issue #1509 #1510）；求职者 greet/apply 发送前原子预约、automation 派发前落盘在途标记，未知结果不再重发（Issue #1508 #1511）。
- ✅ v3.4.80（2026-11-17）：`batch-greet` 拒绝空查询并收紧数量上限（Issue #1490 #1494）；MCP 子进程超时按 argv 估算，批打招呼中断输出已完成的部分结果（Issue #1491 #1492）；MCP action 条件参数按允许键矩阵校验，不再静默丢弃（Issue #1493）。
- ✅ v3.4.79（2026-11-17）：auth 登录态落盘的本地 IO 错误不再伪装成刷新或 CDP 失败（Issue #1479–#1480）；wizard 中断保留未确认写标记、空步骤断点拒绝（Issue #1478 #1483）；crawler 缓存忙、输出目录、配置路径与 Hook 状态校验（Issue #1477 #1481 #1482 #1484）；风险锁写盘编码与无 host 代理拒绝（Issue #1487 #1486）；招聘者聊天保留平台错误码（Issue #1485）。
- ✅ v3.4.78（2026-11-17）：本地状态错误分类、删除顺序、候选池批量导入主键冲突与 favorites 形状漂移（Issue #1464–#1468）；automation 发送前异常还原 pending、风控闩锁写入不覆盖原始异常（Issue #1469–#1470）；MCP 空 `--data-dir`、`friend_ids` 下限、登录提示与深层信封（Issue #1471–#1474）。
- ✅ v3.4.77（2026-11-17）：扫码空凭证、登录态本地保存失败、CDP 不可达、只读导航超时与聊天页 code 37 按真实结果分类（Issue #1454–#1458）；`--dry-run` 不再落熔断闩锁（Issue #1459）；`crawl resume` 坏 Hook 目录按本地状态失败上报（Issue #1460）；fixture 评测允许在 Agent 会话内运行，发布节奏改为批量。
- ✅ v3.4.76（2026-11-17）：横杠位置参数 argv 改写三类错位（Issue #1447–#1449）；坏 `operating_mode` 启动崩溃与采集浏览器 UA 注入（Issue #1450、#1413）；联系人分页上限提示与恢复指引同口径（Issue #1451）。
- ✅ v3.4.75（2026-11-17）：CDP 探测与连接失败清理（Issue #1421、#1425）；预算时间戳自愈与代理端口 / 聊天页签兜底（Issue #1422–#1424）；AI 配置字符串项与 base_url 校验（Issue #1428、#1431）；简历模板与导出边界（Issue #1429、#1434、#1435）；招聘者错误码兜底与匹配字段归一（Issue #1430、#1432）；模型登记表保留名（Issue #1433）；mark / wizard 超长数字输入、friendSource 漂移、resume init 模板与跨平台打开提示（Issue #1437–#1442）。
- ✅ v3.4.74（2026-11-17）：采集 Cookie 注入失败不再误写风险锁、resume 的 Hook / 登录前置检查和导出目录边界（Issue #1405、#1408、#1409、#1412）；automation 队列与状态 fail-closed、agent crawl 先校验简历与风险锁优先级、TTY 列键（Issue #1406、#1407、#1410、#1411、#1419）。
- ✅ v3.4.73（2026-11-17）：wizard 状态校验、平台错误码保留、福利条件归一与超时 / 状态面板边界（Issue #1382–#1386）；登录风控信封与 doctor 登录态脱敏（Issue #1390、#1393）；严格 JSON 出口、MCP 数值边界与 Rich 标题转义（Issue #1391、#1392、#1399）；本地写分类、CSV 行尾、联系人回退与薪资日志（Issue #1400–#1402、#1394）。
- ✅ v3.4.72（2026-11-17）：wizard 缓存库损坏语义（Issue #1362）；简历行漂移与 agent / ai 错误语义（Issue #1366、#1369、#1370）；AI 与平台 / 认证响应深嵌套递归错误（Issue #1365、#1368、#1378）；组预算配置校验（Issue #1367）；MCP 响应代理字符与空标签 / 空路径边界（Issue #1375–#1377）。
- ✅ v3.4.71（2026-11-17）：crawl 翻页 / 导出 / profile 路径与 hook 预检边界（Issue #1345–#1349）；缓存游标坏页统一为 `CACHE_DATABASE_INVALID`（Issue #1350）；联系人身份与查找上限语义（Issue #1351–#1353）；HTML 导出空值、符号链接输出与快照并发（Issue #1354–#1356）。
- ✅ v3.4.70（2026-11-17）：搜索预筛容忍字段类型漂移（Issue #1335）；请求预算库损坏按本地 IO 错误上报（Issue #1336）；automation 队列坏状态与非 UTF-8 字节不再静默变空（Issue #1337）；watch / wizard 运行存量搜索参数时重新校验（Issue #1338）。
- ✅ v3.4.69（2026-11-17）：welfare 筛选跳过 `welfareList` 坏元素（Issue #1329）；wizard 输入 / 配置 / 导出路径边界前置校验（Issue #1330）；wizard 简历损坏与联系人查找上限的错误语义（Issue #1331）。
- ✅ v3.4.68（2026-11-17）：MCP 显式空串原样下发（Issue #1324）；crawl 断点读取补齐范围、枚举与文本校验（Issue #1325）；辅助工作流不再依赖 `pull_request_target`（Issue #1322）。
- ✅ v3.4.67（2026-11-17）：chat 家族三处字段漂移按缺失处理（Issue #1317）；搜索缓存元素与 `browsers.json` 形状漂移按不可用处理（Issue #1318）。
- ✅ v3.4.66（2026-11-17）：向导 `friendSource` 字段漂移按缺失处理（Issue #1314）；ROADMAP 已发布列表按版本独立成行（Issue #1312）。
- ✅ v3.4.65（2026-11-17）：自动化锁损坏按状态错误上报与零派发冲突语义（Issue #1304、#1307）；空 stoken 刷新失败（Issue #1305）；非 ASCII 数字串判定（Issue #1306）。
- ✅ v3.4.64（2026-11-17）：孤立代理字符收口（Issue #1294）；越界时间戳、pipeline 混合类型排序与 `--now-ts-ms 0`（Issue #1295、#1297、#1298）；HTML 导出非字符串字段（Issue #1296）。
- ✅ v3.4.63（2026-11-16）：向导采集动作对损坏记录 / 导出失败 / 配置目录路径不可用按确定性错误上报（Issue #1286、#1287、#1290）；`OverflowError` 边界收口（Issue #1288）；`CRAWL_PROFILE_BUSY` 提示命令补 `<run_id>` 占位（Issue #1289）。
- ✅ v3.4.62（2026-11-16）：`CRAWL_PROFILE_BUSY` 在 `agent crawl` / 向导路径不再被吞成 `stopped`（Issue #1274）；安全页检查识别 DrissionPage 的 `NoneElement`（Issue #1276）；断点恢复校验 `chrome_path` 类型（Issue #1278）；采集风险停止时提示风险锁未写盘（Issue #1281）；采集清理步骤各自兜底不再互相阻断（Issue #1277）；风险锁增加 cache 后备位置，主文件不可写时新进程仍能读到阻断（Issue #1270）。
- ✅ v3.4.61（2026-11-16）：并发 crawl 共用配置目录时返回 `CRAWL_PROFILE_BUSY` 且不再删除运行中 Chrome 的 `SingletonLock`（Issue #1266）；风险锁写盘失败时风控信封明确「新进程不再受保护」（Issue #1270）。
- ✅ v3.4.60（2026-11-16）：`agent run` / `agent train` 执行周期内控制锁冲突改为 `LOCAL_STATE_WRITE_FAILED`（动作可能已发出、禁止自动重发），dry-run 与 `--resume` 入口保持 `AUTOMATION_BUSY`（Issue #1267）。
- ✅ v3.4.59（2026-11-16）：`agent run` / `agent train` 被 Ctrl+C 中断时输出 `CIRCUIT_BREAKER_OPEN` 信封而不是空 stdout，恢复指引为核对后 `--resume`（Issue #1262）。
- ✅ v3.4.58（2026-11-16）：`agent run --dry-run` 遇到遗留的 `executing` 行只报告真实运行会转 `verification-required`，不再打开熔断或改写队列（Issue #1257）；`agent crawl` 对损坏的本地 run 记录返回 `CACHE_RECORD_INVALID` 并指向重建任务（Issue #1259）。
- ✅ v3.4.57（2026-11-16）：automation 执行周期持有跨进程执行锁，第二个 executor 在发出任何请求前返回 `AUTOMATION_RUN_BUSY`，队列在途标记不再被并发回写覆盖（Issue #1253）。
- ✅ v3.4.56（2026-11-16）：automation 控制锁冲突返回新登记的 `AUTOMATION_BUSY` 并指向真正的锁文件，不再误报 `CACHE_BUSY`（Issue #1251）。
- ✅ v3.4.55（2026-11-16）：复核批准改为先入队再标记复核项。入队失败可重试，两步之间失败按 `approved_review_id` 去重，已批准动作不再丢失（Issue #1248）。
- ✅ v3.4.54（2026-11-16）：automation 待执行动作先落盘 `executing` 再派发。中断后不再被 `--resume` 重发，下一轮转 `verification-required` 并打开熔断；`request_sent=false` 的预算停止仍可恢复 `pending`（Issue #1245）。
- ✅ v3.4.53（2026-11-16）：熔断 `open` 字段只接受布尔。非布尔值在 `stop.json` 中 fail-closed、在 `state.json` 中报状态损坏，`SafetyGuard` 与 `hr location` 的读取口径一致（Issue #1242）。
- ✅ v3.4.52（2026-11-16）：收口 crawl 停止与导出的状态机。ambient 控制信号按错误码落成 `workflow_stopped` / `timeout_stopped`，最后一页导出先于 `completed` 落库，导出失败映射 `LOCAL_IO_ERROR`，stop / 预算用尽只指引只读的 `crawl status`（Issue #1238）。
- ✅ v3.4.51（2026-11-16）：收口 wizard `requested_steps` 空数组语义与 MCP argv 文本边界。空数组不再折叠为全量步骤，NUL / 孤立代理项在派生 CLI 前以 `INVALID_PARAM` 拒绝（Issue #1236）。
- ✅ v3.4.50（2026-11-16）：收口平台载荷的容器漂移。`zpData` 为非对象真值时，wizard `last_messages` 按空页继续、`hr download-resume` 不再在附件落盘后崩溃、`hr jobs detail` 归一为空对象、`favorites list` 返回可重试的 `NETWORK_ERROR`（Issue #1233）。
- ✅ v3.4.49（2026-11-16）：本地数值转换的溢出边界一次收口。合法大整数 / `1e400` 不再让搜索缓存、启动期配置校验、automation / crawl / AI 配置与本地模型输出落成 `INTERNAL_ERROR` 或可重试的 `NETWORK_ERROR`（Issue #1230）。
- ✅ v3.4.48（2026-11-16）：本地 JSON 读取补齐超长整数与深嵌套两类确定性解析异常；熔断 latch 读不出来时 fail-closed，`agent stats` / `resume show` 返回契约错误码而不是 traceback（Issue #1227）。
- ✅ v3.4.47（2026-11-16）：索引缓存读路径的解析、自洽与展示边界收口。超长整数 / 深嵌套按「没有缓存」返回 `INVALID_PARAM`；`count` 始终等于 `len(jobs)`；孤立代理字符的 `source` 按空串归一；`page` / `saved_at` 加合理区间（Issue #1224）。
- ✅ v3.4.46（2026-11-16）：索引缓存元信息的类型漂移不再原样进入 `index_snapshot` 信封。非字符串 `source` 按空串、非法 `page` 按 `None`、非法 `count` 回退 `len(jobs)`、非有限 `saved_at` 按 0，`jobs` 可用时不整体丢弃缓存（Issue #1221）。
- ✅ v3.4.45（2026-11-16）：`boss_hr_last_messages` 的显式空 `friend_ids` 不再退化为抓取当前页候选人。schema 声明 `minItems: 1`，MCP 入口按 schema 校验并返回 `INVALID_PARAM`；缺省仍走 page 模式，合法非空数组行为不变（Issue #1218）。
- ✅ v3.4.44（2026-11-16）：缓存 TTL、风险锁时间戳与刷新前的登录态读取收口。`search_cache` / `job_desc_cache` 的坏 `created_at` 按过期删除；风险锁 `locked_at` 的超大整数 / `inf` 按时间未知展示且锁仍有效，`raise_if_locked` 保持风控契约；刷新 stoken 前先校验 `cookies` 结构（Issue #1214）。
- ✅ v3.4.43（2026-11-16）：预算表、`crawl_jobs`、向导展示与风控锁四处本地记录漂移收口。调度时间戳按无历史 / 0 容错并清理 `typeof` 漂移行，坏冷却不再吞掉服务器 429 反馈；`page_no` / `detail_done` 在读取边界校验，`CACHE_RECORD_INVALID` 取代 `ok:true` 的 stopped；历史 `jobs_seen` / `jobs` 漂移降级展示；登录态 `cookies` 结构漂移不再掩盖风控信封（Issue #1212）。
- ✅ v3.4.42（2026-11-16）：`boss ai` 各命令、`boss agent crawl` 与向导 AI 步骤在构造 `AIService` 前校验 `ai_temperature` / `ai_max_tokens`。坏值（字符串、数组、`bool`、负数、`inf`/`nan`）此前会原样进入请求 payload，由服务商返回 400、只看到泛化的 `AI_API_ERROR`；现在返回 `INVALID_PARAM` 并给出 `boss ai config` 修复命令，向导步骤抛同码错误，`null` 与缺键继续按默认值（Issue #1209）。
- ✅ v3.4.41（2026-11-16）：`crawl resume --background` 在断点 `updated_at` 列被写成非数字文本时不再返回可重试的 `NETWORK_ERROR`。时间戳与状态排序、排队探测共用同一容错读数，漂移按 0 降级，后台恢复照常排队/启动，预检失败还原时写回归一后的 0.0（Issue #1206）。
- ✅ v3.4.40（2026-11-16）：MCP 补齐两个 CLI 已有参数。`boss_greet` 新增可选 `message`（自定义打招呼语，缺省使用平台默认招呼），`boss_apply` 新增可选 `lid`（与 `security_id` 同一列表项的访问上下文，取自 search 结果）；两者缺省 / `null` 不下发，自由文本空串按未传处理（与 `boss_detail` 的 `lid` 同口径）。
- ✅ v3.4.39（2026-11-16）：MCP 参数面两处修正。`boss_hr_chatmsg` 的显式 `max_msg_id=0` 不再被真值判断吞掉（键存在即下发，由 CLI 拒绝并返回 `INVALID_PARAM`）；`boss_exchange` 的 schema 暴露 `type`（phone|wechat），微信交换可达，缺省 / `phone` 不带选项、空串交给 CLI 枚举诊断。
- ✅ v3.4.38（2026-11-16）：本地记录与配置的数值/类型漂移继续收敛。`crawl_runs` 数值列被写成文本时 `crawl status` 不再 stdout 为空 + traceback，改为带列名的 `CACHE_RECORD_INVALID`，并与 `crawl resume` 共用 `params` 判定、回显有效预算；上一份聊天快照的 `unread` 类型漂移不再打断 `chat --export`（按 0 比较）；`ai_max_tokens` 被手改成数组/字符串时 `ai local smoke` 返回 `INVALID_PARAM`、招聘者自动回复退回模板话术，不再中止整轮自动化。
- ✅ v3.4.37（2026-11-16）：简历 JSON 字段类型漂移的读取判定统一。`name` 写成对象、`personal_info` 写成字符串、`rows` 元素写成字符串等坏文件不再出现「list 当正常条目、show 返回错误类型、export 把 `'str' object has no attribute 'get'` 兜成 `EXPORT_FAILED`」；模型读取边界按字段校验类型，list 与按名读取共用判定，show/export 返回 `RESUME_FILE_INVALID` 并指出字段，import 仍是字段级 `INVALID_PARAM`。
- ✅ v3.4.36（2026-11-16）：`wizard --stop` 在断点写坏（如 `params` 不是合法 JSON）时不再把已生效的停止报成 `CACHE_RECORD_INVALID`。停止标记先落盘，展示读取两级回退都失败时返回最小可用记录（`run_id` + `status: stopped`）；`--status` 仍按展示契约报告损坏。
- ✅ v3.4.35（2026-11-16）：`wizard --status` 与交互式恢复选择不再把可恢复任务标成断点损坏。此前「读步骤 + 校验重放标记」和「叠加实时采集」共用一个 `try`，绑定的 crawl 记录写坏时整条任务被丢进损坏名单并提示重建；现在叠加数据损坏只影响 jobs / live_status 展示，断点或重放标记损坏仍按损坏跳过。
- ✅ v3.4.34（2026-11-16）：MCP 的 `boss_preset_add` / `boss_watch_add` / `boss_batch_greet` 收到显式空串筛选（`industry` / `scale` / `stage` / `job_type`）时不再当成「没传」并返回 `ok=true`，而是原样下发给 CLI 返回取值诊断；`None` 与缺键仍等价于未传。
- ✅ v3.4.33（2026-11-16）：`wizard --resume` 在任务断点完好、但绑定的内部 crawl 记录损坏时不再被判成 `CACHE_RECORD_INVALID`；恢复与执行器的控制读取只读断点本身，执行结果优先带实时采集叠加、叠加损坏时退回断点，`--status` 仍按展示契约报告损坏。
- ✅ v3.4.32（2026-11-16）：缓存表结构漂移（旧版本建的库缺列、迁移中断）时，`shortlist list` / `shortlist add` 等走 `CacheStore` 的命令不再直接 traceback，改为返回 `CACHE_DATABASE_INVALID` 信封与备份重建指引；锁冲突仍是可重试的 `CACHE_BUSY`，代码写错的 `ProgrammingError` 保持原样上抛。
- ✅ v3.4.31（2026-11-16）：`digest`、`stats`、`chat` 与 `resume export` 的 `-o` 导出统一改为原子写入，写入中途失败时保留上一次的完整导出，不再留下半截文件；失败仍返回 `LOCAL_IO_ERROR` 信封。
- ✅ v3.4.30（2026-11-16）：`config.json` 的 `role` / `platform` / `browser_source` / `cdp_url`（类型）坏值不再挡住修复入口 `boss config reset`，其余命令仍按参数错误拒绝，显式命令行覆盖保持严格校验。
- ✅ v3.4.29（2026-11-15）：`history` 与 wizard 候选人详情在非对象平台载荷上不再崩溃；`history` 按空页返回并保持分页未知，向导详情改走职位卡片，两个通道都读不出职位时返回 `JOB_NOT_FOUND`。
- ✅ v3.4.28（2026-11-15）：断点里的重放标记 `error.replay_safe` 类型漂移时，`wizard --resume` 不再重放结果未知的写步骤；`--resume` / `--status` 与任务列表按 `CACHE_RECORD_INVALID` 拒绝且不改写断点，布尔值与缺省标记行为不变，`--stop` 不受影响。
- ✅ v3.4.27（2026-11-15）：`wizard --stop` 支持幂等重试，外层已停止但内部采集停止尚未写完时可补齐传播；重复停止返回成功，不会恢复或重跑业务步骤，不存在和已完成任务的拒绝行为保持不变。
- ✅ v3.4.26（2026-11-15）：职位卡片、个人信息、推荐列表与评分期望的非对象容器统一容错，合法字段保留原有类型；招聘者简历期望漂移按空处理，联系方式交换已确认成功时不再因响应格式漂移误报失败。
- ✅ v3.4.25（2026-11-15）：沟通 / 面试 / 聊天消息列表的元素与容器漂移统一按跳过坏元素或空处理，`chat`、`pipeline`、`digest`、`chatmsg`、`chat-summary` 与 wizard 对应步骤不再报 `INTERNAL_ERROR`；`friend_detail` 的 `zpData` 漂移按未找到候选人拒绝执行聊天动作。
- ✅ v3.4.24（2026-11-15）：`boss clean` 在搜索缓存表缺列时返回 `CACHE_DATABASE_INVALID` 信封，`--dry-run` 一致；简历解析对平台返回的 `null` 与漂移 section 按空处理，其余字段照常输出；历史、面试、推荐、开聊、导出与联系人去重里的非对象列表元素被跳过，不再让整条命令报 `INTERNAL_ERROR`。
- ✅ v3.4.23（2026-11-15）：`export -o` 先写临时文件再原子替换，失败不再截断已有导出；采集导出的 json / csv / xlsx 一起更新或都不更新，XLSX 清洗控制字符、标签容错，导出失败以 `LOCAL_IO_ERROR` 回报；搜索缓存 `pagination` 形状漂移按未命中处理。
- ✅ v3.4.22（2026-11-15）：`boss wizard --stop` 先落盘停止标记再传给内部采集任务，workflow 自身断点或采集结果损坏都不再挡住停止；`--resume` 把只落盘一半的写失败归并为 `failed` 并 exit 1；招聘自动化队列的 `confidence` / `action` 与 `state.json` 嵌套字段类型漂移统一返回 `AUTOMATION_STATE_INVALID`。
- ✅ v3.4.21（2026-11-15）：后台采集恢复在父进程持锁重读后校验断点参数，缺键和类型漂移在修改 stop、刷新交接时间或探测登录态前返回 `CACHE_RECORD_INVALID`；校验失败保留原任务且不启动 worker。
- ✅ v3.4.20（2026-11-15）：采集断点恢复按请求页码匹配监听响应，保留旧页风险和限流信号；后台预检不再丢失停止请求，并维持排队交接窗口；损坏的断点参数返回 `CACHE_RECORD_INVALID`，缓存写锁冲突返回 `CACHE_BUSY`；Hook 目录缺失在创建任务前拒绝。
- ✅ v3.4.19（2026-11-15）：MCP 显式 `null` 参数与缺键等价，必填参数传 `null` 按缺参返回；发布验收失败保留脱敏后的 stderr 尾部（先脱敏再截断），索引延迟与真实依赖冲突可直接区分。
- ✅ v3.4.18（2026-11-15）：MCP 数组参数按 `items.type` 逐元素校验；标签、selector、步骤与 `friend_ids` 中的错误元素在进入 CLI 前返回带下标的 `INVALID_PARAM`，不再被转成文本写入本地标签，合法数组与空数组行为不变。
- ✅ v3.4.17（2026-11-15）：MCP 位置参数按字面保留以连字符开头的查询、名称、消息与标识符；`--help` 不再触发帮助，真实选项仍生效，回归遍历全部工具与 action 分支。
- ✅ v3.4.16（2026-11-15）：错误码登记表、`boss doctor` 检查项、错误信封与采集结果中的恢复命令均保留自定义 `--data-dir`；改写只扫描原文一次，目录中的命令文字与前文的词内撇号不会破坏引用路径；省略上下文的身份诊断返回通用命令，静态 `boss schema` 仍展示通用示例。
- ✅ v3.4.15（2026-11-15）：向导 TTY 恢复文案的全部命令保留自定义 `--data-dir`，含 `RESUME_NOT_FOUND`、空简历面板与 AI 辅助的导入提示；动态恢复句优先于静态标签，另有守卫测试防止新增裸命令漏登记。
- ✅ v3.4.14（2026-11-15）：`boss status` 与向导失败面板的风控恢复提示保留自定义 `--data-dir`，照抄解锁命令不再去默认目录空转。
- ✅ v3.4.13（2026-11-15）：`boss status` 未登录提示中的登录命令按字面显示，自定义 `--data-dir` 含方括号目录名时不再丢失路径。
- ✅ v3.4.12（2026-11-15）：向导登录先尝试 Cookie / CDP，仅在实际扫码降级报告内核缺失后展示安装指引；已有浏览器登录态不再被扫码内核缺失误拦截，平台风控仍立即停止，安装指引中的方括号路径按字面保留。
- ✅ v3.4.11（2026-11-15）：Windows 内核安装指引按目标 shell 引用解释器路径，含空格等字符时使用 PowerShell 调用运算符；浏览器内核诊断遵循 `PLAYWRIGHT_BROWSERS_PATH`，支持相对路径与包内缓存，显式配置时不再混入默认缓存。
- ✅ v3.4.10（2026-11-14）：`boss-mcp` 默认使用同一安装的 `boss` 入口，MCP 主机用绝对路径启动且不继承 shell `PATH` 时工具调用不再全部 `CLI_ERROR`；`CRAWL_UNAVAILABLE` 与 `boss-mcp` 缺 SDK 的安装指引带上当前已有的依赖组，`uv tool install` 补装 `crawl` / `mcp` 不再互相顶替；便携包内核安装改用运行 boss 的解释器。
- ✅ v3.4.9（2026-11-14）：工具式安装（`uv tool install` / `pipx install`）下的浏览器内核安装指引改用运行 boss 的同一解释器，信封恢复命令、向导文案与文档不再给出 PATH 上不存在的裸命令；`boss doctor` 的 patchright 检查按解释器导入判断，内核缺失判定为 `error`，向导登录门的内核预检恢复拦截。
- ✅ v3.4.8（2026-11-14）：默认安装缺少 MCP SDK 时，`boss-mcp` 输出明确的依赖安装说明并以 1 退出，stdout 保持为空；SDK 可用时保留原服务器参数与传输行为，新旧入口的工具目录逐名一致，双语排障文档同步。
- ✅ v3.4.7（2026-11-14）：采集入口在创建任务、启动后台进程或读取登录态之前检查 `crawl` 可选依赖，缺失时返回 `CRAWL_UNAVAILABLE` 与安装指引，不再返回成功信封并引导反复恢复；向导与终端文案同步，已保存的采集结果仍可离线读取。
- ✅ v3.4.6（2026-11-14）：后台采集在创建任务与启动进程前检查登录态，未登录时返回 `AUTH_REQUIRED` 而不再留下永久排队的任务；没能接手的后台进程把原因写回任务，排队超过启动窗口的任务可用后台恢复重新启动；`agent crawl --query` 与其他采集入口一样向采集浏览器注入登录态。
- ✅ v3.4.5（2026-11-14）：采集显式后台恢复解除旧停止请求，并能从进程突然退出后遗留的 `running` 状态续跑，保留页面断点与累计预算；HTML / JSON 简历导出的通用失败指引按实际格式分流；Agent 快速上手补齐采集并发契约，Node 回归脚本统一启动期限。
- ✅ v3.4.4（2026-11-14）：workflow 与采集任务阻止同一运行的重叠执行；写步骤在结果确认前中断后不再重放，无法重放的失败不再提示恢复；采集退出信号保留可恢复断点；输出路径在平台请求前校验全部祖先目录；浏览器内核缺失、简历导出失败与本地文件错误指引按实际原因分流。
- ✅ v3.4.3（2026-11-14）：MCP 在创建子进程期间取消时保留子进程 checkpoint 与 `run_id`，创建失败同时被取消时恢复指引改指安装 / `--boss-bin`；`boss_wizard` 的非法 `action` 在进入 CLI 前返回 `INVALID_PARAM`，不再静默新建运行；离线评估改为验证当前平台风控停止契约；发布后安装验收定向刷新目标包索引。
- ✅ v3.4.2（2026-11-13）：浏览器非 JSON 写响应、批量招呼和 wizard 的未知写结果不再自动重放，信封统一声明 `replay_safe`；单次、批量与 wizard 的结果核对指引按角色保留数据目录，风控停止与浏览器来源耗尽的恢复提示同样保留上下文；MCP 枚举参数保留显式空串，子命令选择错误时先提示查看帮助。
- ✅ v3.4.1（2026-11-13）：MCP 导出保留显式空串、配置工具遵守必填参数；`CLI_ERROR` 恢复提示区分未启动与读写操作；只读工具带 action 维度却漏登记写模式时按写处理；`ai local status` 按字面渲染配置；采集响应结构异常保留原断点，向导自动重试复用内部任务及计数预算。
- ✅ v3.4.0（2026-11-13）：扫码登录风险错误补本地脱敏诊断（失败阶段 / 事件来源 / 白名单路径，不输出网址与正文）；TTY 终端渲染与 Markdown 导出对平台外部字符串按字面转义（markup 注入与链接 / 图片 / 原始 HTML 语法）；采集配置与后继提示保留数据目录；数据目录与登录态 / AI 密钥文件收紧为仅当前用户可读；`stats` / `interviews` / `cities` / `platforms` / 招聘者 / AI / 招聘自动化 / 简历 / 导出 / 历史 / 配置 / 投递 / 收藏 / 推荐 / 搜索 / 沟通 / 流水线等全部命令族的后继与恢复提示保留数据目录（自定义 `--data-dir` 下照抄提示不再写错目录）；MCP 空备注清空、`boss_hr_jobs` 按参数判定写操作；采集断点恢复 / 翻页截断 / 简历性别 / 面试时间 / `--dry-run` 污染 / 缓存提示复用等约 45 项缺陷修正。
- ✅ v3.3.0（2026-11-12）：新增招聘者 `hr location` 工作地点卡片（默认预览、`--yes` 确认、本地去重）；`hr chatmsg` / `chatmsg` / `chat-summary` / `me --section deliver` / 招聘者四个列表命令 / `hr jobs list` / `chat --page` / `history` / `favorites list` 统一返回分页证据；MCP 工具补齐 CLI 已有参数并以 `inputSchema` 校验类型、拒绝未声明参数；登录恢复、采集后继与清理预演提示保留数据目录；职位描述缓存加容量上限，`clean --all` 覆盖负隅表；截断 / 脱敏 / fail-open 等一批静默变形问题修正。
- ✅ v3.2.2（2026-11-09）：`ACTION_UNCONFIRMED` 诊断补齐 WS 无文本帧计数（`ws_send_without_text`）与历史回读漏斗计数（`history_new_count` / `history_self_count` / `history_recipient_count`），便于定位「已送达却未确认」的发送；同时新增公开架构文档、ADR 索引与命令参考目录，整理维护者文档与路线图。
- ✅ v3.2.1（2026-11-06）：招聘者回复在新会话上先填内容再检查编辑器、多行消息保留换行（社区 PR #728），发送前改为有上限轮询并复查目标会话；`ACTION_UNCONFIRMED` 失败详情补上历史回读证据；发版工作流在发布前校验产物与 tag
- ✅ v3.2.0（2026-11-01）：`--industry` 改用平台行业码、`--experience` / `--salary` 码位按网页筛选器校正；新增「全国」等 4 个城市码；命令兜底改报不可恢复的 `INTERNAL_ERROR`；`login --cookie-source` 提取失败给出可执行诊断（Breaking Change：行业码迁移）
- ✅ v3.1.0（2026-10-30）：新增 `--browser-source httpx-only`（结构上不打开浏览器）、统一 httpx / Chromium 出口代理、页面 token 身份采集与出口地区探测、`boss doctor` 请求间隔检查、搜索结果 `job_url`、招聘者 `hr whoami`
- ✅ v3.0.1（2026-10-23）：招聘者在线简历接口下线改报 `ENDPOINT_DEPRECATED` 并在 `details.tracking_issue` 指向跟踪 Issue；CDP 回读兼容弹层内部的 Vue 实例，`hr resume` 请求参数按现行接口修正
- ✅ v3.0.0（2026-10-02）：移除 Browser Bridge 与智联招聘平台，BOSS 直聘成为唯一注册平台（保留 Platform 抽象）；浏览器通道改为 CDP → headless（Breaking Change）
- ✅ v2.0.1（2026-09-29）：`boss chat` 公开跨请求稳定的 `uid`，`chatmsg` / `chat-summary` / `mark` / `exchange` 与快照、导出改用 `uid` 主键，避免动态 `securityId` 轮换导致联系人查找失败
- ✅ v2.0.0（2026-09-15）：公开可严格限界的 `--browser-source` 并贯通 CLI / config / schema / MCP / wizard；新增 `login --force`、CDP 登录态只读有效性探测与 cURL 凭据导入；补齐招聘者推荐建联及附件简历接收下载；统一账号/环境风控的立即终止语义；移除未完成的前程无忧 / 51job 占位平台与 Atlas 展示位/provider 接入（Breaking Change）
- ✅ v1.20.0（2026-09-03）：适配 mcp 2.x Server API（依赖 `mcp>=2.1.0,<3.0.0`，MCP 线格式未变）+ 修复设了 SOCKS 系统代理时每一条 httpx 命令都失败 + 浏览器通道选择收敛为策略表驱动的单一分发点（#387 seam 前置）+ `boss ai config` 回显解析后的实际端点 + AI provider 准入与移除规则成文
- ✅ v1.19.1（2026-08-27）：修复全新安装解析到 mcp 2.x 导致 `boss-mcp` 直接崩溃（依赖收紧为 `<2.0.0`），并新增不走 `uv.lock` 的 `fresh_install` CI 门禁
- ✅ v1.19.0（2026-08-27）：修复 MCP `tools/list` 从未注册（v1.18.0 起 MCP 入口对所有 host 不可用）并补齐协议层与 CI 门禁 + 开放 assisted / research 下全部已实现能力 + 纯终端 `boss wizard` 统一为单交互窗口 + 信封 `hints` 拆分 Agent / 真人双受众通道
- ✅ v1.18.0（2026-07-29）：可用 Docker / Compose 镜像与 CI 构建门禁 + MCP schema 契约修复与 `mcp_server` 拆分 + 离线 evals 进 P0 + ruff 规则钉死与依赖刷新（rich 15）
- ✅ v1.17.0（2026-07-27）：`boss favorites list/sync` 职位收藏同步 + 受限 Research Mode 的可恢复 crawl 工作流 + 实习岗位类型字段与筛选映射修复
- ✅ v1.16.0（2026-07-17）：显式 `operating_mode` 双模式契约（`assisted` / `research`）+ 合规护栏升级为不可变能力策略注册表，CLI / schema / MCP 过滤从同一真源派生
- ✅ v1.15.0（2026-07-14）：`boss ai cover-letter` 求职信草稿 + 死配置键与死码清理 + 智联页面域名校验加固（精确 hostname 匹配）
- ✅ v1.14.0（2026-06-25）：shortlist 本地标签 / 备注 / 离线对比 + `ai fit` / `ai suggest-keywords` / `ai resume-optimize` + 转向 MCP-first + 搜索 `match_score`
- ✅ v1.13.x（2026-06-11 ~ 06-16）：platforms 能力状态语义 + login 链路错误码补齐 + welfare 详情本地缓存 + 双语 README 重构为导航型；Agent Skill 迁出至独立仓库 boss-skill（Breaking Change）
- ✅ v1.12.0（2026-06-09）：MCP 三种传输（stdio / SSE / HTTP streaming）+ `boss_export` + 51job / `qiancheng` 占位适配器
- ✅ v1.11.0（2026-04-23）：招聘者模式（`--role recruiter`）全套 CLI 命令组 + `BossRecruiterClient` 双通道客户端 + `RecruiterPlatform` 抽象
- ✅ v1.10.x（2026-04-21）：Platform 抽象落地 —— ABC + 注册表 + `--platform` 全局选项 + 20 个命令全量迁移，`commands/` 下不再直接引用 `BossClient`
- ✅ v1.9.x（2026-04-20）：mypy 严格模式全量接入（66/66 业务模块）+ Python 嵌入 API + `py.typed` 类型导出
- ✅ v1.8.x（2026-04-19 ~ 04-20）：AI 沟通与面试扩展（`ai interview-prep` / `ai chat-coach`）+ Cursor / Windsurf 接入 + 英文贡献指南
- ✅ v1.7.0（2026-04-17）：聊天回复草稿 + 投递漏斗

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## ✅ 近期（已完成）

### 数据可视化
- [x] `boss stats --format html` 输出交互式漏斗报表（v1.7.1）
- [x] `boss digest --format md` 每日摘要邮件/飞书可直接发送（v1.8.1）
- [x] codecov badge 集成到 README（v1.7.1）

### Agent 集成
- [x] MCP 服务支持 HTTP streaming / SSE / stdio 三种传输（2026-04-27，PR #160）
- [x] Codex / Cursor / Windsurf 专用接入示例（v1.8.1，docs/integrations/ 全覆盖）
- [x] OpenAI Functions 格式导出 `boss schema --format openai-tools`（v1.7.1）

### 智能能力
- [x] `boss ai chat-coach` — 基于聊天记录给出沟通技巧建议（v1.8.0）
- [x] `boss ai interview-prep` — 基于 JD 生成模拟面试题（v1.8.0）
- [x] 支持 Claude 4.7 / GPT-5 最新模型（v1.8.2，provider 扩至 openrouter/qwen/zhipu/siliconflow）

## 🔮 中期（v2.x–v3.x）

### 治理与合规
- [x] 默认低风险辅助模式（历史项，已被 2026-08 开放能力设计取代）：ADR 0001 曾把敏感命令默认阻断并交接回平台官网；当前 `assisted` / `research` 对已实现能力权限一致，真正的边界是平台风控硬边界与有界长流程
- [x] 显式 `operating_mode` 双模式契约（历史项，v1.16.0 / ADR 0002，已被 2026-08 开放能力设计取代）：两个模式名仅作配置兼容保留，权限一致，不再产生模式级 `COMPLIANCE_BLOCKED` 或 schema / MCP 模式过滤
- [x] 受限 Research Mode 采集：固定请求 / 详情 / 墙钟 / 重试预算 + SQLite checkpoint + 停止开关（v1.17.0）

### 架构演进
- [x] mypy 严格模式全量接入 — **100% 完成**（66/66 业务模块全部 `disallow_untyped_defs + disallow_any_generics + warn_return_any` 严格化，v1.9.1）
- [x] 类型签名导出到 `stubs/`，供下游 IDE 使用（v1.8.6，py.typed + canonical `__all__` + 16 条契约测试）
- [x] 适配 mcp 2.x Server API（Issue #398）：mcp 2.0 移除了 `@server.list_tools()` / `@server.call_tool()` 装饰器，改为 `add_request_handler(method, params_type, handler)`，且 handler 签名与返回类型一并改变（`(ctx, params)` → `ListToolsResult` / `CallToolResult`）。已切到 2.x 并把依赖改为 `mcp>=2.1.0,<3.0.0`；SSE 与 streamable HTTP 两条传输实现原样保留（换实现是独立决策）。handler 注册守卫随之重写：1.x 靠反推 SDK 的装饰器工厂，2.x 改为从 `mcp_server` 源码 AST 扫出 `add_request_handler` 的 method 字面量再问运行时注册表。**上界保留**——上一次无上界导致所有全新安装解析到新大版本并崩溃，而 CI 锁着旧版本永远看不到。
- [x] 移除 Browser Bridge daemon、Chrome 扩展及可选依赖（v3.0.0）；保留 CDP / headless 浏览器通道，不再推进 Bridge 协议升级

### 生态扩展
- [ ] Web UI（React + Tailwind），适合非 Agent 用户（未排期）
- [ ] 多平台支持：Platform 抽象已落地（Week 1a–1c）；拉勾 / 猎聘 API 调研已完成（Issue #90 已闭环 · [docs/research/platforms/](docs/research/platforms/)），经评估不建议接入；智联适配器曾接入（候选者侧 + 招聘者 agent 自动化），已在 v3.0.0 移除，BOSS 直聘为当前唯一注册平台。
  - [x] Week 1a：Platform ABC 骨架 + BossPlatform adapter（#129，零行为变化）
  - [x] Week 1b：`--platform` 全局 CLI 选项 + `get_platform_instance` helper + schema 暴露 current_platform
  - [x] Week 1c：命令层全量迁移到 Platform 接口（**20 个命令**：greet / apply / batch-greet / interviews / detail / show / me / recommend / chat / chatmsg / mark / exchange / pipeline / digest / search / export / chat_summary / history / status / watch）

### 社区建设
- [ ] 更完整的中文 + 英文视频 demo / 发布素材（当前已有 `demo/demo-zh.gif` / `demo/demo-en.gif` + 对应 `demo/demo-zh.tape` / `demo/demo-en.tape` 终端演示）
- [x] [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) 收录（PR #4992，2026-04-26 合并）
- [x] [awesome-agents](https://github.com/kyrolabs/awesome-agents) PR #423 已闭环——由 bot 无 review 直接关闭，同分区存在批量静默关单现象，结论为暂不重投（详见 `docs/marketing/awesome-submissions.md`）
- [ ] 视时机决定是否进入 [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)（该仓库只接受 Web UI issue 表单，禁止 gh CLI）
- [x] 贡献者指南英文版（`CONTRIBUTING.en.md`，v1.8.3）

## 💡 长期愿景

**让 AI Agent 真正成为求职助理**，而不是工具调用生成器：
- Agent 自主完成"搜索 → 筛选 → 打招呼 → 跟进 → 面试准备"全链路
- 用户只需描述期望（"我想找 30K 以上的远程 Python 岗位"），Agent 自动执行
- 数据完全本地化，隐私和合规第一

## 🤝 如何参与

1. 在 Issue 标 `good first issue` / `help wanted` 的任务里认领
2. 对某个方向有兴趣 → 发 Issue 讨论设计
3. 发现 bug / 文档错误 → 直接发 PR
4. 不写代码也能贡献：测试报告、使用场景、翻译

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

> Roadmap 本身是活文档，每次 minor 版本发布时更新。
