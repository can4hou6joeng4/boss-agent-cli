from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STOKEN_FRESH_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class PlatformAuthConfig:
	auth_dir_suffix: tuple[str, ...]
	primary_cookie: str
	secondary_token_label: str
	secondary_token_key: str
	aux_cookies: tuple[str, ...]
	cookie_domain_label: str
	site_url: str
	site_host: str
	login_action: str
	recruiter_read_supported: bool


@dataclass(frozen=True)
class AuthHealthCheck:
	name: str
	status: str
	detail: str
	recovery_action: str | None = None
	hint: str | None = None

	def to_dict(self) -> dict[str, Any]:
		data: dict[str, Any] = {
			"name": self.name,
			"status": self.status,
			"detail": self.detail,
		}
		if self.recovery_action:
			data["recovery_action"] = self.recovery_action
		if self.hint:
			data["hint"] = self.hint
		return data


@dataclass(frozen=True)
class AuthHealthReport:
	platform: str
	auth_dir: Path
	session_path: Path
	salt_path: Path
	token_present: bool
	auth_state: str
	summary: str
	primary_cookie: str
	secondary_token_label: str
	recovery_action: str | None
	checks: tuple[AuthHealthCheck, ...]

	def checks_as_dicts(self) -> list[dict[str, Any]]:
		return [check.to_dict() for check in self.checks]

	def public_summary(self) -> dict[str, Any]:
		return {
			"platform": self.platform,
			"auth_state": self.auth_state,
			"summary": self.summary,
			"token_present": self.token_present,
			"primary_name": self.primary_cookie,
			"secondary_name": self.secondary_token_label,
			"recovery_action": self.recovery_action,
		}


PLATFORM_AUTH_CONFIG: dict[str, PlatformAuthConfig] = {
	"zhipin": PlatformAuthConfig(
		auth_dir_suffix=(),
		primary_cookie="wt2",
		secondary_token_label="stoken",
		secondary_token_key="stoken",
		aux_cookies=("wbg", "zp_at"),
		cookie_domain_label="zhipin",
		site_url="https://www.zhipin.com/",
		site_host="zhipin.com",
		login_action="boss login",
		recruiter_read_supported=True,
	),
	"zhilian": PlatformAuthConfig(
		auth_dir_suffix=("zhilian",),
		primary_cookie="at",
		secondary_token_label="x-zp-client-id",
		secondary_token_key="x_zp_client_id",
		aux_cookies=("rt",),
		cookie_domain_label="zhaopin",
		site_url="https://www.zhaopin.com/",
		site_host="zhaopin.com",
		login_action="boss --platform zhilian login",
		recruiter_read_supported=True,
	),
}


def auth_config_for_platform(platform: str) -> PlatformAuthConfig:
	return PLATFORM_AUTH_CONFIG.get(platform, PLATFORM_AUTH_CONFIG["zhipin"])


def auth_dir_for_platform(data_dir: Path, platform: str) -> Path:
	auth_dir = data_dir / "auth"
	for suffix in auth_config_for_platform(platform).auth_dir_suffix:
		auth_dir = auth_dir / suffix
	return auth_dir


def assess_auth_health(
	data_dir: Path,
	*,
	platform: str = "zhipin",
	token: dict[str, Any] | None,
	now: float | None = None,
) -> AuthHealthReport:
	config = auth_config_for_platform(platform)
	auth_dir = auth_dir_for_platform(data_dir, platform)
	session_path = auth_dir / "session.enc"
	salt_path = auth_dir / "salt"
	current_time = time.time() if now is None else now

	cookies = token.get("cookies", {}) if isinstance(token, dict) else {}
	if not isinstance(cookies, dict):
		cookies = {}

	has_token = token is not None
	has_cookies = bool(cookies)
	has_primary = _has_primary_cookie(config, cookies)
	has_secondary = _has_secondary_token(config, token)
	login_action = config.login_action

	checks: list[AuthHealthCheck] = []

	if has_token:
		checks.append(AuthHealthCheck(
			"credential_file",
			"ok",
			f"登录态可读取: {session_path}",
		))
		checks.append(AuthHealthCheck(
			"auth_session",
			"ok",
			f"检测到登录态文件: {session_path}（cookies={len(cookies)}, {config.secondary_token_label}={'存在' if has_secondary else '缺失'}）",
		))
	else:
		status = "error" if session_path.exists() else "warn"
		detail = f"检测到 session 文件但无法解密/已损坏: {session_path}" if session_path.exists() else "未检测到本地登录态"
		recovery = f"{login_action}；如为旧密钥残留，可先 boss logout" if session_path.exists() else login_action
		checks.append(AuthHealthCheck("credential_file", status, detail, recovery))
		checks.append(AuthHealthCheck("auth_session", status, detail, recovery))

	checks.append(AuthHealthCheck(
		"cookie_presence",
		"ok" if has_cookies else "warn",
		f"已保存 {len(cookies)} 个 Cookie" if has_cookies else "未检测到 Cookie",
		None if has_cookies else login_action,
	))
	checks.append(AuthHealthCheck(
		"wt2_presence",
		"ok" if has_primary else ("warn" if has_token else "error"),
		(
			f"关键 Cookie {config.primary_cookie} 存在"
			if has_primary
			else f"关键 Cookie {config.primary_cookie} 缺失"
		),
		None if has_primary else f"boss logout && {login_action}" if has_token else login_action,
	))
	checks.append(_stoken_presence_check(config, has_token=has_token, has_secondary=has_secondary))
	checks.append(_stoken_freshness_check(config, session_path, has_token=has_token, has_secondary=has_secondary, now=current_time))
	checks.append(_auth_token_quality_check(config, has_token=has_token, has_primary=has_primary, has_secondary=has_secondary))
	checks.append(_login_preflight_check(config, has_token=has_token, has_primary=has_primary, has_secondary=has_secondary))
	checks.append(_cookie_completeness_check(config, cookies, login_action=login_action))
	checks.extend(_capability_readiness_checks(config, has_token=has_token, has_primary=has_primary, has_secondary=has_secondary))

	auth_state = _auth_state(has_token=has_token, has_primary=has_primary, has_secondary=has_secondary)
	summary = _summary_for_checks(checks)
	recovery_action = _first_recovery_action(checks)
	return AuthHealthReport(
		platform=platform,
		auth_dir=auth_dir,
		session_path=session_path,
		salt_path=salt_path,
		token_present=has_token,
		auth_state=auth_state,
		summary=summary,
		primary_cookie=config.primary_cookie,
		secondary_token_label=config.secondary_token_label,
		recovery_action=recovery_action,
		checks=tuple(checks),
	)


def _has_secondary_token(config: PlatformAuthConfig, token: dict[str, Any] | None) -> bool:
	if not isinstance(token, dict):
		return False
	if config.secondary_token_key == "stoken":
		return bool(token.get("stoken"))
	return bool(token.get(config.secondary_token_key) or token.get("client_id"))


def _has_primary_cookie(config: PlatformAuthConfig, cookies: dict[str, Any]) -> bool:
	if cookies.get(config.primary_cookie):
		return True
	return config.cookie_domain_label == "zhaopin" and bool(cookies.get("zp_token"))


def _stoken_presence_check(config: PlatformAuthConfig, *, has_token: bool, has_secondary: bool) -> AuthHealthCheck:
	if config.secondary_token_key != "stoken":
		return AuthHealthCheck(
			"stoken_presence",
			"ok",
			f"{config.site_host} 不使用 __zp_stoken__；改用 {config.secondary_token_label} 评估",
		)
	if has_secondary:
		return AuthHealthCheck("stoken_presence", "ok", "__zp_stoken__ 已保存")
	return AuthHealthCheck(
		"stoken_presence",
		"warn" if has_token else "error",
		"__zp_stoken__ 缺失，二维码或 Cookie 提取可能只得到部分登录态",
		"以 Chrome CDP 远程调试端口启动浏览器后运行 boss login --cdp，或重新执行 boss login",
	)


def _stoken_freshness_check(
	config: PlatformAuthConfig,
	session_path: Path,
	*,
	has_token: bool,
	has_secondary: bool,
	now: float,
) -> AuthHealthCheck:
	if config.secondary_token_key != "stoken":
		return AuthHealthCheck(
			"stoken_freshness",
			"ok",
			f"{config.site_host} 无 __zp_stoken__ 新鲜度要求",
		)
	if not has_token or not has_secondary:
		return AuthHealthCheck(
			"stoken_freshness",
			"warn",
			"无法评估 stoken 新鲜度：stoken 缺失",
			"重新登录或通过 CDP 刷新登录态",
		)
	if not session_path.exists():
		return AuthHealthCheck(
			"stoken_freshness",
			"ok",
			"stoken 存在；当前测试/模拟环境未提供 session 文件时间",
		)
	age_seconds = max(0.0, now - session_path.stat().st_mtime)
	age_hours = age_seconds / 3600
	if age_seconds <= STOKEN_FRESH_SECONDS:
		return AuthHealthCheck(
			"stoken_freshness",
			"ok",
			f"session 文件约 {age_hours:.1f} 小时前更新；stoken 可能仍新鲜",
		)
	return AuthHealthCheck(
		"stoken_freshness",
		"warn",
		f"session 文件约 {age_hours:.1f} 小时前更新；stoken 可能已过期",
		"运行 boss status --live 验证；失败时通过 CDP 或 boss login 刷新",
	)


def _auth_token_quality_check(
	config: PlatformAuthConfig,
	*,
	has_token: bool,
	has_primary: bool,
	has_secondary: bool,
) -> AuthHealthCheck:
	if not has_token:
		return AuthHealthCheck(
			"auth_token_quality",
			"warn",
			"未检测到可评估的登录态",
			config.login_action,
		)
	if has_primary and has_secondary:
		return AuthHealthCheck(
			"auth_token_quality",
			"ok",
			f"登录态完整：{config.primary_cookie}/{config.secondary_token_label} 均存在",
			hint="可运行 boss status --live 做一次只读在线验证",
		)
	if has_primary and not has_secondary:
		return AuthHealthCheck(
			"auth_token_quality",
			"warn",
			f"登录态部分可用：{config.primary_cookie} 存在，但 {config.secondary_token_label} 缺失",
			"以 Chrome CDP 远程调试端口启动浏览器后运行 boss login --cdp，或重新执行 boss login",
		)
	if not has_primary and has_secondary:
		return AuthHealthCheck(
			"auth_token_quality",
			"error",
			f"登录态异常：{config.secondary_token_label} 存在，但关键 Cookie {config.primary_cookie} 缺失",
			f"boss logout && {config.login_action}",
		)
	return AuthHealthCheck(
		"auth_token_quality",
		"error",
		f"登录态无效：{config.primary_cookie}/{config.secondary_token_label} 均缺失",
		config.login_action,
	)


def _login_preflight_check(
	config: PlatformAuthConfig,
	*,
	has_token: bool,
	has_primary: bool,
	has_secondary: bool,
) -> AuthHealthCheck:
	"""Summarise whether login-gated commands may run before making platform requests."""
	if has_token and has_primary and has_secondary:
		return AuthHealthCheck(
			"login_preflight",
			"ok",
			f"登录前置通过：{config.primary_cookie}/{config.secondary_token_label} 均存在，可执行只读请求",
			hint="仍建议用 boss status --live 做低频只读验证；遇到风控立即停止自动化访问",
		)
	if has_token and has_primary:
		return AuthHealthCheck(
			"login_preflight",
			"warn",
			f"登录前置部分通过：{config.primary_cookie} 存在，但 {config.secondary_token_label} 缺失；只读请求可能失败",
			"通过 CDP 或 boss login 刷新登录态后再执行需要平台请求的命令",
			hint="不要在缺少二级令牌时重试高频请求；优先刷新登录态",
		)
	if has_token:
		return AuthHealthCheck(
			"login_preflight",
			"error",
			f"登录前置失败：缺少关键 Cookie {config.primary_cookie}，当前登录态不可用于平台请求",
			f"boss logout && {config.login_action}",
		)
	return AuthHealthCheck(
		"login_preflight",
		"error",
		"登录前置失败：未检测到本地登录态，禁止发起需要认证的平台请求",
		config.login_action,
	)


def _cookie_completeness_check(config: PlatformAuthConfig, cookies: dict[str, Any], *, login_action: str) -> AuthHealthCheck:
	missing_aux = [cookie for cookie in config.aux_cookies if not cookies.get(cookie)]
	if not missing_aux:
		return AuthHealthCheck(
			"cookie_completeness",
			"ok",
			f"辅助 Cookie 完整：{'/'.join(config.aux_cookies)} 均存在",
		)
	return AuthHealthCheck(
		"cookie_completeness",
		"warn",
		f"辅助 Cookie 缺失：{'/'.join(missing_aux)}",
		f"部分接口可能受影响；重新登录通常可补全：boss logout && {login_action}",
	)


def _capability_readiness_checks(
	config: PlatformAuthConfig,
	*,
	has_token: bool,
	has_primary: bool,
	has_secondary: bool,
) -> list[AuthHealthCheck]:
	read_status, read_detail, recovery = _candidate_readiness(config, has_token=has_token, has_primary=has_primary, has_secondary=has_secondary)
	checks = [
		AuthHealthCheck("candidate_search_health", read_status, f"候选人搜索只读流：{read_detail}", recovery),
		AuthHealthCheck("candidate_detail_health", read_status, f"职位详情只读流：{read_detail}", recovery),
	]
	if not config.recruiter_read_supported:
		checks.append(AuthHealthCheck(
			"recruiter_read_health",
			"warn",
			"当前平台未开放 recruiter_read_health 前置检查",
			"查看 boss schema 的 role/platform availability",
		))
	else:
		detail_prefix = (
			"招聘者自动化 browser/CDP 前置条件"
			if config.cookie_domain_label == "zhaopin"
			else "招聘者只读流"
		)
		checks.append(AuthHealthCheck(
			"recruiter_read_health",
			read_status,
			f"{detail_prefix}：{read_detail}",
			recovery,
		))
	return checks


def _candidate_readiness(
	config: PlatformAuthConfig,
	*,
	has_token: bool,
	has_primary: bool,
	has_secondary: bool,
) -> tuple[str, str, str | None]:
	if not has_token:
		return "error", "缺少本地登录态", config.login_action
	if not has_primary:
		return "error", f"缺少关键 Cookie {config.primary_cookie}", f"boss logout && {config.login_action}"
	if config.secondary_token_key == "stoken" and not has_secondary:
		return "warn", "仅具备部分登录态，缺少 __zp_stoken__", "通过 CDP 或 boss login 刷新登录态"
	return "ok", "本地凭据满足前置条件；未执行真实平台请求", None


def _auth_state(*, has_token: bool, has_primary: bool, has_secondary: bool) -> str:
	if not has_token:
		return "missing"
	if has_primary and has_secondary:
		return "complete"
	if has_primary:
		return "partial"
	return "broken"


def _summary_for_checks(checks: list[AuthHealthCheck]) -> str:
	rank = {"ok": 0, "warn": 1, "error": 2}
	worst = max((rank.get(check.status, 0) for check in checks), default=0)
	return "healthy" if worst == 0 else ("degraded" if worst == 1 else "broken")


def _first_recovery_action(checks: list[AuthHealthCheck]) -> str | None:
	for target_status in ("error", "warn"):
		for check in checks:
			if check.status == target_status and check.recovery_action:
				return check.recovery_action
	return None
