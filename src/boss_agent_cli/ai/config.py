"""AI service configuration management.

Handles API key encryption (Fernet), provider settings, and model configuration.
Reuses the auth salt file for key derivation.
"""

import errno
import hashlib
import json
import math
import os
import platform
import unicodedata
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from boss_agent_cli.services.json_files import read_bytes_if_regular, write_json_atomic
from boss_agent_cli.services.private_files import (
	DIRECTORY_MODE,
	load_or_create_private_salt,
	tighten_private_directory,
	tighten_private_file,
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


class AIConfigInvalidError(ValueError):
	"""AI 配置文件损坏，写入方不得用默认值覆盖（Issue #1706）。"""

	def __init__(self, path: Path, detail: str) -> None:
		super().__init__(
			f"{path} 无法作为 AI 配置读取（{detail}）；请备份并修正或删除该文件后重试"
		)
		self.path = path


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


#: 显式 base_url 的长度上限。真实端点远短于此；超长地址会让 httpx 抛 InvalidURL。
_BASE_URL_MAX_LENGTH = 2048
_BASE_URL_PREVIEW_LENGTH = 80
#: 合法 URL 不应出现的 Unicode 类别：控制（Cc）、格式（Cf，零宽 / BOM / 双向控制）、
#: 代理（Cs）、行与段分隔符（Zl / Zp）。
_BASE_URL_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def _base_url_preview(text: str) -> str:
	"""错误回显用的 base_url 前缀：去掉 query / fragment 与 userinfo。

	query 常带密钥（``?api-key=…``），userinfo 也可能带 token。非法与超长
	分支与 query 分支同口径，不能把凭据写进信封（Issue #2254、#2360）。
	"""
	prefix = text.split("?", 1)[0].split("#", 1)[0]
	head, sep, tail = prefix.partition("://")
	if sep and "@" in tail:
		_, _, host = tail.rpartition("@")
		return f"{head}{sep}{host}"
	return prefix


def normalize_base_url(value: str) -> str:
	"""校验显式 base_url：必须是 http(s) 绝对地址（Issue #1431）。

	空白串、缺 scheme 或非 http(s) 的地址会被原样写进配置，之后每条 AI 命令由
	httpx 抛 ``UnsupportedProtocol``，被兜成可重试的 ``AI_API_ERROR``——同一输入
	必然复现。写入与解析端点时都走这里，报 ``INVALID_PARAM`` 级别的本地错误。
	"""
	text = value.strip()
	if len(text) > _BASE_URL_MAX_LENGTH:
		# httpx 对超长地址抛 InvalidURL：写入成功、之后每条命令确定性失败。
		# 错误信息里不回显整串（可能有数万字符），只给长度与开头；query 里的
		# 密钥不能随预览泄露（Issue #2254、#2360）。
		preview = _base_url_preview(text)[:_BASE_URL_PREVIEW_LENGTH]
		raise AIConfigValueError(
			f"AI 配置项 ai_base_url 过长（{len(text)} 个字符，上限 {_BASE_URL_MAX_LENGTH}），"
			f"开头是 {preview!r}…",
			recovery_hint="ai config --base-url https://api.example.com/v1",
		)
	try:
		parsed = urlsplit(text)
		# 端口是惰性解析的：`http://host:abc/v1` 要读一次才会报错；未闭合的 IPv6
		# 括号则在 urlsplit 里直接抛。两者都是裸 ValueError，放过去会在写入时
		# 崩成 traceback，或存进配置后让每条 AI 命令在 httpx 里崩（Issue #1778）。
		_ = parsed.port
		# urlsplit 会把 `\t` / `\r` / `\n` 从 authority 里剔除，host 含空格也照样
		# 解析成功，但 httpx 对同一地址抛 InvalidURL：写入成功、之后每条命令
		# 确定性失败（Issue #2205）。合法 URL 不可能包含裸空白或控制字符。
		# 零宽空格、BOM、双向控制符等格式字符（Cf）与 C1 控制字符（Cc）肉眼不可见，
		# 写进配置后请求会打到错误的主机 / 路径；行 / 段分隔符（Zl / Zp）同理
		# （Issue #2254）。
		has_unsafe_chars = any(
			char.isspace() or unicodedata.category(char) in _BASE_URL_UNSAFE_CATEGORIES for char in text
		)
		valid = bool(text) and not has_unsafe_chars and parsed.scheme in {"http", "https"} and bool(parsed.hostname)
		# 服务层按 ``{base_url}/chat/completions`` 拼端点：带 query / fragment 的地址
		# 会把路径拼进 query 或 fragment 里，请求确定性打不到聊天端点（Issue #2254）。
		# 用分隔符本身判断：``https://host/v1?`` 的空 query 同样会吞掉后面的路径。
		has_query_or_fragment = "?" in text or "#" in text
	except ValueError:
		valid = False
		has_query_or_fragment = False
	if valid and has_query_or_fragment:
		# query 里常带密钥（``?api-key=…``）：只回显分隔符之前的部分。
		prefix = _base_url_preview(text)
		raise AIConfigValueError(
			f"AI 配置项 ai_base_url 不能带 query（?）或 fragment（#）：{prefix!r} 之后的部分会吞掉"
			"端点路径，请只保留到路径为止",
			recovery_hint="ai config --base-url https://api.example.com/v1",
		)
	if not valid:
		# 其他非法原因（空白、坏端口、非 http(s)）同样不能回显 query / userinfo
		# 里的凭据（Issue #2360）。
		preview = _base_url_preview(text)[:_BASE_URL_PREVIEW_LENGTH]
		raise AIConfigValueError(
			f"AI 配置项 ai_base_url 必须是 http(s):// 开头的绝对地址，实际是 {preview!r}",
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
		tighten_private_directory(self._ai_dir)
		self._key_path = self._ai_dir / "api_key.enc"
		self._config_path = self._ai_dir / "config.json"
		self._auth_dir = data_dir / "auth"
		# 只收紧普通文件：符号链接不能跟随（Issue #2206）。
		tighten_private_file(self._key_path)
		tighten_private_file(self._auth_dir / "salt")

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
		"""Reuse the auth salt; create it only when it is really missing.

		Shares the auth-side lifecycle so a failed existence check can never
		replace the salt that decrypts session.enc / api_key.enc (Issue #1688).
		"""
		try:
			return load_or_create_private_salt(self._auth_dir)
		except RuntimeError as exc:
			# salt 路径是目录 / FIFO / 长度异常时共享实现抛 RuntimeError；AI 命令没有
			# auth 侧的转换，裸异常会变成 traceback 而不是信封（Issue #1950）。
			# 带上 filename，全局分流才会按本地 IO 错误报，而不是可重试的网络错误。
			raise OSError(errno.EINVAL, str(exc), str(self._auth_dir / "salt")) from exc

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
		# 与 TokenStore 同口径（Issue #2044）：环境变量按 surrogateescape 解码，
		# 可能包含孤立代理字符；严格 UTF-8 编码会抛 UnicodeEncodeError，让同一
		# 机器 ID 在 auth 侧可用、AI 侧崩（Issue #2204）。
		key = kdf.derive(machine_id.encode("utf-8", "surrogatepass"))
		return urlsafe_b64encode(key)

	def save_api_key(self, key: str) -> None:
		"""Encrypt and persist the API key."""
		fernet = Fernet(self._derive_key())
		encrypted = fernet.encrypt(key.encode("utf-8"))
		write_private_bytes(self._key_path, encrypted)

	def get_api_key(self) -> str | None:
		"""Load and decrypt the API key. Returns None if not set or decryption fails.

		路径状态不可读或不是普通文件时上抛 ``OSError``：返回 None 会让调用方
		报 ``AI_NOT_CONFIGURED`` 并引导用户重新填 key，覆盖原密文（Issue #1709）。
		"""
		encrypted = read_bytes_if_regular(self._key_path)
		if encrypted is None:
			return None
		fernet = Fernet(self._derive_key())
		try:
			plaintext = fernet.decrypt(encrypted)
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
		"""Save configuration, merging with existing values.

		只有配置文件确实不存在才从默认值开始；JSON 损坏或顶层非对象时拒绝
		写入，避免用「默认值 + 本次参数」清空其他设置（Issue #1706）。
		"""
		current = self._load_config_for_write()
		current.update(kwargs)
		write_json_atomic(self._config_path, current)

	def _load_config_for_write(self) -> dict[str, Any]:
		"""写入前严格读取；与只读接口的容错降级分开。"""
		config = dict(_DEFAULT_CONFIG)
		raw = read_bytes_if_regular(self._config_path)
		if raw is None:
			return config
		try:
			saved = json.loads(raw)
		# 超长整数（3.11+）与深嵌套同样按配置损坏处理，不能继续覆盖写回。
		except (ValueError, RecursionError) as exc:
			raise AIConfigInvalidError(self._config_path, f"JSON 无法解析：{exc}") from exc
		if not isinstance(saved, dict):
			raise AIConfigInvalidError(self._config_path, "顶层必须是 JSON object")
		config.update(saved)
		return config

	def load_config(self) -> dict[str, Any]:
		"""Load configuration with defaults for missing keys.

		路径状态读不出来时上抛 ``OSError``，不能带着默认值整文件写回并
		丢掉已保存的 AI 设置（Issue #1695）。
		"""
		config = dict(_DEFAULT_CONFIG)
		raw = read_bytes_if_regular(self._config_path)
		if raw is not None:
			try:
				saved = json.loads(raw)
				if isinstance(saved, dict):
					config.update(saved)
			# 超长整数（3.11+）与深嵌套同样按配置损坏处理，不让 AI 配置读取崩在解析上。
			except (ValueError, RecursionError):
				pass
		return config

	def get_base_url(self) -> str | None:
		"""Get the API base URL: user config takes priority, then provider lookup."""
		config = self.load_config()
		provider = config.get("ai_provider")
		# 缺键 / None / 显式空串才是「未设置」；0 / False / 空容器等伪值必须走
		# 同一份类型校验，否则真值判断会把它们当成未设置并静默改用 provider
		# 默认端点（Issue #3123）。
		base_url = ai_text_setting(config, "ai_base_url")
		if base_url:
			# 手工编辑 / 旧版本写入的配置也走写入侧同一套校验：缺 scheme 的地址
			# 若原样透传，httpx 会抛 UnsupportedProtocol 并被兜成可重试的
			# AI_API_ERROR（Issue #2205）。损坏配置按带 recovery_hint 的本地
			# 配置错误报出。
			base_url = normalize_base_url(base_url)
		return provider_endpoint(
			str(provider) if provider else None,
			base_url if base_url else None,
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
		# 空白串是真值：只按 bool 判断会把纯空白 provider / model 报成已配置，
		# 而命令层（_create_ai_service / ai local smoke）都会在发请求前拒绝它
		# （Issue #2863）。类型漂移仍交给命令层的 ai_text_setting 报具体
		# CONFIG_INVALID，避免在这里被笼统归成 AI_NOT_CONFIGURED。
		if isinstance(provider, str) and not provider.strip():
			return False
		if isinstance(model, str) and not model.strip():
			return False
		return bool(all([provider, model]) and self.api_key_is_set() and self.get_base_url())
