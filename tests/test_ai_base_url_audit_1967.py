"""ai config 的 base_url 生命周期与半截更新（Issue #1967–#1969）。"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli

_KEY = "FAKEKEY-not-real-0001"


def _invoke(data_dir: Path, *args: str) -> tuple[int, dict]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])
	return result.exit_code, json.loads(result.stdout)


def _config(data_dir: Path) -> dict:
	path = data_dir / "ai" / "config.json"
	return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ── Issue #1967：自定义 base_url 可以被清除 ─────────────────────────


def test_clear_base_url_returns_to_provider_default(tmp_path: Path) -> None:
	_invoke(
		tmp_path,
		"ai", "config",
		"--provider", "custom", "--base-url", "http://127.0.0.1:9/v1",
		"--model", "m", "--api-key", _KEY,
	)
	code, envelope = _invoke(tmp_path, "ai", "config", "--provider", "deepseek", "--clear-base-url")

	assert code == 0, envelope
	assert envelope["data"]["resolved_base_url"] == "https://api.deepseek.com/v1"
	assert not _config(tmp_path).get("ai_base_url")


def test_clear_base_url_conflicts_with_base_url(tmp_path: Path) -> None:
	code, envelope = _invoke(tmp_path, "ai", "config", "--base-url", "https://a.example/v1",
		"--clear-base-url")

	assert code == 1
	assert envelope["error"]["code"] == "INVALID_PARAM"


# ── Issue #1968：base_url 类型漂移不再被当成地址回显 ─────────────────


def test_base_url_drift_is_config_invalid_not_true(tmp_path: Path) -> None:
	ai_dir = tmp_path / "ai"
	ai_dir.mkdir(parents=True)
	(ai_dir / "config.json").write_text(
		json.dumps({"ai_provider": "openai", "ai_base_url": True, "ai_model": "gpt-4o"}),
		encoding="utf-8",
	)

	code, envelope = _invoke(tmp_path, "ai", "config")

	assert code == 1
	assert envelope["error"]["code"] == "CONFIG_INVALID"
	assert envelope["data"] is None


def test_provider_switch_with_drifted_base_url_is_rejected(tmp_path: Path) -> None:
	ai_dir = tmp_path / "ai"
	ai_dir.mkdir(parents=True)
	(ai_dir / "config.json").write_text(json.dumps({"ai_base_url": ["x"]}), encoding="utf-8")

	code, envelope = _invoke(tmp_path, "ai", "config", "--provider", "openai")

	assert code == 1
	assert envelope["error"]["code"] == "CONFIG_INVALID"


# ── Issue #1969：密钥写失败不留下半截配置 ───────────────────────────


def test_api_key_write_failure_leaves_config_untouched(tmp_path: Path) -> None:
	_invoke(tmp_path, "ai", "config", "--provider", "openai", "--model", "gpt-4o", "--api-key", _KEY)
	key_path = tmp_path / "ai" / "api_key.enc"
	key_path.unlink()
	key_path.mkdir()

	code, envelope = _invoke(tmp_path, "ai", "config", "--model", "gpt-4.1", "--api-key", "FAKEKEY-2")

	assert code == 1
	assert envelope["error"]["code"] == "LOCAL_IO_ERROR"
	assert _config(tmp_path)["ai_model"] == "gpt-4o"
