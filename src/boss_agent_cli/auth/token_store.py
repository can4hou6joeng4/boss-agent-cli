import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

_LOCK_TIMEOUT = 30


def _chmod_best_effort(path: Path, mode: int) -> None:
	"""尽力收紧权限；Windows 等平台语义有限，失败不影响读写。"""
	try:
		os.chmod(path, mode)
	except OSError:
		pass


def _write_private_bytes(path: Path, data: bytes) -> None:
	"""原子写入仅当前用户可读的字节文件。

	登录态虽是 Fernet 密文，但密钥只由机器 ID 与同目录 salt 派生，不涉及用户
	口令：同机其他用户只要能读这两个文件就能解密。``mkstemp`` 默认 0600 且
	同目录 ``os.replace`` 原子替换，避免半截文件与默认 umask 的 0644 权限。
	"""
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
	temporary = Path(temporary_name)
	try:
		with os.fdopen(fd, "wb") as handle:
			handle.write(data)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary, path)
	finally:
		temporary.unlink(missing_ok=True)
	_chmod_best_effort(path, 0o600)


class TokenStore:
	def __init__(self, auth_dir: Path):
		self._auth_dir = auth_dir
		self._auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
		# 历史安装的目录 / 文件可能是 0755 / 0644：打开时一并收紧。
		_chmod_best_effort(self._auth_dir, 0o700)
		self._session_path = auth_dir / "session.enc"
		self._salt_path = auth_dir / "salt"
		self._lock_path = auth_dir / "refresh.lock"
		for path in (self._salt_path, self._session_path):
			if path.exists():
				_chmod_best_effort(path, 0o600)

	def _get_machine_id(self) -> str:
		# 允许显式覆盖，便于测试 / CI / 沙箱环境稳定运行
		if override := os.getenv("BOSS_AGENT_MACHINE_ID"):
			return override

		system = platform.system()
		try:
			if system == "Darwin":
				if shutil.which("ioreg"):
					result = subprocess.run(
						["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
						capture_output=True,
						text=True,
						check=False,
					)
					for line in result.stdout.splitlines():
						if "IOPlatformUUID" in line:
							return line.split('"')[-2]
			elif system == "Linux":
				machine_id = Path("/etc/machine-id")
				if machine_id.exists():
					return machine_id.read_text().strip()
			elif system == "Windows":
				if shutil.which("reg"):
					result = subprocess.run(
						["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
						capture_output=True,
						text=True,
						check=False,
					)
					for line in result.stdout.splitlines():
						if "MachineGuid" in line:
							return line.split()[-1]
		except (OSError, ValueError):
			pass

		# 最终兜底：基于主机名+系统信息稳定生成一个本地 fallback id
		fingerprint = "|".join([
			platform.node() or "unknown-node",
			system or "unknown-system",
			platform.machine() or "unknown-machine",
		])
		return hashlib.sha256(fingerprint.encode()).hexdigest()

	def _get_salt(self) -> bytes:
		if self._salt_path.exists():
			return self._salt_path.read_bytes()
		salt = os.urandom(16)
		_write_private_bytes(self._salt_path, salt)
		return salt

	def _derive_key(self) -> bytes:
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

	def save(self, token_data: dict[str, Any]) -> None:
		fernet = Fernet(self._derive_key())
		plaintext = json.dumps(token_data, ensure_ascii=False).encode()
		encrypted = fernet.encrypt(plaintext)
		_write_private_bytes(self._session_path, encrypted)

	def load(self) -> dict[str, Any] | None:
		if not self._session_path.exists():
			return None
		fernet = Fernet(self._derive_key())
		encrypted = self._session_path.read_bytes()
		try:
			plaintext = fernet.decrypt(encrypted)
			decoded = json.loads(plaintext)
		except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
			return None
		if not isinstance(decoded, dict):
			return None
		return cast("dict[str, Any]", decoded)

	def clear(self) -> None:
		"""删除 session.enc 文件（保留 salt 供下次登录复用）"""
		self._session_path.unlink(missing_ok=True)

	@contextmanager
	def refresh_lock(self) -> Iterator[None]:
		"""原子文件锁：使用 O_CREAT|O_EXCL 避免 TOCTOU 竞态条件。"""
		deadline = time.time() + _LOCK_TIMEOUT
		fd = None
		while True:
			try:
				fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
				break  # 成功获取锁
			except FileExistsError:
				if time.time() > deadline:
					# 超时：锁可能是残留的，强制释放
					self._lock_path.unlink(missing_ok=True)
					continue
				time.sleep(0.5)
		try:
			if fd is not None:
				os.close(fd)
			yield
		finally:
			self._lock_path.unlink(missing_ok=True)
