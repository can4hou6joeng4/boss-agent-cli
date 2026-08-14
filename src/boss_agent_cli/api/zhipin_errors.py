"""BOSS 直聘响应错误的上下文分类。"""

from __future__ import annotations

from typing import Any, Literal

Code37Kind = Literal["token_expired", "environment_risk"]

_ENVIRONMENT_RISK_MARKERS = (
	"环境存在异常",
	"环境异常",
	"访问环境异常",
	"请求环境异常",
)
_TOKEN_EXPIRED_MARKERS = (
	"__zp_stoken__",
	"stoken",
	"token",
	"登录态过期",
	"登录状态过期",
	"登录状态已失效",
	"登录已过期",
	"凭证过期",
	"认证过期",
)


def response_message(response: dict[str, Any]) -> str:
	return str(response.get("message") or response.get("msg") or "")


def classify_code_37(response: dict[str, Any]) -> Code37Kind:
	"""只在文案明确指向 token 时刷新；其余 code 37 保守视为环境风险。"""
	message = response_message(response).lower()
	if any(marker in message for marker in _ENVIRONMENT_RISK_MARKERS):
		return "environment_risk"
	if any(marker in message for marker in _TOKEN_EXPIRED_MARKERS):
		return "token_expired"
	return "environment_risk"
