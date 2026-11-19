"""风控锁摘要、请求租约与代理解析的边界（Issue #1918–#1921）。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError, parse_proxy_url
from boss_agent_cli.api.request_budget import RequestBudget, RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthManager


# ── Issue #1918：孤立代理字符不能让风控异常被替换 ─────────────────────


def test_hash_stoken_accepts_lone_surrogate() -> None:
	digest = cdp_risk_lock.hash_stoken("\udcff")

	assert len(digest) == 64
	assert digest == cdp_risk_lock.hash_stoken("\udcff")
	assert digest != cdp_risk_lock.hash_stoken("abc")


def test_stoken_hash_from_cookies_accepts_lone_surrogate() -> None:
	digest = cdp_risk_lock.stoken_hash_from_cookies(
		[{"name": "__zp_stoken__", "value": "\udcff", "domain": ".zhipin.com"}]
	)

	assert len(digest) == 64


def test_risk_lock_recording_survives_surrogate_stoken(tmp_path) -> None:
	manager = object.__new__(AuthManager)
	manager._platform = "zhipin"
	manager._token = {"stoken": "\udcff"}
	manager._data_dir = tmp_path
	manager._unpersisted_risk_lock = None
	manager._cdp_url = None
	error = AccountRiskError("synthetic risk", details={"platform_code": 36})

	manager._record_cdp_risk_lock(None, error=error)

	assert error.details["risk_lock"]["locked"] is True
	assert cdp_risk_lock.read_lock(tmp_path) is not None


# ── Issue #1919：租约文件被删除后仍保持互斥 ──────────────────────────


def test_lease_identity_detects_replaced_file(tmp_path) -> None:
	path = tmp_path / "request-budget.lease"
	handle = path.open("a+b")
	try:
		assert RequestBudget._lease_names_locked_file(path, handle) is True
		path.unlink()
		path.write_bytes(b"\0")
		assert RequestBudget._lease_names_locked_file(path, handle) is False
	finally:
		handle.close()


def test_unlinked_lease_file_does_not_admit_second_holder(tmp_path) -> None:
	first = RequestBudget(tmp_path, interval=0, max_per_minute=20, wait_timeout=0.4)
	second = RequestBudget(tmp_path, interval=0, max_per_minute=20, wait_timeout=0.4)
	entered = threading.Event()
	release = threading.Event()
	outcome: dict[str, object] = {}

	def holder() -> None:
		with first.attempt():
			entered.set()
			release.wait(5)

	thread = threading.Thread(target=holder)
	thread.start()
	assert entered.wait(5)
	(tmp_path / "request-budget.lease").unlink()

	started = time.monotonic()
	try:
		with second.attempt():
			outcome["entered"] = True
	except RequestBudgetExceeded:
		outcome["entered"] = False
	outcome["elapsed"] = time.monotonic() - started

	release.set()
	thread.join()

	assert outcome["entered"] is False
	assert outcome["elapsed"] >= 0.3


# ── Issue #1920：非法 scheme / host 按参数错误拒绝 ────────────────────


@pytest.mark.parametrize(
	"raw",
	[
		"ftp://proxy.test:21",
		"file://proxy.test",
		"javascript://proxy.test:8080",
		"socks5h://proxy.test:1080",
		"http://ho st:8080",
	],
)
def test_parse_proxy_url_rejects_unusable_values(raw: str) -> None:
	with pytest.raises(ProxyConfigurationError):
		parse_proxy_url(raw)


@pytest.mark.parametrize("scheme", ["http", "https", "socks4", "socks5"])
def test_parse_proxy_url_accepts_supported_schemes(scheme: str) -> None:
	assert parse_proxy_url(f"{scheme}://127.0.0.1:1080") == {"server": f"{scheme}://127.0.0.1:1080"}


def test_parse_proxy_url_keeps_ipv6_hosts() -> None:
	assert parse_proxy_url("http://[::1]:8080") == {"server": "http://[::1]:8080"}


# ── Issue #1921：任一处写成功即算已持久化 ────────────────────────────


def _budget(tmp_path) -> RequestBudget:
	data_dir = tmp_path / "data"
	shared = tmp_path / "shared"
	data_dir.mkdir()
	shared.mkdir()
	return RequestBudget(data_dir, root=shared)


def test_record_risk_clears_latch_when_one_location_persists(tmp_path, monkeypatch) -> None:
	budget = _budget(tmp_path)
	shared = budget.root
	original = cdp_risk_lock.write_lock

	def fail_shared(directory, *args, **kwargs):
		if Path(directory) == shared:
			raise OSError(28, "No space left on device")
		return original(directory, *args, **kwargs)

	monkeypatch.setattr(cdp_risk_lock, "write_lock", fail_shared)

	budget.record_risk(reason="ACCOUNT_RISK", platform_code=36)

	assert budget._unpersisted_risk_lock is None
	assert cdp_risk_lock.read_lock(budget.data_dir) is not None


def test_record_risk_raises_and_keeps_latch_when_every_location_fails(tmp_path, monkeypatch) -> None:
	budget = _budget(tmp_path)

	def always_fail(*args, **kwargs):
		raise OSError(28, "No space left on device")

	monkeypatch.setattr(cdp_risk_lock, "write_lock", always_fail)

	with pytest.raises(OSError):
		budget.record_risk(reason="ACCOUNT_RISK", platform_code=36)

	assert budget._unpersisted_risk_lock is not None
