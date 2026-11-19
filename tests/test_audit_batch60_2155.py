"""符号链接环让 resolve() 抛 RuntimeError 时的风控记录与状态查询边界（Issue #2155）。

``Path.resolve()`` 在 3.11 / 3.12 遇到符号链接环抛 ``RuntimeError``，3.13 起改为
返回原路径。解释器行为不一致，所以这里按路径 monkeypatch 注入 ``RuntimeError``，
断言与解释器版本无关。
"""

from __future__ import annotations

import errno
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from boss_agent_cli.api._base_client import _BaseHttpClient
from boss_agent_cli.api.client import AccountRiskError, PlatformRiskError
from boss_agent_cli.api.request_budget import budget_directory, risk_lock_status
from boss_agent_cli.auth.manager import AuthManager


def _loop_resolve(monkeypatch: pytest.MonkeyPatch, looped: Path) -> None:
	"""只让 ``looped`` 这一条路径的 resolve() 抛 RuntimeError，其余保持原行为。"""
	original = Path.resolve
	target = str(looped)

	def fake(self: Path, *args, **kwargs):
		if str(self) == target:
			raise RuntimeError(f"Symlink loop from '{self}'")
		return original(self, *args, **kwargs)

	monkeypatch.setattr(Path, "resolve", fake)


def _data_dir_with_group(tmp_path: Path) -> tuple[Path, Path]:
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	group = tmp_path / "group"
	group.mkdir()
	(data_dir / "config.json").write_text(json.dumps({"request_budget_dir": str(group)}), encoding="utf-8")
	return data_dir, group


def _risk_error() -> AccountRiskError:
	return AccountRiskError("synthetic risk", details={"platform_code": 36})


class _ProbeClient(_BaseHttpClient):
	"""只装配风险锁记录需要的属性，跳过真实构造（不连浏览器）。"""

	def __init__(self, data_dir: Path, budget: object | None) -> None:
		self._auth = SimpleNamespace(data_dir=data_dir, _platform="zhipin")
		self._cdp_url = None
		self._request_budget = budget
		self._unpersisted_risk_lock = None


def test_budget_directory_converts_resolve_loop_to_local_io_error(tmp_path, monkeypatch) -> None:
	"""resolve() 的 RuntimeError 归一成带 filename 的 OSError，状态命令不再 traceback。"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	_loop_resolve(monkeypatch, data_dir)

	with pytest.raises(OSError) as excinfo:
		budget_directory(data_dir)

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename == str(data_dir)
	assert "Symlink loop" in str(excinfo.value)


def test_risk_lock_recording_survives_resolve_loop(tmp_path, monkeypatch) -> None:
	"""记录风控时 resolve() 抛错不得替换正在传播的 PlatformRiskError。"""
	data_dir, _group = _data_dir_with_group(tmp_path)
	_loop_resolve(monkeypatch, data_dir)
	auth = AuthManager(data_dir)
	error = _risk_error()

	auth._record_cdp_risk_lock(None, error=error)

	assert isinstance(error, PlatformRiskError)
	details = error.details["risk_lock"]
	assert details["locked"] is True
	assert details["reason"] == "ACCOUNT_RISK"


def test_login_lock_check_survives_resolve_loop(tmp_path, monkeypatch) -> None:
	"""登录 / 刷新前的查锁入口在 data_dir 含符号链接环时按后备目录继续检查。"""
	data_dir, _group = _data_dir_with_group(tmp_path)
	_loop_resolve(monkeypatch, data_dir)
	auth = AuthManager(data_dir)

	auth._check_cdp_login_risk_lock(None)


def test_risk_lock_status_survives_resolve_loop(tmp_path, monkeypatch) -> None:
	"""status / doctor 的风险锁状态查询在 data_dir 含符号链接环时报告后备目录状态。"""
	data_dir, group = _data_dir_with_group(tmp_path)
	_loop_resolve(monkeypatch, data_dir)

	status = risk_lock_status(data_dir)

	assert status["group_path"] == str(group.resolve())
	assert status["group_locked"] is False


def test_client_risk_recording_records_budget_risk_on_resolve_loop(tmp_path, monkeypatch) -> None:
	"""客户端记录风控遇到 resolve() 抛错时仍把风控写进预算目录（fail-closed）。"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	_loop_resolve(monkeypatch, data_dir)
	recorded: list[dict[str, object]] = []
	budget = SimpleNamespace(
		root=tmp_path / "group",
		record_risk=lambda **kwargs: recorded.append(kwargs),
	)
	client = _ProbeClient(data_dir, budget)
	error = _risk_error()

	client._record_cdp_risk_lock(None, error=error)

	assert isinstance(error, PlatformRiskError)
	assert error.details["risk_lock"]["locked"] is True
	assert recorded == [{"reason": "ACCOUNT_RISK", "platform_code": 36}]
