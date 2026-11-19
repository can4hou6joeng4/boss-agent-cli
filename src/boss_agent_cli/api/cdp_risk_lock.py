"""BOSS 平台风险的本地锁存，文件名沿用历史 CDP code 37 协议。

账号风险、环境风险和官方安全页都要求停止后由用户显式恢复。锁中仅保留风险原因、
业务码、时间和可用的 stoken SHA-256 摘要，绝不保存凭据原值。摘要变化不证明风险已解除，
也不允许改用 httpx、headless 或裸 CDP 绕过锁。

``boss doctor`` / ``boss status`` 只读展示本地锁；用户在官方页面确认恢复后执行
``boss clean --risk-lock`` 解除本地阻断。该命令不能解除或证明解除服务器风险。
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boss_agent_cli.api.zhipin_errors import normalize_risk_code
from boss_agent_cli.services.json_files import write_json_atomic

LOCK_FILENAME = "cdp_risk_lock.json"
#: 主锁写盘失败时的后备位置（Issue #1270）。cache 子目录不随 `clean --risk-lock`
#: 之外的清理被删除，读写路径与主位置同源，fail-closed 语义不变。
FALLBACK_LOCK_DIR = "cache"
STOKEN_COOKIE = "__zp_stoken__"
#: 记录时没有 stoken cookie 的诊断占位摘要，不用于决定恢复权限。
ABSENT_STOKEN = "absent"
_ZHIPIN_HOST = "zhipin.com"

_LOCK_VERSION = 1

#: 风险锁诊断时间的合理上界（2100-01-01）；超过按写坏处理，锁本身仍有效。
_MAX_TIMESTAMP = 4_102_444_800.0


def _finite_timestamp(raw: Any) -> float:
	"""``locked_at`` 只用于展示与诊断；超大整数 / ``inf`` 不能穿透到调用方。

	``float(10**400)`` 抛 ``OverflowError``，会绕过 ``read_lock`` 的 fail-closed
	分支；``inf`` 能通过读取，却会让 ``lock_status`` 的 ``time.localtime`` 崩溃。
	两种坏值都按「时间未知」处理，锁的阻断语义不变（Issue #1214）。
	"""
	try:
		parsed = float(raw or 0)
	except (OverflowError, TypeError, ValueError):
		return 0.0
	return parsed if math.isfinite(parsed) and 0 <= parsed <= _MAX_TIMESTAMP else 0.0


def lock_path(data_dir: Path) -> Path:
	return data_dir / LOCK_FILENAME


def lock_paths(data_dir: Path) -> tuple[Path, Path]:
	"""风险锁的候选位置：主位置在前，后备位置在后（Issue #1270）。"""
	return (lock_path(data_dir), data_dir / FALLBACK_LOCK_DIR / LOCK_FILENAME)


def hash_stoken(value: str | None) -> str:
	"""stoken → SHA-256 十六进制摘要；没有值时返回 ``ABSENT_STOKEN``。

	stoken 来自浏览器上下文，可能含非法 UTF-8 字节解码出的孤立代理字符
	（U+DCFF）。严格编码会抛 UnicodeEncodeError，并在风控异常传播路径上把它
	替换成内部错误、连锁都来不及落盘（Issue #1918）。摘要只作诊断，不参与
	自动解锁，用 ``surrogatepass`` 编码与 file_lock 对任意 run_id 的口径一致。
	"""
	if not value:
		return ABSENT_STOKEN
	return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def stoken_hash_from_cookies(cookies: Any) -> str:
	"""从 ``context.cookies()`` 的结果里取 ``__zp_stoken__`` 并返回摘要。"""
	for cookie in cookies or []:
		if not isinstance(cookie, dict) or cookie.get("name") != STOKEN_COOKIE:
			continue
		host = str(cookie.get("domain") or "").lstrip(".").lower()
		if host and host != _ZHIPIN_HOST and not host.endswith("." + _ZHIPIN_HOST):
			continue
		return hash_stoken(str(cookie.get("value") or ""))
	return ABSENT_STOKEN


@dataclass(frozen=True)
class RiskLock:
	"""本地风险锁；缺少摘要不影响阻断，历史文件默认仍表示环境类 code 37。"""

	stoken_sha256: str | None
	locked_at: float
	cdp_url: str | None = None
	corrupt: bool = False
	reason: str = "ENVIRONMENT_RISK"
	platform_code: int | None = 37

	def matches(self, current_hash: str | None) -> bool:
		"""仅比较诊断摘要；返回 False 也不允许自动解除风险锁。"""
		if self.corrupt or self.stoken_sha256 is None or current_hash is None:
			return True
		return current_hash == self.stoken_sha256


def _lock_path_mode(path: Path) -> int | None:
	"""仅对缺失路径返回 None；悬空链接保留链接自身的类型（Issue #1686）。"""
	# 3.14 的 Path.exists() / is_file() 会把权限等 OSError 吞掉并返回 False，
	# 风险锁会因此被当成「没有锁」而放行（Issue #1686）。
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return None
	if stat.S_ISLNK(mode):
		try:
			return path.stat().st_mode
		except FileNotFoundError:
			# 悬空链接的锁状态未知：交给调用方按损坏锁 fail-closed 处理。
			pass
	return mode


def _corrupt_lock() -> RiskLock:
	return RiskLock(stoken_sha256=None, locked_at=0.0, corrupt=True)


def _read_lock_file(path: Path) -> RiskLock:
	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
		if not isinstance(payload, dict):
			raise ValueError("lock payload is not an object")
		raw_hash = payload.get("stoken_sha256")
		stoken_hash = str(raw_hash) if isinstance(raw_hash, str) and raw_hash else None
		locked_at = _finite_timestamp(payload.get("locked_at"))
		cdp_url = payload.get("cdp_url")
		reason = "ACCOUNT_RISK" if payload.get("reason") == "ACCOUNT_RISK" else "ENVIRONMENT_RISK"
		raw_code = payload.get("platform_code", 36 if reason == "ACCOUNT_RISK" else 37)
		return RiskLock(
			stoken_sha256=stoken_hash,
			locked_at=locked_at,
			cdp_url=str(cdp_url) if isinstance(cdp_url, str) and cdp_url else None,
			reason=reason,
			platform_code=normalize_risk_code(raw_code),
		)
	# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError 与 3.11+ 的超长整数
	# 上限；RecursionError 覆盖深嵌套。损坏的锁按「仍被锁」处理（fail-closed），
	# 提示用户手动清除；解析异常穿透会让风控信封被兜成内部错误。
	except (OSError, ValueError, TypeError, OverflowError, RecursionError):
		return RiskLock(stoken_sha256=None, locked_at=0.0, corrupt=True)


def _read_lock_entry(data_dir: Path) -> tuple[RiskLock, Path] | None:
	"""返回命中的锁与实际文件路径；毫不过问主 / 后备之外的来源。"""
	corrupt: tuple[RiskLock, Path] | None = None
	for path in lock_paths(data_dir):
		try:
			mode = _lock_path_mode(path)
		except (OSError, ValueError):
			# 路径状态无法读取（权限、符号链接环、文件系统错误）时锁状态未知：
			# 按损坏锁阻断，不能当成「没有锁」（Issue #1686）。
			if corrupt is None:
				corrupt = (_corrupt_lock(), path)
			continue
		if mode is None:
			continue
		# FIFO / 设备不能直接 read_text，否则会阻塞在打开阶段；悬空链接、
		# 目录等路径也给不出可信锁内容，统一按损坏锁 fail-closed（Issue #1686）。
		if not stat.S_ISREG(mode):
			if corrupt is None:
				corrupt = (_corrupt_lock(), path)
			continue
		lock = _read_lock_file(path)
		if not lock.corrupt:
			return lock, path
		if corrupt is None:
			corrupt = (lock, path)
	return corrupt


def read_lock(data_dir: Path) -> RiskLock | None:
	"""读风险锁：主位置与后备位置任一存在即锁定（Issue #1270）。

	主位置写盘失败时后备位置可能持有锁；两份都可解析时以主位置为准，
	损坏的那份按 fail-closed 返回（不能因为备份较好而放行）。
	"""
	entry = _read_lock_entry(data_dir)
	return entry[0] if entry is not None else None


def write_lock(
	data_dir: Path,
	stoken_sha256: str | None,
	*,
	cdp_url: str | None = None,
	now: float | None = None,
	reason: str = "ENVIRONMENT_RISK",
	platform_code: int | None = 37,
) -> RiskLock:
	"""原子写入锁文件（0600）。``stoken_sha256`` 必须是摘要，不接受原值。

	主位置写失败时尝试后备位置（Issue #1270）：主文件本身被占用 / 权限损坏时，
	后备位置仍能让新进程 fail-closed 读到阻断。两处都失败才抛出最后一个
	``OSError``，调用方保留进程内锁并在信封里说明「新进程不再受保护」。
	写入成功后清掉另一位置的旧锁，避免主锁被手工删除后陈旧后备复活。
	"""
	lock = RiskLock(
		stoken_sha256=stoken_sha256, locked_at=time.time() if now is None else now, cdp_url=cdp_url,
		reason=reason, platform_code=platform_code,
	)
	payload = {
		"version": _LOCK_VERSION,
		"reason": lock.reason,
		"platform_code": lock.platform_code,
		"stoken_sha256": stoken_sha256,
		"locked_at": lock.locked_at,
		"cdp_url": cdp_url,
	}
	errors: list[OSError | UnicodeError] = []
	for path in lock_paths(data_dir):
		try:
			# 走统一的原子 JSON 写入：`mkstemp` 默认 0600，且用
			# `errors="backslashreplace"` 处理孤立代理字符（Issue #1294 / #1487）。
			# 风险锁里的 `cdp_url` 直接来自 CLI / 配置，非法 UTF-8 字节解码出的
			# U+DCFF 会让严格编码抛 `UnicodeEncodeError`；它是 `ValueError` 而不是
			# `OSError`，既不会触发后备位置，还会在记录风控的分支里覆盖原始
			# `PlatformRiskError`，把风控硬边界降级成可重试的本地错误。
			write_json_atomic(path, payload, indent=None)
		except (OSError, UnicodeError) as exc:
			errors.append(exc)
			continue
		for stale in lock_paths(data_dir):
			if stale == path:
				continue
			try:
				stale.unlink()
			except OSError:
				pass
		return lock
	raise errors[-1]


def clear_lock(data_dir: Path) -> bool:
	"""删除主位置与后备位置的锁文件；原本没有锁时返回 False。"""
	removed = False
	for path in lock_paths(data_dir):
		try:
			path.unlink()
			removed = True
		except FileNotFoundError:
			continue
	return removed


def raise_if_locked(data_dir: Path) -> None:
	"""在 BOSS 传输构造前只读检查本地锁，不连接浏览器或自动清理。"""
	from boss_agent_cli.api.client import PlatformRiskError

	lock = read_lock(data_dir)
	if lock is not None:
		raise PlatformRiskError.from_lock(lock)


def lock_status(data_dir: Path, *, now: float | None = None) -> dict[str, Any]:
	"""给 doctor / status 用的只读摘要：只读本地文件，不连浏览器、不访问网络。"""
	entry = _read_lock_entry(data_dir)
	if entry is None:
		return {"locked": False}
	lock, path = entry
	current = time.time() if now is None else now
	info: dict[str, Any] = {
		"locked": True,
		"path": str(path),
		"corrupt": lock.corrupt,
		"reason": lock.reason,
		"platform_code": lock.platform_code,
		"explicit_resume_required": True,
		"stoken_recorded": lock.stoken_sha256 is not None and lock.stoken_sha256 != ABSENT_STOKEN,
	}
	if lock.locked_at > 0:
		try:
			info["locked_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lock.locked_at))
			info["age_seconds"] = max(0, int(current - lock.locked_at))
		except (OverflowError, OSError, ValueError):
			# 展示层不得因为一个坏时间戳崩掉 doctor / status；锁仍然有效。
			info["locked_at"] = "未知时间"
	return info


def unlock_operator_actions(clean_command: str = "boss clean --risk-lock") -> tuple[str, ...]:
	"""风险锁的人工解锁步骤；调用方传入带本地上下文的清理命令。"""
	return (
		"在原会话的 BOSS 直聘官方页面完成验证，并确认目标业务页（如职位列表页）能正常加载，主页可访问不能替代该确认",
		f"确认风险已解除后，由用户执行 {clean_command} 解除本地阻断，再显式恢复原任务；令牌变化不会自动解锁",
		"该清理命令不解除服务器风险；若再次命中风险立即停止并重新锁存，Agent 不应自行清除锁",
	)


UNLOCK_OPERATOR_ACTIONS: tuple[str, ...] = unlock_operator_actions()
