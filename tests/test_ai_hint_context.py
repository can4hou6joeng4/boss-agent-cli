"""AI 命令的后继与恢复提示在产生它的数据目录中直接重放。"""

import io
import json
import shlex
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.service import AIServiceError
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


@pytest.fixture
def ai_dir(tmp_path, monkeypatch):
	"""数据目录含空格、单引号与 Rich 标签；默认目录指向一次性 HOME。"""
	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	data_dir = tmp_path / "jobs [bold] team's"
	data_dir.mkdir()
	return data_dir


class FakeAIService:
	def __init__(self, payload: dict[str, Any]) -> None:
		self.payload = payload
		self.messages: list[dict[str, str]] = []

	def chat(self, messages: list[dict[str, str]], *, temperature: float | None = None, max_tokens: int | None = None) -> str:
		self.messages = messages
		return json.dumps(self.payload, ensure_ascii=False)


def _run(data_dir, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _prefix(action: str, data_dir) -> list[str]:
	parts = shlex.split(action)
	assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())], action
	return parts


def _replay(action: str, data_dir, **placeholders) -> dict[str, Any]:
	values = {"<security_id>": "sec-1", "<job_id>": "job-1", **placeholders}
	argv = [values.get(part, part) for part in shlex.split(action)[1:]]
	result = CliRunner().invoke(cli, ["--json", *argv])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def _setup_resume(data_dir) -> None:
	result = _run(data_dir, "resume", "init", "--name", "test-resume", "--template", "default")
	assert result.exit_code == 0, result.output


def test_ai_config_hints_keep_directory_and_replay(ai_dir):
	result = _run(ai_dir, "ai", "config")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert [_prefix(action, ai_dir)[3:5] for action in actions] == [["ai", "config"], ["ai", "local"]]
	# 提示照抄到默认目录看的是另一套 AI 配置，重放只会把新 provider 写错地方。
	status = _replay(actions[1], ai_dir)
	assert status["command"] == "ai.local.configure"


def test_ai_local_status_tty_next_steps_keep_directory_with_rich_tags(ai_dir, monkeypatch):
	configure = _run(ai_dir, "ai", "local", "configure", "--runtime", "ollama", "--model", "qwen3:14b")
	assert configure.exit_code == 0, configure.output
	stream = io.StringIO()

	from rich.console import Console

	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=200))
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	result = CliRunner().invoke(cli, ["--data-dir", str(ai_dir), "ai", "local", "status"])

	assert result.exit_code == 0, result.output
	text = stream.getvalue()
	assert "--data-dir" in text
	assert "ai local smoke" in text
	# 目录里的 [bold] 必须按字面显示，而不是被 Rich 当成标签吞掉。
	assert "[bold]" in text


def test_ai_local_import_hints_keep_directory_and_replay(ai_dir):
	source = ai_dir / "source.gguf"
	source.write_text("tiny fixture", encoding="utf-8")

	result = _run(ai_dir, "ai", "local", "import", "--path", str(source), "--model", "my model")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	configure = _prefix(actions[0], ai_dir)
	assert configure[3:] == [
		"ai", "local", "configure", "--runtime", "vllm", "--model", "my model",
	]
	# 状态提示直接重放：丢目录就会去默认目录找刚导入的模型。
	status = _replay(actions[1], ai_dir)
	assert any(item["model"] == "my model" for item in status["data"]["imported_models"])


def test_ai_fit_empty_shortlist_hint_replays_in_original_directory(ai_dir):
	_setup_resume(ai_dir)
	service = FakeAIService({"results": []})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _run(ai_dir, "ai", "fit", "--resume", "test-resume")

	assert result.exit_code == 0, result.output
	action = json.loads(result.stdout)["hints"]["next_actions"][0]
	assert _prefix(action, ai_dir)[3:] == ["shortlist", "add", "<security_id>", "<job_id>"]
	_replay(action, ai_dir)
	listed = _run(ai_dir, "shortlist", "list")
	assert len(json.loads(listed.stdout)["data"]) == 1


def test_ai_reply_hints_keep_directory(ai_dir):
	service = FakeAIService({"replies": ["您好"]})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _run(ai_dir, "ai", "reply", "您好，请发简历")

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert _prefix(parsed["hints"]["next_actions"][0], ai_dir)[3:] == ["chatmsg", "<uid>"]
	# 操作指引里引用的 chat 命令同样要指向同一个数据目录。
	operator = parsed["hints"]["operator_actions"][1]
	chat_hint = operator.split("见 ", 1)[1].split(" 输出", 1)[0]
	parts = shlex.split(chat_hint)
	assert parts[:3] == ["boss", "--data-dir", str(ai_dir.resolve())], chat_hint
	assert parts[3:] == ["chat"]


def test_ai_interview_prep_hint_keeps_directory(ai_dir):
	service = FakeAIService({"questions": []})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _run(ai_dir, "ai", "interview-prep", "招聘后端工程师")

	assert result.exit_code == 0, result.output
	action = json.loads(result.stdout)["hints"]["next_actions"][0]
	assert _prefix(action, ai_dir)[3:] == ["ai", "chat-coach", "<chat_text>"]


def test_ai_suggest_keywords_hints_keep_directory(ai_dir):
	with CacheStore(ai_dir / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist({
			"security_id": "sec-1",
			"job_id": "job-1",
			"title": "Python 后端",
			"company": "Acme",
			"city": "广州",
			"salary": "20-30K",
			"source": "zhipin",
		})
	service = FakeAIService({"keyword_groups": [], "patterns": [], "search_suggestions": []})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		result = _run(ai_dir, "ai", "suggest-keywords")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert _prefix(actions[0], ai_dir)[3:] == ["search", "<推荐关键词>"]
	assert _prefix(actions[1], ai_dir)[3:] == ["preset", "add", "<name>", "<关键词>"]


@pytest.mark.parametrize("subcommand", ["resume-optimize", "cover-letter"])
def test_ai_jd_recovery_keeps_directory(ai_dir, subcommand):
	_setup_resume(ai_dir)
	service = FakeAIService({})
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=service):
		missing = _run(ai_dir, "ai", subcommand, "test-resume")
		cache_miss = _run(ai_dir, "ai", subcommand, "test-resume", "--job-id", "job; echo pwn")

	assert missing.exit_code == 1, missing.output
	parsed = json.loads(missing.stdout)
	assert _prefix(parsed["hints"]["next_actions"][0], ai_dir)[3:] == ["search", "<关键词>"]
	assert "boss --data-dir" in parsed["error"]["recovery_action"]

	assert cache_miss.exit_code == 1, cache_miss.output
	parsed = json.loads(cache_miss.stdout)
	assert _prefix(parsed["error"]["recovery_action"], ai_dir)[3:] == [
		"detail", "<security_id>", "--job-id", "job; echo pwn",
	]


def test_ai_not_configured_recovery_keeps_directory(ai_dir):
	result = _run(ai_dir, "ai", "reply", "您好")

	assert result.exit_code == 1, result.output
	recovery = json.loads(result.stdout)["error"]["recovery_action"]
	assert _prefix(recovery, ai_dir)[3:] == [
		"ai", "config", "--provider", "<provider>", "--model", "<model>", "--api-key", "<key>",
	]


def test_ai_api_error_recovery_config_keeps_directory(ai_dir):
	class FailingAIService:
		def chat(self, messages, *, temperature=None, max_tokens=None):
			raise AIServiceError("HTTP 401", status_code=401)

	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=FailingAIService()):
		result = _run(ai_dir, "ai", "reply", "您好")

	assert result.exit_code == 1, result.output
	recovery = json.loads(result.stdout)["error"]["recovery_action"]
	config_command = recovery.split("：", 1)[1]
	assert _prefix(config_command, ai_dir)[3:] == ["ai", "config", "--api-key", "<key>"]


def test_ai_local_smoke_recovery_keeps_directory(ai_dir):
	store = AIConfigStore(ai_dir)
	store.save_config(ai_provider="custom", ai_model="m", ai_base_url=None)
	store.save_api_key("sk-test")

	result = _run(ai_dir, "ai", "local", "smoke")

	assert result.exit_code == 1, result.output
	recovery = json.loads(result.stdout)["error"]["recovery_action"]
	local_configure, ai_config = recovery.split("，或 ", 1)
	assert _prefix(local_configure, ai_dir)[3:] == [
		"ai", "local", "configure", "--runtime", "ollama", "--model", "qwen3:14b",
	]
	assert _prefix(ai_config, ai_dir)[3:] == [
		"ai", "config", "--provider", "<provider>", "--base-url", "<url>",
	]
