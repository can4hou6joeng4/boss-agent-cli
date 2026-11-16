"""MCP 响应与参数边界回归（Issue #1375 / #1376 / #1377）。

- #1375：CLI 信封里的孤立代理转义（``\\udcff``）被 ``json.loads`` 还原后，
  工具结果用 ``ensure_ascii=False`` 写回，MCP SDK 序列化 ``TextContent`` 时抛
  ``PydanticSerializationError``，异常穿出 ``stdio_server()`` 让进程退出。
- #1376：空标签是任何标签名的子串，模糊匹配把它解析成「新招呼」并在平台上
  真的增删标签；MCP ``boss_mark`` 原样下发空 ``tag``。
- #1377：digest / stats 的显式空输出路径被映射层的真值判断当成「没传」，
  argv 缺 ``-o``，CLI 返回 ``ok:true`` 却不写文件。

真实 SDK 的序列化断言放在子进程里：``tests/test_mcp_server.py`` 会在导入时把
``mcp`` 换成 mock 模块，pytest 单进程内拿到的 ``TextContent`` 不一定是真类。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner
import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.commands.mark import _resolve_label
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS


# ── 工具结果序列化（Issue #1375） ─────────────────────────────────


def _surrogate_wire() -> str:
	"""平台侧可能下发的 JSON：``bad\\udcffname`` 是合法的孤立代理转义。"""
	bad = "bad" + chr(0xDCFF) + "name"
	return json.dumps(
		{
			"ok": True,
			"schema_version": "1.0",
			"command": "status",
			"data": {"name": bad},
			"pagination": None,
			"error": None,
			"hints": None,
		},
		ensure_ascii=True,
	)


class _RecordedContent:
	def __init__(self, **kwargs: Any) -> None:
		self.__dict__.update(kwargs)


class _RecordedResult:
	def __init__(self, **kwargs: Any) -> None:
		self.__dict__.update(kwargs)


def _patch_result_types(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(mcp_server, "TextContent", _RecordedContent)
	monkeypatch.setattr(mcp_server, "CallToolResult", _RecordedResult)


def test_parse_cli_output_surrogate_is_serialized_as_escape() -> None:
	"""``json.loads`` 还原出的孤立代理不能在序列化时原样写回。"""
	wire = _surrogate_wire()
	parsed = mcp_server._parse_cli_output(("status",), wire, "")

	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "bad" + chr(0xDCFF) + "name"

	text = mcp_server._dumps_tool_result(parsed)
	# 字面转义（反斜杠 + udcff）而不是孤立代理本身：JSON 解析后的值不变，
	# 但结果一定能按 UTF-8 编码，SDK 不再抛 PydanticSerializationError。
	assert chr(92) + "udcff" in text
	text.encode("utf-8")
	assert json.loads(text)["data"]["name"] == parsed["data"]["name"]


def test_call_tool_surrogate_result_keeps_ok_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
	"""进程级 ``call_tool`` 仍返回可编码的 ok 信封，而不是异常穿出 handler。"""
	_patch_result_types(monkeypatch)

	async def fake_run(*args: str, **kwargs: Any) -> dict[str, Any]:
		return mcp_server._parse_cli_output(args, _surrogate_wire(), "")

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)

	result = asyncio.run(
		mcp_server.call_tool(None, SimpleNamespace(name="boss_status", arguments={}))
	)

	assert result.is_error is False
	text = result.content[0].text
	assert chr(92) + "udcff" in text
	payload = json.loads(text)
	assert payload["ok"] is True
	assert payload["command"] == "status"


_SDK_PROBE = dedent(
	"""\
	import asyncio
	import json

	from mcp.types import CallToolRequestParams

	import boss_agent_cli.mcp_server as mcp_server

	bad = "bad" + chr(0xDCFF) + "name"
	wire = json.dumps({
		"ok": True,
		"schema_version": "1.0",
		"command": "status",
		"data": {"name": bad},
		"pagination": None,
		"error": None,
		"hints": None,
	}, ensure_ascii=True)


	async def fake_run(*args, **kwargs):
		return mcp_server._parse_cli_output(args, wire, "")


	mcp_server._run_boss_async = fake_run
	result = asyncio.run(mcp_server.call_tool(
		None, CallToolRequestParams(name="boss_status", arguments={}),
	))
	text = result.content[0].text
	dumped = result.model_dump_json()
	print(json.dumps({
		"ok": json.loads(text)["ok"],
		"contains_escape": (chr(92) + "udcff") in text,
		"dumped_is_str": isinstance(dumped, str),
	}))
	"""
)


def test_real_sdk_serializes_surrogate_tool_result() -> None:
	""".model_dump_json() 在真实 mcp SDK 下不抛异常，text 含字面转义。"""
	root = Path(__file__).resolve().parents[1]
	env = dict(os.environ)
	existing = env.get("PYTHONPATH")
	env["PYTHONPATH"] = str(root / "src") + (os.pathsep + existing if existing else "")
	completed = subprocess.run(
		[sys.executable, "-c", _SDK_PROBE],
		capture_output=True,
		text=True,
		encoding="utf-8",
		timeout=60,
		env=env,
	)
	assert completed.returncode == 0, completed.stderr
	assert json.loads(completed.stdout) == {
		"ok": True,
		"contains_escape": True,
		"dumped_is_str": True,
	}


# ── 空标签（Issue #1376） ─────────────────────────────────────────


def test_boss_mark_schema_rejects_empty_tag() -> None:
	tool = next(item for item in TOOLS if item.name == "boss_mark")
	tag = tool.input_schema["properties"]["tag"]
	assert tag["minLength"] == 1


@pytest.mark.parametrize("label", ["", " ", "\t"], ids=["empty", "space", "tab"])
def test_resolve_label_rejects_blank_input(label: str) -> None:
	with pytest.raises(click.BadParameter) as exc_info:
		_resolve_label(label)
	message = str(exc_info.value)
	assert "标签不能为空" in message
	# 与既有「未知标签」诊断一样列出可用标签，Agent 能直接改正。
	assert "新招呼" in message


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_empty_label_returns_invalid_param_without_platform(
	mock_auth_cls: MagicMock, mock_platform_cls: MagicMock, tmp_path: Path,
) -> None:
	"""``mark <uid> --label ""`` 必须在触达平台之前返回 INVALID_PARAM。"""
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "mark", "sec_001", "--label", ""]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "标签不能为空" in payload["error"]["message"]
	mock_platform_cls.assert_not_called()


def test_boss_mark_empty_tag_call_tool_returns_invalid_param(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
	"""MCP ``boss_mark`` 传空 ``tag`` 时，CLI 的 INVALID_PARAM 原样回到 Agent。"""
	from boss_agent_cli.commands import mark as mark_cmd_module

	_patch_result_types(monkeypatch)
	platform_calls: list[tuple[Any, Any]] = []

	def unexpected_platform(*args: Any, **kwargs: Any) -> Any:
		platform_calls.append((args, kwargs))
		raise AssertionError("空标签不应触达平台")

	monkeypatch.setattr(mark_cmd_module, "AuthManager", MagicMock())
	monkeypatch.setattr(mark_cmd_module, "get_platform_instance", unexpected_platform)

	async def fake_run(*args: str, **kwargs: Any) -> dict[str, Any]:
		completed = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), *args]
		)
		return json.loads(completed.output)

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)

	result = asyncio.run(
		mcp_server.call_tool(
			None,
			SimpleNamespace(
				name="boss_mark", arguments={"security_id": "sec_001", "tag": ""}
			),
		)
	)

	payload = json.loads(result.content[0].text)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert result.is_error is True
	assert platform_calls == []


# ── 显式空输出路径（Issue #1377） ─────────────────────────────────


@pytest.mark.parametrize(
	("tool_name", "arguments", "expected"),
	[
		(
			"boss_digest",
			{"format": "md", "output": ""},
			["digest", "--format", "md", "-o", ""],
		),
		(
			"boss_stats",
			{"format": "html", "output_file": ""},
			["stats", "--format", "html", "--output", ""],
		),
	],
	ids=["digest-output", "stats-output-file"],
)
def test_empty_output_path_reaches_cli_verbatim(
	tool_name: str, arguments: dict[str, Any], expected: list[str],
) -> None:
	"""显式空串的输出路径必须留在 argv 里，让 CLI 自己给出错误信封。"""
	assert _build_args(tool_name, arguments) == expected


@pytest.mark.parametrize(
	("tool_name", "arguments", "expected"),
	[
		("boss_chat", {"format": "md", "output_file": ""}, ["chat", "--export", "md"]),
		("boss_export", {"query": "python", "format": "md", "output_file": ""}, ["export", "python", "--format", "md"]),
	],
	ids=["chat", "export"],
)
def test_other_empty_output_paths_stay_omitted(
	tool_name: str, arguments: dict[str, Any], expected: list[str],
) -> None:
	"""chat / export 的 ``-o ""`` 行为不变：仍按未传处理。"""
	assert _build_args(tool_name, arguments) == expected


def test_stats_empty_output_call_tool_returns_error_without_file(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
	"""``boss_stats`` 空输出路径不再返回 ok=true：CLI 写盘失败并给出错误信封。"""
	_patch_result_types(monkeypatch)
	captured: dict[str, Any] = {}
	workdir = tmp_path / "cwd"
	workdir.mkdir()
	monkeypatch.chdir(workdir)

	async def fake_run(*args: str, **kwargs: Any) -> dict[str, Any]:
		captured["args"] = args
		completed = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), *args]
		)
		captured["files"] = sorted(path.name for path in Path.cwd().iterdir())
		return json.loads(completed.output)

	monkeypatch.setattr(mcp_server, "_run_boss_async", fake_run)

	result = asyncio.run(
		mcp_server.call_tool(
			None,
			SimpleNamespace(
				name="boss_stats",
				arguments={"format": "html", "output_file": ""},
			),
		)
	)

	assert captured["args"] == ("stats", "--format", "html", "--output", "")
	payload = json.loads(result.content[0].text)
	assert payload["ok"] is False
	assert result.is_error is True
	assert captured["files"] == []
