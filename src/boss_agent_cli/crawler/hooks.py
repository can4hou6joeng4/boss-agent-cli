"""Load user-supplied research hooks without redistributing third-party code."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from boss_agent_cli.services.json_files import read_bytes_if_regular

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

	def __init__(self, injections: list[HookInjection], *, hook_dir: Path | None = None) -> None:
		self.injections = tuple(injections)
		# 运行期注入失败要能让命令层提示具体目录：`crawl resume` 没有
		# `--hook-dir` 参数，只说「注入失败」无法定位要修的路径（Issue #1408）。
		self.hook_dir = hook_dir
		location = f"（{hook_dir}）" if hook_dir is not None else ""
		failed = [item for item in injections if not item.success]
		super().__init__(f"Hook 注入失败{location}: " + "; ".join(f"{item.name}: {item.reason}" for item in failed))


def inject_hook_profile(page: Any, profile: str, hook_dir: Path | None) -> list[HookInjection]:
	"""Verify local user scripts and register them before the first navigation."""
	if profile == "none":
		return []
	if profile != "screenshot-full":
		raise ValueError(f"unknown crawl hook profile: {profile}")
	if hook_dir is None:
		raise ValueError("screenshot-full 需要 --hook-dir 指向用户提供的原始脚本目录")
	try:
		verified = _verified_scripts(hook_dir)
	except HookRegistrationError as exc:
		raise HookRegistrationError(list(exc.injections), hook_dir=hook_dir) from exc
	results: list[HookInjection] = []
	for name, digest, source in verified:
		try:
			page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=source)
			results.append(HookInjection(name=name, success=True, sha256=digest))
		except Exception as exc:
			results.append(HookInjection(name=name, success=False, reason=str(exc)))
	if any(not item.success for item in results):
		raise HookRegistrationError(results, hook_dir=hook_dir)
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
			# Hook 目录是用户显式提供的输入：FIFO / 设备直接 read_bytes() 会永久
			# 阻塞预检与运行期注入（Issue #1736）。缺失与特殊文件都进入逐项失败
			# 列表，由调用方给出具体文件与原因。
			content = read_bytes_if_regular(hook_dir / filename)
			if content is None:
				raise ValueError(f"脚本不存在: {filename}")
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


def _manifest_error(detail: str) -> HookRegistrationError:
	# 清单问题与脚本问题走同一个异常契约：verify 会把它收敛成参数错误，
	# inject 则带着 hook_dir 报目录错误，不再出现裸 UnicodeDecodeError
	# 被 service 兜成 stopped 的情况（Issue #1937）。
	return HookRegistrationError([HookInjection(name=HOOK_MANIFEST, success=False, reason=detail)])


def _read_checksums(path: Path) -> dict[str, str]:
	# 只有文件确实不存在才是「缺少完整性清单」；权限错误与非普通文件要把
	# 真正原因带出来（3.14 的 is_file() 会把 OSError 吞成 False，Issue #1736）。
	try:
		raw = read_bytes_if_regular(path)
	except OSError as exc:
		# 清单被换成目录 / FIFO 或变得不可读时，预检与运行期注入必须走同一个
		# 异常契约：verify 收敛成参数错误，inject 带 hook_dir 报错。裸 OSError
		# 从 inject 逃逸会被 service 的通用 except 兜成 stopped 成功信封
		# （Issue #2127）。
		raise _manifest_error(f"无法读取 Hook 完整性清单（{path.name}）：{exc}") from exc
	if raw is None:
		raise _manifest_error(f"缺少 Hook 完整性清单: {path.name}")
	try:
		text = raw.decode("ascii")
	except UnicodeDecodeError as exc:
		raise _manifest_error(f"SHA256SUMS 必须是 ASCII 清单（{path.name}）") from exc
	checksums: dict[str, str] = {}
	for line in text.splitlines():
		parts = line.split()
		if len(parts) != 2:
			continue
		first, second = parts
		if len(first) == 64:
			checksums[second.lstrip("*")] = first.lower()
		elif len(second) == 64:
			checksums[first] = second.lower()
	if {filename for _, filename in HOOK_SCRIPT_NAMES} - set(checksums):
		raise _manifest_error("SHA256SUMS 必须包含全部 7 个 Hook 文件")
	return checksums
