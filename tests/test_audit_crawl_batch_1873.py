"""crawler 产物完整性与 hooks 口径（Issue #1873 #1874 #1875）。"""

import os
import socket
from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.crawler.operations import crawl_status
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.crawler.hooks import HookInjection


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _job(job_id: str, security_id: str) -> dict:
	return {
		"encryptJobId": job_id,
		"securityId": security_id,
		"jobName": "AI 工程师",
		"salaryDesc": "20-30K",
		"cityName": "杭州",
		"brandName": "测试公司",
		"jobLabels": ["Python", "LLM"],
		"welfareList": ["双休"],
	}


def _page(*jobs: dict, has_more: bool = False, code: int = 0) -> dict:
	return {"code": code, "zpData": {"jobList": list(jobs), "hasMore": has_more}}


class _FakeTransport:
	def __init__(self, pages: dict[int, dict]) -> None:
		self.pages = pages
		self.page_calls: list[int] = []
		self.detail_calls: list[str] = []
		self.hooks: list[HookInjection] = []

	def open(self) -> list[HookInjection]:
		return self.hooks

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		return self.pages[page_no]

	def fetch_detail(self, security_id: str) -> dict:
		self.detail_calls.append(security_id)
		return {"code": 0, "zpData": {"jobCard": {"postDescription": "职位描述", "address": "西湖区"}}}

	def close(self) -> None:
		pass


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		self._cache.put_crawl_budget(f"test:{kind}", 1.0)


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=5,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		max_requests=20,
		max_details=50,
		max_seconds=600,
		max_retries=1,
		operating_mode="research",
	)


def _service(tmp_path: Path, transport: _FakeTransport) -> tuple[CacheStore, CrawlService]:
	cache = CacheStore(tmp_path / "cache.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=lambda store: _NoDelayBudget(store),
	)
	return cache, service


def _run_to_completion(tmp_path: Path) -> tuple[CacheStore, CrawlService, _FakeTransport, str]:
	transport = _FakeTransport({1: _page(_job("job-1", "sec-1"))})
	cache, service = _service(tmp_path, transport)
	outcome = service.create_and_run(_settings(tmp_path))
	assert outcome.status == "completed"
	return cache, service, transport, outcome.run_id


# ── Issue #1873：completed 快速路径不能返回不存在的产物 ──────────────


def test_resume_reexports_missing_artifacts_without_network(tmp_path: Path) -> None:
	cache, service, transport, run_id = _run_to_completion(tmp_path)
	with cache:
		first = service.resume(run_id)
		assert all(Path(path).is_file() for path in first.output_paths.values())
		for path in first.output_paths.values():
			Path(path).unlink()
		page_calls = list(transport.page_calls)

		second = service.resume(run_id)

		assert second.status == "completed"
		assert all(Path(path).is_file() for path in second.output_paths.values())
		assert transport.page_calls == page_calls  # 只用本地缓存重导出，不发平台请求


def test_resume_reexports_when_output_directory_was_removed(tmp_path: Path) -> None:
	cache, service, transport, run_id = _run_to_completion(tmp_path)
	output_dir = Path(service.resume(run_id).output_paths["json"]).parent
	with cache:
		for path in output_dir.iterdir():
			path.unlink()
		output_dir.rmdir()

		outcome = service.resume(run_id)

	assert outcome.status == "completed"
	assert all(Path(path).is_file() for path in outcome.output_paths.values())


def test_completed_fast_path_keeps_existing_artifacts_untouched(tmp_path: Path) -> None:
	cache, service, transport, run_id = _run_to_completion(tmp_path)
	with cache:
		outcome = service.resume(run_id)
		json_path = Path(outcome.output_paths["json"])
		json_path.write_text("marker", encoding="utf-8")

		again = service.resume(run_id)

		assert again.status == "completed"
		assert json_path.read_text(encoding="utf-8") == "marker"


# ── Issue #1874：crawl status 与 resume 同口径校验 hook_results ──────


def test_status_rejects_non_boolean_hook_success(tmp_path: Path) -> None:
	cache, service, transport, run_id = _run_to_completion(tmp_path)
	with cache:
		run = cache.get_crawl_run(run_id)
		assert run is not None
		cache.update_crawl_run(
			run_id,
			status=run["status"],
			next_page=run["next_page"],
			hook_results=[{"name": "x", "success": "false", "sha256": "", "reason": "r"}],
		)
		with pytest.raises(CacheRecordInvalid):
			crawl_status(cache, run_id)


def test_status_reports_boolean_hooks(tmp_path: Path) -> None:
	cache, service, transport, run_id = _run_to_completion(tmp_path)
	with cache:
		run = cache.get_crawl_run(run_id)
		assert run is not None
		cache.update_crawl_run(
			run_id,
			status=run["status"],
			next_page=run["next_page"],
			hook_results=[{"name": "x", "success": False, "sha256": "abc", "reason": "r"}],
		)
		status = crawl_status(cache, run_id)

	assert status["hooks"] == [{"name": "x", "success": False, "sha256": "abc", "reason": "r"}]


# ── Issue #1875：搬运旧产物失败时不残留 .jobs.* 备份 ─────────────────


def test_failed_backup_move_leaves_no_temp_files(tmp_path: Path, monkeypatch) -> None:
	output_dir = tmp_path / "run"
	write_run_outputs(output_dir, [_job("job-1", "sec-1")])
	(jobs_json,) = [output_dir / "jobs.json"]

	real_replace = os.replace

	def failing_replace(source, destination, *args, **kwargs):
		source_path = Path(source)
		if source_path == jobs_json and Path(destination).name.startswith(".jobs.json."):
			raise PermissionError("backup move denied")
		return real_replace(source, destination, *args, **kwargs)

	monkeypatch.setattr("boss_agent_cli.crawler.exporter.os.replace", failing_replace)

	with pytest.raises(CrawlExportError):
		write_run_outputs(output_dir, [_job("job-2", "sec-2")])

	leftovers = sorted(path.name for path in output_dir.iterdir() if path.name.startswith(".jobs."))
	assert leftovers == []
