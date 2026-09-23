"""把登录失败归类成带双通道 hints 的错误信封载荷（login 命令与 wizard 预检共用）。"""

import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.display import boss_command_for_ctx, login_action_for_ctx, risk_error_contract


def classify_login_error(exc: Exception, ctx: click.Context) -> dict[str, object]:
	"""Return a user-facing, redacted login error envelope payload.

	The login flow intentionally remains unchanged; this helper only turns broad
	Cookie/CDP/QR/browser failures into actionable CLI diagnostics.

	hints 分两条受众通道（见 schema conventions.hints）：
	  next_actions     — 面向 Agent 的可执行命令
	  operator_actions — 面向真人操作者的自然语言指引（多半要离开终端完成）
	"""
	raw_message = str(exc) or exc.__class__.__name__
	message = raw_message.lower()
	recovery_action = login_action_for_ctx(ctx)
	login_cmd = login_action_for_ctx(ctx)
	status_cmd = boss_command_for_ctx(ctx, "status")
	doctor_cmd = boss_command_for_ctx(ctx, "doctor")

	def payload(
		code: str,
		user_message: str,
		next_actions: list[str],
		operator_actions: list[str],
		recovery: str | None = None,
	) -> dict[str, object]:
		return {
			"code": code,
			"message": user_message,
			"recoverable": True,
			"recovery_action": recovery or recovery_action,
			"hints": {
				"next_actions": next_actions,
				"operator_actions": operator_actions,
			},
		}

	if isinstance(exc, PlatformRiskError):
		# 复用登录态的只读探测命中风控：与其他命令共用同一份终止契约，不建议重试登录。
		recovery, hints = risk_error_contract(exc.code)
		return {
			"code": exc.code,
			"message": raw_message,
			"recoverable": False,
			"recovery_action": recovery,
			"hints": hints or None,
		}

	if isinstance(exc, ValueError):
		# 选项互斥等用法错误：不是登录链路失败，按 INVALID_PARAM 契约返回，不建议重试登录。
		return {
			"code": "INVALID_PARAM",
			"message": raw_message,
			"recoverable": False,
			"recovery_action": "修正参数",
		}

	if isinstance(exc, TimeoutError) or "timeout" in message or "超时" in raw_message:
		return payload(
			"LOGIN_TIMEOUT",
			f"登录等待超时: {raw_message}",
			[f"{login_cmd} --timeout 180", "boss-chrome"],
			[
				"确认二维码已完成扫码并在网页端授权登录",
				"网络较慢时可延长超时时间后重试",
				"如已打开本机 Chrome，可先启动带调试端口的 Chrome 再重试登录",
			],
		)

	if "executable doesn't exist" in message or "playwright was just installed" in message:
		return payload(
			"BROWSER_KERNEL_MISSING",
			f"patchright 浏览器内核缺失或与所需修订版不匹配: {raw_message}",
			["patchright install chromium", doctor_cmd, login_cmd],
			["浏览器内核安装完成后重新执行登录"],
			recovery="patchright install chromium",
		)

	if "cdp" in message or "chrome" in message or isinstance(exc, ConnectionError):
		return payload(
			"CDP_UNAVAILABLE",
			f"Chrome 调试连接不可用: {raw_message}",
			["boss-chrome", login_cmd],
			[
				"启动带调试端口的 Chrome 后重试，或去掉 --cdp 让命令自动降级到 Cookie / 扫码链路",
				"确认 --cdp-url 指向可访问的 Chrome DevTools 地址",
			],
		)

	if any(term in message for term in ("403", "forbidden", "风控", "risk", "rate limit", "too many")):
		return payload(
			"LOGIN_RISK_CONTROL",
			f"登录请求可能触发平台风控: {raw_message}",
			[],
			[
				"暂停自动化重试，改用浏览器手动确认账号状态",
				"降低请求频率，避免短时间重复登录或刷新",
				"必要时联系平台客服确认账号是否受限",
			],
		)

	if any(term in message for term in ("401", "unauthorized", "expired", "过期", "未登录")):
		return payload(
			"LOGIN_EXPIRED",
			f"登录态已失效或授权不足: {raw_message}",
			[login_cmd, status_cmd],
			[
				"重新登录并在网页端完成授权",
				"如使用 Cookie 提取，确认浏览器内目标平台仍处于登录状态",
			],
		)

	if any(term in message for term in ("cookie", "stoken", "token", "凭证")):
		return payload(
			"LOGIN_CREDENTIAL_EXTRACTION_FAILED",
			f"登录成功后提取凭证失败: {raw_message}",
			[f"{login_cmd} --cookie-source chrome", "boss-chrome"],
			[
				"确认浏览器已完成登录并进入平台首页",
				"若 Cookie 提取失败，可改用 --cookie-source 指定 chrome/firefox/edge",
			],
		)

	return payload(
		"NETWORK_ERROR",
		f"登录失败: {raw_message}",
		[doctor_cmd, login_cmd],
		[
			"检查网络连通性后重试",
			"如浏览器内已登录，可尝试用 --cookie-source 指定 chrome/firefox/edge",
			"若问题持续，请附带 doctor 诊断输出反馈",
		],
	)
