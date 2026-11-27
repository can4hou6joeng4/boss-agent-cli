"""MCP 参数映射与请求估算的审计回归（Issue #2023–#2026）。"""

from __future__ import annotations

from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_MAX_CLI_TIMEOUT_SECONDS,
	_cli_timeout,
	_estimated_requests,
)
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.services.friend_list_pages import MAX_FRIEND_LIST_PAGES

_SEARCH_FILTER_OPTIONS = (
	"city",
	"salary",
	"experience",
	"education",
	"industry",
	"scale",
	"stage",
	"welfare",
	"active",
	"job_type",
)


def _flag_for(key: str) -> str:
	return "--job-type" if key == "job_type" else f"--{key}"


def test_search_explicit_empty_filters_are_forwarded_with_preset() -> None:
	# 与 preset 同用时，显式空串表示「关闭该项筛选」；映射层吞掉后 CLI 会
	# 回填 preset 保存的值（Issue #2023）。
	arguments = {"query": "python", "preset": "remote", **{key: "" for key in _SEARCH_FILTER_OPTIONS}}
	argv = _build_args("boss_search", arguments)
	assert argv[0] == "search"
	assert "python" in argv
	assert "--preset" in argv
	for key in _SEARCH_FILTER_OPTIONS:
		flag = _flag_for(key)
		index = argv.index(flag)
		assert argv[index + 1] == "", f"{flag} 的显式空串被映射层吞掉"


def test_search_absent_filters_are_still_dropped() -> None:
	# 未提供的筛选键不应变成空参数：只有显式空串才是「关闭筛选」意图。
	argv = _build_args("boss_search", {"query": "python", "city": "北京"})
	for key in _SEARCH_FILTER_OPTIONS:
		flag = _flag_for(key)
		if key == "city":
			continue
		assert flag not in argv


def test_pipeline_estimate_covers_collector_page_cap() -> None:
	# 好友列表上限从 collector 共享：按固定 5 页估算会让真实翻页超过 5 页的
	# 账号在 MCP 层被提前掐断（Issue #2024）。
	assert MAX_FRIEND_LIST_PAGES > 5
	expected = MAX_FRIEND_LIST_PAGES + 1
	for command in ("pipeline", "follow-up", "digest"):
		assert _estimated_requests((command,)) == expected
	assert _cli_timeout(("pipeline",)) == _MAX_CLI_TIMEOUT_SECONDS


def test_hr_last_messages_estimate_includes_chat_list_request() -> None:
	# 无 --friend-id 时先取一页列表（Issue #2025），混合来源最多三批（Issue #3006）。
	assert _estimated_requests(("hr", "last-messages", "--limit", "100")) == 4
	assert (
		_estimated_requests(("hr", "last-messages", "--limit", "100", "--friend-id", "f1"))
		== 2
	)


def test_wizard_else_branch_required_strings_have_min_length() -> None:
	# role / platform / goal 在 else（run）分支必填，空串必须在 MCP 入口被拒，
	# 而不是生成 `wizard --input-json` 后再吃 INVALID_PARAM（Issue #2026）。
	catalog = {tool.name: tool for tool in build_tool_catalog()}
	properties = catalog["boss_wizard"].input_schema["properties"]
	for key in ("role", "platform", "goal", "run_id"):
		assert properties[key].get("minLength") == 1, f"{key} 缺少 minLength"
