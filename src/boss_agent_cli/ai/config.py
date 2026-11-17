"""AI service configuration management.

Handles API key encryption (Fernet), provider settings, and model configuration.
Reuses the auth salt file for key derivation.
"""

import hashlib
import json
import math
import os
import platform
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from boss_agent_cli.services.json_files import write_json_atomic
from boss_agent_cli.services.private_files import (
	DIRECTORY_MODE,
	FILE_MODE,
	chmod_best_effort,
	write_private_bytes,
)

PROVIDER_BASE_URLS: dict[str, str | None] = {
	"openai": "https://api.openai.com/v1",
	"deepseek": "https://api.deepseek.com/v1",
	"moonshot": "https://api.moonshot.cn/v1",
	"openrouter": "https://openrouter.ai/api/v1",
	"qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
	"zhipu": "https://open.bigmodel.cn/api/paas/v4",
	"siliconflow": "https://api.siliconflow.cn/v1",
	"ollama": "http://localhost:11434/v1",
	"vllm": "http://localhost:8000/v1",
	"custom": None,
}

def provider_endpoint(provider: str | None, base_url: str | None = None) -> str | None:
	"""解析实际生效的端点：显式 base_url 优先，否则查 provider 登记表。

	登记表里没有的名字（`--provider openai-cn` 这类笔误）与 provider=custom
	都会返回 None——配置里明明填了 provider，却没有任何可用端点。
	"""
	if base_url:
		return base_url
	if provider and provider in PROVIDER_BASE_URLS:
		return PROVIDER_BASE_URLS[provider]
	return None


def ai_int_setting(config: dict[str, Any], key: str, default: int, *, minimum: int = 1) -> int:
	"""读取 AI 配置里的正整数项；类型漂移给明确错误，由消费点决定如何降级。

	``load_config`` 只做默认值合并，不校验值类型：``ai_max_tokens`` 被手工
	改成数组或字符串时，裸 ``int()`` 会抛 ``TypeError``，被命令兜底成
	``INTERNAL_ERROR``（recovery 还写着「不要重试」），而自动回复会让整轮
	执行中止（Issue #1196）。``bool`` 是 ``int`` 子类，但不是合法上限；
	显式 ``0`` 也不能当成「缺省」悄悄改回默认值（与 ``crawl_int_setting``
	同口径）。
	"""
	value = config.get(key)
	if value is None:
		return default
	if not isinstance(value, int) or isinstance(value, bool):
		raise ValueError(f"AI 配置项 {key} 必须是整数，实际是 {type(value).__name__}")
	if value < minimum:
		raise ValueError(f"AI 配置项 {key} 不能小于 {minimum}")
	return value


class AIConfigValueError(ValueError):
	"""AI 本地配置项类型或取值错误（Issue #1428）。

	``recovery_hint`` 是给命令层生成修复指引用的子命令片段；不填时调用方沿用
	既有的数值项提示。异常本身仍是 ``ValueError``，与 ``ai_int_setting`` /
	``ai_float_setting`` 的契约一致。
	"""

	def __init__(self, message: str, *, recovery_hint: str | None = None) -> None:
		super().__init__(message)
		self.recovery_hint = recovery_hint


def ai_text_setting(config: dict[str, Any], key: str, *, default: str | None = None) -> str | None:
	"""读取 AI 配置里的字符串项；类型漂移给明确错误（Issue #1428）。

	``load_config`` 只合并默认值；``ai_model`` 被手工改成数组 / 数字后，服务层
	``_request_params`` 的 ``model.rsplit`` 抛 ``AttributeError``，每条 ``boss ai *``
	命令都落成不可恢复的 ``INTERNAL_ERROR``。``None`` 与缺键按默认值。
	"""
	value = config.get(key, default)
	if value is None:
		return default
	if not isinstance(value, str):
		raise AIConfigValueError(
			f"AI 配置项 {key} 必须是字符串，实际是 {type(value).__name__}",
			recovery_hint=f"ai config --{key.removeprefix('ai_').replace('_', '-')} <值>",
		)
	return value


def normalize_base_url(value: str) -> str:
	"""校验显式 base_url：必须是 http(s) 绝对地址（Issue #1431）。

	空白串、缺 scheme 或非 http(s) 的地址会被原样写进配置，之后每条 AI 命令由
	httpx 抛 ``UnsupportedProtocol``，被兜成可重试的 ``AI_API_ERROR``——同一输入
	必然复现。写入与解析端点时都走这里，报 ``INVALID_PARAM`` 级别的本地错误。
	"""
	text = value.strip()
	parsed = urlsplit(text)
	if not text or parsed.scheme not in {"http", "https"} or not parsed.netloc:
		raise AIConfigValueError(
			f"AI 配置项 ai_base_url 必须是 http(s):// 开头的绝对地址，实际是 {value.strip()!r}",
			recovery_hint="ai config --base-url https://api.example.com/v1",
		)
	return text


def ai_float_setting(config: dict[str, Any], key: str, default: float, *, minimum: float = 0.0) -> float:
	"""读取 AI 配置里的非负数值项（如 ``ai_temperature``）；漂移给明确错误。

	与 :func:`ai_int_setting` 同口径：``load_config`` 只合并默认值，``"hot"``
	这类坏值会被原样塞进请求 payload，由服务商返回 400，调用方只看到泛化的
	平台错误（Issue #1209）。``bool`` 不是合法温度；``None`` 与缺键按默认值。
	"""
	value = config.get(key)
	if value is None:
		return default
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise ValueError(f"AI 配置项 {key} 必须是数值，实际是 {type(value).__name__}")
	try:
		parsed = float(value)
	# 合法 JSON 的大整数转 float 会溢出：与 inf 同等按无效配置处理（Issue #1230）。
	except OverflowError as exc:
		raise ValueError(f"AI 配置项 {key} 必须是有限数值") from exc
	if not math.isfinite(parsed):
		raise ValueError(f"AI 配置项 {key} 必须是有限数值")
	if parsed < minimum:
		raise ValueError(f"AI 配置项 {key} 不能小于 {minimum}")
	return parsed


_DEFAULT_CONFIG: dict[str, Any] = {
	"ai_provider": None,
	"ai_model": None,
	"ai_base_url": None,
	"ai_temperature": 0.7,
	"ai_max_tokens": 4096,
}


class AIConfigStore:
	"""Manages AI service configuration with encrypted API key storage."""

	def __init__(self, data_dir: Path):
		self._data_dir = data_dir
		self._ai_dir = data_dir / "ai"
		# 密钥是 Fernet 密文，但加密密钥只由机器 ID 与 auth/salt 派生，不涉及用户
		# 口令：同机其他用户能读密文与 salt 就能解密。ai/ 与 auth/ 按 0700 创建，
		# 历史安装的宽松权限在打开时一并收紧（与 AuthManager 的 TokenStore 同口径）。
		self._ai_dir.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
		chmod_best_effort(self._ai_dir, DIRECTORY_MODE)
		self._key_path = self._ai_dir / "api_key.enc"
		self._config_path = self._ai_dir / "config.json"
		self._auth_dir = data_dir / "auth"
		if self._key_path.exists():
			chmod_best_effort(self._key_path, FILE_MODE)
		salt_path = self._auth_dir / "salt"
		if salt_path.exists():
			chmod_best_effort(salt_path, FILE_MODE)

	def _get_machine_id(self) -> str:
		"""Get a stable machine identifier for key derivation."""
		if override := os.getenv("BOSS_AGENT_MACHINE_ID"):
			return override
		fingerprint = "|".join([
			platform.node() or "unknown-node",
			platform.system() or "unknown-system",
			platform.machine() or "unknown-machine",
		])
		return hashlib.sha256(fingerprint.encode()).hexdigest()

	def _get_salt(self) -> bytes:
		"""Reuse auth salt file, or create one if it doesn't exist."""
		self._auth_dir.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
		chmod_best_effort(self._auth_dir, DIRECTORY_MODE)
		salt_path = self._auth_dir / "salt"
		if salt_path.exists():
			chmod_best_effort(salt_path, FILE_MODE)
			return salt_path.read_bytes()
		salt = os.urandom(16)
		write_private_bytes(salt_path, salt)
		return salt

	def _derive_key(self) -> bytes:
		"""Derive a Fernet key from machine ID and salt."""
		salt = self._get_salt()
		machine_id = self._get_machine_id()
		kdf = PBKDF2HMAC(
			algorithm=hashes.SHA256(),
			length=32,
			salt=salt,
			iterations=480000,
		)
		key = kdf.derive(machine_id.encode())
		return urlsafe_b64encode(key)

	def save_api_key(self, key: str) -> None:
		"""Encrypt and persist the API key."""
		fernet = Fernet(self._derive_key())
		encrypted = fernet.encrypt(key.encode("utf-8"))
		write_private_bytes(self._key_path, encrypted)

	def get_api_key(self) -> str | None:
		"""Load and decrypt the API key. Returns None if not set or decryption fails."""
		if not self._key_path.exists():
			return None
		fernet = Fernet(self._derive_key())
		try:
			plaintext = fernet.decrypt(self._key_path.read_bytes())
		except (InvalidToken, ValueError):
			return None
		return plaintext.decode("utf-8")

	def api_key_is_set(self) -> bool:
		"""是否存有可用的 API key；空串与纯空白按未设置处理（Issue #1499）。

		旧版本 `ai config --api-key ""` 会把空串加密落盘，``get_api_key()``
		返回空串而不是 ``None``；只看「是不是 None」会把未配置误报成已配置。
		"""
		key = self.get_api_key()
		return bool(key and key.strip())

	def clear_api_key(self) -> None:
		"""删除已存储的 API key（Issue #1499）。

		清除凭据走删文件而不是写入空串：空串仍是一个「已保存」的密文，
		既没有实际用途，又会让状态读取把未配置当成已配置。
		"""
		try:
			self._key_path.unlink()
		except FileNotFoundError:
			pass

	def save_config(self, **kwargs: Any) -> None:
		"""Save configuration, merging with existing values."""
		current = self.load_config()
		current.update(kwargs)
		write_json_atomic(self._config_path, current)

	def load_config(self) -> dict[str, Any]:
		"""Load configuration with defaults for missing keys."""
		config = dict(_DEFAULT_CONFIG)
		if self._config_path.exists():
			try:
				saved = json.loads(self._config_path.read_text(encoding="utf-8"))
				if isinstance(saved, dict):
					config.update(saved)
			# 超长整数（3.11+）与深嵌套同样按配置损坏处理，不让 AI 配置读取崩在解析上。
			except (ValueError, OSError, RecursionError):
				pass
		return config

	def get_base_url(self) -> str | None:
		"""Get the API base URL: user config takes priority, then provider lookup."""
		config = self.load_config()
		provider = config.get("ai_provider")
		base_url = config.get("ai_base_url")
		return provider_endpoint(
			str(provider) if provider else None,
			str(base_url) if base_url else None,
		)

	def unresolved_provider(self) -> str | None:
		"""provider 已填写却解析不出端点时返回 provider 名，否则返回 None。

		`--provider openai-cn` 这类笔误、或 provider=custom 没配 base_url，
		``get_base_url()`` 都是 None；服务层只会笼统地说「AI 服务未配置」，
		用户看不出真正缺的是端点而不是 key。
		"""
		if self.get_base_url():
			return None
		provider = self.load_config().get("ai_provider")
		return str(provider) if provider else None

	def is_configured(self) -> bool:
		"""Check if the service can actually be used (provider + model + api_key + endpoint)."""
		config = self.load_config()
		provider = config.get("ai_provider")
		model = config.get("ai_model")
		return bool(all([provider, model]) and self.api_key_is_set() and self.get_base_url())
