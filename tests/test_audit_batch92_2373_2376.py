"""批次 92 审计修复：NUL 路径归一与异常文本边界（Issue #2373–#2376）。"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from boss_agent_cli.services.candidate_actions import candidate_action_provably_unsent
from boss_agent_cli.services.file_lock import exclusive_file_lock
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.services.private_files import (
	load_or_create_private_salt,
	tighten_private_directory,
	tighten_private_file,
)


# ── Issue #2373：file_lock 的 NUL 路径归一为带 filename 的 OSError ──


@pytest.mark.parametrize("name", ["a\x00b/x.lock", "\x00.lock"], ids=["parent-nul", "name-nul"])
def test_file_lock_nul_path_raises_oserror(tmp_path: Path, name: str) -> None:
	path = tmp_path / name

	with pytest.raises(OSError) as excinfo:
		with exclusive_file_lock(path):
			pass

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename == str(path)


# ── Issue #2374：private_files 的 NUL 路径与权限收紧 ─────────────────


def test_salt_nul_directory_raises_oserror(tmp_path: Path) -> None:
	auth_dir = tmp_path / "a\x00b"

	with pytest.raises(OSError) as excinfo:
		load_or_create_private_salt(auth_dir)

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename == str(auth_dir)


def test_tighten_helpers_ignore_invalid_paths() -> None:
	# 「尽力收紧、读不到就跳过」：NUL 路径的 lstat/stat 抛 ValueError 也不能冒泡。
	assert tighten_private_file(Path("\x00")) is None
	assert tighten_private_directory(Path("\x00")) is None


# ── Issue #2375：prepare_output_path 的 NUL 父路径 ──────────────────


def test_prepare_output_path_nul_parent_raises_oserror(tmp_path: Path) -> None:
	target = tmp_path / "a\x00b" / "out.txt"

	with pytest.raises(OSError) as excinfo:
		prepare_output_path(target)

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename == str(target)


# ── Issue #2376：异常自身无法转成文本时 fail-open ───────────────────


def test_unstringifiable_exception_is_not_provably_unsent() -> None:
	# 携带超长整数的异常在 str() 处抛 ValueError：不能证明写请求未发出，
	# 保留原始异常、不释放预约（Issue #2376）。
	assert candidate_action_provably_unsent(ValueError(10**5000)) is False


def test_kernel_missing_text_still_recognized() -> None:
	# 回归：普通字符串异常仍走内核缺失识别。
	assert candidate_action_provably_unsent(RuntimeError("Executable doesn't exist at ...")) is True
