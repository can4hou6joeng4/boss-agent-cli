import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli


class _FakeAIService:
	def __init__(self, payload: dict) -> None:
		self.payload = payload
		self.messages: list[list[dict]] = []

	def chat(self, messages: list[dict]) -> str:
		self.messages.append(messages)
		return json.dumps(self.payload, ensure_ascii=False)


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=True,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="screenshot-full",
	)


def _job(job_id: str = "job-1") -> dict:
	return {
		"job_id": job_id,
		"security_id": f"sec-{job_id}",
		"title": f"职位 {job_id}",
		"company": "测试公司",
		"city": "杭州",
		"salary": "20-30K",
		"post_description": "Python、LLM、工程化",
	}


def _init_resume(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "test-resume", "--template", "default"],
	)
	assert result.exit_code == 0, result.output


def _enable_research(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _complete_run(tmp_path: Path, run_id: str = "run-1") -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run(run_id, _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / run_id))
		cache.put_crawl_job(run_id, "job-1", 1, _job(), detail_done=True)
		cache.put_job_desc("job-1", "Python、LLM、工程化")
		cache.update_crawl_run(run_id, status="completed", next_page=2, list_finished=True)


def test_agent_crawl_completed_run_imports_then_ranks_only_that_run(tmp_path: Path, monkeypatch) -> None:
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_complete_run(tmp_path)
	fake_ai = _FakeAIService({
		"results": [
			{"selector": "csel_placeholder", "title": "职位 job-1", "match_score": 91, "gaps": [], "keyword_hits": ["Python"], "recommendation": "优先"},
		],
	})
	monkeypatch.setattr("boss_agent_cli.commands.agent._create_ai_service", lambda ctx: fake_ai)

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "crawl", "--run-id", "run-1", "--resume", "test-resume"],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "agent.crawl"
	assert payload["data"]["shortlist"]["imported_count"] == 1
	assert payload["data"]["results"][0]["match_score"] == 91
	assert payload["data"]["results"][0]["selector"].startswith("csel_")
	assert "job_id" not in payload["data"]["results"][0]
	assert payload["data"]["summary"] == {"analyzed": 1, "missing_details": 0, "returned": 1}
	assert len(fake_ai.messages) == 1
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.list_shortlist()[0]["source"] == "crawl:run-1"


def test_agent_crawl_requires_explicit_permission_for_a_new_query(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json", "agent", "crawl",
			"--query", "AI", "--city", "杭州", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CRAWL_PERMISSION_REQUIRED"


def test_agent_crawl_allow_crawl_runs_then_continues_the_local_pipeline(tmp_path: Path, monkeypatch) -> None:
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_enable_research(runner, tmp_path)

	def fake_create_and_run(service, settings):
		service._cache.create_crawl_run("new-run", settings.as_dict(), str(tmp_path / "crawl" / "runs" / "new-run"))
		service._cache.put_crawl_job("new-run", "job-1", 1, _job(), detail_done=True)
		service._cache.put_job_desc("job-1", "Python、LLM、工程化")
		service._cache.update_crawl_run("new-run", status="completed", next_page=2, list_finished=True)
		return SimpleNamespace(run_id="new-run")

	monkeypatch.setattr("boss_agent_cli.commands.agent.CrawlService.create_and_run", fake_create_and_run)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent._create_ai_service",
		lambda ctx: _FakeAIService({"results": [{"selector": "csel_placeholder", "match_score": 80}]}),
	)

	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json", "agent", "crawl",
			"--query", "AI", "--city", "杭州", "--allow-crawl", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)["data"]
	assert payload["run_id"] == "new-run"
	assert payload["shortlist"]["imported_count"] == 1
	assert payload["results"][0]["match_score"] == 80


def test_agent_crawl_does_not_process_risk_stopped_runs(tmp_path: Path, monkeypatch) -> None:
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_complete_run(tmp_path)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.update_crawl_run("run-1", status="risk_stopped", next_page=2, error="code=37")
	monkeypatch.setattr("boss_agent_cli.commands.agent._create_ai_service", lambda ctx: (_ for _ in ()).throw(AssertionError("AI must not run")))

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "crawl", "--run-id", "run-1", "--resume", "test-resume"],
	)
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CRAWL_NOT_COMPLETED"
	assert payload["error"]["recovery_action"] == "boss crawl resume run-1"
