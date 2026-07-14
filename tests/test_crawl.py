import json
import sys
import types
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from click.testing import CliRunner
from openpyxl import load_workbook

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.crawler.hooks import (
	HOOK_PROFILES,
	HOOK_SNAPSHOT_FILES,
	HOOK_SNAPSHOT_SHA256,
	HookInjection,
	HookRegistrationError,
	inject_hook_profile,
)
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import JOBLIST_TARGET, CrawlRiskError, DrissionCrawlerSession
from boss_agent_cli.main import cli


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


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		self._cache.put_crawl_budget(f"test:{kind}", 1.0)


class _FakeTransport:
	def __init__(
		self,
		pages: dict[int, dict] | None = None,
		*,
		risk_pages: set[int] | None = None,
		fail_pages: set[int] | None = None,
		detail_payloads: dict[str, dict] | None = None,
	):
		self.pages = pages or {}
		self.risk_pages = risk_pages or set()
		self.fail_pages = fail_pages or set()
		self.detail_payloads = detail_payloads or {}
		self.page_calls: list[int] = []
		self.detail_calls: list[str] = []
		self.closed = False

	def open(self):
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		if page_no in self.risk_pages:
			raise CrawlRiskError("安全页")
		if page_no in self.fail_pages:
			raise TimeoutError("listener timeout")
		return self.pages[page_no]

	def fetch_detail(self, security_id: str) -> dict:
		self.detail_calls.append(security_id)
		return self.detail_payloads.get(
			security_id,
			{"code": 0, "zpData": {"jobCard": {"postDescription": "职位描述", "address": "西湖区"}}},
		)

	def close(self) -> None:
		self.closed = True


class _FakePage:
	def __init__(self) -> None:
		self.calls: list[tuple[str, dict]] = []

	def run_cdp(self, command: str, **kwargs) -> None:
		self.calls.append((command, kwargs))


class _HookFailureTransport(_FakeTransport):
	def open(self):
		raise HookRegistrationError([
			HookInjection(name="Bypass_Debugger", success=True),
			HookInjection(name="Hook_CryptoJS", success=False, reason="CDP disconnected"),
		])


def _settings(tmp_path: Path, *, pages: int = 5, with_detail: bool = False) -> CrawlSettings:
	return CrawlSettings(
		query="AI", city_code="101210100", pages=pages, with_detail=with_detail,
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9222, hook_profile="screenshot-full",
	)


def _service(tmp_path: Path, transport_factory):
	cache = CacheStore(tmp_path / "cache.db")
	return cache, CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=transport_factory,
		budget_factory=lambda store: _NoDelayBudget(store),
	)


def test_screenshot_full_hook_registers_exactly_seven_scripts():
	page = _FakePage()
	results = inject_hook_profile(page, "screenshot-full")
	assert [item.name for item in results] == [name for name, _ in HOOK_PROFILES["screenshot-full"]]
	assert len(results) == 7
	assert all(item.success for item in results)
	assert all(command == "Page.addScriptToEvaluateOnNewDocument" for command, _ in page.calls)


def test_screenshot_full_hook_uses_verbatim_upstream_snapshots():
	snapshot_root = files("boss_agent_cli.crawler").joinpath("hook_scripts")
	assert [name for name, _ in HOOK_SNAPSHOT_FILES] == [name for name, _ in HOOK_PROFILES["screenshot-full"]]
	for _, filename in HOOK_SNAPSHOT_FILES:
		assert sha256(snapshot_root.joinpath(filename).read_bytes()).hexdigest() == HOOK_SNAPSHOT_SHA256[filename]


def test_joblist_listener_target_matches_the_reference_endpoint():
	assert JOBLIST_TARGET == r"wapi/zpgeek/search/joblist\.json"


def test_hook_registration_happens_before_first_navigation(monkeypatch, tmp_path):
	events: list[str] = []

	class _Options:
		def set_browser_path(self, path: str) -> None:
			events.append("browser-path")

		def set_local_port(self, port: int) -> None:
			events.append("port")

		def set_user_data_path(self, path: str) -> None:
			events.append("profile")

	class _Packet:
		response = types.SimpleNamespace(body=_page())

	class _Listener:
		def start(self, *args, **kwargs) -> None:
			events.append("listen")

		def wait(self, **kwargs):
			return _Packet()

	class _Page:
		url = ""
		html = ""

		def __init__(self) -> None:
			self.listen = _Listener()

		def run_cdp(self, command: str, **kwargs) -> None:
			events.append("hook")

		def get(self, url: str) -> None:
			events.append("navigate")

	page = _Page()
	module = types.SimpleNamespace(ChromiumOptions=_Options, ChromiumPage=lambda options: page)
	monkeypatch.setitem(sys.modules, "DrissionPage", module)
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9222, hook_profile="screenshot-full",
	)

	session.open()
	session.fetch_page("AI", "101210100", 1)

	assert events.count("hook") == 7
	assert max(index for index, event in enumerate(events) if event == "hook") < events.index("navigate")


def test_single_hook_failure_stops_before_listener_or_navigation(monkeypatch, tmp_path):
	events: list[str] = []

	class _Options:
		def set_local_port(self, port: int) -> None:
			pass

		def set_user_data_path(self, path: str) -> None:
			pass

	class _Listener:
		def start(self, *args, **kwargs) -> None:
			events.append("listen")

	class _Page:
		listen = _Listener()

		def run_cdp(self, command: str, **kwargs) -> None:
			events.append("hook")
			if len(events) == 3:
				raise RuntimeError("CDP disconnected")

	page = _Page()
	module = types.SimpleNamespace(ChromiumOptions=_Options, ChromiumPage=lambda options: page)
	monkeypatch.setitem(sys.modules, "DrissionPage", module)
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9222, hook_profile="screenshot-full",
	)

	try:
		session.open()
	except HookRegistrationError as exc:
		assert len(exc.injections) == 7
		assert any(not item.success for item in exc.injections)
	else:
		raise AssertionError("expected HookRegistrationError")
	assert "listen" not in events


def test_listener_parses_string_json_response(monkeypatch, tmp_path):
	class _Options:
		def set_local_port(self, port: int) -> None:
			pass

		def set_user_data_path(self, path: str) -> None:
			pass

	class _Packet:
		response = types.SimpleNamespace(body=json.dumps(_page(_job("job-1", "sec-1"))))

	class _Listener:
		def start(self, *args, **kwargs) -> None:
			pass

		def wait(self, **kwargs):
			return _Packet()

	class _Page:
		url = ""
		html = ""
		listen = _Listener()

		def get(self, url: str) -> None:
			pass

	page = _Page()
	module = types.SimpleNamespace(ChromiumOptions=_Options, ChromiumPage=lambda options: page)
	monkeypatch.setitem(sys.modules, "DrissionPage", module)
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9222, hook_profile="none",
	)

	session.open()
	payload = session.fetch_page("AI", "101210100", 1)

	assert payload["zpData"]["jobList"][0]["encryptJobId"] == "job-1"


def test_listener_non_json_response_is_a_risk_stop(monkeypatch, tmp_path):
	class _Options:
		def set_local_port(self, port: int) -> None:
			pass

		def set_user_data_path(self, path: str) -> None:
			pass

	class _Packet:
		response = types.SimpleNamespace(body="<html>verification</html>")

	class _Listener:
		def start(self, *args, **kwargs) -> None:
			pass

		def wait(self, **kwargs):
			return _Packet()

	class _Page:
		url = "https://www.zhipin.com/web/geek/jobs"
		html = "<html>verification</html>"
		listen = _Listener()

		def get(self, url: str) -> None:
			pass

		def ele(self, selector: str, timeout: int):
			return None

	page = _Page()
	module = types.SimpleNamespace(ChromiumOptions=_Options, ChromiumPage=lambda options: page)
	monkeypatch.setitem(sys.modules, "DrissionPage", module)
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9222, hook_profile="none",
	)

	session.open()
	try:
		session.fetch_page("AI", "101210100", 1)
	except CrawlRiskError as exc:
		assert "安全页" in str(exc)
	else:
		raise AssertionError("expected CrawlRiskError")


def test_crawl_writes_all_outputs_and_uses_cached_detail(tmp_path):
	transport = _FakeTransport({1: _page(_job("job-1", "sec-1"))})
	cache, service = _service(tmp_path, lambda settings: transport)
	cache.put_job_desc("job-1", "缓存职位描述")

	outcome = service.create_and_run(_settings(tmp_path, with_detail=True))

	assert outcome.status == "completed"
	assert outcome.jobs_seen == 1
	assert outcome.detail_checks == 1
	assert transport.detail_calls == []
	assert all(Path(path).exists() for path in outcome.output_paths.values())
	rows = json.loads(Path(outcome.output_paths["json"]).read_text(encoding="utf-8"))
	assert rows[0]["post_description"] == "缓存职位描述"
	assert rows[0]["detail_status"] == "cached"
	workbook = load_workbook(outcome.output_paths["xlsx"])
	sheet = workbook["jobs"]
	assert sheet.freeze_panes == "A2"
	assert sheet.auto_filter.ref == "A1:V2"
	assert sheet["R2"].alignment.wrap_text is not True
	assert sheet["R2"].alignment.vertical == "center"
	assert sheet.row_dimensions[2].height == 20
	assert sheet.tables["JobsTable"].tableStyleInfo.name == "TableStyleMedium2"


def test_crawl_pages_zero_follows_has_more(tmp_path):
	transport = _FakeTransport({
		1: _page(_job("job-1", "sec-1"), has_more=True),
		2: _page(_job("job-2", "sec-2"), has_more=False),
	})
	_, service = _service(tmp_path, lambda settings: transport)

	outcome = service.create_and_run(_settings(tmp_path, pages=0))

	assert outcome.status == "completed"
	assert transport.page_calls == [1, 2]
	assert outcome.next_page == 3
	assert outcome.jobs_seen == 2


def test_crawl_risk_code_checkpoint_stops_and_resume_deduplicates(tmp_path):
	first = _FakeTransport({1: _page(_job("job-1", "sec-1"), has_more=True), 2: _page(code=37)})
	second = _FakeTransport({2: _page(_job("job-2", "sec-2"), has_more=False)})
	transports = iter((first, second))
	cache, service = _service(tmp_path, lambda settings: next(transports))

	stopped = service.create_and_run(_settings(tmp_path, pages=0))
	assert stopped.status == "risk_stopped"
	assert stopped.next_page == 2
	assert cache.get_crawl_run(stopped.run_id)["status"] == "risk_stopped"

	resumed = service.resume(stopped.run_id)
	assert resumed.status == "completed"
	assert resumed.jobs_seen == 2
	assert [item["job_key"] for item in cache.list_crawl_jobs(stopped.run_id)] == ["job-1", "job-2"]


def test_detail_risk_checkpoints_pending_queue_and_resume_skips_finished_list(tmp_path):
	first = _FakeTransport(
		{1: _page(_job("job-1", "sec-1"), has_more=False)},
		detail_payloads={"sec-1": {"code": 37, "message": "risk"}},
	)
	second = _FakeTransport({})
	transports = iter((first, second))
	cache, service = _service(tmp_path, lambda settings: next(transports))

	stopped = service.create_and_run(_settings(tmp_path, with_detail=True))
	assert stopped.status == "risk_stopped"
	assert stopped.next_page == 2
	assert cache.get_crawl_job(stopped.run_id, "job-1")["detail_done"] is False
	assert cache.get_crawl_run(stopped.run_id)["list_finished"] is True

	resumed = service.resume(stopped.run_id)
	assert resumed.status == "completed"
	assert second.page_calls == []
	assert second.detail_calls == ["sec-1"]
	assert cache.get_crawl_job(stopped.run_id, "job-1")["detail_done"] is True


def test_crawl_two_transient_failures_checkpoint_stop(tmp_path):
	transport = _FakeTransport(fail_pages={1})
	cache, service = _service(tmp_path, lambda settings: transport)

	outcome = service.create_and_run(_settings(tmp_path))

	assert outcome.status == "stopped"
	assert outcome.next_page == 1
	assert transport.page_calls == [1, 1]
	assert cache.get_crawl_run(outcome.run_id)["error"] == "listener timeout"


def test_hook_failure_is_checkpointed_with_registration_metadata(tmp_path):
	transport = _HookFailureTransport()
	cache, service = _service(tmp_path, lambda settings: transport)

	outcome = service.create_and_run(_settings(tmp_path))

	assert outcome.status == "stopped"
	assert transport.page_calls == []
	assert outcome.hooks[1].reason == "CDP disconnected"
	run = cache.get_crawl_run(outcome.run_id)
	assert run["hook_results"][1] == {"name": "Hook_CryptoJS", "reason": "CDP disconnected", "success": False}
	assert run["params"]["hook_source"]["commit"] == "b74db937a0825c58bfee181ae85e09fce8474467"


def test_completed_run_resume_returns_existing_artifacts_without_reopening_transport(tmp_path):
	transport = _FakeTransport({1: _page(_job("job-1", "sec-1"))})
	factory_calls = 0

	def factory(settings):
		nonlocal factory_calls
		factory_calls += 1
		return transport

	_, service = _service(tmp_path, factory)
	completed = service.create_and_run(_settings(tmp_path))
	resumed = service.resume(completed.run_id)

	assert factory_calls == 1
	assert resumed.status == "completed"
	assert resumed.output_paths == completed.output_paths


def test_completed_run_resume_can_continue_pages_and_backfill_details(tmp_path):
	first = _FakeTransport({1: _page(_job("job-1", "sec-1"), has_more=True)})
	second = _FakeTransport({
		2: _page(_job("job-2", "sec-2"), has_more=True),
		3: _page(_job("job-3", "sec-3"), has_more=False),
	})
	transports = iter((first, second))
	cache, service = _service(tmp_path, lambda settings: next(transports))

	initial = service.create_and_run(_settings(tmp_path, pages=1))
	resumed = service.resume(initial.run_id, pages=3, with_detail=True)

	assert resumed.status == "completed"
	assert resumed.pages_completed == 3
	assert resumed.jobs_seen == 3
	assert first.page_calls == [1]
	assert second.page_calls == [2, 3]
	assert second.detail_calls == ["sec-1", "sec-2", "sec-3"]
	assert all(item["detail_done"] for item in cache.list_crawl_jobs(initial.run_id))


def test_exporter_formats_workbook(tmp_path):
	paths = write_run_outputs(tmp_path, [{"title": "AI", "salary": "25K", "benefits": "双休", "post_description": "描述"}])
	workbook = load_workbook(paths["xlsx"])
	sheet = workbook.active
	assert sheet["A1"].value == "query"
	assert sheet.column_dimensions["R"].width == 54
	assert sheet["R2"].alignment.wrap_text is not True
	assert sheet["R2"].alignment.vertical == "center"
	assert sheet.row_dimensions[2].height == 20
	assert sheet.tables["JobsTable"].tableStyleInfo.name == "TableStyleMedium2"


def test_crawl_configure_and_schema_are_available(tmp_path):
	runner = CliRunner()
	configured = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "crawl", "configure", "--profile", str(tmp_path / "profile"), "--port", "9333"],
	)
	assert configured.exit_code == 0, configured.output
	payload = json.loads(configured.output)
	assert payload["data"]["crawl"]["cdp_port"] == 9333

	schema = runner.invoke(cli, ["--data-dir", str(tmp_path), "schema"])
	assert schema.exit_code == 0
	assert json.loads(schema.output)["data"]["commands"]["crawl"]["options"]["run"]["--pages"]["default"] == 5


def test_crawl_exports_exact_task_shaped_mcp_tools_and_cli_errors_are_structured(tmp_path):
	runner = CliRunner()
	mcp_schema = runner.invoke(cli, ["--data-dir", str(tmp_path), "schema", "--format", "mcp-tools"])
	assert mcp_schema.exit_code == 0, mcp_schema.output
	names = {item["name"] for item in json.loads(mcp_schema.output)["data"]["tools"]}
	assert {"boss_crawl_start", "boss_crawl_status", "boss_crawl_results", "boss_crawl_shortlist", "boss_crawl_resume"} <= names
	assert "boss_crawl" not in names

	invalid_city = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "run", "AI", "--city", "unknown-city"])
	assert invalid_city.exit_code == 1
	assert json.loads(invalid_city.output)["error"]["code"] == "INVALID_PARAM"

	not_found = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", "missing-run"])
	assert not_found.exit_code == 1
	assert json.loads(not_found.output)["error"]["code"] == "JOB_NOT_FOUND"


def test_crawl_shortlist_imports_selected_jobs_without_overwriting_existing_items(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	cache.create_crawl_run("run-1", _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / "run-1"))
	cache.put_crawl_job("run-1", "job-1", 1, _normalize_crawl_job("job-1", "sec-1", "职位一"), detail_done=True)
	cache.put_crawl_job("run-1", "job-2", 1, _normalize_crawl_job("job-2", "sec-2", "职位二"), detail_done=True)
	cache.close()
	runner = CliRunner()

	first = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "crawl", "shortlist", "run-1", "--job-id", "job-1", "--tags", "AI,优先"],
	)
	assert first.exit_code == 0, first.output
	first_data = json.loads(first.output)["data"]
	assert first_data["imported_count"] == 1
	assert first_data["source"] == "crawl:run-1"

	second = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "shortlist", "run-1", "--all"])
	assert second.exit_code == 0, second.output
	second_data = json.loads(second.output)["data"]
	assert second_data["imported_count"] == 1
	assert second_data["existing_count"] == 1

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as verified:
		items = verified.list_shortlist()
	assert [item["job_id"] for item in items] == ["job-2", "job-1"]
	assert next(item for item in items if item["job_id"] == "job-1")["tags"] == ["AI", "优先"]


def test_crawl_shortlist_requires_an_explicit_selection(tmp_path):
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "shortlist", "missing-run"])
	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_crawl_status_and_results_only_read_persisted_task_state(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	cache.create_crawl_run("run-status", _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / "run-status"))
	cache.put_crawl_job("run-status", "job-1", 1, _normalize_crawl_job("job-1", "sec-1", "职位一"), detail_done=True)
	cache.put_crawl_job("run-status", "job-2", 2, _normalize_crawl_job("job-2", "sec-2", "职位二"), detail_done=False)
	cache.update_crawl_run("run-status", status="risk_stopped", next_page=2, error="code=37", list_finished=False)
	cache.close()
	runner = CliRunner()

	status = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", "run-status"])
	assert status.exit_code == 0, status.output
	status_data = json.loads(status.output)["data"]
	assert status_data["status"] == "risk_stopped"
	assert status_data["jobs_seen"] == 2
	assert status_data["details_completed"] == 1
	assert status_data["details_pending"] == 1
	assert status_data["checkpoint"]["resume_command"] == "boss crawl resume run-status"

	results = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "crawl", "results", "run-status", "--page", "2", "--detail-status", "pending"],
	)
	assert results.exit_code == 0, results.output
	rows = json.loads(results.output)["data"]["jobs"]
	assert [row["job_id"] for row in rows] == ["job-2"]
	assert rows[0]["detail_done"] is False


def test_crawl_start_creates_a_run_then_launches_background_resume(tmp_path, monkeypatch):
	launched: list[tuple[Path, str]] = []

	def fake_launch(data_dir: Path, run_id: str, **kwargs) -> None:
		launched.append((data_dir, run_id))

	monkeypatch.setattr("boss_agent_cli.commands.crawl._launch_background_resume", fake_launch)
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "start", "AI", "--city", "杭州"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)["data"]
	assert payload["background"] is True
	assert launched == [(tmp_path, payload["run_id"])]
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.get_crawl_run(payload["run_id"])["status"] == "queued"


def test_background_resume_does_not_duplicate_a_queued_or_running_task(tmp_path, monkeypatch):
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run-queued", _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / "run-queued"), status="queued")
		cache.create_crawl_run("run-running", _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / "run-running"), status="running")
	launched: list[str] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append(run_id),
	)
	runner = CliRunner()
	for run_id, expected_status in (("run-queued", "queued"), ("run-running", "running")):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", run_id, "--background"])
		assert result.exit_code == 0, result.output
		assert json.loads(result.output)["data"]["status"] == expected_status
	assert launched == []


def _normalize_crawl_job(job_id: str, security_id: str, title: str) -> dict:
	return {
		"job_id": job_id,
		"security_id": security_id,
		"title": title,
		"company": "测试公司",
		"city": "杭州",
		"salary": "20-30K",
	}
