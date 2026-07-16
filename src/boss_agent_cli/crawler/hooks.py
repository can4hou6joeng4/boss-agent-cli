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
	checksums = _read_checksums(hook_dir / HOOK_MANIFEST)
	results: list[HookInjection] = []
	for name, filename in HOOK_SCRIPT_NAMES:
		try:
			content = (hook_dir / filename).read_bytes()
			digest = sha256(content).hexdigest()
			expected = checksums.get(filename)
			if expected != digest:
				raise ValueError("SHA-256 与 SHA256SUMS 不匹配")
			page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=content.decode("utf-8"))
			results.append(HookInjection(name=name, success=True, sha256=expected))
		except Exception as exc:
			results.append(HookInjection(name=name, success=False, reason=str(exc)))
	if any(not item.success for item in results):
		raise HookRegistrationError(results)
	return results


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
