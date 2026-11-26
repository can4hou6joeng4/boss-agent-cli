"""shortlist add 的来源契约：工具 schema 默认值与空白值（Issues #2968 #2970）。

#2960 之后「省略 source」在 CLI / ``SCHEMA_DATA`` 里的语义是「新记录记为
manual，重复加入保留原来源」。工具 schema 仍声明 ``default: manual``，宿主按
schema 注入默认值会覆盖既有来源；空白值则被当成显式来源落库并被后续省略
参数永久保留。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS
from boss_agent_cli.schema.data import SCHEMA_DATA


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", "shortlist", *args])


def _tool(name: str):
	return next(tool for tool in TOOLS if tool.name == name)


def test_tool_schema_does_not_default_source_to_manual() -> None:
	source = _tool("boss_shortlist_add").input_schema["properties"]["source"]

	assert source.get("default") is None
	assert "保留原来源" in source["description"]


def test_tool_schema_matches_schema_data_source_contract() -> None:
	option = SCHEMA_DATA["commands"]["shortlist"]["options"]["add"]["--source"]
	source = _tool("boss_shortlist_add").input_schema["properties"]["source"]

	assert option["default"] is None
	assert source["default"] is None
	assert source["description"] == option["description"]


def test_build_args_omits_source_when_absent() -> None:
	argv = _build_args("boss_shortlist_add", {"security_id": "S", "job_id": "J"})

	assert "--source" not in argv


@pytest.mark.parametrize("source", ["", "   ", "\t\n"])
def test_blank_source_is_rejected_before_write(tmp_path: Path, source: str) -> None:
	result = _invoke(tmp_path, "add", "S", "J", "--source", source)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--source" in payload["error"]["message"]
	assert "省略 --source" in payload["error"]["recovery_action"]
	assert json.loads(_invoke(tmp_path, "list").stdout)["data"] == []


def test_blank_source_does_not_touch_existing_record(tmp_path: Path) -> None:
	first = _invoke(tmp_path, "add", "S", "J", "--source", "favorites")
	assert first.exit_code == 0, first.output

	rejected = _invoke(tmp_path, "add", "S", "J", "--source", "   ")
	assert rejected.exit_code == 1, rejected.output

	listed = json.loads(_invoke(tmp_path, "list").stdout)["data"]
	assert [(item["security_id"], item["source"]) for item in listed] == [("S", "favorites")]
