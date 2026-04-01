import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

_LOCK_TIMEOUT = 30


class TokenStore:
	def __init__(self, auth_dir: Path):
		self._auth_dir = auth_dir
		self._auth_dir.mkdir(parents=True, exist_ok=True)
		self._session_path = auth_dir / "session.enc"
		self._salt_path = auth_dir / "salt"
		self._lock_path = auth_dir / "refresh.lock"

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
		self._salt_path.write_bytes(salt)
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

	def save(self, token_data: dict) -> None:
		fernet = Fernet(self._derive_key())
		plaintext = json.dumps(token_data, ensure_ascii=False).encode()
		encrypted = fernet.encrypt(plaintext)
		self._session_path.write_bytes(encrypted)

	def load(self) -> dict | None:
		if not self._session_path.exists():
			return None
		fernet = Fernet(self._derive_key())
		encrypted = self._session_path.read_bytes()
		try:
			plaintext = fernet.decrypt(encrypted)
		except (InvalidToken, ValueError):
			return None
		return json.loads(plaintext)

	def clear(self) -> None:
		"""删除 session.enc 文件（保留 salt 供下次登录复用）"""
		self._session_path.unlink(missing_ok=True)

	@contextmanager
	def refresh_lock(self):
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
