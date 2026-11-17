"""wizard 的 crawl 动作对确定性异常必须与 CLI 同口径（Issue #1286 / #1287）。

- 本地 crawl 记录写坏：`CACHE_RECORD_INVALID`，不可重试，不能兜成 NETWORK_ERROR；
- 导出失败：`LOCAL_IO_ERROR`，不可重试且 `replay_safe=false`（采集请求可能已发出）。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.main import cli


def _invoke_wizard(tmp_path, goal: str, inputs: dict) -> tuple[int, dict]:
	payload = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": goal,
		"inputs": inputs,
	}
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "wizard", "--input-json", json.dumps(payload)],
	)
	return result.exit_code, json.loads(result.stdout)


def _seed_corrupt_run(tmp_path) -> None:
	params = {
		"query": 5,
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run-1", params, str(tmp_path / "crawl" / "run-1"), status="running")
	# crawl_resume 的登录检查早于记录读取：没有登录态会先报 AUTH_REQUIRED。
	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent"}
	)


def test_wizard_crawl_status_reports_corrupt_record(tmp_path):
	_seed_corrupt_run(tmp_path)

	exit_code, payload = _invoke_wizard(tmp_path, "crawl_status", {"run_id": "run-1"})

	assert exit_code == 1, payload
	error = payload["error"]
	assert error["code"] == "CACHE_RECORD_INVALID"
	# 登记表：可恢复，但恢复方式是重建任务，不是重试同一条坏记录。
	assert error["recoverable"] is True
	assert "重新创建任务" in error["recovery_action"]
	assert "NETWORK_ERROR" not in json.dumps(payload, ensure_ascii=False)


def test_wizard_crawl_resume_reports_corrupt_record(tmp_path):
	_seed_corrupt_run(tmp_path)

	exit_code, payload = _invoke_wizard(tmp_path, "crawl_resume", {"run_id": "run-1"})

	assert exit_code == 1, payload
	error = payload["error"]
	assert error["code"] == "CACHE_RECORD_INVALID"
	assert "重新创建任务" in error["recovery_action"]


def test_wizard_crawl_resume_reports_export_failure_as_local_io(tmp_path, monkeypatch):
	class _Boom:
		def resume(self, *args, **kwargs):
			raise CrawlExportError("Permission denied")

	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions._crawl_service",
		lambda context, cache, **_: _Boom(),
	)

	exit_code, payload = _invoke_wizard(tmp_path, "crawl_resume", {"run_id": "run-1"})

	assert exit_code == 1, payload
	error = payload["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
	assert "输出目录可写" in error["recovery_action"]
	assert "NETWORK_ERROR" not in json.dumps(payload, ensure_ascii=False)

	from boss_agent_cli.wizard.actions import _crawl_export_error

	# 采集请求可能已经发出：恢复流程不得自动重跑该步骤。
	assert _crawl_export_error(CrawlExportError("x")).replay_safe is False


def test_wizard_crawl_start_reports_export_failure_as_local_io(tmp_path, monkeypatch):
	class _Boom:
		def create_and_run(self, settings):
			raise CrawlExportError("No space left on device")

	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions._crawl_service",
		lambda context, cache, **_: _Boom(),
	)

	result = CliRunner().invoke(
		cli,
		[
			"--json", "--data-dir", str(tmp_path), "wizard", "--input-json",
			json.dumps({
				"role": "candidate",
				"platform": "zhipin",
				"goal": "crawl_start",
				"inputs": {"query": "AI", "city": "杭州"},
			}),
		],
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	error = payload["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
	assert "输出目录可写" in error["recovery_action"]
