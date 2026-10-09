from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import click
import httpx

from boss_agent_cli.api.browser_urls import DEFAULT_CDP_URL
from boss_agent_cli.api.cdp_risk_lock import UNLOCK_OPERATOR_ACTIONS, lock_status
from boss_agent_cli.auth.browser import probe_cdp
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.health import assess_auth_health, auth_config_for_platform
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._doctor_checks import _add_live_probe_checks, _add_quality_baseline_checks, _doctor_command
from boss_agent_cli.display import handle_output, render_simple_list
from boss_agent_cli.services.browser_runtime import (
	evaluate_patchright_chromium,
	patchright_browser_cache_dirs,
	patchright_chromium_revision,
)


@click.command("doctor")
@click.option("--live-probe", is_flag=True, default=False, help="执行低频只读平台探测（默认仅做本地诊断）")
@click.pass_context
def doctor_cmd(ctx: click.Context, live_probe: bool) -> None:
	"""诊断本地运行环境、依赖和登录条件。"""
	data_dir = ctx.obj["data_dir"]
	platform_name = ctx.obj.get("platform", "zhipin")
	config = auth_config_for_platform(platform_name)
	auth = AuthManager(data_dir, platform=platform_name)
	cdp_url = ctx.obj.get("cdp_url")

	checks: list[dict[str, Any]] = []

	def add_check(name: str, status: str, detail: str, hint: str | None = "") -> None:
		checks.append(
			{
				"name": name,
				"status": status,
				"detail": detail,
				"hint": hint,
			}
		)

	# 1) CLI dependencies
	import sys

	py_version = sys.version_info
	python_ok = py_version >= (3, 10)
	py_detail = f"Python {py_version.major}.{py_version.minor}.{py_version.micro}"
	add_check(
		"python",
		"ok" if python_ok else "error",
		f"{py_detail}" if python_ok else f"{py_detail}（需要 >=3.10）",
		None if python_ok else "升级 Python 到 3.10+",
	)

	patchright_bin = shutil.which("patchright")
	add_check(
		"patchright",
		"ok" if patchright_bin else "warn",
		f"{patchright_bin}" if patchright_bin else "未找到 patchright 可执行文件",
		"运行 uv run patchright install chromium 或确认已正确安装 patchright",
	)

	patchright_browser_dirs = [
		*patchright_browser_cache_dirs(),
	]
	chromium_candidates: list[Path] = []
	for base in patchright_browser_dirs:
		if base.exists():
			chromium_candidates.extend(base.glob("chromium-*"))
			chromium_candidates.extend(base.glob("chromium_headless_shell-*"))
	chromium_status, chromium_detail = evaluate_patchright_chromium(
		patchright_chromium_revision(), chromium_candidates
	)
	add_check(
		"patchright_chromium",
		chromium_status,
		chromium_detail,
		"运行 patchright install chromium 安装浏览器内核",
	)

	chrome_bins = [
		"google-chrome",
		"google-chrome-stable",
		"chromium",
		"chromium-browser",
		"chrome",
		"msedge",
	]
	chrome_path = next((shutil.which(name) for name in chrome_bins if shutil.which(name)), None)
	add_check(
		"browser",
		"ok" if chrome_path else "warn",
		chrome_path or "未在 PATH 中发现 Chrome/Chromium/Edge",
		"如需 CDP 登录，请先启动支持远程调试端口的浏览器；如仅用 patchright，可忽略此项",
	)

	if os.name == "nt":
		uv_tool_bin = Path.home() / ".local" / "bin"
		path_parts = os.environ.get("PATH", "").split(os.pathsep)
		add_check(
			"windows_uv_tool_path",
			"ok" if str(uv_tool_bin) in path_parts else "warn",
			f"{uv_tool_bin} {'已在' if str(uv_tool_bin) in path_parts else '未在'} PATH",
			f'临时修复: $env:PATH = "{uv_tool_bin};$env:PATH"；永久修复: uv tool update-shell',
		)

	# 1.5) Local source quality baseline
	source_root = _add_quality_baseline_checks(checks)

	# 2) Auth storage
	token = auth.check_status()
	auth_health = assess_auth_health(data_dir, platform=platform_name, token=token)
	has_token = auth_health.token_present
	checks.extend(auth_health.checks_as_dicts())

	add_check(
		"auth_salt",
		"ok" if auth_health.salt_path.exists() else "warn",
		f"salt 文件{'存在' if auth_health.salt_path.exists() else '不存在'}: {auth_health.salt_path}",
		"首次保存登录态后会自动生成，可忽略",
	)

	# 3) Cookie extraction support
	cookie_probe = extract_cookies(None, platform=platform_name)
	if cookie_probe:
		cookie_sources = ",".join(sorted(cookie_probe.get("cookies", {}).keys())[:5])
		add_check(
			"cookie_extract",
			"ok",
			f"可从本地浏览器提取 Cookie（示例键: {cookie_sources or 'n/a'}）",
		)
	else:
		add_check(
			"cookie_extract",
			"warn",
			f"未从本地浏览器提取到 {config.cookie_domain_label} Cookie",
			f"请先在本机浏览器登录 {config.site_host}，或使用 {config.login_action}",
		)

	# 4) CDP availability
	try:
		ws_url = probe_cdp(cdp_url)
		cdp_detail = ws_url or f"CDP 不可用（目标: {cdp_url or DEFAULT_CDP_URL}）"
		if ws_url:
			try:
				resp = httpx.get(f"{cdp_url or DEFAULT_CDP_URL}/json/version", timeout=3)
				meta = resp.json()
				browser_name = meta.get("Browser") or "unknown-browser"
				user_agent = meta.get("User-Agent") or "unknown-ua"
				cdp_detail = f"{browser_name} | {user_agent} | {ws_url}"
			except Exception:
				pass
		add_check(
			"cdp",
			"ok" if ws_url else "warn",
			cdp_detail,
			"如需复用用户 Chrome，请以远程调试模式启动浏览器",
		)
	except Exception as e:
		add_check("cdp", "error", f"CDP 探测失败: {e}", "检查浏览器和调试端口配置")

	# 4.5) CDP code 37 风控锁（只读本地文件，不连浏览器、不访问网络）
	risk_lock = lock_status(Path(data_dir))
	if risk_lock["locked"]:
		since = risk_lock.get("locked_at", "未知时间")
		detail = f"CDP Chrome 自 {since} 起处于 code 37 风控锁定，浏览器请求会在本地被拒绝"
		if risk_lock.get("corrupt"):
			detail = "CDP 风控锁文件无法解析，浏览器请求会在本地被拒绝"
		add_check(
			"cdp_risk_lock",
			"warn",
			detail,
			"在该 CDP Chrome 中打开 BOSS 直聘职位列表页，确认能正常加载，等几分钟后重试（stoken 更新后自动解锁）；"
			"确认已恢复仍被拦时运行 boss clean --risk-lock",
		)
	else:
		add_check("cdp_risk_lock", "ok", "未记录 CDP code 37 风控锁")

	# 5) Network probe
	try:
		resp = httpx.get(config.site_url, timeout=5, follow_redirects=True)
		status = "ok" if resp.status_code < 400 else "warn"
		add_check("network", status, f"访问 {config.site_host} 返回 HTTP {resp.status_code}")
	except Exception as e:
		add_check("network", "warn", f"访问 {config.site_host} 失败: {e}", "检查网络、代理或风控拦截")

	# 5.5) Browser channel risk assessment
	cdp_ok = any(item["name"] == "cdp" and item["status"] == "ok" for item in checks)
	if cdp_ok:
		add_check(
			"browser_channel",
			"ok",
			"CDP 兼容通道可用；不得用于规避平台风控",
		)
	else:
		add_check(
			"browser_channel",
			"warn",
			"CDP 不可用；普通 httpx 读取不依赖浏览器通道",
			"如需登录，请使用 boss login；命中风控时停止自动化访问",
		)

	# 5.6) Optional low-frequency live read probes
	if live_probe:
		_add_live_probe_checks(ctx, auth, checks)

	# 6) Data dir writable
	try:
		auth_health.auth_dir.mkdir(parents=True, exist_ok=True)
		probe_file = auth_health.auth_dir / ".doctor-write-test"
		probe_file.write_text("ok", encoding="utf-8")
		probe_file.unlink(missing_ok=True)
		add_check("data_dir", "ok", f"数据目录可写: {data_dir}")
	except Exception as e:
		add_check("data_dir", "error", f"数据目录不可写: {e}", "修改 --data-dir 或目录权限")

	status_rank = {"ok": 0, "warn": 1, "error": 2}
	worst = max((status_rank[item["status"]] for item in checks), default=0)
	summary = "healthy" if worst == 0 else ("degraded" if worst == 1 else "broken")

	next_actions = []
	operator_actions = []
	login_command = _doctor_command(ctx, "login")
	if not has_token:
		next_actions.append(login_command)
		operator_actions.append(f"尚未建立登录态；执行 {login_command}，按提示在官方页面完成登录")
	else:
		auth_quality = next((item for item in checks if item["name"] == "auth_token_quality"), None)
		if auth_quality and auth_quality["status"] == "error":
			operator_actions.append(
				f"本地登录态损坏；确认需要重建后，先执行 {_doctor_command(ctx, 'logout')}，再执行 {login_command}"
			)
		else:
			next_actions.append(_doctor_command(ctx, "status", "--live"))
			operator_actions.append("可选择执行一次 status --live，只读验证当前登录态；不是诊断时自动发起的请求")
			if auth_quality and auth_quality["status"] == "warn":
				operator_actions.append(
					f"缺少 {config.secondary_token_label}；若状态异常，先在官方页面确认，再决定是否执行 {login_command} 重建登录态"
				)
	if not cdp_ok:
		next_actions.append(_doctor_command(ctx, "doctor", cdp_url=cdp_url or DEFAULT_CDP_URL))
		operator_actions.append("如需 CDP，请先确认目标浏览器及调试端口已就绪，再重新诊断；不要反复重试")
	if risk_lock["locked"]:
		operator_actions.extend(UNLOCK_OPERATOR_ACTIONS)
	if not any(item["name"] == "cookie_extract" and item["status"] == "ok" for item in checks):
		operator_actions.append(f"需要提取本地登录态时，先在本机浏览器的 {config.site_host} 官方页面完成登录")
	if source_root is not None:
		operator_actions.append(f"源码维护者：在 {source_root} 运行 uv run python scripts/quality_baseline.py 检查本地门禁")
	operator_actions.append("涉及敏感操作或命中平台风控时，停止自动化访问并回到官方页面由用户手动完成")

	data = {
		"summary": summary,
		"auth_state": auth_health.auth_state,
		"data_dir": str(data_dir),
		"live_probe": live_probe,
		"cdp_risk_lock": risk_lock,
		"check_count": len(checks),
		"checks": checks,
	}
	hints = {
		"next_actions": next_actions,
		"operator_actions": operator_actions,
	}
	handle_output(
		ctx,
		"doctor",
		data,
		render=lambda d: render_simple_list(
			d["checks"],
			f"doctor: {d['summary']}",
			[
				("name", "name", "cyan"),
				("status", "status", "green"),
				("detail", "detail", "white"),
			],
		),
		hints=hints,
	)
