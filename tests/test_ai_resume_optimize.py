"""Tests for boss ai resume-optimize."""

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


def test_ai_resume_optimize_with_jd_text_returns_suggestions(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({
		"match_score": 78,
		"key_suggestions": [
			{
				"section": "工作经历",
				"original_snippet": "负责 API 开发",
				"optimized_snippet": "负责 RESTful API 设计与开发，日均处理 10w+ 请求",
				"reason": "量化成果，匹配 JD 中的高并发要求",
			},
		],
		"keywords_to_add": ["Kubernetes", "微服务"],
		"sections_to_emphasize": ["技术栈", "项目经验"],
		"sections_to_reduce": ["教育背景"],
		"warnings": ["保持真实性，不捏造经历"],
	})

	with (
		patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service),
		patch("boss_agent_cli.commands._platform.get_platform_instance") as get_platform,
		patch("boss_agent_cli.api.client.BossClient") as boss_client,
	):
		result = _invoke(runner, tmp_path, [
			"resume-optimize",
			"test-resume",
			"--jd",
			"招聘 Python 后端工程师，要求熟悉微服务架构和 Kubernetes。",
		])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "ai-resume-optimize"
	assert parsed["data"]["match_score"] == 78
	assert len(parsed["data"]["key_suggestions"]) == 1
	assert parsed["data"]["key_suggestions"][0]["section"] == "工作经历"
	assert "Kubernetes" in parsed["data"]["keywords_to_add"]
	assert "技术栈" in parsed["data"]["sections_to_emphasize"]
	assert len(parsed["data"]["warnings"]) == 1

	# Verify prompt contains JD and resume
	prompt = service.messages[1]["content"]
	assert "Python 后端工程师" in prompt
	assert "Kubernetes" in prompt

	# Zero platform requests
	get_platform.assert_not_called()
	boss_client.assert_not_called()


def test_ai_resume_optimize_with_job_id_loads_from_cache(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()

	# Seed job description in cache
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.put_job_desc("job-123", "需要熟悉 Django 和 PostgreSQL 的 Python 开发。")

	service = FakeAIService({
		"match_score": 82,
		"key_suggestions": [],
		"keywords_to_add": ["Django", "PostgreSQL"],
		"sections_to_emphasize": [],
		"sections_to_reduce": [],
		"warnings": [],
	})

	with (
		patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service),
		patch("boss_agent_cli.commands._platform.get_platform_instance") as get_platform,
		patch("boss_agent_cli.api.client.BossClient") as boss_client,
	):
		result = _invoke(runner, tmp_path, ["resume-optimize", "test-resume", "--job-id", "job-123"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["match_score"] == 82
	assert "Django" in parsed["data"]["keywords_to_add"]

	# Verify prompt contains cached JD
	prompt = service.messages[1]["content"]
	assert "Django" in prompt
	assert "PostgreSQL" in prompt

	# Zero platform requests
	get_platform.assert_not_called()
	boss_client.assert_not_called()


def test_ai_resume_optimize_with_file_path_reads_jd_from_file(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()

	# Create JD file
	jd_file = tmp_path / "jd.txt"
	jd_file.write_text("招聘 Golang 后端，熟悉 gRPC 和分布式系统。", encoding="utf-8")

	service = FakeAIService({
		"match_score": 65,
		"key_suggestions": [],
		"keywords_to_add": ["Golang", "gRPC"],
		"sections_to_emphasize": [],
		"sections_to_reduce": [],
		"warnings": [],
	})

	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["resume-optimize", "test-resume", "--jd", f"@{jd_file}"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["match_score"] == 65

	# Verify prompt contains file content
	prompt = service.messages[1]["content"]
	assert "Golang" in prompt
	assert "gRPC" in prompt


def test_ai_resume_optimize_requires_ai_configuration(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	_setup_resume(tmp_path)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["resume-optimize", "test-resume", "--jd", "some jd"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_ai_resume_optimize_reports_missing_resume(tmp_path):
	runner = CliRunner()
	service = FakeAIService({"match_score": 0, "key_suggestions": [], "keywords_to_add": [], "sections_to_emphasize": [], "sections_to_reduce": [], "warnings": []})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["resume-optimize", "ghost", "--jd", "some jd"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_ai_resume_optimize_requires_jd_or_job_id(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["resume-optimize", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "需要指定 --jd 或 --job-id" in parsed["error"]["message"]


def test_ai_resume_optimize_reports_cache_miss_for_job_id(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["resume-optimize", "test-resume", "--job-id", "job-999"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "CACHE_MISS"
	assert "job-999" in parsed["error"]["message"]
	assert "boss detail" in parsed["error"]["recovery_action"]


def test_ai_resume_optimize_reports_nonexistent_file(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["resume-optimize", "test-resume", "--jd", "@/nonexistent.txt"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "不存在" in parsed["error"]["message"]
