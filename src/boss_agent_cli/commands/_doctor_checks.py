from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import RISK_ERROR_CONTRACTS, platform_error_contract, risk_error_message
from boss_agent_cli.services.recruiter_identity import interpret_recruiter_identity


def _find_project_root() -> Path | None:
	"""仅识别当前模块所属的本项目源码，不借用安装包用户的工作目录。"""
	source = Path(__file__).resolve()
	for parent in source.parents:
		if (
			parent / "src" / "boss_agent_cli" / "commands" / source.name == source
			and (parent / "pyproject.toml").is_file()
		):
			return parent
	return None


def _doctor_command(ctx: click.Context, *args: str, cdp_url: str | None = None) -> str:
	"""构造保留诊断上下文的命令，参数独立引用，不混入真人说明。"""
	argv = [
		"boss", "--data-dir", str(ctx.obj["data_dir"].resolve()),
		"--platform", ctx.obj.get("platform", "zhipin"),
	]
	if role := ctx.obj.get("role"):
		argv.extend(["--role", role])
	if source := ctx.obj.get("browser_source"):
		argv.extend(["--browser-source", source])
	if address := cdp_url or ctx.obj.get("cdp_url"):
		argv.extend(["--cdp-url", address])
	argv.extend(args)
	if os.name == "nt":
		# Windows 指引面向 PowerShell；单引号也保护路径里的 &, $, ; 等字符。
		return " ".join(
			arg if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", arg) else "'" + arg.replace("'", "''") + "'"
			for arg in argv
		)
	return shlex.join(argv)


def _resolve_quality_tool(tool: str) -> tuple[str, str, str]:
	"""Return doctor status, detail, and hint for a quality baseline tool."""
	if path := shutil.which(tool):
		return "ok", path, ""
	if shutil.which("uv"):
		command = f"uv run {tool}"
		return "ok", f"PATH 未发现 {tool}，但可通过 {command} 使用项目环境", command
	return (
		"warn",
		f"未在 PATH 中发现 {tool}，且 uv 不可用",
		"运行 uv sync --all-extras，或先安装 uv",
	)


def _add_quality_baseline_checks(checks: list[dict[str, Any]]) -> Path | None:
	"""只为源码运行加入开发者门禁检查，返回可执行门禁的源码根目录。"""
	root = _find_project_root()
	if root is None:
		return None
	baseline = root / "scripts" / "quality_baseline.py"
	if baseline.is_file():
		checks.append(
			{
				"name": "quality_baseline",
				"status": "ok",
				"detail": "可运行 scripts/quality_baseline.py 执行本地 P0 门禁：ruff、全量离线 pytest 和 mypy",
				"hint": f"在源码根目录 {root} 运行 uv run python scripts/quality_baseline.py",
			}
		)
	else:
		checks.append(
			{
				"name": "quality_baseline",
				"status": "warn",
				"detail": "当前源码仓库缺少 scripts/quality_baseline.py",
				"hint": "请先恢复源码仓库的质量门禁脚本",
			}
		)

	for tool in ("ruff", "pytest", "mypy"):
		status, detail, hint = _resolve_quality_tool(tool)
		checks.append(
			{
				"name": f"quality_tool_{tool}",
				"status": status,
				"detail": detail,
				"hint": hint,
			}
		)
	return root if baseline.is_file() else None


def _live_probe_risk_check(name: str, code: str) -> dict[str, Any]:
	"""在线探测命中风控时的检查项：文案取自登记表，不透传异常或平台响应文本。"""
	_, recovery_action, _ = platform_error_contract(code)
	return {
		"name": name,
		"status": "warn",
		"detail": f"只读探测命中平台风控（{code}）：{risk_error_message(code)}",
		"recovery_action": recovery_action or "停止自动化访问",
	}


def _live_probe_skipped_check(name: str, code: str) -> dict[str, Any]:
	"""风控命中后不再向另一个身份发请求，用一条检查项说明跳过原因。"""
	return {
		"name": name,
		"status": "warn",
		"detail": f"已命中平台风控（{code}），未继续发起在线只读探测",
	}


def _add_live_probe_checks(ctx: click.Context, auth: AuthManager, checks: list[dict[str, Any]]) -> str | None:
	"""Run explicit, low-frequency read probes only when requested.

	返回命中的风控错误码。风控是硬边界：任一次探测命中后立即返回，不再向另一个
	身份发请求，由调用方据此抑制后续在线探测建议。
	"""
	identity: dict[str, Any] | None = None
	try:
		with get_platform_instance(ctx, auth) as platform:
			info = platform.user_info()
			if platform.is_success(info):
				checks.append(
					{
						"name": "candidate_live_user_info",
						"status": "ok",
						"detail": "求职者只读 user_info 探测通过",
					}
				)
				identity = interpret_recruiter_identity(_user_info_payload(platform, info))
				checks.append(_recruiter_identity_check(identity))
			else:
				code, message = platform.parse_error(info)
				if code in RISK_ERROR_CONTRACTS:
					checks.append(_live_probe_risk_check("candidate_live_user_info", code))
					checks.append(_live_probe_skipped_check("recruiter_live_read", code))
					return code
				checks.append(
					{
						"name": "candidate_live_user_info",
						"status": "warn",
						"detail": f"求职者只读 user_info 探测失败: {code} {message}".strip(),
						"recovery_action": "按错误码执行恢复；命中风控时停止自动化访问",
					}
				)
	except PlatformRiskError as exc:
		# 漏掉这个分支时，风控异常会被通用兜底吞成“探测异常”并带出平台文案，
		# 而且探测会继续请求招聘者接口——等于命中风控后再发一次请求。
		checks.append(_live_probe_risk_check("candidate_live_user_info", exc.code))
		checks.append(_live_probe_skipped_check("recruiter_live_read", exc.code))
		return exc.code
	except Exception as exc:
		checks.append(
			{
				"name": "candidate_live_user_info",
				"status": "warn",
				"detail": f"求职者只读 user_info 探测异常: {exc}",
				"recovery_action": "先运行 boss status 检查本地登录态；命中风控时停止自动化访问",
			}
		)

	if identity is not None and identity.get("side") == "candidate":
		checks.append(
			{
				"name": "recruiter_live_read",
				"status": "warn",
				"detail": "当前登录态在求职者侧，未请求招聘者接口",
				"recovery_action": "在官方页面切换到「我要招聘」后执行 boss hr whoami；命中风控时停止自动化访问",
			}
		)
		return None

	try:
		with get_recruiter_platform_instance(ctx, auth) as recruiter:
			result = recruiter.list_jobs()
			if recruiter.is_success(result):
				checks.append(
					{
						"name": "recruiter_live_read",
						"status": "ok",
						"detail": "招聘者职位列表只读探测通过",
					}
				)
			else:
				code, message = recruiter.parse_error(result)
				if code in RISK_ERROR_CONTRACTS:
					checks.append(_live_probe_risk_check("recruiter_live_read", code))
					return code
				checks.append(
					{
						"name": "recruiter_live_read",
						"status": "warn",
						"detail": f"招聘者只读探测失败: {code} {message}".strip(),
						"recovery_action": "确认当前账号具备招聘者身份；命中风控时停止自动化访问",
					}
				)
	except PlatformRiskError as exc:
		checks.append(_live_probe_risk_check("recruiter_live_read", exc.code))
		return exc.code
	except Exception as exc:
		checks.append(
			{
				"name": "recruiter_live_read",
				"status": "warn",
				"detail": f"招聘者只读探测异常: {exc}",
				"recovery_action": "确认当前账号具备招聘者身份；命中风控时停止自动化访问",
			}
		)
	return None


def _user_info_payload(platform: Any, info: Any) -> dict[str, Any]:
	unwrap = getattr(platform, "unwrap_data", None)
	if callable(unwrap):
		data = unwrap(info)
		if isinstance(data, dict):
			return data
	if isinstance(info, dict):
		payload = info.get("zpData")
		if isinstance(payload, dict):
			return payload
	return {}


def _recruiter_identity_check(identity: dict[str, Any]) -> dict[str, Any]:
	if identity.get("ready") is True:
		return {
			"name": "recruiter_identity",
			"status": "ok",
			"detail": "当前登录态在招聘者侧（identity=1）",
		}
	if identity.get("side") == "candidate":
		detail = "当前登录态在求职者侧（identity=0）"
		if identity.get("double_identity") is True:
			detail += "；账号具备双身份"
		return {
			"name": "recruiter_identity",
			"status": "warn",
			"detail": detail,
			"recovery_action": "在官方页面切换到「我要招聘」后执行 boss hr whoami",
		}
	return {
		"name": "recruiter_identity",
		"status": "warn",
		"detail": "getUserInfo 的 identity 不是 0 或 1，继续做招聘者只读探测",
		"recovery_action": "执行 boss hr whoami 核对身份",
	}
