"""批次二十五回归测试：Issue #1644–#1650。

全部测试离线运行；不调用平台 API、浏览器或网络。
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.exporter import _xlsx_cell
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _location_platform(kind: str) -> MagicMock:
	platform = MagicMock()
	real = BossRecruiterPlatform(MagicMock())
	platform.is_success.side_effect = real.is_success
	platform.parse_error.side_effect = real.parse_error
	platform.friend_detail.return_value = {
		"code": 0,
		"zpData": {"friendList": [{"uid": 10001, "securityId": "sec", "jobId": 5566}]},
	}
	if kind == "raise":
		platform.send_job_location.side_effect = AccountRiskError("risk")
	elif kind == "response":
		platform.send_job_location.return_value = {"code": 32, "message": "risk"}
	else:
		platform.send_job_location.return_value = {"code": 0, "zpData": {}}
	return platform


def _run_location(tmp_path: Path, platform: MagicMock):
	with patch("boss_agent_cli.commands.recruiter.location.AuthManager"), patch(
		"boss_agent_cli.commands.recruiter.location.get_recruiter_platform_instance"
	) as factory, patch("boss_agent_cli.commands.recruiter.location.require_compliance_allowed"):
		factory.return_value.__enter__.return_value = platform
		return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "hr", "location", "10001", "--yes"])


@pytest.mark.parametrize("kind", ["raise", "response"])
def test_location_risk_keeps_pending_and_blocks_retry(kind: str) -> None:
	with tempfile.TemporaryDirectory() as directory:
		tmp_path = Path(directory)
		first = _run_location(tmp_path, _location_platform(kind))
		with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
			assert cache.location_send_status(10001) == "pending"
		retry_platform = _location_platform("ok")
		retry = _run_location(tmp_path, retry_platform)
		assert retry.exit_code == 1
		assert json.loads(retry.output)["error"]["code"] == "LOCATION_RESULT_UNKNOWN"
		retry_platform.send_job_location.assert_not_called()
		assert first.exit_code == 1


def test_empty_job_id_claim_does_not_create_global_row(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache.db") as cache:
		assert cache.claim_candidate_action("sid-a", "", "greet", account="acct") == "pending"
		cache.mark_candidate_action_sent("sid-a", "", "greet", account="acct")
		assert cache.claim_candidate_action("sid-b", "", "greet", account="acct") == "pending"


def test_future_cache_timestamp_is_expired(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "boss_agent.db", search_ttl_seconds=1) as cache:
		cache.put_search({"query": "x"}, '{"zpData":{"jobList":["stale"]}}')
		cache.put_job_desc("job-1", "stale description")
		future = time.time() + 10 * 365 * 86400
		cache._conn.execute("UPDATE search_cache SET created_at=?", (future,))
		cache._conn.execute("UPDATE job_desc_cache SET created_at=?", (future,))
		cache._conn.commit()
		assert cache.get_search({"query": "x"}) is None
		assert cache.get_job_desc("job-1") is None


def test_xlsx_cell_converts_unexpected_containers_to_text() -> None:
	assert _xlsx_cell({"unexpected": "object"}) == "{'unexpected': 'object'}"
	assert _xlsx_cell(["a", "b"]) == "['a', 'b']"


def test_favorites_tty_status_column() -> None:
	platform = MagicMock()
	platform.is_success.return_value = True
	platform.unwrap_data.return_value = {
		"cardList": [{
			"securityId": "sec", "encryptJobId": "job", "jobName": "后端工程师",
			"brandName": "示例公司", "cityName": "北京", "jobSalary": "20-30K",
			"jobValidStatus": 1,
		}],
		"hasMore": False,
	}
	with tempfile.TemporaryDirectory() as directory:
		with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
			"boss_agent_cli.commands.favorites.get_platform_instance"
		) as factory, patch("boss_agent_cli.display.is_json_mode", return_value=False):
			factory.return_value.__enter__.return_value = platform
			result = CliRunner().invoke(cli, ["--data-dir", directory, "favorites", "list"])
	assert "active" in result.output


def test_mcp_search_accepts_url_and_preset() -> None:
	tool = next(item for item in build_tool_catalog() if item.name == "boss_search")
	properties = tool.input_schema["properties"]
	assert {"query", "url", "preset"} <= properties.keys()
	assert tool.input_schema["required"] == []
	assert {"query", "url", "preset"} <= {item["required"][0] for item in tool.input_schema["anyOf"]}
	assert _build_args("boss_search", {"url": "https://www.zhipin.com/web/geek/jobs?query=x"}) == [
		"search", "--url", "https://www.zhipin.com/web/geek/jobs?query=x"
	]
	assert _build_args("boss_search", {"query": "x", "preset": "p"}) == ["search", "x", "--preset", "p"]
