"""Local model manifests and filesystem registry helpers."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from boss_agent_cli.services.file_lock import exclusive_file_lock
from boss_agent_cli.services.json_files import path_mode, read_bytes_if_regular, write_json_atomic
from boss_agent_cli.services.output_path import refuse_symlink_target

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


#: ``models/`` 下登记表的固定文件名；也是 ``--model`` 的保留名（Issue #1433）。
_MODEL_REGISTRY_NAME = "registry.json"
#: ``models/`` 下导入锁的固定文件名；同样是 ``--model`` 的保留名（Issue #2255）。
_IMPORT_LOCK_NAME = "import.lock"
_RESERVED_MODEL_DIRS = frozenset({_MODEL_REGISTRY_NAME.casefold(), _IMPORT_LOCK_NAME.casefold()})


def model_registry_path(data_dir: Path) -> Path:
	return data_dir / "models" / _MODEL_REGISTRY_NAME


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
	try:
		raw = read_bytes_if_regular(path)
	except OSError as exc:
		# 路径元数据读不出来、目录 / FIFO / 悬空链接与不可读文件：strict 写入方
		# 必须报损坏，不能拿空表覆盖其他登记（Issue #1700）。
		if strict:
			raise _registry_corrupt_error(path, str(exc)) from exc
		return []
	if raw is None:
		return []
	try:
		rows = json.loads(raw.decode("utf-8"))
	# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError 与 3.11+ 的超长整数
	# 上限；RecursionError 覆盖深嵌套。登记表坏了按既定语义降级或报错。
	except (ValueError, RecursionError) as exc:
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
	"""Copy an external model artifact into the user data directory and register it.

	导入全程持有跨进程锁，复制先落临时路径、成功后再替换目标：重导入复制失败
	时旧副本与登记表原样保留，两个并发导入不会互相覆盖 `registry.json`
	（Issue #1705）。源 / 目标的路径状态读不出来时上抛，不误报「不存在」。
	"""
	safe_name = _safe_model_dir(model)
	if source.name in {"", ".", ".."}:
		# `--path <dir>/model/..` 的末段不是可导入的名字：target 会解析成
		# models/<name>/..（数据目录本体），rename 失败并留下空目录（Issue #2361）。
		raise LocalModelManifestError(
			"INVALID_PARAM",
			f"模型路径必须以文件或目录名结尾: {source}；`.` / `..` 不能作为导入源",
		)
	models_dir = data_dir / "models"
	models_dir.mkdir(parents=True, exist_ok=True)
	lock_path = models_dir / _IMPORT_LOCK_NAME
	with exclusive_file_lock(lock_path) as acquired:
		if not acquired:
			raise OSError(errno.EAGAIN, "另一个模型导入正在进行，请等待其完成后重试", str(lock_path))
		return _import_local_model_locked(data_dir, source, model, safe_name)


def _import_local_model_locked(
	data_dir: Path,
	source: Path,
	model: str,
	safe_name: str,
) -> ImportedLocalModel:
	"""调用方必须持有 `models/import.lock`。"""
	mode = path_mode(source)
	if mode is None:
		raise LocalModelManifestError("MODEL_SOURCE_NOT_FOUND", f"模型路径不存在: {source}")
	source_is_dir = stat.S_ISDIR(mode)
	if not source_is_dir and not stat.S_ISREG(mode):
		# FIFO / 设备会阻塞读取，悬空链接也不能复制（Issue #1705）。
		raise LocalModelManifestError(
			"INVALID_PARAM",
			f"模型路径不是普通文件或目录: {source}；FIFO、设备与悬空链接不能导入",
		)
	# 先读登记表再复制：登记表损坏时直接报错，不留下一份复制了却没登记进来的模型目录。
	target_dir = data_dir / "models" / safe_name
	registered = read_imported_models(data_dir, strict=True)
	owner = _directory_owner(registered, model, safe_name)
	if owner is not None:
		# 不同模型名折到同一个目录（`x:1` 与 `x-1`，或大小写不敏感文件系统上的
		# `Qwen` 与 `qwen`）：源文件同名时后一次导入会替换前一个模型的文件，而
		# 登记表里两条记录指向同一路径，前一个名字之后静默加载另一份权重
		# （Issue #2255）。同名重新导入不受影响。
		raise LocalModelManifestError(
			"INVALID_PARAM",
			f"模型名 {model!r} 与已导入的 {owner!r} 使用同一个目录 models/{safe_name}；"
			"请换一个 --model 名，避免覆盖已有模型",
		)
	rows = [item for item in registered if item.model != model]
	target = target_dir / source.name
	# 悬空链接的 exists() 为假，copy2 会顺着链接在数据目录外创建文件；指向普通
	# 文件的链接则会被写穿替换外部目标（Issue #1705）。
	refuse_symlink_target(target)
	src_resolved = source.resolve()
	tgt_resolved = target.resolve()
	if source_is_dir and src_resolved != tgt_resolved and (
		src_resolved in tgt_resolved.parents or tgt_resolved in src_resolved.parents
	):
		# 源目录包含目标、或目标包含源目录：copytree 会把已写出的副本再当成源一路
		# 复制下去（直到 File name too long），或先删掉源目录再复制失败。
		# 例如 `--path ~/.boss-agent --model qwen3`，目标就在源目录里面。
		raise LocalModelManifestError(
			"INVALID_PARAM",
			f"源目录与导入目标互相包含: {source} -> {target}；复制会递归到自身或先删掉源目录，请换一个源目录或 --model 名",
		)
	target_dir.mkdir(parents=True, exist_ok=True)
	if src_resolved != tgt_resolved:
		# 已经在目标位置（例如重复导入同一路径）时不复制，只登记。
		_replace_model_target(source, target, is_dir=source_is_dir)
	imported = ImportedLocalModel(model=model, path=str(target), runtime="custom")
	rows.append(imported)
	write_json_atomic(model_registry_path(data_dir), [asdict(item) for item in rows])
	return imported


def _staging_path(parent: Path) -> Path:
	"""在目标同目录预留一个临时名；失败时不会让旧副本半截可见。

	临时名不含目标文件名：NAME_MAX=255 时「.<完整名称>.<随机>」会让合法的
	长文件名在复制前 ENAMETOOLONG（Issue #1985、#2359）。
	"""
	fd, raw = tempfile.mkstemp(prefix=".staging-", dir=parent)
	os.close(fd)
	path = Path(raw)
	path.unlink()
	return path


def _discard(path: Path) -> None:
	try:
		if path.is_symlink() or not path.is_dir():
			path.unlink(missing_ok=True)
		else:
			shutil.rmtree(path)
	except FileNotFoundError:
		pass


def _replace_model_target(source: Path, target: Path, *, is_dir: bool) -> None:
	"""完整复制到临时路径后再替换目标；复制失败时旧副本保持原样。"""
	staging = _staging_path(target.parent)
	backup: Path | None = None
	try:
		if is_dir:
			shutil.copytree(source, staging)
		else:
			shutil.copy2(source, staging)
		if path_mode(target) is not None:
			backup = _staging_path(target.parent)
			os.rename(target, backup)
		try:
			os.replace(staging, target)
		except BaseException:
			if backup is not None and path_mode(target) is None:
				os.rename(backup, target)
			raise
		if backup is not None:
			# 旧副本清理失败不应把已经成功的新副本回滚；残留的备用路径可人工删除。
			try:
				_discard(backup)
			except OSError:
				pass
	finally:
		_discard(staging)


def _directory_owner(registered: list[ImportedLocalModel], model: str, safe_name: str) -> str | None:
	"""返回已占用 `models/<safe_name>` 的另一个模型名；没有冲突时返回 None。

	按模型名重新计算目录名而不是读登记表里的 path：path 可能是旧版本写入的历史
	数据。比较用 casefold，与大小写不敏感的默认文件系统口径一致。
	"""
	wanted = safe_name.casefold()
	for item in registered:
		if item.model == model:
			continue
		try:
			other = _safe_model_dir(item.model)
		except LocalModelManifestError:
			# 手工改坏的登记行（空名等）不占用任何目录。
			continue
		if other.casefold() == wanted:
			return item.model
	return None


def _safe_model_dir(model: str) -> str:
	"""把模型名折成 `models/` 下的单层目录名。

	`.` 在允许字符里（`qwen3.5` 这类名字要保留），所以只由点组成的名字必须单独
	拒绝：`--model ..` 会让目标目录变成 `models/..`（即数据目录根），源目录同名时
	还会先 `rmtree` 掉那里的同名目录——`~/.boss-agent` 下的 `auth`、`resumes`、
	`cache` 都可能这样被删掉。
	"""
	safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in model).strip("-")
	if not safe.strip(".") or safe.casefold() in _RESERVED_MODEL_DIRS:
		# casefold：macOS / Windows 的默认文件系统大小写不敏感，REGISTRY.JSON 同样
		# 会撞上登记表（Issue #1433）。导入锁文件 import.lock 也在 models/ 下：
		# 同名模型的目标目录会撞上锁文件，报成本地 IO 错误（Issue #2255）。
		raise LocalModelManifestError(
			"INVALID_PARAM",
			f"模型名无效: {model!r}；不能为空，不能只由 . 和 - 组成，也不能与保留文件名 "
			f"{_MODEL_REGISTRY_NAME} / {_IMPORT_LOCK_NAME} 相同",
		)
	return safe
