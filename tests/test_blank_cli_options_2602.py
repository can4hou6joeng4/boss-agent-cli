"""顶层命令显式空选项不得静默退化（Issue #2602 / #2603 / #2604）。"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(args: list[str], tmp_path: Any) -> dict[str, Any]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])
	return json.loads(result.output)


BLANK_BATCH_FILTERS = [
	["--city", ""],
	["--city", "  "],
	["--salary", ""],
	["--experience", "   "],
	["--education", ""],
]


@pytest.mark.parametrize("extra", BLANK_BATCH_FILTERS)
def test_batch_greet_rejects_blank_filter_before_search(tmp_path, extra):
	with patch("boss_agent_cli.commands.greet.get_platform_instance") as platform:
		payload = _invoke(["batch-greet", "Python", "--count", "1", *extra], tmp_path)

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不能为空" in payload["error"]["message"]
	platform.assert_not_called()


def test_batch_greet_omitted_filter_keeps_unfiltered_behavior(tmp_path):
	with patch("boss_agent_cli.commands.greet.AuthManager"), \
			patch("boss_agent_cli.commands.greet.get_platform_instance") as patched:
		client = patched.return_value.__enter__.return_value
		client.is_success.return_value = True
		client.unwrap_data.return_value = {"jobList": []}
		payload = _invoke(["batch-greet", "Python", "--count", "1"], tmp_path)

	assert payload["ok"] is True
	client.search_jobs.assert_called_once()


@pytest.mark.parametrize("message", ["", "  "])
def test_greet_rejects_blank_explicit_message(tmp_path, message):
	with patch("boss_agent_cli.commands.greet.get_platform_instance") as platform:
		payload = _invoke(["greet", "sec_FAKE", "job_FAKE", "--message", message], tmp_path)

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "--message 不能只有空白字符"
	platform.assert_not_called()


def _ctx_mock(mock_cls: MagicMock) -> MagicMock:
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.claim_candidate_action.return_value = None
	return instance


def test_greet_omitted_message_still_sends_default(tmp_path):
	with patch("boss_agent_cli.commands.greet.get_platform_instance") as platform_cls, \
			patch("boss_agent_cli.commands.greet.AuthManager"), \
			patch("boss_agent_cli.commands.greet.CacheStore") as cache_cls:
		cache = _ctx_mock(cache_cls)
		cache.is_greeted.return_value = False
		client = _ctx_mock(platform_cls)
		client.is_success.return_value = True
		client.greet.return_value = {"code": 0, "zpData": {}}
		payload = _invoke(["greet", "sec_FAKE", "job_FAKE"], tmp_path)

	assert payload["ok"] is True, payload["error"]
	client.greet.assert_called_once_with("sec_FAKE", "job_FAKE", "")


def test_agent_crawl_blank_city_names_the_option(tmp_path):
	payload = _invoke(["agent", "crawl", "--query", "AI", "--city", "", "--resume", "demo"], tmp_path)

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "--city 的值不能为空"


def test_agent_crawl_missing_city_keeps_original_message(tmp_path):
	payload = _invoke(["agent", "crawl", "--query", "AI", "--resume", "demo"], tmp_path)

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "使用 --query 时必须提供 --city"
