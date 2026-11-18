"""模型导入的原子替换与并发锁回归测试（Issue #1705）。

旧实现先 rmtree 再 copytree：重导入复制失败会永久丢失旧副本，且登记表仍
指向它；目标为符号链接时还会写穿到数据目录外。
"""

from __future__ import annotations

import errno
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.ai.local_models import (
	LocalModelManifestError,
	import_local_model,
	model_registry_path,
)
from boss_agent_cli.services.file_lock import exclusive_file_lock


def _read_registry(data_dir: Path) -> list[dict[str, Any]]:
	return json.loads(model_registry_path(data_dir).read_text(encoding="utf-8"))


def _seed_dir_import(tmp_path: Path) -> tuple[Path, Path, Path]:
	data_dir = tmp_path / "data"
	source = tmp_path / "artifact"
	source.mkdir()
	(source / "weights.bin").write_text("v1", encoding="utf-8")
	imported = import_local_model(data_dir, source, "demo")
	return data_dir, source, Path(imported.path)


def _seed_file_import(tmp_path: Path) -> tuple[Path, Path, Path]:
	data_dir = tmp_path / "data"
	source = tmp_path / "weights.bin"
	source.write_text("v1", encoding="utf-8")
	imported = import_local_model(data_dir, source, "demo")
	return data_dir, source, Path(imported.path)


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def test_failed_directory_copy_preserves_old_copy_and_registry(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	data_dir, source, target = _seed_dir_import(tmp_path)
	registry_before = model_registry_path(data_dir).read_bytes()
	(source / "weights.bin").write_text("v2", encoding="utf-8")

	def copytree_fails(*args: Any, **kwargs: Any) -> None:
		raise OSError("disk full")

	monkeypatch.setattr(shutil, "copytree", copytree_fails)

	with pytest.raises(OSError):
		import_local_model(data_dir, source, "demo")

	assert (target / "weights.bin").read_text(encoding="utf-8") == "v1"
	assert model_registry_path(data_dir).read_bytes() == registry_before
	assert [entry.name for entry in target.parent.iterdir() if ".staging-" in entry.name] == []


def test_failed_file_copy_preserves_old_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir, source, target = _seed_file_import(tmp_path)
	source.write_text("v2", encoding="utf-8")

	def copy2_fails(*args: Any, **kwargs: Any) -> None:
		raise OSError("disk full")

	monkeypatch.setattr(shutil, "copy2", copy2_fails)

	with pytest.raises(OSError):
		import_local_model(data_dir, source, "demo")

	assert target.read_text(encoding="utf-8") == "v1"


def test_dangling_symlink_target_is_rejected_without_writing_outside(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	source = tmp_path / "weights.bin"
	source.write_text("payload", encoding="utf-8")
	target_dir = data_dir / "models" / "demo"
	target_dir.mkdir(parents=True)
	target = target_dir / "weights.bin"
	outside = tmp_path / "outside.bin"
	target.symlink_to(outside)

	with pytest.raises(OSError):
		import_local_model(data_dir, source, "demo")

	assert not outside.exists()
	assert target.is_symlink()
	assert not model_registry_path(data_dir).exists()


def test_busy_import_lock_fails_without_registering(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	source = tmp_path / "weights.bin"
	source.write_text("payload", encoding="utf-8")
	models_dir = data_dir / "models"
	models_dir.mkdir(parents=True)

	with exclusive_file_lock(models_dir / "import.lock") as acquired:
		assert acquired
		with pytest.raises(OSError) as caught:
			import_local_model(data_dir, source, "demo")

	assert caught.value.errno == errno.EAGAIN
	assert not model_registry_path(data_dir).exists()


def test_source_metadata_error_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	source = tmp_path / "weights.bin"
	source.write_text("payload", encoding="utf-8")
	_fail_lstat_for(source, monkeypatch)

	with pytest.raises(PermissionError):
		import_local_model(tmp_path / "data", source, "demo")


def test_fifo_source_is_rejected_without_reading(tmp_path: Path) -> None:
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	source = tmp_path / "weights.fifo"
	os.mkfifo(source)

	with pytest.raises(LocalModelManifestError) as caught:
		import_local_model(tmp_path / "data", source, "demo")

	assert caught.value.code == "INVALID_PARAM"


def test_missing_source_reports_not_found(tmp_path: Path) -> None:
	with pytest.raises(LocalModelManifestError) as caught:
		import_local_model(tmp_path / "data", tmp_path / "missing", "demo")

	assert caught.value.code == "MODEL_SOURCE_NOT_FOUND"


def test_target_metadata_error_preserves_old_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir, source, target = _seed_dir_import(tmp_path)
	(source / "weights.bin").write_text("v2", encoding="utf-8")
	_fail_lstat_for(target, monkeypatch)

	with pytest.raises(PermissionError):
		import_local_model(data_dir, source, "demo")

	assert (target / "weights.bin").read_text(encoding="utf-8") == "v1"
	assert [entry.name for entry in target.parent.iterdir() if ".staging-" in entry.name] == []
