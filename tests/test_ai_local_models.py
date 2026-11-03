"""本地模型登记表（models/registry.json）的读取与写入容错。"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.local_models import (
	LocalModelManifestError,
	import_local_model,
	model_registry_path,
	read_imported_models,
)
from boss_agent_cli.main import cli


def _write_registry(tmp_path, content: bytes) -> None:
	path = model_registry_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_bytes(content)


def _make_model_source(tmp_path):
	source = tmp_path / "model.bin"
	source.write_bytes(b"weights")
	return source


# ── read_imported_models ─────────────────────────────────────


def test_read_imported_models_round_trips(tmp_path):
	source = _make_model_source(tmp_path)
	imported = import_local_model(tmp_path, source, "qwen-local")
	rows = read_imported_models(tmp_path)
	assert [(item.model, item.path, item.runtime) for item in rows] == [
		(imported.model, imported.path, imported.runtime)
	]


@pytest.mark.parametrize(
	"content",
	[
		b'{"not": "a list"}',
		b'{"model": "qwen',
		'{"model": "通义"}'.encode("gbk"),
	],
)
def test_read_imported_models_tolerates_corrupt_registry(tmp_path, content):
	"""登记表只是 bookkeeping：损坏时 status 仍要能打开，按空表处理。"""
	_write_registry(tmp_path, content)
	assert read_imported_models(tmp_path) == []


def test_import_local_model_reports_corrupt_registry(tmp_path):
	"""写入方不能用空表覆盖损坏的登记表，否则还能人工修复的条目直接消失。"""
	source = _make_model_source(tmp_path)
	broken = b'{"model": "qwen'
	_write_registry(tmp_path, broken)

	with pytest.raises(LocalModelManifestError) as excinfo:
		import_local_model(tmp_path, source, "qwen-local")

	assert excinfo.value.code == "MODEL_REGISTRY_INVALID"
	assert str(model_registry_path(tmp_path)) in excinfo.value.message
	assert model_registry_path(tmp_path).read_bytes() == broken
	# 先读登记表再复制：失败时不应留下复制了却没登记的模型目录。
	assert not (tmp_path / "models" / "qwen-local").exists()


@pytest.mark.parametrize("model", ["..", ".", "---", ""])
def test_import_local_model_rejects_dot_only_model_names(tmp_path, model):
	"""`.` 是允许字符，所以只由点组成的模型名会指回 models/ 的上级目录。"""
	source_dir = tmp_path / "artifact"
	source_dir.mkdir()
	(source_dir / "w.bin").write_bytes(b"weights")
	data_dir = tmp_path / "data"
	# 数据目录根上放一个同名目录：`--model ..` 会把它当成目标并 rmtree 掉。
	victim = data_dir / "artifact"
	victim.mkdir(parents=True)
	(victim / "important.txt").write_text("keep me", encoding="utf-8")

	with pytest.raises(LocalModelManifestError) as excinfo:
		import_local_model(data_dir, source_dir, model)

	assert excinfo.value.code == "INVALID_PARAM"
	assert victim.joinpath("important.txt").read_text(encoding="utf-8") == "keep me"
	assert not model_registry_path(data_dir).exists()


def test_import_local_model_keeps_dots_inside_the_name(tmp_path):
	"""正常带点的模型名（qwen3.5）不受影响，仍折成 models/ 下的单层目录。"""
	source = _make_model_source(tmp_path)
	imported = import_local_model(tmp_path, source, "qwen3.5")

	assert imported.path == str(tmp_path / "models" / "qwen3.5" / "model.bin")


def test_import_local_model_rejects_source_containing_target(tmp_path):
	"""`--path` 指到数据目录本身时，copytree 会把刚写出的副本当成源一路复制下去。

	旧实现里这条命令会递归到 ``[Errno 63] File name too long``，再被顶层兜成
	可重试的 ``NETWORK_ERROR``——拿去重试只会得到同一个错误。
	"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()

	with pytest.raises(LocalModelManifestError) as excinfo:
		import_local_model(data_dir, data_dir, "qwen3")

	assert excinfo.value.code == "INVALID_PARAM"
	assert "互相包含" in excinfo.value.message
	assert not model_registry_path(data_dir).exists()


def test_import_local_model_registers_source_already_at_target(tmp_path):
	"""源目录恰好就是目标目录时，旧实现先 rmtree 再复制，源目录会被删掉。"""
	data_dir = tmp_path / "data"
	source = data_dir / "models" / "qwen3" / "artifact"
	source.mkdir(parents=True)
	(source / "w.bin").write_bytes(b"weights")

	imported = import_local_model(data_dir, source, "qwen3")

	assert imported.path == str(source)
	assert (source / "w.bin").read_bytes() == b"weights"


def test_local_import_cli_reports_recursive_copy_as_param_error(tmp_path):
	"""端到端：同一路径同时作为 --data-dir 与 --path 时不能回 NETWORK_ERROR。"""
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "ai", "local", "import", "--model", "qwen3", "--path", str(tmp_path)],
	)
	payload = json.loads(result.stdout)

	assert result.exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "互相包含" in payload["error"]["message"]


def test_import_local_model_writes_registry_without_leftover_temp(tmp_path):
	source = _make_model_source(tmp_path)
	import_local_model(tmp_path, source, "qwen-local")

	registry = model_registry_path(tmp_path)
	rows = json.loads(registry.read_text(encoding="utf-8"))
	assert [row["model"] for row in rows] == ["qwen-local"]
	assert [path.name for path in registry.parent.iterdir() if ".tmp" in path.name] == []


# ── CLI ──────────────────────────────────────────────────────


def _invoke(tmp_path, *args, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine-id")
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "ai", "local", *args])


def test_local_status_survives_corrupt_registry(tmp_path, monkeypatch):
	_write_registry(tmp_path, b'{"model": "qwen')
	result = _invoke(tmp_path, "status", monkeypatch=monkeypatch)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["imported_models"] == []


def test_local_import_rejects_dot_dot_model_name(tmp_path, monkeypatch):
	"""`--model ..` 不能把源目录写到数据目录根，更不能删掉那里的同名目录。"""
	source_dir = tmp_path / "src" / "artifact"
	source_dir.mkdir(parents=True)
	(source_dir / "w.bin").write_bytes(b"weights")
	victim = tmp_path / "artifact"
	victim.mkdir()
	(victim / "session.enc").write_text("keep me", encoding="utf-8")

	result = _invoke(tmp_path, "import", "--path", str(source_dir), "--model", "..", monkeypatch=monkeypatch)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert (victim / "session.enc").read_text(encoding="utf-8") == "keep me"


def test_local_import_reports_registry_error_instead_of_traceback(tmp_path, monkeypatch):
	source = _make_model_source(tmp_path)
	_write_registry(tmp_path, b'{"model": "qwen')
	result = _invoke(tmp_path, "import", "--path", str(source), "--model", "qwen-local", monkeypatch=monkeypatch)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["command"] == "ai.local.import"
	assert parsed["error"]["code"] == "MODEL_REGISTRY_INVALID"
	assert "registry.json" in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is True
