"""本地敏感文件与目录的私有写入 / 权限收紧。

登录态与 AI 服务商密钥都是 Fernet 密文，但密钥只由机器 ID 与同目录 salt
派生，不涉及用户口令：同机其他用户只要能读到密文与 salt 就能解密。``Path.write_bytes``
与 ``Path.mkdir`` 都跟随进程 umask（常见 022 下分别是 0644 / 0755），因此这里统一走
``mkstemp``（默认 0600）同目录原子替换，并在创建 / 打开时尽力收紧权限。

Windows 与不支持 chmod 的挂载点上收紧会失败：按 best-effort 处理，不影响读写。
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import time
from pathlib import Path

from boss_agent_cli.services.file_lock import exclusive_file_lock
from boss_agent_cli.services.json_files import read_bytes_if_regular
from boss_agent_cli.services.output_path import refuse_non_regular_target

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
SALT_BYTES = 16

#: 锁被占用时等待另一进程创建 salt 的有界窗口：并发首次启动时持锁者通常几十
#: 毫秒内完成创建，2 秒足够覆盖慢盘上的 fsync；仍然缺失才按失败报出（Issue #2099）。
SALT_WAIT_SECONDS = 2.0
SALT_POLL_SECONDS = 0.1

#: 不支持硬链接的文件系统错误码：``link`` 发布失败时改走独占创建回退
#: （Issue #2200）。``ENOTSUP`` 与 ``EOPNOTSUPP`` 在部分平台取值相同，
#: 用集合去重。
_LINK_UNSUPPORTED_ERRNOS = frozenset(
	code for code in (getattr(errno, "ENOTSUP", None), getattr(errno, "EOPNOTSUPP", None), errno.EPERM, errno.EXDEV) if code is not None
)


def chmod_best_effort(path: Path, mode: int) -> None:
	"""尽力收紧权限；Windows 等平台语义有限，失败不影响读写。"""
	try:
		os.chmod(path, mode)
	except OSError:
		pass


def tighten_private_file(path: Path) -> None:
	"""把已存在的普通文件尽力收紧为 ``FILE_MODE``。

	符号链接不能跟随：``exists()`` 对「指向普通文件的链接」为 True，而
	``chmod`` 会改到链接目标，等于修改数据目录之外的文件（Issue #2206）。
	``lstat`` 只用 ``FileNotFoundError`` 判断缺失，权限等 ``OSError`` 静默
	跳过（#1675 口径）；目录 / FIFO 等特殊文件同样不收紧。
	"""
	try:
		mode = path.lstat().st_mode
	except (OSError, ValueError):
		return
	if stat.S_ISREG(mode):
		chmod_best_effort(path, FILE_MODE)


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
	except (OSError, ValueError):
		return
	if mode & 0o002 or not mode & 0o200:
		return
	chmod_best_effort(path, DIRECTORY_MODE)


def _read_salt(salt_path: Path) -> bytes | None:
	"""读取普通文件 salt；缺失返回 None，状态异常按 RuntimeError 报出。

	目录、FIFO、悬空链接不能直接 ``read_bytes()``：FIFO 会阻塞在打开阶段。
	权限错误保持 ``PermissionError`` 原样上抛，与既有调用方口径一致。
	"""
	try:
		return read_bytes_if_regular(salt_path)
	except PermissionError:
		raise
	except OSError as exc:
		raise RuntimeError(
			f"登录态 salt 路径状态异常（{exc}）；请确认 {salt_path} 是普通文件后重试"
		) from exc


def _validated_salt(raw: bytes, salt_path: Path) -> bytes:
	"""只接受 16 字节 salt；空文件或半截写入必须 fail-closed（Issue #1699）。"""
	if len(raw) != SALT_BYTES:
		raise RuntimeError(
			f"登录态 salt 长度异常（{len(raw)} 字节，应为 {SALT_BYTES}）："
			f"请备份后删除 {salt_path}，已有登录态与 AI 密钥需要重新配置"
		)
	return raw


def _create_salt(salt_path: Path) -> bytes:
	"""写入完整 salt 后再原子落位；并发创建者只能复用胜出值。"""
	fd, temporary_name = tempfile.mkstemp(prefix=f".{salt_path.name[:32]}.", dir=salt_path.parent)
	temporary = Path(temporary_name)
	try:
		payload = os.urandom(SALT_BYTES)
		with os.fdopen(fd, "wb") as handle:
			handle.write(payload)
			handle.flush()
			os.fsync(handle.fileno())
		try:
			os.link(temporary, salt_path)
		except FileExistsError:
			# 另一个进程（可能是旧版本）已经创建：绝不覆盖，读回胜出值。
			raw = _read_salt(salt_path)
			if raw is None:
				raise RuntimeError("登录态 salt 在创建过程中消失，请重试") from None
			return raw
		except OSError as exc:
			if exc.errno not in _LINK_UNSUPPORTED_ERRNOS:
				raise
			# exFAT / 部分 SMB / FUSE 等不支持硬链接的挂载点：改走独占创建
			# 回退，否则首次登录在保存登录态时确定性失败（Issue #2200）。
			return _create_salt_exclusive(salt_path, payload)
		chmod_best_effort(salt_path, FILE_MODE)
		return payload
	finally:
		temporary.unlink(missing_ok=True)


def _create_salt_exclusive(salt_path: Path, payload: bytes) -> bytes:
	"""``link`` 不可用时的回退：独占创建并直接写满 salt。

	文件在写满之前就对外可见（不同于 link 的原子落位），所以读取端在锁忙
	窗口内对长度不符的 salt 继续等待；写入失败必须删除本次创建的文件，
	不能留下永久的「长度异常」状态（Issue #2200）。
	"""
	try:
		fd = os.open(salt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, FILE_MODE)
	except FileExistsError:
		# 与 link 的 EEXIST 同口径：读回胜出值，绝不覆盖。
		raw = _read_salt(salt_path)
		if raw is None:
			raise RuntimeError("登录态 salt 在创建过程中消失，请重试") from None
		return raw
	try:
		written = 0
		while written < len(payload):
			written += os.write(fd, payload[written:])
		os.fsync(fd)
	except BaseException:
		os.close(fd)
		salt_path.unlink(missing_ok=True)
		raise
	os.close(fd)
	chmod_best_effort(salt_path, FILE_MODE)
	return payload


def load_or_create_private_salt(auth_dir: Path) -> bytes:
	"""读取共享 salt；缺失时由唯一创建者生成，禁止覆盖已有值。

	salt 决定 session.enc 与 ai/api_key.enc 的派生密钥：两个进程首次运行
	各自生成一次，后写盘的那份会让先保存的密文永久解不开（Issue #1633）。
	创建与落位全程持有刷新锁；支持硬链接时只有写满 16 字节后才通过 link
	出现，不支持硬链接的文件系统回退为独占创建（读取端用有界等待兜住半写
	窗口）；空文件、半截写入、目录 / FIFO / 悬空链接都按状态损坏失败，不再
	静默派生密钥（Issue #1688、#1699、#2200）。
	"""
	try:
		auth_dir.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
	except ValueError as exc:
		# 内嵌 NUL 等非法目录名在 mkdir 处只抛裸 ValueError；补上 filename
		# 后命令层按本地路径错误报，而不是未分类异常（Issue #2374）。
		raise OSError(errno.EINVAL, str(exc), str(auth_dir)) from exc
	chmod_best_effort(auth_dir, DIRECTORY_MODE)
	salt_path = auth_dir / "salt"
	with exclusive_file_lock(auth_dir / "refresh.lock") as acquired:
		if acquired:
			raw = _read_salt(salt_path)
			if raw is None:
				raw = _create_salt(salt_path)
			return _validated_salt(raw, salt_path)
	# 锁不可用时也不能生成第二份 salt：读到什么就用什么。锁忙通常表示另一个
	# 进程正在创建：走 link 时 salt 在写满 16 字节后才出现，走独占创建回退时
	# 文件会先以空文件出现，因此「缺失或长度不符」都要在有界窗口内继续等它
	# 落位，仍然不符合才按状态损坏报错（Issue #2099、#2200）。
	deadline = time.monotonic() + SALT_WAIT_SECONDS
	raw = _read_salt(salt_path)
	while (raw is None or len(raw) != SALT_BYTES) and time.monotonic() < deadline:
		time.sleep(SALT_POLL_SECONDS)
		raw = _read_salt(salt_path)
	if raw is None:
		raise RuntimeError("登录态 salt 尚未创建，且等待跨进程锁超时；请稍后重试")
	return _validated_salt(raw, salt_path)


def write_private_bytes(path: Path, data: bytes) -> None:
	"""原子写入仅当前用户可读的字节文件。

	``mkstemp`` 默认 0600 且同目录 ``os.replace`` 原子替换，避免半截文件与默认
	umask 的 0644 权限。写入器负责检查目标类型，避免替换已有的特殊文件。
	"""
	refuse_non_regular_target(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary: Path | None = None
	try:
		# 临时名只用截断后的目标名（最多 32 字符）：把完整目标名拼进前缀时，
		# 接近 NAME_MAX 的合法文件名会让 mkstemp 直接 ENAMETOOLONG
		# （Issue #2080，与 #1985/#1986 同口径）。
		fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name[:32]}.", dir=path.parent)
		temporary = Path(temporary_name)
		with os.fdopen(fd, "wb") as handle:
			handle.write(data)
			handle.flush()
			os.fsync(handle.fileno())
		refuse_non_regular_target(path)
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
