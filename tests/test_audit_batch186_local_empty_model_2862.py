"""Issue #2862: ai local smoke 不应把空模型名送进请求。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.main import cli


def _invoke(runner: CliRunner, tmp_path: Path, args: list[str]):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "local"] + args)


@pytest.mark.parametrize("bad_model", ["", "   "])
def test_local_smoke_rejects_empty_model_without_request(tmp_path: Path, monkeypatch, bad_model: str) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	assert _invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"]).exit_code == 0
	AIConfigStore(tmp_path).save_config(ai_model=bad_model)

	status = json.loads(_invoke(runner, tmp_path, ["status"]).output)
	assert status["data"]["configured"] is False

	with patch("boss_agent_cli.ai.service.httpx.post") as post:
		result = _invoke(runner, tmp_path, ["smoke"])
	post.assert_not_called()

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "CONFIG_INVALID"
	assert "ai_model" in parsed["error"]["message"]
	assert "ai local configure --runtime ollama --model" in parsed["error"]["recovery_action"]
	assert str(tmp_path.resolve()) in parsed["error"]["recovery_action"]
