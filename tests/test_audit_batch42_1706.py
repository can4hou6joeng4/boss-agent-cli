"""AI 配置写入方的严格读取回归测试（Issue #1706）。

只读接口可以把坏 JSON 降级成默认值；写入方不能，否则「默认值 + 本次参数」
会整文件覆盖，静默清空其他 AI 设置。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigInvalidError, AIConfigStore
from boss_agent_cli.main import cli


def _config_path(tmp_path: Path) -> Path:
	return tmp_path / "ai" / "config.json"


def _write_raw(tmp_path: Path, payload: str) -> Path:
	path = _config_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(payload, encoding="utf-8")
	return path


def _run(tmp_path: Path, *args: str) -> tuple[int, dict[str, object]]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])
	return result.exit_code, json.loads(result.stdout)


def test_corrupt_json_save_is_rejected_without_overwriting(tmp_path: Path) -> None:
	path = _write_raw(tmp_path, '{"ai_provider": "deepseek",')

	with pytest.raises(AIConfigInvalidError) as caught:
		AIConfigStore(tmp_path).save_config(ai_temperature=0.1)

	assert caught.value.path == path
	assert path.read_text(encoding="utf-8") == '{"ai_provider": "deepseek",'


@pytest.mark.parametrize("payload", ["[]", '"text"', "42", "null"])
def test_non_object_json_save_is_rejected(tmp_path: Path, payload: str) -> None:
	path = _write_raw(tmp_path, payload)

	with pytest.raises(AIConfigInvalidError):
		AIConfigStore(tmp_path).save_config(ai_temperature=0.1)

	assert path.read_text(encoding="utf-8") == payload


def test_valid_config_still_merges_and_missing_file_starts_from_defaults(tmp_path: Path) -> None:
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="deepseek", ai_base_url="https://api.example.com/v1")
	store.save_config(ai_temperature=0.2)

	config = store.load_config()
	assert config["ai_provider"] == "deepseek"
	assert config["ai_base_url"] == "https://api.example.com/v1"
	assert config["ai_temperature"] == 0.2


def test_read_only_load_still_tolerates_corrupt_json(tmp_path: Path) -> None:
	_write_raw(tmp_path, "not valid json")

	assert AIConfigStore(tmp_path).load_config()["ai_provider"] is None


def test_fifo_config_is_rejected_without_reading(tmp_path: Path) -> None:
	if os.name == "nt":
		pytest.skip("Windows 不提供 os.mkfifo")
	path = _config_path(tmp_path)
	path.parent.mkdir(parents=True)
	os.mkfifo(path)

	with pytest.raises(OSError):
		AIConfigStore(tmp_path).save_config(ai_temperature=0.1)


def test_ai_config_command_reports_config_invalid(tmp_path: Path) -> None:
	path = _write_raw(tmp_path, "{broken")
	original = path.read_bytes()

	exit_code, payload = _run(tmp_path, "ai", "config", "--model", "offline-model")

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CONFIG_INVALID"
	assert path.read_bytes() == original


def test_ai_local_configure_reports_config_invalid(tmp_path: Path) -> None:
	path = _write_raw(tmp_path, "{broken")
	original = path.read_bytes()

	exit_code, payload = _run(
		tmp_path, "ai", "local", "configure", "--runtime", "ollama", "--model", "qwen3:8b"
	)

	assert exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CONFIG_INVALID"
	assert path.read_bytes() == original
