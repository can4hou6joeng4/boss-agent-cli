"""职位发现流程的提示必须沿用本次调用的数据目录。"""

import io
import json
import re
import shlex
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


@pytest.fixture
def discovery_context(tmp_path):
	# 同时覆盖 shell 引号与 Rich 标签：目录必须作为一个原样参数保留下来。
	data_dir = tmp_path / "jobs [bold] team's"
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda raw: raw["zpData"]
	platform.resume_expect.return_value = {"zpData": {}}
	platform.recommend_jobs.side_effect = lambda **kwargs: {"zpData": {
		"hasMore": kwargs["page"] == 2,
		"jobList": [{"encryptJobId": "job_fixture", "securityId": "sid_fixture", "jobName": "测试职位"}],
	}}
	platform.job_card.return_value = {"zpData": {"jobCard": {
		"encryptJobId": "job_fixture", "jobName": "测试职位",
	}}}
	platform.job_detail.return_value = {"zpData": {"jobInfo": {"jobName": "测试职位"}}}

	with ExitStack() as stack:
		auth_classes = []
		for module in ("search", "recommend", "show", "detail"):
			auth = stack.enter_context(patch(f"boss_agent_cli.commands.{module}.AuthManager"))
			auth.return_value.account_fingerprint.return_value = "account_fixture"
			auth_classes.append(auth)
			stack.enter_context(patch(f"boss_agent_cli.commands.{module}.get_platform_instance", return_value=platform))
		pipeline = stack.enter_context(patch("boss_agent_cli.commands.search.run_search_pipeline"))
		pipeline.return_value = SimpleNamespace(
			items=[{"job_id": "job_fixture", "security_id": "sid_fixture", "title": "测试职位"}],
			has_more=False,
			has_more_reported=False,
			total=1,
			stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
		)
		yield data_dir, platform, pipeline, auth_classes


def _argv(action):
	parts = shlex.split(action)
	assert parts[0] == "boss"
	return parts[1:]


def test_recommend_next_page_replays_same_directory_and_score(discovery_context):
	data_dir, platform, _, auth_classes = discovery_context
	runner = CliRunner()
	first = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "recommend", "--page", "2", "--with-score"])
	assert first.exit_code == 0, first.output
	actions = json.loads(first.stdout)["hints"]["next_actions"]
	for action in actions:
		assert _argv(action)[:2] == ["--data-dir", str(data_dir.resolve())]
	next_page = next(action for action in actions if "--page" in action)
	second = runner.invoke(cli, ["--json", *_argv(next_page)])
	assert second.exit_code == 0, second.output
	payload = json.loads(second.stdout)
	assert payload["pagination"]["page"] == 3
	assert "match_score" in payload["data"][0]
	assert [call.kwargs["page"] for call in platform.recommend_jobs.call_args_list] == [2, 3]
	assert platform.resume_expect.call_count == 2
	assert [call.args[0].resolve() for call in auth_classes[1].call_args_list] == [data_dir.resolve()] * 2


def test_show_recovery_rebuilds_index_in_same_directory(discovery_context):
	data_dir, _, pipeline, _ = discovery_context
	runner = CliRunner()
	missing = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "show", "1"])
	assert missing.exit_code == 1, missing.output
	payload = json.loads(missing.stdout)
	search_action = payload["hints"]["next_actions"][0]
	assert search_action in payload["error"]["recovery_action"]
	search_args = ["Python" if part == "<关键词>" else part for part in _argv(search_action)]
	assert search_args[:2] == ["--data-dir", str(data_dir.resolve())]

	searched = runner.invoke(cli, ["--json", *search_args])
	assert searched.exit_code == 0, searched.output
	pipeline.assert_called_once()
	shown = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "show", "1"])
	assert shown.exit_code == 0, shown.output
	assert json.loads(shown.stdout)["data"]["title"] == "测试职位"


@pytest.mark.parametrize("command", ["detail", "show"])
def test_detail_follow_ups_preserve_directory(discovery_context, command):
	data_dir, _, _, _ = discovery_context
	runner = CliRunner()
	if command == "show":
		seed = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "recommend"])
		assert seed.exit_code == 0, seed.output
		args = ["show", "1"]
	else:
		args = ["detail", "sid_fixture", "--job-id", "job_fixture"]
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", *args])
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert [_argv(action) for action in actions] == [
		["--data-dir", str(data_dir.resolve()), "apply", "<security_id>", "<job_id>"],
		["--data-dir", str(data_dir.resolve()), "greet", "<security_id>", "<job_id>"],
		["--data-dir", str(data_dir.resolve()), "search", "<query>"],
	]


@pytest.mark.parametrize("source", ["recommend", "search", "search-cache"])
def test_table_show_hint_opens_same_index(discovery_context, monkeypatch, source):
	data_dir, _, pipeline, _ = discovery_context
	runner = CliRunner()
	args = ["recommend"] if source == "recommend" else ["search", "Python"]
	if source == "search-cache":
		seed = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", *args])
		assert seed.exit_code == 0, seed.output
		pipeline.reset_mock()

	stream = io.StringIO()
	with monkeypatch.context() as tty:
		tty.setattr(display, "is_json_mode", lambda ctx: False)
		tty.setattr(display, "console", Console(file=stream, width=2000, color_system=None))
		listed = runner.invoke(cli, ["--data-dir", str(data_dir), *args])
	assert listed.exit_code == 0, listed.output
	if source == "search-cache":
		pipeline.assert_not_called()

	match = re.search(r"use: (.+) to view details", stream.getvalue())
	assert match is not None, stream.getvalue()
	show_args = ["1" if part == "<#>" else part for part in _argv(match[1])]
	assert show_args == ["--data-dir", str(data_dir.resolve()), "show", "1"]
	shown = runner.invoke(cli, ["--json", *show_args])
	assert shown.exit_code == 0, shown.output
	assert json.loads(shown.stdout)["data"]["index_snapshot"]["source"] == ("recommend" if source == "recommend" else "search:Python")


def test_search_terminal_next_page_preserves_literal_directory(discovery_context, monkeypatch):
	data_dir, _, pipeline, _ = discovery_context
	pipeline.return_value.has_more = True
	pipeline.return_value.has_more_reported = True
	stream = io.StringIO()
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
	monkeypatch.setattr(display, "console", Console(file=stream, width=2000, color_system=None))
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "search", "Python"])
	assert result.exit_code == 0, result.output
	match = re.search(r"more: (.+)", stream.getvalue())
	assert match is not None, stream.getvalue()
	assert _argv(match[1]) == ["--data-dir", str(data_dir.resolve()), "search", "Python", "--page", "2"]


def test_show_terminal_error_renders_literal_directory(discovery_context, monkeypatch):
	"""TTY 错误恢复里的目录同样按字面显示；含闭合标签的目录不能击穿 CLI。"""
	data_dir, _, _, _ = discovery_context
	stream = io.StringIO()
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
	monkeypatch.setattr(display, "console", Console(file=stream, width=2000, color_system=None))
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "show", "1"])
	assert result.exit_code == 1, result.output
	output = stream.getvalue()
	assert "当前数据目录没有缓存的搜索结果" in output
	recovery = next(line for line in output.splitlines() if "recovery:" in line).split("recovery:", 1)[1]
	parts = shlex.split(recovery)
	assert parts[parts.index("--data-dir") + 1] == str(data_dir.resolve())


@pytest.mark.parametrize("command", ["detail", "show"])
def test_detail_terminal_hint_preserves_literal_directory(discovery_context, monkeypatch, command):
	data_dir, _, _, _ = discovery_context
	runner = CliRunner()
	if command == "show":
		runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "recommend"])
		args = ["show", "1"]
	else:
		args = ["detail", "sid_fixture", "--job-id", "job_fixture"]

	stream = io.StringIO()
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
	monkeypatch.setattr(display, "console", Console(file=stream, width=2000, color_system=None))
	result = runner.invoke(cli, ["--data-dir", str(data_dir), *args])
	assert result.exit_code == 0, result.output
	hint = stream.getvalue().split("next: ", 1)[1].strip()
	for action in hint.split(" 或 "):
		assert _argv(action)[:2] == ["--data-dir", str(data_dir.resolve())]
