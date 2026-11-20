import errno
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from boss_agent_cli.services.file_lock import exclusive_file_lock
from boss_agent_cli.services.json_files import read_bytes_if_regular
from boss_agent_cli.services.private_files import (
	chmod_best_effort,
	load_or_create_private_salt,
	tighten_private_file,
	write_private_bytes,
)

_LOCK_TIMEOUT = 30
_LOCK_POLL_SECONDS = 0.05
# 机器 ID 的外部命令输出按名字取值：格式漂移只应落到指纹兜底，不能
# 因下标越界抛 IndexError（Issue #1894）。
_IO_PLATFORM_UUID_RE = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')


class RefreshLockBusy(Exception):
	"""另一个进程持有刷新锁超过等待上限：本次没有刷新，也没有改动对方的锁。"""


class TokenStore:
	def __init__(self, auth_dir: Path):
		self._auth_dir = auth_dir
		self._auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
		# 历史安装的目录 / 文件可能是 0755 / 0644：打开时一并收紧。
		chmod_best_effort(self._auth_dir, 0o700)
		self._session_path = auth_dir / "session.enc"
		self._salt_path = auth_dir / "salt"
		self._lock_path = auth_dir / "refresh.lock"
		for path in (self._salt_path, self._session_path):
			# 只收紧普通文件：符号链接不能跟随（Issue #2206）。
			tighten_private_file(path)

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
							# 输出格式漂移（shim / 封装脚本）时按下标取值会抛
							# IndexError 并绕过指纹兜底（Issue #1894）。
							match = _IO_PLATFORM_UUID_RE.search(line)
							if match:
								return match.group(1)
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
							# 没有值字段的行（shim / 格式漂移）跳过继续找，不能让
							# 下标越界抛 IndexError 绕过指纹兜底（Issue #1894）。
							parts = line.split()
							if len(parts) >= 2 and parts[-1] not in {"MachineGuid", "REG_SZ"}:
								return parts[-1]
		except (OSError, ValueError):
			pass

		# 最终兜底：基于主机名+系统信息稳定生成一个本地 fallback id
		fingerprint = "|".join([
			platform.node() or "unknown-node",
			system or "unknown-system",
			platform.machine() or "unknown-machine",
		])
		return hashlib.sha256(fingerprint.encode("utf-8", "surrogatepass")).hexdigest()

	def _get_salt(self) -> bytes:
		# salt 决定 session.enc 的派生密钥；共享实现保证首个写入者获胜，
		# 读取失败时不会用新 salt 覆盖旧密文（Issue #1633 / #1688）。
		try:
			return load_or_create_private_salt(self._auth_dir)
		except RuntimeError as exc:
			# salt 路径是目录 / FIFO / 长度异常时共享实现抛 RuntimeError。status /
			# doctor 等命令没有 AI 侧的转换，裸异常会变成 traceback 或可重试的
			# NETWORK_ERROR（Issue #1996）。带上 filename 后全局分流按本地 IO 错误报。
			raise OSError(errno.EINVAL, str(exc), str(self._auth_dir / "salt")) from exc

	def _derive_key(self) -> bytes:
		salt = self._get_salt()
		machine_id = self._get_machine_id()
		kdf = PBKDF2HMAC(
			algorithm=hashes.SHA256(),
			length=32,
			salt=salt,
			iterations=480000,
		)
		# machine_id 只作密钥派生的本地输入（不参与安全边界），稳定唯一即可：
		# BOSS_AGENT_MACHINE_ID 经 surrogateescape 解码可含孤立代理字符，严格编码
		# 会让 load / save 直接抛 UnicodeEncodeError，绕过登录态的「未登录 / 本地
		# IO 错误」两种既有分流（Issue #2044）。surrogatepass 与 file_lock /
		# cdp_risk_lock 的口径一致。
		key = kdf.derive(machine_id.encode("utf-8", "surrogatepass"))
		return urlsafe_b64encode(key)

	def save(self, token_data: dict[str, Any]) -> None:
		fernet = Fernet(self._derive_key())
		# cookie / stoken 值可能含孤立代理字符（非法 UTF-8 字节经 surrogateescape
		# 解码，Issue #1918 已认定可达）。严格编码会抛 UnicodeEncodeError，被登录
		# 错误分流当成 INVALID_PARAM，把平台已成功的登录说成参数非法（Issue #2040）。
		# backslashreplace 写出合法的 ``\udcff`` 转义，读回值不变，与
		# services/json_files.py / output.py 同口径。
		plaintext = json.dumps(token_data, ensure_ascii=False).encode("utf-8", "backslashreplace")
		encrypted = fernet.encrypt(plaintext)
		write_private_bytes(self._session_path, encrypted)

	def load(self) -> dict[str, Any] | None:
		"""读取登录态；只有文件缺失才按未登录处理。

		路径元数据不可读、目录 / FIFO / 悬空链接与不可读文件必须上抛，
		否则 status / doctor 会报「未登录」并引导用户重新登录，覆盖那份可能
		仍然有效的密文（Issue #1709）。
		"""
		encrypted = read_bytes_if_regular(self._session_path)
		if encrypted is None:
			return None
		fernet = Fernet(self._derive_key())
		try:
			plaintext = fernet.decrypt(encrypted)
			decoded = json.loads(plaintext)
		# RecursionError：密文明文深嵌套时解析失败，与其它损坏登录态一样按未登录处理。
		except (InvalidToken, ValueError, TypeError, json.JSONDecodeError, RecursionError):
			return None
		if not isinstance(decoded, dict):
			return None
		return cast("dict[str, Any]", decoded)

	def clear(self) -> None:
		"""删除 session.enc 文件（保留 salt 供下次登录复用）"""
		self._session_path.unlink(missing_ok=True)

	@contextmanager
	def refresh_lock(self) -> Iterator[None]:
		"""跨进程刷新互斥：内核文件锁，持有者退出即释放。

		旧实现用 ``O_CREAT|O_EXCL`` 建文件当锁，等待超过 ``_LOCK_TIMEOUT`` 就删除
		锁文件继续。刷新走 CDP / headless 浏览器，合法持有时间可以超过 30 秒：
		等待方会删掉仍在使用的锁，先持有者退出时又会删掉后来者的锁，互斥失效
		（Issue #1554）。``file_lock`` 的 flock / msvcrt 语义由内核释放，不存在
		「残留锁」需要清理；超时也绝不删除对方的锁，而是按可重试失败返回。
		"""
		deadline = time.monotonic() + _LOCK_TIMEOUT
		while True:
			with exclusive_file_lock(self._lock_path) as acquired:
				if acquired:
					yield
					return
			if time.monotonic() >= deadline:
				raise RefreshLockBusy(f"另一个进程正在刷新登录态，等待 {_LOCK_TIMEOUT:g} 秒仍未释放锁")
			time.sleep(_LOCK_POLL_SECONDS)
