"""agent crawl 采集职位映射的载荷安全标量（Issue #2833）。"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.agent import _crawl_fit_jobs
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli


class _FakeCache:
	def __init__(self, descs: dict[str, str] | None = None) -> None:
		self._descs = descs or {}

	def get_job_desc(self, job_id: str) -> str | None:
		return self._descs.get(job_id)


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
		hook_profile="none",
	)


def _init_resume(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "test-resume", "--template", "default"],
	)
	assert result.exit_code == 0, result.output


def test_container_fields_fall_into_missing_without_repr() -> None:
	jobs, missing = _crawl_fit_jobs(
		_FakeCache(),
		[
			{
				"selector": "s1",
				"payload": {
					"job_id": "j1",
					"title": {"bad": 1},
					"company": ["x"],
					"city": {"bad": 3},
					"salary": ["20-30K"],
					"post_description": {"bad": 2},
				},
			}
		],
	)

	assert jobs == []
	assert missing == [{"selector": "s1", "title": "", "company": "", "status": "缺详情"}]
	assert "{'" not in json.dumps(missing, ensure_ascii=False)


def test_normal_string_fields_are_preserved() -> None:
	jobs, missing = _crawl_fit_jobs(
		_FakeCache(),
		[
			{
				"selector": "s2",
				"payload": {
					"job_id": "j2",
					"title": "后端工程师",
					"company": "示例公司",
					"city": "北京",
					"salary": "20-30K",
					"post_description": "Python 工程化",
				},
			}
		],
	)

	assert missing == []
	assert jobs == [
		{
			"selector": "s2",
			"title": "后端工程师",
			"company": "示例公司",
			"city": "北京",
			"salary": "20-30K",
			"description": "Python 工程化",
		}
	]


def test_cached_description_is_used_while_display_fields_stay_safe() -> None:
	jobs, missing = _crawl_fit_jobs(
		_FakeCache({"j1": "缓存正文"}),
		[
			{
				"selector": "s1",
				"payload": {"job_id": "j1", "title": {"bad": 1}, "company": ["x"], "post_description": {"bad": 2}},
			}
		],
	)

	assert missing == []
	assert jobs[0]["title"] == ""
	assert jobs[0]["company"] == ""
	assert jobs[0]["description"] == "缓存正文"


def test_agent_crawl_prompt_has_no_python_repr(tmp_path: Path, monkeypatch) -> None:
	runner = CliRunner()
	_init_resume(runner, tmp_path)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run-1", _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / "run-1"))
		cache.put_crawl_job(
			"run-1",
			"job-1",
			1,
			{
				"job_id": "job-1",
				"security_id": "sec-1",
				"title": {"bad": 1},
				"company": ["x"],
				"post_description": {"bad": 2},
			},
			detail_done=False,
		)
		cache.put_crawl_job(
			"run-1",
			"job-2",
			1,
			{
				"job_id": "job-2",
				"security_id": "sec-2",
				"title": "正常职位",
				"company": "示例公司",
				"post_description": "Python 工程化",
			},
			detail_done=True,
		)
		cache.update_crawl_run("run-1", status="completed", next_page=2, list_finished=True)
	fake_ai = _FakeAIService({"results": [{"selector": "csel_placeholder", "match_score": 88}]})
	monkeypatch.setattr("boss_agent_cli.commands.agent._create_ai_service", lambda ctx: fake_ai)

	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "crawl", "--run-id", "run-1", "--resume", "test-resume"],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)["data"]
	assert payload["summary"] == {"analyzed": 1, "missing_details": 1, "returned": 1}
	assert payload["missing"][0]["title"] == ""
	assert payload["missing"][0]["company"] == ""
	assert len(fake_ai.messages) == 1
	prompt = fake_ai.messages[0][-1]["content"]
	assert "{'" not in prompt
	assert '"title": "正常职位"' in prompt
	assert '"description": "Python 工程化"' in prompt
