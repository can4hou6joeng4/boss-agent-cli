"""审计批次五十四：shortlist 写失败、favorites 形状漂移与 doctor 指引、mark 身份回显。

Issue #2135：`shortlist annotate` 忽略 UPDATE 返回值，BLOB 漂移 / 并发删除时
未写入却报 `updated: true`。
Issue #2136：`favorites list` 在 cardList 元素全部漂移时静默返回空收藏。
Issue #2137：favorites 的 recovery_action 写死裸 `boss doctor`，丢失 --data-dir。
Issue #2138：`mark` 用 str() 回显类型漂移的 securityId。
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = (
		lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	)
	return instance


def _invoke(tmp_path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _seed_shortlist(tmp_path) -> None:
	with patch("boss_agent_cli.commands._platform.get_platform_instance") as platform:
		result = _invoke(
			tmp_path, "shortlist", "add", "sec_001", "job_001",
			"--title", "Go 开发", "--company", "TestCo",
		)
		platform.assert_not_called()
	assert result.exit_code == 0, result.output


# ── #2135：UPDATE 没写进去不能报 updated:true ────────────────────────


def test_annotate_blob_security_id_without_write_reports_error(tmp_path) -> None:
	_seed_shortlist(tmp_path)
	# security_id 列被写成 UTF-8 BLOB：list 读得出文本，但 TEXT 参数匹配不到，
	# UPDATE 的 rowcount 为 0。
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		conn.execute(
			"UPDATE shortlist_records SET security_id = ? WHERE security_id = ?",
			(b"sec_001", "sec_001"),
		)

	with patch("boss_agent_cli.commands._platform.get_platform_instance"):
		result = _invoke(tmp_path, "shortlist", "annotate", "sec_001", "job_001", "--add-tag", "重点")

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SHORTLIST_ENTRY_NOT_FOUND"
	assert parsed["ok"] is False
	assert parsed.get("data") is None


def test_annotate_note_write_failure_reports_error(tmp_path, monkeypatch) -> None:
	_seed_shortlist(tmp_path)
	monkeypatch.setattr(
		"boss_agent_cli.cache.store.CacheStore.set_shortlist_note",
		lambda self, *args: False,
	)

	with patch("boss_agent_cli.commands._platform.get_platform_instance"):
		result = _invoke(tmp_path, "shortlist", "annotate", "sec_001", "job_001", "--note", "等回复")

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SHORTLIST_ENTRY_NOT_FOUND"


def test_annotate_success_still_reports_updated(tmp_path) -> None:
	_seed_shortlist(tmp_path)

	with patch("boss_agent_cli.commands._platform.get_platform_instance"):
		result = _invoke(tmp_path, "shortlist", "annotate", "sec_001", "job_001", "--add-tag", "重点")

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["updated"] is True
	assert parsed["data"]["item"]["tags"] == ["重点"]


# ── #2136 / #2137：favorites list 形状漂移与 doctor 指引 ──────────────


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_all_drifted_elements_reports_network_error(
	mock_auth_cls, mock_platform_cls, tmp_path
) -> None:
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [123, "x", None], "hasMore": False},
	}

	result = _invoke(tmp_path, "favorites", "list")

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	assert "cardList 元素不是对象" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_recovery_action_keeps_data_dir(
	mock_auth_cls, mock_platform_cls, tmp_path
) -> None:
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {"code": 0, "zpData": "boom"}

	result = _invoke(tmp_path, "favorites", "list")

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["recovery_action"] == (
		f"重试；若持续出现，用 boss --data-dir {tmp_path.resolve()} doctor 检查登录态与网络"
	)


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_empty_cardlist_stays_ok(
	mock_auth_cls, mock_platform_cls, tmp_path
) -> None:
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [], "hasMore": False},
	}

	result = _invoke(tmp_path, "favorites", "list")

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"] == []


# ── #2138：mark 不把漂移 securityId 的 repr 当令牌回显 ──────────────


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_blank_security_id_when_drifted(mock_auth_cls, mock_platform_cls, tmp_path) -> None:
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"zpData": {"result": [{"securityId": True, "uid": "1", "name": "候选人A"}]},
	}

	result = _invoke(tmp_path, "mark", "1", "--label", "收藏")

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["security_id"] == ""


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_keeps_valid_security_id(mock_auth_cls, mock_platform_cls, tmp_path) -> None:
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"zpData": {"result": [{"securityId": " sec-9 ", "uid": "1", "name": "候选人A"}]},
	}

	result = _invoke(tmp_path, "mark", "1", "--label", "收藏")

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["security_id"] == "sec-9"
