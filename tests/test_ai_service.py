"""Tests for AI service client."""

from unittest.mock import patch

import httpx
import pytest

from boss_agent_cli.ai.service import AIService, AIServiceError, recovery_action_for


def _make_service(**kwargs) -> AIService:
	defaults = {
		"base_url": "https://api.example.com/v1",
		"api_key": "sk-test-key",
		"model": "gpt-4",
		"temperature": 0.7,
		"max_tokens": 4096,
	}
	defaults.update(kwargs)
	return AIService(**defaults)


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
	"""Create a mock httpx.Response."""
	if json_data is None:
		json_data = {
			"choices": [{"message": {"content": "Hello, world!"}}]
		}
	return httpx.Response(
		status_code=status_code,
		json=json_data,
		request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
	)


# ── successful call ──────────────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_success(mock_post):
	"""Successful chat call returns the assistant's reply."""
	mock_post.return_value = _mock_response(200, {"choices": [{"message": {"content": "Test reply"}}]})
	service = _make_service()
	result = service.chat([{"role": "user", "content": "Hi"}])
	assert result == "Test reply"


# ── request validation ───────────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_request_headers(mock_post):
	"""Request includes Authorization header and Content-Type."""
	mock_post.return_value = _mock_response()
	service = _make_service(api_key="sk-my-key")
	service.chat([{"role": "user", "content": "Hi"}])

	call_kwargs = mock_post.call_args
	headers = call_kwargs.kwargs["headers"]
	assert headers["Authorization"] == "Bearer sk-my-key"
	assert headers["Content-Type"] == "application/json"


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_request_body(mock_post):
	"""Request body includes model, messages, temperature, max_tokens."""
	mock_post.return_value = _mock_response()
	service = _make_service(model="gpt-4o", temperature=0.5, max_tokens=2048)
	service.chat([{"role": "user", "content": "Hello"}])

	call_kwargs = mock_post.call_args
	payload = call_kwargs.kwargs["json"]
	assert payload["model"] == "gpt-4o"
	assert payload["messages"] == [{"role": "user", "content": "Hello"}]
	assert payload["temperature"] == 0.5
	assert payload["max_tokens"] == 2048


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_url_construction(mock_post):
	"""URL is base_url + /chat/completions."""
	mock_post.return_value = _mock_response()
	service = _make_service(base_url="https://api.example.com/v1")
	service.chat([{"role": "user", "content": "Hi"}])

	call_args = mock_post.call_args
	assert call_args.args[0] == "https://api.example.com/v1/chat/completions"


# ── HTTP errors ──────────────────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_http_401(mock_post):
	"""HTTP 401 raises AIServiceError with status_code."""
	mock_post.return_value = _mock_response(401, {"error": {"message": "Unauthorized"}})
	service = _make_service()
	with pytest.raises(AIServiceError) as exc_info:
		service.chat([{"role": "user", "content": "Hi"}])
	assert exc_info.value.status_code == 401


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_http_500(mock_post):
	"""HTTP 500 raises AIServiceError with status_code."""
	mock_post.return_value = _mock_response(500, {"error": {"message": "Internal Server Error"}})
	service = _make_service()
	with pytest.raises(AIServiceError) as exc_info:
		service.chat([{"role": "user", "content": "Hi"}])
	assert exc_info.value.status_code == 500


# ── network error ────────────────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_network_error(mock_post):
	"""Network error raises AIServiceError without status_code."""
	mock_post.side_effect = httpx.ConnectError("Connection refused")
	service = _make_service()
	with pytest.raises(AIServiceError) as exc_info:
		service.chat([{"role": "user", "content": "Hi"}])
	assert exc_info.value.status_code is None
	assert "网络请求失败" in str(exc_info.value)


# ── response format error ────────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_malformed_response(mock_post):
	"""Malformed response raises AIServiceError."""
	mock_post.return_value = _mock_response(200, {"unexpected": "format"})
	service = _make_service()
	with pytest.raises(AIServiceError) as exc_info:
		service.chat([{"role": "user", "content": "Hi"}])
	assert "响应格式异常" in str(exc_info.value)


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_empty_choices(mock_post):
	"""Empty choices array raises AIServiceError."""
	mock_post.return_value = _mock_response(200, {"choices": []})
	service = _make_service()
	with pytest.raises(AIServiceError) as exc_info:
		service.chat([{"role": "user", "content": "Hi"}])
	assert "响应格式异常" in str(exc_info.value)


# ── parameter overrides ──────────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_temperature_override(mock_post):
	"""Per-call temperature override works."""
	mock_post.return_value = _mock_response()
	service = _make_service(temperature=0.7)
	service.chat([{"role": "user", "content": "Hi"}], temperature=0.2)

	payload = mock_post.call_args.kwargs["json"]
	assert payload["temperature"] == 0.2


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_max_tokens_override(mock_post):
	"""Per-call max_tokens override works."""
	mock_post.return_value = _mock_response()
	service = _make_service(max_tokens=4096)
	service.chat([{"role": "user", "content": "Hi"}], max_tokens=1024)

	payload = mock_post.call_args.kwargs["json"]
	assert payload["max_tokens"] == 1024


# ── base_url normalization ───────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_base_url_trailing_slash(mock_post):
	"""Trailing slash in base_url is handled."""
	mock_post.return_value = _mock_response()
	service = _make_service(base_url="https://api.example.com/v1/")
	service.chat([{"role": "user", "content": "Hi"}])

	url = mock_post.call_args.args[0]
	assert url == "https://api.example.com/v1/chat/completions"
	assert "//" not in url.replace("https://", "")


# ── #499：按模型族发送请求参数 ─────────────────────────────


@pytest.mark.parametrize(
	("model", "token_param", "sends_temperature"),
	[
		("gpt-4o", "max_tokens", True),
		("deepseek-chat", "max_tokens", True),
		("Qwen/Qwen3-8B", "max_tokens", True),
		("gpt-oss-20b", "max_tokens", True),
		("gpt-5", "max_completion_tokens", False),
		("gpt-5-mini", "max_completion_tokens", False),
		("gpt-5.1", "max_completion_tokens", False),
		("o3-mini", "max_completion_tokens", False),
		("o1", "max_completion_tokens", False),
		("openai/gpt-5", "max_tokens", False),
		("kimi-k2.6", "max_tokens", False),
		("kimi-k3.1-pro", "max_tokens", False),
		("moonshotai/kimi-k2.6", "max_tokens", False),
		("kimi-k2-0905-preview", "max_tokens", True),
		("moonshot-v1-8k", "max_tokens", True),
	],
)
@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_payload_follows_model_family(mock_post, model, token_param, sends_temperature):
	"""OpenAI 推理模型改发 max_completion_tokens 且不发 temperature；Kimi 新型号不发 temperature。"""
	mock_post.return_value = _mock_response()
	_make_service(model=model, temperature=0.7, max_tokens=2048).chat([{"role": "user", "content": "Hi"}])

	payload = mock_post.call_args.kwargs["json"]
	assert payload[token_param] == 2048
	other = "max_tokens" if token_param == "max_completion_tokens" else "max_completion_tokens"
	assert other not in payload
	assert ("temperature" in payload) is sends_temperature


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_per_call_overrides_follow_reasoning_params(mock_post):
	mock_post.return_value = _mock_response()
	_make_service(model="gpt-5", max_tokens=4096).chat(
		[{"role": "user", "content": "Hi"}], temperature=0.2, max_tokens=1024
	)

	payload = mock_post.call_args.kwargs["json"]
	assert payload["max_completion_tokens"] == 1024
	assert "temperature" not in payload


# ── #500：正文不是字符串时报错，不把 None 交给调用方 ─────────────


def _choice_response(content, finish_reason="stop"):
	return _mock_response(
		200,
		{"choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}]},
	)


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_null_content_after_length_cutoff_is_truncation_error(mock_post):
	"""vLLM 开 --reasoning-parser 时思考阶段耗尽上限：content 为 null、finish_reason=length。"""
	mock_post.return_value = _mock_response(
		200,
		{
			"choices": [
				{
					"index": 0,
					"finish_reason": "length",
					"message": {"role": "assistant", "content": None, "reasoning": "Okay, the user wants"},
				}
			]
		},
	)
	with pytest.raises(AIServiceError) as exc_info:
		_make_service(model="Qwen/Qwen3-8B", max_tokens=32).chat([{"role": "user", "content": "ping"}])
	assert exc_info.value.truncated is True
	assert "max_tokens=32" in str(exc_info.value)
	assert "finish_reason=length" in str(exc_info.value)


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_empty_content_after_length_cutoff_names_reasoning_param(mock_post):
	mock_post.return_value = _choice_response("", "length")
	with pytest.raises(AIServiceError) as exc_info:
		_make_service(model="gpt-5", max_tokens=4096).chat([{"role": "user", "content": "Hi"}])
	assert exc_info.value.truncated is True
	assert "max_completion_tokens=4096" in str(exc_info.value)


@pytest.mark.parametrize("content", [None, ["text part"], {"text": "x"}])
@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_non_string_content_raises_format_error(mock_post, content):
	mock_post.return_value = _choice_response(content, "stop")
	with pytest.raises(AIServiceError) as exc_info:
		_make_service().chat([{"role": "user", "content": "Hi"}])
	assert exc_info.value.truncated is False
	assert "没有返回文本内容" in str(exc_info.value)


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_empty_content_with_normal_stop_is_returned_as_is(mock_post):
	mock_post.return_value = _choice_response("", "stop")
	assert _make_service().chat([{"role": "user", "content": "Hi"}]) == ""


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_non_json_success_body_raises_format_error(mock_post):
	mock_post.return_value = httpx.Response(
		200,
		text="<html>gateway</html>",
		request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
	)
	with pytest.raises(AIServiceError) as exc_info:
		_make_service().chat([{"role": "user", "content": "Hi"}])
	assert "响应格式异常" in str(exc_info.value)


# ── HTTP 错误带上服务端说明 ─────────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_http_400_includes_provider_message(mock_post):
	reason = "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."
	mock_post.return_value = _mock_response(
		400, {"error": {"message": reason, "type": "invalid_request_error", "param": "max_tokens"}}
	)
	with pytest.raises(AIServiceError) as exc_info:
		_make_service().chat([{"role": "user", "content": "Hi"}])
	assert exc_info.value.status_code == 400
	assert str(exc_info.value) == f"API 请求失败: HTTP 400（{reason}）"


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_http_error_masks_echoed_key_and_truncates(mock_post):
	message = "Incorrect API key provided: sk-proj-abcdefghijklmnop1234. " + "x" * 400
	mock_post.return_value = _mock_response(401, {"error": {"message": message}})
	with pytest.raises(AIServiceError) as exc_info:
		_make_service().chat([{"role": "user", "content": "Hi"}])
	text = str(exc_info.value)
	assert "sk-***" in text
	assert "abcdefghijklmnop1234" not in text
	assert len(text) < 240


@patch("boss_agent_cli.ai.service.httpx.post")
def test_chat_http_error_without_json_body_keeps_plain_status(mock_post):
	mock_post.return_value = httpx.Response(
		502,
		text="<html>Bad Gateway</html>",
		request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
	)
	with pytest.raises(AIServiceError) as exc_info:
		_make_service().chat([{"role": "user", "content": "Hi"}])
	assert str(exc_info.value) == "API 请求失败: HTTP 502"


@pytest.mark.parametrize(
	("exc", "expected"),
	[
		(AIServiceError("x", truncated=True), "--max-tokens"),
		(AIServiceError("x", status_code=401), "--api-key"),
		(AIServiceError("x", status_code=400), "--model"),
		(AIServiceError("x", status_code=429), "限流"),
		(AIServiceError("x", status_code=503), "服务端"),
		(AIServiceError("x"), "调用方默认建议"),
	],
)
def test_recovery_action_for_maps_failure_kind(exc, expected):
	assert expected in recovery_action_for(exc, "调用方默认建议")
