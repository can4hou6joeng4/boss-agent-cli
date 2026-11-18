"""crawl 恢复窗口的状态、wizard 输入一致性与损坏结果行提示（Issue #1771 #1772 #1773）。"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WizardInput
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


class _Pages:
	def __init__(self, on_open=None) -> None:
		self.on_open = on_open
		self.fetches: list[tuple[str, str, int]] = []

	def open(self):
		if self.on_open is not None:
			self.on_open()
		return []

	def fetch_page(self, query, city_code, page_no):
		self.fetches.append((query, city_code, page_no))
		return {"code": 0, "zpData": {"jobList": [{
			"encryptJobId": f"job-{page_no}", "securityId": f"fixture-sec-{page_no}",
			"jobName": "测试职位", "brandName": "测试公司",
		}], "hasMore": page_no < 3}}

	def close(self, *, quit_browser=True):
		pass


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _db(tmp_path):
	return tmp_path / "cache" / "boss_agent.db"


def _stopped_run(tmp_path) -> str:
	settings = CrawlSettings(
		query="AI", city_code="101210100", pages=3, with_detail=False,
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9333,
		hook_profile="none", operating_mode="research",
	)
	with CacheStore(_db(tmp_path)) as cache:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda s: _Pages(), budget_factory=_NoDelayBudget)
		run_id = service.create(settings)
		cache.update_crawl_run(run_id, status="stopped", next_page=2, error="stop requested")
		cache._conn.commit()
	return run_id


def _status(tmp_path, run_id) -> str:
	with CacheStore(_db(tmp_path)) as cache:
		return cache.get_crawl_run(run_id)["status"]


@pytest.fixture
def offline_cli(monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.crawl._session_seed", lambda ctx: ({}, ""))
	return CliRunner()


def test_stop_is_accepted_while_a_resumed_run_opens_its_browser(tmp_path, offline_cli, monkeypatch):
	run_id = _stopped_run(tmp_path)
	stops = []

	def on_open():
		stops.append(CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "stop", run_id]))

	transport = _Pages(on_open)
	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", lambda settings, **kwargs: transport)
	monkeypatch.setattr("boss_agent_cli.commands.crawl.CrawlService", lambda cache, **kwargs: CrawlService(
		cache, budget_factory=_NoDelayBudget, **kwargs,
	))

	result = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", run_id])

	assert len(stops) == 1, result.output
	assert stops[0].exit_code == 0, stops[0].output
	assert transport.fetches == [], "浏览器打开期间到达的停止请求必须在抓页前生效"
	assert _status(tmp_path, run_id) == "stopped"


def test_background_resume_persists_queued_before_the_worker_starts(tmp_path, offline_cli, monkeypatch):
	run_id = _stopped_run(tmp_path)
	launched = []
	monkeypatch.setattr("boss_agent_cli.commands.crawl._crawl_preflight", lambda ctx: None)
	monkeypatch.setattr("boss_agent_cli.commands.crawl._launch_background_resume", lambda data_dir, rid, **kw: launched.append(rid))
	argv = ["--data-dir", str(tmp_path), "--json", "crawl", "resume", run_id, "--background"]

	first = offline_cli.invoke(cli, argv)
	assert json.loads(first.stdout)["data"]["status"] == "queued"
	assert _status(tmp_path, run_id) == "queued"

	second = offline_cli.invoke(cli, argv)
	assert json.loads(second.stdout)["data"]["status"] == "queued"
	assert launched == [run_id], "交接窗口内重复恢复不能再拉起一个 worker"

	stop = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "stop", run_id])
	assert stop.exit_code == 0, stop.output


def test_background_resume_launch_failure_does_not_leave_the_run_queued(tmp_path, offline_cli, monkeypatch):
	run_id = _stopped_run(tmp_path)

	def launch(data_dir, rid, **kwargs):
		raise OSError("spawn failed")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._crawl_preflight", lambda ctx: None)
	monkeypatch.setattr("boss_agent_cli.commands.crawl._launch_background_resume", launch)

	result = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", run_id, "--background"])

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "CRAWL_UNAVAILABLE"
	assert _status(tmp_path, run_id) not in ("queued", "running")


def test_corrupt_job_row_hint_names_the_run_not_the_job(tmp_path, offline_cli):
	run_id = _stopped_run(tmp_path)
	with CacheStore(_db(tmp_path)) as cache:
		cache.put_crawl_job(run_id, "jobkey_abc", 1, {"job_key": "jobkey_abc"}, detail_done=False)
		cache._conn.execute("UPDATE crawl_jobs SET detail_done = 'yes' WHERE run_id = ?", (run_id,))
		cache._conn.commit()

	result = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", run_id, "--with-detail"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	actions = payload["hints"]["next_actions"]
	assert any(action.endswith(f"crawl status {run_id}") for action in actions)
	assert not any("jobkey_abc" in action for action in actions)


def _wizard_crawl_start(tmp_path, monkeypatch, inputs):
	transport = _Pages()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_seed_session", lambda ctx: ({}, ""))
	monkeypatch.setattr("boss_agent_cli.wizard.actions.DrissionCrawlerSession", lambda **kwargs: transport)
	monkeypatch.setattr("boss_agent_cli.wizard.actions.CrawlService", lambda cache, **kwargs: CrawlService(
		cache, budget_factory=_NoDelayBudget, **kwargs,
	))
	plan = build_plan(WizardInput.from_mapping({
		"role": "candidate", "platform": "zhipin", "goal": "crawl_start",
		"inputs": inputs, "requested_steps": ["crawl_start"],
	}))
	context = ActionContext(tmp_path, "zhipin", "candidate", Logger("error"))
	with WorkflowStore(tmp_path) as store:
		store.create("wrn-mismatch", plan)
		outcome = WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id="wrn-mismatch")
	return outcome, transport


@pytest.mark.parametrize("override", [{"query": "golang"}, {"city": "上海"}], ids=["query", "city"])
def test_wizard_crawl_start_rejects_run_id_with_different_query_or_city(tmp_path, monkeypatch, override):
	run_id = _stopped_run(tmp_path)

	outcome, transport = _wizard_crawl_start(tmp_path, monkeypatch, {"query": "AI", "city": "杭州", "run_id": run_id, **override})

	assert outcome["status"] == "failed"
	assert outcome["steps"][0]["error"]["code"] == "INVALID_PARAM"
	assert transport.fetches == [], "输入不一致时不能按旧关键词向平台发请求"
	assert _status(tmp_path, run_id) == "stopped"


def test_wizard_crawl_start_resumes_run_id_with_matching_inputs(tmp_path, monkeypatch):
	run_id = _stopped_run(tmp_path)

	outcome, transport = _wizard_crawl_start(tmp_path, monkeypatch, {"query": "AI", "city": "杭州", "run_id": run_id})

	assert outcome["status"] == "completed"
	assert transport.fetches == [("AI", "101210100", 2), ("AI", "101210100", 3)]
