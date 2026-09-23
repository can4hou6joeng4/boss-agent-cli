"""探测 patchright 所需 Chromium 是否已安装（doctor 检查与 wizard 预检共用）。"""

from __future__ import annotations

import json
import os
from pathlib import Path


def patchright_chromium_revision() -> str | None:
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


def patchright_browser_cache_dirs() -> list[Path]:
	"""Return Playwright/Patchright browser cache directories for the current OS."""
	dirs = [
		Path.home() / ".cache" / "ms-playwright",
		Path.home() / "Library" / "Caches" / "ms-playwright",
	]
	if local_app_data := os.environ.get("LOCALAPPDATA"):
		dirs.append(Path(local_app_data) / "ms-playwright")
	return dirs


def evaluate_patchright_chromium(required_revision: str | None, installed: list[Path]) -> tuple[str, str]:
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
