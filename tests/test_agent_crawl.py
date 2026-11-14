import json
import shlex
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def _seed_login(tmp_path: Path, cookies: dict[str, str] | None = None) -> None:
	from boss_agent_cli.auth.token_store import TokenStore

	TokenStore(tmp_path / "auth").save(
		{
			"cookies": {"wt2": "test-wt2", "wbg": "1"} if cookies is None else cookies,
			"user_agent": "test-agent",
			"stoken": "",
		}
	)


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


def test_agent_crawl_new_query_runs_without_permission_gate(tmp_path: Path, monkeypatch) -> None:
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_seed_login(tmp_path)

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
			"--query", "AI", "--city", "杭州", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)["data"]
	assert payload["run_id"] == "new-run"
	assert payload["shortlist"]["imported_count"] == 1
	assert payload["results"][0]["match_score"] == 80


def test_agent_crawl_new_query_seeds_the_crawl_browser_with_the_login_session(tmp_path: Path, monkeypatch) -> None:
	"""采集浏览器的配置目录是隔离的：不注入登录 Cookie 就是登录页，会落成风险停止（Issue #1044）。"""
	sessions: list[dict] = []

	class _Session:
		def __init__(self, **kwargs) -> None:
			sessions.append(kwargs)

		def open(self) -> list:
			return []

		def fetch_page(self, query, city_code, page_no):
			return {"code": 0, "zpData": {"jobList": [], "hasMore": False}}

		def close(self, **kwargs) -> None:
			pass

	monkeypatch.setattr("boss_agent_cli.commands.crawl.DrissionCrawlerSession", _Session)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent._create_ai_service",
		lambda ctx: (_ for _ in ()).throw(AssertionError("没有职位时不该调用 AI")),
	)
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_seed_login(tmp_path)

	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json", "agent", "crawl",
			"--query", "AI", "--city", "杭州", "--pages", "1", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 0, result.output
	assert json.loads(result.output)["data"]["crawl"]["status"] == "completed"
	assert len(sessions) == 1
	assert sessions[0]["seed_cookies"] == {"wt2": "test-wt2", "wbg": "1"}
	assert sessions[0]["user_agent"] == "test-agent"


@pytest.mark.parametrize("cookies", [None, {}, {"wbg": "1"}], ids=["logged-out", "no-cookies", "no-wt2"])
def test_agent_crawl_new_query_requires_login_before_opening_the_browser(tmp_path: Path, monkeypatch, cookies) -> None:
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl.DrissionCrawlerSession",
		lambda **kwargs: (_ for _ in ()).throw(AssertionError("登录态不足时不该构造采集浏览器")),
	)
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	if cookies is not None:
		_seed_login(tmp_path, cookies)

	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json", "agent", "crawl",
			"--query", "AI", "--city", "杭州", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "agent.crawl"
	assert payload["error"]["code"] == "AUTH_REQUIRED"
	assert payload["error"]["recoverable"] is True
	assert shlex.split(payload["error"]["recovery_action"])[:3] == ["boss", "--data-dir", str(tmp_path.resolve())]
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		assert conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 0, "登录态不足不该留下任务"


def test_agent_crawl_reading_a_completed_run_does_not_need_login(tmp_path: Path, monkeypatch) -> None:
	"""--run-id 只读本地结果，不打开浏览器，登录态与它无关。"""
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_complete_run(tmp_path)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent._create_ai_service",
		lambda ctx: _FakeAIService({"results": [{"selector": "csel_placeholder", "match_score": 80}]}),
	)
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "crawl", "--run-id", "run-1", "--resume", "test-resume"],
	)
	assert result.exit_code == 0, result.output


def test_agent_crawl_invalid_city_points_at_crawl_config(tmp_path: Path) -> None:
	"""采集参数校验失败要给配置入口，不能落到默认「修正参数」。"""
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json", "agent", "crawl",
			"--query", "AI", "--city", "火星", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	expected_help = f"boss --data-dir {tmp_path.resolve()} crawl configure --help"
	assert expected_help in payload["error"]["recovery_action"]
	assert payload["hints"]["next_actions"] == [expected_help]


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
	assert "clean --risk-lock" in payload["error"]["recovery_action"]
	assert str(tmp_path.resolve()) in payload["error"]["recovery_action"]
	assert not payload["hints"].get("next_actions")
	resume_action = next(item for item in payload["hints"]["operator_actions"] if "crawl resume run-1" in item)
	assert str(tmp_path.resolve()) in resume_action


def test_agent_crawl_missing_details_hint_quotes_run_id(tmp_path: Path, monkeypatch) -> None:
	"""run_id 是自由文本，拼进 ``boss crawl resume`` 提示前必须按 shell 参数转义。"""
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	run_id = "run; echo pwn"
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run(run_id, _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / run_id))
		cache.update_crawl_run(run_id, status="completed", next_page=2, list_finished=True)
	monkeypatch.setattr("boss_agent_cli.commands.agent._create_ai_service", lambda ctx: (_ for _ in ()).throw(AssertionError("AI must not run")))

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "crawl", "--run-id", run_id, "--resume", "test-resume"],
	)
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert [shlex.split(action) for action in payload["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "crawl", "resume", run_id, "--with-detail"],
	]


def test_agent_crawl_results_hint_quotes_run_id(tmp_path: Path, monkeypatch) -> None:
	"""成功后的 ``boss crawl results <run_id>`` 同样要转义 run_id。"""
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	run_id = "run; echo pwn"
	_complete_run(tmp_path, run_id)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent._create_ai_service",
		lambda ctx: _FakeAIService({"results": [{"selector": "csel_placeholder", "match_score": 80}]}),
	)

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "crawl", "--run-id", run_id, "--resume", "test-resume"],
	)
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert [shlex.split(action) for action in payload["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "crawl", "results", run_id],
		["boss", "--data-dir", str(tmp_path.resolve()), "shortlist", "list"],
	]
	followed = runner.invoke(cli, shlex.split(payload["hints"]["next_actions"][0])[1:])
	assert followed.exit_code == 0, followed.output
	assert json.loads(followed.output)["data"]["run_id"] == run_id


def test_agent_crawl_new_query_without_crawl_dependencies_creates_no_run(tmp_path: Path, monkeypatch) -> None:
	"""默认安装不带 `[crawl]`：不能先建任务再失败（Issue #1050）。"""
	monkeypatch.setitem(sys.modules, "DrissionPage", None)
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	_seed_login(tmp_path)

	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json", "agent", "crawl",
			"--query", "AI", "--city", "杭州", "--resume", "test-resume",
		],
	)
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "agent.crawl"
	assert payload["error"]["code"] == "CRAWL_UNAVAILABLE"
	assert payload["error"]["recoverable"] is True
	assert "boss-agent-cli[crawl]" in payload["error"]["recovery_action"]
	assert "next_actions" not in payload["hints"]
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		assert conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 0, "依赖缺失不该留下任务"
