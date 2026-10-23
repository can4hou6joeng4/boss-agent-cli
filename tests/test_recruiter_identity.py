"""招聘者身份判断：identity=1 才就绪，recruit 不参与。"""
import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS, _compliance_command_for_tool
from boss_agent_cli.services.recruiter_identity import interpret_recruiter_identity


def test_recruiter_workbench_identity_is_ready_when_recruit_flag_is_false():
	record = interpret_recruiter_identity({
		"name": "hidden",
		"phone": "hidden",
		"identity": 1,
		"doubleIdentity": True,
		"recruit": False,
	})
	assert record == {
		"identity": 1,
		"side": "recruiter",
		"double_identity": True,
		"profile_recruit": False,
		"ready": True,
		"basis": "user_info.identity",
	}
	assert "hidden" not in record.values()


def test_candidate_side_is_not_ready_even_with_double_identity():
	record = interpret_recruiter_identity({
		"identity": "0",
		"doubleIdentity": 1,
		"recruit": 0,
	})
	assert record["side"] == "candidate"
	assert record["double_identity"] is True
	assert record["profile_recruit"] is False
	assert record["ready"] is False


def test_unknown_identity_is_not_ready():
	record = interpret_recruiter_identity({"identity": True, "recruit": "yes"})
	assert record["identity"] is None
	assert record["side"] == "unknown"
	assert record["double_identity"] is None
	assert record["profile_recruit"] is None
	assert record["ready"] is False


def _platform(factory, payload):
	platform = factory.return_value
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *args: None
	platform.user_info.return_value = {"code": 0, "zpData": payload}
	platform.is_success.return_value = True
	platform.unwrap_data.return_value = payload
	return platform


@patch("boss_agent_cli.commands.recruiter.whoami.AuthManager")
@patch("boss_agent_cli.commands.recruiter.whoami.get_platform_instance")
def test_hr_whoami_reports_recruiter_side(factory, _auth):
	_platform(factory, {"identity": 1, "doubleIdentity": True, "recruit": False, "name": "hidden"})
	result = CliRunner().invoke(cli, ["--json", "hr", "whoami"])
	parsed = json.loads(result.output)
	assert result.exit_code == 0
	assert parsed["command"] == "recruiter-whoami"
	assert parsed["data"]["ready"] is True
	assert parsed["data"]["profile_recruit"] is False
	assert "hidden" not in result.output
	assert parsed["hints"]["next_actions"] == ["boss hr chat", "boss hr jobs list"]


@patch("boss_agent_cli.commands.recruiter.whoami.AuthManager")
@patch("boss_agent_cli.commands.recruiter.whoami.get_platform_instance")
def test_hr_whoami_asks_double_identity_account_to_switch_on_the_official_site(factory, _auth):
	_platform(factory, {"identity": 0, "doubleIdentity": True, "recruit": False})
	result = CliRunner().invoke(cli, ["--json", "hr", "whoami"])
	parsed = json.loads(result.output)
	assert result.exit_code == 0
	assert parsed["ok"] is True
	assert parsed["data"]["ready"] is False
	assert "我要招聘" in parsed["hints"]["operator_actions"][0]
	assert parsed["hints"]["next_actions"] == ["boss hr whoami"]


@patch("boss_agent_cli.commands.recruiter.whoami.AuthManager")
@patch("boss_agent_cli.commands.recruiter.whoami.get_platform_instance")
def test_hr_whoami_surfaces_platform_error(factory, _auth):
	platform = factory.return_value
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *args: None
	platform.user_info.return_value = {"code": 7, "message": "当前登录状态已失效"}
	platform.is_success.return_value = False
	platform.parse_error.return_value = ("AUTH_EXPIRED", "当前登录状态已失效")
	result = CliRunner().invoke(cli, ["--json", "hr", "whoami"])
	parsed = json.loads(result.output)
	assert result.exit_code == 1
	assert parsed["error"]["code"] == "AUTH_EXPIRED"


def test_whoami_tool_is_discoverable():
	tool = next(item for item in TOOLS if item.name == "boss_hr_whoami")
	assert tool.input_schema["required"] == []
	assert _build_args("boss_hr_whoami", {}) == ["hr", "whoami"]
	assert _compliance_command_for_tool("boss_hr_whoami") == "recruiter-whoami"
