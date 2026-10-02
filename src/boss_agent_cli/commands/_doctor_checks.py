from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance


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


def _add_live_probe_checks(ctx: click.Context, auth: AuthManager, checks: list[dict[str, Any]]) -> None:
	"""Run explicit, low-frequency read probes only when requested."""
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
			else:
				code, message = platform.parse_error(info)
				checks.append(
					{
						"name": "candidate_live_user_info",
						"status": "warn",
						"detail": f"求职者只读 user_info 探测失败: {code} {message}".strip(),
						"recovery_action": "按错误码执行恢复；命中风控时停止自动化访问",
					}
				)
	except Exception as exc:
		checks.append(
			{
				"name": "candidate_live_user_info",
				"status": "warn",
				"detail": f"求职者只读 user_info 探测异常: {exc}",
				"recovery_action": "先运行 boss status 检查本地登录态；命中风控时停止自动化访问",
			}
		)

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
				checks.append(
					{
						"name": "recruiter_live_read",
						"status": "warn",
						"detail": f"招聘者只读探测失败: {code} {message}".strip(),
						"recovery_action": "确认当前账号具备招聘者身份；命中风控时停止自动化访问",
					}
				)
	except Exception as exc:
		checks.append(
			{
				"name": "recruiter_live_read",
				"status": "warn",
				"detail": f"招聘者只读探测异常: {exc}",
				"recovery_action": "确认当前账号具备招聘者身份；命中风控时停止自动化访问",
			}
		)
