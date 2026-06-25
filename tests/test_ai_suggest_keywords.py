"""Tests for boss ai suggest-keywords."""

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
			"tags": "双休,远程",
		})
		cache.add_shortlist({
			"security_id": "sec-2",
			"job_id": "job-2",
			"title": "Golang 后端",
			"company": "Beta",
			"city": "深圳",
			"salary": "25-35K",
			"source": "zhipin",
			"tags": "双休",
		})
		cache.add_shortlist({
			"security_id": "sec-3",
			"job_id": "job-3",
			"title": "Python 数据分析",
			"company": "Gamma",
			"city": "广州",
			"salary": "18-28K",
			"source": "zhipin",
			"tags": "",
		})


def test_ai_suggest_keywords_analyzes_shortlist_and_returns_keyword_groups(tmp_path):
	_seed_shortlist(tmp_path)
	runner = CliRunner()
	service = FakeAIService({
		"keyword_groups": [
			{
				"keywords": "Python 后端",
				"reason": "候选池有 2 个 Python 相关职位",
				"priority": "high",
			},
			{
				"keywords": "Golang 微服务",
				"reason": "扩展技术栈",
				"priority": "medium",
			},
		],
		"patterns": ["后端开发", "广深地区", "20-35K 薪资"],
		"search_suggestions": ["搜索 Python 微服务 扩展候选池", "尝试 Go + Kubernetes 组合"],
	})

	with (
		patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service),
		patch("boss_agent_cli.commands._platform.get_platform_instance") as get_platform,
		patch("boss_agent_cli.api.client.BossClient") as boss_client,
	):
		result = _invoke(runner, tmp_path, ["suggest-keywords", "--limit", "5"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "ai-suggest-keywords"
	assert len(parsed["data"]["keyword_groups"]) == 2
	assert parsed["data"]["keyword_groups"][0]["keywords"] == "Python 后端"
	assert parsed["data"]["keyword_groups"][0]["priority"] == "high"
	assert len(parsed["data"]["patterns"]) == 3
	assert len(parsed["data"]["search_suggestions"]) == 2

	# Verify prompt contains shortlist data
	prompt = service.messages[1]["content"]
	assert "Python 后端" in prompt
	assert "Golang 后端" in prompt
	assert "广州" in prompt

	# Zero platform requests
	get_platform.assert_not_called()
	boss_client.assert_not_called()


def test_ai_suggest_keywords_requires_ai_configuration(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["suggest-keywords"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_ai_suggest_keywords_handles_empty_shortlist(tmp_path):
	runner = CliRunner()
	service = FakeAIService({"keyword_groups": [], "patterns": [], "search_suggestions": []})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["suggest-keywords"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["keyword_groups"] == []
	assert parsed["data"]["patterns"] == []
	assert parsed["data"]["search_suggestions"] == []
	assert "boss shortlist add" in parsed["hints"]["next_actions"][0]
	assert service.messages == []  # AI not called when shortlist is empty


def test_ai_suggest_keywords_respects_limit(tmp_path):
	_seed_shortlist(tmp_path)
	runner = CliRunner()
	service = FakeAIService({"keyword_groups": [], "patterns": [], "search_suggestions": []})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["suggest-keywords", "--limit", "2"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True

	# Verify only 2 jobs in prompt
	prompt = service.messages[1]["content"]
	job_count = prompt.count('"title":')
	assert job_count == 2
