"""批次 95 审计修复：登录风控锁复查与写请求重定向上限（Issue #2394、#2396）。"""

from __future__ import annotations

import httpx
import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import BossClient, PlatformRiskError
from boss_agent_cli.auth.manager import AuthManager


class _Auth:
	data_dir = None
	_platform = "zhipin"

	def get_token(self) -> dict[str, object]:
		return {"cookies": {}, "stoken": "s", "user_agent": "UA"}

	def force_refresh(self, **kwargs: object) -> None:
		pass


class _RaisingClient:
	cookies = httpx.Cookies()

	def __init__(self, error: Exception) -> None:
		self._error = error

	def request(self, *args: object, **kwargs: object) -> httpx.Response:
		raise self._error


def _client(error: Exception) -> BossClient:
	client = BossClient(_Auth(), delay=(0, 0))  # type: ignore[arg-type]
	client._throttle.wait = lambda: None  # type: ignore[method-assign]
	client._throttle.mark = lambda: None  # type: ignore[method-assign]
	client._get_client = lambda: _RaisingClient(error)  # type: ignore[method-assign]
	return client


# ── Issue #2396：写请求的 TooManyRedirects 是结果未知 ─────────────────


def test_write_request_redirect_loop_is_unconfirmed() -> None:
	response = _client(httpx.TooManyRedirects("loop")).friend_label("fid", 1)

	assert response["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert response["__cli_error_details__"] == {
		"outcome": "unknown",
		"replay_safe": False,
		"reason": "transport_error",
	}


def test_read_request_redirect_loop_stays_retryable() -> None:
	with pytest.raises(httpx.TooManyRedirects):
		_client(httpx.TooManyRedirects("loop")).user_info()


# ── Issue #2394：扫码兜底前复查风控锁 ───────────────────────────────


def test_login_rechecks_risk_lock_before_browser_fallback(tmp_path, monkeypatch) -> None:
	manager = AuthManager(tmp_path)
	calls: list[str] = []

	def fake_extract(*args: object, **kwargs: object) -> None:
		# Cookie 提取耗时期间另一进程写下风控锁。
		cdp_risk_lock.write_lock(tmp_path, None)
		return None

	def fake_probe(*args: object, **kwargs: object) -> bool:
		calls.append("probe")
		return False

	def fake_browser(**kwargs: object) -> dict[str, object]:
		calls.append("browser")
		return {}

	monkeypatch.setattr("boss_agent_cli.auth.manager.extract_cookies", fake_extract)
	monkeypatch.setattr("boss_agent_cli.auth.manager.probe_cdp", fake_probe)
	monkeypatch.setattr("boss_agent_cli.auth.manager.login_via_browser", fake_browser)

	with pytest.raises(PlatformRiskError):
		manager.login(timeout=5)

	assert calls == [], "锁已存在时不得探测 CDP 或打开浏览器扫码"
