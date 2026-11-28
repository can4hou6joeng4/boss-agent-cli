"""Issue #3132: ai local smoke 不应把纯空白 API key 当成已配置。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.main import cli


def _invoke(runner: CliRunner, tmp_path: Path, args: list[str]):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "local"] + args)


@pytest.mark.parametrize("bad_key", ["", "   ", "\t\n"])
def test_local_smoke_rejects_blank_api_key_without_request(tmp_path: Path, monkeypatch, bad_key: str) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	assert _invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"]).exit_code == 0
	AIConfigStore(tmp_path).save_api_key(bad_key)

	with patch("boss_agent_cli.ai.service.httpx.post") as post:
		result = _invoke(runner, tmp_path, ["smoke"])
	post.assert_not_called()

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AI_NOT_CONFIGURED"
	assert "ai config" in parsed["error"]["recovery_action"]
