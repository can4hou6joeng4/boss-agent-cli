import ast
import json
from pathlib import Path

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


def test_schema_parameters_preserve_metadata_and_redact_payload_values():
	from boss_agent_cli.schema.formats import schema_parameter_paths

	data = {
		"format": "mcp-tools",
		"tools": [{
			"name": "boss_schema_fixture",
			"inputSchema": {
				"type": "object",
				"properties": {
					"include_private": {"type": "boolean", "description": "保留字段", "default": False},
					"access_token": {
						"type": "string", "description": "参数说明 cookie=test-cookie",
						"default": "test-default-token", "examples": ["test-example-token"],
					},
					"inputs": {
						"type": "object",
						"properties": {"session_id": {"type": "string", "description": "会话参数", "default": "test-session"}},
						"default": {"private_token": "test-nested-default", "type": "object"},
						"examples": [{"credentials": {"type": "object", "properties": {"token": "test-nested-example"}}}],
					},
					"cookies": {"type": "array", "items": {"type": "string", "default": "test-array-cookie", "examples": ["test-array-example"]}},
				},
			},
		}],
		"business": {"private_token": {"type": "object", "properties": {"token": "test-business-token"}}},
	}
	paths = schema_parameter_paths(data)
	assert ("tools", 0, "inputSchema", "properties", "inputs", "default", "private_token") not in paths
	parsed = json.loads(envelope_success("schema", data, schema_parameter_paths=paths))
	properties = parsed["data"]["tools"][0]["inputSchema"]["properties"]
	assert properties["include_private"] == data["tools"][0]["inputSchema"]["properties"]["include_private"]
	assert properties["access_token"]["type"] == "string"
	assert properties["access_token"]["description"] == "参数说明 cookie=[REDACTED]"
	assert properties["access_token"]["default"] == "[REDACTED]"
	assert properties["access_token"]["examples"] == "[REDACTED]"
	assert properties["inputs"]["properties"]["session_id"]["default"] == "[REDACTED]"
	assert properties["inputs"]["default"]["private_token"] == "[REDACTED]"
	assert properties["inputs"]["examples"][0]["credentials"] == "[REDACTED]"
	assert properties["cookies"]["items"]["type"] == "string"
	assert properties["cookies"]["items"]["default"] == "[REDACTED]"
	assert properties["cookies"]["items"]["examples"] == "[REDACTED]"
	assert parsed["data"]["business"]["private_token"] == "[REDACTED]"
	assert "test-default-token" not in json.dumps(parsed)
	assert "test-business-token" not in json.dumps(parsed)
	# 深拷贝脱敏不能改写源目录，否则下一次导出或 MCP 服务会跟着变化。
	assert data["tools"][0]["inputSchema"]["properties"]["access_token"]["default"] == "test-default-token"


def test_schema_parameter_paths_are_ignored_for_business_commands():
	data = {"private_token": {"type": "object", "description": "业务字典"}}
	parsed = json.loads(envelope_success("search", data, schema_parameter_paths={("private_token",): "private_token"}))
	assert parsed["data"]["private_token"] == "[REDACTED]"


def test_schema_shape_alone_cannot_bypass_redaction():
	data = {"private_token": {"type": "object", "properties": {"token": "test-token"}}}
	parsed = json.loads(envelope_success("schema", data))
	assert parsed["data"]["private_token"] == "[REDACTED]"


def test_native_schema_parameter_metadata_remains_visible_with_private_defaults_redacted():
	from boss_agent_cli.schema.formats import schema_parameter_paths

	data = {
		"commands": {"fixture": {
			"args": [{"name": "session_id", "description": "会话参数", "default": "test-session"}],
			"options": {"group": {"--include-private": {"type": "bool", "default": False, "description": "保留字段"},
				"--api-key": {"type": "string", "default": "test-api-key", "description": "参数说明"}}},
		}},
		"global_options": {},
		"credentials": {"type": "object", "properties": {"token": "test-business"}},
	}
	parsed = json.loads(envelope_success("schema", data, schema_parameter_paths=schema_parameter_paths(data)))
	command = parsed["data"]["commands"]["fixture"]
	assert command["options"]["group"]["--include-private"]["default"] is False
	assert command["options"]["group"]["--api-key"]["type"] == "string"
	assert command["options"]["group"]["--api-key"]["default"] == "[REDACTED]"
	assert command["args"][0]["default"] == "[REDACTED]"
	assert parsed["data"]["credentials"] == "[REDACTED]"


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


def test_envelope_error_fills_contract_from_registry():
	"""只传 code / message 时按错误码登记表补齐元数据。"""
	from boss_agent_cli.schema.error_codes import ERROR_CODES

	parsed = json.loads(envelope_error("search", code="NETWORK_ERROR", message="请求失败"))
	assert parsed["error"]["recoverable"] is ERROR_CODES["NETWORK_ERROR"]["recoverable"]
	assert parsed["error"]["recovery_action"] == ERROR_CODES["NETWORK_ERROR"]["recovery_action"]


def test_envelope_error_explicit_contract_wins_over_registry():
	"""显式 False 表示调用方判定不可重试，不能被登记表的 True 覆盖。"""
	from boss_agent_cli.schema.error_codes import ERROR_CODES

	assert ERROR_CODES["NETWORK_ERROR"]["recoverable"] is True
	parsed = json.loads(
		envelope_error(
			"search",
			code="NETWORK_ERROR",
			message="请求失败",
			recoverable=False,
			recovery_action="不要自动重试",
		)
	)
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == "不要自动重试"


def test_envelope_error_unknown_code_keeps_empty_contract():
	parsed = json.loads(envelope_error("search", code="NOT_REGISTERED_CODE", message="未知错误"))
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] is None


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


def test_redaction_stops_at_cjk_punctuation() -> None:
	"""凭据值在全角标点处结束，标点及其后的中文说明必须保留。

	旧实现用 ``[^\\s,;]+`` 匹配值，全角标点不是终止符：``stoken=abc）`` 会把
	右括号吞进 ``[REDACTED]``，``Bearer x，然后`` 连后续中文一起消失，
	``boss status`` 的就绪详情里因此出现过缺右括号的文案。
	"""
	from boss_agent_cli.output import redact_sensitive_text

	assert redact_sensitive_text("（cookies=14, stoken=abc123）") == "（cookies=14, stoken=[REDACTED]）"
	assert redact_sensitive_text("token=abc.def。") == "token=[REDACTED]。"
	assert redact_sensitive_text("Authorization: Bearer eyJ.payload.sig，然后") == "Authorization: [REDACTED]，然后"
	assert redact_sensitive_text("cookie: sessionid=xyz；继续") == "cookie: [REDACTED]；继续"


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
	assert cfg["request_delay"] == [30.0, 60.0]
	assert cfg["log_level"] == "error"


def test_config_from_file(tmp_path):
	import json as json_mod
	from boss_agent_cli.config import load_config
	cfg_file = tmp_path / "config.json"
	cfg_file.write_text(json_mod.dumps({"export_dir": "/tmp/exports", "log_level": "debug"}))
	cfg = load_config(cfg_file)
	assert cfg["export_dir"] == "/tmp/exports"
	assert cfg["log_level"] == "debug"
	assert cfg["request_delay"] == [30.0, 60.0]


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


def test_error_details_normalize_retry_safety_across_cli_and_mcp_shapes():
	cli_shape = json.loads(
		envelope_error(
			"greet",
			code="ACTION_UNCONFIRMED",
			message="请求结果未确认",
			details={"outcome": "unknown", "replay_safe": False, "reason": "transport_error"},
		)
	)
	details = cli_shape["error"]["details"]
	assert details["outcome"] == "unknown"
	assert details["replay_safe"] is False
	assert details["outcome_unknown"] is True

	mcp_shape = json.loads(
		envelope_error(
			"wizard",
			code="WORKFLOW_TIMEOUT",
			message="底层 CLI 超时",
			details={"outcome_unknown": True},
		)
	)
	details = mcp_shape["error"]["details"]
	assert details["outcome_unknown"] is True
	assert details["replay_safe"] is False


def test_error_details_leave_replay_safe_reads_without_unknown_flag():
	# 读取被中断时重试没有副作用：outcome 未知但 replay_safe=true，不应被标成禁止重试。
	payload = json.loads(
		envelope_error(
			"detail",
			code="NETWORK_ERROR",
			message="浏览器读取请求中断",
			details={"outcome": "unknown", "replay_safe": True},
		)
	)
	details = payload["error"]["details"]
	assert details["replay_safe"] is True
	assert "outcome_unknown" not in details


def test_unknown_outcome_details_always_declare_replay_safety():
	"""``outcome="unknown"`` 必须与显式的 ``replay_safe`` 一起出现。

	Agent 只能依据 ``replay_safe`` 判断写操作能否重试；只写 ``outcome`` 时
	``envelope_error`` 无法补出 ``outcome_unknown``，调用方会误以为可以重试。
	"""
	source_root = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli"
	checked = 0
	failures: list[str] = []
	for path in sorted(source_root.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if not isinstance(node, ast.Dict):
				continue
			entries = {
				key.value: value.value
				for key, value in zip(node.keys, node.values)
				if isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.Constant)
			}
			if entries.get("outcome") != "unknown":
				continue
			checked += 1
			if "replay_safe" not in entries:
				failures.append(f"{path.relative_to(source_root.parents[1])}:{node.lineno}")
	assert checked >= 1, "没有找到 outcome=unknown 的 details 字面量，AST 扫描可能失效"
	assert failures == [], f"这些位置缺少 replay_safe：{failures}"
