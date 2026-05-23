import json

from boss_agent_cli.output import envelope_success, envelope_error, Logger


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
	assert parsed["error"]["message"] == "登录态已过期 token=secret-token"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == "boss login"
	assert parsed["hints"]["cookie"] == "[REDACTED]"


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
