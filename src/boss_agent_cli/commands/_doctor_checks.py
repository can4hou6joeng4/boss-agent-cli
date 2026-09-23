from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance


def _find_project_root() -> Path:
	"""Return the repository root when running from a source checkout."""
	for parent in Path(__file__).resolve().parents:
		if (parent / "pyproject.toml").exists():
			return parent
	return Path.cwd()


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


def _add_quality_baseline_checks(checks: list[dict[str, Any]]) -> None:
	"""Report whether the local P0 quality baseline can be run offline."""
	root = _find_project_root()
	baseline = root / "scripts" / "quality_baseline.py"
	pyproject = root / "pyproject.toml"
	if baseline.exists() and pyproject.exists():
		checks.append(
			{
				"name": "quality_baseline",
				"status": "ok",
				"detail": "可运行 scripts/quality_baseline.py 执行 CI 同款 P0 门禁：ruff、全量离线 pytest 和 mypy",
				"hint": "python scripts/quality_baseline.py",
			}
		)
	else:
		checks.append(
			{
				"name": "quality_baseline",
				"status": "warn",
				"detail": "未检测到源码仓库质量基线入口（安装包运行时可忽略）",
				"hint": "在项目根目录运行，或使用发布包自带的外部 CI",
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

	if (ctx.obj or {}).get("platform") == "zhilian":
		checks.append(
			{
				"name": "recruiter_live_read",
				"status": "warn",
				"detail": "zhilian 招聘者侧通过 agent browser/CDP adapter 探测；doctor 不执行会话扫描或写动作",
				"recovery_action": "运行 boss --platform zhilian --role recruiter agent run --dry-run --limit 1",
			}
		)
		return

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
				"recovery_action": "确认当前账号具备招聘者身份；zhilian 招聘者侧暂不支持",
			}
		)
