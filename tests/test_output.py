import json
import pytest

from boss_agent_cli.output import envelope_success, envelope_error, emit_error, emit_success, Logger


def test_envelope_success_minimal():
	result = envelope_success("status", {"logged_in": True})
	parsed = json.loads(result)
	assert parsed["ok"] is True
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == "status"
	assert parsed["data"] == {"logged_in": True}
	assert parsed["pagination"] is None
	assert parsed["error"] is None
	assert parsed["hints"] is None


def test_envelope_success_with_pagination():
	result = envelope_success(
		"search",
		{
			"jobs": [],
			"auth": {
				"token": "secret-token",
				"api_key_set": False,
				"cookies": {"wt2": "secret-cookie"},
				"security_id": "sec_001",
			},
		},
		pagination={"page": 1, "total_pages": 5, "total_count": 50, "has_next": True},
		hints={"next_actions": ["boss search q --page 2"]},
	)
	parsed = json.loads(result)
	assert parsed["ok"] is True
	assert parsed["pagination"]["has_next"] is True
	assert parsed["hints"]["next_actions"][0] == "boss search q --page 2"
	assert parsed["data"]["auth"]["token"] == "[REDACTED]"
	assert parsed["data"]["auth"]["api_key_set"] is False
	assert parsed["data"]["auth"]["cookies"] == "[REDACTED]"
	# security_id is a CLI routing identifier and must remain available in stdout envelopes.
	assert parsed["data"]["auth"]["security_id"] == "sec_001"


def test_redaction_preserves_public_error_code_metadata():
	result = envelope_success(
		"schema",
		{
			"error_codes": {
				"TOKEN_REFRESH_FAILED": {
					"message": "Token 刷新失败",
					"recoverable": True,
					"recovery_action": "boss login",
				},
			},
			"real_token": "secret-token",
		},
	)
	parsed = json.loads(result)
	assert parsed["data"]["error_codes"]["TOKEN_REFRESH_FAILED"]["message"] == "Token 刷新失败"
	assert parsed["data"]["error_codes"]["TOKEN_REFRESH_FAILED"]["recovery_action"] == "boss login"
	assert parsed["data"]["real_token"] == "[REDACTED]"


def test_redaction_preserves_public_private_fields_metadata():
	result = envelope_success(
		"export",
		{
			"private_fields": "omitted",
			"private_token": "secret-token",
		},
	)
	parsed = json.loads(result)
	assert parsed["data"]["private_fields"] == "omitted"
	assert parsed["data"]["private_token"] == "[REDACTED]"


def test_envelope_error():
	result = envelope_error(
		"search",
		code="AUTH_EXPIRED",
		message="登录态已过期 token=secret-token",
		recoverable=True,
		recovery_action="boss login",
		hints={"cookie": "secret-cookie"},
	)
	parsed = json.loads(result)
	assert parsed["ok"] is False
	assert parsed["data"] is None
	assert parsed["error"]["code"] == "AUTH_EXPIRED"
	assert parsed["error"]["message"] == "登录态已过期 token=[REDACTED]"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "boss login"
	assert parsed["hints"]["cookie"] == "[REDACTED]"


def test_envelope_error_redacts_sensitive_values_inside_message():
	result = envelope_error(
		"search",
		code="NETWORK_ERROR",
		message="请求失败 token=secret-token cookie: wt2=secret-cookie session_id=abc123 password=hunter2 authorization: Bearer auth-secret",
		recoverable=True,
		recovery_action="重试",
	)
	parsed = json.loads(result)
	message = parsed["error"]["message"]
	assert "secret-token" not in message
	assert "secret-cookie" not in message
	assert "abc123" not in message
	assert "hunter2" not in message
	assert "auth-secret" not in message
	assert message.count("[REDACTED]") == 5


def test_logger_filters_by_level(capsys):
	logger = Logger("warning")
	logger.debug("debug msg")
	logger.info("info msg")
	logger.warning("warn msg")
	logger.error("error msg")
	captured = capsys.readouterr()
	assert "debug msg" not in captured.err
	assert "info msg" not in captured.err
	assert "warn msg" in captured.err
	assert "error msg" in captured.err


def test_logger_redacts_sensitive_values_inside_message(capsys):
	logger = Logger("debug")
	logger.error("refresh failed token=secret-token cookie: wt2=secret-cookie session=abc123")
	captured = capsys.readouterr()
	assert "secret-token" not in captured.err
	assert "secret-cookie" not in captured.err
	assert "abc123" not in captured.err
	assert "token=[REDACTED]" in captured.err
	assert "cookie: [REDACTED]" in captured.err
	assert "session=[REDACTED]" in captured.err


def test_emit_success_redacts_sensitive_text_at_stdout_boundary(capsys):
	emit_success("status", {"message": "token=secret-token", "cookie": "secret-cookie"})
	captured = capsys.readouterr()
	assert "secret-token" not in captured.out
	assert "secret-cookie" not in captured.out
	parsed = json.loads(captured.out)
	assert parsed["data"]["message"] == "token=[REDACTED]"
	assert parsed["data"]["cookie"] == "[REDACTED]"


def test_emit_error_redacts_sensitive_text_at_stdout_boundary(capsys):
	with pytest.raises(SystemExit):
		emit_error("status", code="NETWORK_ERROR", message="cookie: wt2=secret-cookie")
	captured = capsys.readouterr()
	assert "secret-cookie" not in captured.out
	parsed = json.loads(captured.out)
	assert parsed["error"]["message"] == "cookie: [REDACTED]"


def test_config_defaults():
	from boss_agent_cli.config import load_config
	cfg = load_config(None)
	assert cfg["request_delay"] == [1.5, 3.0]
	assert cfg["log_level"] == "error"


def test_config_from_file(tmp_path):
	import json as json_mod
	from boss_agent_cli.config import load_config
	cfg_file = tmp_path / "config.json"
	cfg_file.write_text(json_mod.dumps({"export_dir": "/tmp/exports", "log_level": "debug"}))
	cfg = load_config(cfg_file)
	assert cfg["export_dir"] == "/tmp/exports"
	assert cfg["log_level"] == "debug"
	assert cfg["request_delay"] == [1.5, 3.0]


# --- hints 双受众通道契约（operator_actions） ---


def test_envelope_success_preserves_operator_actions():
	"""operator_actions 是面向真人的通道，不得被脱敏抹掉。"""
	result = envelope_success(
		"wizard",
		{"authenticated": False},
		hints={
			"next_actions": ["boss wizard --resume wrn_x9k2"],
			"operator_actions": ["扫码登录后回到终端"],
		},
	)
	parsed = json.loads(result)
	assert parsed["hints"]["operator_actions"] == ["扫码登录后回到终端"]
	assert parsed["hints"]["next_actions"] == ["boss wizard --resume wrn_x9k2"]


def test_envelope_error_preserves_operator_actions():
	result = envelope_error(
		"login",
		code="LOGIN_TIMEOUT",
		message="登录等待超时",
		recoverable=True,
		hints={
			"next_actions": ["boss login --timeout 180"],
			"operator_actions": ["确认二维码已完成扫码并在网页端授权登录"],
		},
	)
	parsed = json.loads(result)
	assert parsed["hints"]["operator_actions"] == ["确认二维码已完成扫码并在网页端授权登录"]
	assert parsed["hints"]["next_actions"] == ["boss login --timeout 180"]


def test_operator_actions_key_is_not_sensitive():
	"""字段名不得命中 _SENSITIVE_KEY_PARTS，否则整个数组会变成 [REDACTED]。"""
	from boss_agent_cli.output import redact_sensitive

	redacted = redact_sensitive({"operator_actions": ["去浏览器里确认筛选条件"]})
	assert redacted["operator_actions"] == ["去浏览器里确认筛选条件"]


def test_operator_actions_values_still_redact_credentials():
	"""值仍走 redact_sensitive_text——文案里别写「键：值」形式。"""
	result = envelope_success(
		"wizard",
		{},
		hints={"operator_actions": ["把 token=abc123 填进去"]},
	)
	parsed = json.loads(result)
	assert parsed["hints"]["operator_actions"] == ["把 token=[REDACTED] 填进去"]


def test_step_result_carries_operator_actions():
	from boss_agent_cli.wizard.models import StepResult, WorkflowStatus

	result = StepResult(
		{"authenticated": False},
		status=WorkflowStatus.WAITING_INPUT,
		next_action="boss wizard --resume wrn_x9k2",
		operator_actions=("扫码登录后回到终端",),
	)
	payload = result.to_dict()
	assert payload["operator_actions"] == ["扫码登录后回到终端"]
	assert payload["status"] == "waiting_input"


def test_step_result_operator_actions_defaults_empty():
	from boss_agent_cli.wizard.models import StepResult

	assert StepResult({}).to_dict()["operator_actions"] == []


# ── 脱敏精度：数值 / None 不可能是凭据 ──────────────────────────


def test_numeric_values_are_not_credentials_even_with_sensitive_key():
	"""凭据必然是字符串或容器；数值命中关键词属误报。

	`ai_max_tokens` / `max_tokens` 是 AI 生成长度上限（进 OpenAI 兼容请求体的
	`max_tokens` 字段），`token_expires_in` 是过期秒数——都不是凭据，
	却因为键名含子串 `token` 被脱敏，用户永远看不到自己配的值。
	"""
	from boss_agent_cli.output import redact_sensitive

	result = redact_sensitive({
		"ai_max_tokens": 4096,
		"max_tokens": 512,
		"token_expires_in": 3600,
		"session_timeout": 30.5,
	})

	assert result["ai_max_tokens"] == 4096
	assert result["max_tokens"] == 512
	assert result["token_expires_in"] == 3600
	assert result["session_timeout"] == 30.5


def test_none_values_are_not_redacted():
	"""None 里没有秘密可泄漏；把它说成 [REDACTED] 是误导。"""
	from boss_agent_cli.output import redact_sensitive

	result = redact_sensitive({"token_expires_in": None, "api_key": None, "stoken": None})

	assert result["token_expires_in"] is None
	assert result["api_key"] is None
	assert result["stoken"] is None


def test_real_string_credentials_still_redacted():
	"""回归护栏：放宽数值不得放过真凭据。"""
	from boss_agent_cli.output import redact_sensitive

	result = redact_sensitive({
		"stoken": "real-stoken-value",
		"api_key": "sk-live-xxxx",
		"access_token": "at-xxxx",
		"refresh_token": "rt-xxxx",
		"zp_token": "zp-xxxx",
		"cookies": {"wt2": "cookie-value"},
		"authorization": "Bearer abc",
		"password": "hunter2",
		"session_id": "sess-xxxx",
	})

	for key in result:
		assert result[key] == "[REDACTED]", f"{key} 是真凭据，必须仍被脱敏"


def test_bool_exemption_still_holds():
	"""既有的 bool 豁免不受影响。"""
	from boss_agent_cli.output import redact_sensitive

	result = redact_sensitive({"api_key_set": False, "token_present": True})

	assert result["api_key_set"] is False
	assert result["token_present"] is True


def test_status_envelope_exposes_token_expiry():
	"""端到端：boss status 的过期时间必须能被用户看到。"""
	import json

	from boss_agent_cli.output import envelope_success

	payload = json.loads(envelope_success("status", {"logged_in": True, "token_expires_in": 7200}))

	assert payload["data"]["token_expires_in"] == 7200
