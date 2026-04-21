from typing import Any

import click

from boss_agent_cli.output import emit_success
from boss_agent_cli.platforms import list_platforms

# 类型转换：native schema → JSON Schema 基础类型
_JSON_SCHEMA_TYPE_MAP = {
	"string": "string",
	"int": "integer",
	"integer": "integer",
	"bool": "boolean",
	"boolean": "boolean",
	"float": "number",
	"number": "number",
}


def _option_to_json_schema_property(opt_spec: dict[str, Any]) -> dict[str, Any]:
	"""把 native option 转成单个 JSON Schema 属性。"""
	native_type = opt_spec.get("type", "string")
	prop: dict[str, Any] = {"type": _JSON_SCHEMA_TYPE_MAP.get(native_type, "string")}
	desc = opt_spec.get("description")
	if desc:
		prop["description"] = desc
	default = opt_spec.get("default")
	if default is not None:
		prop["default"] = default
	return prop


def _command_to_json_schema(cmd_name: str, cmd_spec: dict[str, Any]) -> dict[str, Any]:
	"""把 native 命令描述转成 OpenAI Tools / Anthropic Tool Use 共用的 JSON Schema。"""
	properties: dict[str, Any] = {}
	required: list[str] = []

	for arg in cmd_spec.get("args", []):
		arg_name = arg["name"]
		properties[arg_name] = {
			"type": "string",
			"description": arg.get("description", ""),
		}
		if arg.get("required"):
			required.append(arg_name)

	for opt_key, opt_spec in cmd_spec.get("options", {}).items():
		# 去掉短/长选项前缀，保留长选项作为参数名
		primary_name = opt_key.split(",")[-1].strip().lstrip("-").replace("-", "_")
		properties[primary_name] = _option_to_json_schema_property(opt_spec)

	schema: dict[str, Any] = {
		"type": "object",
		"properties": properties,
	}
	if required:
		schema["required"] = required
	return schema


def _format_openai_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""OpenAI Functions / Tools API 格式。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		tools.append({
			"type": "function",
			"function": {
				"name": f"boss_{cmd_name.replace('-', '_')}",
				"description": cmd_spec.get("description", ""),
				"parameters": _command_to_json_schema(cmd_name, cmd_spec),
			},
		})
	return tools


def _format_anthropic_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""Anthropic Tool Use 格式。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		tools.append({
			"name": f"boss_{cmd_name.replace('-', '_')}",
			"description": cmd_spec.get("description", ""),
			"input_schema": _command_to_json_schema(cmd_name, cmd_spec),
		})
	return tools


SCHEMA_DATA = {
	"name": "boss-agent-cli",
	"description": "BOSS直聘求职工具。32 个命令覆盖搜索、筛选、打招呼、沟通、流水线、简历优化全流程。",
	"commands": {
		"login": {
			"description": "登录 BOSS 直聘（四级降级：Cookie 提取 → CDP → QR httpx 扫码 → patchright 扫码）",
			"args": [],
			"options": {
				"--timeout": {
					"type": "int",
					"default": 120,
					"description": "登录超时时间（秒）",
				},
				"--cdp": {
					"type": "bool",
					"default": False,
					"description": "强制 CDP 模式（跳过 Cookie 提取，CDP 不可用直接报错）",
				},
			},
		},
		"status": {
			"description": "检查当前登录态",
			"args": [],
			"options": {},
		},
		"doctor": {
			"description": "诊断本地运行环境、依赖、登录条件和网络连通性",
			"args": [],
			"options": {},
		},
		"search": {
			"description": "按关键词和筛选条件搜索职位列表",
			"args": [
				{"name": "query", "required": True, "description": "搜索关键词"},
			],
			"options": {
				"--city": {
					"type": "string",
					"default": None,
					"description": "城市名称（如 北京、上海）",
				},
				"--salary": {
					"type": "string",
					"default": None,
					"description": "薪资范围（如 10-20K）",
				},
				"--experience": {
					"type": "string",
					"default": None,
					"description": "经验要求（如 3-5年）",
				},
				"--education": {
					"type": "string",
					"default": None,
					"description": "学历要求（如 本科）",
				},
				"--industry": {
					"type": "string",
					"default": None,
					"description": "行业类型",
					"choices": ["不限", "互联网", "电子商务", "游戏", "软件/信息服务", "人工智能", "大数据", "云计算", "区块链", "物联网", "金融", "银行", "保险", "证券/基金", "教育培训", "医疗健康", "房地产", "汽车", "物流/运输", "广告/传媒", "消费品", "制造业", "能源/环保", "政府/非营利", "农业"],
				},
				"--scale": {
					"type": "string",
					"default": None,
					"description": "公司规模（如 100-499人）",
					"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
				},
				"--stage": {
					"type": "string",
					"default": None,
					"description": "融资阶段（如 已上市、A轮）",
					"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
				},
				"--job-type": {
					"type": "string",
					"default": None,
					"description": "职位类型（全职/兼职/实习）",
					"choices": ["全职", "兼职", "实习"],
				},
				"--welfare": {
					"type": "string",
					"default": None,
					"description": "福利筛选关键词（如 双休、五险一金）。启用后会逐个检查职位详情，自动翻页直到找到匹配结果",
					"examples": ["双休", "五险一金", "年终奖", "餐补", "住房补贴"],
				},
				"--page": {
					"type": "int",
					"default": 1,
					"description": "页码",
				},
				"--with-score": {
					"type": "bool",
					"default": False,
					"description": "附加匹配分和原因",
				},
				"--no-cache": {
					"type": "bool",
					"default": False,
					"description": "跳过缓存，强制请求接口",
				},
			},
		},
		"detail": {
			"description": "查看职位完整信息（职位描述、地址、招聘者信息）。传入 --job-id 走 httpx 快速通道（毫秒级），否则先查缓存、最后降级浏览器通道（秒级）",
			"args": [
				{"name": "security_id", "required": True, "description": "安全 ID，从 search/chat/recommend 结果中获取"},
			],
			"options": {
				"--job-id": {
					"type": "string",
					"default": "",
					"description": "职位加密 ID（从 search/chat 结果的 encrypt_job_id 获取，传入时走 httpx 快速通道，跳过浏览器）",
				},
				"--lid": {
					"type": "string",
					"default": "",
					"description": "列表项 ID（可选，提高匹配精度）",
				},
			},
		},
		"greet": {
			"description": "向指定招聘者打招呼",
			"args": [
				{"name": "security_id", "required": True, "description": "安全 ID"},
				{"name": "job_id", "required": True, "description": "加密职位 ID"},
			],
			"options": {
				"--message": {
					"type": "string",
					"default": "",
					"description": "自定义打招呼消息",
				},
			},
		},
		"batch-greet": {
			"description": "搜索后批量打招呼（上限 10）",
			"args": [
				{"name": "query", "required": True, "description": "搜索关键词"},
			],
			"options": {
				"--city": {
					"type": "string",
					"default": None,
					"description": "城市名称",
				},
				"--salary": {
					"type": "string",
					"default": None,
					"description": "薪资范围",
				},
				"--experience": {
					"type": "string",
					"default": None,
					"description": "经验要求（如 3-5年）",
				},
				"--education": {
					"type": "string",
					"default": None,
					"description": "学历要求（如 本科）",
				},
				"--industry": {
					"type": "string",
					"default": None,
					"description": "行业类型",
					"choices": ["不限", "互联网", "电子商务", "游戏", "软件/信息服务", "人工智能", "大数据", "云计算", "区块链", "物联网", "金融", "银行", "保险", "证券/基金", "教育培训", "医疗健康", "房地产", "汽车", "物流/运输", "广告/传媒", "消费品", "制造业", "能源/环保", "政府/非营利", "农业"],
				},
				"--scale": {
					"type": "string",
					"default": None,
					"description": "公司规模（如 100-499人）",
					"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
				},
				"--stage": {
					"type": "string",
					"default": None,
					"description": "融资阶段（如 已上市、A轮）",
					"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
				},
				"--job-type": {
					"type": "string",
					"default": None,
					"description": "职位类型（全职/兼职/实习）",
					"choices": ["全职", "兼职", "实习"],
				},
				"--count": {
					"type": "int",
					"default": 10,
					"description": "打招呼数量上限（最大 10）",
				},
				"--dry-run": {
					"type": "bool",
					"default": False,
					"description": "仅模拟执行，不实际打招呼",
				},
			},
		},
		"recommend": {
			"description": "基于简历的个性化职位推荐",
			"args": [],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
				"--with-score": {"type": "bool", "default": False, "description": "附加匹配分和原因"},
			},
		},
		"export": {
			"description": "导出搜索结果为 HTML / CSV / JSON 文件",
			"args": [
				{"name": "query", "required": True, "description": "搜索关键词"},
			],
			"options": {
				"--city": {"type": "string", "default": None, "description": "城市名称"},
				"--salary": {"type": "string", "default": None, "description": "薪资范围"},
				"--count": {"type": "int", "default": 50, "description": "导出数量"},
				"--format": {"type": "string", "default": "csv", "description": "输出格式", "enum": ["html", "csv", "json"]},
				"--output": {"type": "string", "default": None, "description": "输出文件路径（不指定则输出到 stdout）"},
			},
		},
		"cities": {
			"description": "列出所有支持的城市",
			"args": [],
			"options": {},
		},
		"me": {
			"description": "获取当前登录用户的个人信息（基本信息、简历、求职期望、投递记录）",
			"args": [],
			"options": {
				"--section": {
					"type": "string",
					"default": None,
					"choices": ["user", "resume", "expect", "deliver"],
					"description": "只获取指定部分（不指定则获取全部）",
				},
				"--deliver-page": {
					"type": "int",
					"default": 1,
					"description": "投递记录页码",
				},
			},
		},
		"show": {
			"description": "按编号查看搜索/推荐结果中的职位详情（先 search/recommend 后使用）",
			"args": [
				{"name": "index", "required": True, "description": "搜索结果编号（1-based）"},
			],
			"options": {},
		},
		"history": {
			"description": "查看最近浏览过的职位",
			"args": [],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
			},
		},
		"chat": {
			"description": "查看沟通列表（支持按发起方、时间筛选，支持导出 html/md/csv/json）",
			"args": [],
			"options": {
				"--from": {
					"type": "string",
					"default": None,
					"description": "筛选发起方：boss=对方主动联系 / me=我主动打招呼",
					"choices": ["boss", "me"],
				},
				"--days": {
					"type": "int",
					"default": None,
					"description": "只显示最近 N 天的记录",
				},
				"--export": {
					"type": "string",
					"default": None,
					"description": "导出格式：html=HTML / md=Markdown / csv=CSV / json=JSON",
					"choices": ["html", "md", "csv", "json"],
				},
				"-o/--output": {
					"type": "string",
					"default": None,
					"description": "输出文件路径（不指定则自动保存到 config.export_dir，默认 ~/Documents/files/boss，按日期命名同天覆盖）",
				},
				"--page": {
					"type": "int",
					"default": 1,
					"description": "页码",
				},
			},
		},
		"chatmsg": {
			"description": "查看与指定好友的聊天消息历史",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
				"--count": {"type": "int", "default": 20, "description": "每页消息数量"},
			},
		},
		"chat-summary": {
			"description": "基于聊天历史生成结构化摘要与下一步建议",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
				"--count": {"type": "int", "default": 20, "description": "每页消息数量"},
			},
		},
		"mark": {
			"description": "给联系人添加/移除标签（新招呼/沟通中/已约面/不合适/收藏等）",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--label": {"type": "string", "required": True, "description": "标签名称或 ID", "enum": ["新招呼", "沟通中", "已约面", "已获取简历", "已交换电话", "已交换微信", "不合适", "收藏"]},
				"--remove": {"type": "boolean", "default": False, "description": "移除标签（默认为添加）"},
			},
		},
		"exchange": {
			"description": "请求交换联系方式（手机号或微信）",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--type": {"type": "string", "default": "phone", "description": "交换类型", "enum": ["phone", "wechat"]},
			},
		},
		"interviews": {
			"description": "查看面试邀请列表",
			"args": [],
			"options": {},
		},
		"logout": {
			"description": "退出登录，清除本地保存的登录态",
			"args": [],
			"options": {},
		},
		"watch": {
			"description": "保存搜索条件并执行增量监控（子命令：add/list/remove/run）",
			"args": [],
			"options": {},
		},
		"preset": {
			"description": "管理可复用搜索预设（子命令：add/list/remove）",
			"args": [],
			"options": {},
		},
		"pipeline": {
			"description": "聚合聊天和面试数据，生成统一候选进度视图",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "description": "超过 N 天未推进则标记为 follow_up"},
			},
		},
		"follow-up": {
			"description": "筛出需要优先跟进的候选项（未读、超时未推进、面试）",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "description": "超过 N 天未推进则视为 follow_up"},
			},
		},
		"apply": {
			"description": "发起最小可用投递/立即沟通动作（当前复用立即沟通链路）",
			"args": [
				{"name": "security_id", "required": True, "description": "安全 ID"},
				{"name": "job_id", "required": True, "description": "加密职位 ID"},
			],
			"options": {
				"--lid": {"type": "string", "default": "", "description": "列表项 ID（可选）"},
			},
		},
		"shortlist": {
			"description": "管理职位候选池（子命令：add/list/remove）",
			"args": [],
			"options": {},
		},
		"digest": {
			"description": "汇总新增职位、待跟进会话和面试项的只读日报（支持 JSON / Markdown 两种输出）",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "description": "超过 N 天未推进则视为 follow_up"},
				"--format": {"type": "string", "default": "json", "description": "输出格式（json 信封 / md 可直发邮件飞书）"},
				"-o, --output": {"type": "string", "default": None, "description": "Markdown 输出路径（仅 --format md 时有效）"},
			},
		},
		"config": {
			"description": "查看和修改配置项（子命令：list/get/set/reset）",
			"args": [],
			"options": {},
			"subcommands": {
				"list": "显示当前全部配置",
				"get": "查看单个配置项",
				"set": "修改配置项",
				"reset": "恢复配置项为默认值",
			},
		},
		"clean": {
			"description": "清理过期缓存和临时文件",
			"args": [],
			"options": {
				"--dry-run": {"type": "bool", "default": False, "description": "仅预览将清理的内容"},
				"--all": {"type": "bool", "default": False, "description": "清理全部缓存"},
				"--days": {"type": "int", "default": 30, "description": "清理超过指定天数的快照和导出"},
			},
		},
		"stats": {
			"description": "投递转化漏斗统计（只读聚合打招呼/投递/候选池/监控）",
			"args": [],
			"options": {
				"--days": {"type": "int", "default": 30, "description": "统计窗口天数"},
				"--format": {"type": "string", "default": "json", "description": "输出格式：json（JSON 信封）或 html（自包含报表）"},
				"-o, --output": {"type": "string", "default": None, "description": "HTML 输出路径（仅 --format html 时有效）"},
			},
		},
		"resume": {
			"description": "本地简历管理（子命令：init/list/show/edit/delete/export/import/clone/diff/link/applications）",
			"args": [],
			"options": {},
			"subcommands": {
				"init": "从 BOSS 直聘简历或默认模板初始化本地简历",
				"list": "列出所有本地简历",
				"show": "查看简历详情",
				"edit": "编辑简历字段",
				"delete": "删除简历",
				"export": "导出为 PDF/JSON/HTML",
				"import": "导入 JSON 简历（兼容 wzdnzd/zine0 格式）",
				"clone": "复制简历为新版本",
				"diff": "对比两份简历差异",
				"link": "关联简历与职位",
				"applications": "查看简历关联的所有职位",
			},
		},
		"ai": {
			"description": "AI 简历优化与聊天回复（子命令：config/analyze-jd/polish/optimize/suggest/reply/interview-prep/chat-coach）",
			"args": [],
			"options": {},
			"subcommands": {
				"config": "配置 AI 服务提供商和模型",
				"analyze-jd": "分析职位描述并评估简历匹配度",
				"polish": "通用简历润色",
				"optimize": "基于目标职位描述优化简历",
				"suggest": "基于目标职位描述给出优化建议（不修改简历）",
				"reply": "基于招聘者消息生成回复草稿（2-3 条候选）",
				"interview-prep": "基于目标职位生成模拟面试题与准备建议",
				"chat-coach": "基于聊天记录诊断沟通状态并给出下一步建议",
			},
		},
		"hr": {
			"description": "招聘者模式快捷命令",
			"args": [],
			"options": {},
			"subcommands": {
				"applications": "查看候选人投递申请列表",
				"resume": "查看或请求候选人简历",
				"chat": "查看与候选人的沟通列表",
				"jobs": "管理职位发布（list/offline/online）",
				"candidates": "搜索候选人",
				"reply": "回复候选人消息",
				"request-resume": "请求候选人分享附件简历",
			},
		},
	},
	"global_options": {
		"--data-dir": {
			"type": "string",
			"default": "~/.boss-agent",
			"description": "数据存储目录",
		},
		"--delay": {
			"type": "string",
			"default": "1.5-3.0",
			"description": "请求间隔范围（秒），如 1.5-3.0",
		},
		"--log-level": {
			"type": "string",
			"default": "error",
			"choices": ["error", "warning", "info", "debug"],
			"description": "日志级别",
		},
		"--cdp-url": {
			"type": "string",
			"default": None,
			"description": "Chrome CDP 调试地址（如 http://localhost:9222），启用后优先用用户 Chrome 发请求",
		},
		"--platform": {
			"type": "string",
			"default": "zhipin",
			"description": "招聘平台适配器（zhipin=BOSS 直聘生产可用；zhilian=智联招聘 stub，Week 2 真实现）",
			"choices": ["zhipin", "zhilian"],
		},
		"--json": {
			"type": "bool",
			"default": False,
			"description": "强制 JSON 输出（即使在终端中，默认管道模式自动 JSON）",
		},
		"--role": {
			"type": "string",
			"default": "candidate",
			"description": "角色模式：candidate（求职者）/ recruiter（招聘者）",
			"choices": ["candidate", "recruiter"],
		},
	},
	"error_codes": {
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
		"TOKEN_REFRESH_FAILED": {
			"message": "Token 刷新失败",
			"recoverable": True,
			"recovery_action": "boss login",
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
			"recoverable": True,
			"recovery_action": "启动 CDP Chrome 重试，或联系客服",
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
		"INVALID_PARAM": {
			"message": "参数校验失败",
			"recoverable": False,
			"recovery_action": "修正参数",
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
			"recovery_action": "使用 resume <id> --request 请求简历",
		},
		"JOB_POST_LIMIT": {
			"message": "职位发布数量已达上限",
			"recoverable": False,
			"recovery_action": None,
		},
	},
	"conventions": {
		"stdout": "仅 JSON 结构化数据（信封格式）",
		"stderr": "日志和进度信息（通过 --log-level 控制）",
		"exit_code": {
			"0": "命令成功 (ok=true)",
			"1": "命令失败 (ok=false)",
		},
	},
}


@click.command("schema")
@click.option(
	"--format", "output_format",
	type=click.Choice(["native", "openai-tools", "anthropic-tools"]),
	default="native",
	help="输出格式：native（本项目信封）/ openai-tools（OpenAI Functions & Tools API）/ anthropic-tools（Claude Tool Use API）",
)
@click.pass_context
def schema_cmd(ctx: click.Context, output_format: str) -> None:
	"""返回工具完整能力描述的 JSON"""
	# 动态注入当前会话的平台信息（Issue #129 Week 1b）
	data = dict(SCHEMA_DATA)
	current = (ctx.obj or {}).get("platform") or "zhipin"
	data["current_platform"] = current
	data["current_role"] = (ctx.obj or {}).get("role") or "candidate"
	data["supported_platforms"] = list_platforms()

	if output_format == "openai-tools":
		emit_success("schema", {"format": "openai-tools", "tools": _format_openai_tools(data)})
		return
	if output_format == "anthropic-tools":
		emit_success("schema", {"format": "anthropic-tools", "tools": _format_anthropic_tools(data)})
		return
	emit_success("schema", data)
