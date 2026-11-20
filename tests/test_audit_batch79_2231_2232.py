"""审计批次七十九：camelCase 与 passwd 凭据键的脱敏边界（Issue #2231、#2232）。

自由文本脱敏要求凭据键前是「非 ASCII 字母数字」边界，camelCase 键里关键字
前是小写字母，整段漏脱敏；``passwd`` 则不在任何凭据关键字集合里。两条路径
都会让凭据明文进入 CLI 信封的 error.message / recovery_action 以及 MCP 的
子进程失败信息。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import json

import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.output import envelope_error, redact_sensitive, redact_sensitive_text


@pytest.mark.parametrize(
	("text", "secret"),
	(
		("accessToken=SECRET123", "SECRET123"),
		("refreshToken: SECRET999", "SECRET999"),
		("clientSecret=SECRET456", "SECRET456"),
		("sessionId=SID789", "SID789"),
		("MyAuthorization: Basic QURS", "QURS"),
	),
)
def test_camel_case_credentials_are_redacted_in_text(text: str, secret: str) -> None:
	redacted = redact_sensitive_text(text)
	assert "[REDACTED]" in redacted
	assert secret not in redacted


def test_camel_case_key_name_is_preserved_for_diagnostics() -> None:
	# 键名原样回填：脱敏后仍是可读的 `accessToken=[REDACTED]`，不是整段抹掉。
	assert redact_sensitive_text("accessToken=SECRET123") == "accessToken=[REDACTED]"


def test_passwd_redacted_in_text_and_structured_paths() -> None:
	assert redact_sensitive_text("passwd=SECRET") == "passwd=[REDACTED]"
	assert redact_sensitive({"passwd": "SECRET"}) == {"passwd": "[REDACTED]"}


def test_camel_case_key_in_structured_dict_still_redacted() -> None:
	# 结构化路径按小写键名匹配，camelCase 一直有效；这里作为两条路径口径一致的护栏。
	result = redact_sensitive({"accessToken": "SECRET", "refreshToken": "SECRET2"})
	assert result == {"accessToken": "[REDACTED]", "refreshToken": "[REDACTED]"}


def test_non_credential_config_keys_are_not_over_redacted() -> None:
	# 新增的 camelCase 边界只能识别「小写→大写」转折，不能把普通配置名当凭据。
	assert redact_sensitive_text("token_expires_in=3600") == "token_expires_in=3600"
	assert redact_sensitive_text("max_tokens=512") == "max_tokens=512"
	result = redact_sensitive({"ai_max_tokens": 4096, "token_expires_in": 3600})
	assert result == {"ai_max_tokens": 4096, "token_expires_in": 3600}


def test_envelope_error_redacts_camel_case_message() -> None:
	envelope = json.loads(
		envelope_error(
			"test",
			code="INVALID_PARAM",
			message="request failed: accessToken=SECRET123",
			recoverable=False,
			recovery_action="stop and check accessToken=SECRET123",
		)
	)
	assert "SECRET123" not in envelope["error"]["message"]
	assert "SECRET123" not in envelope["error"]["recovery_action"]
	assert "[REDACTED]" in envelope["error"]["message"]


def test_mcp_cli_failure_message_is_redacted() -> None:
	# MCP 子进程失败时把 stderr 原样作为 message；信封出口必须走同一脱敏契约。
	parsed = mcp_server._parse_cli_output(
		("status",), "", "request failed: refreshToken=SECRET999", may_write=False
	)
	assert "SECRET999" not in json.dumps(parsed, ensure_ascii=False)
	assert "[REDACTED]" in parsed["error"]["message"]
