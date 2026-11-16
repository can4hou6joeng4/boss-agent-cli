"""Load user-supplied research hooks without redistributing third-party code."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

HOOK_SCRIPT_NAMES = (
	("Bypass_Debugger", "Bypass_Debugger.js"),
	("Hook_CryptoJS", "Hook_CryptoJS.js"),
	("hook_table", "hook_table.js"),
	("hook_clear", "hook_clear.js"),
	("hook_close", "hook_close.js"),
	("hook_history", "hook_history.js"),
	("Fixed_window_size", "Fixed_window_size.js"),
)
HOOK_MANIFEST = "SHA256SUMS"


@dataclass(frozen=True)
class HookInjection:
	"""One early-document registration result; never retain source text."""

	name: str
	success: bool
	sha256: str = ""
	reason: str = ""


class HookRegistrationError(RuntimeError):
	"""Raised when a requested user-owned script cannot be verified or injected."""

	def __init__(self, injections: list[HookInjection]) -> None:
		self.injections = tuple(injections)
		failed = [item for item in injections if not item.success]
		super().__init__("Hook 注入失败: " + "; ".join(f"{item.name}: {item.reason}" for item in failed))


def inject_hook_profile(page: Any, profile: str, hook_dir: Path | None) -> list[HookInjection]:
	"""Verify local user scripts and register them before the first navigation."""
	if profile == "none":
		return []
	if profile != "screenshot-full":
		raise ValueError(f"unknown crawl hook profile: {profile}")
	if hook_dir is None:
		raise ValueError("screenshot-full 需要 --hook-dir 指向用户提供的原始脚本目录")
	results: list[HookInjection] = []
	for name, digest, source in _verified_scripts(hook_dir):
		try:
			page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=source)
			results.append(HookInjection(name=name, success=True, sha256=digest))
		except Exception as exc:
			results.append(HookInjection(name=name, success=False, reason=str(exc)))
	if any(not item.success for item in results):
		raise HookRegistrationError(results)
	return results


def verify_hook_profile(profile: str, hook_dir: Path | None) -> list[HookInjection]:
	"""Validate the profile and the local hook directory without a browser page.

	创建任务 / 打开浏览器之前调用（Issue #1348）：目录写错、空目录、清单缺失
	或脚本被改时按参数错误拒绝。否则任务先落库、Chrome 先打开，直到注入阶段
	才抛错并被记成 ``status=stopped`` 的成功信封，而 ``crawl resume`` 没有
	``--hook-dir`` 参数可以修复。
	"""
	if profile == "none":
		return []
	if profile != "screenshot-full":
		raise ValueError(f"unknown crawl hook profile: {profile}")
	if hook_dir is None:
		raise ValueError("screenshot-full 需要 --hook-dir 指向用户提供的原始脚本目录")
	try:
		verified = _verified_scripts(hook_dir)
	except (HookRegistrationError, ValueError) as exc:
		raise ValueError(f"Hook 目录校验失败（{hook_dir}）：{exc}") from exc
	except OSError as exc:
		raise ValueError(f"无法读取 Hook 目录 {hook_dir}：{exc}") from exc
	return [HookInjection(name=name, success=True, sha256=digest) for name, digest, _ in verified]


def _verified_scripts(hook_dir: Path) -> list[tuple[str, str, str]]:
	"""Read, digest-check and decode every required script.

	预检（不依赖页面）与运行期注入共用同一套清单 / 摘要 / 解码校验，同一目录
	在两端得到同样的结论（Issue #1348）。返回值只在调用栈内使用，
	``HookInjection`` 不保留脚本文本。
	"""
	checksums = _read_checksums(hook_dir / HOOK_MANIFEST)
	verified: list[tuple[str, str, str]] = []
	results: list[HookInjection] = []
	for name, filename in HOOK_SCRIPT_NAMES:
		try:
			content = (hook_dir / filename).read_bytes()
			digest = sha256(content).hexdigest()
			expected = checksums.get(filename)
			if expected is None or expected != digest:
				raise ValueError("SHA-256 与 SHA256SUMS 不匹配")
			source = content.decode("utf-8")
			verified.append((name, expected, source))
			results.append(HookInjection(name=name, success=True, sha256=expected))
		except Exception as exc:
			results.append(HookInjection(name=name, success=False, reason=str(exc)))
	if any(not item.success for item in results):
		raise HookRegistrationError(results)
	return verified


def _read_checksums(path: Path) -> dict[str, str]:
	if not path.is_file():
		raise ValueError(f"缺少 Hook 完整性清单: {path.name}")
	checksums: dict[str, str] = {}
	for line in path.read_text(encoding="ascii").splitlines():
		parts = line.split()
		if len(parts) != 2:
			continue
		first, second = parts
		if len(first) == 64:
			checksums[second.lstrip("*")] = first.lower()
		elif len(second) == 64:
			checksums[first] = second.lower()
	if {filename for _, filename in HOOK_SCRIPT_NAMES} - set(checksums):
		raise ValueError("SHA256SUMS 必须包含全部 7 个 Hook 文件")
	return checksums
