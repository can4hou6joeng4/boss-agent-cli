"""配置命令的后继提示只操作本次调用的数据目录。"""

import json
import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
	directory = tmp_path / "jobs [bold] team's"
	home = tmp_path / "home"
	home.mkdir()
	# 未显式指定目录时 CLI 用 ~/.boss-agent；把家目录指向临时目录，
	# 重放时既不会碰真实配置，也能看出提示是否落到了默认目录。
	monkeypatch.setenv("HOME", str(home))
	return directory


def _argv(action):
	parts = shlex.split(action)
	assert parts[0] == "boss"
	return parts[1:]


def _replay(runner, action, data_dir):
	args = _argv(action)
	assert args[:2] == ["--data-dir", str(data_dir.resolve())]
	return runner.invoke(cli, ["--json", *args])


def _default_config_path() -> Path:
	return (Path.home() / ".boss-agent" / "config.json").resolve()


def test_config_get_followups_replay_in_original_directory(data_dir):
	runner = CliRunner()
	first = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "config", "get", "log_level"])
	assert first.exit_code == 0, first.output
	actions = json.loads(first.stdout)["hints"]["next_actions"]
	assert [shlex.split(action)[:4] for action in actions] == [
		["boss", "--data-dir", str(data_dir.resolve()), "config"],
		["boss", "--data-dir", str(data_dir.resolve()), "config"],
	]
	# 用真实取值替换占位符后重放：写入的必须是原目录的 config.json。
	set_args = ["debug" if part == "<值>" else part for part in _argv(actions[0])]
	changed = runner.invoke(cli, ["--json", *set_args])
	assert changed.exit_code == 0, changed.output
	assert json.loads(changed.stdout)["data"]["value"] == "debug"
	assert json.loads((data_dir / "config.json").read_text())["log_level"] == "debug"
	assert not _default_config_path().exists(), "config set 不得在默认目录另建配置"
	listed = _replay(runner, actions[1], data_dir)
	assert listed.exit_code == 0, listed.output
	assert json.loads(listed.stdout)["data"]["config_path"] == str(data_dir.resolve() / "config.json")


@pytest.mark.parametrize(
	("args", "key"),
	[
		(["set", "log_level", "bogus"], "log_level"),
		(["set", "cdp_url", "not a url"], "cdp_url"),
		(["get", "bad_key"], "bad_key"),
		(["reset", "bad_key"], "bad_key"),
	],
)
def test_config_recovery_replays_in_original_directory(data_dir, args, key):
	runner = CliRunner()
	failed = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "config", *args])
	assert failed.exit_code == 1, failed.output
	payload = json.loads(failed.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	actions = payload["hints"]["next_actions"]
	assert actions and all("config" in action for action in actions)
	if key != "bad_key":
		assert any(key in action for action in actions)
	for action in actions:
		replayed = _replay(runner, action, data_dir)
		assert replayed.exit_code == 0, replayed.output
		assert json.loads(replayed.stdout)["ok"] is True


@pytest.mark.parametrize("subcommand", ["set", "reset"])
def test_config_set_and_reset_followups_replay_in_original_directory(data_dir, subcommand):
	runner = CliRunner()
	if subcommand == "set":
		args = ["set", "log_level", "info"]
	else:
		(data_dir / "config.json").parent.mkdir(parents=True, exist_ok=True)
		(data_dir / "config.json").write_text('{"log_level": "debug"}', encoding="utf-8")
		args = ["reset", "log_level"]
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "config", *args])
	assert result.exit_code == 0, result.output
	for action in json.loads(result.stdout)["hints"]["next_actions"]:
		assert shlex.split(action)[1:3] == ["--data-dir", str(data_dir.resolve())]
		replayed = _replay(runner, action, data_dir)
		assert replayed.exit_code == 0
		payload = json.loads(replayed.stdout)
		# get 回读当前值，list 回读配置路径：两者都必须来自原目录。
		assert payload["data"].get("config_path", str(data_dir.resolve() / "config.json")) == str(
			data_dir.resolve() / "config.json"
		)
	assert not _default_config_path().exists()


def test_config_list_followups_replay_in_original_directory(data_dir):
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "config", "list"])
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert len(actions) == 3
	for action in actions:
		assert _argv(action)[:3] == ["--data-dir", str(data_dir.resolve()), "config"]
		# get / reset 的提示带的是真实键名，可直接重放；set 的占位符只核对前缀。
		if "<键>" not in action:
			replayed = _replay(runner, action, data_dir)
			assert replayed.exit_code == 0, replayed.output
