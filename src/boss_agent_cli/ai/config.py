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

PROVIDER_BASE_URLS: dict[str, str | None] = {
	"openai": "https://api.openai.com/v1",
	"deepseek": "https://api.deepseek.com/v1",
	"moonshot": "https://api.moonshot.cn/v1",
	"openrouter": "https://openrouter.ai/api/v1",
	"qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
	"zhipu": "https://open.bigmodel.cn/api/paas/v4",
	"siliconflow": "https://api.siliconflow.cn/v1",
	"custom": None,
}

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
		self._ai_dir.mkdir(parents=True, exist_ok=True)
		self._key_path = self._ai_dir / "api_key.enc"
		self._config_path = self._ai_dir / "config.json"
		self._auth_dir = data_dir / "auth"

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
		self._auth_dir.mkdir(parents=True, exist_ok=True)
		salt_path = self._auth_dir / "salt"
		if salt_path.exists():
			return salt_path.read_bytes()
		salt = os.urandom(16)
		salt_path.write_bytes(salt)
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
		self._key_path.write_bytes(encrypted)

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
		self._config_path.write_text(
			json.dumps(current, ensure_ascii=False, indent=2),
			encoding="utf-8",
		)

	def load_config(self) -> dict[str, Any]:
		"""Load configuration with defaults for missing keys."""
		config = dict(_DEFAULT_CONFIG)
		if self._config_path.exists():
			try:
				saved = json.loads(self._config_path.read_text(encoding="utf-8"))
				config.update(saved)
			except (json.JSONDecodeError, OSError):
				pass
		return config

	def get_base_url(self) -> str | None:
		"""Get the API base URL: user config takes priority, then provider lookup."""
		config = self.load_config()
		base_url = config.get("ai_base_url")
		if base_url:
			return str(base_url)
		provider = config.get("ai_provider")
		if provider and provider in PROVIDER_BASE_URLS:
			return PROVIDER_BASE_URLS[provider]
		return None

	def is_configured(self) -> bool:
		"""Check if all required settings are present (provider + model + api_key)."""
		config = self.load_config()
		provider = config.get("ai_provider")
		model = config.get("ai_model")
		api_key = self.get_api_key()
		return all([provider, model, api_key])
