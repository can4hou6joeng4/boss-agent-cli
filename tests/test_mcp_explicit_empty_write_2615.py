"""MCP 写工具不得吞掉显式空串而绕过 CLI 空值守卫（Issue #2615）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args


def test_greet_explicit_empty_message_reaches_cli_guard() -> None:
	args = _build_args("boss_greet", {"security_id": "sec_fake", "job_id": "job_fake", "message": ""})
	assert args == ["greet", "sec_fake", "job_fake", "--message", ""]

	result = CliRunner().invoke(cli, ["--json", *args])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--message" in payload["error"]["message"]


def test_batch_greet_explicit_empty_filters_reach_cli_guard() -> None:
	args = _build_args(
		"boss_batch_greet",
		{
			"query": "前端",
			"city": "",
			"salary": "",
			"experience": "",
			"education": "",
			"limit": 1,
		},
	)
	assert args == [
		"batch-greet",
		"前端",
		"--city",
		"",
		"--salary",
		"",
		"--experience",
		"",
		"--education",
		"",
		"--count",
		"1",
	]

	result = CliRunner().invoke(cli, ["--json", *args])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--city" in payload["error"]["message"]


def test_hr_candidates_explicit_empty_filters_reach_cli_guard() -> None:
	args = _build_args("boss_hr_candidates", {"query": "x", "city": "", "job_id": ""})
	assert args == ["hr", "candidates", "x", "--city", "", "--job-id", ""]

	result = CliRunner().invoke(cli, ["--json", *args])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--city" in payload["error"]["message"]


def test_omitted_filters_still_keep_previous_behavior() -> None:
	assert _build_args("boss_greet", {"security_id": "s", "job_id": "j"}) == ["greet", "s", "j"]
	assert _build_args("boss_batch_greet", {"query": "q", "limit": 1}) == ["batch-greet", "q", "--count", "1"]
	assert _build_args("boss_hr_candidates", {"query": "x"}) == ["hr", "candidates", "x"]
