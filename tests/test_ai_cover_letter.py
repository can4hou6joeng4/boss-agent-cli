"""Tests for boss ai cover-letter."""

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
		["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "test-resume", "--template", "default"],
	)
	assert result.exit_code == 0, result.output


def _payload() -> dict[str, Any]:
	return {
		"greeting_opener": "您好，我是有 5 年经验的 Python 后端工程师。",
		"cover_letter": "尊敬的招聘官：\n我对贵司的岗位很感兴趣……",
		"highlights": ["主导高并发微服务", "熟悉 Kubernetes"],
		"closing": "期待进一步沟通。",
		"warnings": [],
	}


def test_ai_cover_letter_with_jd_text_drafts_letter(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService(_payload())

	with (
		patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service),
		patch("boss_agent_cli.commands._platform.get_platform_instance") as get_platform,
		patch("boss_agent_cli.api.client.BossClient") as boss_client,
	):
		result = _invoke(runner, tmp_path, [
			"cover-letter",
			"test-resume",
			"--jd",
			"招聘 Python 后端工程师，要求熟悉微服务架构和 Kubernetes。",
			"--tone",
			"热情积极",
		])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "ai-cover-letter"
	assert parsed["data"]["greeting_opener"].startswith("您好")
	assert "Kubernetes" in parsed["data"]["highlights"][1]
	# draft-only 声明存在
	assert "不发送" in parsed["hints"]["note"]

	# prompt 含 JD、简历与语气
	prompt = service.messages[1]["content"]
	assert "Python 后端工程师" in prompt
	assert "热情积极" in prompt

	# 零平台请求
	get_platform.assert_not_called()
	boss_client.assert_not_called()


def test_ai_cover_letter_with_job_id_loads_from_cache(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.put_job_desc("job-123", "需要熟悉 Django 和 PostgreSQL 的 Python 开发。")

	service = FakeAIService(_payload())
	with (
		patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service),
		patch("boss_agent_cli.commands._platform.get_platform_instance") as get_platform,
	):
		result = _invoke(runner, tmp_path, ["cover-letter", "test-resume", "--job-id", "job-123"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	# prompt 含缓存 JD
	prompt = service.messages[1]["content"]
	assert "Django" in prompt
	get_platform.assert_not_called()


def test_ai_cover_letter_lang_en_uses_english_label(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService(_payload())
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["cover-letter", "test-resume", "--jd", "some jd", "--lang", "en"])

	assert result.exit_code == 0, result.output
	prompt = service.messages[1]["content"]
	assert "English" in prompt


def test_ai_cover_letter_requires_ai_configuration(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	_setup_resume(tmp_path)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["cover-letter", "test-resume", "--jd", "some jd"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_ai_cover_letter_reports_missing_resume(tmp_path):
	runner = CliRunner()
	service = FakeAIService(_payload())
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["cover-letter", "ghost", "--jd", "some jd"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_ai_cover_letter_requires_jd_or_job_id(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["cover-letter", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "需要指定 --jd 或 --job-id" in parsed["error"]["message"]


def test_ai_cover_letter_reports_cache_miss_for_job_id(tmp_path):
	_setup_resume(tmp_path)
	runner = CliRunner()
	service = FakeAIService({})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _invoke(runner, tmp_path, ["cover-letter", "test-resume", "--job-id", "job-999"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "CACHE_MISS"
	assert "job-999" in parsed["error"]["message"]
