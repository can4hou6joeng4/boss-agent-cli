"""Register verbatim AntiDebug_Breaker snapshots for the explicit crawler only."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any

UPSTREAM_REPOSITORY = "https://github.com/Komikawayi/AntiDebug_Breaker"
UPSTREAM_COMMIT = "b74db937a0825c58bfee181ae85e09fce8474467"
UPSTREAM_SNAPSHOT_URL = f"{UPSTREAM_REPOSITORY}/tree/{UPSTREAM_COMMIT}"

# The JavaScript files in hook_scripts/ are byte-for-byte copies from the
# upstream commit above. Keep this order aligned with the enabled screenshot.
HOOK_SNAPSHOT_FILES: tuple[tuple[str, str], ...] = (
	("Bypass_Debugger", "Bypass_Debugger.js"),
	("Hook_CryptoJS", "Hook_CryptoJS.js"),
	("hook_table", "hook_table.js"),
	("hook_clear", "hook_clear.js"),
	("hook_close", "hook_close.js"),
	("hook_history", "hook_history.js"),
	("Fixed_window_size", "Fixed_window_size.js"),
)

HOOK_SNAPSHOT_SHA256 = {
	"Bypass_Debugger.js": "564d86ee9a8a93453481468f01d247d6228b0f5fe75e0e4f329d255acc55ec0f",
	"Hook_CryptoJS.js": "b1b17043f99eae7400ed8f88aedaec0dbddfa08c10772c8a784bd0c655c5ee61",
	"hook_table.js": "14d7956abd4e00cd51fb55635c7a288944778ad0e37a66ed3cf71c499fe06c08",
	"hook_clear.js": "0344b5e147e03b002d22281dba580ef6df8ee6dcf14b4188c4981eda607b3368",
	"hook_close.js": "8f7915e87cb9ba409c4f3326de3bcda19aa1228413570e2b88f7ec1850c0c4f6",
	"hook_history.js": "cb0811ff789ad8c913f6d94b05d1ccaf93fae77b736ab3bd7d96d66e6d58050c",
	"Fixed_window_size.js": "3f0eb7fa369f0b0e69854e857daa68e61d752ffc32c0253ae379c6f533cd044f",
}


def _read_snapshot(filename: str) -> str:
	return files("boss_agent_cli.crawler").joinpath("hook_scripts").joinpath(filename).read_text(encoding="utf-8")


HOOK_PROFILES: dict[str, tuple[tuple[str, str], ...]] = {
	"none": (),
	"screenshot-full": tuple((name, _read_snapshot(filename)) for name, filename in HOOK_SNAPSHOT_FILES),
}


@dataclass(frozen=True)
class HookInjection:
	"""A single early-document script registration result."""

	name: str
	success: bool
	reason: str = ""


class HookRegistrationError(RuntimeError):
	"""Raised when an early-document profile could not be registered in full."""

	def __init__(self, injections: list[HookInjection]) -> None:
		self.injections = tuple(injections)
		failed = [item for item in injections if not item.success]
		super().__init__("Hook 注入失败: " + "; ".join(f"{item.name}: {item.reason}" for item in failed))


def inject_hook_profile(page: Any, profile: str) -> list[HookInjection]:
	"""Register every unmodified snapshot in *profile* before navigation."""
	if profile not in HOOK_PROFILES:
		raise ValueError(f"unknown crawl hook profile: {profile}")

	results: list[HookInjection] = []
	for name, source in HOOK_PROFILES[profile]:
		try:
			page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=source)
			results.append(HookInjection(name=name, success=True))
		except Exception as exc:
			results.append(HookInjection(name=name, success=False, reason=str(exc)))
	return results
