"""批次二十二回归测试：Issue #1623–#1627。

全部测试离线运行；不调用平台 API，不启动真实下载或模型服务。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.manager import TokenRefreshFailed
from boss_agent_cli.main import cli


def _payload(result) -> dict:
	payload = json.loads(result.output)
	assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	return payload


@pytest.mark.parametrize("value", ["inf", "nan", "1e400"])
def test_ai_config_rejects_non_finite_temperature(tmp_path: Path, value: str) -> None:
	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "ai", "config", "--temperature", value]
	)
	payload = _payload(result)

	assert result.exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	assert not (tmp_path / "ai" / "config.json").exists()


def test_login_curl_parse_value_error_is_not_retryable(tmp_path: Path) -> None:
	# 登录入口在解析 curl 前会读取数据目录下的风险锁；不隔离会用真实
	# ~/.boss-agent，本机存在 cdp_risk_lock.json 时误报 ACCOUNT_RISK（Issue #2677）。
	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "login", "--curl-file", "-"], input="not-a-curl"
	)
	payload = _payload(result)

	assert result.exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False


def test_ai_local_pull_rejects_empty_model_before_spawn() -> None:
	with patch("boss_agent_cli.commands.ai_local.subprocess.run") as run:
		result = CliRunner().invoke(
			cli, ["--json", "ai", "local", "pull", "--model", "", "--confirm-download"]
		)
	payload = _payload(result)

	assert result.exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert run.called is False


def test_ai_local_smoke_rejects_non_string_model_without_request(tmp_path: Path) -> None:
	(tmp_path / "ai").mkdir(parents=True)
	(tmp_path / "ai" / "config.json").write_text(
		json.dumps({"ai_provider": "ollama", "ai_model": ["qwen3:14b"], "ai_max_tokens": 512}),
		encoding="utf-8",
	)

	with patch("boss_agent_cli.commands.ai_local.AIService") as service, patch(
		"boss_agent_cli.commands.ai_local.AIConfigStore.get_api_key", return_value="offline-key"
	):
		result = CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "ai", "local", "smoke"]
		)
	payload = _payload(result)

	assert result.exit_code == 1
	assert payload["error"]["code"] == "CONFIG_INVALID"
	assert service.called is False


def test_me_token_refresh_failure_keeps_specific_error_code() -> None:
	instance = MagicMock()
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.user_info.side_effect = TokenRefreshFailed()

	with patch("boss_agent_cli.commands.me.AuthManager"), patch(
		"boss_agent_cli.commands.me.get_platform_instance", return_value=instance
	):
		result = CliRunner().invoke(cli, ["--json", "me", "--section", "user"])
	payload = _payload(result)

	assert result.exit_code == 1
	assert payload["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert "Token 刷新失败" in payload["error"]["message"]
