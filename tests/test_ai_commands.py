"""Tests for AI command group (boss ai)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(runner, tmp_path, args):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai"] + args)


def _setup_ai_config(tmp_path, monkeypatch):
	"""配置 AI 服务使其 is_configured() 返回 True。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine-id")
	from boss_agent_cli.ai.config import AIConfigStore
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o")
	store.save_api_key("sk-test-key")
	return store


def _setup_resume(tmp_path):
	"""创建一份测试简历。"""
	runner = CliRunner()
	runner.invoke(cli, [
		"--data-dir", str(tmp_path), "--json",
		"resume", "init", "--name", "test-resume", "--template", "default",
	])


def _mock_ai_response(json_data: dict):
	"""构造一个 mock httpx 响应。"""
	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = {
		"choices": [{"message": {"content": json.dumps(json_data, ensure_ascii=False)}}]
	}
	mock_resp.raise_for_status = MagicMock()
	return mock_resp


# ── config 子命令 ──────────────────────────────────────────


def test_config_show_default(tmp_path, monkeypatch):
	"""查看默认配置"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["config"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["ai_provider"] is None
	assert parsed["data"]["api_key_set"] is False


def test_config_set_provider(tmp_path, monkeypatch):
	"""设置 AI 提供商"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["config", "--provider", "openai", "--model", "gpt-4o", "--api-key", "sk-123"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "ai_provider" in parsed["data"]["updated_fields"]
	assert "api_key" in parsed["data"]["updated_fields"]


def test_config_show_after_set(tmp_path, monkeypatch):
	"""设置后查看配置"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["config", "--provider", "deepseek", "--model", "deepseek-chat"])
	result = _invoke(runner, tmp_path, ["config"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["ai_provider"] == "deepseek"
	assert parsed["data"]["ai_model"] == "deepseek-chat"


def test_config_set_temperature(tmp_path, monkeypatch):
	"""设置温度参数"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["config", "--temperature", "0.3"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "ai_temperature" in parsed["data"]["updated_fields"]


# ── AI 未配置时错误 ────────────────────────────────────────


def test_analyze_jd_not_configured(tmp_path, monkeypatch):
	"""AI 未配置时返回错误"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["analyze-jd", "some jd text", "--resume", "myresume"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_polish_not_configured(tmp_path, monkeypatch):
	"""AI 未配置时 polish 返回错误"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["polish", "myresume"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


# ── 简历不存在时错误 ──────────────────────────────────────


def test_analyze_jd_resume_not_found(tmp_path, monkeypatch):
	"""简历不存在时返回错误"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["analyze-jd", "some jd", "--resume", "ghost"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── analyze-jd 成功路径 ──────────────────────────────────


def test_analyze_jd_success(tmp_path, monkeypatch):
	"""分析职位描述成功"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_result = {"match_score": 85, "match_analysis": "匹配度较高"}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["analyze-jd", "需要三年经验的后端工程师", "--resume", "test-resume"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["match_score"] == 85


# ── polish 成功路径 ───────────────────────────────────────


def test_polish_success(tmp_path, monkeypatch):
	"""简历润色成功"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_result = {"polished_sections": [], "general_suggestions": ["建议一"]}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["polish", "test-resume"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "general_suggestions" in parsed["data"]


def test_cover_letter_non_object_ai_json_returns_parse_error(tmp_path, monkeypatch):
	"""AI 返回合法 JSON 但不是对象时按解析失败返回，而不是 setdefault 的 AttributeError。"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = {"choices": [{"message": {"content": '["建议一", "建议二"]'}}]}
	mock_resp.raise_for_status = MagicMock()
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=mock_resp):
		result = _invoke(runner, tmp_path, ["cover-letter", "test-resume", "--jd", "目标岗位"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_PARSE_ERROR"
	assert "JSON 对象" in parsed["error"]["message"]


# ── optimize 成功路径 ─────────────────────────────────────


def test_optimize_success(tmp_path, monkeypatch):
	"""基于职位优化简历成功"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_result = {"match_score_before": 60, "match_score_after": 85, "optimized_sections": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["optimize", "test-resume", "--jd", "需要后端工程师"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["match_score_before"] == 60


# ── suggest 成功路径 ──────────────────────────────────────


def test_suggest_success(tmp_path, monkeypatch):
	"""基于职位给出建议成功"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_result = {"suggestions": [{"priority": "high", "suggestion": "补充项目经验"}]}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["suggest", "test-resume", "--jd", "需要后端工程师"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["suggestions"][0]["priority"] == "high"


# ── AI 调用失败 ──────────────────────────────────────────


def test_reply_success_without_resume(tmp_path, monkeypatch):
	"""ai reply 不提供简历也能生成草稿"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	mock_result = {
		"intent_analysis": "招聘者希望确认到岗时间",
		"reply_drafts": [
			{"style": "简洁专业", "text": "您好，下月初可入职", "suitable_when": "已做好准备"},
		],
		"key_points": ["明确入职时间"],
		"avoid": ["模糊承诺"],
	}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["reply", "您什么时候可以入职？"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]["reply_drafts"]) == 1
	assert parsed["data"]["reply_drafts"][0]["style"] == "简洁专业"


def test_reply_with_resume_and_context(tmp_path, monkeypatch):
	"""ai reply 支持简历和上下文"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_result = {"intent_analysis": "x", "reply_drafts": [{"style": "热情积极", "text": "感谢", "suitable_when": ""}], "key_points": [], "avoid": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, [
			"reply", "请发一下简历", "--resume", "test-resume",
			"--context", "前面聊了 Python 岗位", "--tone", "热情积极",
		])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


def test_reply_requires_ai_config(tmp_path, monkeypatch):
	"""未配置 AI 时 reply 应返回 AI_NOT_CONFIGURED"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()

	result = _invoke(runner, tmp_path, ["reply", "hello"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_analyze_jd_ai_error(tmp_path, monkeypatch):
	"""AI 调用失败返回错误"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	from boss_agent_cli.ai.service import AIServiceError
	with patch("boss_agent_cli.ai.service.httpx.post", side_effect=AIServiceError("API 请求失败: HTTP 500", status_code=500)):
		result = _invoke(runner, tmp_path, ["analyze-jd", "some jd", "--resume", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_API_ERROR"


# ── AI 返回非 JSON ───────────────────────────────────────


def test_analyze_jd_parse_error(tmp_path, monkeypatch):
	"""AI 返回非 JSON 时返回解析错误"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = {
		"choices": [{"message": {"content": "这不是JSON格式的回复"}}]
	}
	mock_resp.raise_for_status = MagicMock()
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=mock_resp):
		result = _invoke(runner, tmp_path, ["analyze-jd", "some jd", "--resume", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_PARSE_ERROR"


# ── @file 语法 ────────────────────────────────────────────


def test_analyze_jd_at_file(tmp_path, monkeypatch):
	"""支持 @file 语法读取文件"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	jd_file = tmp_path / "jd.txt"
	jd_file.write_text("需要五年经验的后端工程师", encoding="utf-8")

	mock_result = {"match_score": 70}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["analyze-jd", f"@{jd_file}", "--resume", "test-resume"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["match_score"] == 70


def test_analyze_jd_at_file_not_found(tmp_path, monkeypatch):
	"""@file 文件不存在时返回错误"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["analyze-jd", "@/no/such/file.txt", "--resume", "test-resume"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert parsed["error"]["recovery_action"] == "检查 @ 后面的文件路径是否存在后重试"


_AT_FILE_COMMANDS = [
	["analyze-jd", "FILE", "--resume", "test-resume"],
	["optimize", "test-resume", "--jd", "FILE"],
	["suggest", "test-resume", "--jd", "FILE"],
	["reply", "FILE"],
	["interview-prep", "FILE"],
	["chat-coach", "FILE"],
	["resume-optimize", "test-resume", "--jd", "FILE"],
	["cover-letter", "test-resume", "--jd", "FILE"],
]


@pytest.mark.parametrize("args", _AT_FILE_COMMANDS)
def test_at_file_non_utf8_returns_envelope(tmp_path, monkeypatch, args):
	"""GBK 保存的输入文件在每条 @file 命令上都给信封，而不是 UnicodeDecodeError traceback"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	jd_file = tmp_path / "gbk.txt"
	jd_file.write_bytes("需要五年经验的后端工程师".encode("gbk"))

	argv = [f"@{jd_file}" if arg == "FILE" else arg for arg in args]

	result = _invoke(runner, tmp_path, argv)

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "UTF-8" in parsed["error"]["message"]
	assert "转存为 UTF-8" in parsed["error"]["recovery_action"]


def test_at_file_directory_returns_envelope(tmp_path, monkeypatch):
	"""@路径 指向目录时给信封，而不是 IsADirectoryError traceback"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	result = _invoke(runner, tmp_path, ["analyze-jd", f"@{tmp_path}", "--resume", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "是目录" in parsed["error"]["message"]
	assert "具体的 UTF-8 文本文件路径" in parsed["error"]["recovery_action"]


def test_at_file_permission_error_returns_envelope(tmp_path, monkeypatch):
	"""@file 读取失败（权限不足）时按 LOCAL_IO_ERROR 返回"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	jd_file = tmp_path / "locked.txt"
	jd_file.write_text("后端工程师", encoding="utf-8")
	original_read_text = Path.read_text

	def _deny_locked(self, *args, **kwargs):
		if self == jd_file:
			raise PermissionError(13, "Permission denied")
		return original_read_text(self, *args, **kwargs)

	monkeypatch.setattr(Path, "read_text", _deny_locked)

	result = _invoke(runner, tmp_path, ["analyze-jd", f"@{jd_file}", "--resume", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "LOCAL_IO_ERROR"
	assert "读取" in parsed["error"]["message"]


# ── markdown 代码块包裹的 JSON ────────────────────────────


def test_analyze_jd_markdown_wrapped_json(tmp_path, monkeypatch):
	"""AI 返回 markdown 代码块包裹的 JSON 也能解析"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	wrapped = '```json\n{"match_score": 90}\n```'
	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = {
		"choices": [{"message": {"content": wrapped}}]
	}
	mock_resp.raise_for_status = MagicMock()
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=mock_resp):
		result = _invoke(runner, tmp_path, ["analyze-jd", "jd text", "--resume", "test-resume"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["match_score"] == 90


# ── schema 集成 ──────────────────────────────────────────


def test_schema_contains_ai():
	"""schema 包含 ai 命令"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "ai" in parsed["data"]["commands"]
	cmd = parsed["data"]["commands"]["ai"]
	assert "config" in cmd["subcommands"]
	assert "local" in cmd["subcommands"]
	assert "analyze-jd" in cmd["subcommands"]
	assert "polish" in cmd["subcommands"]
	assert "optimize" in cmd["subcommands"]
	assert "suggest" in cmd["subcommands"]
	assert "reply" in cmd["subcommands"]
	assert "interview-prep" in cmd["subcommands"]
	assert "chat-coach" in cmd["subcommands"]


def test_schema_contains_ai_error_codes():
	"""schema 包含 AI 错误码"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	codes = parsed["data"]["error_codes"]
	assert "AI_NOT_CONFIGURED" in codes
	assert "AI_API_ERROR" in codes
	assert "AI_PARSE_ERROR" in codes


# ── interview-prep 子命令 ─────────────────────────────────


def test_interview_prep_not_configured(tmp_path, monkeypatch):
	"""AI 未配置时返回错误"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["interview-prep", "需要三年后端经验"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_interview_prep_success(tmp_path, monkeypatch):
	"""基于 JD 生成面试题成功"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	mock_result = {
		"job_summary": "后端工程师岗位",
		"questions": [
			{
				"category": "技术",
				"question": "说说对分布式事务的理解",
				"framework": "先定义再举例再总结",
				"evaluation_points": ["概念准确", "举例贴合"],
				"difficulty": "中等",
			},
			{
				"category": "行为",
				"question": "描述一次线上故障处理经历",
				"framework": "STAR",
				"evaluation_points": ["定位能力", "复盘深度"],
				"difficulty": "高",
			},
		],
		"preparation_tips": ["复习 CAP 定理", "准备项目故事"],
	}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["interview-prep", "招聘后端工程师 需要 Go 和 Kafka"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]["questions"]) == 2
	assert parsed["data"]["questions"][0]["category"] == "技术"


def test_interview_prep_with_resume_and_count(tmp_path, monkeypatch):
	"""interview-prep 支持简历参考和题量参数"""
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()

	mock_result = {"job_summary": "x", "questions": [], "preparation_tips": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, [
			"interview-prep", "需要 Java 工程师", "--resume", "test-resume", "--count", "5",
		])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


def test_interview_prep_at_file(tmp_path, monkeypatch):
	"""interview-prep 支持 @file 读取 JD"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	jd_file = tmp_path / "jd.txt"
	jd_file.write_text("资深后端工程师 要求 Java + Kafka", encoding="utf-8")

	mock_result = {"job_summary": "x", "questions": [], "preparation_tips": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["interview-prep", f"@{jd_file}"])

	assert result.exit_code == 0


def test_interview_prep_resume_not_found(tmp_path, monkeypatch):
	"""指定了不存在的简历应返回 RESUME_NOT_FOUND"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["interview-prep", "JD 文本", "--resume", "ghost"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


# ── chat-coach 子命令 ────────────────────────────────────


def test_chat_coach_not_configured(tmp_path, monkeypatch):
	"""AI 未配置时返回错误"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["chat-coach", "聊天记录文本"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


def test_chat_coach_success(tmp_path, monkeypatch):
	"""基于聊天记录生成沟通建议成功"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	mock_result = {
		"stage_analysis": "初次接触阶段",
		"recruiter_intent": "想确认工作经验和薪资期望",
		"strengths": ["自我介绍清晰"],
		"weaknesses": ["未主动引导下一步"],
		"next_action_recommendation": "主动询问流程",
		"message_templates": [
			{"scenario": "询问流程", "text": "请问接下来需要准备什么？"}
		],
		"avoid_pitfalls": ["避免直接谈薪资"],
	}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["chat-coach", "招聘者：您好\n我：你好"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["recruiter_intent"] == "想确认工作经验和薪资期望"
	assert len(parsed["data"]["message_templates"]) == 1


def test_chat_coach_at_file(tmp_path, monkeypatch):
	"""chat-coach 支持 @file 读取聊天记录"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	chat_file = tmp_path / "chat.txt"
	chat_file.write_text("招聘者：您好\n我：你好", encoding="utf-8")

	mock_result = {
		"stage_analysis": "x",
		"recruiter_intent": "y",
		"strengths": [], "weaknesses": [],
		"next_action_recommendation": "z",
		"message_templates": [], "avoid_pitfalls": [],
	}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["chat-coach", f"@{chat_file}"])

	assert result.exit_code == 0


def test_chat_coach_with_style(tmp_path, monkeypatch):
	"""chat-coach 支持沟通风格偏好"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	mock_result = {
		"stage_analysis": "x", "recruiter_intent": "y",
		"strengths": [], "weaknesses": [],
		"next_action_recommendation": "z",
		"message_templates": [], "avoid_pitfalls": [],
	}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["chat-coach", "聊天记录", "--style", "积极主动"])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


def test_chat_coach_parse_error(tmp_path, monkeypatch):
	"""AI 返回非 JSON 时返回 AI_PARSE_ERROR"""
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()

	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = {
		"choices": [{"message": {"content": "this is plain text"}}]
	}
	mock_resp.raise_for_status = MagicMock()
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=mock_resp):
		result = _invoke(runner, tmp_path, ["chat-coach", "文本"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_PARSE_ERROR"


# ── 覆盖率补齐：各命令剩余错误分支 ─────────────────────────

from boss_agent_cli.ai.service import AIServiceError  # noqa: E402


def _mock_ai_error(message: str = "API 500", status: int = 500):
	return patch("boss_agent_cli.ai.service.httpx.post", side_effect=AIServiceError(message, status_code=status))


def _mock_ai_non_json():
	"""返回 200 但 content 不是 JSON 的响应"""
	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = {"choices": [{"message": {"content": "plain"}}]}
	mock_resp.raise_for_status = MagicMock()
	return patch("boss_agent_cli.ai.service.httpx.post", return_value=mock_resp)


# ── _create_ai_service 边界：配置存在但 api_key / base_url 缺失 ──


def test_require_ai_service_when_api_key_missing(tmp_path, monkeypatch):
	"""AI 配置存在但 api_key 未保存时应视为未配置"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	from boss_agent_cli.ai.config import AIConfigStore
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o")
	# 故意不存 api_key -> is_configured 为 True，但 get_api_key 返回 None
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["polish", "任意"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"


# ── ai config 剩余参数分支 ────────────────────────────────


def test_config_set_base_url(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["config", "--base-url", "https://api.openai.com/v1"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "ai_base_url" in parsed["data"]["updated_fields"]


def test_config_set_max_tokens(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["config", "--max-tokens", "8000"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "ai_max_tokens" in parsed["data"]["updated_fields"]


def test_config_rejects_unknown_provider_without_endpoint(tmp_path, monkeypatch):
	"""`--provider openai-cn` 这类笔误不能再写进配置。

	以前直接返回 ok=true，之后每条 AI 命令都报「AI 服务未配置」，而 key 其实
	已经存下了——用户会去重填 key，而不是改 provider 名。
	"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	from boss_agent_cli.ai.config import AIConfigStore
	runner = CliRunner()
	result = _invoke(
		runner, tmp_path, ["config", "--provider", "openai-cn", "--model", "gpt-4o", "--api-key", "sk-test"]
	)

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "openai-cn" in parsed["error"]["message"]
	assert "openai" in parsed["error"]["message"]
	store = AIConfigStore(tmp_path)
	assert store.load_config()["ai_provider"] is None
	assert store.get_api_key() is None


def test_config_rejects_custom_provider_without_base_url(tmp_path, monkeypatch):
	"""provider=custom 必须同时给 --base-url，否则写下的是永远不能用的配置。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["config", "--provider", "custom", "--model", "m"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "--base-url" in parsed["error"]["message"]


def test_config_accepts_unknown_provider_with_explicit_base_url(tmp_path, monkeypatch):
	"""自建 OpenAI 兼容端点：名字不在登记表里，但给了 --base-url 就应当可用。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	result = _invoke(
		runner,
		tmp_path,
		[
			"config", "--provider", "myproxy", "--base-url", "https://proxy.example/v1",
			"--model", "m", "--api-key", "sk-test",
		],
	)

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["resolved_base_url"] == "https://proxy.example/v1"


def test_ai_command_names_provider_when_endpoint_missing(tmp_path, monkeypatch):
	"""遗留坏配置（provider 已填、没有端点）再跑 AI 命令时要点名 provider。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	from boss_agent_cli.ai.config import AIConfigStore
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="custom", ai_model="m", ai_base_url=None)
	store.save_api_key("sk-test")
	_setup_resume(tmp_path)

	result = _invoke(CliRunner(), tmp_path, ["polish", "test-resume"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"
	assert "'custom'" in parsed["error"]["message"]
	assert "--base-url" in parsed["error"]["recovery_action"]


# ── polish 错误分支 ──────────────────────────────────────


def test_polish_resume_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["polish", "ghost"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_polish_ai_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	with _mock_ai_error():
		result = _invoke(runner, tmp_path, ["polish", "test-resume"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_API_ERROR"


def test_polish_parse_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	with _mock_ai_non_json():
		result = _invoke(runner, tmp_path, ["polish", "test-resume"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_PARSE_ERROR"


# ── optimize 错误分支 ─────────────────────────────────────


def test_optimize_at_file_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["optimize", "test-resume", "--jd", "@/no/such.txt"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"


def test_optimize_resume_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["optimize", "ghost", "--jd", "jd"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_optimize_ai_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	with _mock_ai_error():
		result = _invoke(runner, tmp_path, ["optimize", "test-resume", "--jd", "jd"])
	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "AI_API_ERROR"


def test_optimize_parse_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	with _mock_ai_non_json():
		result = _invoke(runner, tmp_path, ["optimize", "test-resume", "--jd", "jd"])
	assert json.loads(result.output)["error"]["code"] == "AI_PARSE_ERROR"


def test_optimize_at_file_success(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	jd_file = tmp_path / "jd.txt"
	jd_file.write_text("JD 内容", encoding="utf-8")
	mock_result = {"match_score_before": 60, "match_score_after": 82, "optimized_sections": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["optimize", "test-resume", "--jd", f"@{jd_file}"])
	assert result.exit_code == 0


# ── suggest 错误分支 ──────────────────────────────────────


def test_suggest_at_file_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["suggest", "test-resume", "--jd", "@/no/such.txt"])
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_suggest_resume_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["suggest", "ghost", "--jd", "jd"])
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"


def test_suggest_ai_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	with _mock_ai_error():
		result = _invoke(runner, tmp_path, ["suggest", "test-resume", "--jd", "jd"])
	assert json.loads(result.output)["error"]["code"] == "AI_API_ERROR"


def test_suggest_parse_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	with _mock_ai_non_json():
		result = _invoke(runner, tmp_path, ["suggest", "test-resume", "--jd", "jd"])
	assert json.loads(result.output)["error"]["code"] == "AI_PARSE_ERROR"


def test_suggest_at_file_success(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	jd_file = tmp_path / "jd.txt"
	jd_file.write_text("资深后端", encoding="utf-8")
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response({"suggestions": []})):
		result = _invoke(runner, tmp_path, ["suggest", "test-resume", "--jd", f"@{jd_file}"])
	assert result.exit_code == 0


# ── reply 错误分支 ────────────────────────────────────────


def test_reply_recruiter_message_file_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["reply", "@/no/such.txt"])
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_reply_context_file_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["reply", "hello", "--context", "@/no/such.txt"])
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_reply_resume_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["reply", "hello", "--resume", "ghost"])
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"


def test_reply_ai_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	with _mock_ai_error():
		result = _invoke(runner, tmp_path, ["reply", "hi"])
	assert json.loads(result.output)["error"]["code"] == "AI_API_ERROR"


def test_reply_parse_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	with _mock_ai_non_json():
		result = _invoke(runner, tmp_path, ["reply", "hi"])
	assert json.loads(result.output)["error"]["code"] == "AI_PARSE_ERROR"


def test_reply_recruiter_message_at_file_success(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	msg_file = tmp_path / "msg.txt"
	msg_file.write_text("您好，请问...", encoding="utf-8")
	mock_result = {"intent_analysis": "x", "reply_drafts": [], "key_points": [], "avoid": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["reply", f"@{msg_file}"])
	assert result.exit_code == 0


def test_reply_context_at_file_success(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	ctx_file = tmp_path / "ctx.txt"
	ctx_file.write_text("之前聊了 Python 岗", encoding="utf-8")
	mock_result = {"intent_analysis": "x", "reply_drafts": [], "key_points": [], "avoid": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["reply", "你好", "--context", f"@{ctx_file}"])
	assert result.exit_code == 0


# ── interview-prep 错误分支 ───────────────────────────────


def test_interview_prep_at_file_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["interview-prep", "@/no/such.txt"])
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_interview_prep_ai_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	with _mock_ai_error():
		result = _invoke(runner, tmp_path, ["interview-prep", "jd"])
	assert json.loads(result.output)["error"]["code"] == "AI_API_ERROR"


def test_interview_prep_parse_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	with _mock_ai_non_json():
		result = _invoke(runner, tmp_path, ["interview-prep", "jd"])
	assert json.loads(result.output)["error"]["code"] == "AI_PARSE_ERROR"


# ── chat-coach 错误分支 ──────────────────────────────────


def test_chat_coach_at_file_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["chat-coach", "@/no/such.txt"])
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_chat_coach_resume_not_found(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["chat-coach", "文本", "--resume", "ghost"])
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"


def test_chat_coach_ai_error(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	with _mock_ai_error():
		result = _invoke(runner, tmp_path, ["chat-coach", "文本"])
	assert json.loads(result.output)["error"]["code"] == "AI_API_ERROR"


def test_chat_coach_with_resume_success(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	_setup_resume(tmp_path)
	runner = CliRunner()
	mock_result = {
		"stage_analysis": "x", "recruiter_intent": "y",
		"strengths": [], "weaknesses": [],
		"next_action_recommendation": "z",
		"message_templates": [], "avoid_pitfalls": [],
	}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)):
		result = _invoke(runner, tmp_path, ["chat-coach", "聊天", "--resume", "test-resume"])
	assert result.exit_code == 0


# ── #499 / #500：模型参数与异常回复形状 ─────────────────────

import httpx  # noqa: E402


def _mock_ai_raw(payload: dict):
	mock_resp = MagicMock()
	mock_resp.status_code = 200
	mock_resp.json.return_value = payload
	mock_resp.raise_for_status = MagicMock()
	return mock_resp


def test_interview_prep_null_content_returns_ai_api_error_envelope(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	reply = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": None}}]}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_raw(reply)):
		result = _invoke(runner, tmp_path, ["interview-prep", "Python 后端开发"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_API_ERROR"
	assert "没有返回文本内容" in parsed["error"]["message"]


def test_documented_gpt5_config_sends_reasoning_params(tmp_path, monkeypatch):
	"""docs/integrations/ai-models.md 的 gpt-5 示例配置要能直接用。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine-id")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["config", "--provider", "openai", "--model", "gpt-5", "--api-key", "sk-test-key"])
	mock_result = {"job_summary": "后端工程师岗位", "questions": [], "preparation_tips": []}
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_ai_response(mock_result)) as post:
		result = _invoke(runner, tmp_path, ["interview-prep", "Python 后端开发"])
	assert result.exit_code == 0, result.output
	payload = post.call_args.kwargs["json"]
	assert payload["model"] == "gpt-5"
	assert payload["max_completion_tokens"] == 4096
	assert "max_tokens" not in payload
	assert "temperature" not in payload


def test_ai_http_400_surfaces_provider_reason_and_param_hint(tmp_path, monkeypatch):
	_setup_ai_config(tmp_path, monkeypatch)
	runner = CliRunner()
	response = httpx.Response(
		400,
		json={"error": {"message": "Unsupported value: 'temperature' does not support 0.7 with this model."}},
		request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
	)
	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response):
		result = _invoke(runner, tmp_path, ["interview-prep", "Python 后端开发"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_API_ERROR"
	assert "does not support 0.7" in parsed["error"]["message"]
	assert "--model" in parsed["error"]["recovery_action"]
