"""AI service client for OpenAI-compatible APIs.

Provides a simple interface for chat completions with error handling.
"""

import re
from typing import Any

import httpx

# OpenAI 推理模型（o 系列、gpt-5 及之后）在 Chat Completions 里拒绝 max_tokens、要求
# max_completion_tokens，temperature 也只接受默认值。只按不带 vendor 前缀的模型名切换
# 参数名：OpenRouter 这类网关（openai/gpt-5）收的仍是 max_tokens，由网关自己转换。
_OPENAI_REASONING_MODEL = re.compile(r"^(?:o[1-9]\d*|gpt-(?:[5-9]|[1-9]\d+))(?:[.:-]|$)", re.IGNORECASE)
# Kimi 新型号（K2.5 起）的 temperature 是固定值，官方文档要求不要显式传，传了直接报错。
_KIMI_FIXED_TEMPERATURE_MODEL = re.compile(r"^kimi-k(?:2\.(?:[5-9]|[1-9]\d+)|[3-9]|[1-9]\d+)(?:[.:-]|$)", re.IGNORECASE)
# 服务端报错偶尔回显部分密钥（如 sk-proj-****abcd），写进错误信息前遮掉
_SECRET_LIKE = re.compile(r"\b((?:sk|ak|rk)-)[\w*-]{4,}|\*{3,}\w{0,8}")
_ERROR_DETAIL_LIMIT = 200


class AIServiceError(Exception):
	"""Raised when an AI service call fails."""

	def __init__(self, message: str, *, status_code: int | None = None, truncated: bool = False):
		super().__init__(message)
		self.status_code = status_code
		# 模型在给出正文前就用完了输出上限（思考模型常见），调大上限才能恢复
		self.truncated = truncated


def recovery_action_for(exc: AIServiceError, default: str) -> str:
	"""按失败类型给出可执行的恢复建议；判断不了时沿用调用方的默认建议。"""
	if exc.truncated:
		return "调大输出上限后重试：boss ai config --max-tokens <更大的值>；思考模型也可以关闭思考模式"
	status = exc.status_code
	if status in (401, 403):
		return "检查 API Key 是否有效、是否有该模型的权限：boss ai config --api-key <key>"
	if status in (400, 404, 422):
		return "按错误信息核对模型名与请求参数：boss ai config --model <model>"
	if status == 429:
		return "已被限流或额度不足，稍后重试或检查账户额度"
	if status is not None and status >= 500:
		return "AI 服务端异常，稍后重试"
	return default


def _request_params(model: str) -> tuple[str, bool]:
	"""按模型名决定输出上限用哪个参数名，以及是否发送 temperature。"""
	bare = model.rsplit("/", 1)[-1]
	reasoning = bool(_OPENAI_REASONING_MODEL.match(bare))
	token_param = "max_completion_tokens" if reasoning and "/" not in model else "max_tokens"
	send_temperature = not (reasoning or _KIMI_FIXED_TEMPERATURE_MODEL.match(bare))
	return token_param, send_temperature


def _error_detail(response: httpx.Response) -> str:
	"""取 OpenAI 风格错误体里的说明，压成一行、遮掉疑似密钥并截断；取不到时返回空串。"""
	try:
		body = response.json()
	except ValueError:
		return ""
	detail: Any = None
	if isinstance(body, dict):
		error = body.get("error")
		if isinstance(error, dict):
			detail = error.get("message")
		elif isinstance(error, str):
			detail = error
		if not detail:
			detail = body.get("message") or body.get("detail")
	if not isinstance(detail, str):
		return ""
	text = " ".join(detail.split())
	text = _SECRET_LIKE.sub(lambda m: f"{m.group(1)}***" if m.group(1) else "***", text)
	return text[:_ERROR_DETAIL_LIMIT]


class AIService:
	"""Client for OpenAI-compatible chat completion APIs."""

	def __init__(
		self,
		base_url: str,
		api_key: str,
		model: str,
		temperature: float = 0.7,
		max_tokens: int = 4096,
	):
		# Normalize base_url: strip trailing slash
		self.base_url = base_url.rstrip("/")
		self.api_key = api_key
		self.model = model
		self.temperature = temperature
		self.max_tokens = max_tokens

	def chat(
		self,
		messages: list[dict[str, Any]],
		*,
		temperature: float | None = None,
		max_tokens: int | None = None,
	) -> str:
		"""Send a chat completion request and return the assistant's reply text.

		Args:
			messages: List of message dicts with 'role' and 'content' keys.
			temperature: Override default temperature for this call.
			max_tokens: Override default max_tokens for this call.

		Returns:
			The assistant's reply text.

		Raises:
			AIServiceError: On HTTP errors, network errors, or unexpected response format.
		"""
		url = f"{self.base_url}/chat/completions"
		headers = {
			"Authorization": f"Bearer {self.api_key}",
			"Content-Type": "application/json",
		}
		token_param, send_temperature = _request_params(self.model)
		token_limit = max_tokens if max_tokens is not None else self.max_tokens
		payload: dict[str, Any] = {"model": self.model, "messages": messages}
		if send_temperature:
			payload["temperature"] = temperature if temperature is not None else self.temperature
		payload[token_param] = token_limit

		try:
			response = httpx.post(url, json=payload, headers=headers, timeout=60)
			response.raise_for_status()
		except httpx.HTTPStatusError as exc:
			status = exc.response.status_code
			detail = _error_detail(exc.response)
			raise AIServiceError(
				f"API 请求失败: HTTP {status}" + (f"（{detail}）" if detail else ""),
				status_code=status,
			) from exc
		except httpx.RequestError as exc:
			raise AIServiceError(f"网络请求失败: {exc}") from exc

		try:
			data = response.json()
			choice = data["choices"][0]
			content = choice["message"]["content"]
		except (KeyError, IndexError, TypeError, ValueError) as exc:
			raise AIServiceError(f"响应格式异常: {exc}") from exc
		finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
		if finish_reason == "length" and not content:
			# 思考模型（vLLM --reasoning-parser、gpt-5 等）在思考阶段就耗尽上限时，正文为 null 或空串
			raise AIServiceError(
				f"模型在输出正文前用完了 {token_param}={token_limit}（finish_reason=length）",
				truncated=True,
			)
		if not isinstance(content, str):
			raise AIServiceError(f"响应格式异常: 模型没有返回文本内容（finish_reason={finish_reason}）")
		return content
