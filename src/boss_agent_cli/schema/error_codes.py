"""错误码登记表：`boss schema` 的 error_codes 与信封 recoverable 元数据的唯一真源。"""

from typing import Any


ERROR_CODES: dict[str, dict[str, Any]] = {
	"CONFIRMATION_REQUIRED": {
		"message": "尚未获得操作者对本次操作的明确批准",
		"recoverable": True,
		"recovery_action": "确认操作目标与内容后重新执行并加 --yes",
	},
	"RESUME_ACCEPT_RESULT_UNKNOWN": {
		"message": "同意附件简历请求的结果未确认，禁止自动重试",
		"recoverable": False,
		"recovery_action": "在官方页面核对请求状态，不要自动重试",
	},
	"GREET_RESULT_UNKNOWN": {
		"message": "首次招呼状态未确认，禁止自动重发",
		"recoverable": False,
		"recovery_action": "先用 boss hr chat --job-id <id> 核对会话；保留本地预约，必要时在官方页面处理",
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
	"RATE_LIMITED": {
		"message": "请求频率过高",
		"recoverable": True,
		"recovery_action": "等待后重试",
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
		"recovery_action": "停止自动化访问；保留当前专用 profile，在官方页面确认并降低访问频率",
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
		"recovery_action": None,
	},
	"ALREADY_GREETED": {
		"message": "已向该招聘者打过招呼",
		"recoverable": False,
		"recovery_action": None,
	},
	"ALREADY_APPLIED": {
		"message": "已发起过投递/立即沟通",
		"recoverable": False,
		"recovery_action": None,
	},
	"ACCOUNT_RISK": {
		"message": "风控拦截",
		"recoverable": False,
		"recovery_action": "停止自动化访问，回到平台官网手动处理，必要时联系客服",
	},
	"COMPLIANCE_BLOCKED": {
		"message": "历史版本能力策略阻断（当前版本不主动产生）",
		"recoverable": False,
		"recovery_action": "升级到当前版本后重试",
	},
	"GREET_LIMIT": {
		"message": "今日打招呼次数已用完",
		"recoverable": False,
		"recovery_action": None,
	},
	"NETWORK_ERROR": {
		"message": "网络请求失败",
		"recoverable": True,
		"recovery_action": "重试",
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
		"recovery_action": "使用返回的 run_id 恢复 workflow",
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
		"recovery_action": "跟进 https://github.com/can4hou6joeng4/boss-agent-cli/issues/217",
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
		"recovery_action": None,
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
	"RECRUITER_NOT_AUTHORIZED": {
		"message": "当前账号非招聘者账号",
		"recoverable": True,
		"recovery_action": "切换招聘者账号或使用 --role candidate",
	},
	"APPLICATION_NOT_FOUND": {
		"message": "投递申请不存在",
		"recoverable": False,
		"recovery_action": None,
	},
	"RESUME_NOT_SHARED": {
		"message": "候选人未分享简历",
		"recoverable": True,
		"recovery_action": "使用 boss hr request-resume <friend_id> 请求附件简历",
	},
	"JOB_POST_LIMIT": {
		"message": "职位发布数量已达上限",
		"recoverable": False,
		"recovery_action": None,
	},
	"PLATFORM_NOT_SUPPORTED": {
		"message": "当前平台不支持该角色或子命令",
		"recoverable": True,
		"recovery_action": "切换到支持的平台（如 boss --platform zhipin hr ...）",
	},
	"AUTO_EXECUTED": {
		"message": "招聘自动化动作已执行",
		"recoverable": False,
		"recovery_action": None,
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
		"recovery_action": "人工确认平台状态后恢复",
	},
	"PLATFORM_VERIFICATION_REQUIRED": {
		"message": "平台要求人工验证",
		"recoverable": True,
		"recovery_action": "回到平台官网完成人工验证",
	},
}
