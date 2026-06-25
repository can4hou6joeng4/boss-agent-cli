from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import click
import httpx

from boss_agent_cli.auth.browser import probe_cdp, _DEFAULT_CDP_URL
from boss_agent_cli.auth.cookie_extract import extract_cookies
from boss_agent_cli.auth.health import assess_auth_health, auth_config_for_platform
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands.doctor_checks import (
	_add_live_probe_checks,
	_add_quality_baseline_checks,
	_evaluate_patchright_chromium,
	_patchright_browser_cache_dirs,
	_patchright_chromium_revision,
)
from boss_agent_cli.display import handle_output, render_simple_list


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
		*_patchright_browser_cache_dirs(),
	]
	chromium_candidates: list[Path] = []
	for base in patchright_browser_dirs:
		if base.exists():
			chromium_candidates.extend(base.glob("chromium-*"))
			chromium_candidates.extend(base.glob("chromium_headless_shell-*"))
	chromium_status, chromium_detail = _evaluate_patchright_chromium(
		_patchright_chromium_revision(), chromium_candidates
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
	_add_quality_baseline_checks(checks)

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
		cdp_detail = ws_url or f"CDP 不可用（目标: {cdp_url or _DEFAULT_CDP_URL}）"
		if ws_url:
			try:
				resp = httpx.get(f"{cdp_url or _DEFAULT_CDP_URL}/json/version", timeout=3)
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

	# 5) Network probe
	try:
		resp = httpx.get(config.site_url, timeout=5, follow_redirects=True)
		status = "ok" if resp.status_code < 400 else "warn"
		add_check("network", status, f"访问 {config.site_host} 返回 HTTP {resp.status_code}")
	except Exception as e:
		add_check("network", "warn", f"访问 {config.site_host} 失败: {e}", "检查网络、代理或风控拦截")

	# 5.1) 跨平台网络探针：智联端点连通性（与当前平台无关，便于 Agent 调试 --platform zhilian）
	try:
		resp = httpx.get("https://www.zhaopin.com/", timeout=5, follow_redirects=True)
		zhilian_status = "ok" if resp.status_code < 400 else "warn"
		add_check("network_zhilian", zhilian_status, f"访问 zhaopin.com 返回 HTTP {resp.status_code}")
	except Exception as e:
		add_check("network_zhilian", "warn", f"访问 zhaopin.com 失败: {e}", "检查网络或代理")

	# 5.5) Browser channel risk assessment
	cdp_ok = any(item["name"] == "cdp" and item["status"] == "ok" for item in checks)
	bridge_ok = False
	bridge_checks: list[dict[str, Any]] = []
	try:
		from boss_agent_cli.bridge.client import BridgeClient

		bc = BridgeClient()
		bridge_checks = bc.diagnose(workspace="boss", run_probes=True)
		bridge_ok = any(item["name"] == "bridge_extension" and item["status"] == "ok" for item in bridge_checks)
	except Exception as exc:
		bridge_checks = [
			{
				"name": "bridge_daemon",
				"status": "warn",
				"detail": f"Bridge 诊断失败: {exc}",
				"recovery_action": "检查 Bridge daemon、扩展安装状态和本地端口占用",
				"hint": "检查 Bridge daemon、扩展安装状态和本地端口占用",
			}
		]
	checks.extend(bridge_checks)

	if cdp_ok or bridge_ok:
		mode = "CDP + Bridge" if cdp_ok and bridge_ok else ("CDP" if cdp_ok else "Bridge")
		add_check(
			"browser_channel",
			"ok",
			f"{mode} 兼容通道可用；默认低风险模式下不得用于规避平台风控",
		)
	else:
		add_check(
			"browser_channel",
			"warn",
			"CDP 和 Bridge 均不可用；默认低风险模式不依赖它们执行敏感操作",
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
	if not has_token:
		next_actions.append(f"{config.login_action} — 建立登录态")
	else:
		auth_quality = next((item for item in checks if item["name"] == "auth_token_quality"), None)
		if auth_quality and auth_quality["status"] == "warn":
			next_actions.append(f"boss status --live — 验证缺失 {config.secondary_token_label} 的登录态是否仍可用")
			next_actions.append(f"如状态异常，执行 {config.login_action} — 重建或刷新登录态")
		elif auth_quality and auth_quality["status"] == "error":
			next_actions.append(f"boss logout && {config.login_action} — 重建损坏的登录态")
		else:
			next_actions.append("boss status --live — 可选执行一次只读在线验证")
	if not any(item["name"] == "cdp" and item["status"] == "ok" for item in checks):
		next_actions.append("boss --cdp-url http://localhost:9222 doctor — 检查指定 CDP 地址")
	if not any(item["name"] == "cookie_extract" and item["status"] == "ok" for item in checks):
		next_actions.append(f"先在本机浏览器登录 {config.site_host}，再重试 {config.login_action}")
	next_actions.append("python scripts/quality_baseline.py — 提交前运行本地 P0 质量基线")
	next_actions.append("敏感操作或命中风控时，停止自动化访问并回到官方页面由用户手动完成")

	data = {
		"summary": summary,
		"auth_state": auth_health.auth_state,
		"data_dir": str(data_dir),
		"live_probe": live_probe,
		"check_count": len(checks),
		"checks": checks,
	}
	hints = {
		"next_actions": next_actions,
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
