"""Tests for boss ai fit."""

import json
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


class FakeAIService:
	def __init__(self, payload: dict[str, Any]) -> None:
		self.payload = payload
		self.messages: list[dict[str, str]] = []

	def chat(self, messages: list[dict[str, str]], *, temperature: float | None = None, max_tokens: int | None = None) -> str:
		self.messages = messages
		return json.dumps(self.payload, ensure_ascii=False)


def _invoke(runner: CliRunner, tmp_path, args: list[str]):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai"] + args)


def _setup_resume(tmp_path) -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"resume",
			"init",
			"--name",
			"test-resume",
			"--template",
			"default",
		],
	)
	assert result.exit_code == 0, result.output


def _seed_shortlist(tmp_path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist({
			"security_id": "sec-1",
			"job_id": "job-1",
			"title": "Python 后端",
			"company": "Acme",
			"city": "广州",
			"salary": "20-30K",
			"source": "zhipin",
		})
		cache.add_shortlist({
			"security_id": "sec-2",
			"job_id": "job-2",
			"title": "Go 后端",
			"company": "Beta",
			"city": "深圳",
			"salary": "25-35K",
			"source": "zhipin",
		})
		cache.put_job_desc("job-1", "负责 Python API、SQLite 缓存和自动化测试。")


def test_ai_fit_uses_local_resume_and_cached_job_details(tmp_path):
	_setup_resume(tmp_path)
	_seed_shortlist(tmp_path)
	runner = CliRunner()
	service = FakeAIService({
		"results": [
			{
				"job_id": "job-1",
				"title": "Python 后端",
				"match_score": 86,
				"gaps": ["补充云服务经验"],
				"keyword_hits": ["Python", "SQLite"],
				"recommendation": "值得优先准备",
			},
		],
	})

	with (
		patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service),
		patch("boss_agent_cli.commands._platform.get_platform_instance") as get_platform,
		patch("boss_agent_cli.api.client.BossClient") as boss_client,
	):
		result = _invoke(runner, tmp_path, ["fit", "--resume", "test-resume", "--limit", "5"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "ai-fit"
	assert parsed["data"]["results"][0]["match_score"] == 86
	assert parsed["data"]["missing"] == [
		{
			"job_id": "job-2",
			"security_id": "sec-2",
			"title": "Go 后端",
			"company": "Beta",
			"status": "缺详情",
			"hint": "先 boss detail sec-2",
		},
	]
	assert parsed["data"]["summary"] == {"analyzed": 1, "missing_details": 1}
	assert "Python API" in service.messages[1]["content"]
	get_platform.assert_not_called()
	boss_client.assert_not_called()


def test_ai_fit_requires_ai_configuration(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["fit", "--resume", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_ai_fit_reports_missing_resume(tmp_path):
	runner = CliRunner()
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=FakeAIService({"results": []})):
		result = _invoke(runner, tmp_path, ["fit", "--resume", "ghost"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_ai_fit_empty_shortlist_returns_empty_report(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({"results": [{"job_id": "unexpected"}]})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["fit", "--resume", "test-resume"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"] == {"results": [], "missing": [], "summary": {"analyzed": 0, "missing_details": 0}}
	assert service.messages == []
