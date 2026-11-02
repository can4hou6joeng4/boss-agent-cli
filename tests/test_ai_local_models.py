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
