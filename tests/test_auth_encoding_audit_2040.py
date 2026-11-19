"""登录态编码与刷新写回边界（Issue #2040–#2044）。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.auth.identity import account_fingerprint
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.auth.token_store import TokenStore


# ── Issue #2040：孤立代理字符不得让登录保存抛 UnicodeEncodeError ─────


def test_token_store_round_trips_lone_surrogates(tmp_path) -> None:
	store = TokenStore(tmp_path / "auth")
	token = {"cookies": {"wt2": "a\udcffb"}, "stoken": "ok"}
	store.save(token)

	assert store.load() == token


# ── Issue #2041：账号指纹对孤立代理字符稳定可用 ─────────────────────


def test_account_fingerprint_accepts_lone_surrogates() -> None:
	first = account_fingerprint("\udcff")

	assert first
	assert len(first) == 16
	assert account_fingerprint("\udcff") == first
	assert account_fingerprint("plain") != first


# ── Issue #2044：机器 ID 含孤立代理字符时读路径不得抛 UnicodeEncodeError ──


def test_machine_id_with_lone_surrogate_keeps_store_usable(tmp_path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "machine-ok")
	store = TokenStore(tmp_path / "auth")
	token = {"cookies": {"wt2": "abc"}, "stoken": "ok"}
	store.save(token)

	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "\udcff")
	# 换了机器 ID 解不开旧密文：按未登录处理即可，不能抛编码异常。
	assert store.load() is None
	# 同一机器 ID 下保存与读回仍然可用。
	store.save(token)
	assert store.load() == token


# ── Issue #2042：force_refresh 不得覆盖刷新期间完成的登录 ────────────


def test_force_refresh_does_not_overwrite_concurrent_login(tmp_path, monkeypatch) -> None:
	from boss_agent_cli.auth import manager as manager_module

	store = TokenStore(tmp_path / "auth")
	store.save({"cookies": {"wt2": "old"}, "stoken": "old", "user_agent": "ua"})

	def _refresh(cookies: Any, user_agent: Any) -> str:
		# 刷新走浏览器需要数秒：期间另一个进程完成登录并写入全新会话。
		store.save({"cookies": {"wt2": "new"}, "stoken": "login", "user_agent": "ua2"})
		return "refreshed-stoken"

	monkeypatch.setattr(manager_module, "probe_cdp", lambda *args, **kwargs: None)
	monkeypatch.setattr(manager_module, "refresh_stoken", _refresh)
	monkeypatch.setattr(manager_module, "refresh_stoken_via_cdp", _refresh)

	manager = AuthManager(tmp_path)
	manager.force_refresh()

	expected = {"cookies": {"wt2": "new"}, "stoken": "login", "user_agent": "ua2"}
	assert manager._token == expected
	assert store.load() == expected


def test_force_refresh_still_writes_when_session_unchanged(tmp_path, monkeypatch) -> None:
	from boss_agent_cli.auth import manager as manager_module

	store = TokenStore(tmp_path / "auth")
	store.save({"cookies": {"wt2": "abc"}, "stoken": "old", "user_agent": "ua"})

	monkeypatch.setattr(manager_module, "probe_cdp", lambda *args, **kwargs: None)
	monkeypatch.setattr(
		manager_module, "refresh_stoken", lambda cookies, user_agent: "fresh-stoken"
	)
	monkeypatch.setattr(manager_module, "refresh_stoken_via_cdp", lambda *a, **k: "fresh-stoken")

	manager = AuthManager(tmp_path)
	manager.force_refresh()

	loaded = store.load()
	assert loaded is not None and loaded["stoken"] == "fresh-stoken"
	assert loaded["cookies"] == {"wt2": "abc"}


# ── Issue #2043：connect 之后、主 try 之前的失败同样收尾 driver ───────


def test_login_via_cdp_stops_driver_when_page_prep_fails(monkeypatch) -> None:
	from boss_agent_cli.auth import browser as browser_module

	pw = MagicMock()
	driver = pw.start.return_value
	browser = MagicMock()
	browser.contexts = []
	context = MagicMock()
	context.new_page.side_effect = RuntimeError("Target closed")
	browser.new_context.return_value = context
	driver.chromium.connect_over_cdp.return_value = browser

	monkeypatch.setattr(browser_module, "sync_playwright", lambda: pw)
	monkeypatch.setattr(browser_module, "probe_cdp", lambda *args, **kwargs: "ws://cdp")

	with pytest.raises(RuntimeError, match="Target closed"):
		browser_module.login_via_cdp(reuse_existing=False, timeout=1)

	driver.stop.assert_called_once()
