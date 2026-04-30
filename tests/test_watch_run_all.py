"""验证 boss watch run --all 聚合执行所有 watch。"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _add_watch(runner: CliRunner, data_dir: Path, name: str, query: str = "Golang") -> None:
	result = runner.invoke(cli, [
		"--data-dir", str(data_dir), "watch", "add", name, query,
		"--city", "广州",
	])
	assert result.exit_code == 0, result.output


def test_watch_run_all_aggregates_all_watches(tmp_path: Path) -> None:
	"""--all 应遍历所有已保存 watch 并聚合输出。"""
	runner = CliRunner()
	_add_watch(runner, tmp_path, "p1")
	_add_watch(runner, tmp_path, "p2")

	# Patch run_search_pipeline 避免真实网络调用
	fake_result = MagicMock()
	fake_result.items = []
	with patch(
		"boss_agent_cli.commands.watch.run_search_pipeline",
		return_value=fake_result,
	), patch(
		"boss_agent_cli.commands.watch.AuthManager"
	) as auth_mock, patch(
		"boss_agent_cli.commands.watch.get_platform_instance"
	) as plat_mock:
		auth_mock.return_value = MagicMock()
		plat_mock.return_value.__enter__ = lambda self: MagicMock()
		plat_mock.return_value.__exit__ = lambda self, *a: None
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "watch", "run", "--all"])

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	assert envelope["data"].get("mode") == "all"
	watches = envelope["data"].get("watches", [])
	names = {w["name"] for w in watches}
	assert names == {"p1", "p2"}


def test_watch_run_all_with_no_watches(tmp_path: Path) -> None:
	"""无 watch 时 --all 应返回空列表，不报错。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "watch", "run", "--all"])
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	assert envelope["data"]["watches"] == []


def test_watch_run_requires_name_or_all(tmp_path: Path) -> None:
	"""不传 name 也不传 --all 应给出明确错误。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "watch", "run"])
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
