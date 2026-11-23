"""审计批次 143：列表命令的页码与数量钳制到可信上限（Issue #2550）。

`recommend` / `chatmsg` / `chat-summary` 的数值参数此前没有上限，超长整数
会进入平台参数序列化 / 本地索引写入，撞上 Python 3.11+ 的整数转字符串
限制（4300 位）并被兜成可重试错误。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_COUNT, MAX_TRUSTED_PAGE, clamp_trusted


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


# ── clamp_trusted ───────────────────────────────────────────────────


@pytest.mark.parametrize(
	("value", "expected"),
	[
		(1, 1),
		(2, 2),
		(0, 1),
		(-5, 1),
		(MAX_TRUSTED_PAGE + 1, MAX_TRUSTED_PAGE),
		(int("9" * 4300), MAX_TRUSTED_PAGE),
		(True, 1),
		(False, 1),
		("3", 1),
		(None, 1),
	],
)
def test_clamp_trusted_normalizes_values(value, expected) -> None:
	assert clamp_trusted(value) == expected


def test_clamp_trusted_uses_explicit_limit() -> None:
	assert clamp_trusted(MAX_TRUSTED_COUNT + 1, MAX_TRUSTED_COUNT) == MAX_TRUSTED_COUNT


# ── Click 边界 ──────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.recommend.get_platform_instance")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_rejects_oversized_page(mock_auth_cls, mock_platform_cls, tmp_path) -> None:
	_ctx_mock(mock_platform_cls)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "recommend", "--page", str(MAX_TRUSTED_PAGE + 1)]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	mock_platform_cls.return_value.recommend_jobs.assert_not_called()


def test_recommend_rejects_huge_page_before_request(tmp_path) -> None:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "recommend", "--page", "9" * 4300]
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "INVALID_PARAM"


@pytest.mark.parametrize(
	("args", "flag"),
	[
		(("chatmsg", "uid-1", "--page", str(MAX_TRUSTED_PAGE + 1)), "--page"),
		(("chatmsg", "uid-1", "--count", str(MAX_TRUSTED_COUNT + 1)), "--count"),
		(("chat-summary", "uid-1", "--page", str(MAX_TRUSTED_PAGE + 1)), "--page"),
		(("chat-summary", "uid-1", "--count", str(MAX_TRUSTED_COUNT + 1)), "--count"),
	],
)
def test_message_commands_reject_oversized_numbers(args, flag, tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "1000000" in payload["error"]["message"]
	assert flag in payload["error"]["message"]


def test_chatmsg_rejects_huge_page(tmp_path) -> None:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "chatmsg", "uid-1", "--page", "9" * 4300]
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "INVALID_PARAM"


# ── 正常路径 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("page", "expect_next"), [(2, True), (MAX_TRUSTED_PAGE, False)])
@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.get_platform_instance")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_only_suggests_reachable_pages(
	mock_auth_cls, mock_platform_cls, mock_cache_cls, page, expect_next, tmp_path,
) -> None:
	platform = _ctx_mock(mock_platform_cls)
	platform.is_success.return_value = True
	platform.unwrap_data.return_value = {"hasMore": True, "jobList": []}
	_ctx_mock(mock_cache_cls)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "recommend", "--page", str(page)]
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["page"] == page
	assert payload["pagination"]["has_more"] is True
	next_actions = payload["hints"]["next_actions"]
	assert any(action.endswith(f"recommend --page {page + 1}") for action in next_actions) is expect_next
	platform.recommend_jobs.assert_called_once_with(page=page)
