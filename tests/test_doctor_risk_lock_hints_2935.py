"""Issue #2935：已有风险锁时 doctor 不能再给出会被锁拒绝的后继命令。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.main import cli

TOKEN = {"cookies": {"wt2": "tok", "wbg": "1", "zp_at": "a"}, "stoken": "st_ok", "user_agent": "ua"}
UNLOCK_HINT = "在原会话的 BOSS 直聘官方页面完成验证"


def _invoke(tmp_path: Any, *, token: dict[str, Any] | None) -> tuple[dict[str, Any], MagicMock]:
	with (
		patch("boss_agent_cli.commands.doctor.AuthManager") as auth,
		patch("boss_agent_cli.commands.doctor.probe_cdp") as cdp,
		patch("boss_agent_cli.commands.doctor.httpx.get") as httpx_get,
		patch("boss_agent_cli.commands.doctor.extract_cookies") as cookies,
	):
		auth.return_value.check_status.return_value = token
		cdp.return_value = None
		cookies.return_value = None
		httpx_get.return_value = MagicMock(status_code=200)
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--delay", "45-90", "doctor"],
		)
	assert result.exit_code == 0, result.output
	return json.loads(result.output), httpx_get


def test_no_token_with_lock_does_not_suggest_login(tmp_path: Any) -> None:
	cdp_risk_lock.write_lock(tmp_path, "demo-digest", reason="ENVIRONMENT_RISK")

	envelope, httpx_get = _invoke(tmp_path, token=None)
	hints = envelope["hints"]

	assert envelope["ok"] is True
	assert hints["next_actions"] == []
	assert hints["operator_actions"][0].startswith(UNLOCK_HINT)
	assert any("clean --risk-lock" in action for action in hints["operator_actions"][:3])
	httpx_get.assert_not_called()


def test_token_with_lock_does_not_suggest_live_status(tmp_path: Any) -> None:
	cdp_risk_lock.write_lock(tmp_path, "demo-digest", reason="ENVIRONMENT_RISK")

	envelope, _ = _invoke(tmp_path, token=TOKEN)
	joined = " ".join(envelope["hints"]["next_actions"])

	assert "status --live" not in joined
	assert "login" not in joined
	assert "doctor --cdp-url" not in joined
	assert envelope["hints"]["operator_actions"][0].startswith(UNLOCK_HINT)


def test_no_lock_keeps_existing_next_actions(tmp_path: Any) -> None:
	envelope, httpx_get = _invoke(tmp_path, token=TOKEN)

	assert any("status --live" in action for action in envelope["hints"]["next_actions"])
	httpx_get.assert_called_once()
