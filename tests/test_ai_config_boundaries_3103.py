"""AI 配置边界：假值类型漂移与本地配置的原子性（Issue #3103）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", *args]
	)


def _broken_config(tmp_path: Path, model: object) -> None:
	store = AIConfigStore(tmp_path)
	store.save_api_key("test-key")
	(tmp_path / "ai" / "config.json").write_text(
		json.dumps({"ai_provider": "openai", "ai_model": model, "ai_base_url": None}),
		encoding="utf-8",
	)


@pytest.mark.parametrize("bad_model", [[], {}, 0, False])
def test_falsy_type_drift_reports_config_invalid(tmp_path: Path, monkeypatch, bad_model) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	_broken_config(tmp_path, bad_model)

	result = _invoke(tmp_path, "ai", "polish", "dummy")

	assert result.exit_code == 1, result.output
	error = json.loads(result.output)["error"]
	assert error["code"] == "CONFIG_INVALID"
	assert "ai_model" in error["message"]
	assert "--model" in error["recovery_action"]


def test_blank_model_keeps_not_configured_contract(tmp_path: Path, monkeypatch) -> None:
	"""#2863 的空白串口径不能被类型校验改动。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	_broken_config(tmp_path, "   ")

	result = _invoke(tmp_path, "ai", "polish", "dummy")

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "AI_NOT_CONFIGURED"


def test_local_configure_key_write_failure_does_not_change_config(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	key_path = tmp_path / "ai" / "api_key.enc"
	key_path.mkdir(parents=True)

	result = _invoke(tmp_path, "ai", "local", "configure", "--runtime", "ollama", "--model", "qwen3:14b")

	assert result.exit_code == 1, result.output
	error = json.loads(result.output)["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert not (tmp_path / "ai" / "config.json").exists()


def test_local_configure_still_writes_both_files(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")

	result = _invoke(tmp_path, "ai", "local", "configure", "--runtime", "ollama", "--model", "qwen3:14b")

	assert result.exit_code == 0, result.output
	store = AIConfigStore(tmp_path)
	assert store.load_config()["ai_provider"] == "ollama"
	assert store.get_api_key() == "local"
