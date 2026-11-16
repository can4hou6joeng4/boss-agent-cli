"""本地记录漂移的下一组读取边界（Issue #1214）。

三处同族但独立：

1. ``search_cache`` / ``job_desc_cache`` 的 TTL 时间列漂移：坏值按「已过期」
   删除后继续，不能把 ``TypeError`` 兜成 ``INTERNAL_ERROR``。
2. 风险锁 ``locked_at`` 的超大整数 / ``inf``：读取不抛 ``OverflowError``，
   ``raise_if_locked`` 保持风控契约，``lock_status`` 降级展示。
3. 刷新 stoken 前 ``cookies`` 结构漂移：不发起刷新，不误报成泛化的刷新失败。
"""

import json

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.auth import manager as manager_module
from boss_agent_cli.auth.manager import AuthManager, TokenRefreshFailed
from boss_agent_cli.cache.store import CacheStore


def _open_cache(tmp_path):
	return CacheStore(tmp_path / "cache" / "boss_agent.db")


# ── 1. 缓存 TTL ─────────────────────────────────────────────────────


@pytest.mark.parametrize("drifted", ["2026-01-01T00:00:00Z", "nan", float("inf"), -5])
def test_search_cache_tolerates_drifted_created_at(tmp_path, drifted):
	with _open_cache(tmp_path) as cache:
		cache.put_search({}, '{"ok": true}')
		cache._conn.execute("UPDATE search_cache SET created_at=?", (drifted,))
		cache._conn.commit()
		assert cache.get_search({}) is None
		assert cache._conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 0


@pytest.mark.parametrize("drifted", ["2026-01-01T00:00:00Z", "nan", float("inf"), -5])
def test_job_desc_cache_tolerates_drifted_created_at(tmp_path, drifted):
	with _open_cache(tmp_path) as cache:
		cache.put_job_desc("job-1", "synthetic description")
		cache._conn.execute("UPDATE job_desc_cache SET created_at=?", (drifted,))
		cache._conn.commit()
		assert cache.get_job_desc("job-1") is None
		assert cache._conn.execute("SELECT COUNT(*) FROM job_desc_cache").fetchone()[0] == 0


def test_cache_ttl_still_hits_for_fresh_entries(tmp_path):
	with _open_cache(tmp_path) as cache:
		cache.put_search({}, '{"ok": true}')
		cache.put_job_desc("job-1", "synthetic description")
		assert cache.get_search({}) == '{"ok": true}'
		assert cache.get_job_desc("job-1") == "synthetic description"


# ── 2. 风险锁时间戳 ─────────────────────────────────────────────────


@pytest.mark.parametrize("locked_at", [10**400, float("inf"), float("nan"), "yesterday"])
def test_read_lock_tolerates_drifted_locked_at(tmp_path, locked_at):
	path = tmp_path / cdp_risk_lock.LOCK_FILENAME
	path.write_text(json.dumps({"locked_at": locked_at, "reason": "ACCOUNT_RISK"}), encoding="utf-8")

	lock = cdp_risk_lock.read_lock(tmp_path)

	assert lock is not None
	assert lock.locked_at == 0.0
	assert lock.corrupt is False  # 时间未知不等于锁损坏：阻断仍然有效
	status = cdp_risk_lock.lock_status(tmp_path)
	assert status["locked"] is True
	assert "locked_at" not in status


def test_raise_if_locked_keeps_risk_contract_with_drifted_timestamp(tmp_path):
	path = tmp_path / cdp_risk_lock.LOCK_FILENAME
	path.write_text(json.dumps({"locked_at": 10**400, "reason": "ACCOUNT_RISK"}), encoding="utf-8")

	with pytest.raises(PlatformRiskError) as caught:
		cdp_risk_lock.raise_if_locked(tmp_path)

	assert caught.value.code == "ACCOUNT_RISK"


def test_lock_status_keeps_valid_timestamp(tmp_path):
	cdp_risk_lock.write_lock(tmp_path, None, now=1_700_000_000.0, reason="ACCOUNT_RISK", platform_code=36)

	status = cdp_risk_lock.lock_status(tmp_path, now=1_700_000_100.0)

	assert status["locked"] is True
	assert status["locked_at"]
	assert status["age_seconds"] == 100


# ── 3. 刷新 stoken 前的 cookies 校验 ────────────────────────────────


def test_force_refresh_rejects_drifted_cookies_without_refreshing(tmp_path, monkeypatch):
	manager = AuthManager(tmp_path)
	manager._store.load = lambda: {"cookies": [], "stoken": "synthetic-stale"}  # type: ignore[method-assign]
	called: list[tuple] = []
	monkeypatch.setattr(manager_module, "refresh_stoken", lambda *args, **kwargs: called.append(args) or "new")

	with pytest.raises(TokenRefreshFailed) as caught:
		manager.force_refresh()

	assert "cookies" in str(caught.value)
	assert called == []
