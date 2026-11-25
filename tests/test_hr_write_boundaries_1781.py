"""招聘者写操作边界：开聊结果标记漂移、空 ID、后继命令引用（Issue #1781 #1782 #1783）。"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

_RECOMMENDATIONS = "boss_agent_cli.commands.recruiter.recommendations"
_JOBS = "boss_agent_cli.commands.recruiter.jobs"
_RESUME = "boss_agent_cli.commands.recruiter.resume"


def _greet_args(tmp_path: Path, **overrides: str) -> list[str]:
	values = {"geek-id": "geek", "job-id": "job", "expect-id": "expect", "lid": "lid", "security-id": "security", "message": "hello"}
	values.update({key.replace("_", "-"): value for key, value in overrides.items()})
	args = ["--data-dir", str(tmp_path), "--json", "hr", "greet"]
	for key, value in values.items():
		args.extend([f"--{key}", value])
	return args


def _invoke_with_platform(module: str, client: MagicMock, args: list[str]):
	with patch(f"{module}.AuthManager"), patch(f"{module}.get_recruiter_platform_instance") as factory:
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		return CliRunner().invoke(cli, args)


@pytest.mark.parametrize("chat", ["0", " 0 ", 0.0])
@pytest.mark.parametrize("status", [3, "3", 3.0])
def test_quota_page_with_drifted_flags_is_not_reported_as_sent(tmp_path, chat, status):
	client = MagicMock()
	client.start_chat.return_value = {
		"code": 0, "__cli_endpoint_hint__": ep.BOSS_CHAT_START_URL,
		"zpData": {"chat": chat, "status": status, "limitTitle": "今日主动沟通人数已达上限"},
	}
	args = [*_greet_args(tmp_path), "--yes"]

	first = _invoke_with_platform(_RECOMMENDATIONS, client, args)
	second = _invoke_with_platform(_RECOMMENDATIONS, client, args)

	assert first.exit_code == 1, first.output
	body = json.loads(first.output)
	assert body["error"]["code"] == "GREET_LIMIT"
	assert body["error"]["details"]["sent"] is False
	assert json.loads(second.output)["error"]["code"] == "GREET_LIMIT", "被拦截的招呼不能记成 ALREADY_GREETED"
	client.start_chat.assert_called_once()


@pytest.mark.parametrize("chat", [1, "1", True, False, None, "abc", 0.5])
def test_non_zero_chat_flag_keeps_the_existing_success_contract(chat):
	platform = BossRecruiterPlatform(MagicMock())
	response = {"code": 0, "zpData": {"chat": chat, "status": 3, "limitTitle": "旧提示"}, "__cli_endpoint_hint__": ep.BOSS_CHAT_START_URL}
	assert platform.is_success(response) is True


# lid 是访问上下文而非目标标识（平台可能给空值，schema 也允许空串），
# 空白校验只覆盖真正标识候选人与职位的 ID（Issue #2789）。
@pytest.mark.parametrize("field", ["geek_id", "job_id", "expect_id", "security_id"])
@pytest.mark.parametrize("value", ["", "   "])
@pytest.mark.parametrize("mode", ["--yes", "--dry-run"])
def test_greet_rejects_blank_ids_before_auth_request_and_reservation(tmp_path, field, value, mode):
	client = MagicMock()
	with patch(f"{_RECOMMENDATIONS}.AuthManager") as auth:
		result = _invoke_with_platform(_RECOMMENDATIONS, client, [*_greet_args(tmp_path, **{field: value}), mode])
		auth.assert_not_called()

	assert result.exit_code == 1, result.output
	body = json.loads(result.output)
	assert body["error"]["code"] == "INVALID_PARAM"
	assert f"--{field.replace('_', '-')}" in body["error"]["message"]
	assert body["error"]["recovery_action"]
	assert body["hints"]["next_actions"][0].endswith("hr recommendations --job-id <job_id>")
	client.start_chat.assert_not_called()
	assert not (tmp_path / "cache" / "boss_agent.db").exists()


@pytest.mark.parametrize("value", ["", " \t"])
def test_greet_allows_blank_lid_in_dry_run_without_auth_request(tmp_path, value):
	client = MagicMock()
	with patch(f"{_RECOMMENDATIONS}.AuthManager") as auth:
		result = _invoke_with_platform(_RECOMMENDATIONS, client, [*_greet_args(tmp_path, lid=value), "--dry-run"])
		auth.assert_not_called()

	assert result.exit_code == 0, result.output
	body = json.loads(result.output)
	assert body["data"]["dry_run"] is True
	client.start_chat.assert_not_called()
	assert not (tmp_path / "cache" / "boss_agent.db").exists()


@pytest.mark.parametrize("value", ["", " \t"])
def test_recommendations_rejects_blank_job_id_without_a_request(tmp_path, value):
	client = MagicMock()
	result = _invoke_with_platform(
		_RECOMMENDATIONS, client, ["--data-dir", str(tmp_path), "--json", "hr", "recommendations", "--job-id", value],
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
	client.recommend_geeks.assert_not_called()


def test_resume_rejects_whitespace_ids_without_a_request(tmp_path):
	client = MagicMock()
	result = _invoke_with_platform(
		_RESUME, client,
		["--data-dir", str(tmp_path), "--json", "hr", "resume", " ", "--job-id", " ", "--security-id", " "],
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
	client.view_geek.assert_not_called()


@pytest.mark.parametrize("job_id", ["abc; rm -rf ~", "x$(id)", " abc ", "--", "-x", "plain"])
@pytest.mark.parametrize("action", ["offline", "online", "detail"])
def test_job_follow_up_commands_replay_the_exact_job_id(tmp_path, job_id, action):
	client = MagicMock()
	client.job_offline.return_value = {"code": 0}
	client.job_online.return_value = {"code": 0}
	client.job_detail.return_value = {"code": 0, "zpData": {}}
	result = _invoke_with_platform(
		_JOBS, client, ["--data-dir", str(tmp_path), "--json", "hr", "jobs", action, "--", job_id],
	)

	assert result.exit_code == 0, result.output
	follow_ups = [
		shlex.split(command) for command in json.loads(result.output)["hints"]["next_actions"]
		if " hr jobs online" in command or " hr jobs offline" in command
	]
	assert follow_ups
	for argv in follow_ups:
		tail = argv[argv.index("jobs") + 2:]
		assert tail == (["--", job_id] if job_id.startswith("-") else [job_id]), argv
