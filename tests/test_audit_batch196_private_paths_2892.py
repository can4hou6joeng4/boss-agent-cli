"""Issue #2892：私有目录权限收紧与输出路径 UTF-8 守卫的一致性。"""

import errno
import os
import stat
import sys

import pytest

from boss_agent_cli.services.output_path import (
	_FS_REQUIRES_UTF8_NAMES,
	inspect_output_path,
	prepare_output_path,
)
from boss_agent_cli.services.private_files import (
	load_or_create_private_salt,
	tighten_private_directory,
	write_private_bytes,
)


def _mode(path):
	return stat.S_IMODE(path.stat().st_mode)


def test_salt_creation_does_not_chmod_symlink_target(tmp_path):
	target = tmp_path / "shared"
	target.mkdir()
	target.chmod(0o1777)
	link = tmp_path / "auth"
	link.symlink_to(target, target_is_directory=True)

	load_or_create_private_salt(link)

	assert _mode(target) == 0o1777
	target.chmod(0o1777)
	tighten_private_directory(link)
	assert _mode(target) == 0o1777


def test_private_write_creates_private_parent_directories(tmp_path):
	os.umask(0o022)
	target = tmp_path / "a" / "b" / "secret.enc"

	write_private_bytes(target, b"x")

	assert _mode(target) == 0o600
	assert _mode(target.parent) == 0o700
	assert _mode(target.parent.parent) == 0o700


@pytest.mark.skipif(not _FS_REQUIRES_UTF8_NAMES, reason="仅 macOS 文件系统拒绝非 UTF-8 文件名")
def test_prepare_output_path_rejects_non_utf8_name_before_creating_parents(tmp_path):
	target = tmp_path / "u" / "r-\udcff.md"
	assert inspect_output_path(target)[1] is not None

	with pytest.raises(OSError) as excinfo:
		prepare_output_path(target)

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename == str(target)
	assert not (tmp_path / "u").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限语义")
def test_existing_shared_parent_is_not_tightened(tmp_path):
	shared = tmp_path / "shared"
	shared.mkdir()
	shared.chmod(0o1777)

	write_private_bytes(shared / "secret.enc", b"x")

	assert _mode(shared) == 0o1777
