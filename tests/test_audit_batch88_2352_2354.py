"""批次 88 审计修复：数值校验溢出、NUL 路径与预算目录符号链接环（Issue #2352–#2354）。"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from boss_agent_cli.api.request_budget import RequestBudget, validate_delay
from boss_agent_cli.services.json_files import read_bytes_if_regular


# ── Issue #2352：超大数值必须以 ValueError 拒绝 ─────────────────────


@pytest.mark.parametrize(
	"call",
	[
		pytest.param(lambda tmp: validate_delay((10**400, 10**400)), id="validate_delay"),
		pytest.param(lambda tmp: RequestBudget(tmp, interval=10**400), id="interval"),
		pytest.param(lambda tmp: RequestBudget(tmp, wait_timeout=10**400), id="wait_timeout"),
		pytest.param(lambda tmp: RequestBudget(tmp).cooldown(10**400), id="cooldown"),
	],
)
def test_oversized_numbers_raise_value_error(tmp_path: Path, call) -> None:
	with pytest.raises(ValueError):
		call(tmp_path)


def test_normal_numbers_still_accepted(tmp_path: Path) -> None:
	validate_delay((0.5, 1.5))
	RequestBudget(tmp_path, interval=1.5, wait_timeout=30.0).cooldown(2.0)


# ── Issue #2353：含 NUL 的读路径归一为带 filename 的 OSError ─────────


def test_read_bytes_if_regular_nul_path_raises_oserror_with_filename() -> None:
	path = Path("/tmp/a\x00b")

	with pytest.raises(OSError) as excinfo:
		read_bytes_if_regular(path)

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename == str(path)


def test_read_bytes_if_regular_missing_path_still_returns_none(tmp_path: Path) -> None:
	assert read_bytes_if_regular(tmp_path / "missing.json") is None


# ── Issue #2354：预算目录符号链接环归一为 OSError ───────────────────


def test_request_budget_symlink_loop_raises_oserror(tmp_path: Path) -> None:
	loop = tmp_path / "loop"
	os.symlink(loop, loop)

	# 3.11 / 3.12 的 Path.resolve() 对符号链接环抛 RuntimeError；3.13 起改为
	# 返回原路径。两种版本都不能让裸 RuntimeError 穿出，3.13+ 构造成功后
	# 保留环路径即可。
	try:
		budget = RequestBudget(loop, root=tmp_path)
	except OSError as exc:
		assert exc.errno == errno.EINVAL
		assert exc.filename == str(loop)
	else:
		assert sys.version_info >= (3, 13)
		assert budget.data_dir == loop


def test_request_budget_resolves_normal_directory(tmp_path: Path) -> None:
	budget = RequestBudget(tmp_path)

	assert budget.data_dir == tmp_path.resolve()
	assert budget.root == tmp_path.resolve()
