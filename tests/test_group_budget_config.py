"""成员目录指向组预算目录时，组 config.json 的确定性错误必须在启动期拒绝（Issue #1367）。

``RequestBudget.for_data_dir`` 在客户端构造阶段才读取组目录配置：坏 JSON 或
``request_max_per_minute: 0`` 会抛 ``ValueError``，随后被命令层通用兜底报成
``NETWORK_ERROR`` + ``recoverable=true`` +「重试」。这是本地配置错误，每条平台
命令都会复现，必须在启动期按 ``INVALID_PARAM`` 拒绝，且不构造客户端。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.api.request_budget import RequestBudget
from boss_agent_cli.main import cli


def _setup_member_group(tmp_path: Path, group_config_text: str) -> tuple[Path, Path]:
	"""建一个指向组目录的成员目录，返回 (成员目录, 组 config.json 路径)。"""
	member = tmp_path / "member"
	group = tmp_path / "group"
	member.mkdir()
	group.mkdir()
	(member / "config.json").write_text(
		json.dumps({"request_budget_dir": str(group)}), encoding="utf-8"
	)
	group_config = group / "config.json"
	group_config.write_text(group_config_text, encoding="utf-8")
	return member, group_config


def _invoke_search(member: Path):
	return CliRunner().invoke(cli, ["--data-dir", str(member), "--json", "search", "golang"])


@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_bad_group_config_json_is_invalid_param_before_client(mock_auth_cls, mock_platform_cls, tmp_path):
	"""组 config.json 是坏 JSON：按参数错误拒绝，点名文件，且不构造认证 / 客户端。"""
	member, group_config = _setup_member_group(tmp_path, "{not json")

	result = _invoke_search(member)

	assert result.exit_code == 1
	payload = json.loads(result.output.strip())
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert error["recoverable"] is False
	assert error["recovery_action"] != "重试"
	shown_path = str(group_config.resolve())
	assert shown_path in error["message"]
	assert shown_path in error["recovery_action"]
	assert "不是合法 JSON" in error["message"]
	mock_auth_cls.assert_not_called()
	mock_platform_cls.assert_not_called()


@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_zero_request_max_per_minute_in_group_config_is_rejected(mock_auth_cls, mock_platform_cls, tmp_path):
	"""组配置 ``request_max_per_minute: 0`` 同样在启动期按参数错误拒绝。"""
	member, group_config = _setup_member_group(tmp_path, json.dumps({"request_max_per_minute": 0}))

	result = _invoke_search(member)

	assert result.exit_code == 1
	payload = json.loads(result.output.strip())
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert error["recoverable"] is False
	shown_path = str(group_config.resolve())
	assert shown_path in error["message"]
	assert shown_path in error["recovery_action"]
	assert "request_max_per_minute" in error["message"]
	mock_auth_cls.assert_not_called()
	mock_platform_cls.assert_not_called()


def test_valid_group_config_does_not_block_command(tmp_path):
	"""合法组配置不被启动期校验阻断。"""
	member, _ = _setup_member_group(tmp_path, json.dumps({"request_max_per_minute": 5}))

	result = CliRunner().invoke(cli, ["--data-dir", str(member), "--json", "schema"])

	assert result.exit_code == 0
	payload = json.loads(result.output.strip())
	assert payload["ok"] is True
	assert payload["command"] == "schema"


def test_valid_group_config_still_shares_budget_directory(tmp_path):
	"""合法组配置仍按原行为共享预算目录与组限流值。"""
	member, group_config = _setup_member_group(
		tmp_path, json.dumps({"request_max_per_minute": 5, "request_wait_timeout": 12})
	)

	budget = RequestBudget.for_data_dir(member)

	assert budget.root == group_config.parent.resolve()
	assert budget.max_per_minute == 5
	assert budget.wait_timeout == 12
