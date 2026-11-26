"""收藏同步的重复页早停必须如实报告不完整（Issue #2979）。"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli.main import cli


def _page(job_id: str, has_more: bool | None) -> dict:
	return {"code": 0, "zpData": {"cardList": [{
		"securityId": f"sec_{job_id}", "encryptJobId": job_id,
		"jobName": "示例职位", "jobValidStatus": 1,
	}], "hasMore": has_more}}


def _invoke(tmp_path, pages: list[dict], *, tty: bool = False):
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.side_effect = lambda response: response["code"] == 0
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	platform.parse_error.return_value = ("RATE_LIMITED", "示例限流")
	platform.job_favorites.side_effect = pages
	output = io.StringIO()
	with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
		"boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform,
	), patch("boss_agent_cli.display.is_json_mode", return_value=not tty), patch(
		"boss_agent_cli.display.console", Console(file=output, width=160),
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "favorites", "sync"])
	return result, platform, output.getvalue()


@pytest.mark.parametrize("has_more", [True, False, None])
def test_repeated_favorites_page_reports_only_explicit_remaining_pages(tmp_path, has_more):
	result, platform, _ = _invoke(tmp_path, [_page("job_demo", True), _page("job_demo", has_more)])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"]["truncated"] is (has_more is True)
	assert payload["data"]["pages_scanned"] == 2
	assert payload["data"]["imported_count"] == 1
	assert payload["data"]["active_count"] == 1
	assert platform.job_favorites.call_count == 2
	if has_more is True:
		assert "不完整" in " ".join(payload["hints"]["warnings"])
		assert "重复页" in " ".join(payload["hints"]["operator_actions"])
		assert "页数上限" not in " ".join(payload["hints"]["warnings"])
	else:
		assert not payload["hints"]["warnings"]


def test_favorites_normal_last_page_remains_complete(tmp_path):
	result, platform, _ = _invoke(tmp_path, [_page("job_first", True), _page("job_last", False)])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"]["truncated"] is False
	assert payload["data"]["pages_scanned"] == 2
	assert payload["data"]["imported_count"] == 2
	assert platform.job_favorites.call_count == 2
	assert not payload["hints"]["warnings"]


def test_favorites_repeat_warning_is_visible_in_terminal(tmp_path):
	result, _, output = _invoke(tmp_path, [_page("job_demo", True), _page("job_demo", True)], tty=True)
	assert result.exit_code == 0, result.output
	assert "重复页" in output and "同步不完整" in output
	assert "页数上限" not in output
	assert result.stdout == ""


def test_favorites_page_failure_does_not_import_prefix(tmp_path):
	result, platform, _ = _invoke(tmp_path, [_page("job_demo", True), {"code": 9}])
	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "RATE_LIMITED"
	assert platform.job_favorites.call_count == 2
	from boss_agent_cli.cache.store import CacheStore
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert not cache.list_shortlist()
