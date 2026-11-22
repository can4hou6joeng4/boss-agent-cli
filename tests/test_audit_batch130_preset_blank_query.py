"""审计批次 130：搜索路径拒绝存量 / 输入的空白关键词（Issue #2513、#2514）。

命令行参数的空白关键词会在合并预设之前归一为 None，而预设记录里的 ``"   "``
是真值，能绕过 ``if not query``，被平台当成无关键词搜索并返回 ok: true。
``watch run`` 的读取侧校验（Issue #2511）已拒绝同一记录，搜索路径补齐；
向导的 ``candidate_search`` 动作同样在入口拒绝纯空白关键词。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import SearchPipelineResult
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


def _hinted(tmp_path: Path, *parts: str) -> str:
	return " ".join(["boss --data-dir", str(tmp_path.resolve()), *parts])


def _seed(tmp_path: Path, name: str, params: dict[str, object]) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)


def _platform_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def test_search_rejects_preset_with_blank_query(tmp_path: Path) -> None:
	_seed(tmp_path, "blank", {"query": "   "})

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "blank"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert "关键词不能为空" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == [
		_hinted(tmp_path, "preset remove blank"),
		_hinted(tmp_path, "preset add blank <关键词>"),
	]


def test_search_explicit_query_overrides_blank_stored_query(tmp_path: Path) -> None:
	_seed(tmp_path, "blank", {"query": "   "})

	with patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline, \
		patch("boss_agent_cli.commands.search.AuthManager"), \
		patch("boss_agent_cli.commands.search.get_platform_instance") as mock_platform_cls:
		_platform_mock(mock_platform_cls)
		mock_pipeline.return_value = SearchPipelineResult()
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "search", "golang", "--preset", "blank"]
		)

	assert result.exit_code == 0, result.output
	assert mock_pipeline.call_args.kwargs["criteria"].query == "golang"


def test_search_url_query_overrides_blank_stored_query(tmp_path: Path) -> None:
	_seed(tmp_path, "blank", {"query": "   "})

	with patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline, \
		patch("boss_agent_cli.commands.search.AuthManager"), \
		patch("boss_agent_cli.commands.search.get_platform_instance") as mock_platform_cls:
		_platform_mock(mock_platform_cls)
		mock_pipeline.return_value = SearchPipelineResult()
		result = CliRunner().invoke(
			cli,
			[
				"--data-dir", str(tmp_path), "--json", "search", "--preset", "blank",
				"--url", "https://www.zhipin.com/web/geek/job?query=Golang&city=101280100",
			],
		)

	assert result.exit_code == 0, result.output
	assert mock_pipeline.call_args.kwargs["criteria"].query == "Golang"


def test_search_url_without_query_still_runs(tmp_path: Path) -> None:
	"""--url 自带筛选参数时按链接搜索：预设的空白关键词按缺失处理，不再报失效。"""
	_seed(tmp_path, "blank", {"query": "   "})

	with patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline, \
		patch("boss_agent_cli.commands.search.AuthManager"), \
		patch("boss_agent_cli.commands.search.get_platform_instance") as mock_platform_cls:
		_platform_mock(mock_platform_cls)
		mock_pipeline.return_value = SearchPipelineResult()
		result = CliRunner().invoke(
			cli,
			[
				"--data-dir", str(tmp_path), "--json", "search", "--preset", "blank",
				"--url", "https://www.zhipin.com/web/geek/job?city=101280100",
			],
		)

	assert result.exit_code == 0, result.output
	assert mock_pipeline.call_args.kwargs["criteria"].query == ""


def test_wizard_search_rejects_blank_query(tmp_path: Path) -> None:
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_search"](context, {"query": "   "}, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert "query" in str(exc_info.value)
