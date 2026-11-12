"""stats / interviews / cities / platforms 的后继提示在产生它的数据目录中直接重放。"""

import html
import json
import shlex
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.display import shell_argument
from boss_agent_cli.main import cli


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
	"""数据目录含空格、单引号与 Rich 标签；默认目录指向一次性 HOME。"""
	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	path = tmp_path / "jobs [bold] team's"
	path.mkdir()
	return path


def _run(data_dir, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _prefix(action: str, data_dir) -> list[str]:
	parts = shlex.split(action)
	assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())], action
	return parts


def test_stats_hints_keep_directory_and_replay(data_dir):
	result = _run(data_dir, "stats")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert [_prefix(action, data_dir)[3:] for action in actions] == [
		["search", "<query>"],
		["pipeline"],
		["follow-up"],
	]
	# pipeline / follow-up 需要登录态；补 --help 验证提示的命令解析无误。
	for action in actions[1:]:
		replayed = CliRunner().invoke(cli, ["--json", *shlex.split(action)[1:], "--help"])
		assert replayed.exit_code == 0, replayed.output


def test_stats_tty_hints_render_directory_literally(tmp_path, monkeypatch):
	"""目录名含 Rich 结束标签时，TTY 的「下一步」必须按字面渲染而不是抛异常。"""
	import io

	from rich.console import Console

	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	data_dir = tmp_path / "[/dim] team"
	data_dir.mkdir(parents=True)
	stream = io.StringIO()
	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=200))
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)

	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "stats"])

	assert result.exit_code == 0, result.output
	assert "[/dim] team" in stream.getvalue()


def test_stats_operator_action_keeps_directory(data_dir):
	stats = {
		"window_days": 30,
		"funnel": {"greeted": 20, "applied": 0, "shortlist": 0, "watch_hits": 0},
		"conversion": {"apply_rate": 0.0, "shortlist_rate": 0.0, "apply_rate_window": 0.0},
	}
	with patch("boss_agent_cli.commands.stats._collect_stats", return_value=stats):
		result = _run(data_dir, "stats")

	assert result.exit_code == 0, result.output
	prose = json.loads(result.stdout)["hints"]["operator_actions"][0]
	optimize = prose.split("（", 1)[1].split("）", 1)[0]
	assert _prefix(optimize, data_dir)[3:] == ["ai", "optimize"]


def test_stats_html_report_hints_keep_directory(data_dir):
	target = data_dir / "report.html"
	result = _run(data_dir, "stats", "--format", "html", "-o", str(target))

	assert result.exit_code == 0, result.output
	content = target.read_text(encoding="utf-8")
	assert html.escape(f"boss --data-dir {shell_argument(str(data_dir.resolve()))} pipeline") in content


def test_stats_json_html_next_action_keeps_directory(data_dir):
	result = _run(data_dir, "stats", "--format", "html")

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	action = parsed["hints"]["next_actions"][0]
	assert _prefix(action, data_dir)[3:] == ["stats", "--format", "html", "-o", "<path>"]


def test_interviews_hint_keeps_directory(data_dir):
	module = "boss_agent_cli.commands.interviews"
	with (
		patch(module + ".AuthManager") as auth,
		patch(module + ".get_platform_instance") as factory,
	):
		auth.return_value.check_status.return_value = {"cookies": {"wt2": "x"}}
		platform = factory.return_value
		platform.__enter__ = lambda self: self
		platform.__exit__ = lambda self, *a: None
		platform.interview_data.return_value = {"code": 0, "zpData": {"interviewList": []}}
		platform.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
		platform.unwrap_data.return_value = {"interviewList": []}
		result = _run(data_dir, "interviews")

	assert result.exit_code == 0, result.output
	action = json.loads(result.stdout)["hints"]["next_actions"][0]
	assert _prefix(action, data_dir)[3:] == ["search", "<query>"]


def test_cities_hint_keeps_directory_and_replays(data_dir):
	result = _run(data_dir, "cities")

	assert result.exit_code == 0, result.output
	action = json.loads(result.stdout)["hints"]["next_actions"][0]
	parts = _prefix(action, data_dir)
	assert parts[3:] == ["search", "<query>", "--city", "<城市名>"]
	# 占位符换成真实参数后补 --help：命令能解析且不触网。
	replay = [part.replace("<query>", "demo").replace("<城市名>", "北京") for part in parts[1:]]
	replayed = CliRunner().invoke(cli, ["--json", *replay, "--help"])
	assert replayed.exit_code == 0, replayed.output


def test_platforms_next_actions_keep_directory_and_replay(data_dir):
	result = _run(data_dir, "platforms")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert [shlex.split(action) for action in actions] == [
		["boss", "--platform", "zhipin", "--data-dir", str(data_dir.resolve()), "status"],
		["boss", "--data-dir", str(data_dir.resolve()), "schema"],
	]
	# 重放 status：只读原目录里的登录态，未登录时登录恢复提示必须指回同一目录。
	replayed = CliRunner().invoke(cli, ["--json", *shlex.split(actions[0])[1:]])
	assert replayed.exit_code == 1, replayed.output
	parsed = json.loads(replayed.stdout)
	assert parsed["error"]["code"] == "AUTH_REQUIRED"
	assert _prefix(parsed["hints"]["next_actions"][0], data_dir)[3:] == ["login"]
