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
_SECRET_LIKE = re.compile(r"\b((?:sk|ak|rk)-)[\w*.-]{4,}|\*{3,}\w{0,8}")
# 回显片段至少这么长才按密钥遮掉，避免把普通短词误伤成 ***。
_SECRET_FRAGMENT_MIN = 8
_ERROR_DETAIL_LIMIT = 200


class AIServiceError(Exception):
	"""Raised when an AI service call fails."""

	def __init__(
		self,
		message: str,
		*,
		status_code: int | None = None,
		truncated: bool = False,
		recovery_hint: str | None = None,
		code: str = "AI_API_ERROR",
		recoverable: bool = True,
	):
		super().__init__(message)
		self.status_code = status_code
		# 模型在给出正文前就用完了输出上限（思考模型常见），调大上限才能恢复
		self.truncated = truncated
		# 本地确定性失败（代理环境变量、请求内容无法编码）的专属恢复建议：这类错误
		# 原样重试不会成功，不能沿用调用方「检查网络后重试」的默认文案（Issue #2254）。
		self.recovery_hint = recovery_hint
		self.code = code
		self.recoverable = recoverable


def recovery_action_for(exc: AIServiceError, default: str, *, config_command: str = "boss ai config") -> str:
	"""按失败类型给出可执行的恢复建议；判断不了时沿用调用方的默认建议。

	``config_command`` 允许调用方传入带 ``--data-dir`` 的等价命令，避免照抄恢复
	提示后把新的 key / model 写进默认目录。
	"""
	if exc.recovery_hint:
		return exc.recovery_hint
	if exc.truncated:
		return f"调大输出上限后重试：{config_command} --max-tokens <更大的值>；思考模型也可以关闭思考模式"
	status = exc.status_code
	if status in (401, 403):
		return f"检查 API Key 是否有效、是否有该模型的权限：{config_command} --api-key <key>"
	if status in (400, 404, 422):
		return f"按错误信息核对模型名与请求参数：{config_command} --model <model>"
	if status == 429:
		return "已被限流或额度不足，稍后重试或检查账户额度"
	if status is not None and status >= 500:
		return "AI 服务端异常，稍后重试"
	return default


#: httpx（trust_env）读取的代理环境变量；大小写两种写法都生效。
_PROXY_ENV_HINT = "HTTPS_PROXY / HTTP_PROXY / ALL_PROXY"


def _request_params(model: str) -> tuple[str, bool]:
	"""按模型名决定输出上限用哪个参数名，以及是否发送 temperature。"""
	bare = model.rsplit("/", 1)[-1]
	reasoning = bool(_OPENAI_REASONING_MODEL.match(bare))
	token_param = "max_completion_tokens" if reasoning and "/" not in model else "max_tokens"
	send_temperature = not (reasoning or _KIMI_FIXED_TEMPERATURE_MODEL.match(bare))
	return token_param, send_temperature


def _mask_api_key(text: str, api_key: str) -> str:
	"""遮掉服务商原样回显的密钥及其被 ``.`` 分开的各段（Issue #1953）。

	正则只认 sk- / ak- / rk- 前缀；其它形态（如 ``id.secret``）的密钥被回显时
	会原样进入 ``error.message``。
	"""
	key = api_key.strip()
	fragments = {key, *key.split(".")}
	for fragment in sorted(fragments, key=len, reverse=True):
		if len(fragment) >= _SECRET_FRAGMENT_MIN:
			text = text.replace(fragment, "***")
	return text


def _error_detail(response: httpx.Response, api_key: str = "") -> str:
	"""取 OpenAI 风格错误体里的说明，压成一行、遮掉疑似密钥并截断；取不到时返回空串。"""
	try:
		body = response.json()
	# RecursionError：3.11–3.13 解析极深嵌套的错误体时抛出；取不到说明就按空串处理，
	# 不能让它在 HTTP 错误分支里冒泡成 traceback。
	except (ValueError, RecursionError):
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
	text = _mask_api_key(" ".join(detail.split()), api_key)
	text = _SECRET_LIKE.sub(lambda m: f"{m.group(1)}***" if m.group(1) else "***", text)
	return text[:_ERROR_DETAIL_LIMIT]


def _embedded_status(body: Any) -> int | None:
	"""HTTP 200 的错误对象里携带的状态码（`error.code` / `error.status`）；取不到返回 None。"""
	error = body.get("error") if isinstance(body, dict) else None
	if not isinstance(error, dict):
		return None
	for key in ("code", "status"):
		value = error.get(key)
		if isinstance(value, int) and not isinstance(value, bool) and 400 <= value <= 599:
			return value
	return None


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
			detail = _error_detail(exc.response, self.api_key)
			raise AIServiceError(
				f"API 请求失败: HTTP {status}" + (f"（{detail}）" if detail else ""),
				status_code=status,
			) from exc
		except httpx.RequestError as exc:
			# 与状态错误分支同口径脱敏：异常文本里出现密钥时不原样带进信封（Issue #2254）。
			raise AIServiceError(f"网络请求失败: {_mask_api_key(str(exc), self.api_key)}") from exc
		except httpx.InvalidURL as exc:
			# InvalidURL 不是 RequestError 的子类：手工改坏的 base_url 绕过配置校验
			# 直达这里时也要落成信封，而不是 traceback（Issue #1778）。它与相邻的
			# ValueError / UnicodeEncodeError 一样是请求没有发出的本地构造失败，按
			# 不可恢复的参数错误上报，否则调用方会对确定性失败反复重试（Issue #2285）。
			raise AIServiceError(
				f"AI 服务地址无效: {_mask_api_key(str(exc), self.api_key)}；请用 boss ai config --base-url 重新设置",
				recovery_hint="用 boss ai config --base-url 把服务地址改回有效地址后重试；原样重试不会成功",
				code="INVALID_PARAM",
				recoverable=False,
			) from exc
		except UnicodeEncodeError as exc:
			# UnicodeEncodeError 是 ValueError 的子类，必须排在前面。httpx 按 UTF-8 编码
			# 请求体，孤立代理字符（损坏文本里的 ``\ud800`` 等）编码不了；同一输入必然
			# 复现，不是网络问题（Issue #2254）。不回显原文，只报位置。
			raise AIServiceError(
				f"请求内容包含无法按 UTF-8 编码的字符（位置 {exc.start}，如孤立代理字符）",
				recovery_hint="清理输入文本里的损坏字符（如孤立代理字符）后重试；原样重试不会成功",
				code="INVALID_PARAM",
				recoverable=False,
			) from exc
		except ValueError as exc:
			# httpx 在本地构造客户端 / 请求体时抛裸 ValueError，请求尚未发出：代理环境
			# 变量用了不支持的 scheme（``socks4://`` / ``socks://`` / ``ftp://``）最常见，
			# 其次是请求参数无法序列化（如 temperature 为 NaN）（Issue #2254）。
			raise AIServiceError(
				f"请求无法构造: {_mask_api_key(str(exc), self.api_key)}；"
				f"请检查代理环境变量（{_PROXY_ENV_HINT}）与请求参数",
				recovery_hint=(
					f"检查代理环境变量（{_PROXY_ENV_HINT}）：使用 http:// / https:// / socks5:// 地址，"
					"或取消设置；同时核对模型请求参数后重试，原样重试不会成功"
				),
				code="INVALID_PARAM",
				recoverable=False,
			) from exc

		try:
			data = response.json()
		# RecursionError：3.11–3.13 的 json.loads 对极深嵌套响应抛它，按响应格式异常上报，
		# 与裸 ValueError（非法 JSON / 超长整数）走同一个 AIServiceError 信封分支。
		except (ValueError, RecursionError) as exc:
			raise AIServiceError(f"响应格式异常: {exc}") from exc
		choices = data.get("choices") if isinstance(data, dict) else None
		if not isinstance(choices, list) or not choices:
			# 有的网关用 HTTP 200 带回错误对象（限流 / 额度 / 鉴权）：读出服务商的说明，
			# 并用其中的状态码选恢复建议，而不是报 KeyError 的文本（Issue #1780）。
			detail = _error_detail(response, self.api_key)
			raise AIServiceError(
				f"API 返回了错误（{detail}）" if detail else "响应格式异常: 响应里没有 choices",
				status_code=_embedded_status(data),
			)
		try:
			choice = choices[0]
			content = choice["message"]["content"]
		except (KeyError, IndexError, TypeError) as exc:
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
