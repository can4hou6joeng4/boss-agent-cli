"""Issue #2865: 剩余 AI 命令的模型输出字段类型校验。"""

import json
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import ai_cmd
from boss_agent_cli.main import cli


class FakeAIService:
	def __init__(self, payload: dict[str, Any]) -> None:
		self.payload = payload

	def chat(self, messages: list[dict[str, str]], *, temperature: float | None = None, max_tokens: int | None = None) -> str:
		return json.dumps(self.payload, ensure_ascii=False)


@pytest.mark.parametrize(("args", "payload", "field", "expected", "actual"), [
	(["analyze-jd", "职位文本", "--resume", "demo"], {"match_score": "85"}, "match_score", "int", "str"),
	(["polish", "demo"], {"polished_sections": "段落"}, "polished_sections", "list", "str"),
	(["optimize", "demo", "--jd", "职位文本"], {"match_score_before": "65"}, "match_score_before", "int", "str"),
	(["suggest", "demo", "--jd", "职位文本"], {"long_term_plan": 123}, "long_term_plan", "str", "int"),
	(["reply", "招聘者消息"], {"reply_drafts": {"style": "简洁专业"}}, "reply_drafts", "list", "dict"),
	(["interview-prep", "职位文本", "--count", "2"], {"questions": "问题一"}, "questions", "list", "str"),
	(["chat-coach", "聊天记录"], {"strengths": "优点"}, "strengths", "list", "str"),
	(["resume-optimize", "demo", "--jd", "职位文本"], {"key_suggestions": "建议"}, "key_suggestions", "list", "str"),
])
def test_ai_command_rejects_drifted_model_field(
	tmp_path, monkeypatch, args: list[str], payload: dict[str, Any],
	field: str, expected: str, actual: str,
) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	with (
		patch.object(ai_cmd, "_require_ai_service", return_value=FakeAIService(payload)),
		patch.object(ai_cmd, "_load_resume_text", return_value="候选人简历摘要"),
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai"] + args)

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_PARSE_ERROR"
	assert field in parsed["error"]["message"]
	assert expected in parsed["error"]["message"]
	assert actual in parsed["error"]["message"]
	assert parsed["data"] is None


def test_ai_command_keeps_success_shape_when_fields_are_missing(tmp_path, monkeypatch) -> None:
	"""只校验已给出的字段：缺失字段不补键，成功信封形状保持不变。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	with (
		patch.object(ai_cmd, "_require_ai_service", return_value=FakeAIService({"match_score": 85})),
		patch.object(ai_cmd, "_load_resume_text", return_value="候选人简历摘要"),
	):
		result = CliRunner().invoke(cli, [
			"--data-dir", str(tmp_path), "--json", "ai", "analyze-jd", "职位文本", "--resume", "demo",
		])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"] == {"match_score": 85}
