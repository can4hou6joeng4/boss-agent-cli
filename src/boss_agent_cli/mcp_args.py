"""MCP 工具名 → `boss` CLI 参数列表的映射。

从 `mcp_server` 拆出。这是一张纯粹的分发表：不触网、不读配置、无模块级状态，
唯一职责是把 MCP 调用的 arguments 翻译成命令行参数。

`mcp_server` 会原样再导出 `_build_args`，导入路径保持不变。
"""

from typing import Any


def _build_args(tool_name: str, arguments: dict[str, Any]) -> list[str]:
	"""根据 tool name 和参数构建 CLI 参数列表。"""
	name = tool_name.replace("boss_", "")

	if name == "search":
		args = [name, arguments["query"]]
		for opt in ("city", "salary", "experience", "education", "welfare"):
			if opt in arguments and arguments[opt]:
				args.extend([f"--{opt}", str(arguments[opt])])
		if "page" in arguments:
			args.extend(["--page", str(arguments["page"])])
		if arguments.get("sort"):
			args.extend(["--sort", str(arguments["sort"])])
		return args

	if name == "crawl_status":
		return ["crawl", "status", str(arguments["run_id"])]

	if name == "crawl_results":
		args = ["crawl", "results", str(arguments["run_id"])]
		if arguments.get("page") is not None:
			args.extend(["--page", str(arguments["page"])])
		if arguments.get("detail_status"):
			args.extend(["--detail-status", str(arguments["detail_status"])])
		return args

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
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		if arguments.get("raw"):
			args.append("--raw")
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

	if name == "agent_run":
		args = ["agent", "run"]
		if arguments.get("dry_run", True):
			args.append("--dry-run")
		if "limit" in arguments and arguments["limit"] is not None:
			args.extend(["--limit", str(arguments["limit"])])
		return args

	if name == "agent_train":
		args = ["agent", "train"]
		if "limit" in arguments and arguments["limit"] is not None:
			args.extend(["--limit", str(arguments["limit"])])
		return args

	if name == "agent_review":
		return ["agent", "review", "list"]

	if name == "agent_review_approve":
		return ["agent", "review", "approve", str(arguments["id"])]

	if name == "agent_review_reject":
		args = ["agent", "review", "reject", str(arguments["id"])]
		if arguments.get("reason"):
			args.extend(["--reason", str(arguments["reason"])])
		return args

	if name == "agent_pending":
		return ["agent", "pending", "list"]

	if name == "agent_stats":
		return ["agent", "stats"]

	if name == "agent_stop":
		args = ["agent", "stop"]
		if arguments.get("reason"):
			args.extend(["--reason", str(arguments["reason"])])
		return args

	if name == "export":
		args = [name]
		if arguments.get("query"):
			args.append(arguments["query"])
		if arguments.get("url"):
			args.extend(["--url", arguments["url"]])
		for opt in ("city", "salary", "experience", "education", "industry", "scale", "stage", "job_type"):
			if arguments.get(opt):
				cli_flag = f"--{opt.replace('_', '-')}"
				args.extend([cli_flag, arguments[opt]])
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		if arguments.get("format"):
			args.extend(["--format", arguments["format"]])
		if arguments.get("output_file"):
			args.extend(["-o", arguments["output_file"]])
		if arguments.get("include_private"):
			args.append("--include-private")
		return args

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
		if arguments.get("jd_text"):
			args.extend(["--jd", arguments["jd_text"]])
		if arguments.get("job_id"):
			args.extend(["--job-id", arguments["job_id"]])
		return args

	if name == "ai_cover_letter":
		args = ["ai", "cover-letter", arguments["resume"]]
		if arguments.get("jd_text"):
			args.extend(["--jd", arguments["jd_text"]])
		if arguments.get("job_id"):
			args.extend(["--job-id", arguments["job_id"]])
		if arguments.get("tone"):
			args.extend(["--tone", arguments["tone"]])
		if arguments.get("lang"):
			args.extend(["--lang", arguments["lang"]])
		return args

	if name == "watch_list":
		return ["watch", "list"]

	if name == "watch_run":
		return ["watch", "run", arguments["name"]]

	if name == "preset_list":
		return ["preset", "list"]

	if name == "shortlist_list":
		return ["shortlist", "list"]

	if name == "favorites_list":
		args = ["favorites", "list"]
		if arguments.get("page"):
			args.extend(["--page", str(arguments["page"])])
		return args

	if name == "shortlist_add":
		args = ["shortlist", "add", arguments["security_id"], arguments["job_id"]]
		if arguments.get("tags"):
			args.extend(["--tags", str(arguments["tags"])])
		if arguments.get("note"):
			args.extend(["--note", str(arguments["note"])])
		return args

	if name == "shortlist_annotate":
		args = ["shortlist", "annotate", arguments["security_id"], arguments["job_id"]]
		for tag in arguments.get("add_tags") or []:
			args.extend(["--add-tag", str(tag)])
		for tag in arguments.get("remove_tags") or []:
			args.extend(["--remove-tag", str(tag)])
		if arguments.get("note"):
			args.extend(["--note", str(arguments["note"])])
		return args

	if name == "shortlist_compare":
		args = ["shortlist", "compare"]
		if arguments.get("tag"):
			args.extend(["--tag", str(arguments["tag"])])
		return args

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
		for opt in ("city", "job_id", "experience", "degree", "age", "school_level", "activeness", "source", "salary"):
			if arguments.get(opt):
				args.extend([f"--{opt.replace('_', '-')}", str(arguments[opt])])
		if arguments.get("select"):
			args.append("--select")
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

	if name == "hr_chatmsg":
		args = ["hr", "chatmsg", str(arguments["friend_id"])]
		if "count" in arguments:
			args.extend(["--count", str(arguments["count"])])
		if arguments.get("max_msg_id"):
			args.extend(["--max-msg-id", str(arguments["max_msg_id"])])
		return args

	if name == "hr_last_messages":
		args = ["hr", "last-messages"]
		for friend_id in arguments.get("friend_ids") or []:
			args.extend(["--friend-id", str(friend_id)])
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

	if name == "hr_exchange":
		args = ["hr", "resume", "--exchange", "--friend-id", str(arguments["friend_id"])]
		if arguments.get("type") and arguments["type"] != "phone":
			args.extend(["--type", str(arguments["type"])])
		return args

	if name == "hr_reply":
		return ["hr", "reply", str(arguments["friend_id"]), arguments["message"]]

	if name == "hr_request_resume":
		return ["hr", "request-resume", str(arguments["friend_id"])]

	if name == "hr_jobs":
		action = arguments.get("action", "list")
		args = ["hr", "jobs", action]
		if action in {"online", "offline"}:
			args.append(str(arguments["job_id"]))
		return args

	if name == "hr_jobs_detail":
		return ["hr", "jobs", "detail", str(arguments["enc_job_id"])]

	# 无参数命令：status, doctor, cities, interviews, history, pipeline
	return [name]
