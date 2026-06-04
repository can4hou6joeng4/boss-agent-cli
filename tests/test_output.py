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
	assert cfg["batch_greet_max"] == 10
	assert cfg["log_level"] == "error"


def test_config_from_file(tmp_path):
	import json as json_mod
	from boss_agent_cli.config import load_config
	cfg_file = tmp_path / "config.json"
	cfg_file.write_text(json_mod.dumps({"default_city": "杭州", "log_level": "debug"}))
	cfg = load_config(cfg_file)
	assert cfg["default_city"] == "杭州"
	assert cfg["log_level"] == "debug"
	assert cfg["batch_greet_max"] == 10
