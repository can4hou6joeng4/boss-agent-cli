from typing import Any, cast

import click

from boss_agent_cli.compliance import compliance_mode_data
from boss_agent_cli.output import emit_success
from boss_agent_cli.platforms import list_platforms, list_recruiter_platforms
from boss_agent_cli.wizard.catalog import catalog_data

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
		if not opt_key.startswith("-"):
			continue
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


_ROLE_BOTH_COMMANDS = {
	"login",
	"status",
	"doctor",
	"logout",
	"schema",
	"config",
	"clean",
	"cities",
	"platforms",
	"wizard",
}

_CANDIDATE_COMMANDS = {
	"search",
	"detail",
	"recommend",
	"greet",
	"batch-greet",
	"export",
	"me",
	"show",
	"history",
	"chat",
	"chatmsg",
	"chat-summary",
	"mark",
	"exchange",
	"interviews",
	"watch",
	"preset",
	"pipeline",
	"follow-up",
	"apply",
	"shortlist",
	"favorites",
	"digest",
	"stats",
	"resume",
	"ai",
}

_QIANCHENG_PLACEHOLDER_COMMANDS = {
	"search",
	"detail",
	"recommend",
	"me",
	"show",
}


def _availability_note(availability: dict[str, Any]) -> str:
	roles = ", ".join(availability.get("roles", [])) or "none"
	candidate_platforms = ", ".join(availability.get("candidate_platforms", [])) or "-"
	recruiter_platforms = ", ".join(availability.get("recruiter_platforms", [])) or "-"
	return (
		f"可用性: roles={roles}; candidate_platforms={candidate_platforms}; recruiter_platforms={recruiter_platforms}"
	)


def _command_availability(
	cmd_name: str,
	*,
	candidate_platforms: list[str],
	recruiter_platforms: list[str],
) -> dict[str, Any]:
	if cmd_name == "agent":
		return {
			"roles": ["candidate", "recruiter"],
			"candidate_platforms": ["zhipin"],
			"recruiter_platforms": ["zhilian", "zhipin"],
			"note": (
				"agent run/train 等为招聘者自动化；agent crawl 为候选人本地编排，"
				"可新建 crawl 或分析已完成的 crawl run。"
			),
		}
	if cmd_name == "hr":
		commands = cast(dict[str, Any], SCHEMA_DATA.get("commands", {}))
		hr_spec = commands.get("hr", {})
		if not isinstance(hr_spec, dict):
			hr_spec = {}
		subcommands = hr_spec.get("subcommands", {})
		if not isinstance(subcommands, dict):
			subcommands = {}
		subcommand_availability = {
			sub_name: {
				"roles": ["recruiter"],
				"candidate_platforms": [],
				"recruiter_platforms": recruiter_platforms,
			}
			for sub_name in subcommands
		}
		return {
			"roles": ["recruiter"],
			"candidate_platforms": [],
			"recruiter_platforms": recruiter_platforms,
			"subcommands": subcommand_availability,
		}
	availability_candidate_platforms = list(candidate_platforms)
	placeholder_note = "qiancheng/51job 当前为稳定 NOT_SUPPORTED 占位适配器，列入候选平台仅表示可选择与可发现。"
	include_qiancheng_placeholder = cmd_name in _QIANCHENG_PLACEHOLDER_COMMANDS
	if include_qiancheng_placeholder and "qiancheng" not in availability_candidate_platforms:
		availability_candidate_platforms = ["qiancheng", *availability_candidate_platforms]
	if cmd_name in _ROLE_BOTH_COMMANDS:
		availability: dict[str, Any] = {
			"roles": ["candidate", "recruiter"],
			"candidate_platforms": availability_candidate_platforms,
			"recruiter_platforms": recruiter_platforms,
		}
		if include_qiancheng_placeholder:
			availability["note"] = placeholder_note
		return availability
	if cmd_name in _CANDIDATE_COMMANDS:
		availability = {
			"roles": ["candidate"],
			"candidate_platforms": availability_candidate_platforms,
			"recruiter_platforms": [],
		}
		if include_qiancheng_placeholder:
			availability["note"] = placeholder_note
		return availability
	availability = {
		"roles": ["candidate"],
		"candidate_platforms": availability_candidate_platforms,
		"recruiter_platforms": [],
	}
	if include_qiancheng_placeholder:
		availability["note"] = placeholder_note
	return availability


def _inject_availability(data: dict[str, Any]) -> dict[str, Any]:
	# supported_platforms 表示“已注册 / 可选择”的平台；availability 表示命令真实可用的
	# 候选者平台。qiancheng/51job 当前仍是 NOT_SUPPORTED 占位 adapter，仅在
	# 对应占位能力命令中通过 availability.note 明示不可调度真实平台能力。
	candidate_platforms = ["zhilian", "zhipin"]
	recruiter_platforms = data.get("supported_recruiter_platforms", [])
	commands: dict[str, Any] = {}
	for cmd_name, cmd_spec in data["commands"].items():
		cmd_copy = dict(cmd_spec)
		cmd_copy["availability"] = _command_availability(
			cmd_name,
			candidate_platforms=candidate_platforms,
			recruiter_platforms=recruiter_platforms,
		)
		commands[cmd_name] = cmd_copy
	data["commands"] = commands
	return data


def _format_openai_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""OpenAI Functions / Tools API 格式。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		description = cmd_spec.get("description", "")
		if availability := cmd_spec.get("availability"):
			description = f"{description} [{_availability_note(availability)}]"
		tools.append(
			{
				"type": "function",
				"function": {
					"name": f"boss_{cmd_name.replace('-', '_')}",
					"description": description,
					"parameters": _command_to_json_schema(cmd_name, cmd_spec),
				},
			}
		)
	return tools


def _format_anthropic_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""Anthropic Tool Use 格式。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		description = cmd_spec.get("description", "")
		if availability := cmd_spec.get("availability"):
			description = f"{description} [{_availability_note(availability)}]"
		tools.append(
			{
				"name": f"boss_{cmd_name.replace('-', '_')}",
				"description": description,
				"input_schema": _command_to_json_schema(cmd_name, cmd_spec),
			}
		)
	return tools


def _format_mcp_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
	"""Model Context Protocol Tools 格式（与 Anthropic 同结构，键名 inputSchema）。"""
	tools = []
	for cmd_name, cmd_spec in data["commands"].items():
		if mcp_tools := cmd_spec.get("mcp_tools"):
			availability = cmd_spec.get("availability")
			for tool in mcp_tools:
				description = tool["description"]
				if availability:
					description = f"{description} [{_availability_note(availability)}]"
				tools.append({
					"name": tool["name"],
					"description": description,
					"inputSchema": tool["inputSchema"],
				})
			continue
		if cmd_spec.get("mcp_exposed") is False:
			continue
		description = cmd_spec.get("description", "")
		if availability := cmd_spec.get("availability"):
			description = f"{description} [{_availability_note(availability)}]"
		tools.append(
			{
				"name": f"boss_{cmd_name.replace('-', '_')}",
				"description": description,
				"inputSchema": _command_to_json_schema(cmd_name, cmd_spec),
			}
		)
	return tools


SCHEMA_DATA = {
	"name": "boss-agent-cli",
	"description": "面向真人和 Agent 的招聘平台 CLI，共 39 个顶层命令；所有已实现能力均可直接调用。",
	"commands": {
		"login": {
			"description": "按当前平台登录（zhipin / zhilian）；两种兼容运行模式共享相同能力，平台风控仍会停止当前流程。",
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
		"platforms": {
			"description": "列出本地已注册平台与能力状态；只读本地元数据，不触发登录、浏览器、CDP 或网络请求",
			"args": [],
			"options": {
				"--platform": {
					"type": "string",
					"default": None,
					"description": "仅查看指定平台（支持 qiancheng / 51job 等已注册平台或别名）",
				},
				"--capability": {
					"type": "string",
					"default": None,
					"description": "按现有本地能力矩阵反查平台状态；返回 available / placeholder / not_supported，blocked_by_policy 仅保留空兼容分组",
					"choices": ["search", "detail", "recommend", "me", "status", "greet", "apply", "shortlist", "stats", "config", "schema"],
				},
			},
		},
		"status": {
			"description": "轻量检查当前登录态分层健康状态；默认不请求平台，--live 才执行一次只读在线验证",
			"args": [],
			"options": {
				"--live": {
					"type": "bool",
					"default": False,
					"description": "执行一次只读 user_info 在线验证；默认仅检查本地凭据完整性",
				},
			},
		},
		"doctor": {
			"description": "诊断本地运行环境、依赖、分层认证健康、CDP/Bridge 可达性和网络连通性；默认不做真实业务探测，浏览器桥仅用于用户主动的本地诊断与登录兼容，不得用于规避平台风控",
			"args": [],
			"options": {
				"--live-probe": {
					"type": "bool",
					"default": False,
					"description": "显式执行低频只读平台探测，用于区分本地凭据完整但接口不可用的状态",
				},
			},
		},
		"schema": {
			"description": "返回工具完整能力描述的 JSON",
			"args": [],
			"options": {
				"--format": {
					"type": "string",
					"default": "native",
					"description": "输出格式",
					"choices": ["native", "openai-tools", "anthropic-tools", "mcp-tools"],
				},
			},
		},
		"wizard": {
			"description": "启动真人纯向导，或通过 JSON 执行、恢复、查询和停止共享 workflow；role/platform/goal 取值见顶层 wizard_catalog",
			"args": [],
			"options": {
				"--input-json": {"type": "string", "default": None, "description": "包含 role/platform/goal/inputs/requested_steps 的 JSON object"},
				"--resume": {"type": "string", "default": None, "description": "恢复指定 workflow run_id"},
				"--status": {"type": "string", "default": None, "description": "查询指定 workflow run_id"},
				"--stop": {"type": "string", "default": None, "description": "停止指定 workflow run_id"},
				"--timeout": {"type": "float", "default": None, "description": "workflow 超时秒数"},
				"--max-retries": {"type": "int", "default": 0, "description": "可恢复步骤的最大重试次数"},
			},
		},
		"search": {
			"description": "按关键词和筛选条件搜索职位列表，可传入 BOSS 直聘搜索页 URL 复用网页筛选参数",
			"args": [
				{"name": "query", "required": False, "description": "搜索关键词；提供 --url 时可省略"},
			],
			"options": {
				"--url": {
					"type": "string",
					"default": None,
					"description": "BOSS 直聘搜索页 URL（可从网页复制完整筛选条件）",
				},
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
					"description": "经验要求（如 3-5年），支持逗号分隔多选",
				},
				"--education": {
					"type": "string",
					"default": None,
					"description": "学历要求（如 本科），支持逗号分隔多选",
				},
				"--industry": {
					"type": "string",
					"default": None,
					"description": "行业类型，支持逗号分隔多选",
					"choices": [
						"不限",
						"互联网",
						"电子商务",
						"游戏",
						"软件/信息服务",
						"人工智能",
						"大数据",
						"云计算",
						"区块链",
						"物联网",
						"金融",
						"银行",
						"保险",
						"证券/基金",
						"教育培训",
						"医疗健康",
						"房地产",
						"汽车",
						"物流/运输",
						"广告/传媒",
						"消费品",
						"制造业",
						"能源/环保",
						"政府/非营利",
						"农业",
					],
				},
				"--scale": {
					"type": "string",
					"default": None,
					"description": "公司规模（如 100-499人），支持逗号分隔多选",
					"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
				},
				"--stage": {
					"type": "string",
					"default": None,
					"description": "融资阶段（如 已上市、A轮），支持逗号分隔多选",
					"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
				},
				"--job-type": {
					"type": "string",
					"default": None,
					"description": "职位类型（全职/兼职/实习），支持逗号分隔多选",
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
				"--sort": {
					"type": "string",
					"default": "relevance",
					"description": "排序方式：relevance 保持平台返回顺序；score 按本地 match_score 降序",
					"choices": ["relevance", "score"],
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
				{
					"name": "security_id",
					"required": True,
					"description": "安全 ID，从 search/chat/recommend 结果中获取",
				},
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
			"description": "向指定招聘者打招呼。",
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
			"description": "搜索后按显式数量上限批量打招呼。",
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
					"choices": [
						"不限",
						"互联网",
						"电子商务",
						"游戏",
						"软件/信息服务",
						"人工智能",
						"大数据",
						"云计算",
						"区块链",
						"物联网",
						"金融",
						"银行",
						"保险",
						"证券/基金",
						"教育培训",
						"医疗健康",
						"房地产",
						"汽车",
						"物流/运输",
						"广告/传媒",
						"消费品",
						"制造业",
						"能源/环保",
						"政府/非营利",
						"农业",
					],
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
			"description": "基于用户登录态获取个性化职位推荐。",
			"args": [],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
				"--with-score": {"type": "bool", "default": False, "description": "附加匹配分和原因"},
			},
		},
		"export": {
			"description": "导出搜索结果为 HTML / CSV / JSON 文件，可传入 BOSS 直聘搜索页 URL 复用网页筛选参数",
			"args": [
				{"name": "query", "required": False, "description": "搜索关键词；提供 --url 时可省略"},
			],
			"options": {
				"--url": {"type": "string", "default": None, "description": "BOSS 直聘搜索页 URL（可从网页复制完整筛选条件）"},
				"--city": {"type": "string", "default": None, "description": "城市名称"},
				"--salary": {"type": "string", "default": None, "description": "薪资范围"},
				"--experience": {"type": "string", "default": None, "description": "经验要求，支持逗号分隔多选"},
				"--education": {"type": "string", "default": None, "description": "学历要求，支持逗号分隔多选"},
				"--industry": {"type": "string", "default": None, "description": "行业类型，支持逗号分隔多选"},
				"--scale": {"type": "string", "default": None, "description": "公司规模，支持逗号分隔多选"},
				"--stage": {"type": "string", "default": None, "description": "融资阶段，支持逗号分隔多选"},
				"--job-type": {"type": "string", "default": None, "description": "职位类型，支持逗号分隔多选"},
				"--count": {"type": "int", "default": 50, "description": "导出数量"},
				"--format": {
					"type": "string",
					"default": "csv",
					"description": "输出格式",
					"enum": ["html", "csv", "json"],
				},
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
			"description": "查看沟通列表或导出会话摘要。",
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
			"description": "查看与指定好友的聊天消息历史；--raw 输出保真结构化消息字段。",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
				"--count": {"type": "int", "default": 20, "description": "每页消息数量"},
				"--raw": {"type": "bool", "default": False, "description": "保真输出结构化 body、链接、职位卡片字段和原始消息对象"},
			},
		},
		"chat-summary": {
			"description": "基于聊天历史生成结构化摘要与下一步建议。",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--page": {"type": "int", "default": 1, "description": "页码"},
				"--count": {"type": "int", "default": 20, "description": "每页消息数量"},
			},
		},
		"mark": {
			"description": "给联系人添加或移除标签。",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--label": {
					"type": "string",
					"required": True,
					"description": "标签名称或 ID",
					"enum": ["新招呼", "沟通中", "已约面", "已获取简历", "已交换电话", "已交换微信", "不合适", "收藏"],
				},
				"--remove": {"type": "boolean", "default": False, "description": "移除标签（默认为添加）"},
			},
		},
		"exchange": {
			"description": "请求交换联系方式（手机号或微信）。",
			"args": [
				{"name": "security_id", "required": True, "description": "联系人的 security_id（从 chat 命令获取）"},
			],
			"options": {
				"--type": {
					"type": "string",
					"default": "phone",
					"description": "交换类型",
					"enum": ["phone", "wechat"],
				},
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
			"description": "本地保存搜索条件，并可通过 run 子命令增量拉取平台数据。",
			"args": [],
			"options": {},
		},
		"crawl": {
			"description": "可恢复的 DrissionPage 批量采集（子命令：configure/run/start/status/results/resume/stop）；风险码或安全页会保存断点后停止。",
			"args": [],
			"options": {
				"run": {
					"--city": {"type": "string", "required": True, "description": "城市名称或数字城市代码"},
					"--pages": {"type": "int", "default": 5, "minimum": 1, "description": "严格正数页数上限"},
					"--with-detail": {"type": "bool", "default": False, "description": "串行补全所有职位的 job_card"},
					"--hook-profile": {"type": "string", "default": "none", "enum": ["screenshot-full", "none"]},
					"--hook-dir": {"type": "string", "default": None, "description": "screenshot-full 必填：用户已授权的原始 Hook 目录，须含 SHA256SUMS"},
				},
				"resume": {
					"--pages": {"type": "int", "default": None, "minimum": 1, "description": "覆盖原任务的严格正数页数上限"},
					"--with-detail": {"type": "bool", "default": False, "description": "补全已采职位和后续职位的 job_card"},
					"--background": {"type": "bool", "default": False, "description": "后台恢复并立即返回 run_id"},
				},
				"results": {
					"--page": {"type": "int", "default": None, "description": "仅返回指定采集页"},
					"--detail-status": {"type": "string", "default": None, "enum": ["completed", "pending"]},
				},
				"shortlist": {
					"--selector": {"type": "string", "default": None, "description": "导入 results 返回的非敏感 selector，可重复传入"},
					"--all": {"type": "bool", "default": False, "description": "导入该 run 的全部可关联职位"},
					"--tags": {"type": "string", "default": "", "description": "写入候选池的本地标签，逗号分隔"},
					"--note": {"type": "string", "default": "", "description": "写入候选池的本地备注"},
				},
			},
			"subcommands": {
				"configure": "设置 crawl 专用 Chrome 路径、端口和固定预算",
				"run <query>": "开始可恢复的批量职位采集",
				"start <query>": "创建后台任务并立即返回 run_id（供 MCP 轮询）",
				"status <run_id>": "读取页游标、详情进度和风险状态",
				"results <run_id>": "读取已持久化职位结果",
				"resume <run_id>": "从已保存页游标和详情队列继续",
				"stop <run_id>": "请求运行中的 crawl 在下一个安全点停止并保留断点",
				"shortlist <run_id>": "将 crawl 结果导入本地职位候选池",
			},
			"mcp_tools": [
				{
					"name": "boss_crawl_status",
					"description": "读取 crawl 页游标、职位数、详情进度和风险状态。",
					"inputSchema": {
						"type": "object",
						"properties": {
							"run_id": {"type": "string", "description": "crawl run 标识，由 boss crawl start 返回"},
						},
						"required": ["run_id"],
					},
				},
				{
					"name": "boss_crawl_results",
					"description": "读取持久化 crawl 职位，可按页码和详情状态筛选。",
					"inputSchema": {
						"type": "object",
						"properties": {
							"run_id": {"type": "string", "description": "crawl run 标识，由 boss crawl start 返回"},
							"page": {"type": "integer", "description": "只返回该 crawl 页的结果，省略则返回全部"},
							"detail_status": {
								"type": "string",
								"enum": ["completed", "pending"],
								"description": "按职位详情抓取状态筛选：completed 已补全详情，pending 仅有列表信息",
							},
						},
						"required": ["run_id"],
					},
				},
				{
					"name": "boss_crawl_shortlist",
					"description": "将 crawl results 返回的 selector 导入本地 shortlist，不请求 BOSS。",
					"inputSchema": {
						"type": "object",
						"properties": {
							"run_id": {"type": "string", "description": "crawl run 标识，由 boss crawl start 返回"},
							"selectors": {
								"type": "array",
								"items": {"type": "string"},
								"description": "要导入的职位 selector 列表，取自 boss_crawl_results 的返回；与 all 二选一",
							},
							"all": {
								"type": "boolean",
								"default": False,
								"description": "导入该 run 的全部可关联职位；与 selectors 二选一",
							},
							"tags": {"type": "string", "description": "写入候选池的本地标签，逗号分隔"},
							"note": {"type": "string", "description": "写入候选池的本地备注"},
						},
						"required": ["run_id"],
					},
				},
			],
		},
		"preset": {
			"description": "管理可复用搜索预设（子命令：add/list/remove）",
			"args": [],
			"options": {},
		},
		"pipeline": {
			"description": "聚合聊天和面试数据生成候选进度视图。",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "description": "超过 N 天未推进则标记为 follow_up"},
			},
		},
		"follow-up": {
			"description": "基于聊天和面试数据筛出需要跟进的候选项。",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "description": "超过 N 天未推进则视为 follow_up"},
			},
		},
		"apply": {
			"description": "发起投递或立即沟通动作。",
			"args": [
				{"name": "security_id", "required": True, "description": "安全 ID"},
				{"name": "job_id", "required": True, "description": "加密职位 ID"},
			],
			"options": {
				"--lid": {"type": "string", "default": "", "description": "列表项 ID（可选）"},
			},
		},
		"shortlist": {
			"description": "管理本地职位候选池（子命令：add/list/annotate/compare/remove），支持本地标签、备注和离线对比",
			"args": [],
			"options": {
				"add": {
					"--tags": {"type": "string", "default": "", "description": "本地标签，逗号分隔"},
					"--note": {"type": "string", "default": "", "description": "本地备注"},
				},
				"annotate": {
					"--add-tag": {"type": "string", "default": None, "description": "添加本地标签，可重复"},
					"--remove-tag": {"type": "string", "default": None, "description": "移除本地标签，可重复"},
					"--note": {"type": "string", "default": None, "description": "替换本地备注"},
				},
				"compare": {
					"--tag": {"type": "string", "default": None, "description": "只比较包含该本地标签的候选职位"},
				},
			},
			"subcommands": {
				"add": "加入本地候选池，可附加本地标签和备注",
				"list": "列出本地候选池职位",
				"annotate": "更新候选职位的本地标签和备注",
				"compare": "本地对比候选职位，可按标签过滤",
				"remove": "从本地候选池移除职位",
			},
		},
		"favorites": {
			"description": "读取 BOSS 职位收藏并同步到本地候选池（子命令：list/sync）。list 远端只读预览，sync 远端只读拉取后写入本地 shortlist（upsert）；默认低风险、用户主动触发。",
			"args": [],
			"options": {
				"list": {
					"--page": {"type": "int", "default": 1, "description": "页码"},
				},
				"sync": {},
			},
			"subcommands": {
				"list": "预览职位收藏单页（不落库）",
				"sync": "同步全部职位收藏到本地候选池（远端只读拉取，本地 upsert；刷新动态访问 ID 并保留首次收藏时间）",
			},
		},
		"digest": {
			"description": "汇总新增职位、待跟进会话和面试项的日报。",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "description": "超过 N 天未推进则视为 follow_up"},
				"--format": {
					"type": "string",
					"default": "json",
					"description": "输出格式（json 信封 / md 可直发邮件飞书）",
				},
				"-o, --output": {
					"type": "string",
					"default": None,
					"description": "Markdown 输出路径（仅 --format md 时有效）",
				},
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
				"--format": {
					"type": "string",
					"default": "json",
					"description": "输出格式：json（JSON 信封）或 html（自包含报表）",
				},
				"-o, --output": {
					"type": "string",
					"default": None,
					"description": "HTML 输出路径（仅 --format html 时有效）",
				},
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
			"description": "AI 简历优化、聊天回复与本地模型管理（子命令：config/local/analyze-jd/polish/optimize/suggest/fit/reply/interview-prep/chat-coach/suggest-keywords/resume-optimize/cover-letter）",
			"args": [],
			"options": {},
			"subcommands": {
				"config": "配置 AI 服务提供商和模型",
				"local": "本地模型状态、配置、下载、导入和 smoke 测试",
				"analyze-jd": "分析职位描述并评估简历匹配度",
				"polish": "通用简历润色",
				"optimize": "基于目标职位描述优化简历",
				"suggest": "基于目标职位描述给出优化建议（不修改简历）",
				"fit": "fit --resume <name> [--limit N]：本地简历 × 候选池缓存详情的匹配报告",
				"reply": "基于招聘者消息生成回复草稿（2-3 条候选）",
				"interview-prep": "基于目标职位生成模拟面试题与准备建议",
				"chat-coach": "基于聊天记录诊断沟通状态并给出下一步建议",
				"suggest-keywords": "基于候选池分析推荐搜索关键词组合",
				"resume-optimize": "基于目标岗位优化简历措辞（仅建议，不修改简历）",
				"cover-letter": "基于本地简历与目标岗位起草求职信/自我介绍（仅草稿，不发送）",
			},
		},
		"agent": {
			"description": (
				"招聘自动化与候选人 crawl 编排入口。run/train 可直接执行满足阈值的动作；"
				"review/pending 仅管理旧版本遗留队列；crawl 可新建或分析已有 run。"
			),
			"args": [],
			"options": {
				"--dry-run": {
					"type": "bool",
					"default": False,
					"description": "只演练自动化决策，不执行真实平台动作",
				},
				"--limit": {
					"type": "int",
					"default": None,
					"description": "本轮最多处理多少个会话",
				},
			},
			"subcommands": {
				"run": "运行一轮招聘自动化",
				"train": "训练校准模式：默认演练，--live 直接执行满足阈值的动作",
				"review list": "查看旧版本遗留的人工复核队列",
				"review approve <id>": "处理旧版本复核项并写入兼容 pending 队列",
				"review reject <id>": "拒绝旧版本复核项并记录跳过事件",
				"pending list": "查看旧版本遗留的待执行动作队列",
				"stats": "查看招聘自动化统计",
				"control": "查看本地控制台入口信息",
				"stop": "打开招聘自动化熔断",
				"crawl": "候选人链路：新建或读取 crawl → shortlist → ai fit",
			},
		},
		"hr": {
			"description": "招聘者模式快捷命令。已实现的候选人搜索、简历、沟通、联系方式交换和消息发送在 assisted/research 下均可调用。",
			"args": [],
			"options": {},
			"subcommands": {
				"applications": "查看候选人投递申请列表",
				"resume": "查看候选人在线简历或发起联系方式交换",
				"chat": "查看与候选人的沟通列表（含未读数和最近消息摘要）",
				"chatmsg": "查看与指定候选人的聊天消息历史",
				"last-messages": "批量查看候选人最近消息摘要",
				"jobs": "管理职位发布（list/offline/online/detail）",
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
			"description": "Chrome CDP 调试地址（兼容保留）。不得用于规避平台风控或重试被平台拦截的操作。",
		},
		"--browser-mode": {
			"type": "string",
			"default": "auto",
			"choices": ["auto", "cdp-required"],
			"description": "浏览器通道模式。cdp-required 要求连接指定 CDP，失败时禁止降级到 headless。",
		},
		"--platform": {
			"type": "string",
			"default": "zhipin",
			"description": "招聘平台适配器（zhipin=BOSS 直聘求职者/招聘者均可用；zhilian=智联招聘已接通求职者侧包络与命令兼容；qiancheng/51job=前程无忧占位适配器，当前稳定返回 NOT_SUPPORTED）",
			"choices": ["51job", "qiancheng", "zhipin", "zhilian"],
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
	},
	"conventions": {
		"stdout": "仅 JSON 结构化数据（信封格式）",
		"stderr": "日志和进度信息（通过 --log-level 控制）",
		"exit_code": {
			"0": "命令成功 (ok=true)",
			"1": "命令失败 (ok=false)",
		},
		"hints": {
			"next_actions": "面向 AI Agent 的后继命令（boss xxx 形式），由 Agent 直接执行",
			"operator_actions": "面向真人操作者的自然语言指引，通常需要离开终端完成"
			"（扫码、在浏览器里调整条件、处理风控验证等）；Agent 应转述给操作者，TTY 下渲染到 stderr",
		},
		"command_vs_wizard": "单次、无状态的能力调用走顶层命令；需要跨步骤状态、可恢复、"
		"或中途需要把指引递给真人操作者的走 boss wizard（goal 取值见 wizard_catalog）",
	},
}


@click.command("schema")
@click.option(
	"--format",
	"output_format",
	type=click.Choice(["native", "openai-tools", "anthropic-tools", "mcp-tools"]),
	default="native",
	help="输出格式：native（本项目信封）/ openai-tools（OpenAI Functions & Tools API）/ anthropic-tools（Claude Tool Use API）/ mcp-tools（Model Context Protocol Tools）",
)
@click.pass_context
def schema_cmd(ctx: click.Context, output_format: str) -> None:
	"""返回工具完整能力描述的 JSON"""
	# 动态注入当前会话的平台信息（Issue #129 Week 1b）
	data = dict(SCHEMA_DATA)
	current = (ctx.obj or {}).get("platform") or "zhipin"
	data["current_platform"] = current
	data["current_role"] = (ctx.obj or {}).get("role") or "candidate"
	data["current_browser_mode"] = (
		"cdp-required" if (ctx.obj or {}).get("browser_mode") == "cdp_required" else "auto"
	)
	data["supported_platforms"] = list_platforms()
	data["supported_recruiter_platforms"] = list_recruiter_platforms()
	data["wizard_catalog"] = catalog_data()
	data["compliance"] = compliance_mode_data(ctx)
	data = _inject_availability(data)

	if output_format == "openai-tools":
		emit_success("schema", {"format": "openai-tools", "tools": _format_openai_tools(data)})
		return
	if output_format == "anthropic-tools":
		emit_success("schema", {"format": "anthropic-tools", "tools": _format_anthropic_tools(data)})
		return
	if output_format == "mcp-tools":
		emit_success("schema", {"format": "mcp-tools", "tools": _format_mcp_tools(data)})
		return
	emit_success("schema", data)
