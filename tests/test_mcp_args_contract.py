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
