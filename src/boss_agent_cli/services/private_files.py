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


def write_private_bytes(path: Path, data: bytes) -> None:
	"""原子写入仅当前用户可读的字节文件。

	``mkstemp`` 默认 0600 且同目录 ``os.replace`` 原子替换，避免半截文件与默认
	umask 的 0644 权限。
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
	chmod_best_effort(path, FILE_MODE)
