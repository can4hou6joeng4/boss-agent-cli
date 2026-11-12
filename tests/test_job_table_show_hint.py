"""职位表的编号提示只在本次列表写入了编号索引时出现。"""

import io
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands.watch import _render_watch_run
from boss_agent_cli.index_cache import get_index_info, get_job_by_index, save_index
from boss_agent_cli.main import cli

_SHOW_HINT = "to view details"
_ROW = {
	"security_id": "sid_fixture",
	"job_id": "job_fixture",
	"title": "本次列出的职位",
	"company": "示例公司",
	"salary": "20K",
	"experience": "3-5年",
	"education": "本科",
	"city": "北京",
}


@pytest.fixture
def terminal(monkeypatch):
	stream = io.StringIO()
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
	monkeypatch.setattr(display, "console", Console(file=stream, width=2000, color_system=None))
	return stream


def _platform():
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda raw: raw["zpData"]
	return platform


def test_job_table_omits_show_hint_by_default(terminal):
	display.render_job_table([_ROW], "jobs", hint_next="more: next page")

	output = terminal.getvalue()
	assert "本次列出的职位" in output
	assert "more: next page" in output
	assert _SHOW_HINT not in output
	assert "show" not in output


def test_job_table_prints_show_hint_when_caller_saved_index(terminal):
	display.render_job_table([_ROW], "jobs", show_command="boss show <#>")

	assert f"use: boss show <#> {_SHOW_HINT}" in terminal.getvalue()


def test_history_table_does_not_point_at_previous_search_index(tmp_path, terminal):
	# 先前搜索留下的编号索引里，1 号是另一个职位。
	save_index(tmp_path, [{**_ROW, "security_id": "sid_previous", "title": "先前搜索的职位"}], source="search:旧关键词", page=1)
	platform = _platform()
	platform.job_history.return_value = {"zpData": {"hasMore": False, "jobList": [{
		"encryptJobId": "job_fixture", "securityId": "sid_fixture", "jobName": "本次列出的职位",
	}]}}

	with ExitStack() as stack:
		stack.enter_context(patch("boss_agent_cli.commands.history.AuthManager"))
		stack.enter_context(patch("boss_agent_cli.commands.history.get_platform_instance", return_value=platform))
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "history"])

	assert result.exit_code == 0, result.output
	output = terminal.getvalue()
	assert "本次列出的职位" in output
	# history 不写编号索引：1 号仍是先前搜索的职位，表格不能把编号指向 show。
	assert get_index_info(tmp_path)["source"] == "search:旧关键词"
	assert get_job_by_index(tmp_path, 1)["title"] == "先前搜索的职位"
	assert _SHOW_HINT not in output


def test_batch_greet_dry_run_table_omits_show_hint(terminal):
	display.render_batch_operation_summary({"dry_run": True, "candidates": [_ROW]}, title="batch-greet")

	output = terminal.getvalue()
	assert "本次列出的职位" in output
	assert _SHOW_HINT not in output


def test_watch_run_table_omits_show_hint(terminal):
	_render_watch_run({"name": "fixture", "new_count": 1, "seen_count": 0, "total_count": 1, "new_items": [_ROW]})

	output = terminal.getvalue()
	assert "本次列出的职位" in output
	assert _SHOW_HINT not in output


@pytest.mark.parametrize("command", ["recommend", "search"])
@pytest.mark.parametrize("index_saved", [True, False])
def test_listing_show_hint_follows_index_write(tmp_path, terminal, command, index_saved):
	platform = _platform()
	platform.recommend_jobs.return_value = {"zpData": {"hasMore": False, "jobList": [{
		"encryptJobId": "job_fixture", "securityId": "sid_fixture", "jobName": "本次列出的职位",
	}]}}
	pipeline_result = SimpleNamespace(
		items=[dict(_ROW)],
		has_more=False,
		has_more_reported=False,
		total=1,
		stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
	)

	with ExitStack() as stack:
		auth = stack.enter_context(patch(f"boss_agent_cli.commands.{command}.AuthManager"))
		auth.return_value.account_fingerprint.return_value = "account_fixture"
		stack.enter_context(patch(f"boss_agent_cli.commands.{command}.get_platform_instance", return_value=platform))
		stack.enter_context(patch("boss_agent_cli.commands.search.run_search_pipeline", return_value=pipeline_result))
		if not index_saved:
			stack.enter_context(patch(f"boss_agent_cli.commands.{command}.try_save_index", return_value=False))
		args = ["recommend"] if command == "recommend" else ["search", "Python"]
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *args])

	assert result.exit_code == 0, result.output
	output = terminal.getvalue()
	assert "本次列出的职位" in output
	assert (_SHOW_HINT in output) is index_saved
	assert get_index_info(tmp_path)["exists"] is index_saved
