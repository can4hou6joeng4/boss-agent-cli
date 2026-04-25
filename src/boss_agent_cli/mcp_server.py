"""MCP Server for boss-agent-cli — 让 Claude Desktop / Cursor 直接调用 BOSS 直聘求职工具。"""

import json
import subprocess
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("boss-agent-cli")

# ── Tool 定义 ──────────────────────────────────────────────────────

TOOLS = [
	Tool(
		name="boss_status",
		description="检查 BOSS 直聘登录态",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_doctor",
		description="诊断本地运行环境、依赖、登录态和网络连通性",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_search",
		description="按关键词和筛选条件搜索 BOSS 直聘职位列表。支持城市、薪资、经验、学历、福利等多维度筛选。",
		inputSchema={
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "搜索关键词（如 Golang、Python 后端）"},
				"city": {"type": "string", "description": "城市名称（如 北京、广州）"},
				"salary": {"type": "string", "description": "薪资范围（如 20-50K）"},
				"experience": {"type": "string", "description": "经验要求（如 3-5年）"},
				"education": {"type": "string", "description": "学历要求（如 本科）"},
				"welfare": {"type": "string", "description": "福利筛选，逗号分隔 AND 逻辑（如 双休,五险一金）"},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": ["query"],
		},
	),
	Tool(
		name="boss_recommend",
		description="获取基于简历的个性化职位推荐",
		inputSchema={
			"type": "object",
			"properties": {
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_detail",
		description="查看职位详情。参数为 security_id（从 search/recommend 结果获取）。",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位的 security_id"},
				"job_id": {"type": "string", "description": "encrypt_job_id，传入可走快速通道"},
			},
			"required": ["security_id"],
		},
	),
	Tool(
		name="boss_greet",
		description="向招聘者打招呼。需要 security_id 和 job_id。",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位的 security_id"},
				"job_id": {"type": "string", "description": "职位的 encrypt_job_id"},
			},
			"required": ["security_id", "job_id"],
		},
	),
	Tool(
		name="boss_chat",
		description="查看沟通列表，支持按发起方和时间筛选",
		inputSchema={
			"type": "object",
			"properties": {
				"from_who": {"type": "string", "enum": ["boss", "me"], "description": "筛选发起方"},
				"days": {"type": "integer", "description": "只显示最近 N 天的记录"},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_me",
		description="获取当前登录用户信息（基本信息、简历、求职期望、投递记录）",
		inputSchema={
			"type": "object",
			"properties": {
				"section": {
					"type": "string",
					"enum": ["info", "resume", "expect", "deliver"],
					"description": "指定查看的部分",
				},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_cities",
		description="列出支持的城市列表（约 40 个）",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_interviews",
		description="查看面试邀请列表",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_history",
		description="查看浏览历史",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_chatmsg",
		description="查看与指定好友的聊天消息历史",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "好友的 security_id"},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": ["security_id"],
		},
	),
	Tool(
		name="boss_chat_summary",
		description="基于聊天历史生成结构化摘要与下一步建议",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "好友的 security_id"},
			},
			"required": ["security_id"],
		},
	),
	Tool(
		name="boss_mark",
		description="给联系人添加或移除标签（新招呼/沟通中/已约面/不合适/收藏等）",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "联系人的 security_id"},
				"tag": {"type": "string", "description": "标签名称"},
				"remove": {"type": "boolean", "description": "是否移除标签", "default": False},
			},
			"required": ["security_id", "tag"],
		},
	),
	Tool(
		name="boss_exchange",
		description="请求交换联系方式（手机号或微信）",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "联系人的 security_id"},
			},
			"required": ["security_id"],
		},
	),
	Tool(
		name="boss_apply",
		description="发起投递或立即沟通动作（幂等，不会重复投递）",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位的 security_id"},
				"job_id": {"type": "string", "description": "职位的 encrypt_job_id"},
			},
			"required": ["security_id", "job_id"],
		},
	),
	Tool(
		name="boss_batch_greet",
		description="搜索后批量打招呼（上限 10）",
		inputSchema={
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "搜索关键词"},
				"city": {"type": "string", "description": "城市名称"},
				"limit": {"type": "integer", "description": "最大打招呼数量", "default": 5},
				"dry_run": {"type": "boolean", "description": "预览模式", "default": False},
			},
			"required": ["query"],
		},
	),
	Tool(
		name="boss_show",
		description="按编号快速查看上次搜索或推荐结果中的职位详情",
		inputSchema={
			"type": "object",
			"properties": {
				"number": {"type": "integer", "description": "职位编号（从搜索结果中获取）"},
			},
			"required": ["number"],
		},
	),
	Tool(
		name="boss_pipeline",
		description="聚合聊天和面试数据，生成统一候选进度视图",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_follow_up",
		description="筛出需要优先跟进的候选项（未读、超时未推进、面试）",
		inputSchema={
			"type": "object",
			"properties": {
				"days_stale": {"type": "integer", "description": "超过 N 天未推进视为待跟进", "default": 3},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_digest",
		description="汇总新增职位、待跟进会话和面试项的只读日报（支持 md 输出便于邮件/飞书直发）",
		inputSchema={
			"type": "object",
			"properties": {
				"days_stale": {"type": "integer", "description": "超过 N 天未推进视为待跟进", "default": 3},
				"format": {"type": "string", "description": "输出格式", "enum": ["json", "md"], "default": "json"},
				"output": {"type": "string", "description": "Markdown 输出路径（仅 format=md 时生效）"},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_config",
		description="查看和修改配置项",
		inputSchema={
			"type": "object",
			"properties": {
				"action": {"type": "string", "enum": ["list", "get", "set", "reset"], "description": "操作类型"},
				"key": {"type": "string", "description": "配置项名称"},
				"value": {"type": "string", "description": "配置值（仅 set 时需要）"},
			},
			"required": ["action"],
		},
	),
	Tool(
		name="boss_clean",
		description="清理过期缓存和临时文件",
		inputSchema={
			"type": "object",
			"properties": {
				"dry_run": {"type": "boolean", "description": "仅预览不删除", "default": False},
				"all": {"type": "boolean", "description": "全量清理", "default": False},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_stats",
		description="投递转化漏斗统计（只读聚合打招呼、投递、候选池、监控数据）",
		inputSchema={
			"type": "object",
			"properties": {
				"days": {"type": "integer", "description": "统计窗口天数", "default": 30},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_ai_reply",
		description="基于招聘者消息生成回复草稿（2-3 条候选，支持简历参考和语气偏好）",
		inputSchema={
			"type": "object",
			"properties": {
				"recruiter_message": {"type": "string", "description": "招聘者消息文本"},
				"context": {"type": "string", "description": "会话上下文（可选）"},
				"resume": {"type": "string", "description": "参考简历名称（可选）"},
				"tone": {
					"type": "string",
					"description": "语气偏好",
					"enum": ["简洁专业", "热情积极", "谨慎确认"],
					"default": "简洁专业",
				},
			},
			"required": ["recruiter_message"],
		},
	),
	Tool(
		name="boss_ai_interview_prep",
		description="基于目标职位描述生成模拟面试题与准备建议（支持简历参考定制题目）",
		inputSchema={
			"type": "object",
			"properties": {
				"jd_text": {"type": "string", "description": "目标职位描述文本"},
				"resume": {"type": "string", "description": "参考简历名称（可选）"},
				"count": {"type": "integer", "description": "题量，默认 10", "default": 10},
			},
			"required": ["jd_text"],
		},
	),
	Tool(
		name="boss_ai_chat_coach",
		description="基于聊天记录诊断沟通状态并给出下一步行动建议与现成消息模板",
		inputSchema={
			"type": "object",
			"properties": {
				"chat_text": {"type": "string", "description": "聊天记录文本"},
				"resume": {"type": "string", "description": "参考简历名称（可选）"},
				"style": {
					"type": "string",
					"description": "沟通风格偏好（如 简洁专业/积极主动/谨慎稳重）",
					"default": "简洁专业",
				},
			},
			"required": ["chat_text"],
		},
	),
	Tool(
		name="boss_resume_list",
		description="列出所有本地简历（名称、创建时间、关联职位数）",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_resume_show",
		description="查看指定简历的完整内容（基本信息、教育、工作经历、技能、项目）",
		inputSchema={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "简历名称"},
			},
			"required": ["name"],
		},
	),
	Tool(
		name="boss_ai_analyze_jd",
		description="分析职位描述并评估简历匹配度，输出匹配分数和差距分析",
		inputSchema={
			"type": "object",
			"properties": {
				"jd_text": {"type": "string", "description": "职位描述文本"},
				"resume": {"type": "string", "description": "对比的本地简历名称"},
			},
			"required": ["jd_text", "resume"],
		},
	),
	Tool(
		name="boss_ai_optimize",
		description="基于目标职位描述优化简历（输出优化后结构，不直接写回磁盘）",
		inputSchema={
			"type": "object",
			"properties": {
				"resume": {"type": "string", "description": "简历名称"},
				"jd_text": {"type": "string", "description": "目标职位描述"},
			},
			"required": ["resume", "jd_text"],
		},
	),
	Tool(
		name="boss_ai_suggest",
		description="基于目标职位给出简历改进建议（按优先级排序，不修改简历）",
		inputSchema={
			"type": "object",
			"properties": {
				"resume": {"type": "string", "description": "简历名称"},
				"jd_text": {"type": "string", "description": "目标职位描述"},
			},
			"required": ["resume", "jd_text"],
		},
	),
	Tool(
		name="boss_watch_list",
		description="列出所有已保存的监控条件",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_watch_run",
		description="执行指定监控并返回新增职位列表",
		inputSchema={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "监控名称"},
			},
			"required": ["name"],
		},
	),
	Tool(
		name="boss_preset_list",
		description="列出所有搜索预设",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_shortlist_list",
		description="查看候选池中的所有职位",
		inputSchema={"type": "object", "properties": {}, "required": []},
	),
	Tool(
		name="boss_shortlist_add",
		description="将职位加入候选池",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位安全 ID"},
				"job_id": {"type": "string", "description": "加密职位 ID"},
			},
			"required": ["security_id", "job_id"],
		},
	),
	Tool(
		name="boss_shortlist_remove",
		description="从候选池移除职位",
		inputSchema={
			"type": "object",
			"properties": {
				"security_id": {"type": "string", "description": "职位安全 ID"},
				"job_id": {"type": "string", "description": "加密职位 ID"},
			},
			"required": ["security_id", "job_id"],
		},
	),
	Tool(
		name="boss_preset_add",
		description="保存搜索预设",
		inputSchema={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "预设名称"},
				"query": {"type": "string", "description": "搜索关键词"},
				"city": {"type": "string", "description": "城市（可选）"},
				"salary": {"type": "string", "description": "薪资范围（可选）"},
				"experience": {"type": "string", "description": "经验要求（可选）"},
				"education": {"type": "string", "description": "学历要求（可选）"},
				"welfare": {"type": "string", "description": "福利筛选（可选）"},
			},
			"required": ["name", "query"],
		},
	),
	Tool(
		name="boss_preset_remove",
		description="删除指定搜索预设",
		inputSchema={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "预设名称"},
			},
			"required": ["name"],
		},
	),
	Tool(
		name="boss_watch_add",
		description="保存增量监控的搜索条件",
		inputSchema={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "监控名称"},
				"query": {"type": "string", "description": "搜索关键词"},
				"city": {"type": "string", "description": "城市（可选）"},
				"salary": {"type": "string", "description": "薪资范围（可选）"},
			},
			"required": ["name", "query"],
		},
	),
	Tool(
		name="boss_watch_remove",
		description="删除指定监控",
		inputSchema={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "监控名称"},
			},
			"required": ["name"],
		},
	),
	Tool(
		name="boss_hr_applications",
		description="招聘者模式：查看候选人投递申请列表",
		inputSchema={
			"type": "object",
			"properties": {
				"job_id": {"type": "string", "description": "按职位筛选（可选）"},
				"label_id": {"type": "integer", "description": "标签筛选（0=全部, 1=新招呼, 2=沟通中）", "default": 0},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_hr_candidates",
		description="招聘者模式：搜索候选人",
		inputSchema={
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "搜索关键词（可选，不传时返回默认候选集）"},
				"city": {"type": "string", "description": "城市筛选"},
				"job_id": {"type": "string", "description": "按职位筛选"},
				"experience": {"type": "string", "description": "经验要求"},
				"degree": {"type": "string", "description": "学历要求"},
				"page": {"type": "integer", "description": "页码", "default": 1},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_hr_chat",
		description="招聘者模式：查看与候选人的沟通列表",
		inputSchema={
			"type": "object",
			"properties": {
				"page": {"type": "integer", "description": "页码", "default": 1},
				"job_id": {"type": "string", "description": "按职位筛选"},
				"label_id": {"type": "integer", "description": "标签筛选（0=全部, 1=新招呼, 2=沟通中）", "default": 0},
			},
			"required": [],
		},
	),
	Tool(
		name="boss_hr_resume",
		description="招聘者模式：查看候选人在线简历",
		inputSchema={
			"type": "object",
			"properties": {
				"geek_id": {"type": "string", "description": "候选人 geek_id"},
				"job_id": {"type": "string", "description": "关联职位 ID"},
				"security_id": {"type": "string", "description": "候选人的 security_id"},
				"raw": {"type": "boolean", "description": "输出原始 API 数据", "default": False},
			},
			"required": ["geek_id", "job_id", "security_id"],
		},
	),
	Tool(
		name="boss_hr_reply",
		description="招聘者模式：回复候选人消息",
		inputSchema={
			"type": "object",
			"properties": {
				"friend_id": {"type": "integer", "description": "候选人会话 friend_id"},
				"message": {"type": "string", "description": "回复消息内容"},
			},
			"required": ["friend_id", "message"],
		},
	),
	Tool(
		name="boss_hr_request_resume",
		description="招聘者模式：请求候选人分享附件简历",
		inputSchema={
			"type": "object",
			"properties": {
				"friend_id": {"type": "integer", "description": "候选人会话 friend_id"},
				"job_id": {"type": "integer", "description": "关联职位 ID"},
			},
			"required": ["friend_id", "job_id"],
		},
	),
	Tool(
		name="boss_hr_jobs",
		description="招聘者模式：查看职位列表，或执行上线/下线操作",
		inputSchema={
			"type": "object",
			"properties": {
				"action": {"type": "string", "description": "操作类型", "enum": ["list", "online", "offline"], "default": "list"},
				"job_id": {"type": "string", "description": "职位 ID（online/offline 时必填）"},
			},
			"required": [],
		},
	),
]


# ── Tool 调用逻辑 ──────────────────────────────────────────────────


def _run_boss(*args: str) -> dict[str, Any]:
	"""调用 boss CLI 并返回解析后的 JSON。"""
	cmd = ["boss", "--json", *args]
	result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
	try:
		return json.loads(result.stdout)
	except json.JSONDecodeError:
		return {
			"ok": False,
			"error": {"code": "CLI_ERROR", "message": result.stderr or "命令执行失败"},
		}


def _build_args(tool_name: str, arguments: dict) -> list[str]:
	"""根据 tool name 和参数构建 CLI 参数列表。"""
	name = tool_name.replace("boss_", "")

	if name == "search":
		args = [name, arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "welfare"):
			if opt in arguments and arguments[opt]:
				args.extend([f"--{opt}", str(arguments[opt])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "recommend":
		args = [name]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "detail":
		args = [name, arguments["security_id"]]
		if "job_id" in arguments and arguments["job_id"]:
			args.extend(["--job-id", arguments["job_id"]])
		return args

	if name == "greet":
		return [name, arguments["security_id"], arguments["job_id"]]

	if name == "chat":
		args = [name]
		if "from_who" in arguments and arguments["from_who"]:
			args.extend(["--from", arguments["from_who"]])
		if "days" in arguments:
			args.extend(["--days", str(arguments["days"])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "me":
		args = [name]
		if "section" in arguments and arguments["section"]:
			args.extend(["--section", arguments["section"]])
		return args

	if name == "chatmsg":
		args = [name, arguments["security_id"]]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "chat_summary":
		return ["chat-summary", arguments["security_id"]]

	if name == "mark":
		args = [name, arguments["security_id"], "--tag", arguments["tag"]]
		if arguments.get("remove"):
			args.append("--remove")
		return args

	if name == "exchange":
		return [name, arguments["security_id"]]

	if name == "apply":
		return [name, arguments["security_id"], arguments["job_id"]]

	if name == "batch_greet":
		args = ["batch-greet", arguments["query"]]
		if "city" in arguments and arguments["city"]:
			args.extend(["--city", arguments["city"]])
		if "limit" in arguments:
			args.extend(["--limit", str(arguments["limit"])])
		if arguments.get("dry_run"):
			args.append("--dry-run")
		return args

	if name == "show":
		return [name, str(arguments["number"])]

	if name == "follow_up":
		args = ["follow-up"]
		if "days_stale" in arguments:
			args.extend(["--days-stale", str(arguments["days_stale"])])
		return args

	if name == "config":
		action = arguments.get("action", "list")
		args = [name, action]
		if action in ("get", "set", "reset") and "key" in arguments:
			args.append(arguments["key"])
		if action == "set" and "value" in arguments:
			args.append(arguments["value"])
		return args

	if name == "clean":
		args = [name]
		if arguments.get("dry_run"):
			args.append("--dry-run")
		if arguments.get("all"):
			args.append("--all")
		return args

	if name == "digest":
		args = [name]
		if "days_stale" in arguments:
			args.extend(["--days-stale", str(arguments["days_stale"])])
		if arguments.get("format"):
			args.extend(["--format", arguments["format"]])
		if arguments.get("output"):
			args.extend(["-o", arguments["output"]])
		return args

	if name == "stats":
		args = [name]
		if "days" in arguments:
			args.extend(["--days", str(arguments["days"])])
		return args

	if name == "ai_reply":
		args = ["ai", "reply", arguments["recruiter_message"]]
		if arguments.get("context"):
			args.extend(["--context", arguments["context"]])
		if arguments.get("resume"):
			args.extend(["--resume", arguments["resume"]])
		if arguments.get("tone"):
			args.extend(["--tone", arguments["tone"]])
		return args

	if name == "ai_interview_prep":
		args = ["ai", "interview-prep", arguments["jd_text"]]
		if arguments.get("resume"):
			args.extend(["--resume", arguments["resume"]])
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		return args

	if name == "ai_chat_coach":
		args = ["ai", "chat-coach", arguments["chat_text"]]
		if arguments.get("resume"):
			args.extend(["--resume", arguments["resume"]])
		if arguments.get("style"):
			args.extend(["--style", arguments["style"]])
		return args

	if name == "resume_list":
		return ["resume", "list"]

	if name == "resume_show":
		return ["resume", "show", arguments["name"]]

	if name == "ai_analyze_jd":
		return ["ai", "analyze-jd", arguments["jd_text"], "--resume", arguments["resume"]]

	if name == "ai_optimize":
		return ["ai", "optimize", arguments["resume"], "--jd", arguments["jd_text"]]

	if name == "ai_suggest":
		return ["ai", "suggest", arguments["resume"], "--jd", arguments["jd_text"]]

	if name == "watch_list":
		return ["watch", "list"]

	if name == "watch_run":
		return ["watch", "run", arguments["name"]]

	if name == "preset_list":
		return ["preset", "list"]

	if name == "shortlist_list":
		return ["shortlist", "list"]

	if name == "shortlist_add":
		return ["shortlist", "add", arguments["security_id"], arguments["job_id"]]

	if name == "shortlist_remove":
		return ["shortlist", "remove", arguments["security_id"], arguments["job_id"]]

	if name == "preset_add":
		args = ["preset", "add", arguments["name"], arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "welfare"):
			if arguments.get(opt):
				args.extend([f"--{opt}", str(arguments[opt])])
		return args

	if name == "preset_remove":
		return ["preset", "remove", arguments["name"]]

	if name == "watch_add":
		args = ["watch", "add", arguments["name"], arguments["query"]]
		for opt in ("city", "salary"):
			if arguments.get(opt):
				args.extend([f"--{opt}", str(arguments[opt])])
		return args

	if name == "watch_remove":
		return ["watch", "remove", arguments["name"]]

	if name == "hr_applications":
		args = ["hr", "applications"]
		if arguments.get("job_id"):
			args.extend(["--job-id", str(arguments["job_id"])])
		if "label_id" in arguments:
			args.extend(["--label-id", str(arguments["label_id"])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "hr_candidates":
		args = ["hr", "candidates"]
		if arguments.get("query"):
			args.append(arguments["query"])
		for opt in ("city", "job_id", "experience", "degree"):
			if arguments.get(opt):
				args.extend([f"--{opt.replace('_', '-')}", str(arguments[opt])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "hr_chat":
		args = ["hr", "chat"]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if arguments.get("job_id"):
			args.extend(["--job-id", str(arguments["job_id"])])
		if "label_id" in arguments:
			args.extend(["--label-id", str(arguments["label_id"])])
		return args

	if name == "hr_resume":
		args = ["hr", "resume", arguments["geek_id"], "--job-id", str(arguments["job_id"]), "--security-id", str(arguments["security_id"])]
		if arguments.get("raw"):
			args.append("--raw")
		return args

	if name == "hr_reply":
		return ["hr", "reply", str(arguments["friend_id"]), arguments["message"]]

	if name == "hr_request_resume":
		return ["hr", "request-resume", str(arguments["friend_id"]), "--job-id", str(arguments["job_id"])]

	if name == "hr_jobs":
		action = arguments.get("action", "list")
		args = ["hr", "jobs", action]
		if action in {"online", "offline"}:
			args.append(str(arguments["job_id"]))
		return args

	# 无参数命令：status, doctor, cities, interviews, history, pipeline
	return [name]


# ── MCP Handlers ───────────────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[Tool]:
	return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
	args = _build_args(name, arguments)
	result = _run_boss(*args)
	return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


# ── 入口 ──────────────────────────────────────────────────────────


async def main():
	async with stdio_server() as (read_stream, write_stream):
		await server.run(read_stream, write_stream, server.create_initialization_options())


def run() -> None:
	import asyncio
	asyncio.run(main())


if __name__ == "__main__":
	run()
