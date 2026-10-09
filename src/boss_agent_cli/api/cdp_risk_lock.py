"""CDP 环境风控锁（code 37）。

CDP 模式下浏览器通道一旦拿到环境类 code 37，说明 Chrome 当前的 ``__zp_stoken__``
已被平台判定异常。CLI 自己无法像 BOSS 前端那样跑安全校验换出新 stoken，继续发请求
只会让同一个 stoken 一直吃 37。这里在数据目录记一把锁：

- 锁里只存 stoken 的 SHA-256 摘要和时间戳，**绝不落原值**；
- 之后每次 CDP 浏览器请求前，从 Chrome 的 ``context.cookies()``（本机 CDP 调用，
  不访问平台）读出当前 stoken 摘要：没变就拒绝发送；变了说明用户已在页面里
  完成校验、拿到新 stoken，自动解锁继续；
- ``boss doctor`` / ``boss status`` 只读本地文件展示锁状态，``boss clean --risk-lock``
  手动解除。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_FILENAME = "cdp_risk_lock.json"
STOKEN_COOKIE = "__zp_stoken__"
#: 记录时 Chrome 里没有 stoken cookie 的占位摘要；之后出现任何 stoken 都算「已变化」。
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
	"""一把 code 37 锁。``stoken_sha256 is None`` 表示记录时读不到 Chrome cookie。"""

	stoken_sha256: str | None
	locked_at: float
	cdp_url: str | None = None
	corrupt: bool = False

	def matches(self, current_hash: str | None) -> bool:
		"""当前 stoken 是否仍是被拦截的那一个（读不到 / 记录不全时按「仍被锁」处理）。"""
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
		return RiskLock(
			stoken_sha256=stoken_hash,
			locked_at=locked_at,
			cdp_url=str(cdp_url) if isinstance(cdp_url, str) and cdp_url else None,
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
) -> RiskLock:
	"""原子写入锁文件（0600）。``stoken_sha256`` 必须是摘要，不接受原值。"""
	lock = RiskLock(stoken_sha256=stoken_sha256, locked_at=time.time() if now is None else now, cdp_url=cdp_url)
	data_dir.mkdir(parents=True, exist_ok=True)
	path = lock_path(data_dir)
	tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
	payload = {
		"version": _LOCK_VERSION,
		"reason": "ENVIRONMENT_RISK",
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
		"stoken_recorded": lock.stoken_sha256 is not None and lock.stoken_sha256 != ABSENT_STOKEN,
	}
	if lock.locked_at > 0:
		info["locked_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lock.locked_at))
		info["age_seconds"] = max(0, int(current - lock.locked_at))
	return info


UNLOCK_OPERATOR_ACTIONS: tuple[str, ...] = (
	"在这个 CDP Chrome 里手动打开一个 BOSS 直聘职位列表页（如 https://www.zhipin.com/web/geek/jobs），确认页面能正常加载",
	"等几分钟让页面完成安全校验并换出新的 __zp_stoken__，再重试命令；stoken 变化后 CLI 会自动解锁",
	"确认已在页面里恢复但仍被拦时，可执行 boss clean --risk-lock 手动解除",
)
