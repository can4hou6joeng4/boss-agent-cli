"""审计批次 136：搜索文本的可编码性在写库 / 入口 / 存量读取三处收口（Issue #2528）。

argv 里的非 UTF-8 字节经 surrogateescape 成为孤立代理项后，关键词会一路写到
httpx 组 URL 参数时才抛 ``UnicodeEncodeError``，被全局兜成带磁盘指引的
``LOCAL_IO_ERROR``；``watch run --all`` 的一条坏记录会因此中止整批。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.services.saved_searches import validate_stored_search_params

SURROGATE = "\ud800"


def _payload(result) -> dict:
	return json.loads(result.stdout)


def test_validate_stored_search_params_rejects_unencodable_query() -> None:
	with pytest.raises(ValueError) as exc_info:
		validate_stored_search_params({"query": f"Python{SURROGATE}"})
	assert "query" in str(exc_info.value)
	assert "UTF-8" in str(exc_info.value)


def test_validate_stored_search_params_rejects_unencodable_filter() -> None:
	with pytest.raises(ValueError) as exc_info:
		validate_stored_search_params({"query": "Python", "city": SURROGATE})
	assert "city" in str(exc_info.value)


def test_watch_add_rejects_unencodable_query(tmp_path: Path) -> None:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "w", f"Python{SURROGATE}"]
	)

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM", payload["error"]
	assert "UTF-8" in payload["error"]["message"]


def test_preset_add_rejects_unencodable_query(tmp_path: Path) -> None:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "p", f"Python{SURROGATE}"]
	)

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM", payload["error"]


def test_search_rejects_unencodable_query_before_auth(tmp_path: Path) -> None:
	# 关键词校验在构造 AuthManager / 平台实例之前：坏输入不能先消耗登录检查。
	with patch("boss_agent_cli.commands.search.AuthManager", side_effect=AssertionError("不应构造 AuthManager")):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "search", f"Python{SURROGATE}"]
		)

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM", payload["error"]
	assert "UTF-8" in payload["error"]["message"]


def test_export_rejects_unencodable_query(tmp_path: Path) -> None:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "export", f"Python{SURROGATE}"]
	)

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM", payload["error"]


def test_watch_run_reports_unencodable_stored_query_as_saved_search_invalid(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search("bad", {"query": f"Python{SURROGATE}"})

	with patch("boss_agent_cli.commands.watch.AuthManager", side_effect=AssertionError("不应构造 AuthManager")):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "bad"]
		)

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID", payload["error"]
	hints = payload["hints"]["next_actions"]
	assert any("watch remove" in action for action in hints), hints


def test_search_preset_reports_unencodable_query_as_saved_search_invalid(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search("bad", {"query": f"Python{SURROGATE}"})

	with patch("boss_agent_cli.commands.search.AuthManager", side_effect=AssertionError("不应构造 AuthManager")):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "bad"]
		)

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID", payload["error"]
	hints = payload["hints"]["next_actions"]
	assert any("preset remove" in action for action in hints), hints
	assert any("preset add" in action for action in hints), hints
