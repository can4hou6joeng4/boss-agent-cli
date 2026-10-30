"""BOSS 直聘响应错误的上下文分类。"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from boss_agent_cli.api.endpoints import CODE_ACCOUNT_RESTRICTED, CODE_IP_RESTRICTED

Code37Kind = Literal["token_expired", "environment_risk"]


def normalize_risk_code(value: Any) -> int | None:
	"""只保留明确观察到的平台阻断码，不根据异常或文案猜造业务码。"""
	return int(value) if value in (CODE_IP_RESTRICTED, str(CODE_IP_RESTRICTED), CODE_ACCOUNT_RESTRICTED, str(CODE_ACCOUNT_RESTRICTED), 35, "35", 36, "36", 37, "37", 38, "38") else None

_ENVIRONMENT_RISK_MARKERS = (
	"环境存在异常",
	"环境异常",
	"访问环境异常",
	"请求环境异常",
	"异常",
	"安全验证",
	"安全校验",
	"风险验证",
	"风控",
	"risk",
	"security",
	"captcha",
	"非法",
	"invalid",
	"token异常",
	"stoken异常",
)
_CREDENTIAL_MARKERS = (
	"__zp_stoken__",
	"stoken",
	"token",
	"登录",
	"凭证",
	"认证",
	"credential",
	"session",
)
_TOKEN_EXPIRED_MARKERS = ("过期", "失效", "expired")
_TOKEN_NOT_EXPIRED_MARKERS = (
	"未过期", "没有过期", "尚未过期", "未失效", "没有失效", "尚未失效",
	"not expired", "not yet expired", "hasn't expired", "haven't expired", "isn't expired",
)


def response_message(response: dict[str, Any]) -> str:
	# 兜底链与 platforms/base.py 的通用信封解析对齐（message/msg/error/zpData），
	# 保证 code 37 短路分支与 _classify_platform_error 取到同一份文案，
	# 避免 {"code": 37, "zpData": "..."} 这类响应的信封 message 退化为空串。
	return str(response.get("message") or response.get("msg") or response.get("error") or response.get("zpData") or "")


def classify_code_37(response: dict[str, Any]) -> Code37Kind:
	"""只在文案明确指向 token 时刷新；其余 code 37 保守视为环境风险。"""
	message = response_message(response).lower()
	if any(marker in message for marker in _ENVIRONMENT_RISK_MARKERS):
		return "environment_risk"
	if any(marker in message for marker in _TOKEN_NOT_EXPIRED_MARKERS):
		return "environment_risk"
	if any(marker in message for marker in _CREDENTIAL_MARKERS) and any(marker in message for marker in _TOKEN_EXPIRED_MARKERS):
		return "token_expired"
	return "environment_risk"


def is_security_verification_response(*, url: str = "", content_type: str = "", body: str = "") -> bool:
	"""识别官方安全页，不把 JSON 文案或业务页面里的验证说明当作安全页。"""
	try:
		if urlsplit(url).path.rstrip("/") in ("/web/passport/zp/verify.html", "/web/passport/zp/security.html"):
			return True
	except ValueError:
		pass
	if "html" not in content_type.lower() and not body.lstrip().lower().startswith(("<!doctype html", "<html")):
		return False
	title = re.search(r"<title\b[^>]*>(.*?)</title\s*>", body, re.IGNORECASE | re.DOTALL)
	return title is not None and any(marker in title.group(1) for marker in ("安全验证", "安全校验", "风险验证"))


def platform_risk_page_response(*, url: str = "", content_type: str = "", body: str = "") -> dict[str, Any] | None:
	"""将官方限制页转换为可复用的停止信号；未知原码显式保持 None。

	403.html 的 code 31 是 IP/访问环境限制，32 是账号限制。仅从该页 URL
	的唯一 code 参数读取原因；页面同时包含的隐藏文案不能证明当前命中哪一类。
	内部 code 36 仅用于复用历史风险信封，__cli_platform_code__ 始终保留原码/未知。
	"""
	code = None
	restricted = False
	try:
		parts = urlsplit(url)
		host = parts.hostname or ""
		if (not parts.netloc or host == "zhipin.com" or host.endswith(".zhipin.com")) and parts.path.rstrip("/") == "/web/passport/zp/403.html":
			restricted = True
			values = parse_qs(parts.query, keep_blank_values=True).get("code", [])
			candidate = normalize_risk_code(values[0]) if len(values) == 1 else None
			code = candidate if candidate in (CODE_IP_RESTRICTED, CODE_ACCOUNT_RESTRICTED) else None
	except ValueError:
		pass
	if not restricted and ("html" in content_type.lower() or body.lstrip().lower().startswith(("<!doctype html", "<html"))):
		title = re.search(r"<title\b[^>]*>\s*访问受限\s*</title\s*>", body, re.IGNORECASE)
		heading = re.search(r"<h[1-3]\b[^>]*>\s*访问受限\s*</h[1-3]\s*>", body, re.IGNORECASE)
		restricted = title is not None or (heading is not None and any(marker in body for marker in ("异常行为", "违规访问", "暂时被禁止访问")))
	if restricted:
		return {
			"code": code if code is not None else 36,
			"message": f"平台限制当前访问环境 (code {CODE_IP_RESTRICTED})，请在官方页面核实后显式恢复" if code == CODE_IP_RESTRICTED else "平台返回访问受限页，请在官方页面核实后显式恢复",
			"__cli_platform_code__": code,
			"__cli_risk_source__": "access_restricted_page",
		}
	if is_security_verification_response(url=url, content_type=content_type, body=body):
		return {
			"code": 36, "message": "平台返回官方安全验证页，请在官方页面处理后显式恢复",
			"__cli_platform_code__": None, "__cli_risk_source__": "security_page",
			# 带上命中的路径：否则用户和报告者都无法区分 verify.html（安全校验）
			# 与 security.html（风险页），Issue #488 就是卡在这一步。
			"path": _url_path(url),
		}
	return None


def _url_path(url: str) -> str:
	"""返回 URL 的 path；解析失败返回空串，绝不因为展示信息再抛一次。"""
	try:
		return urlsplit(url or "").path
	except ValueError:
		return ""


def raise_platform_risk_response(response: dict[str, Any], *, is_cdp: bool = False) -> None:
	"""在解包、刷新或降级前抛出平台风险；普通失败仍交给原调用者处理。"""
	# 惰性导入避免 client → zhipin_errors 的模块初始化环。
	from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError

	code = normalize_risk_code(response.get("code"))
	details: dict[str, Any] = {"platform_code": normalize_risk_code(response.get("__cli_platform_code__", code))}
	if response.get("__cli_risk_source__") in ("access_restricted_page", "security_page"):
		details["risk_source"] = response["__cli_risk_source__"]
	if path := response.get("path"):
		details["path"] = str(path)
	if code in (CODE_ACCOUNT_RESTRICTED, 35, 36):
		raise AccountRiskError(
			response_message(response) or "平台要求完成安全验证", is_cdp=is_cdp,
			details=details,
		)
	if code == CODE_IP_RESTRICTED or (code == 37 and classify_code_37(response) == "environment_risk"):
		raise EnvironmentRiskError(
			response_message(response) or "平台访问环境风险", is_cdp=is_cdp,
			details=details,
		)
