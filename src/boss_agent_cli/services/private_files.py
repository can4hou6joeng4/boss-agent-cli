"""本地敏感文件与目录的私有写入 / 权限收紧。

登录态与 AI 服务商密钥都是 Fernet 密文，但密钥只由机器 ID 与同目录 salt
派生，不涉及用户口令：同机其他用户只要能读到密文与 salt 就能解密。``Path.write_bytes``
与 ``Path.mkdir`` 都跟随进程 umask（常见 022 下分别是 0644 / 0755），因此这里统一走
``mkstemp``（默认 0600）同目录原子替换，并在创建 / 打开时尽力收紧权限。

Windows 与不支持 chmod 的挂载点上收紧会失败：按 best-effort 处理，不影响读写。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from boss_agent_cli.services.file_lock import exclusive_file_lock
from boss_agent_cli.services.output_path import refuse_symlink_target

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


def chmod_best_effort(path: Path, mode: int) -> None:
	"""尽力收紧权限；Windows 等平台语义有限，失败不影响读写。"""
	try:
		os.chmod(path, mode)
	except OSError:
		pass


def tighten_private_directory(path: Path) -> None:
	"""把本程序管理的数据目录尽力收紧为仅当前用户可访问。

	只收紧**已存在且可写**的目录：

	- 世界可写的共享位置（``/tmp`` 这类 1777）不是本程序的数据目录，收紧会
	  破坏同机其他进程；
	- 只读目录（如用户手动 ``chmod 0500`` 的数据目录）是显式选择，收紧会
	  替用户恢复写权限并掩盖原本该报的本地 IO 错误。

	新建目录由调用方用 ``mkdir(mode=DIRECTORY_MODE)`` 直接创建。
	"""
	try:
		mode = path.stat().st_mode
	except OSError:
		return
	if mode & 0o002 or not mode & 0o200:
		return
	chmod_best_effort(path, DIRECTORY_MODE)


def load_or_create_private_salt(auth_dir: Path) -> bytes:
	"""读取共享 salt；缺失时由唯一创建者生成，禁止覆盖已有值。

	salt 决定 session.enc 与 ai/api_key.enc 的派生密钥：两个进程首次运行
	各自生成一次，后写盘的那份会让先保存的密文永久解不开（Issue #1633）。
	``O_CREAT|O_EXCL`` 保证只有一个创建者；随后在刷新锁内重读，后到者必须
	复用胜出值。只有 ``FileNotFoundError`` 才表示 salt 确实缺失；权限、
	IO 等错误必须上抛，不能用新 salt 覆盖旧密文（Issue #1688）。
	"""
	auth_dir.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
	chmod_best_effort(auth_dir, DIRECTORY_MODE)
	salt_path = auth_dir / "salt"
	try:
		fd = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
	except FileExistsError:
		pass
	else:
		with os.fdopen(fd, "wb") as handle:
			handle.write(os.urandom(16))
	chmod_best_effort(salt_path, FILE_MODE)
	with exclusive_file_lock(auth_dir / "refresh.lock") as acquired:
		if acquired:
			return salt_path.read_bytes()
	# 锁不可用时也不能生成第二份 salt：读到什么就用什么；确实不存在才报错。
	try:
		return salt_path.read_bytes()
	except FileNotFoundError:
		raise RuntimeError("登录态 salt 尚未创建，且无法获取跨进程锁") from None


def write_private_bytes(path: Path, data: bytes) -> None:
	"""原子写入仅当前用户可读的字节文件。

	``mkstemp`` 默认 0600 且同目录 ``os.replace`` 原子替换，避免半截文件与默认
	umask 的 0644 权限。私有文件写入不经过 ``-o`` 预检，符号链接目标必须在这里
	直接拒绝：``os.replace`` 只会替换链接本身（Issue #1355）。
	"""
	refuse_symlink_target(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary: Path | None = None
	try:
		fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
		temporary = Path(temporary_name)
		with os.fdopen(fd, "wb") as handle:
			handle.write(data)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary, path)
	except OSError as error:
		# fd 级写入（write / flush / fsync）失败的异常没有 filename：补上目标路径，
		# 调用方才会把它归成本地文件错误，而不是一次值得重试的网络异常
		# （与 file_lock._acquire 同口径，Issue #1400）。
		if error.filename is None:
			error.filename = str(path)
		raise
	finally:
		if temporary is not None:
			temporary.unlink(missing_ok=True)
	chmod_best_effort(path, FILE_MODE)
