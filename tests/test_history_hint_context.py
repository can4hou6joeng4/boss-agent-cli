"""浏览历史的后继提示在产生这份列表的数据目录中直接重放。"""

import io
import json
import shlex
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.main import cli


@pytest.fixture
def history_context(tmp_path):
	data_dir = tmp_path / "jobs [bold] team's"
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda raw: raw["zpData"]
	platform.job_history.return_value = {"zpData": {"hasMore": True, "jobList": [
		{"encryptJobId": "job_fixture", "securityId": "sid_fixture", "jobName": "测试职位", "brandName": "公司A"},
	]}}
	auth = MagicMock()

	def authenticated_in_original_directory(path, **kwargs):
		# 只有原目录有登录态：裸命令落到默认目录时必须失败，不能由 mock 放行。
		if Path(path).resolve() != data_dir.resolve():
			raise AuthRequired("测试登录态仅存在于原数据目录")
		return auth

	with ExitStack() as stack:
		stack.enter_context(patch("boss_agent_cli.commands.history.AuthManager", side_effect=authenticated_in_original_directory))
		stack.enter_context(patch("boss_agent_cli.commands.history.get_platform_instance", return_value=platform))
		yield data_dir, platform


def _run(data_dir, *args, json_mode=True):
	prefix = ["--data-dir", str(data_dir), "--json"] if json_mode else ["--data-dir", str(data_dir)]
	return CliRunner().invoke(cli, [*prefix, *args])


def test_history_followups_replay_in_original_directory(history_context):
	data_dir, _ = history_context
	result = _run(data_dir, "history", "--page", "2")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert len(actions) == 4
	for action in actions:
		parts = shlex.split(action)
		assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())]
	# 翻页提示带占位符以外都是真实参数，直接重放：登录态只在原目录，
	# mock 的 AuthManager 会拒绝默认目录，重放失败即说明提示丢了目录。
	page_action = next(action for action in actions if "--page" in action)
	replayed = CliRunner().invoke(cli, ["--json", *shlex.split(page_action)[1:]])
	assert replayed.exit_code == 0, replayed.output
	payload = json.loads(replayed.stdout)
	assert payload["ok"] is True
	assert payload["pagination"]["page"] == 3


def test_history_followups_carry_shell_quoted_directory(history_context):
	"""目录含空格、单引号与 Rich 标签时提示仍可按 shell 参数还原。"""
	data_dir, _ = history_context
	result = _run(data_dir, "history")
	assert result.exit_code == 0, result.output
	for action in json.loads(result.stdout)["hints"]["next_actions"]:
		assert str(data_dir.resolve()) in shlex.split(action)


def test_history_without_more_has_no_page_hint(history_context):
	data_dir, platform = history_context
	platform.job_history.return_value = {"zpData": {"hasMore": False, "jobList": []}}
	result = _run(data_dir, "history")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert all("--page" not in action for action in actions)
	assert all(shlex.split(action)[:3] == ["boss", "--data-dir", str(data_dir.resolve())] for action in actions)


def test_history_tty_more_hint_keeps_directory(history_context, monkeypatch):
	data_dir, _ = history_context
	stream = io.StringIO()

	from rich.console import Console

	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=200))
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	result = _run(data_dir, "history", "--page", "2", json_mode=False)

	assert result.exit_code == 0, result.output
	text = stream.getvalue()
	assert "--data-dir" in text
	assert "history --page 3" in text
