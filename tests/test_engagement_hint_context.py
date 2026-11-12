"""投递、打招呼与个人记录的提示在同一数据目录中直接重放。"""

import json
import re
import shlex
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.main import cli


@pytest.fixture
def engagement_context(tmp_path):
	data_dir = tmp_path / "jobs [bold] team's"
	platform = MagicMock()
	platform.name = "zhipin"
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda raw: raw["zpData"]
	platform.apply.return_value = {"zpData": {}}
	platform.greet.return_value = {"zpData": {}}
	platform.deliver_list.side_effect = lambda *, page: {"zpData": {
		"page": page, "hasMore": page == 1, "list": [{"jobName": "测试职位"}],
	}}
	platform.friend_list.return_value = {"zpData": {"hasMore": False, "friendList": []}}
	platform.user_info.return_value = {"zpData": {}}
	platform.resume_baseinfo.return_value = {"zpData": {}}
	platform.resume_expect.return_value = {"zpData": {}}
	jobs = [{"encryptJobId": "job_fixture", "securityId": "sid_fixture", "jobName": "测试职位"}]
	platform.search_jobs.return_value = {"zpData": {"hasMore": False, "jobList": jobs}}
	platform.recommend_jobs.return_value = {"zpData": {"hasMore": False, "jobList": jobs}}
	cache = MagicMock()
	cache.__enter__.return_value = cache
	cache.is_applied.return_value = False
	cache.is_greeted.return_value = False
	cache.get_search.return_value = None
	auth = MagicMock()
	auth.account_fingerprint.return_value = "account_fixture"

	def authenticated_in_original_directory(path, **kwargs):
		# 只有原目录有登录态。裸命令落到默认目录时必须失败，不能由 mock 放行。
		if Path(path).resolve() != data_dir.resolve():
			raise AuthRequired("测试登录态仅存在于原数据目录")
		return auth

	with ExitStack() as stack:
		auth_classes = {}
		for module in ("apply", "greet", "me", "chat", "search", "recommend"):
			auth_classes[module] = stack.enter_context(patch(
				f"boss_agent_cli.commands.{module}.AuthManager",
				side_effect=authenticated_in_original_directory,
			))
			stack.enter_context(patch(f"boss_agent_cli.commands.{module}.get_platform_instance", return_value=platform))
		for module in ("apply", "greet", "search", "recommend"):
			stack.enter_context(patch(f"boss_agent_cli.commands.{module}.CacheStore", return_value=cache))
		pipeline = stack.enter_context(patch("boss_agent_cli.commands.search.run_search_pipeline"))
		pipeline.return_value = SimpleNamespace(
			items=[{"job_id": "job_fixture", "security_id": "sid_fixture", "title": "测试职位"}],
			has_more=False, has_more_reported=False, total=1,
			stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
		)
		yield data_dir, platform, cache, auth_classes


def _replay(runner, action, data_dir):
	parts = shlex.split(action)
	assert parts[0] == "boss"
	values = {"<关键词>": "Python", "<query>": "Python", "<城市>": "北京"}
	args = [values.get(part, part) for part in parts[1:]]
	# 只使用提示里的参数，调用方不补回 --data-dir。
	result = runner.invoke(cli, ["--json", *args])
	assert result.exit_code == 0, result.output
	assert args[:2] == ["--data-dir", str(data_dir.resolve())]
	return json.loads(result.stdout)


@pytest.mark.parametrize("command", ["apply", "greet"])
def test_blank_identifier_recovery_replays_search_in_original_directory(engagement_context, command):
	data_dir, platform, _, auth_classes = engagement_context
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", command, "", "job_fixture"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	action = payload["hints"]["next_actions"][0]
	assert action in payload["error"]["recovery_action"]
	replayed = _replay(runner, action, data_dir)
	assert replayed["data"][0]["title"] == "测试职位"
	auth_classes["search"].assert_called_once()
	platform.apply.assert_not_called()
	platform.greet.assert_not_called()


@pytest.mark.parametrize("duplicate", [False, True])
def test_apply_followups_replay_delivery_and_chat_in_original_directory(engagement_context, duplicate):
	data_dir, platform, cache, auth_classes = engagement_context
	cache.is_applied.return_value = duplicate
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "apply", "sid_fixture", "job_fixture"])
	assert result.exit_code == int(duplicate), result.output
	payload = json.loads(result.stdout)
	if duplicate:
		assert payload["error"]["code"] == "ALREADY_APPLIED"
		assert payload["error"]["recoverable"] is False
	for action in payload["hints"]["next_actions"]:
		if duplicate:
			assert action in payload["error"]["recovery_action"]
		_replay(runner, action, data_dir)
	platform.deliver_list.assert_called_once_with(page=1)
	platform.friend_list.assert_called_once_with(page=1)
	assert platform.apply.call_count == (0 if duplicate else 1)
	auth_classes["me"].assert_called_once()
	auth_classes["chat"].assert_called_once()


@pytest.mark.parametrize("duplicate", [False, True])
def test_greet_followups_replay_search_and_recommend_in_original_directory(engagement_context, duplicate):
	data_dir, platform, cache, _ = engagement_context
	cache.is_greeted.return_value = duplicate
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "greet", "sid_fixture", "job_fixture"])
	assert result.exit_code == int(duplicate), result.output
	payload = json.loads(result.stdout)
	if duplicate:
		assert payload["error"]["code"] == "ALREADY_GREETED"
		assert payload["error"]["recoverable"] is False
		match = re.search(r"用 (boss .+) 查看会话", payload["error"]["recovery_action"])
		assert match is not None, payload["error"]["recovery_action"]
		_replay(runner, match[1], data_dir)
	for action in payload["hints"]["next_actions"]:
		if duplicate:
			assert action in payload["error"]["recovery_action"]
		_replay(runner, action, data_dir)
	assert platform.greet.call_count == (0 if duplicate else 1)


@pytest.mark.parametrize("write_failed", [False, True])
def test_batch_greet_followup_replays_chat_without_resending(engagement_context, write_failed):
	data_dir, platform, cache, _ = engagement_context
	if write_failed:
		cache.record_greet.side_effect = OSError("fixture write failure")
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "batch-greet", "Python", "--count", "1"])
	assert result.exit_code == int(write_failed), result.output
	payload = json.loads(result.stdout)
	if write_failed:
		assert payload["error"]["code"] == "LOCAL_STATE_WRITE_FAILED"
		assert payload["error"]["recoverable"] is False
		operator_action = next(action for action in payload["hints"]["operator_actions"] if "核对会话" in action)
		match = re.search(r"再用 (boss .+) 核对会话", operator_action)
		assert match is not None, operator_action
		action = match[1]
	else:
		action = payload["hints"]["next_actions"][0]
	_replay(runner, action, data_dir)
	platform.friend_list.assert_called_once_with(page=1)
	platform.greet.assert_called_once_with("sid_fixture", "job_fixture")


@pytest.mark.parametrize("section", [None, "deliver"])
def test_me_followups_and_next_page_use_original_directory(engagement_context, section):
	data_dir, platform, _, auth_classes = engagement_context
	runner = CliRunner()
	args = ["me"] if section is None else ["me", "--section", section]
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", *args])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	for action in payload["hints"]["next_actions"]:
		replayed = _replay(runner, action, data_dir)
		if "--deliver-page" in action:
			assert replayed["pagination"]["page"] == 2
			assert replayed["pagination"]["has_more"] is False
	assert [call.kwargs["page"] for call in platform.deliver_list.call_args_list] == ([1] if section is None else [1, 2])
	assert all(call.args[0].resolve() == data_dir.resolve() for call in auth_classes["me"].call_args_list)
