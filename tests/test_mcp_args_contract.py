"""MCP 工具参数映射契约：每个工具生成的 CLI argv 必须能被 Click 解析。

`_build_args` 是 MCP 与 CLI 之间唯一的手写映射层：参数名与选项名不一致
（`tag` → `--label`、`limit` → `--count`）、枚举值拼错、分支漏写 `--` 前缀时，
工具目录与 schema 都看不出问题，调用方只会在真实 MCP 会话里拿到 Click 的
用法错误。这里按每个工具的 `inputSchema` 自动生成样例参数，解析到目标
命令后跑一次 Argument/Options 解析（不执行命令回调），把映射错误挡在本地门禁。
"""

from __future__ import annotations

from typing import Any

import click
import pytest

from boss_agent_cli.commands import recommend as recommend_cmd_module
from boss_agent_cli.commands import search as search_cmd_module
from boss_agent_cli.commands.recruiter import (
	accept_resume as recruiter_accept_resume,
	applications as recruiter_applications,
	candidates as recruiter_candidates,
	chat as recruiter_chat,
	download_resume as recruiter_download_resume,
	recommendations as recruiter_recommendations,
	reply as recruiter_reply,
	request_resume as recruiter_request_resume,
	whoami as recruiter_whoami,
)
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS


def _sample_value(prop: dict[str, Any]) -> Any:
	"""按 JSON Schema 生成一个合法的样例值；枚举取第一个，与 CLI Choice 对齐。"""
	if "enum" in prop:
		return prop["enum"][0]
	if prop.get("type") == "array":
		return [_sample_value(prop.get("items") or {})]
	return {
		"string": "sample",
		"integer": 1,
		"number": 1.0,
		"boolean": False,
		"object": {},
	}.get(prop.get("type"), "sample")


def _resolve_command(argv: list[str]) -> tuple[click.Command, list[str]]:
	"""把 argv 拆成「命令路径」与「参数」：沿 Group 的子命令往下钻。"""
	command: click.Command = cli
	rest = list(argv)
	while isinstance(command, click.Group):
		assert rest, f"命令路径不完整：{argv}"
		child = command.commands.get(rest[0])
		assert child is not None, f"{argv} 中的 {rest[0]!r} 不是已注册命令"
		command = child
		rest = rest[1:]
	return command, rest


@pytest.mark.parametrize("tool", TOOLS, ids=[tool.name for tool in TOOLS])
def test_tool_arguments_parse_with_cli(tool) -> None:
	properties = tool.input_schema.get("properties") or {}
	arguments = {key: _sample_value(prop) for key, prop in properties.items()}

	argv = _build_args(tool.name, arguments)

	command, rest = _resolve_command(argv)
	# make_context 只做参数解析与类型校验，不执行命令回调。
	ctx = command.make_context(command.name, rest)
	ctx.close()


# 单工具 ↔ 单命令的 hr 工具：inputSchema 的属性集合必须与 Click 参数逐一相等。
# 新增 CLI 选项却忘了同步 schema（或反之）时，MCP 调用方要么看不到可用参数，
# 要么声明了 CLI 不认识的参数。`hr chat --summary-limit` / `hr last-messages --limit`
# 就是因此长期不可达。
_HR_SINGLE_COMMAND_TOOLS = {
	"boss_hr_whoami": recruiter_whoami.whoami_cmd,
	"boss_hr_applications": recruiter_applications.applications_cmd,
	"boss_hr_candidates": recruiter_candidates.candidates_cmd,
	"boss_hr_recommendations": recruiter_recommendations.recommendations_cmd,
	"boss_hr_greet": recruiter_recommendations.greet_cmd,
	"boss_hr_chat": recruiter_chat.recruiter_chat_cmd,
	"boss_hr_chatmsg": recruiter_chat.recruiter_chatmsg_cmd,
	"boss_hr_last_messages": recruiter_chat.recruiter_last_messages_cmd,
	"boss_hr_reply": recruiter_reply.reply_cmd,
	"boss_hr_request_resume": recruiter_request_resume.request_resume_cmd,
	"boss_hr_accept_resume": recruiter_accept_resume.accept_resume_cmd,
	"boss_hr_download_resume": recruiter_download_resume.download_resume_cmd,
}

# resume / exchange 共用一条 CLI 命令却被拆成两个工具；jobs 用 action 合并
# list / online / offline，detail 用位置参数。结构不同，不做集合相等比较。
_HR_STRUCTURED_TOOLS = {
	"boss_hr_resume",
	"boss_hr_exchange",
	"boss_hr_jobs",
	"boss_hr_jobs_detail",
}


def test_hr_completeness_table_covers_every_hr_tool() -> None:
	hr_tools = {tool.name for tool in TOOLS if tool.name.startswith("boss_hr_")}
	assert set(_HR_SINGLE_COMMAND_TOOLS) | _HR_STRUCTURED_TOOLS == hr_tools


@pytest.mark.parametrize(
	("tool_name", "command"),
	list(_HR_SINGLE_COMMAND_TOOLS.items()),
	ids=list(_HR_SINGLE_COMMAND_TOOLS),
)
def test_hr_tool_schema_lists_every_cli_parameter(tool_name: str, command: click.Command) -> None:
	schema = next(tool for tool in TOOLS if tool.name == tool_name).input_schema
	properties = set((schema.get("properties") or {}).keys())
	parameters = {param.name for param in command.params if param.name}
	missing_in_schema = sorted(parameters - properties)
	extra_in_schema = sorted(properties - parameters)
	assert not missing_in_schema and not extra_in_schema, (
		f"{tool_name} 的 inputSchema 与 CLI 参数不一致："
		f"CLI 有但 schema 未声明 {missing_in_schema}；schema 声明但 CLI 没有 {extra_in_schema}"
	)


# 核心读取工具同样要求 schema 与 CLI 参数对齐；CLI 参数名与 schema 不同名时
# 显式登记别名，确实不暴露的参数写明原因，避免新增筛选条件只加在 CLI 里。
_CORE_SINGLE_COMMAND_TOOLS = {
	"boss_search": (
		search_cmd_module.search_cmd,
		{"sort_mode": "sort"},
		# 二者与 query 是互斥输入源，而工具 schema 把 query 标为必填；
		# 要暴露需先设计 oneOf，本次不改契约。
		{"search_url", "preset"},
	),
	"boss_recommend": (recommend_cmd_module.recommend_cmd, {}, set()),
}


@pytest.mark.parametrize(
	("tool_name", "command", "aliases", "hidden"),
	[(tool_name, *spec) for tool_name, spec in _CORE_SINGLE_COMMAND_TOOLS.items()],
	ids=list(_CORE_SINGLE_COMMAND_TOOLS),
)
def test_core_tool_schema_lists_every_cli_parameter(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	schema = next(tool for tool in TOOLS if tool.name == tool_name).input_schema
	properties = set((schema.get("properties") or {}).keys())
	parameters = {
		aliases.get(param.name, param.name)
		for param in command.params
		if param.name and param.name not in hidden
	}
	missing_in_schema = sorted(parameters - properties)
	extra_in_schema = sorted(properties - parameters)
	assert not missing_in_schema and not extra_in_schema, (
		f"{tool_name} 的 inputSchema 与 CLI 参数不一致："
		f"CLI 有但 schema 未声明 {missing_in_schema}；schema 声明但 CLI 没有 {extra_in_schema}"
	)
