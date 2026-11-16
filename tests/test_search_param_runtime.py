"""watch 与 wizard 运行存量搜索参数时必须重新校验（Issue #1338）。"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore, SavedSearchParamsError
from boss_agent_cli.commands.watch import _execute_single_watch
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS, execute_candidate_search
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(tmp_path) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())


def _watch_cache(record: dict):
	cache = MagicMock()
	cache.get_saved_search.return_value = record
	return cache


def _watch_ctx(tmp_path) -> MagicMock:
	ctx = MagicMock()
	ctx.obj = {"data_dir": tmp_path, "logger": Logger(), "platform": "zhipin"}
	return ctx


@pytest.mark.parametrize(
	("params", "needle"),
	[
		({"query": "后端", "city": "北京市"}, "未知城市"),
		({"query": "后端", "salary": "20-50k"}, "薪资"),
		({"query": "后端", "city": 123}, "必须是字符串"),
		({"query": "后端", "welfare": ["双休"]}, "必须是字符串"),
	],
)
def test_watch_runtime_rejects_invalid_stored_params(tmp_path, params, needle):
	with pytest.raises(SavedSearchParamsError) as exc_info:
		_execute_single_watch(_watch_ctx(tmp_path), _watch_cache({"name": "bad", "params": params}), "bad")

	assert needle in str(exc_info.value)


def test_watch_run_all_continues_after_invalid_params(tmp_path):
	records = [{"name": "bad"}, {"name": "good"}]
	cache = MagicMock()
	cache.__enter__.return_value = cache
	cache.list_saved_searches_with_corrupt.return_value = (records, [])

	with (
		patch("boss_agent_cli.commands.watch.CacheStore", return_value=cache),
		patch(
			"boss_agent_cli.commands.watch._execute_single_watch",
			side_effect=[
				SavedSearchParamsError("bad", "未知城市: 北京市"),
				{"name": "good", "new_count": 1, "seen_count": 1, "total_count": 1, "new_items": []},
			],
		),
	):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "--all"]
		)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["data"]["invalid"] == ["bad"]
	assert [item["name"] for item in payload["data"]["watches"]] == ["bad", "good"]
	assert "watch remove" in payload["hints"]["operator_actions"][0]


def test_candidate_search_rejects_unknown_city_before_pipeline():
	def _pipeline_must_not_run(*args, **kwargs):
		raise AssertionError("平台搜索不应在参数校验失败时被调用")

	with pytest.raises(WorkflowActionError) as exc_info:
		execute_candidate_search(
			None,
			None,
			None,
			{"query": "后端", "city": "北京市"},
			pipeline=_pipeline_must_not_run,
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "未知城市" in str(exc_info.value)


def test_candidate_search_rejects_unknown_salary_label_before_pipeline():
	with pytest.raises(WorkflowActionError) as exc_info:
		execute_candidate_search(
			None,
			None,
			None,
			{"query": "后端", "salary": "20-50k"},
			pipeline=lambda *a, **k: pytest.fail("平台搜索不应被调用"),
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "薪资" in str(exc_info.value)


def test_candidate_watch_add_rejects_invalid_filters(tmp_path):
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](
			_context(tmp_path),
			{"action": "add", "name": "daily", "query": "后端", "city": "北京市"},
			{},
		)
	assert exc_info.value.code == "INVALID_PARAM"
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.get_saved_search("daily") is None


def test_candidate_watch_run_reports_saved_search_invalid(tmp_path):
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search("daily", {"query": "后端", "city": "北京市"})

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](
			_context(tmp_path), {"action": "run", "name": "daily"}, {}
		)

	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "watch remove" in exc_info.value.recovery_action
	assert "watch add" in exc_info.value.recovery_action
