"""watch 本地记录损坏与 history 上限页提示（Issue #2011–#2012）。"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _corrupt_timestamps(data_dir) -> None:
	conn = sqlite3.connect(data_dir / "cache" / "boss_agent.db")
	conn.execute("UPDATE saved_searches SET created_at = 'oops'")
	conn.commit()
	conn.close()


# ── #2011：时间戳列损坏不能兜成可重试的 NETWORK_ERROR ────────────────


def test_watch_run_reports_local_record_corruption_as_saved_search_invalid(tmp_path) -> None:
	runner = CliRunner()
	added = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "broken", "golang"])
	assert added.exit_code == 0, added.output
	_corrupt_timestamps(tmp_path)

	with patch("boss_agent_cli.commands.watch.AuthManager"), patch(
		"boss_agent_cli.commands.watch.get_platform_instance", return_value=MagicMock()
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "broken"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID", payload["error"]
	# 可恢复 = 删除重建后能继续，而不是「重试同一条记录」。
	assert payload["error"]["recoverable"] is True
	next_actions = payload["hints"]["next_actions"]
	assert any("watch remove" in action for action in next_actions), next_actions
	assert any("watch add" in action for action in next_actions), next_actions


def test_watch_run_missing_record_still_reports_not_found(tmp_path) -> None:
	with patch("boss_agent_cli.commands.watch.AuthManager"), patch(
		"boss_agent_cli.commands.watch.get_platform_instance", return_value=MagicMock()
	):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "ghost"]
		)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "SAVED_SEARCH_NOT_FOUND"
