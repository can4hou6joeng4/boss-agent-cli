"""本地序列化失败的错误分类（Issue #2078）。"""

from __future__ import annotations

import errno

import pytest

from boss_agent_cli.output import is_local_io_error
from boss_agent_cli.services import json_files
from boss_agent_cli.services.json_files import strict_json_text, write_json_atomic


def test_unlabelled_einval_is_local_io() -> None:
	"""无 filename 的 EINVAL 只来自本地序列化失败，不能被当成可重试网络错误。"""
	assert is_local_io_error(OSError(errno.EINVAL, "本地状态无法序列化")) is True
	assert is_local_io_error(OSError(errno.EINVAL, "本地状态无法序列化", "/tmp/target.json")) is True


def test_network_errors_stay_retryable() -> None:
	assert is_local_io_error(OSError(errno.ECONNREFUSED, "refused")) is False
	assert is_local_io_error(ConnectionRefusedError(errno.ECONNREFUSED, "refused")) is False
	assert is_local_io_error(OSError(errno.ENOSPC, "no space")) is True


def test_strict_json_text_recursion_failure_is_local(monkeypatch) -> None:
	def boom(*args, **kwargs):
		raise RecursionError("maximum recursion depth exceeded")

	monkeypatch.setattr(json_files.json, "dumps", boom)

	with pytest.raises(OSError) as excinfo:
		strict_json_text({"items": []})

	assert excinfo.value.errno == errno.EINVAL
	assert excinfo.value.filename is None
	assert is_local_io_error(excinfo.value) is True


def test_write_json_atomic_recursion_failure_is_local(tmp_path, monkeypatch) -> None:
	def boom(*args, **kwargs):
		raise RecursionError("maximum recursion depth exceeded")

	monkeypatch.setattr(json_files.json, "dump", boom)
	target = tmp_path / "state.json"

	with pytest.raises(OSError) as excinfo:
		write_json_atomic(target, {"items": []})

	assert is_local_io_error(excinfo.value) is True
	assert not target.exists()
	assert list(tmp_path.iterdir()) == []
