"""本地模型登记表路径状态的 fail-closed 回归测试（Issue #1700）。

``strict=True`` 的写入方不得把「读不出来」当成空登记表，否则 import 会用
空表重写 `models/registry.json`，丢掉其他仍有效的已导入模型。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.ai.local_models import (
	LocalModelManifestError,
	import_local_model,
	model_registry_path,
	read_imported_models,
)


def _write_registry(data_dir: Path, payload: str) -> Path:
	path = model_registry_path(data_dir)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(payload, encoding="utf-8")
	return path


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def test_strict_metadata_error_reports_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	registry = _write_registry(tmp_path, '[{"model": "a", "path": "/a", "runtime": "custom"}]')
	original = registry.read_bytes()
	_fail_lstat_for(registry, monkeypatch)

	with pytest.raises(LocalModelManifestError) as caught:
		read_imported_models(tmp_path, strict=True)

	assert caught.value.code == "MODEL_REGISTRY_INVALID"
	assert registry.read_bytes() == original


def test_non_strict_metadata_error_still_degrades_to_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	registry = _write_registry(tmp_path, "[]")
	_fail_lstat_for(registry, monkeypatch)

	assert read_imported_models(tmp_path) == []


def test_strict_directory_registry_reports_invalid(tmp_path: Path) -> None:
	registry = model_registry_path(tmp_path)
	registry.mkdir(parents=True)

	with pytest.raises(LocalModelManifestError) as caught:
		read_imported_models(tmp_path, strict=True)

	assert caught.value.code == "MODEL_REGISTRY_INVALID"
	assert "无法解析" in str(caught.value)
	assert registry.is_dir()


def test_strict_dangling_symlink_reports_invalid(tmp_path: Path) -> None:
	registry = model_registry_path(tmp_path)
	registry.parent.mkdir(parents=True)
	registry.symlink_to(tmp_path / "missing-registry.json")

	with pytest.raises(LocalModelManifestError) as caught:
		read_imported_models(tmp_path, strict=True)

	assert caught.value.code == "MODEL_REGISTRY_INVALID"
	assert registry.is_symlink()
	assert not (tmp_path / "missing-registry.json").exists()


def test_strict_unreadable_registry_reports_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	registry = _write_registry(tmp_path, "[]")
	real_read_bytes = Path.read_bytes

	def read_bytes_fails(self: Path) -> bytes:
		if self == registry:
			raise PermissionError("permission denied")
		return real_read_bytes(self)

	monkeypatch.setattr(Path, "read_bytes", read_bytes_fails)

	with pytest.raises(LocalModelManifestError) as caught:
		read_imported_models(tmp_path, strict=True)

	assert caught.value.code == "MODEL_REGISTRY_INVALID"
	assert real_read_bytes(registry) == b"[]"


def test_strict_missing_registry_is_empty(tmp_path: Path) -> None:
	assert read_imported_models(tmp_path, strict=True) == []


def test_strict_corrupt_registry_still_reports_invalid(tmp_path: Path) -> None:
	_write_registry(tmp_path, "not valid json")

	with pytest.raises(LocalModelManifestError) as caught:
		read_imported_models(tmp_path, strict=True)

	assert caught.value.code == "MODEL_REGISTRY_INVALID"


def test_import_does_not_overwrite_registry_when_metadata_unreadable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir = tmp_path / "data"
	registry = _write_registry(data_dir, '[{"model": "keep", "path": "/keep", "runtime": "custom"}]')
	original = registry.read_bytes()
	source = tmp_path / "weights.bin"
	source.write_bytes(b"offline")
	_fail_lstat_for(registry, monkeypatch)

	with pytest.raises(LocalModelManifestError) as caught:
		import_local_model(data_dir, source, "new-model")

	assert caught.value.code == "MODEL_REGISTRY_INVALID"
	assert registry.read_bytes() == original
	assert not (data_dir / "models" / "new-model").exists()
