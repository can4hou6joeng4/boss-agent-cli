"""Issue #2858 / #2859: AI 配置端点与嵌套输出类型边界。"""

import json
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


class FakeAIService:
	def __init__(self, payload: dict[str, Any]) -> None:
		self.payload = payload

	def chat(self, messages: list[dict[str, str]], *, temperature: float | None = None, max_tokens: int | None = None) -> str:
		return json.dumps(self.payload, ensure_ascii=False)


def _invoke(runner: CliRunner, tmp_path, args: list[str]):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai"] + args)


def _setup(tmp_path, monkeypatch, base_url: str | None = None) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="demo-model", ai_base_url=base_url)
	result = CliRunner().invoke(cli, [
		"--data-dir", str(tmp_path), "--json", "resume", "init",
		"--name", "test-resume", "--template", "default",
	])
	assert result.exit_code == 0, result.output
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist({
			"security_id": "sec-1", "job_id": "job-1", "title": "Python 后端",
			"company": "Acme", "city": "", "salary": "", "source": "zhipin",
		})
		cache.put_job_desc("job-1", "负责 Python API。")


@pytest.mark.parametrize("bad_url", [
	"not-a-url",
	"ftp://api.example.invalid/v1",
	"http://api.example.invalid/v1?key=demo-pass",
	"http://api.example.invalid/v1#fragment",
])
def test_ai_config_rejects_invalid_stored_base_url(tmp_path, monkeypatch, bad_url: str) -> None:
	"""查看配置不能把执行时必然 CONFIG_INVALID 的地址回显成生效端点（Issue #2858）。"""
	_setup(tmp_path, monkeypatch, base_url=bad_url)
	result = _invoke(CliRunner(), tmp_path, ["config"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "CONFIG_INVALID"
	assert "ai config --base-url" in parsed["error"]["recovery_action"]
	assert parsed["data"] is None


def test_ai_config_keeps_valid_stored_base_url(tmp_path, monkeypatch) -> None:
	_setup(tmp_path, monkeypatch, base_url="https://api.example.invalid/v1")
	result = _invoke(CliRunner(), tmp_path, ["config"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["resolved_base_url"] == "https://api.example.invalid/v1"


@pytest.mark.parametrize(("args", "payload", "field", "expected_type", "actual_type"), [
	(["fit", "--resume", "test-resume"], {"results": "not-a-list"}, "results", "list", "str"),
	(["suggest-keywords"], {"keyword_groups": 42}, "keyword_groups", "list", "int"),
	(["cover-letter", "test-resume", "--jd", "demo jd"], {"greeting_opener": 42}, "greeting_opener", "str", "int"),
	(["cover-letter", "test-resume", "--jd", "demo jd"], {"cover_letter": {"x": 1}}, "cover_letter", "str", "dict"),
	(["cover-letter", "test-resume", "--jd", "demo jd"], {"highlights": "oops"}, "highlights", "list", "str"),
])
def test_ai_nested_field_type_drift_returns_parse_error(
	tmp_path, monkeypatch, args: list[str], payload: dict[str, Any],
	field: str, expected_type: str, actual_type: str,
) -> None:
	"""模型输出已知字段类型漂移时不能作为 ok=true 结果出信封（Issue #2859）。"""
	_setup(tmp_path, monkeypatch)
	with patch("boss_agent_cli.commands.ai_cmd._create_ai_service", return_value=FakeAIService(payload)):
		result = _invoke(CliRunner(), tmp_path, args)
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_PARSE_ERROR"
	assert field in parsed["error"]["message"]
	assert expected_type in parsed["error"]["message"]
	assert actual_type in parsed["error"]["message"]
	assert parsed["data"] is None
