"""AI service configuration management.

Handles API key encryption (Fernet), provider settings, and model configuration.
Reuses the auth salt file for key derivation.
"""

import hashlib
import json
import os
import platform
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any

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
			except (json.JSONDecodeError, UnicodeDecodeError, OSError):
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
		api_key = self.get_api_key()
		return bool(all([provider, model, api_key]) and self.get_base_url())
