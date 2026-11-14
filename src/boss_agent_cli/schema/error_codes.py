"""错误码登记表：`boss schema` 的 error_codes 与信封 recoverable 元数据的唯一真源。"""

from typing import Any


ERROR_CODES: dict[str, dict[str, Any]] = {
	"CONFIRMATION_REQUIRED": {
		"message": "尚未获得操作者对本次操作的明确批准",
		"recoverable": True,
		"recovery_action": "确认操作目标与内容后重新执行并加 --yes",
	},
	"HOOK_BLOCKED": {
		"message": "操作被本地钩子阻止",
		"recoverable": True,
		"recovery_action": "检查 greet_before 钩子的返回内容，调整或移除阻止后重试",
	},
	"RESUME_ACCEPT_RESULT_UNKNOWN": {
		"message": "同意附件简历请求的结果未确认，禁止自动重试",
		"recoverable": False,
		"recovery_action": "在官方页面核对请求状态，不要自动重试",
		"replay_safe": False,
		"outcome_unknown": True,
	},
	"ACTION_UNCONFIRMED": {
		"message": "页面动作已执行，但未确认是否发送成功，禁止自动重试",
		"recoverable": False,
		"recovery_action": "动作可能已生效，请先 `boss hr chatmsg <friend_id>` 核实，勿直接重试",
		"replay_safe": False,
		"outcome_unknown": True,
	},
	"LOCAL_STATE_WRITE_FAILED": {
		"message": "平台操作已完成，但本地状态保存失败，禁止自动重发",
		"recoverable": False,
		"recovery_action": "先在官方页面核对本次操作是否已生效；不要自动重发，确认磁盘空间与缓存目录权限后再继续",
		# 平台结果已确认成功，只是本地记账失败：禁止重发，但不是「结果未知」。
		"replay_safe": False,
	},
	"LOCAL_IO_ERROR": {
		"message": "本地文件读写失败",
		"recoverable": False,
		"recovery_action": "不要自动重试：确认目标路径是可写目录（而不是同名的已有文件）、目录权限与磁盘空间正常，修正后再执行",
	},
	"AUTOMATION_STATE_INVALID": {
		"message": "招聘自动化状态文件损坏",
		"recoverable": True,
		"recovery_action": "修正或删除数据目录下的 automation/state.json 后重试；删除会丢失自动化去重与历史进度，建议先备份该文件",
	},
	"EXCHANGE_NOT_AVAILABLE": {
		"message": "换电话/换微信按钮未解锁（招聘者尚未在会话中发过消息），本次未发出任何请求",
		"recoverable": True,
		"recovery_action": "先回复候选人（boss hr reply <friend_id> <消息> 或 boss hr request-resume <friend_id>），再重新执行交换",
	},
	"GREET_RESULT_UNKNOWN": {
		"message": "首次招呼状态未确认，禁止自动重发",
		"recoverable": False,
		"recovery_action": "先用 boss hr chat --job-id <id> 核对会话；保留本地预约，必要时在官方页面处理",
		"replay_safe": False,
		"outcome_unknown": True,
	},
	"LOCATION_ALREADY_SENT": {
		"message": "该会话已发送过工作地点，禁止重复发送",
		"recoverable": False,
		"recovery_action": "用 boss hr chatmsg <friend_id> 核对地点卡，不要删除本地记录后重发",
	},
	"LOCATION_RESULT_UNKNOWN": {
		"message": "工作地点发送结果未确认，禁止自动重发",
		"recoverable": False,
		"recovery_action": "先用 boss hr chatmsg <friend_id> 核对地点卡是否已发出；保留本地预约，不要自动重发",
		"replay_safe": False,
		"outcome_unknown": True,
	},
	"AUTH_EXPIRED": {
		"message": "登录态过期",
		"recoverable": True,
		"recovery_action": "boss login",
	},
	"AUTH_REQUIRED": {
		"message": "未登录",
		"recoverable": True,
		"recovery_action": "boss login",
	},
	"LOGOUT_FAILED": {
		"message": "退出登录失败",
		"recoverable": True,
		"recovery_action": "检查数据目录权限与磁盘空间后重试 boss logout",
	},
	"RATE_LIMITED": {
		"message": "请求频率过高",
		"recoverable": True,
		"recovery_action": "等待后重试",
	},
	"REQUEST_BUDGET_EXHAUSTED": {
		"message": "共享请求预算、执行锁或冷却等待超过本次时限，请求未发送",
		"recoverable": True,
		"recovery_action": "保留 checkpoint，等待冷却并检查并发任务后显式恢复；不要清除风控锁或自动重试",
	},
	"RESULT_LIMIT_REACHED": {
		"message": "结果超过安全处理上限",
		"recoverable": True,
		"recovery_action": "缩小结果范围后重试",
	},
	"TOKEN_REFRESH_FAILED": {
		"message": "Token 刷新失败",
		"recoverable": True,
		"recovery_action": "boss login",
	},
	"ENVIRONMENT_RISK": {
		"message": "访问环境存在异常",
		"recoverable": False,
		"recovery_action": "停止自动化访问；保留原会话，在官方目标业务页确认风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务，降低访问频率",
	},
	"ENVIRONMENT_RISK_LOCKED": {
		"message": "此前记录的环境风险锁尚未由用户显式解除，本次请求未发送",
		"recoverable": False,
		"recovery_action": "在原会话的 BOSS 官方目标业务页确认风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务；令牌变化不会自动解锁",
	},
	"LOGIN_TIMEOUT": {
		"message": "登录等待超时（扫码未完成或网络缓慢）",
		"recoverable": True,
		"recovery_action": "boss login --timeout 180",
	},
	"CDP_UNAVAILABLE": {
		"message": "Chrome 调试连接不可用",
		"recoverable": True,
		"recovery_action": "boss login",
	},
	"BROWSER_DISABLED": {
		"message": "当前 browser-source 不打开浏览器，该能力需要页面通道",
		"recoverable": True,
		"recovery_action": "改用 --browser-source auto 后重试",
	},
	"BROWSER_SESSION_NOT_FOUND": {
		"message": "未发现可复用的现有浏览器会话",
		"recoverable": True,
		"recovery_action": "boss doctor",
	},
	"BROWSER_KERNEL_MISSING": {
		"message": "patchright 浏览器内核缺失或与所需修订版不匹配",
		"recoverable": True,
		"recovery_action": "patchright install chromium",
	},
	"BROWSER_NAV_TIMEOUT": {
		"message": "浏览器首页导航超时",
		"recoverable": True,
		"recovery_action": "用已登录 Chrome 的调试端口重试，或先执行 boss login",
	},
	"LOGIN_RISK_CONTROL": {
		"message": "登录请求可能触发平台风控",
		"recoverable": False,
		"recovery_action": "停止自动化重试，改用浏览器手动确认账号状态",
	},
	"LOGIN_EXPIRED": {
		"message": "登录态已失效或授权不足",
		"recoverable": True,
		"recovery_action": "boss login",
	},
	"LOGIN_CREDENTIAL_EXTRACTION_FAILED": {
		"message": "登录成功后提取凭证失败",
		"recoverable": True,
		"recovery_action": "boss login --cookie-source chrome",
	},
	"JOB_NOT_FOUND": {
		"message": "职位不存在或已下架",
		"recoverable": False,
		# 平台职位不存在是确定性结果，但 Agent 仍需一条换线索的路径；
		# 留 null 会让错误信封变成「下架」的死胡同。
		"recovery_action": "该职位已不可用：用 boss search <关键词> 重新搜索，或 boss recommend 查看推荐职位",
	},
	"CONTACT_NOT_FOUND": {
		"message": "沟通列表中未找到该联系人",
		"recoverable": False,
		"recovery_action": "boss chat",
	},
	"CRAWL_RUN_NOT_FOUND": {
		"message": "本地采集任务不存在或 run_id 有误",
		"recoverable": False,
		"recovery_action": "boss crawl start <query>（新建任务），或 boss crawl status <run_id> 核对 ID",
	},
	"SAVED_SEARCH_NOT_FOUND": {
		"message": "本地预设或 watch 不存在",
		"recoverable": False,
		"recovery_action": "boss preset list（预设）或 boss watch list（watch）",
	},
	"SHORTLIST_ENTRY_NOT_FOUND": {
		"message": "候选池中没有该职位，无法标注",
		"recoverable": False,
		"recovery_action": "boss shortlist add <security_id> <job_id>（先加入候选池），或 boss shortlist list 核对已有条目",
	},
	"WORKFLOW_NOT_FOUND": {
		"message": "本地 workflow 任务不存在或 run_id 有误",
		"recoverable": False,
		"recovery_action": "boss wizard（新建 workflow），或 boss wizard --status <run_id> 核对 ID",
	},
	"ALREADY_GREETED": {
		"message": "已向该招聘者打过招呼",
		"recoverable": False,
		# 重复打招呼被本地去重拦下：会话其实已建立，下一步是跟进而不是停住。
		"recovery_action": "该招聘者已打过招呼：用 boss chat 查看会话，或用 boss search <关键词> 换一批职位",
	},
	"ALREADY_APPLIED": {
		"message": "已发起过投递/立即沟通",
		"recoverable": False,
		# 与 greet 同理：投递记录已经存在，给出查看与跟进的路径。
		"recovery_action": "该职位已投递过：用 boss me --section deliver 查看投递记录，或用 boss chat 跟进",
	},
	"ACCOUNT_RISK": {
		"message": "风控拦截",
		"recoverable": False,
		"recovery_action": "停止自动化访问，回到平台官网手动处理；确认目标业务页风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务，必要时联系客服",
	},
	"COMPLIANCE_BLOCKED": {
		"message": "历史版本能力策略阻断（当前版本不主动产生）",
		"recoverable": False,
		"recovery_action": "升级到当前版本后重试",
	},
	"GREET_LIMIT": {
		"message": "今日打招呼次数已用完",
		"recoverable": False,
		# 平台按职位/账号限制主动沟通人数：等待权益恢复，不能自动重发。
		"recovery_action": "今日主动沟通额度已用完：停止自动打招呼，等平台权益恢复后再试；不要自动重发本次招呼",
	},
	"INTERNAL_ERROR": {
		"message": "CLI 内部错误，或平台返回了未预期的数据结构",
		"recoverable": False,
		"recovery_action": "不要重试：同样的输入会得到同样的结果。先升级到最新版本；仍出现时附上命令与 error.message 提交 issue",
	},
	"NETWORK_ERROR": {
		"message": "网络请求失败",
		"recoverable": True,
		"recovery_action": "重试",
	},
	"UNKNOWN": {
		"message": "平台返回了未识别的错误",
		"recoverable": False,
		"recovery_action": "不要自动重试：先在 BOSS 官方页面核对本次操作是否生效，并用 boss doctor 检查登录态、浏览器通道与网络；确认后重新执行一次，仍失败请附上命令与 error.message 提交 issue",
		# 未识别的响应无法证明写操作没有发生：写命令不得把它升级成可重试的
		# NETWORK_ERROR，批量与 wizard 也不得重放（Issue #990）。声明后，单次
		# 命令的 UNKNOWN 信封同样能从 error.details 读出该判定。
		"replay_safe": False,
	},
	"CRAWL_UNAVAILABLE": {
		"message": "DrissionPage crawl 运行环境或 Hook 注入不可用",
		"recoverable": True,
		"recovery_action": "安装 boss-agent-cli[crawl] 并执行 boss crawl configure",
	},
	"CRAWL_PERMISSION_REQUIRED": {
		"message": "历史版本要求显式授权启动 crawl（当前版本不主动产生）",
		"recoverable": True,
		"recovery_action": "升级当前版本后直接使用 --query，或分析已有 --run-id",
	},
	"WIZARD_INPUT_REQUIRED": {
		"message": "headless wizard 缺少结构化输入或 run_id",
		"recoverable": True,
		"recovery_action": "boss --json wizard --input-json '<object>'",
	},
	"WORKFLOW_TIMEOUT": {
		"message": "workflow 超过调用方设置的超时",
		"recoverable": True,
		"recovery_action": "使用返回的 run_id 执行 boss wizard --resume <run_id> 恢复 workflow",
	},
	"WORKFLOW_PLAN_MISMATCH": {
		"message": "run_id 已绑定到不同的 workflow plan",
		"recoverable": False,
		"recovery_action": "使用原 plan 恢复，或创建新 workflow",
	},
	"WORKFLOW_STOPPED": {
		"message": "workflow 已按 stop 请求停止",
		"recoverable": True,
		"recovery_action": "创建新 workflow，或恢复其内部可恢复任务",
	},
	"WORKFLOW_BUSY": {
		"message": "同一 workflow 正在由另一次调用执行，本次调用未执行任何步骤",
		"recoverable": True,
		"recovery_action": "boss wizard --status <run_id> 查看进度，等当前执行结束后再恢复；不要并发恢复同一个 run_id",
	},
	"CRAWL_BUSY": {
		"message": "同一 crawl run 正在由另一次调用执行，本次调用未发出任何请求",
		"recoverable": True,
		"recovery_action": "boss crawl status <run_id> 查看进度，等当前执行结束后再恢复；不要并发恢复同一个 run_id",
	},
	"CRAWL_NOT_COMPLETED": {
		"message": "crawl 尚未完成，Agent 不会导入不完整结果",
		"recoverable": True,
		"recovery_action": "处理浏览器验证后执行 boss crawl resume <run_id>",
	},
	"INVALID_PARAM": {
		"message": "参数校验失败",
		"recoverable": False,
		"recovery_action": "修正参数",
	},
	"ENDPOINT_DEPRECATED": {
		"message": "服务端端点已迁移，CLI 当前实现无法直接发送",
		"recoverable": False,
		"recovery_action": "停止重试。该平台端点已下线，查看 error.details.tracking_issue",
	},
	"RECRUITER_CHAT_TAB_REQUIRED": {
		"message": "招聘者操作需要 Chrome 已打开聊天页 (chat/index)",
		"recoverable": True,
		"recovery_action": "回到 BOSS 直聘官方招聘者页面手动处理",
	},
	"NOT_SUPPORTED": {
		"message": "当前平台暂不支持该能力",
		"recoverable": True,
		"recovery_action": "切换平台或调整命令参数后重试",
	},
	"RESUME_NOT_FOUND": {
		"message": "简历不存在",
		"recoverable": False,
		"recovery_action": "boss resume list",
	},
	"RESUME_FILE_INVALID": {
		"message": "本地简历文件损坏或字段类型不正确，无法读取",
		"recoverable": True,
		"recovery_action": "修正 resumes/<name>.json 的 JSON 内容后重试，或执行 boss resume delete <name> 后重新创建；该文件只存在本机",
	},
	"RESUME_ALREADY_EXISTS": {
		"message": "简历名称已存在",
		"recoverable": False,
		"recovery_action": "使用不同名称或先删除已有简历",
	},
	"EXPORT_FAILED": {
		"message": "导出失败",
		"recoverable": True,
		"recovery_action": "检查 patchright 安装：patchright install chromium",
	},
	"AI_NOT_CONFIGURED": {
		"message": "AI 服务未配置",
		"recoverable": True,
		"recovery_action": "boss ai config --provider <provider> --model <model> --api-key <key>",
	},
	"CONFIRM_DOWNLOAD_REQUIRED": {
		"message": "下载模型权重需要显式确认",
		"recoverable": True,
		"recovery_action": "确认模型与下载量后重新执行并加 --confirm-download",
	},
	"LOCAL_MODEL_PULL_FAILED": {
		"message": "Ollama 拉取模型失败",
		"recoverable": True,
		"recovery_action": "确认 Ollama 已安装并可访问网络后重试",
	},
	"MODEL_SOURCE_NOT_FOUND": {
		"message": "本地模型源文件或目录不存在",
		"recoverable": True,
		"recovery_action": "检查 --path 指向的模型文件或目录后重试",
	},
	"MODEL_REGISTRY_INVALID": {
		"message": "本地模型登记表损坏",
		"recoverable": True,
		"recovery_action": "修正或删除数据目录下的 models/registry.json 后重试；模型文件仍在磁盘上，重新导入即可重建登记",
	},
	"SAVED_SEARCH_INVALID": {
		"message": "本地预设的参数损坏",
		"recoverable": True,
		"recovery_action": "执行 boss preset remove <名称>（watch 用 boss watch remove）后重新添加；参数只存在本地缓存，删除不影响平台数据",
	},
	"AI_API_ERROR": {
		"message": "AI 服务调用失败",
		"recoverable": True,
		"recovery_action": "检查网络连接和密钥配置，重试",
	},
	"AI_PARSE_ERROR": {
		"message": "AI 返回结果解析失败",
		"recoverable": True,
		"recovery_action": "重试（模型输出不稳定时可能发生）",
	},
	"CACHE_MISS": {
		"message": "缓存数据缺失",
		"recoverable": True,
		"recovery_action": "执行对应的数据获取命令以填充缓存",
	},
	"CACHE_RECORD_INVALID": {
		"message": "本地任务记录损坏，断点无法恢复",
		"recoverable": True,
		"recovery_action": "该 run 的记录只存在本地缓存：重新创建任务（crawl 用 boss crawl start，workflow 用 boss wizard --input-json）后重试；已导出的文件不会自动删除",
	},
	"CACHE_DATABASE_INVALID": {
		"message": "本地缓存数据库损坏或不是 SQLite 文件",
		"recoverable": True,
		"recovery_action": "缓存库只存本地数据：先备份 cache/boss_agent.db（--data-dir 指定时在对应目录下），再删除该文件后重试，命令会自动重建一个空库",
	},
	"RECRUITER_NOT_AUTHORIZED": {
		"message": "当前账号非招聘者账号（当前版本不主动产生）",
		"recoverable": True,
		"recovery_action": "切换招聘者账号或使用 --role candidate",
	},
	"RESUME_NOT_SHARED": {
		"message": "候选人未分享简历（当前版本不主动产生）",
		"recoverable": True,
		"recovery_action": "使用 boss hr request-resume <friend_id> 请求附件简历",
	},
	"PLATFORM_NOT_SUPPORTED": {
		"message": "当前平台不支持该角色或子命令",
		"recoverable": True,
		"recovery_action": "切换到支持的平台（如 boss --platform zhipin hr ...）",
	},
	"QUEUED_FOR_REVIEW": {
		"message": "旧版本招聘自动化动作进入人工复核（当前决策路径不主动产生）",
		"recoverable": True,
		"recovery_action": "boss agent review list",
	},
	"QUEUED_PENDING_ACTION": {
		"message": "旧版本招聘自动化动作进入待执行队列（当前决策路径不主动产生）",
		"recoverable": True,
		"recovery_action": "boss agent pending list",
	},
	"STOPPED_BY_SAFETY": {
		"message": "招聘自动化动作被安全额度或冷却策略停止",
		"recoverable": True,
		"recovery_action": "boss agent stats",
	},
	"CIRCUIT_BREAKER_OPEN": {
		"message": "招聘自动化熔断已打开",
		"recoverable": True,
		"recovery_action": "人工确认平台状态并核实未确认动作后，执行 boss agent run --resume 显式恢复",
	},
	"PLATFORM_VERIFICATION_REQUIRED": {
		"message": "平台要求人工验证",
		"recoverable": True,
		"recovery_action": "回到平台官网完成人工验证",
	},
}
