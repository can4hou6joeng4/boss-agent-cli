"""Tests for AI command group (boss ai)."""

import json
from unittest.mock import MagicMock, patch

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
	assert "analyze-jd" in cmd["subcommands"]
	assert "polish" in cmd["subcommands"]
	assert "optimize" in cmd["subcommands"]
	assert "suggest" in cmd["subcommands"]


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
