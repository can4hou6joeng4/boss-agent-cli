"""Issue #3080：风控锁写入后的 stale 清理不能顺着符号链接删掉刚写入的锁。

`data_dir/cache` 是符号链接（如指向 ``.``）时，主位置与后备位置字面不同却
解析到同一个文件。`write_lock` 先写主锁、再把后备路径当作「另一位置」
``unlink``，实际删掉的是刚写入的主锁；函数仍返回成功，调用方记
``persisted=true``，新进程读到「无锁」，风控硬边界 fail-open。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import PlatformRiskError


def _write_account_risk(data_dir: Path) -> cdp_risk_lock.RiskLock:
	return cdp_risk_lock.write_lock(data_dir, None, reason="ACCOUNT_RISK", platform_code=36)


@pytest.mark.skipif(os.name == "nt", reason="Windows 不能创建目录符号链接（需特权）")
def test_write_lock_survives_fallback_dir_symlink_to_self(tmp_path) -> None:
	"""cache -> . 时写入的主锁不能被「清理另一位置」删除。"""
	os.symlink(".", tmp_path / "cache")

	lock = _write_account_risk(tmp_path)

	assert lock.reason == "ACCOUNT_RISK"
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).is_file()
	read = cdp_risk_lock.read_lock(tmp_path)
	assert read is not None and read.reason == "ACCOUNT_RISK"
	with pytest.raises(PlatformRiskError) as captured:
		cdp_risk_lock.raise_if_locked(tmp_path)
	assert captured.value.code == "ACCOUNT_RISK"
	assert cdp_risk_lock.lock_status(tmp_path)["locked"] is True


@pytest.mark.skipif(os.name == "nt", reason="Windows 不能创建目录符号链接（需特权）")
def test_write_lock_survives_fallback_dir_symlink_absolute(tmp_path) -> None:
	"""cache 指向数据目录自身（绝对路径链接）时同样不能自删。"""
	os.symlink(str(tmp_path), tmp_path / "cache")

	_write_account_risk(tmp_path)

	assert cdp_risk_lock.read_lock(tmp_path) is not None


def test_stale_fallback_lock_still_cleared_in_normal_layout(tmp_path) -> None:
	"""修复不能把正常布局下的陈旧后备锁清理一并跳过。"""
	fallback = tmp_path / cdp_risk_lock.FALLBACK_LOCK_DIR / cdp_risk_lock.LOCK_FILENAME
	fallback.parent.mkdir(parents=True)
	cdp_risk_lock.write_json_atomic(fallback, {"reason": "ENVIRONMENT_RISK", "locked_at": 1.0}, indent=None)

	_write_account_risk(tmp_path)

	assert not fallback.exists(), "写入主锁后陈旧后备锁应被清理"
	assert cdp_risk_lock.read_lock(tmp_path).reason == "ACCOUNT_RISK"


@pytest.mark.skipif(os.name == "nt", reason="Windows 不能创建符号链接（需特权）")
def test_dangling_stale_symlink_is_unlinked(tmp_path) -> None:
	"""悬空链接不可能指向刚写入的锁：仍按原语义删除链接本身。"""
	fallback = tmp_path / cdp_risk_lock.FALLBACK_LOCK_DIR / cdp_risk_lock.LOCK_FILENAME
	fallback.parent.mkdir(parents=True)
	os.symlink(str(tmp_path / "missing-target.json"), fallback)

	_write_account_risk(tmp_path)

	assert not fallback.is_symlink(), "悬空链接的陈旧锁应被删除"
	assert cdp_risk_lock.read_lock(tmp_path).reason == "ACCOUNT_RISK"
