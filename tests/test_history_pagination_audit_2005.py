"""history 分页与坏元素页截断回归（Issue #2005–#2006）。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE


def _job(security_id: str) -> dict:
	return {
		"encryptJobId": f"job_{security_id}",
		"securityId": security_id,
		"jobName": "职位",
		"brandName": "公司",
		"salaryDesc": "20K",
	}


def _history_platform(has_more: bool = True) -> MagicMock:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.job_history.return_value = {
		"code": 0,
		"zpData": {"jobList": [_job("s1")], "hasMore": has_more},
	}
	platform.is_success.side_effect = lambda raw: raw.get("code", 0) == 0
	platform.unwrap_data.side_effect = lambda raw: raw.get("zpData")
	platform.parse_error.return_value = ("UNKNOWN", "")
	return platform


# ── #2005：--page 钳制 ────────────────────────────────────────────────


def test_history_page_above_trusted_max_is_invalid_param() -> None:
	result = CliRunner().invoke(cli, ["--json", "history", "--page", str(MAX_TRUSTED_PAGE + 1)])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"


def test_history_max_trusted_page_builds_next_hint_without_overflow(tmp_path) -> None:
	with patch("boss_agent_cli.commands.history.AuthManager"), patch(
		"boss_agent_cli.commands.history.get_platform_instance", return_value=_history_platform()
	):
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "history", "--page", str(MAX_TRUSTED_PAGE)],
		)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert any(
		action.endswith(f"history --page {MAX_TRUSTED_PAGE + 1}")
		for action in payload["hints"]["next_actions"]
	), payload["hints"]


# ── #2006：坏元素页翻满上限也必须标记截断 ─────────────────────────────


def _bad_element_platform(pages: int, *, has_more: bool) -> SimpleNamespace:
	return SimpleNamespace(
		friend_list=lambda *, page: {"code": 0, "zpData": {"result": [None, 5], "hasMore": has_more}},
		is_success=lambda raw: raw.get("code", 0) == 0,
		unwrap_data=lambda raw: raw.get("zpData"),
	)


def test_all_bad_element_pages_reaching_limit_are_truncated() -> None:
	items, error, scanned, truncated = collect_friend_list_page(
		_bad_element_platform(3, has_more=True), max_pages=3
	)

	assert items == []
	assert error is None
	assert scanned == 3
	assert truncated is True, "坏元素页翻满上限同样是前缀，不能报完整"


def test_bad_element_pages_with_explicit_end_are_not_truncated() -> None:
	items, error, scanned, truncated = collect_friend_list_page(
		_bad_element_platform(1, has_more=False), max_pages=3
	)

	assert items == []
	assert error is None
	assert scanned == 1
	assert truncated is False


def test_good_pages_reaching_limit_still_truncated() -> None:
	def platform() -> SimpleNamespace:
		return SimpleNamespace(
			friend_list=lambda *, page: {
				"code": 0,
				"zpData": {"result": [{"uid": f"u{page}"}], "hasMore": True},
			},
			is_success=lambda raw: raw.get("code", 0) == 0,
			unwrap_data=lambda raw: raw.get("zpData"),
		)

	items, error, scanned, truncated = collect_friend_list_page(platform(), max_pages=2)

	assert [item["uid"] for item in items] == ["u1", "u2"]
	assert scanned == 2
	assert truncated is True
