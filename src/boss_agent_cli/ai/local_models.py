"""Local model manifests and filesystem registry helpers."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

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


def read_imported_models(data_dir: Path) -> list[ImportedLocalModel]:
	path = model_registry_path(data_dir)
	if not path.exists():
		return []
	rows = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(rows, list):
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
	rows = read_imported_models(data_dir)
	rows = [item for item in rows if item.model != model]
	rows.append(imported)
	registry = model_registry_path(data_dir)
	registry.parent.mkdir(parents=True, exist_ok=True)
	registry.write_text(
		json.dumps([asdict(item) for item in rows], ensure_ascii=False, indent=2),
		encoding="utf-8",
	)
	return imported


def _safe_model_dir(model: str) -> str:
	return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in model).strip("-") or "model"
