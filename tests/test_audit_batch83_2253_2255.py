"""Issue #2253–#2255：AI 端点生命周期、请求构造与模型目录归属。

全部离线：HTTP 成功 / 错误用 mock，代理构造失败在发请求前发生。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore, AIConfigValueError, normalize_base_url
from boss_agent_cli.ai.local_models import LocalModelManifestError, import_local_model, read_imported_models
from boss_agent_cli.ai.service import AIService, AIServiceError, recovery_action_for
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy
from boss_agent_cli.automation.models import CandidateKey, Conversation, Decision, PlatformAction
from boss_agent_cli.automation.reply_ai import apply_reply_strategy
from boss_agent_cli.main import cli

_KEY = "placeholder-key-for-test"


def _configured(tmp_path: Path, **values) -> AIConfigStore:
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="example-model", **values)
	store.save_api_key(_KEY)
	return store


def _service() -> AIService:
	return AIService(base_url="http://127.0.0.1:9/v1", api_key=_KEY, model="example-model")


def _success() -> httpx.Response:
	return httpx.Response(200, request=httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions"),
		json={"choices": [{"message": {"content": '{"reply": "建议回复"}'}}]})


@pytest.mark.parametrize("stored", ["", None])
def test_empty_stored_endpoint_uses_provider_default(tmp_path, monkeypatch, stored) -> None:
	_configured(tmp_path, ai_base_url=stored)
	post = Mock(return_value=_success())
	monkeypatch.setattr("boss_agent_cli.ai.service.httpx.post", post)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "reply", "你好"])
	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["ok"] is True
	assert post.call_args.args[0] == "https://api.openai.com/v1/chat/completions"


def test_clear_base_url_persists_default_and_chat_works(tmp_path, monkeypatch) -> None:
	store = _configured(tmp_path, ai_base_url="http://127.0.0.1:9/v1")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "config", "--clear-base-url"])
	assert result.exit_code == 0, result.output
	assert store.load_config()["ai_base_url"] is None
	post = Mock(return_value=_success())
	monkeypatch.setattr("boss_agent_cli.ai.service.httpx.post", post)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "reply", "你好"])
	assert result.exit_code == 0, result.output
	assert post.call_args.args[0] == "https://api.openai.com/v1/chat/completions"


def test_clear_with_unknown_default_provider_does_not_destroy_endpoint(tmp_path) -> None:
	store = _configured(tmp_path, ai_base_url="http://127.0.0.1:9/v1")
	before = store.load_config()
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "config", "--provider", "custom", "--clear-base-url"])
	assert result.exit_code == 1
	assert json.loads(result.stdout)["error"]["code"] == "INVALID_PARAM"
	assert store.load_config() == before


@pytest.mark.parametrize("address", [
	"https://api.example.invalid/v1?version=1", "https://api.example.invalid/v1#fragment",
	"https://api.example.invalid/v1?", "https://api.example.invalid/v1#",
	"https://api.example.invalid/v1​", "https://api.example.invalid/﻿v1",
	"https://api.example.invalid/‮v1", "https://api.example.invalid/\x85v1",
	"https://api.example.invalid/\ud800v1", "https://api.example.invalid/ v1",
])
def test_endpoint_rejects_query_and_invisible_characters(address) -> None:
	with pytest.raises(AIConfigValueError):
		normalize_base_url(address)


@pytest.mark.parametrize("address", ["https://api.example.invalid/v1", "http://127.0.0.1:9/v1", "https://例子.invalid/路径", "https://api.example.invalid/v1%3Fpart"])
def test_endpoint_accepts_normal_paths(address) -> None:
	assert normalize_base_url(address) == address


def test_long_endpoint_rejected_with_short_error() -> None:
	with pytest.raises(AIConfigValueError) as caught:
		normalize_base_url("https://api.example.invalid/" + "a" * 70000)
	assert "过长" in str(caught.value)
	assert len(str(caught.value)) < 250


def test_query_rejection_does_not_echo_query_value() -> None:
	with pytest.raises(AIConfigValueError) as caught:
		normalize_base_url("https://api.example.invalid/v1?api-key=placeholder-query-value")
	assert "placeholder-query-value" not in str(caught.value)


def test_bad_endpoint_config_write_keeps_previous_value(tmp_path) -> None:
	store = _configured(tmp_path, ai_base_url="http://127.0.0.1:9/v1")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "config", "--base-url", "https://api.example.invalid/v1?version=1"])
	assert result.exit_code == 1
	assert json.loads(result.stdout)["error"]["code"] == "INVALID_PARAM"
	assert store.load_config()["ai_base_url"] == "http://127.0.0.1:9/v1"


@pytest.mark.parametrize("scheme", ["socks4", "socks", "ftp"])
def test_proxy_scheme_failure_is_local_parameter_error(monkeypatch, scheme) -> None:
	for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
		monkeypatch.delenv(key, raising=False)
	monkeypatch.setenv("ALL_PROXY", f"{scheme}://127.0.0.1:9")
	# 构造 Client 即失败；用禁止 transport 派发的桩确保无网络请求。
	transport = Mock(side_effect=AssertionError("不应派发 HTTP 请求"))
	monkeypatch.setattr(httpx.HTTPTransport, "handle_request", transport)
	with pytest.raises(AIServiceError) as caught:
		_service().chat([{"role": "user", "content": "你好"}])
	assert caught.value.code == "INVALID_PARAM"
	assert caught.value.recoverable is False
	assert "代理环境变量" in str(caught.value)
	assert "ALL_PROXY" in recovery_action_for(caught.value, "检查网络后重试")
	transport.assert_not_called()


def test_unicode_encoding_failure_does_not_escape_service(monkeypatch) -> None:
	# httpx 的本地 Request 构造会编码 JSON；无 Client、无传输层。
	def encode_request(url, **kwargs):
		return httpx.Request("POST", url, json=kwargs["json"])
	monkeypatch.setattr("boss_agent_cli.ai.service.httpx.post", encode_request)
	with pytest.raises(AIServiceError) as caught:
		_service().chat([{"role": "user", "content": "损坏\ud800文本"}])
	assert caught.value.code == "INVALID_PARAM"
	assert caught.value.recoverable is False
	assert "UTF-8" in str(caught.value)
	assert "输入文本" in recovery_action_for(caught.value, "检查网络后重试")


def test_request_error_masks_key_but_keeps_network_recovery(monkeypatch) -> None:
	post = Mock(side_effect=httpx.ConnectError(f"网关回显 {_KEY}"))
	monkeypatch.setattr("boss_agent_cli.ai.service.httpx.post", post)
	with pytest.raises(AIServiceError) as caught:
		_service().chat([{"role": "user", "content": "你好"}])
	assert _KEY not in str(caught.value)
	assert "***" in str(caught.value)
	assert caught.value.code == "AI_API_ERROR"
	assert caught.value.recoverable is True
	assert recovery_action_for(caught.value, "网络恢复建议") == "网络恢复建议"


def test_request_success_keeps_response_content(monkeypatch) -> None:
	monkeypatch.setattr("boss_agent_cli.ai.service.httpx.post", Mock(return_value=_success()))
	assert json.loads(_service().chat([{"role": "user", "content": "你好"}])) == {"reply": "建议回复"}


def test_chat_parameter_failure_has_single_cli_envelope(tmp_path, monkeypatch) -> None:
	_configured(tmp_path)
	monkeypatch.setattr("boss_agent_cli.ai.service.httpx.post", Mock(side_effect=ValueError("Unknown scheme for proxy URL")))
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "reply", "你好"])
	assert result.exit_code == 1
	envelope = json.loads(result.stdout)
	assert set(envelope) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert envelope["error"]["recoverable"] is False
	assert "代理环境变量" in envelope["error"]["recovery_action"]


@pytest.mark.parametrize("base_url", ["https://api.example.invalid/v1?version=1", "invalid", "https://api.example.invalid/v1"])
def test_local_reply_endpoint_failure_uses_template_or_valid_draft(tmp_path, monkeypatch, base_url) -> None:
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="vllm", ai_model="example-model", ai_base_url=base_url)
	store.save_api_key(_KEY)
	decision = Decision(action=PlatformAction.SEND_QUESTIONNAIRE, confidence=0.9, reason="规则", candidate_key=CandidateKey("candidate-example"), message="模板话术")
	chat = Mock(return_value=json.dumps({"action": "send_questionnaire", "confidence": 0.9, "reply": "模型话术", "reason": "建议", "risk_flags": []}))
	monkeypatch.setattr(AIService, "chat", chat)
	actual = apply_reply_strategy(decision, Conversation(title="示例候选人"), AutomationConfig(reply_strategy=ReplyStrategy.LOCAL_AI), tmp_path)
	if base_url.endswith("/v1"):
		assert actual.message == "模型话术"
		chat.assert_called_once()
	else:
		assert actual.message == "模板话术"
		assert "local ai config invalid" in actual.reason
		assert "template fallback" in actual.reason
		chat.assert_not_called()


@pytest.mark.parametrize("first, second", [("x:1", "x-1"), ("Qwen", "qwen")])
def test_colliding_model_names_preserve_first_model(tmp_path, first, second) -> None:
	source = tmp_path / "model.gguf"
	source.write_bytes(b"first weights")
	data_dir = tmp_path / "data"
	initial = import_local_model(data_dir, source, first)
	source.write_bytes(b"second weights")
	with pytest.raises(LocalModelManifestError) as caught:
		import_local_model(data_dir, source, second)
	assert caught.value.code == "INVALID_PARAM"
	assert "--model" in str(caught.value)
	assert Path(initial.path).read_bytes() == b"first weights"
	assert read_imported_models(data_dir, strict=True) == [initial]


def test_same_model_reimport_can_replace_its_own_weights(tmp_path) -> None:
	source = tmp_path / "model.gguf"
	source.write_bytes(b"old")
	data_dir = tmp_path / "data"
	import_local_model(data_dir, source, "x:1")
	source.write_bytes(b"updated")
	actual = import_local_model(data_dir, source, "x:1")
	assert Path(actual.path).read_bytes() == b"updated"
	assert read_imported_models(data_dir, strict=True) == [actual]


@pytest.mark.parametrize("model", ["import.lock", "IMPORT.LOCK", "Import.Lock"])
def test_import_lock_is_reserved_model_name(tmp_path, model) -> None:
	source = tmp_path / "model.gguf"
	source.write_bytes(b"weights")
	data_dir = tmp_path / "data"
	with pytest.raises(LocalModelManifestError) as caught:
		import_local_model(data_dir, source, model)
	assert caught.value.code == "INVALID_PARAM"
	assert "import.lock" in str(caught.value)
	assert not (data_dir / "models").exists()


def test_distinct_model_directories_remain_independent(tmp_path) -> None:
	source = tmp_path / "model.gguf"
	source.write_bytes(b"one")
	data_dir = tmp_path / "data"
	first = import_local_model(data_dir, source, "model-one")
	source.write_bytes(b"two")
	second = import_local_model(data_dir, source, "model-two")
	assert Path(first.path).read_bytes() == b"one"
	assert Path(second.path).read_bytes() == b"two"
	assert len(read_imported_models(data_dir, strict=True)) == 2
