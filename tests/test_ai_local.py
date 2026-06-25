"""Tests for local AI model management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.local_models import LocalModelManifestError, parse_model_manifest
from boss_agent_cli.main import cli


def _invoke(runner: CliRunner, tmp_path: Path, args: list[str]):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "local", *args])


def _mock_chat_response(content: str):
	response = MagicMock()
	response.json.return_value = {"choices": [{"message": {"content": content}}]}
	response.raise_for_status = MagicMock()
	return response


def test_parse_model_manifest_accepts_recommended_model() -> None:
	manifest = parse_model_manifest({
		"name": "qwen3:14b",
		"runtime": "ollama",
		"license": "Apache-2.0",
		"min_memory_gb": 16,
		"recommended": True,
	})

	assert manifest.name == "qwen3:14b"
	assert manifest.runtime == "ollama"
	assert manifest.recommended is True


def test_parse_model_manifest_rejects_missing_name() -> None:
	try:
		parse_model_manifest({"runtime": "ollama", "license": "Apache-2.0"})
	except LocalModelManifestError as exc:
		assert exc.code == "MODEL_MANIFEST_INVALID"
	else:
		raise AssertionError("manifest without name should fail")


def test_parse_model_manifest_rejects_unapproved_license() -> None:
	try:
		parse_model_manifest({"name": "unknown", "runtime": "ollama", "license": "unknown"})
	except LocalModelManifestError as exc:
		assert exc.code == "MODEL_LICENSE_UNAPPROVED"
	else:
		raise AssertionError("manifest with unknown license should fail")


def test_ai_local_configure_sets_ollama_provider(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()

	result = _invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	store = AIConfigStore(tmp_path)
	assert payload["command"] == "ai.local.configure"
	assert store.load_config()["ai_provider"] == "ollama"
	assert store.load_config()["ai_model"] == "qwen3:14b"
	assert store.get_api_key() == "local"


def test_ai_local_status_reports_manifest_and_config(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"])

	result = _invoke(runner, tmp_path, ["status"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "ai.local.status"
	assert payload["data"]["configured"] is True
	assert payload["data"]["base_url"] == "http://localhost:11434/v1"
	assert any(item["name"] == "qwen3:14b" for item in payload["data"]["recommended_models"])


def test_ai_local_pull_requires_confirm_download(tmp_path: Path) -> None:
	runner = CliRunner()

	result = _invoke(runner, tmp_path, ["pull", "--model", "qwen3:14b"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CONFIRM_DOWNLOAD_REQUIRED"


def test_ai_local_pull_runs_ollama_pull_when_confirmed(tmp_path: Path) -> None:
	runner = CliRunner()

	with patch("boss_agent_cli.commands.ai_local.subprocess.run") as run:
		run.return_value = MagicMock(returncode=0, stdout="pulled", stderr="")
		result = _invoke(runner, tmp_path, ["pull", "--model", "qwen3:14b", "--confirm-download"])

	assert result.exit_code == 0
	run.assert_called_once()
	payload = json.loads(result.output)
	assert payload["data"]["status"] == "installed"


def test_ai_local_import_registers_external_model(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	source = tmp_path / "source.gguf"
	source.write_text("tiny fixture", encoding="utf-8")
	runner = CliRunner()

	result = _invoke(runner, tmp_path, ["import", "--path", str(source), "--model", "local-test"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "ai.local.import"
	assert payload["data"]["model"] == "local-test"
	assert (tmp_path / "models" / "local-test" / "source.gguf").exists()


def test_ai_local_smoke_calls_openai_compatible_endpoint(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"])

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_chat_response("ok")) as post:
		result = _invoke(runner, tmp_path, ["smoke"])

	assert result.exit_code == 0
	post.assert_called_once()
	payload = json.loads(result.output)
	assert payload["command"] == "ai.local.smoke"
	assert payload["data"]["status"] == "ok"
