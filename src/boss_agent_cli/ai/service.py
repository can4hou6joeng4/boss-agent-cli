"""AI service client for OpenAI-compatible APIs.

Provides a simple interface for chat completions with error handling.
"""

from typing import Any, cast

import httpx


class AIServiceError(Exception):
	"""Raised when an AI service call fails."""

	def __init__(self, message: str, *, status_code: int | None = None):
		super().__init__(message)
		self.status_code = status_code


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
		payload = {
			"model": self.model,
			"messages": messages,
			"temperature": temperature if temperature is not None else self.temperature,
			"max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
		}

		try:
			response = httpx.post(url, json=payload, headers=headers, timeout=60)
			response.raise_for_status()
		except httpx.HTTPStatusError as exc:
			raise AIServiceError(
				f"API 请求失败: HTTP {exc.response.status_code}",
				status_code=exc.response.status_code,
			) from exc
		except httpx.RequestError as exc:
			raise AIServiceError(f"网络请求失败: {exc}") from exc

		try:
			data = response.json()
			return cast("str", data["choices"][0]["message"]["content"])
		except (KeyError, IndexError, TypeError) as exc:
			raise AIServiceError(f"响应格式异常: {exc}") from exc
