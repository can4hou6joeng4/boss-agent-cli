"""风控锁记录与机器 ID 解析的边界（Issue #1892 #1893 #1894）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import AccountRiskError, PlatformRiskError
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.auth.token_store import TokenStore


def _risk_error() -> AccountRiskError:
	return AccountRiskError("synthetic risk", details={"platform_code": 36})


def _write_config(data_dir: Path, payload: str) -> None:
	data_dir.mkdir(parents=True, exist_ok=True)
	(data_dir / "config.json").write_text(payload, encoding="utf-8")


# ── Issue #1892：非法配置不能替换 PlatformRiskError ─────────────────


@pytest.mark.parametrize("config", ['{"request_budget_dir": 123}', "{not json", ""])
def test_risky_lock_recording_survives_broken_config(tmp_path, monkeypatch, config: str) -> None:
	_write_config(tmp_path, config)

	def _always_fail(*_args, **_kwargs):
		raise OSError(28, "No space left on device")

	monkeypatch.setattr(cdp_risk_lock, "write_lock", _always_fail)
	auth = AuthManager(tmp_path)
	error = _risk_error()

	# 只记录、不抛：异常不得替换正在传播的风控异常。
	auth._record_cdp_risk_lock(None, error=error)

	details = error.details["risk_lock"]
	assert details["locked"] is True
	assert details["persisted"] is False
	assert isinstance(error, PlatformRiskError)


def test_risky_lock_recording_keeps_details_on_invalid_budget_dir(tmp_path, monkeypatch) -> None:
	"""后备目录非法时仍要写入 risk_lock.details（旧实现直接跳过）。"""
	_write_config(tmp_path, '{"request_budget_dir": "relative/dir"}')
	auth = AuthManager(tmp_path)
	error = _risk_error()

	auth._record_cdp_risk_lock(None, error=error)

	assert error.details["risk_lock"]["persisted"] is True
	assert error.details["risk_lock"]["reason"] == "ACCOUNT_RISK"


# ── Issue #1893：任一位置写成功即算已持久化 ─────────────────────────


def test_fallback_location_persisted_reports_true(tmp_path, monkeypatch) -> None:
	budget = tmp_path / "budget"
	_write_config(tmp_path, json.dumps({"request_budget_dir": str(budget)}))
	data_dir = tmp_path
	original = cdp_risk_lock.write_lock

	def _fail_main(target, *args, **kwargs):
		if Path(target) == data_dir:
			raise OSError(28, "No space left on device")
		return original(target, *args, **kwargs)

	monkeypatch.setattr(cdp_risk_lock, "write_lock", _fail_main)
	auth = AuthManager(tmp_path)
	error = _risk_error()

	auth._record_cdp_risk_lock(None, error=error)

	assert error.details["risk_lock"]["persisted"] is True
	assert auth._unpersisted_risk_lock is None
	assert cdp_risk_lock.read_lock(budget) is not None


def test_both_locations_failing_keeps_in_memory_lock(tmp_path, monkeypatch) -> None:
	_write_config(tmp_path, "{}")

	def _always_fail(*_args, **_kwargs):
		raise OSError(28, "No space left on device")

	monkeypatch.setattr(cdp_risk_lock, "write_lock", _always_fail)
	auth = AuthManager(tmp_path)
	error = _risk_error()

	auth._record_cdp_risk_lock(None, error=error)

	assert error.details["risk_lock"]["persisted"] is False
	assert auth._unpersisted_risk_lock is not None


# ── Issue #1894：ioreg / reg 输出格式漂移落到指纹兜底 ────────────────


def _machine_id(monkeypatch, *, system: str, stdout: str) -> str:
	with patch("boss_agent_cli.auth.token_store.platform.system", return_value=system), patch(
		"boss_agent_cli.auth.token_store.shutil.which", return_value="/usr/bin/tool"
	), patch(
		"boss_agent_cli.auth.token_store.subprocess.run",
		return_value=SimpleNamespace(stdout=stdout, returncode=0),
	):
		return TokenStore(Path(_machine_id.tmp))._get_machine_id()


@pytest.fixture()
def machine_id(tmp_path, monkeypatch):
	_machine_id.tmp = tmp_path  # type: ignore[attr-defined]
	monkeypatch.delenv("BOSS_AGENT_MACHINE_ID", raising=False)
	return lambda **kwargs: _machine_id(monkeypatch, **kwargs)


def test_ioreg_normal_output_is_parsed(machine_id) -> None:
	stdout = '    "IOPlatformUUID" = "12345678-1234-1234-1234-1234567890AB"\n'

	assert machine_id(system="Darwin", stdout=stdout) == "12345678-1234-1234-1234-1234567890AB"


def test_ioreg_malformed_output_falls_back(machine_id) -> None:
	value = machine_id(system="Darwin", stdout="IOPlatformUUID\n")

	assert value


def test_reg_normal_output_is_parsed(machine_id) -> None:
	stdout = "    MachineGuid    REG_SZ    12345678-1234-1234-1234-1234567890AB\n\n"

	assert machine_id(system="Windows", stdout=stdout) == "12345678-1234-1234-1234-1234567890AB"


def test_reg_malformed_output_falls_back(machine_id) -> None:
	value = machine_id(system="Windows", stdout="MachineGuid\n")

	assert value
	assert value not in {"MachineGuid", "REG_SZ"}
