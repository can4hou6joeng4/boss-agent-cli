"""把 getUserInfo 的身份字段收成招聘者就绪判断。

2026-10-23 在已登录的招聘者沟通页 ``m.zhipin.com/web/chat/index`` 上读到：
``identity`` 为 1，``doubleIdentity`` 为 true，``recruit`` 为 false，页面导航是职位管理、
推荐牛人、沟通、牛人管理和招聘数据。因此 ``recruit`` 只是账号资料字段，不能当作
当前是否停在招聘者工作台。``identity`` 为 1 才表示这次登录态在招聘者侧。
``identity`` 为 0 视为同一字段的另一侧（求职者）。其他取值保持未知，不猜测。
"""

from __future__ import annotations

from typing import Any


def interpret_recruiter_identity(zp_data: dict[str, Any] | None) -> dict[str, Any]:
	"""Return a recruiter-readiness record. Does not include profile PII."""
	payload = zp_data if isinstance(zp_data, dict) else {}
	identity = _identity_side(payload.get("identity"))
	if identity == 1:
		side = "recruiter"
	elif identity == 0:
		side = "candidate"
	else:
		side = "unknown"
	return {
		"identity": identity,
		"side": side,
		"double_identity": _optional_bool(payload.get("doubleIdentity")),
		"profile_recruit": _optional_bool(payload.get("recruit")),
		"ready": side == "recruiter",
		"basis": "user_info.identity",
	}


def _identity_side(value: Any) -> int | None:
	if isinstance(value, bool):
		return None
	if isinstance(value, int) and value in (0, 1):
		return value
	if isinstance(value, str) and value in {"0", "1"}:
		return int(value)
	return None


def _optional_bool(value: Any) -> bool | None:
	if isinstance(value, bool):
		return value
	if isinstance(value, int) and value in (0, 1):
		return bool(value)
	if isinstance(value, str) and value in {"0", "1", "true", "false"}:
		return value in {"1", "true"}
	return None
