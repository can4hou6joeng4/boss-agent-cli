"""向导扫码内核检查不得阻断 Cookie / CDP 登录（Issue #1075）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from boss_agent_cli.auth import manager as auth_module
from boss_agent_cli.wizard import preflight


@pytest.mark.parametrize("source", ["cookie", "cdp"])
def test_login_gate_accepts_existing_browser_login_without_qr_kernel(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
	"""走真实认证链，仅 mock 外部边界；缺少扫码内核时仍可完成登录。"""
	auth = auth_module.AuthManager(tmp_path)
	token = {"cookies": {"wt2": "fixture"}}
	save = Mock()
	cdp_login = Mock(return_value=token)
	qr_login = Mock(side_effect=AssertionError("不应调用扫码浏览器"))
	kernel_check = Mock(return_value=("error", "缺少扫码内核"))
	monkeypatch.setattr(auth, "check_status", lambda: None)
	monkeypatch.setattr(auth, "_verify_cookie", lambda value: True)
	monkeypatch.setattr(auth._store, "save", save)
	monkeypatch.setattr(auth, "_cdp_login", cdp_login)
	monkeypatch.setattr(auth_module, "extract_cookies", lambda *args, **kwargs: token if source == "cookie" else None)
	monkeypatch.setattr(auth_module, "probe_cdp", lambda *args, **kwargs: source == "cdp")
	monkeypatch.setattr(auth_module, "login_via_browser", qr_login)
	monkeypatch.setattr(preflight, "AuthManager", lambda *args, **kwargs: auth)
	monkeypatch.setattr(preflight, "_browser_kernel_status", kernel_check)
	context = SimpleNamespace(obj={
		"data_dir": tmp_path, "logger": None, "platform": "zhipin", "role": "candidate",
	})
	menu = SimpleNamespace(select=Mock(side_effect=["login"]))

	assert preflight.ensure_login(context, menu=menu) == preflight.GATE_READY
	assert auth._token is not None
	save.assert_called_once_with(token)
	assert cdp_login.call_count == (1 if source == "cdp" else 0)
	qr_login.assert_not_called()
	kernel_check.assert_not_called()
