"""浏览器内核安装指引在目标 shell 中保留解释器路径（Issue #1072）。"""

from __future__ import annotations

import shlex
from types import SimpleNamespace

import pytest

from boss_agent_cli.services import browser_runtime
from boss_agent_cli.services.browser_runtime import interpreter_invocation
from boss_agent_cli.wizard.renderer import recovery_message


@pytest.mark.parametrize("path", [
	"/opt/tool/bin/python",
	"/opt/tool env/bin/python",
	"/opt/工具/bin/python",
	"/opt/it's literal/$env;`echo`/python",
])
def test_posix_kernel_command_preserves_interpreter_path(path: str) -> None:
	command = f"{interpreter_invocation(path, windows=False)} -m patchright install chromium"

	assert shlex.split(command) == [path, "-m", "patchright", "install", "chromium"]


@pytest.mark.parametrize("path", [
	r"C:\Tools\boss-agent-cli\Scripts\python.exe",
	"C:/Tools/boss-agent-cli/Scripts/python.exe",
	r"\\server\tools\python.exe",
])
def test_windows_bare_path_is_not_wrapped_in_posix_quotes(path: str) -> None:
	"""反斜杠不应触发 POSIX 引用，否则 PowerShell 会把路径当成字符串表达式。"""
	assert interpreter_invocation(path, windows=True) == path


@pytest.mark.parametrize(("path", "expected"), [
	(r"C:\Program Files\Python\python.exe", r"& 'C:\Program Files\Python\python.exe'"),
	(r"C:\工具\python.exe", r"& 'C:\工具\python.exe'"),
	(r"C:\O'Brien\python.exe", r"& 'C:\O''Brien\python.exe'"),
	(r"C:\$env;`literal`\python.exe", r"& 'C:\$env;`literal`\python.exe'"),
])
def test_windows_quoted_path_uses_powershell_call_operator(path: str, expected: str) -> None:
	"""调用运算符执行单引号内的完整路径；路径中的单引号按 PowerShell 转义。"""
	assert interpreter_invocation(path, windows=True) == expected


@pytest.mark.parametrize(("path", "prefix"), [
	(r"C:\Tools\python.exe", r"C:\Tools\python.exe"),
	(r"C:\Program Files\python.exe", r"& 'C:\Program Files\python.exe'"),
])
def test_windows_kernel_hints_and_wizard_keep_runnable_command(
	monkeypatch: pytest.MonkeyPatch, path: str, prefix: str,
) -> None:
	# 只替换本模块的引用，避免把 pytest / pathlib 也切到模拟的 Windows 环境。
	monkeypatch.setattr(browser_runtime, "sys", SimpleNamespace(executable=path))
	monkeypatch.setattr(browser_runtime, "os", SimpleNamespace(name="nt"))
	install = f"{prefix} -m patchright install chromium"
	headless = f"{prefix} -m patchright install chromium-headless-shell"

	assert browser_runtime.patchright_install_command() == install
	assert browser_runtime.patchright_headless_shell_command() == headless
	status, detail = browser_runtime.evaluate_patchright_chromium("123", [])
	assert status == "error"
	assert install in detail
	assert install in (recovery_message("BROWSER_KERNEL_MISSING") or "")
	assert headless in (recovery_message("BROWSER_KERNEL_MISSING", headless) or "")
