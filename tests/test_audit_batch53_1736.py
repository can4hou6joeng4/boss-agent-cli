"""批次五十三审计修复（Issue #1736）：crawl Hook 脚本按路径状态读取。

用户提供的 `--hook-dir` 里脚本被换成 FIFO / 设备时，预检与运行期注入的
`read_bytes()` 会永久阻塞；`SHA256SUMS` 的 `is_file()` 在 3.14 又会吞掉
元数据错误并报成「缺少清单」。
"""

import json
import os
from hashlib import sha256
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

from boss_agent_cli.crawler.hooks import HOOK_SCRIPT_NAMES, verify_hook_profile
from boss_agent_cli.main import cli


def _write_hooks(root: Path) -> Path:
	root.mkdir(parents=True, exist_ok=True)
	lines = []
	for index, (_, filename) in enumerate(HOOK_SCRIPT_NAMES, 1):
		payload = f"globalThis.hook{index} = true;\n".encode()
		(root / filename).write_bytes(payload)
		lines.append(f"{filename} {sha256(payload).hexdigest()}")
	(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
	return root


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", *args])


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_hook_script_fails_precheck_without_blocking(tmp_path):
	root = _write_hooks(tmp_path / "hooks")
	filename = HOOK_SCRIPT_NAMES[0][1]
	(root / filename).unlink()
	os.mkfifo(root / filename)

	with pytest.raises(ValueError) as exc_info:
		verify_hook_profile("screenshot-full", root)

	assert "不是普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO((root / filename).lstat().st_mode), "FIFO 不能被替换或删除"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_manifest_fails_precheck_without_blocking(tmp_path):
	root = _write_hooks(tmp_path / "hooks")
	(root / "SHA256SUMS").unlink()
	os.mkfifo(root / "SHA256SUMS")

	with pytest.raises(ValueError) as exc_info:
		verify_hook_profile("screenshot-full", root)

	assert "不是普通文件" in str(exc_info.value)


def test_missing_manifest_keeps_existing_message(tmp_path):
	root = tmp_path / "hooks"
	root.mkdir()

	with pytest.raises(ValueError, match="缺少 Hook 完整性清单"):
		verify_hook_profile("screenshot-full", root)


def test_symlink_to_regular_script_still_verifies(tmp_path):
	root = _write_hooks(tmp_path / "hooks")
	filename = HOOK_SCRIPT_NAMES[0][1]
	payload = (root / filename).read_bytes()
	real = tmp_path / "real-hook.js"
	real.write_bytes(payload)
	(root / filename).unlink()
	try:
		(root / filename).symlink_to(real)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	results = verify_hook_profile("screenshot-full", root)

	assert len(results) == len(HOOK_SCRIPT_NAMES)
	assert all(item.success for item in results)


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_crawl_run_rejects_fifo_hook_script_before_browser(tmp_path, monkeypatch):
	root = _write_hooks(tmp_path / "hooks")
	filename = HOOK_SCRIPT_NAMES[0][1]
	(root / filename).unlink()
	os.mkfifo(root / filename)
	calls: list[object] = []

	def _explode(settings):
		calls.append(settings)
		raise AssertionError("Hook 预检失败不应构造浏览器")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", _explode)
	result = _invoke(
		tmp_path,
		"run",
		"AI",
		"--city",
		"杭州",
		"--hook-profile",
		"screenshot-full",
		"--hook-dir",
		str(root),
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不是普通文件" in payload["error"]["message"]
	assert calls == []
