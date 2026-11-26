"""收藏重复页证据与历史末页指引回归（Issue #2954、#2955）。"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands.favorites import FavoritesPageLimitExceeded, collect_favorites_items
from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE


def _platform() -> MagicMock:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	return platform


def _page(job_id: object, *, has_more: bool, title: str = "示例职位") -> dict:
	return {"zpData": {
		"cardList": [{"encryptJobId": job_id, "jobName": title, "jobValidStatus": 1}],
		"hasMore": has_more,
	}}


@pytest.mark.parametrize("invalid_id", [True, 7, {"key": "value"}, ["value"], "   ", None])
def test_favorites_sync_invalid_ids_do_not_hide_later_valid_page(tmp_path, invalid_id):
	platform = _platform()
	platform.job_favorites.side_effect = [
		_page(invalid_id, has_more=True, title="第一页"),
		_page(invalid_id, has_more=True, title="第二页"),
		_page("job_fixture", has_more=False, title="后续有效职位"),
	]
	with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
		"boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform,
	), patch("boss_agent_cli.commands.favorites.CacheStore") as cache_cls:
		cache = cache_cls.return_value.__enter__.return_value
		cache.add_shortlist_batch.return_value = {
			"imported_count": 1, "existing_count": 0, "skipped_count": 2,
		}
		result = CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "favorites", "sync"],
		)
		assert result.exit_code == 0, result.output
		payload = json.loads(result.stdout)
		assert payload["ok"] is True
		assert [call.kwargs["page"] for call in platform.job_favorites.call_args_list] == [1, 2, 3]
		items = cache.add_shortlist_batch.call_args.args[0]
		assert [item["title"] for item in items] == ["第一页", "第二页", "后续有效职位"]
		assert items[-1]["job_id"] == "job_fixture"


@pytest.mark.parametrize("job_id", ["job_fixture", "True", "7"])
def test_favorites_sync_valid_string_ids_still_stop_repeated_pages(job_id):
	platform = _platform()
	platform.job_favorites.side_effect = [
		_page(job_id, has_more=True),
		_page(job_id, has_more=True),
	]
	items, error = collect_favorites_items(platform)
	assert error is None
	assert len(items) == 1
	assert platform.job_favorites.call_count == 2


def test_favorites_invalid_ids_still_respect_page_budget():
	platform = _platform()
	platform.job_favorites.side_effect = [
		_page(True, has_more=True), _page(True, has_more=True),
	]
	with pytest.raises(FavoritesPageLimitExceeded):
		collect_favorites_items(platform, max_pages=2)
	assert platform.job_favorites.call_count == 2


@pytest.mark.parametrize("has_more", [False, True, None])
def test_history_max_page_hint_matches_platform_evidence(tmp_path, has_more):
	platform = _platform()
	platform.job_history.return_value = {"zpData": {"jobList": [], "hasMore": has_more}}
	with patch("boss_agent_cli.commands.history.AuthManager"), patch(
		"boss_agent_cli.commands.history.get_platform_instance", return_value=platform,
	):
		result = CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "history", "--page", str(MAX_TRUSTED_PAGE)],
		)
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["has_more"] is has_more
	actions = payload["hints"].get("operator_actions", [])
	assert any("平台仍报告有更多记录" in action for action in actions) is (has_more is True)
	assert any("未提供可靠的下一页标记" in action for action in actions) is (has_more is None)
	assert not any("history --page" in action for action in payload["hints"]["next_actions"])
