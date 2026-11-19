"""审计批次五十：crawl 运行期 Hook 清单读取错误契约（Issue #2127）。

`_read_checksums` 的 `read_bytes_if_regular()` 对目录 / FIFO / 权限错误抛
`OSError`。预检路径一直有 `except OSError` 兜底，运行期注入只捕
`HookRegistrationError`，裸 OSError 会逃逸到 service 的通用 except 被记成
stopped 成功信封（#1553 / #1937）。修复后两条路径统一走 `_manifest_error`。
"""

from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path

import pytest

from boss_agent_cli.crawler.hooks import (
	HOOK_SCRIPT_NAMES,
	HookRegistrationError,
	inject_hook_profile,
	verify_hook_profile,
)


def _write_hooks(root: Path) -> Path:
	root.mkdir(parents=True, exist_ok=True)
	lines = []
	for index, (_, filename) in enumerate(HOOK_SCRIPT_NAMES, 1):
		payload = f"globalThis.hook{index} = true;\n".encode()
		(root / filename).write_bytes(payload)
		lines.append(f"{filename} {sha256(payload).hexdigest()}")
	(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
	return root


def test_directory_manifest_raises_registration_error_on_injection(tmp_path: Path) -> None:
	root = _write_hooks(tmp_path / "hooks")
	(root / "SHA256SUMS").unlink()
	(root / "SHA256SUMS").mkdir()

	with pytest.raises(HookRegistrationError) as exc_info:
		inject_hook_profile(object(), "screenshot-full", root)

	assert exc_info.value.hook_dir == root
	assert "不是普通文件" in str(exc_info.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_manifest_raises_registration_error_on_injection(tmp_path: Path) -> None:
	root = _write_hooks(tmp_path / "hooks")
	(root / "SHA256SUMS").unlink()
	os.mkfifo(root / "SHA256SUMS")

	with pytest.raises(HookRegistrationError) as exc_info:
		inject_hook_profile(object(), "screenshot-full", root)

	assert "不是普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO((root / "SHA256SUMS").lstat().st_mode), "FIFO 不能被替换或删除"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限语义")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 忽略文件权限")
def test_unreadable_manifest_raises_registration_error_on_injection(tmp_path: Path) -> None:
	root = _write_hooks(tmp_path / "hooks")
	manifest = root / "SHA256SUMS"
	manifest.chmod(0o000)
	try:
		with pytest.raises(HookRegistrationError) as exc_info:
			inject_hook_profile(object(), "screenshot-full", root)
	finally:
		manifest.chmod(0o644)

	assert exc_info.value.hook_dir == root


def test_directory_manifest_still_fails_precheck_as_value_error(tmp_path: Path) -> None:
	root = _write_hooks(tmp_path / "hooks")
	(root / "SHA256SUMS").unlink()
	(root / "SHA256SUMS").mkdir()

	with pytest.raises(ValueError) as exc_info:
		verify_hook_profile("screenshot-full", root)

	assert "不是普通文件" in str(exc_info.value)


def test_missing_manifest_message_unchanged_on_injection(tmp_path: Path) -> None:
	root = _write_hooks(tmp_path / "hooks")
	(root / "SHA256SUMS").unlink()

	with pytest.raises(HookRegistrationError) as exc_info:
		inject_hook_profile(object(), "screenshot-full", root)

	assert "缺少 Hook 完整性清单" in str(exc_info.value)
