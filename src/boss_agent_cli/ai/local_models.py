"""Local model manifests and filesystem registry helpers."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from boss_agent_cli.services.json_files import write_json_atomic

RUNTIME_BASE_URLS: Final = {
	"ollama": "http://localhost:11434/v1",
	"vllm": "http://localhost:8000/v1",
}


@dataclass(frozen=True, slots=True)
class LocalModelManifest:
	name: str
	runtime: str
	license: str
	min_memory_gb: int
	description: str = ""
	recommended: bool = False


@dataclass(frozen=True, slots=True)
class ImportedLocalModel:
	model: str
	path: str
	runtime: str


class LocalModelManifestError(Exception):
	"""Raised when a local model manifest is not safe to register."""

	def __init__(self, code: str, message: str) -> None:
		super().__init__(message)
		self.code = code
		self.message = message


RECOMMENDED_MODELS: Final = (
	LocalModelManifest(
		name="qwen3:14b",
		runtime="ollama",
		license="Apache-2.0",
		min_memory_gb=16,
		description="默认推荐；招聘短回复质量与本地部署成本较均衡。",
		recommended=True,
	),
	LocalModelManifest(
		name="qwen3:8b",
		runtime="ollama",
		license="Apache-2.0",
		min_memory_gb=8,
		description="低配机器降级选项；建议配合人审。",
	),
	LocalModelManifest(
		name="qwen3:32b",
		runtime="ollama",
		license="Apache-2.0",
		min_memory_gb=32,
		description="高配 GPU/内存机器选项；回复质量更稳。",
	),
)


def recommended_model_rows() -> list[dict[str, Any]]:
	"""Return built-in local model manifests as JSON rows."""
	return [asdict(item) for item in RECOMMENDED_MODELS]


def model_registry_path(data_dir: Path) -> Path:
	return data_dir / "models" / "registry.json"


def _registry_corrupt_error(path: Path, detail: str) -> LocalModelManifestError:
	return LocalModelManifestError(
		"MODEL_REGISTRY_INVALID",
		f"{path} 无法解析（{detail}）：修正或删除该文件后重试；模型文件仍在 models/ 目录下，重新 import 即可重建登记",
	)


def read_imported_models(data_dir: Path, *, strict: bool = False) -> list[ImportedLocalModel]:
	"""读取已导入模型登记表。

	登记表只是本机的 bookkeeping，模型文件仍在 `models/` 下，重新 import 即可重建；
	所以默认在文件损坏时按空表处理，`ai local status` 不因一个坏文件整体失败。
	写入方必须传 strict=True：用空表覆盖掉原文件会丢掉还能人工修复的登记。
	"""
	path = model_registry_path(data_dir)
	if not path.exists():
		return []
	try:
		rows = json.loads(path.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
		if strict:
			raise _registry_corrupt_error(path, str(exc)) from exc
		return []
	if not isinstance(rows, list):
		if strict:
			raise _registry_corrupt_error(path, "顶层必须是数组")
		return []
	return [
		ImportedLocalModel(
			model=str(row.get("model", "")),
			path=str(row.get("path", "")),
			runtime=str(row.get("runtime", "custom")),
		)
		for row in rows
		if isinstance(row, dict)
	]


def import_local_model(data_dir: Path, source: Path, model: str) -> ImportedLocalModel:
	"""Copy an external model artifact into the user data directory and register it."""
	if not source.exists():
		raise LocalModelManifestError("MODEL_SOURCE_NOT_FOUND", f"model source does not exist: {source}")
	# 先读登记表再复制：登记表损坏时直接报错，不留下一份复制了却没登记进来的模型目录。
	rows = [item for item in read_imported_models(data_dir, strict=True) if item.model != model]
	target_dir = data_dir / "models" / _safe_model_dir(model)
	target_dir.mkdir(parents=True, exist_ok=True)
	target = target_dir / source.name
	if source.is_dir():
		if target.exists():
			shutil.rmtree(target)
		shutil.copytree(source, target)
	else:
		shutil.copy2(source, target)
	imported = ImportedLocalModel(model=model, path=str(target), runtime="custom")
	rows.append(imported)
	write_json_atomic(model_registry_path(data_dir), [asdict(item) for item in rows])
	return imported


def _safe_model_dir(model: str) -> str:
	return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in model).strip("-") or "model"
