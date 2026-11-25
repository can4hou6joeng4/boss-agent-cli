"""把登录失败归类成带双通道 hints 的错误信封载荷（login 命令与 wizard 预检共用）。"""

import re
from typing import Any

import click

from boss_agent_cli.api.client import PlatformRateLimitError, PlatformRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.display import (
	boss_command_for_ctx,
	error_contract_for_code,
	login_action_for_ctx,
	risk_error_contract,
	risk_error_message,
	risk_lock_persistence_actions,
)
from boss_agent_cli.output import is_local_io_error, redact_sensitive_text
from boss_agent_cli.services.browser_runtime import is_browser_kernel_missing, patchright_install_command


def classify_login_error(exc: Exception, ctx: click.Context) -> dict[str, Any]:
	"""Return a user-facing, redacted login error envelope payload.

	The login flow intentionally remains unchanged; this helper only turns broad
	Cookie/CDP/QR/browser failures into actionable CLI diagnostics.

	hints 分两条受众通道（见 schema conventions.hints）：
	  next_actions     — 面向 Agent 的可执行命令
	  operator_actions — 面向真人操作者的自然语言指引（多半要离开终端完成）
	"""
	# 登录链路本身处理 cURL / Cookie / CDP 输入，底层异常（cookie 库、Playwright、
	# httpx）可能带 `wt2=...` / `at=...` 等会话凭据；所有文本启发式分支都会把
	# 这段文本拼进 message，入口统一脱敏一次（Issue #2337）。
	raw_message = redact_sensitive_text(str(exc) or exc.__class__.__name__)
	message = raw_message.lower()
	recovery_action = login_action_for_ctx(ctx)
	login_cmd = login_action_for_ctx(ctx)
	status_cmd = boss_command_for_ctx(ctx, "status", include_data_dir=True)
	doctor_cmd = boss_command_for_ctx(ctx, "doctor", include_data_dir=True)

	def payload(
		code: str,
		user_message: str,
		next_actions: list[str],
		operator_actions: list[str],
		recovery: str | None = None,
	) -> dict[str, Any]:
		# recoverable 以错误码登记表为准：风控码必须报不可恢复，否则 Agent 会把
		# 「重试登录」当成合法动作反复触发风控（Issue #1390）。未登记的码保留
		# 登录链路既有的可重试语义。
		recoverable, _ = error_contract_for_code(code, fallback_recoverable=True)
		return {
			"code": code,
			"message": user_message,
			"recoverable": recoverable,
			"recovery_action": recovery or recovery_action,
			"hints": {
				"next_actions": next_actions,
				"operator_actions": operator_actions,
			},
		}

	if isinstance(exc, PlatformRiskError):
		# 复用登录态的只读探测命中风控：与其他命令共用同一份终止契约，不建议重试登录。
		recovery, hints = risk_error_contract(exc.code, ctx=ctx)
		persistence_actions = risk_lock_persistence_actions(exc.details)
		if persistence_actions:
			# 锁只留在进程内时，新进程不会再被它阻止：必须让操作者知道（Issue #1270）。
			hints = {
				**(hints or {}),
				"operator_actions": [
					*((hints or {}).get("operator_actions") or []),
					*persistence_actions,
				],
			}
		return {
			"code": exc.code,
			# 风控信封不透传平台响应文本（可能带临时凭据），只给登记表文案。
			"message": risk_error_message(exc.code),
			"recoverable": False,
			"recovery_action": recovery,
			"hints": hints or None,
			"details": exc.details or None,
		}

	if isinstance(exc, PlatformRateLimitError):
		# 验证被限流不等于登录态失效：不清登录态、不降级到 CDP / 扫码，等待后重试
		# （与 #2092 的复用探测口径一致，Issue #2237）。
		return payload(
			"RATE_LIMITED",
			"登录态验证被平台限流，暂时无法确认 Cookie 是否有效",
			[],
			[
				"等待一段时间后再执行登录验证",
				"限流期间不要反复触发扫码或 cURL 导入，避免叠加请求",
			],
			recovery="等待平台限流窗口结束后重试登录",
		)

	if isinstance(exc, ProxyConfigurationError):
		# 代理环境变量非法是确定性配置错误：登录链路不重试、不回退直连，
		# 也不能把带凭据的代理 URL 透传到信封（Issue #1423）。
		return {
			"code": "INVALID_PARAM",
			"message": str(exc),
			"recoverable": False,
			"recovery_action": "修正或清除 HTTPS_PROXY/ALL_PROXY 等代理环境变量后重试",
			"hints": {
				"operator_actions": [
					"确认代理地址形如 scheme://host:port，端口为 1-65535 的整数",
					"不使用代理时请清除 HTTPS_PROXY/ALL_PROXY 等对应环境变量；CLI 不会回退为直连",
				]
			},
		}

	# 风控文本判定必须先于所有文本 / 类型启发式（本地 IO 除外）：Playwright 的
	# 超时文案常带导航 URL / Call log，命中安全页时是「timeout + security.html」
	# 混合文本；ValueError 也可能携带风控词。排在 timeout / ValueError / CDP
	# 之后会把硬边界降级成可重试错误并诱导 Agent 继续重试（Issue #2145、#2336）。
	# 数字码按完整词匹配：端口 4030、4290 里的子串不是平台状态码（Issue #2309）。
	if re.search(r"\b403\b|\bforbidden\b|风控|\brisk\b|security\.html|verify\.html|安全验证", message):
		# 风控是硬边界：恢复动作取登记表原文，不能沿用 login_action_for_ctx 的
		# 默认「重新登录」，否则信封会诱导 Agent 反复重试（Issue #1390）。
		# 平台响应文本可能带临时凭据，启发式分支同样只给登记表文案（Issue #2309）。
		_, risk_recovery = error_contract_for_code("LOGIN_RISK_CONTROL")
		return payload(
			"LOGIN_RISK_CONTROL",
			"登录请求可能触发平台风控",
			[],
			[
				"暂停自动化重试，改用浏览器手动确认账号状态",
				"降低请求频率，避免短时间重复登录或刷新",
				"必要时联系平台客服确认账号是否受限",
			],
			recovery=risk_recovery,
		)

	if is_local_io_error(exc):
		# 本地 IO 失败既可能发生在登录态保存阶段，也可能更早（临时目录、Cookie
		# 库文件）：不能断言「登录成功」，恢复动作同时覆盖两类目录（Issue #2311）。
		return payload(
			"LOCAL_IO_ERROR",
			f"本地文件操作失败，登录态可能未保存: {raw_message}",
			[],
			["确认登录态目录与临时目录可写、磁盘空间充足后重新执行登录"],
			recovery="修正本地目录权限或磁盘空间后重新执行登录",
		)

	if isinstance(exc, ValueError):
		# 选项互斥等用法错误：不是登录链路失败，按 INVALID_PARAM 契约返回，不建议重试登录。
		return {
			"code": "INVALID_PARAM",
			"message": raw_message,
			"recoverable": False,
			"recovery_action": "修正参数",
		}

	# 文本兜底要求「timed out」短语或紧跟在 timeout 后的分隔符 / 数字：路径或选项名
	# 里的 timeout 子串不再命中（Issue #2309）。
	if (
		isinstance(exc, TimeoutError)
		or re.search(r"\btimed[ -]?out\b|\btimeout\b(?=[ :]|\d|$)", message)
		or "超时" in raw_message
	):
		return payload(
			"LOGIN_TIMEOUT",
			f"登录等待超时: {raw_message}",
			[f"{login_cmd} --timeout 180"],
			[
				"确认二维码已完成扫码并在网页端授权登录",
				"网络较慢时可延长超时时间后重试",
				"如需复用已登录的 Chrome，请先以 --remote-debugging-port=9222 启动带调试端口的 Chrome，再重试登录",
			],
		)

	if is_browser_kernel_missing(message):
		install_command = patchright_install_command()
		return payload(
			"BROWSER_KERNEL_MISSING",
			f"patchright 浏览器内核缺失或与所需修订版不匹配: {raw_message}",
			[install_command, doctor_cmd, login_cmd],
			["浏览器内核安装完成后重新执行登录"],
			recovery=install_command,
		)

	# 限流不是风控（与 typed PlatformRateLimitError 同口径）：非 typed 的 429 /
	# 「too many requests」文本应等待限流窗口，而不是升级成硬停止（Issue #2309）。
	if re.search(r"\b429\b", message) or "too many requests" in message or "rate limit" in message:
		return payload(
			"RATE_LIMITED",
			"登录请求被平台限流，暂时无法完成验证",
			[],
			[
				"等待一段时间后再执行登录验证",
				"限流期间不要反复触发扫码或 cURL 导入，避免叠加请求",
			],
			recovery="等待平台限流窗口结束后重试登录",
		)

	if "cdp" in message or "chrome" in message or isinstance(exc, ConnectionError):
		return payload(
			"CDP_UNAVAILABLE",
			f"Chrome 调试连接不可用: {raw_message}",
			[login_cmd],
			[
				"以 --remote-debugging-port=9222 启动带调试端口的 Chrome 后重试，或去掉 --cdp 让命令自动降级到 Cookie / 扫码链路",
				"确认 --cdp-url 指向可访问的 Chrome DevTools 地址",
			],
		)

	# 401 只在「明确状态码」上下文里算登录过期：裸 `\b401\b` 会命中
	# `failed at step 401`，端口 4010 里的 401 子串同样不是状态码
	# （Issue #2309、#2335）。
	if (
		any(term in message for term in ("unauthorized", "expired", "过期", "未登录"))
		or re.search(r"\b(?:http|status|code|error|response|returned|got|with)[\s:=-]*401\b", message)
		or message.strip() == "401"
	):
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
			[f"{login_cmd} --cookie-source chrome"],
			[
				"确认浏览器已完成登录并进入平台首页",
				"若 Cookie 提取失败，可改用 --cookie-source 指定 chrome/firefox/edge",
				"如需通过 CDP 复用已登录的 Chrome，请先以 --remote-debugging-port=9222 启动带调试端口的 Chrome",
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
