"""风险锁写盘遇到孤立代理字符时不得覆盖原始风控异常（Issue #1487）。

`cdp_url` 直接来自 CLI 参数或 `config.json`，非法 UTF-8 字节会解码出 U+DCFF 这类
孤立代理字符。旧实现用严格 UTF-8 写锁文件，`UnicodeEncodeError` 不是 `OSError`，
既不会触发后备位置，又会在记录风控的分支里替换原始 `PlatformRiskError`，把风控
硬边界降级成可重试的本地错误。锁文件写入改走 `write_json_atomic`（backslashreplace）
后，写盘成功且读回值不变；记录路径同时按 `(OSError, UnicodeError)` fail-closed。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api._base_client import _BaseHttpClient
from boss_agent_cli.api.client import PlatformRiskError

_SURROGATE_URL = "http://127.0.0.1:9222/\udcff"


class _ProbeClient(_BaseHttpClient):
	"""只装配风险锁记录需要的属性，跳过真实构造（不连浏览器、不建预算）。"""

	def __init__(self, data_dir: Path, cdp_url: str | None) -> None:
		self._auth = SimpleNamespace(data_dir=data_dir, _platform="zhipin")
		self._cdp_url = cdp_url
		self._request_budget = None
		self._unpersisted_risk_lock = None


def test_write_lock_accepts_surrogate_cdp_url(tmp_path: Path) -> None:
	"""孤立代理字符按 \\udcff 转义写出，读回仍是同一个字符串。"""
	lock = cdp_risk_lock.write_lock(tmp_path, None, cdp_url=_SURROGATE_URL, reason="ACCOUNT_RISK", platform_code=36)

	assert lock.cdp_url == _SURROGATE_URL
	raw = (tmp_path / cdp_risk_lock.LOCK_FILENAME).read_text(encoding="utf-8")
	assert "\\udcff" in raw
	loaded = cdp_risk_lock.read_lock(tmp_path)
	assert loaded is not None
	assert loaded.corrupt is False
	assert loaded.reason == "ACCOUNT_RISK"
	assert loaded.cdp_url == _SURROGATE_URL


def test_record_risk_lock_keeps_original_error_with_surrogate_cdp_url(tmp_path: Path) -> None:
	"""记录风控时锁写盘成功，原始 PlatformRiskError 不被替换，details 标记已持久化。"""
	client = _ProbeClient(tmp_path, _SURROGATE_URL)
	error = PlatformRiskError("ACCOUNT_RISK", details={"platform_code": 36})

	client._record_cdp_risk_lock(None, error=error)

	assert error.code == "ACCOUNT_RISK"
	assert error.details["risk_lock"]["persisted"] is True
	assert client._unpersisted_risk_lock is None
	assert cdp_risk_lock.read_lock(tmp_path) is not None


def test_record_risk_lock_fails_closed_when_every_write_fails(tmp_path: Path, monkeypatch) -> None:
	"""两处锁位置都写不进去时保留进程内锁，并标记 persisted=false，而不是抛出。"""
	client = _ProbeClient(tmp_path, "http://127.0.0.1:9222")
	error = PlatformRiskError("ACCOUNT_RISK", details={"platform_code": 36})

	def _boom(*_args, **_kwargs):
		raise UnicodeError("surrogates not allowed")

	monkeypatch.setattr(cdp_risk_lock, "write_json_atomic", _boom)

	client._record_cdp_risk_lock(None, error=error)

	assert error.code == "ACCOUNT_RISK"
	assert error.details["risk_lock"]["persisted"] is False
	assert client._unpersisted_risk_lock is not None
