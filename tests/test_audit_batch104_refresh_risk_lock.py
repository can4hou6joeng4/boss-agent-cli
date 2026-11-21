"""批次 104：force_refresh 等刷新锁期间落下的风控锁必须复查（Issue #2427）。"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.auth import manager as manager_module
from boss_agent_cli.auth import token_store as token_store_module
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.auth.token_store import TokenStore

_SEED: dict[str, Any] = {"cookies": {"wt2": "old"}, "stoken": "old", "user_agent": "ua"}


def _seed(tmp_path: Any) -> TokenStore:
	store = TokenStore(tmp_path / "auth")
	store.save(dict(_SEED))
	return store


def _stub_refresh_channels(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
	def probe(*args: Any, **kwargs: Any) -> None:
		calls.append("probe")
		return None

	monkeypatch.setattr(manager_module, "probe_cdp", probe)
	monkeypatch.setattr(manager_module, "refresh_stoken", lambda *a, **k: calls.append("headless") or "fresh")
	monkeypatch.setattr(manager_module, "refresh_stoken_via_cdp", lambda *a, **k: calls.append("cdp") or "fresh")


# ── Issue #2427：等锁窗口内落下的风控锁必须在启动刷新通道前生效 ─────


def test_force_refresh_rechecks_risk_lock_after_waiting_for_refresh_lock(
	tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = _seed(tmp_path)
	calls: list[str] = []
	_stub_refresh_channels(monkeypatch, calls)
	# 等待窗口按真实实现，只缩短锁超时，避免失败路径把测试挂满 30 秒。
	monkeypatch.setattr(token_store_module, "_LOCK_TIMEOUT", 5)

	manager = AuthManager(tmp_path)
	started = threading.Event()
	release = threading.Event()

	def hold_lock() -> None:
		with store.refresh_lock():
			started.set()
			release.wait(10)

	def write_risk_lock_then_release() -> None:
		# 主线程已在 refresh_lock 上等锁：此刻落下风控锁再释放。
		time.sleep(0.25)
		cdp_risk_lock.write_lock(tmp_path, None, reason="ENVIRONMENT_RISK")
		release.set()

	holder = threading.Thread(target=hold_lock)
	holder.start()
	assert started.wait(5)
	interloper = threading.Thread(target=write_risk_lock_then_release)
	interloper.start()
	try:
		with pytest.raises(PlatformRiskError):
			manager.force_refresh()
	finally:
		holder.join(10)
		interloper.join(10)

	# 风控命中后两条刷新通道都不得启动，登录态也不得被改写。
	assert calls == []
	loaded = store.load()
	assert loaded is not None and loaded["stoken"] == "old"


def test_force_refresh_without_risk_lock_still_refreshes(
	tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = _seed(tmp_path)
	calls: list[str] = []
	_stub_refresh_channels(monkeypatch, calls)

	AuthManager(tmp_path).force_refresh()

	assert calls == ["probe", "headless"]
	loaded = store.load()
	assert loaded is not None and loaded["stoken"] == "fresh"


def test_force_refresh_still_rejects_existing_risk_lock(
	tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = _seed(tmp_path)
	calls: list[str] = []
	_stub_refresh_channels(monkeypatch, calls)
	cdp_risk_lock.write_lock(tmp_path, None, reason="ACCOUNT_RISK")

	with pytest.raises(PlatformRiskError):
		AuthManager(tmp_path).force_refresh()

	assert calls == []
	loaded = store.load()
	assert loaded is not None and loaded["stoken"] == "old"
