from __future__ import annotations

import json
import os
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


def _patchright_chromium_revision() -> str | None:
	"""读取 patchright 自带 browsers.json 声明的 chromium 修订版。"""
	try:
		import patchright

		browsers_json = Path(patchright.__file__).resolve().parent / "driver" / "package" / "browsers.json"
		data = json.loads(browsers_json.read_text(encoding="utf-8"))
	except Exception:
		return None
	for browser in data.get("browsers", []):
		if browser.get("name") == "chromium":
			revision = browser.get("revision")
			return str(revision) if revision else None
	return None


def _patchright_browser_cache_dirs() -> list[Path]:
	"""Return Playwright/Patchright browser cache directories for the current OS."""
	dirs = [
		Path.home() / ".cache" / "ms-playwright",
		Path.home() / "Library" / "Caches" / "ms-playwright",
	]
	if local_app_data := os.environ.get("LOCALAPPDATA"):
		dirs.append(Path(local_app_data) / "ms-playwright")
	return dirs


def _evaluate_patchright_chromium(required_revision: str | None, installed: list[Path]) -> tuple[str, str]:
	"""Return (status, detail) for the patchright_chromium doctor check."""
	if required_revision:
		expected = f"chromium-{required_revision}"
		expected_headless = f"chromium_headless_shell-{required_revision}"
		has_chromium = any(p.name == expected for p in installed)
		has_headless = any(p.name == expected_headless for p in installed)
		if has_chromium and has_headless:
			return "ok", f"已安装 patchright 所需修订版 {expected} 与 {expected_headless}"
		if has_chromium:
			return (
				"warn",
				f"已安装 {expected}，但缺少 {expected_headless}；如全局 tool 环境启动失败，运行 "
				"patchright install chromium-headless-shell",
			)
		found = "、".join(sorted(p.name for p in installed)) or "无"
		return (
			"warn",
			f"patchright 需要 {expected}，本机缓存仅有：{found}；boss login 启动内置浏览器会失败，"
			"请运行 patchright install chromium",
		)
	if installed:
		return "ok", f"检测到 {len(installed)} 个 Chromium 安装（无法确认 patchright 所需修订版）"
	return "warn", "未检测到 patchright/Playwright Chromium 缓存"


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
