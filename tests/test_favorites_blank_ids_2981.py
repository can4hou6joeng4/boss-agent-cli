"""纯空白收藏标识不得写入候选池或覆盖既有访问标识（Issue #2981）。"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


@pytest.mark.parametrize("field", ["security_id", "job_id"])
@pytest.mark.parametrize("blank", [" ", "\t", "\n", "　"])
def test_batch_import_skips_whitespace_identifiers(tmp_path: Path, field: str, blank: str) -> None:
	invalid = {"security_id": "sec_invalid", "job_id": "job_invalid", field: blank}
	valid = {"security_id": "sec_demo", "job_id": "job_demo"}
	with CacheStore(tmp_path / "cache.db") as cache:
		assert cache.add_shortlist_batch([invalid, valid]) == {
			"imported_count": 1, "existing_count": 0, "skipped_count": 1,
		}
		assert [row["job_id"] for row in cache.list_shortlist()] == ["job_demo"]


def test_blank_security_id_does_not_replace_existing_favorite(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache.db") as cache:
		cache.add_shortlist_batch([{
			"security_id": "sec_original", "job_id": "job_demo", "lid": "lid_original",
			"tags": ["示例标签"], "note": "示例备注",
		}])
		before = cache.list_shortlist()
		assert cache.add_shortlist_batch([{
			"security_id": " \t", "job_id": "job_demo", "lid": "lid_invalid",
		}]) == {"imported_count": 0, "existing_count": 0, "skipped_count": 1}
		assert cache.list_shortlist() == before


def test_favorites_sync_counts_blank_ids_as_skipped(tmp_path: Path) -> None:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	platform.job_favorites.return_value = {"zpData": {"cardList": [
		{"securityId": " ", "encryptJobId": "job_bad", "jobValidStatus": 1},
		{"securityId": "sec_bad", "encryptJobId": "\t ", "jobValidStatus": 1},
		{"securityId": "sec_demo", "encryptJobId": "job_demo", "jobValidStatus": 1},
	], "hasMore": False}}
	with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
		"boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform,
	):
		result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "favorites", "sync"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"]["imported_count"] == 1
	assert payload["data"]["skipped_count"] == 2
	assert payload["data"]["active_count"] == 3
	assert platform.job_favorites.call_count == 1
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert [row["job_id"] for row in cache.list_shortlist()] == ["job_demo"]


def test_nonblank_string_identifiers_keep_normal_refresh(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache.db") as cache:
		cache.add_shortlist_batch([{"security_id": "7", "job_id": "1"}])
		assert cache.add_shortlist_batch([{"security_id": "8", "job_id": "1"}]) == {
			"imported_count": 0, "existing_count": 1, "skipped_count": 0,
		}
		assert cache.list_shortlist()[0]["security_id"] == "8"
