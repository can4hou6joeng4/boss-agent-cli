"""history / favorites / stats 的类型漂移与形状漂移（Issue #1855–#1858）。"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.commands.favorites import FavoritesResponseInvalid, collect_favorites_items
from boss_agent_cli.commands.history import _normalize_history_job
from boss_agent_cli.commands.stats import _percent
from boss_agent_cli.main import cli


def _fake_platform(payload: dict) -> MagicMock:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda raw: raw["zpData"]
	platform.job_history.return_value = {"zpData": payload}
	platform.job_favorites.return_value = {"zpData": payload}
	return platform


# ── Issue #1855：history 的 ID / 文本字段不接受容器 ──────────────────


def test_history_item_normalizes_container_fields() -> None:
	raw = {
		"encryptJobId": {"a": 1},
		"securityId": [1, 2],
		"lid": {"x": 1},
		"jobName": {"x": True},
		"brandName": ["c"],
		"skills": [{"k": 1}, "py"],
		"welfareList": [1, "五险"],
		"jobLabels": [None, "急招"],
		"jobType": 3,
	}

	item = JobItem.from_api(_normalize_history_job(raw)).to_dict()

	assert item["job_id"] == ""
	assert item["security_id"] == ""
	assert item["lid"] == ""
	assert item["title"] == ""
	assert item["company"] == ""
	assert item["job_url"] == ""
	assert item["skills"] == ["py"]
	assert item["welfare"] == ["五险"]
	assert item["job_labels"] == ["急招"]
	assert item["raw_job_type"] == 3


def test_history_command_does_not_leak_python_repr(tmp_path: Path) -> None:
	platform = _fake_platform(
		{
			"hasMore": False,
			"jobList": [{"encryptJobId": {"a": 1}, "securityId": [1, 2], "jobName": {"x": True}}],
		}
	)

	with patch("boss_agent_cli.commands.history.AuthManager"), patch(
		"boss_agent_cli.commands.history.get_platform_instance", return_value=platform
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "history"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"][0]["job_id"] == ""
	assert payload["data"][0]["security_id"] == ""
	assert "{'" not in result.output


# ── Issue #1856：cardList 假值非列表不能吞成空列表 ────────────────────


@pytest.mark.parametrize("card_list", [{}, "", 0, False])
def test_collect_favorites_rejects_falsy_non_list(card_list: object) -> None:
	with pytest.raises(FavoritesResponseInvalid):
		collect_favorites_items(_fake_platform({"cardList": card_list, "hasMore": False}))


@pytest.mark.parametrize("card_list", [None, []])
def test_collect_favorites_accepts_missing_or_empty_list(card_list: object) -> None:
	items, error = collect_favorites_items(_fake_platform({"cardList": card_list, "hasMore": False}))

	assert items == []
	assert error is None


def test_favorites_list_rejects_falsy_non_list(tmp_path: Path) -> None:
	platform = _fake_platform({"cardList": {}, "hasMore": False})

	with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
		"boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "list"])

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "NETWORK_ERROR"


# ── Issue #1857：页内全为非对象元素不能翻满上限 ──────────────────────


def test_collect_favorites_rejects_page_without_objects() -> None:
	with pytest.raises(FavoritesResponseInvalid):
		collect_favorites_items(_fake_platform({"cardList": ["junk"]}))


def test_favorites_sync_reports_shape_error_not_page_limit(tmp_path: Path) -> None:
	platform = _fake_platform({"cardList": ["junk"]})

	with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
		"boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])

	assert result.exit_code == 1, result.output
	error = json.loads(result.output)["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["code"] != "RESULT_LIMIT_REACHED"


# ── Issue #1858：_percent 对合法大整数返回占位符 ─────────────────────


def test_percent_handles_huge_integers() -> None:
	assert _percent(10**400) == "-"
	assert _percent(0.5) == "50.0%"
	assert _percent("bad") == "-"
