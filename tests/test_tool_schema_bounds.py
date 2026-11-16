"""MCP 工具 schema 的数值边界必须与 Click 命令参数一致（Issue #1392）。

`boss schema --format openai-tools / anthropic-tools / mcp-tools` 导出的
`inputSchema` 是 Agent 唯一能看到的取值契约。只写类型不写边界时，模型会先被
schema 认可，再被 CLI 的 `IntRange` / `FloatRange` 以 `INVALID_PARAM` 拒绝
（`timeout=0`、`count=0`、`days=-1` 等）。这里把每个数值属性映射到真实 Click
参数，缺界、多界和数值不一致都算失败；新增数值属性未登记也会失败。
"""

from typing import Any

import click

from boss_agent_cli.commands import (
	agent,
	ai_cmd,
	chat,
	chat_summary,
	chatmsg,
	clean,
	crawl,
	digest,
	export,
	favorites,
	greet,
	history,
	me,
	pipeline,
	recommend,
	search,
	show,
	stats,
	wizard,
)
from boss_agent_cli.commands.recruiter import (
	accept_resume,
	applications,
	candidates,
	chat as recruiter_chat,
	download_resume,
	jobs as recruiter_jobs,
	location,
	recommendations,
	reply,
	request_resume,
	resume as recruiter_resume,
)
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _subcommand(group: click.Group, name: str) -> click.Command:
	command = group.commands.get(name)
	assert command is not None, f"Click 组 {group.name} 缺少子命令 {name}"
	return command


#: MCP 工具 → (Click 命令, {工具 schema 属性名: Click 参数名})。
#: 每个数值属性都必须登记；`test_every_numeric_tool_property_is_checked` 会
#: 双向核对，新增属性或改名都会失败。
_TOOL_PARAMS: dict[str, tuple[click.Command, dict[str, str]]] = {
	"boss_wizard": (wizard.wizard_cmd, {"timeout": "timeout_seconds", "max_retries": "max_retries"}),
	"boss_search": (search.search_cmd, {"page": "page"}),
	"boss_crawl_results": (_subcommand(crawl.crawl_group, "results"), {"page": "page"}),
	"boss_recommend": (recommend.recommend_cmd, {"page": "page"}),
	"boss_chat": (chat.chat_cmd, {"days": "days", "page": "page"}),
	"boss_me": (me.me_cmd, {"deliver_page": "deliver_page"}),
	"boss_history": (history.history_cmd, {"page": "page"}),
	"boss_chatmsg": (chatmsg.chatmsg_cmd, {"page": "page", "count": "count"}),
	"boss_chat_summary": (chat_summary.chat_summary_cmd, {"page": "page", "count": "count"}),
	"boss_batch_greet": (greet.batch_greet_cmd, {"limit": "count"}),
	"boss_show": (show.show_cmd, {"number": "index"}),
	"boss_export": (export.export_cmd, {"count": "count"}),
	"boss_pipeline": (pipeline.pipeline_cmd, {"days_stale": "days_stale"}),
	"boss_follow_up": (pipeline.follow_up_cmd, {"days_stale": "days_stale"}),
	"boss_digest": (digest.digest_cmd, {"days_stale": "days_stale"}),
	"boss_clean": (clean.clean_cmd, {"days": "days"}),
	"boss_stats": (stats.stats_cmd, {"days": "days"}),
	"boss_agent_run": (_subcommand(agent.agent_group, "run"), {"limit": "limit"}),
	"boss_agent_train": (_subcommand(agent.agent_group, "train"), {"limit": "limit"}),
	"boss_ai_interview_prep": (_subcommand(ai_cmd.ai_group, "interview-prep"), {"count": "count"}),
	"boss_ai_fit": (_subcommand(ai_cmd.ai_group, "fit"), {"limit": "limit"}),
	"boss_ai_suggest_keywords": (_subcommand(ai_cmd.ai_group, "suggest-keywords"), {"limit": "limit"}),
	"boss_favorites_list": (_subcommand(favorites.favorites_group, "list"), {"page": "page"}),
	"boss_hr_applications": (applications.applications_cmd, {"label_id": "label_id", "page": "page"}),
	"boss_hr_candidates": (candidates.candidates_cmd, {"page": "page"}),
	"boss_hr_recommendations": (recommendations.recommendations_cmd, {"page": "page"}),
	"boss_hr_location": (location.location_cmd, {"friend_id": "friend_id"}),
	"boss_hr_chat": (
		recruiter_chat.recruiter_chat_cmd,
		{"page": "page", "label_id": "label_id", "summary_limit": "summary_limit"},
	),
	"boss_hr_chatmsg": (
		recruiter_chat.recruiter_chatmsg_cmd,
		{"friend_id": "friend_id", "count": "count", "max_msg_id": "max_msg_id"},
	),
	"boss_hr_last_messages": (
		recruiter_chat.recruiter_last_messages_cmd,
		{"page": "page", "label_id": "label_id", "limit": "limit"},
	),
	"boss_hr_exchange": (recruiter_resume.resume_cmd, {"friend_id": "friend_id"}),
	"boss_hr_reply": (reply.reply_cmd, {"friend_id": "friend_id"}),
	"boss_hr_request_resume": (request_resume.request_resume_cmd, {"friend_id": "friend_id"}),
	"boss_hr_jobs": (_subcommand(recruiter_jobs.jobs_group, "list"), {"page": "page"}),
	"boss_hr_accept_resume": (
		accept_resume.accept_resume_cmd,
		{"friend_id": "friend_id", "message_id": "message_id"},
	),
	"boss_hr_download_resume": (
		download_resume.download_resume_cmd,
		{"friend_id": "friend_id", "message_id": "message_id"},
	),
}

#: Click 没声明、但命令实现会收敛到的有效上界。
#: `batch-greet --count` 是 `IntRange(0)`（无上界），命令内部会 `count = min(count, 10)`，
#: 因此工具 schema 的 `maximum: 10` 是真实生效的上界，不能按 Click 的 `None` 断言。
_EFFECTIVE_MAXIMUM: dict[tuple[str, str], int | float] = {
	("boss_batch_greet", "limit"): 10,
}


def _catalog() -> dict[str, dict[str, Any]]:
	return {tool.name: tool.input_schema for tool in build_tool_catalog()}


def _params(command: click.Command) -> dict[str, click.Parameter]:
	return {param.name: param for param in command.params}


def _click_bounds(param: click.Parameter) -> tuple[float | None, float | None]:
	param_type = param.type
	if not isinstance(param_type, (click.IntRange, click.FloatRange)):
		return None, None
	# JSON Schema 的 minimum / maximum 是闭区间；Click 的开区间需要改用
	# exclusiveMinimum / exclusiveMaximum，不能在这里悄悄放宽。
	assert not param_type.min_open, f"{param.name} 使用开区间下界，schema 需改用 exclusiveMinimum"
	assert not param_type.max_open, f"{param.name} 使用开区间上界，schema 需改用 exclusiveMaximum"
	return param_type.min, param_type.max


def test_mcp_numeric_bounds_match_click_params() -> None:
	"""每个数值属性的 minimum / maximum 必须与 Click 参数的 min / max 一致。"""
	catalog = _catalog()
	problems: list[str] = []
	for tool_name, (command, properties) in _TOOL_PARAMS.items():
		schema = catalog[tool_name]
		params = _params(command)
		for property_name, param_name in properties.items():
			prop = schema["properties"][property_name]
			param = params[param_name]
			click_min, click_max = _click_bounds(param)
			expected_max = _EFFECTIVE_MAXIMUM.get((tool_name, property_name), click_max)
			for bound, expected, actual in (
				("minimum", click_min, prop.get("minimum")),
				("maximum", expected_max, prop.get("maximum")),
			):
				if actual != expected:
					problems.append(
						f"{tool_name}.{property_name}: {bound}={actual!r}，"
						f"Click 参数 {param_name}={expected!r}"
					)
	assert not problems, "MCP 工具数值边界与 Click 不一致:\n" + "\n".join(problems)


def test_every_numeric_tool_property_is_checked() -> None:
	"""工具目录里的每个数值属性都要有 Click 映射，映射也不能指向已删除的属性。"""
	catalog = _catalog()
	problems: list[str] = []
	for tool_name, schema in catalog.items():
		numeric = {
			name
			for name, prop in schema.get("properties", {}).items()
			if isinstance(prop, dict) and prop.get("type") in ("integer", "number")
		}
		_, mapped = _TOOL_PARAMS.get(tool_name, (None, {}))
		missing = sorted(numeric - set(mapped))
		stale = sorted(set(mapped) - numeric)
		if missing:
			problems.append(f"{tool_name} 未登记的数值属性: {missing}")
		if stale:
			problems.append(f"{tool_name} 登记了非数值属性: {stale}")
	extra_tools = sorted(set(_TOOL_PARAMS) - set(catalog))
	if extra_tools:
		problems.append(f"映射表引用了不存在的工具: {extra_tools}")
	assert not problems, "数值属性覆盖不完整:\n" + "\n".join(problems)
