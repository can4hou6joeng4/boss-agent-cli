"""Tests for local AI model management."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.main import cli


def _invoke(runner: CliRunner, tmp_path: Path, args: list[str]):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "local", *args])


def _mock_chat_response(content: str):
	response = MagicMock()
	response.json.return_value = {"choices": [{"message": {"content": content}}]}
	response.raise_for_status = MagicMock()
	return response


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


def test_ai_local_pull_hint_quotes_model_with_spaces(tmp_path: Path) -> None:
	"""模型名是自由文本：recovery_action 里的名字带空格时必须整体加引号。

	Agent 照抄 `--model my model --confirm-download` 会把 `model` 当位置参数，
	真正下载的还是错误的名字。
	"""
	result = _invoke(CliRunner(), tmp_path, ["pull", "--model", "my model"])

	assert result.exit_code == 1
	recovery = json.loads(result.output)["error"]["recovery_action"]
	assert recovery == (
		f"boss --data-dir {tmp_path.resolve()} ai local pull --model 'my model' --confirm-download"
	)
	assert shlex.split(recovery) == [
		"boss", "--data-dir", str(tmp_path.resolve()),
		"ai", "local", "pull", "--model", "my model", "--confirm-download",
	]


def test_ai_local_pull_runs_ollama_pull_when_confirmed(tmp_path: Path) -> None:
	runner = CliRunner()

	with patch("boss_agent_cli.commands.ai_local.subprocess.run") as run:
		run.return_value = MagicMock(returncode=0, stdout="pulled", stderr="")
		result = _invoke(runner, tmp_path, ["pull", "--model", "qwen3:14b", "--confirm-download"])

	assert result.exit_code == 0
	run.assert_called_once()
	payload = json.loads(result.output)
	assert payload["data"]["status"] == "installed"


def test_ai_local_pull_reports_missing_ollama_as_pull_failure(tmp_path: Path) -> None:
	"""没装 ollama 时应返回 LOCAL_MODEL_PULL_FAILED，而不是顶层网兜的 LOCAL_IO_ERROR。"""
	runner = CliRunner()

	with patch("boss_agent_cli.commands.ai_local.subprocess.run") as run:
		run.side_effect = FileNotFoundError(2, "No such file or directory", "ollama")
		result = _invoke(runner, tmp_path, ["pull", "--model", "qwen3:14b", "--confirm-download"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "LOCAL_MODEL_PULL_FAILED"
	assert payload["error"]["recoverable"] is True
	assert "ollama" in payload["error"]["message"]


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


def test_ai_local_import_hint_quotes_model_with_spaces(tmp_path: Path, monkeypatch) -> None:
	"""导入名带空格时，下一步 configure 的 hint 必须保持是同一个模型名。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	source = tmp_path / "source.gguf"
	source.write_text("tiny fixture", encoding="utf-8")

	result = _invoke(
		CliRunner(), tmp_path, ["import", "--path", str(source), "--model", "my model"]
	)

	assert result.exit_code == 0, result.output
	hint = json.loads(result.output)["hints"]["next_actions"][0]
	assert hint == f"boss --data-dir {tmp_path.resolve()} ai local configure --runtime vllm --model 'my model'"
	assert shlex.split(hint)[-1] == "my model"


def test_ai_local_import_does_not_reset_configured_endpoint(tmp_path: Path, monkeypatch) -> None:
	"""导入模型只登记文件，不能改 AI 配置。

	以前会写 ai_provider=custom / ai_base_url=None / ai_model=<导入名> 并覆盖
	api_key：用户刚配好的 ollama 端点被清空，下一条 AI 命令直接报「AI 服务未配置」。
	"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"])
	source = tmp_path / "source.gguf"
	source.write_text("tiny fixture", encoding="utf-8")

	result = _invoke(runner, tmp_path, ["import", "--path", str(source), "--model", "local-test"])

	assert result.exit_code == 0
	store = AIConfigStore(tmp_path)
	assert store.load_config()["ai_provider"] == "ollama"
	assert store.load_config()["ai_model"] == "qwen3:14b"
	assert store.get_base_url() == "http://localhost:11434/v1"
	assert store.get_api_key() == "local"
	payload = json.loads(result.output)
	assert payload["hints"]["next_actions"][0] == (
		f"boss --data-dir {tmp_path.resolve()} ai local configure --runtime vllm --model local-test"
	)


def test_ai_local_smoke_names_provider_without_endpoint(tmp_path: Path, monkeypatch) -> None:
	"""遗留坏配置下要点名是端点缺失，而不是笼统的「本地 AI 服务未配置」。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="custom", ai_model="m", ai_base_url=None)
	store.save_api_key("sk-test")

	result = _invoke(CliRunner(), tmp_path, ["smoke"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AI_NOT_CONFIGURED"
	assert "'custom'" in payload["error"]["message"]


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


def _mock_raw_response(payload: dict):
	response = MagicMock()
	response.json.return_value = payload
	response.raise_for_status = MagicMock()
	return response


def test_ai_local_smoke_null_content_returns_ai_api_error_envelope(tmp_path: Path, monkeypatch) -> None:
	"""#500：vLLM 思考阶段耗尽上限返回 content null，smoke 输出错误信封而不是 traceback。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["configure", "--runtime", "vllm", "--model", "Qwen/Qwen3-8B"])
	vllm_reply = {
		"choices": [
			{
				"index": 0,
				"finish_reason": "length",
				"message": {"role": "assistant", "content": None, "reasoning": "Okay, the user wants"},
			}
		]
	}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_raw_response(vllm_reply)):
		result = _invoke(runner, tmp_path, ["smoke"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert payload["error"]["code"] == "AI_API_ERROR"
	assert "max_tokens=512" in payload["error"]["message"]
	assert "--max-tokens" in payload["error"]["recovery_action"]


def test_ai_local_smoke_uses_configured_max_tokens(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	runner = CliRunner()
	_invoke(runner, tmp_path, ["configure", "--runtime", "ollama", "--model", "qwen3:14b"])
	AIConfigStore(tmp_path).save_config(ai_max_tokens=2048)

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=_mock_chat_response("pong")) as post:
		result = _invoke(runner, tmp_path, ["smoke"])

	assert result.exit_code == 0
	assert post.call_args.kwargs["json"]["max_tokens"] == 2048
