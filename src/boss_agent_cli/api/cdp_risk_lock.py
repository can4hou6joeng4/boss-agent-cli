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
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boss_agent_cli.api.zhipin_errors import normalize_risk_code

LOCK_FILENAME = "cdp_risk_lock.json"
STOKEN_COOKIE = "__zp_stoken__"
#: 记录时没有 stoken cookie 的诊断占位摘要，不用于决定恢复权限。
ABSENT_STOKEN = "absent"
_ZHIPIN_HOST = "zhipin.com"

_LOCK_VERSION = 1


def lock_path(data_dir: Path) -> Path:
	return data_dir / LOCK_FILENAME


def hash_stoken(value: str | None) -> str:
	"""stoken → SHA-256 十六进制摘要；没有值时返回 ``ABSENT_STOKEN``。"""
	if not value:
		return ABSENT_STOKEN
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def read_lock(data_dir: Path) -> RiskLock | None:
	path = lock_path(data_dir)
	if not path.exists():
		return None
	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
		if not isinstance(payload, dict):
			raise ValueError("lock payload is not an object")
		raw_hash = payload.get("stoken_sha256")
		stoken_hash = str(raw_hash) if isinstance(raw_hash, str) and raw_hash else None
		locked_at = float(payload.get("locked_at") or 0)
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
	except (OSError, ValueError, TypeError):
		# 损坏的锁按「仍被锁」处理（fail-closed），提示用户手动清除。
		return RiskLock(stoken_sha256=None, locked_at=0.0, corrupt=True)


def write_lock(
	data_dir: Path,
	stoken_sha256: str | None,
	*,
	cdp_url: str | None = None,
	now: float | None = None,
	reason: str = "ENVIRONMENT_RISK",
	platform_code: int | None = 37,
) -> RiskLock:
	"""原子写入锁文件（0600）。``stoken_sha256`` 必须是摘要，不接受原值。"""
	lock = RiskLock(
		stoken_sha256=stoken_sha256, locked_at=time.time() if now is None else now, cdp_url=cdp_url,
		reason=reason, platform_code=platform_code,
	)
	data_dir.mkdir(parents=True, exist_ok=True)
	path = lock_path(data_dir)
	tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
	payload = {
		"version": _LOCK_VERSION,
		"reason": lock.reason,
		"platform_code": lock.platform_code,
		"stoken_sha256": stoken_sha256,
		"locked_at": lock.locked_at,
		"cdp_url": cdp_url,
	}
	tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
	try:
		os.chmod(tmp, 0o600)
	except OSError:
		pass
	os.replace(tmp, path)
	return lock


def clear_lock(data_dir: Path) -> bool:
	"""删除锁文件；原本没有锁时返回 False。"""
	try:
		lock_path(data_dir).unlink()
		return True
	except FileNotFoundError:
		return False


def raise_if_locked(data_dir: Path) -> None:
	"""在 BOSS 传输构造前只读检查本地锁，不连接浏览器或自动清理。"""
	from boss_agent_cli.api.client import PlatformRiskError

	lock = read_lock(data_dir)
	if lock is not None:
		raise PlatformRiskError.from_lock(lock)


def lock_status(data_dir: Path, *, now: float | None = None) -> dict[str, Any]:
	"""给 doctor / status 用的只读摘要：只读本地文件，不连浏览器、不访问网络。"""
	lock = read_lock(data_dir)
	if lock is None:
		return {"locked": False}
	current = time.time() if now is None else now
	info: dict[str, Any] = {
		"locked": True,
		"path": str(lock_path(data_dir)),
		"corrupt": lock.corrupt,
		"reason": lock.reason,
		"platform_code": lock.platform_code,
		"explicit_resume_required": True,
		"stoken_recorded": lock.stoken_sha256 is not None and lock.stoken_sha256 != ABSENT_STOKEN,
	}
	if lock.locked_at > 0:
		info["locked_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lock.locked_at))
		info["age_seconds"] = max(0, int(current - lock.locked_at))
	return info


def unlock_operator_actions(clean_command: str = "boss clean --risk-lock") -> tuple[str, ...]:
	"""风险锁的人工解锁步骤；调用方传入带本地上下文的清理命令。"""
	return (
		"在原会话的 BOSS 直聘官方页面完成验证，并确认目标业务页（如职位列表页）能正常加载，主页可访问不能替代该确认",
		f"确认风险已解除后，由用户执行 {clean_command} 解除本地阻断，再显式恢复原任务；令牌变化不会自动解锁",
		"该清理命令不解除服务器风险；若再次命中风险立即停止并重新锁存，Agent 不应自行清除锁",
	)


UNLOCK_OPERATOR_ACTIONS: tuple[str, ...] = unlock_operator_actions()
