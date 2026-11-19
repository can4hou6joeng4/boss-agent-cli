"""MCP 工具名 → `boss` CLI 参数列表的映射。

从 `mcp_server` 拆出。这是一张纯粹的分发表：不触网、不读配置、无模块级状态，
唯一职责是把 MCP 调用的 arguments 翻译成命令行参数。

`mcp_server` 会原样再导出 `_build_args`，导入路径保持不变。
"""

import json
from typing import Any

#: 枚举参数（按 MCP 侧 JSON 键登记，Issue #982）。
#: 这些参数在某条命令上有取值校验，显式空串会得到 ``INVALID_PARAM`` + 可选值列表；
#: 映射层若用真值判断，空串被化装成「没传」，调用方误以为参数生效（``format=""``
#: 静默落回默认格式、``sort=""`` 静默按默认排序）。键存在且值不为 ``None`` 时
#: 原样下发；``None`` 与缺键仍等价于未传（与 Issue #958 的 ``query`` / ``url`` 一致）。
#: 自由文本命令（如 ``search --industry``）收到空串时本来就按未传处理，原样下发
#: 不改变行为；``active`` 不在此列：``resolve_active_level("")`` 返回 ``None``。
_ENUM_KEYS: frozenset[str] = frozenset(
	{
		"sort",
		"detail_status",
		"from_who",
		"format",
		"section",
		"lang",
		"tone",
		"type",
		# preset / watch / batch-greet 的筛选键是 Click Choice（Issue #1180）。
		"industry",
		"scale",
		"stage",
		"job_type",
	}
)

#: 显式空串必须原样下发的输出路径参数（按 (子命令, MCP 键) 登记，Issue #1377）。
#: ``click.Path`` 把空串转成当前目录，真正写盘时得到 ``LOCAL_IO_ERROR``；映射层
#: 若按真值判断丢弃，调用方以为已指定输出文件，CLI 却返回 ``ok:true`` 且不写文件。
#: chat / export 的空 ``-o ""`` 会由 CLI 判成 ``INVALID_PARAM``（Issue #1501），
#: 同样必须原样下发，否则 MCP 调用方仍会静默拿到默认导出文件。
_EXPLICIT_EMPTY_KEYS: frozenset[tuple[str, str]] = frozenset(
	{
		("digest", "output"),
		("stats", "output_file"),
		("chat", "output_file"),
		("export", "output_file"),
		# CLI 对空 --url / --preset、空 --jd / --job-id 有专门诊断与互斥校验；
		# 映射层吞掉后调用方会静默按其余参数成功（Issue #1941）。
		("search", "url"),
		("search", "preset"),
		# 与 preset 同用时，空串表示「关闭该项筛选」并覆盖 preset 保存的值；
		# 映射层吞掉后 CLI 会回填 preset 的城市 / 薪资等（Issue #2023）。
		("search", "city"),
		("search", "salary"),
		("search", "experience"),
		("search", "education"),
		("search", "industry"),
		("search", "scale"),
		("search", "stage"),
		("search", "welfare"),
		("search", "active"),
		("search", "job_type"),
		("ai_resume_optimize", "jd_text"),
		("ai_resume_optimize", "job_id"),
		("ai_cover_letter", "jd_text"),
		("ai_cover_letter", "job_id"),
	}
)

#: action → 该 action 实际会下发的键（Issue #1493）。
#: 矩阵之外的键若带非空值，`_build_args` 直接拒绝而不是静默丢弃：越界参数被
#: 忽略后调用仍会成功，调用方会以为过滤、恢复或写入已经生效。
#: 键集合以 CLI 为准：wizard 的 --timeout / --max-retries 对四种 action 都可用，
#: config 的 key 对应 get/set/reset 三个子命令（没有 unset），hr jobs 的 page
#: 只属于 list、job_id 只属于 online/offline。
_WIZARD_ACTION_KEYS: dict[str, frozenset[str]] = {
	"run": frozenset({"action", "role", "platform", "goal", "inputs", "requested_steps", "timeout", "max_retries"}),
	"resume": frozenset({"action", "run_id", "timeout", "max_retries"}),
	"status": frozenset({"action", "run_id", "timeout", "max_retries"}),
	"stop": frozenset({"action", "run_id", "timeout", "max_retries"}),
}
_WIZARD_KEY_USAGE = {
	"role": "仅用于 action=run",
	"platform": "仅用于 action=run",
	"goal": "仅用于 action=run",
	"inputs": "仅用于 action=run",
	"requested_steps": "仅用于 action=run",
	"run_id": "仅用于 action=resume/status/stop",
}
_CONFIG_ACTION_KEYS: dict[str, frozenset[str]] = {
	"list": frozenset({"action"}),
	"get": frozenset({"action", "key"}),
	"set": frozenset({"action", "key", "value"}),
	"reset": frozenset({"action", "key"}),
}
_CONFIG_KEY_USAGE = {
	"key": "仅用于 action=get/set/reset",
	"value": "仅用于 action=set",
}
_HR_JOBS_ACTION_KEYS: dict[str, frozenset[str]] = {
	"list": frozenset({"action", "page"}),
	"online": frozenset({"action", "job_id"}),
	"offline": frozenset({"action", "job_id"}),
}
_HR_JOBS_KEY_USAGE = {
	"job_id": "仅用于 action=online/offline（这两个 action 必填）",
	"page": "仅用于 action=list",
}


def _is_blank_action_value(value: Any) -> bool:
	"""action 用不到的键只在值非空时才算越界（Issue #1493）。

	显式 ``null`` 已在 `call_tool` 之前剥离；空串 / 空数组 / 空对象与「没传」
	等价，不能因为 schema 里有这个字段就拒绝缺省形态。
	"""
	if value is None:
		return True
	if isinstance(value, (str, list, tuple, dict, set)):
		return not value
	return False


def _reject_incompatible_action_args(
	tool_name: str,
	action: str,
	arguments: dict[str, Any],
	allowed: frozenset[str],
	usage: dict[str, str],
) -> None:
	"""拒绝「值非空但该 action 用不到」的键，异常由 `call_tool` 转成 INVALID_PARAM。

	错误消息点名参数、action 与正确用法，让 Agent 能自己改成合法组合。
	"""
	unused = [
		key
		for key, value in arguments.items()
		if key not in allowed and not _is_blank_action_value(value)
	]
	if not unused:
		return
	details = "；".join(f"{key} {usage.get(key, '不适用于该 action')}" for key in unused)
	raise ValueError(
		f"{tool_name} 的 action={action} 与参数不兼容：{details}；"
		"请去掉不适用于该 action 的参数，或改用支持它的 action"
	)


def _optional(
	args: list[str],
	flag: str,
	key: str,
	arguments: dict[str, Any],
	*,
	command: str | None = None,
) -> None:
	"""下发可选字符串参数：枚举参数与登记的输出路径空串也要原样下发（Issue #982）。"""
	if key not in arguments:
		return
	value = arguments[key]
	if value is None:
		return
	if not value and key not in _ENUM_KEYS and (command, key) not in _EXPLICIT_EMPTY_KEYS:
		return
	args.extend([flag, str(value)])


def _literal_positionals(args: list[str], *, start: int = 1, count: int = 1) -> list[str]:
	"""以连字符开头的位置参数放到 ``--`` 后，选项保持生效（Issue #1102）。"""
	end = start + count
	values = args[start:end]
	if any(value.startswith("-") for value in values):
		return [*args[:start], *args[end:], "--", *values]
	return args


def _build_args(tool_name: str, arguments: dict[str, Any]) -> list[str]:
	"""根据 tool name 和参数构建 CLI 参数列表。"""
	name = tool_name.replace("boss_", "")

	if name == "schema":
		# 能力真源对 MCP-only Agent 同样可读；只读本地数据，不发平台请求。
		args = ["schema"]
		output_format = arguments.get("format")
		if output_format is not None:
			args.extend(["--format", str(output_format)])
		return args

	if name == "wizard":
		# 未识别的 action 不能落进 run 分支（Issue #1002）：把 `resume` 拼错成
		# `resum` 会静默新建一次 workflow run，调用方以为控制请求已生效。
		# 显式空串同样是非法取值（与 Issue #982 的枚举契约一致），只有缺省 /
		# ``null`` 才落回 schema 默认的 run。
		raw_action = arguments.get("action")
		# schema 只说 run_id 是 resume/status/stop 的参数，没有承诺缺省 action 会
		# 按 run_id 恢复；默认仍是 run，不能猜测调用方意图（Issue #1493）。
		if raw_action is None and not _is_blank_action_value(arguments.get("run_id")):
			raise ValueError(
				"boss_wizard 收到 run_id 但缺少 action：run_id 仅用于 action=resume/status/stop，"
				"缺省 action 是 run；请显式传入 action"
			)
		action = "run" if raw_action is None else str(raw_action)
		if action not in _WIZARD_ACTION_KEYS:
			raise ValueError(f"action 应为 run / resume / status / stop，收到 {action!r}")
		_reject_incompatible_action_args("boss_wizard", action, arguments, _WIZARD_ACTION_KEYS[action], _WIZARD_KEY_USAGE)
		if action in {"resume", "status", "stop"}:
			run_id = str(arguments["run_id"])
			args = ["wizard", f"--{action}", run_id]
		else:
			payload = {
				key: arguments[key]
				for key in ("role", "platform", "goal", "inputs", "requested_steps")
				if key in arguments
			}
			args = ["wizard", "--input-json", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
		if arguments.get("timeout") is not None:
			args.extend(["--timeout", str(arguments["timeout"])])
		if arguments.get("max_retries") is not None:
			args.extend(["--max-retries", str(arguments["max_retries"])])
		return args

	if name == "search":
		# query / url / preset 至少一个；没有 query 时先放占位，最后按 CLI 的
		# 位置参数语义清理，避免把 --url 误当成关键词（Issue #1648）。
		args = [name]
		if arguments.get("query") is not None:
			args.append(str(arguments["query"]))
		_optional(args, "--url", "url", arguments, command=name)
		_optional(args, "--preset", "preset", arguments, command=name)
		for opt in ("city", "salary", "experience", "education", "industry", "scale", "stage", "welfare", "active"):
			_optional(args, f"--{opt}", opt, arguments, command=name)
		_optional(args, "--job-type", "job_type", arguments, command=name)
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if arguments.get("no_cache"):
			args.append("--no-cache")
		if arguments.get("with_score"):
			args.append("--with-score")
		_optional(args, "--sort", "sort", arguments)
		return _literal_positionals(args, count=1 if arguments.get("query") is not None else 0)

	if name == "crawl_status":
		return _literal_positionals(["crawl", "status", str(arguments["run_id"])], start=2)

	if name == "crawl_results":
		args = ["crawl", "results", str(arguments["run_id"])]
		if arguments.get("page") is not None:
			args.extend(["--page", str(arguments["page"])])
		_optional(args, "--detail-status", "detail_status", arguments)
		return _literal_positionals(args, start=2)

	if name == "crawl_shortlist":
		args = ["crawl", "shortlist", str(arguments["run_id"])]
		for selector in arguments.get("selectors") or []:
			args.extend(["--selector", str(selector)])
		if arguments.get("all"):
			args.append("--all")
		if arguments.get("tags"):
			args.extend(["--tags", str(arguments["tags"])])
		if arguments.get("note"):
			args.extend(["--note", str(arguments["note"])])
		return _literal_positionals(args, start=2)

	if name == "recommend":
		args = [name]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if arguments.get("with_score"):
			args.append("--with-score")
		return args

	if name == "detail":
		args = [name, arguments["security_id"]]
		if "job_id" in arguments and arguments["job_id"]:
			args.extend(["--job-id", arguments["job_id"]])
		if arguments.get("lid"):
			args.extend(["--lid", str(arguments["lid"])])
		return _literal_positionals(args)

	if name == "greet":
		args = [name, arguments["security_id"], arguments["job_id"]]
		# 缺省使用平台默认招呼语；自由文本空串按未传处理（Issue #1202）。
		_optional(args, "--message", "message", arguments)
		return _literal_positionals(args, count=2)

	if name == "chat":
		args = [name]
		_optional(args, "--from", "from_who", arguments)
		if "days" in arguments:
			args.extend(["--days", str(arguments["days"])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		_optional(args, "--export", "format", arguments)
		_optional(args, "--output", "output_file", arguments, command=name)
		return args

	if name == "me":
		args = [name]
		_optional(args, "--section", "section", arguments)
		if arguments.get("deliver_page") is not None:
			args.extend(["--deliver-page", str(arguments["deliver_page"])])
		return args

	if name == "chatmsg":
		args = [name, arguments["security_id"]]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		if arguments.get("raw"):
			args.append("--raw")
		return _literal_positionals(args)

	if name == "chat_summary":
		args = ["chat-summary", arguments["security_id"]]
		if arguments.get("page") is not None:
			args.extend(["--page", str(arguments["page"])])
		if arguments.get("count") is not None:
			args.extend(["--count", str(arguments["count"])])
		return _literal_positionals(args)

	if name == "mark":
		# MCP 侧参数名是 tag，CLI 的选项名是 --label；两者不是同一个词，写错会让
		# Click 以 NoSuchOption 拒绝整个工具调用。
		args = [name, arguments["security_id"], "--label", arguments["tag"]]
		if arguments.get("remove"):
			args.append("--remove")
		return _literal_positionals(args)

	if name == "exchange":
		args = [name, arguments["security_id"]]
		# 描述承诺「手机号或微信」，但 CLI 默认 phone：省略/显式 phone 不下发，
		# 空串等非法值交给 CLI 的枚举诊断（Issue #982、#1200）。
		if "type" in arguments and arguments["type"] is not None and arguments["type"] != "phone":
			args.extend(["--type", str(arguments["type"])])
		return _literal_positionals(args)

	if name == "apply":
		args = [name, arguments["security_id"], arguments["job_id"]]
		# 与 detail 的 lid 同口径：列表项访问上下文可选，空串按未传处理（Issue #1202）。
		_optional(args, "--lid", "lid", arguments)
		return _literal_positionals(args, count=2)

	if name == "batch_greet":
		args = ["batch-greet", arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "industry", "scale", "stage"):
			_optional(args, f"--{opt}", opt, arguments)
		_optional(args, "--job-type", "job_type", arguments)
		# MCP 侧参数名是 limit，CLI 选项名是 --count。默认值要显式下发：不传就落到
		# CLI 的默认 10，比工具声明的 5 多打一倍招呼。
		args.extend(["--count", str(arguments.get("limit", 5))])
		if arguments.get("dry_run"):
			args.append("--dry-run")
		return _literal_positionals(args)

	if name == "show":
		return _literal_positionals([name, str(arguments["number"])])

	if name == "agent_run":
		# 预览模式不能解除操作者停止闩锁：MCP 默认 dry_run=true，若这里直接
		# 生成 `--dry-run --resume`，CLI 虽会拒绝，但 Agent 只看到一次无谓调用；
		# 更危险的是将来 CLI 放宽该组合时预览就会静默解锁（Issue #1591）。
		if arguments.get("resume", False) and arguments.get("dry_run", True):
			raise ValueError(
				"boss_agent_run 的 --dry-run 与 --resume 不能同时为真："
				"恢复必须显式设置 dry_run=false 并真实执行一轮；仅预览时去掉 resume"
			)
		args = ["agent", "run"]
		if arguments.get("dry_run", True):
			args.append("--dry-run")
		if "limit" in arguments and arguments["limit"] is not None:
			args.extend(["--limit", str(arguments["limit"])])
		if arguments.get("resume"):
			args.append("--resume")
		return args

	if name == "agent_train":
		args = ["agent", "train"]
		if "limit" in arguments and arguments["limit"] is not None:
			args.extend(["--limit", str(arguments["limit"])])
		return args

	if name == "agent_review":
		return ["agent", "review", "list"]

	if name == "agent_review_approve":
		return _literal_positionals(["agent", "review", "approve", str(arguments["id"])], start=3)

	if name == "agent_review_reject":
		args = ["agent", "review", "reject", str(arguments["id"])]
		# 显式空串要原样下发（Issue #1324）：CLI 的 default 只在选项缺席时生效。
		if arguments.get("reason") is not None:
			args.extend(["--reason", str(arguments["reason"])])
		return _literal_positionals(args, start=3)

	if name == "agent_pending":
		return ["agent", "pending", "list"]

	if name == "agent_stats":
		return ["agent", "stats"]

	if name == "agent_stop":
		args = ["agent", "stop"]
		# 显式空串要原样下发（Issue #1324）：CLI 的 default 只在选项缺席时生效。
		if arguments.get("reason") is not None:
			args.extend(["--reason", str(arguments["reason"])])
		return args

	if name == "export":
		args = [name]
		# 显式空串要原样下发（Issue #958）：CLI 对 `export --url ""` 有专门诊断
		# （「--url 的值不能为空」），真值判断会把空值化装成没传，Agent 收到的
		# 就是误导性的「未提供 query」。query 同理，空关键词应显式到达 CLI。
		if "query" in arguments and arguments["query"] is not None:
			args.append(str(arguments["query"]))
		if "url" in arguments and arguments["url"] is not None:
			args.extend(["--url", str(arguments["url"])])
		for opt in ("city", "salary", "experience", "education", "industry", "scale", "stage", "job_type"):
			_optional(args, f"--{opt.replace('_', '-')}", opt, arguments)
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		_optional(args, "--format", "format", arguments)
		_optional(args, "-o", "output_file", arguments, command=name)
		if arguments.get("include_private"):
			args.append("--include-private")
		return _literal_positionals(args, count=int("query" in arguments and arguments["query"] is not None))

	if name == "follow_up":
		args = ["follow-up"]
		if "days_stale" in arguments:
			args.extend(["--days-stale", str(arguments["days_stale"])])
		return args

	if name == "config":
		# action 在 schema 里是 required（Issue #960）：漏传要落到 call_tool 的
		# KeyError 分支返回 INVALID_PARAM，不能在这里私自默认成 list——那会让
		# 绕过客户端校验的调用拿到 ok:true 的全量配置，schema 契约形同虚设。
		# 需要默认行为的工具（boss_hr_jobs / boss_wizard）在 schema 里声明
		# 非 required + default，映射层才允许补默认值。
		action = arguments["action"]
		# 只有 list/get/set/reset 有矩阵；未识别的 action 原样交给 CLI 的
		# "No such command" 拒绝，不在映射层猜测（Issue #1493）。
		allowed_config = _CONFIG_ACTION_KEYS.get(action) if isinstance(action, str) else None
		if allowed_config is not None:
			_reject_incompatible_action_args("boss_config", action, arguments, allowed_config, _CONFIG_KEY_USAGE)
		args = [name, action]
		if action in ("get", "set", "reset") and "key" in arguments:
			args.append(arguments["key"])
		if action == "set" and "value" in arguments:
			args.append(arguments["value"])
		return _literal_positionals(args, start=2, count=len(args) - 2)

	if name == "clean":
		args = [name]
		if arguments.get("dry_run"):
			args.append("--dry-run")
		if arguments.get("all"):
			args.append("--all")
		if arguments.get("days") is not None:
			args.extend(["--days", str(arguments["days"])])
		return args

	if name == "digest":
		args = [name]
		if "days_stale" in arguments:
			args.extend(["--days-stale", str(arguments["days_stale"])])
		_optional(args, "--format", "format", arguments)
		_optional(args, "-o", "output", arguments, command=name)
		return args

	if name == "stats":
		args = [name]
		if "days" in arguments:
			args.extend(["--days", str(arguments["days"])])
		_optional(args, "--format", "format", arguments)
		_optional(args, "--output", "output_file", arguments, command=name)
		return args

	if name == "ai_reply":
		args = ["ai", "reply", arguments["recruiter_message"]]
		_optional(args, "--context", "context", arguments)
		_optional(args, "--resume", "resume", arguments)
		_optional(args, "--tone", "tone", arguments)
		return _literal_positionals(args, start=2)

	if name == "ai_interview_prep":
		args = ["ai", "interview-prep", arguments["jd_text"]]
		if arguments.get("resume"):
			args.extend(["--resume", arguments["resume"]])
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		return _literal_positionals(args, start=2)

	if name == "ai_chat_coach":
		args = ["ai", "chat-coach", arguments["chat_text"]]
		if arguments.get("resume"):
			args.extend(["--resume", arguments["resume"]])
		# 显式空串与缺省不是同一语义：CLI 会把 style 原样替换进 prompt，
		# 空串要原样下发（Issue #1862，与 agent_stop.reason 同口径）。
		if "style" in arguments:
			args.extend(["--style", arguments["style"]])
		return _literal_positionals(args, start=2)

	if name == "resume_list":
		return ["resume", "list"]

	if name == "resume_show":
		return _literal_positionals(["resume", "show", arguments["name"]], start=2)

	if name == "ai_analyze_jd":
		return _literal_positionals(["ai", "analyze-jd", arguments["jd_text"], "--resume", arguments["resume"]], start=2)

	if name == "ai_optimize":
		return _literal_positionals(["ai", "optimize", arguments["resume"], "--jd", arguments["jd_text"]], start=2)

	if name == "ai_suggest":
		return _literal_positionals(["ai", "suggest", arguments["resume"], "--jd", arguments["jd_text"]], start=2)

	if name == "ai_fit":
		args = ["ai", "fit", "--resume", arguments["resume"]]
		if "limit" in arguments:
			args.extend(["--limit", str(arguments["limit"])])
		return args

	if name == "ai_suggest_keywords":
		args = ["ai", "suggest-keywords"]
		if "limit" in arguments:
			args.extend(["--limit", str(arguments["limit"])])
		return args

	if name == "ai_resume_optimize":
		args = ["ai", "resume-optimize", arguments["resume"]]
		_optional(args, "--jd", "jd_text", arguments, command=name)
		_optional(args, "--job-id", "job_id", arguments, command=name)
		return _literal_positionals(args, start=2)

	if name == "ai_cover_letter":
		args = ["ai", "cover-letter", arguments["resume"]]
		_optional(args, "--jd", "jd_text", arguments, command=name)
		_optional(args, "--job-id", "job_id", arguments, command=name)
		_optional(args, "--tone", "tone", arguments)
		_optional(args, "--lang", "lang", arguments)
		return _literal_positionals(args, start=2)

	if name == "watch_list":
		return ["watch", "list"]

	if name == "watch_run":
		return _literal_positionals(["watch", "run", arguments["name"]], start=2)

	if name == "preset_list":
		return ["preset", "list"]

	if name == "shortlist_list":
		return ["shortlist", "list"]

	if name == "favorites_list":
		args = ["favorites", "list"]
		# page=0 是显式传入的非法值，要原样下发交给 CLI 的 IntRange(1) 拒绝；
		# 用真值判断会把它静默换成默认第 1 页，与其他工具的页码行为不一致。
		if arguments.get("page") is not None:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "history":
		args = ["history"]
		# 与 favorites_list 同一取舍：显式传入的 0 交给 CLI 拒绝，不静默改写。
		if arguments.get("page") is not None:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "shortlist_add":
		args = ["shortlist", "add", arguments["security_id"], arguments["job_id"]]
		# 显式空串要原样下发（Issue #1324）：CLI 的 default 只在选项缺席时生效，
		# 真值判断会把 "" 化装成没传，落库的 source 与调用方参数不一致。
		for opt in ("title", "company", "city", "salary", "source"):
			if arguments.get(opt) is not None:
				args.extend([f"--{opt}", str(arguments[opt])])
		if arguments.get("lid"):
			args.extend(["--lid", str(arguments["lid"])])
		if arguments.get("tags"):
			args.extend(["--tags", str(arguments["tags"])])
		if arguments.get("note"):
			args.extend(["--note", str(arguments["note"])])
		return _literal_positionals(args, start=2, count=2)

	if name == "shortlist_annotate":
		args = ["shortlist", "annotate", arguments["security_id"], arguments["job_id"]]
		for tag in arguments.get("add_tags") or []:
			args.extend(["--add-tag", str(tag)])
		for tag in arguments.get("remove_tags") or []:
			args.extend(["--remove-tag", str(tag)])
		# `--note ""` 在 CLI 是「替换为空」，即清空备注；用 truthiness 会把空串
		# 丢掉，旧备注保留而信封仍回 updated=true（Issue #872）。
		if "note" in arguments and arguments["note"] is not None:
			args.extend(["--note", str(arguments["note"])])
		return _literal_positionals(args, start=2, count=2)

	if name == "shortlist_compare":
		args = ["shortlist", "compare"]
		# 显式空串必须原样下发：CLI 统一按 INVALID_PARAM 拒绝，真值判断会把它
		# 化装成「没传」并比较全部条目（Issue #1565）。
		if arguments.get("tag") is not None:
			args.extend(["--tag", str(arguments["tag"])])
		return args

	if name == "shortlist_remove":
		return _literal_positionals(["shortlist", "remove", arguments["security_id"], arguments["job_id"]], start=2, count=2)

	if name == "preset_add":
		args = ["preset", "add", arguments["name"], arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "industry", "scale", "stage", "job_type", "welfare", "active"):
			_optional(args, f"--{opt.replace('_', '-')}", opt, arguments)
		return _literal_positionals(args, start=2, count=2)

	if name == "preset_remove":
		return _literal_positionals(["preset", "remove", arguments["name"]], start=2)

	if name == "watch_add":
		args = ["watch", "add", arguments["name"], arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "industry", "scale", "stage", "job_type", "welfare"):
			_optional(args, f"--{opt.replace('_', '-')}", opt, arguments)
		return _literal_positionals(args, start=2, count=2)

	if name == "watch_remove":
		return _literal_positionals(["watch", "remove", arguments["name"]], start=2)

	if name == "hr_whoami":
		return ["hr", "whoami"]

	if name == "hr_applications":
		args = ["hr", "applications"]
		# 空串不是「未传」：与 label_id 同口径按存在性下发，交给 CLI 归一
		# （Issue #1865）。
		if "job_id" in arguments:
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
		for opt in ("city", "job_id", "experience", "degree", "age", "school_level", "activeness", "source", "salary"):
			if arguments.get(opt):
				args.extend([f"--{opt.replace('_', '-')}", str(arguments[opt])])
		if arguments.get("select"):
			args.append("--select")
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return _literal_positionals(args, start=2, count=int(bool(arguments.get("query"))))

	if name == "hr_chat":
		args = ["hr", "chat"]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if "job_id" in arguments:
			args.extend(["--job-id", str(arguments["job_id"])])
		if "label_id" in arguments:
			args.extend(["--label-id", str(arguments["label_id"])])
		if "summary_limit" in arguments:
			args.extend(["--summary-limit", str(arguments["summary_limit"])])
		return args

	if name == "hr_chatmsg":
		args = ["hr", "chatmsg", str(arguments["friend_id"])]
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		# 存在即下发（显式 null 已在入口剥离）：游标 0 是非法取值，但不能被
		# 真值判断化装成「没传」，否则调用方拿到默认最新一页还以为翻页生效（Issue #1199）。
		if "max_msg_id" in arguments:
			args.extend(["--max-msg-id", str(arguments["max_msg_id"])])
		return _literal_positionals(args, start=2)

	if name == "hr_last_messages":
		args = ["hr", "last-messages"]
		for friend_id in arguments.get("friend_ids") or []:
			args.extend(["--friend-id", str(friend_id)])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if "job_id" in arguments:
			args.extend(["--job-id", str(arguments["job_id"])])
		if "label_id" in arguments:
			args.extend(["--label-id", str(arguments["label_id"])])
		if "limit" in arguments:
			args.extend(["--limit", str(arguments["limit"])])
		return args

	if name == "hr_resume":
		args = ["hr", "resume", arguments["geek_id"], "--job-id", str(arguments["job_id"]), "--security-id", str(arguments["security_id"])]
		if arguments.get("raw"):
			args.append("--raw")
		return _literal_positionals(args, start=2)

	if name == "hr_exchange":
		args = ["hr", "resume", "--exchange", "--friend-id", str(arguments["friend_id"])]
		# 省略 type 等于 phone；空串是调用方写错了取值，交给 CLI 的枚举诊断（Issue #982）。
		if "type" in arguments and arguments["type"] is not None and arguments["type"] != "phone":
			args.extend(["--type", str(arguments["type"])])
		return args

	if name == "hr_reply":
		return _literal_positionals(["hr", "reply", str(arguments["friend_id"]), arguments["message"]], start=2, count=2)

	if name == "hr_request_resume":
		return _literal_positionals(["hr", "request-resume", str(arguments["friend_id"])], start=2)

	if name == "hr_accept_resume":
		args = ["hr", "accept-resume", str(arguments["friend_id"]), "--message-id", str(arguments["message_id"])]
		if arguments.get("yes") is True:
			args.append("--yes")
		if arguments.get("dry_run") is True:
			args.append("--dry-run")
		return _literal_positionals(args, start=2)

	if name == "hr_download_resume":
		return _literal_positionals(
			["hr", "download-resume", str(arguments["friend_id"]), "--message-id", str(arguments["message_id"]), "--output", str(arguments["output"])],
			start=2,
		)

	if name == "hr_recommendations":
		args = ["hr", "recommendations", "--job-id", str(arguments["job_id"])]
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "hr_greet":
		args = [
			"hr", "greet",
			"--geek-id", str(arguments["geek_id"]),
			"--job-id", str(arguments["job_id"]),
			"--expect-id", str(arguments["expect_id"]),
			"--lid", str(arguments["lid"]),
			"--security-id", str(arguments["security_id"]),
			"--message", str(arguments["message"]),
		]
		if arguments.get("suid"):
			args.extend(["--suid", str(arguments["suid"])])
		if arguments.get("yes") is True:
			args.append("--yes")
		if arguments.get("dry_run") is True:
			args.append("--dry-run")
		return args

	if name == "hr_location":
		args = ["hr", "location", str(arguments["friend_id"])]
		if arguments.get("yes") is True:
			args.append("--yes")
		if arguments.get("dry_run") is True:
			args.append("--dry-run")
		return _literal_positionals(args, start=2)

	if name == "hr_jobs":
		action = arguments.get("action", "list")
		# 缺省 action 与 schema 默认的 list 一致；未识别的 action 交给 CLI 拒绝。
		allowed_jobs = _HR_JOBS_ACTION_KEYS.get(action) if isinstance(action, str) else None
		if allowed_jobs is not None:
			_reject_incompatible_action_args("boss_hr_jobs", action, arguments, allowed_jobs, _HR_JOBS_KEY_USAGE)
		args = ["hr", "jobs", action]
		if action in {"online", "offline"}:
			args.append(str(arguments["job_id"]))
		elif arguments.get("page") is not None:
			args.extend(["--page", str(arguments["page"])])
		return _literal_positionals(args, start=3, count=int(action in {"online", "offline"}))

	if name == "hr_jobs_detail":
		return _literal_positionals(["hr", "jobs", "detail", str(arguments["enc_job_id"])], start=3)

	if name == "status":
		args = [name]
		if arguments.get("live"):
			args.append("--live")
		return args

	if name == "pipeline":
		args = [name]
		if arguments.get("days_stale") is not None:
			args.extend(["--days-stale", str(arguments["days_stale"])])
		return args

	# 无参数命令：doctor, cities, interviews
	return [name]
