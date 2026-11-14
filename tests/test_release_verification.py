"""发布验证工具的离线回归，不安装依赖或访问平台。"""

from __future__ import annotations

import argparse
import importlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.3"


@pytest.fixture
def verifier(monkeypatch):
	monkeypatch.syspath_prepend(str(ROOT / "scripts"))
	return importlib.import_module("verify_release")


@pytest.fixture
def probe_module(monkeypatch):
	monkeypatch.syspath_prepend(str(ROOT / "scripts"))
	return importlib.import_module("_release_probe")


def metadata(version=VERSION, extra=""):
	return f"Metadata-Version: 2.3\nName: boss-agent-cli\nVersion: {version}\n{extra}\n".encode()


def make_artifacts(directory, *, version=VERSION, package_version=None, member=None):
	directory.mkdir(parents=True, exist_ok=True)
	wheel = directory / f"boss_agent_cli-{version}-py3-none-any.whl"
	with zipfile.ZipFile(wheel, "w") as archive:
		archive.writestr(f"boss_agent_cli-{version}.dist-info/METADATA", metadata(package_version or version))
		if member:
			archive.writestr(member, "placeholder")
	with tarfile.open(directory / f"boss_agent_cli-{version}.tar.gz", "w:gz") as archive:
		raw = metadata(package_version or version)
		info = tarfile.TarInfo(f"boss_agent_cli-{version}/PKG-INFO")
		info.size = len(raw)
		archive.addfile(info, io.BytesIO(raw))
	return wheel


def source_tree(root):
	(root / "src/boss_agent_cli").mkdir(parents=True)
	(root / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n')
	(root / "src/boss_agent_cli/__init__.py").write_text(f'__version__ = "{VERSION}"\n')


def probe_report(**overrides):
	return {
		"ok": True,
		"version": VERSION,
		"cli": "passed",
		"mcp": "passed",
		"tool_count": 2,
		"preview_sent": False,
		"unapproved_code": "CONFIRMATION_REQUIRED",
		**overrides,
	}


def options(artifacts=None, **kwargs):
	values = dict(
		artifacts=artifacts,
		pypi_version=None,
		fresh_install=False,
		python="3.11",
		timeout=10,
		require_clean=False,
		tag=None,
		json=True,
	)
	return argparse.Namespace(**(values | kwargs))


def test_source_version_must_match(verifier, tmp_path):
	source_tree(tmp_path)
	assert verifier.source_version(tmp_path) == VERSION
	(tmp_path / "src/boss_agent_cli/__init__.py").write_text('__version__ = "0.0.1"\n')
	with pytest.raises(verifier.VerificationError, match="不一致"):
		verifier.source_version(tmp_path)


def test_artifact_metadata_and_hashes(verifier, tmp_path):
	make_artifacts(tmp_path)
	result = verifier.check_artifacts(tmp_path, VERSION)
	assert len(result["files"]) == 2
	assert all(len(item["sha256"]) == 64 for item in result["files"])


@pytest.mark.parametrize("kind", ["missing", "old", "mixed", "metadata", "corrupt"])
def test_bad_artifacts_are_rejected(verifier, tmp_path, kind):
	if kind != "missing":
		wheel = make_artifacts(
			tmp_path,
			version="0.0.1" if kind == "old" else VERSION,
			package_version="0.0.1" if kind == "metadata" else None,
		)
		if kind == "mixed":
			make_artifacts(tmp_path, version="0.0.1")
		if kind == "corrupt":
			wheel.write_text("not a zip")
	with pytest.raises((verifier.VerificationError, zipfile.BadZipFile)):
		verifier.check_artifacts(tmp_path, VERSION)


@pytest.mark.parametrize(
	"name",
	[
		"../escape",
		"/absolute",
		"C:\\outside",
		"C:/outside",
		"boss_agent_cli/bridge/client.py",
		"extension/manifest.json",
		"CLAUDE.md",
		"nested/AGENTS.md",
		".venv/lib/code.py",
		".claude/settings.json",
		".env",
		"config.json",
	],
)
def test_forbidden_archive_members(verifier, tmp_path, name):
	make_artifacts(tmp_path, member=name)
	with pytest.raises(verifier.VerificationError):
		verifier.check_artifacts(tmp_path, VERSION)


def test_tracked_files_reads_the_real_repository(verifier):
	"""守卫必须真的读 git：`PYTHONUTF8` 之外不改环境，路径与当前仓库一致。"""
	tracked = verifier.tracked_files(ROOT, timeout=30)
	assert "pyproject.toml" in tracked
	assert "scripts/verify_release.py" in tracked
	assert not any(name.startswith("src/") for name in tracked if name.endswith(".pyc"))


def test_tracked_file_matching_ignores_wheel_dist_info_and_directories(verifier):
	names = [
		f"boss_agent_cli-{VERSION}/",
		f"boss_agent_cli-{VERSION}.dist-info/METADATA",
		f"boss_agent_cli-{VERSION}/PKG-INFO",
		"boss_agent_cli/__init__.py",
	]
	verifier.check_tracked_members(names, {"src/boss_agent_cli/__init__.py"}, version=VERSION)


def test_regular_bridge_word_is_not_a_removed_component(verifier):
	verifier.check_member("tests/test_removed_bridge.py", VERSION, sdist=False)
	verifier.check_member(f"boss_agent_cli-{VERSION}/docs/bridge-history.md", VERSION, sdist=True)


def test_artifacts_reject_members_that_git_does_not_track(verifier, tmp_path):
	"""构建后端只读 `.gitignore`，`.git/info/exclude` 里的本地文件会被静默打进源码包。

	v3.3.0 准备时本地源码包因此从 6.5 MB 涨到 78 MB（混入演示素材、研究草稿与
	本地测试），而 `--require-clean` 的 git status 看不到被忽略的文件。
	"""
	tracked = {"src/boss_agent_cli/__init__.py", "README.md"}
	verifier.check_tracked_members(
		[f"boss_agent_cli-{VERSION}/PKG-INFO", f"boss_agent_cli-{VERSION}/README.md"],
		tracked,
		version=VERSION,
	)
	verifier.check_tracked_members(["boss_agent_cli/__init__.py"], tracked, version=VERSION)
	with pytest.raises(verifier.VerificationError, match="未被 git 跟踪"):
		verifier.check_tracked_members(
			[f"boss_agent_cli-{VERSION}/docs/research/draft.md"], tracked, version=VERSION
		)
	with pytest.raises(verifier.VerificationError, match="未被 git 跟踪"):
		verifier.check_tracked_members(["boss_agent_cli/private_module.py"], tracked, version=VERSION)


def test_artifact_check_reads_the_tracked_file_list(verifier, tmp_path, monkeypatch):
	"""`root` 给出时必须真的去问 git，而不是只做形状检查。"""
	make_artifacts(tmp_path, member="boss_agent_cli/private_module.py")
	calls = []

	def fake_tracked_files(root, *, timeout):
		calls.append((root, timeout))
		return set()

	monkeypatch.setattr(verifier, "tracked_files", fake_tracked_files)
	with pytest.raises(verifier.VerificationError, match="未被 git 跟踪"):
		verifier.check_artifacts(tmp_path, VERSION, root=tmp_path, timeout=3)
	assert calls == [(tmp_path, 3)]
	monkeypatch.setattr(
		verifier, "tracked_files", lambda root, *, timeout: {"PKG-INFO", "src/boss_agent_cli/private_module.py"}
	)
	assert verifier.check_artifacts(tmp_path, VERSION, root=tmp_path, timeout=3)["files"]


def test_archive_links_and_wrong_sdist_root_are_rejected(verifier):
	with pytest.raises(verifier.VerificationError):
		verifier.check_member("package/file", VERSION, sdist=True)
	with pytest.raises(verifier.VerificationError):
		verifier.check_member("package/file", VERSION, sdist=False, link=True)


def test_removed_extra_and_duplicate_version_are_rejected(verifier):
	for extra in ("Provides-Extra: bridge\n", f"Version: {VERSION}\n"):
		with pytest.raises(verifier.VerificationError):
			verifier.check_metadata(metadata(extra=extra), VERSION)


@pytest.mark.parametrize("windows,executable", [(False, "bin/python"), (True, "Scripts/python.exe")])
def test_environment_isolated_and_platform_aware(verifier, monkeypatch, tmp_path, windows, executable):
	for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "BOSS_DATA_DIR", "UV_TARGET", "PIP_TARGET"):
		monkeypatch.setenv(key, "unwanted")
	python, env = verifier.environment(tmp_path, windows=windows)
	assert python == tmp_path / executable
	assert env["PATH"].startswith(str(python.parent))
	assert not {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "BOSS_DATA_DIR", "UV_TARGET", "PIP_TARGET"} & env.keys()
	assert env["PYTHONUTF8"] == "1"


def test_git_clean_and_tag_checks(verifier, monkeypatch, tmp_path):
	responses = {
		("rev-parse", "HEAD"): "abc",
		("status", "--porcelain", "--untracked-files=all"): "",
		("rev-parse", "--verify", f"refs/tags/v{VERSION}^{{commit}}"): "abc",
	}
	monkeypatch.setattr(verifier, "run_command", lambda argv, **kw: responses[tuple(argv[1:])])
	assert verifier.check_git(tmp_path, VERSION, timeout=1, require_clean=True, tag=f"v{VERSION}")["clean"]
	responses[("status", "--porcelain", "--untracked-files=all")] = " M changed.py"
	with pytest.raises(verifier.VerificationError, match="不干净"):
		verifier.check_git(tmp_path, VERSION, timeout=1, require_clean=True, tag=None)
	with pytest.raises(verifier.VerificationError, match="标签"):
		verifier.check_git(tmp_path, VERSION, timeout=1, require_clean=False, tag="v0.0.1")


def test_command_timeout_kills_process_group(verifier, monkeypatch, tmp_path):
	process = MagicMock()
	process.__enter__.return_value = process
	process.pid = 999999
	process.communicate.side_effect = [subprocess.TimeoutExpired("uv", 1), ("", "")]
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)
	kill = MagicMock()
	monkeypatch.setattr(verifier, "os", SimpleNamespace(name="posix", killpg=kill))
	with pytest.raises(verifier.VerificationError, match="超时"):
		verifier.run_command(["uv"], cwd=tmp_path, timeout=1)
	kill.assert_called_once_with(process.pid, verifier.signal.SIGKILL)


def test_offline_report_never_installs(verifier, monkeypatch, tmp_path):
	source_tree(tmp_path)
	make_artifacts(tmp_path / "dist")
	monkeypatch.setattr(verifier, "check_git", lambda *a, **kw: {"clean": False, "commit": "abc"})
	monkeypatch.setattr(verifier, "tracked_files", lambda root, *, timeout: {"PKG-INFO"})
	monkeypatch.setattr(verifier, "run_command", lambda *a, **kw: pytest.fail("默认检查不运行安装器"))
	report = verifier.verify(options(tmp_path / "dist"), tmp_path)
	assert report["ok"]
	assert [s["status"] for s in report["steps"]][-3:] == ["not_run"] * 3


@pytest.mark.parametrize(
	"failure", [None, "uv_missing", "install", "probe_json", "probe_false", "probe_version", "probe_incomplete"]
)
def test_fresh_install_reports_failures_and_cleans(verifier, monkeypatch, tmp_path, failure):
	source_tree(tmp_path)
	make_artifacts(tmp_path / "dist")
	monkeypatch.setattr(verifier, "check_git", lambda *a, **kw: {"clean": True})
	monkeypatch.setattr(verifier, "tracked_files", lambda root, *, timeout: {"PKG-INFO"})
	monkeypatch.setattr(verifier.shutil, "which", lambda name: None if failure == "uv_missing" else "/fake/uv")
	calls = []

	def run(argv, **kw):
		calls.append((argv, kw))
		if "pip" in argv and failure == "install":
			raise verifier.VerificationError("安装失败")
		if argv[0].endswith(("python", "python.exe")):
			if failure == "probe_json":
				return "not json"
			if failure == "probe_incomplete":
				return json.dumps({"ok": True, "version": VERSION})
			return json.dumps(
				probe_report(ok=failure != "probe_false", version="0.0.1" if failure == "probe_version" else VERSION)
			)
		return ""

	monkeypatch.setattr(verifier, "run_command", run)
	report = verifier.verify(options(tmp_path / "dist", fresh_install=True), tmp_path)
	assert report["ok"] is (failure is None)
	if failure:
		assert any(s["status"] == "failed" for s in report["steps"])
	for argv, kwargs in calls:
		assert not kwargs["cwd"].exists()
		assert "PYTHONPATH" not in kwargs["env"]
	if failure is None:
		assert calls[1][0][-1].endswith(".whl[mcp]")
		assert "--refresh-package" not in calls[1][0]


@pytest.mark.parametrize("install_fails", [False, True])
def test_pypi_uses_exact_version_and_refreshes_only_target(verifier, monkeypatch, tmp_path, install_fails):
	monkeypatch.setattr(verifier, "check_git", lambda *a, **kw: {"clean": True})
	monkeypatch.setattr(verifier.shutil, "which", lambda _: "/fake/uv")
	calls = []

	def run(argv, **kw):
		calls.append(argv)
		if "pip" in argv and install_fails:
			raise verifier.VerificationError("安装失败")
		return json.dumps(probe_report()) if argv[0].endswith(("python", "python.exe")) else ""

	monkeypatch.setattr(verifier, "run_command", run)
	report = verifier.verify(options(pypi_version=VERSION), tmp_path)
	assert report["ok"] is (not install_fails)
	install = calls[1]
	assert install[-1] == f"boss-agent-cli[mcp]=={VERSION}"
	assert "--refresh-package" in install
	assert install[install.index("--refresh-package") + 1] == "boss-agent-cli"
	assert install.count("--refresh-package") == 1
	assert "--refresh" not in install
	assert "--no-cache" not in install
	assert report["steps"][-1]["status"] == ("not_run" if install_fails else "passed")


def test_cli_failure_is_single_json(verifier, monkeypatch, tmp_path, capsys):
	monkeypatch.setattr(verifier, "source_version", lambda root: VERSION)
	monkeypatch.setattr(verifier, "check_git", lambda *a, **kw: {"clean": True})
	assert verifier.main(["--artifacts", str(tmp_path), "--json"]) == 1
	result = json.loads(capsys.readouterr().out)
	assert result["ok"] is False
	assert result["steps"][2]["status"] == "failed"


def test_probe_rejects_missing_tools_and_wrong_envelope(probe_module):
	probe_module.check_tools(["a", "b"], ["b", "a"])
	for actual in (["a"], ["a", "a", "b"]):
		with pytest.raises(probe_module.ProbeError):
			probe_module.check_tools(actual, ["a", "b"])
	with pytest.raises(probe_module.ProbeError):
		probe_module.envelope('{"ok":true}')


@pytest.mark.parametrize("sent,code", [(True, "CONFIRMATION_REQUIRED"), (False, "AUTH_REQUIRED")])
def test_probe_rejects_unsafe_or_wrong_confirmation_result(probe_module, sent, code):
	with pytest.raises(probe_module.ProbeError):
		probe_module.check_preview(
			{"ok": True, "data": {"dry_run": True, "sent": sent}}, {"ok": False, "error": {"code": code}}
		)


def test_windows_timeout_terminates_process_tree(verifier, monkeypatch, tmp_path):
	process = MagicMock()
	process.__enter__.return_value = process
	process.pid = 123
	process.communicate.side_effect = [subprocess.TimeoutExpired("uv", 1), ("", "")]
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)
	monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt"))
	kill_tree = MagicMock()
	monkeypatch.setattr(verifier.subprocess, "run", kill_tree)
	with pytest.raises(verifier.VerificationError, match="超时"):
		verifier.run_command(["uv"], cwd=tmp_path, timeout=1)
	assert kill_tree.call_args.args[0] == ["taskkill", "/PID", "123", "/T", "/F"]
	process.kill.assert_called_once()


@pytest.mark.parametrize("output", ['{"ok": true}', "invalid"])
def test_nonzero_process_cannot_claim_success(verifier, monkeypatch, tmp_path, output):
	process = MagicMock()
	process.__enter__.return_value = process
	process.returncode = 1
	process.communicate.return_value = (output, "")
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)
	with pytest.raises(verifier.VerificationError, match="退出码 1"):
		verifier.run_command(["python"], cwd=tmp_path, timeout=1, allow_failure=True)


def test_failed_command_reports_the_stderr_reason(verifier, monkeypatch, tmp_path):
	"""失败报告要带上 stderr 尾部：只报退出码无法区分索引延迟与真实冲突（Issue #1107）。"""
	process = MagicMock()
	process.__enter__.return_value = process
	process.returncode = 1
	process.communicate.return_value = (
		"",
		"  × No solution found when resolving dependencies:\n"
		"  ╰─▶ Because there is no version of boss-agent-cli[mcp]==999.0.0\n"
		"Using credentials from https://maintainer:hunter2@nested@pypi.example/simple\n",
	)
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)

	with pytest.raises(verifier.VerificationError) as error:
		verifier.run_command(["uv", "pip", "install"], cwd=tmp_path, timeout=1)

	message = str(error.value)
	assert message.startswith("uv 失败，退出码 1：")
	assert "no version of boss-agent-cli[mcp]==999.0.0" in message
	assert "https://[REDACTED]@pypi.example/simple" in message
	assert "hunter2" not in message
	assert "nested@" not in message


def test_failed_command_tail_is_bounded_and_keeps_the_last_lines(verifier, monkeypatch, tmp_path):
	process = MagicMock()
	process.__enter__.return_value = process
	process.returncode = 2
	lines = [f"line-{index:02d}" for index in range(40)] + ["x" * 5000]
	process.communicate.return_value = ("", "\n".join(lines))
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)

	with pytest.raises(verifier.VerificationError) as error:
		verifier.run_command(["git", "ls-files"], cwd=tmp_path, timeout=1)

	message = str(error.value)
	assert message.startswith("git 失败，退出码 2：")
	assert "line-39" in message
	assert "line-00" not in message
	tail_lines = message.split("：", 1)[1].splitlines()
	assert len(tail_lines) == verifier._STDERR_TAIL_LINES
	assert all(len(line) <= verifier._STDERR_LINE_LIMIT for line in tail_lines)
	assert "x" * verifier._STDERR_LINE_LIMIT in message
	assert "x" * (verifier._STDERR_LINE_LIMIT + 1) not in message


def test_credentials_are_redacted_before_truncation(verifier, monkeypatch, tmp_path):
	"""凭据的 `@` 落在 300 字符之外时也必须整体抹掉（Issue #1107 复核）。

	先截断再脱敏会切掉 `user:pass@host` 的 `@`，正则认不出 userinfo，凭据
	前缀原样进入报告；因此顺序必须是先脱敏、后截断。
	"""
	process = MagicMock()
	process.__enter__.return_value = process
	process.returncode = 1
	process.communicate.return_value = ("", "x" * 270 + " https://maintainer:" + "A" * 100 + "@pypi.example/simple")
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)

	with pytest.raises(verifier.VerificationError) as error:
		verifier.run_command(["uv"], cwd=tmp_path, timeout=1)

	message = str(error.value)
	assert "https://[REDACTED]@" in message
	assert "A" * 10 not in message
	tail_lines = message.split("：", 1)[1].splitlines()
	assert all(len(line) <= verifier._STDERR_LINE_LIMIT for line in tail_lines)


def test_bare_userinfo_query_credentials_and_bearer_are_redacted(verifier, monkeypatch, tmp_path):
	"""无 scheme 的 userinfo、查询串凭据与 Bearer 令牌同样先抹掉。"""
	process = MagicMock()
	process.__enter__.return_value = process
	process.returncode = 1
	process.communicate.return_value = (
		"",
		"index user:hunter2@pypi.example/simple?token=abc123&page=2\n"
		"Authorization: Bearer ghp_abcdefghijklmnop\n"
		"note: expected Bearer token\n",
	)
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)

	with pytest.raises(verifier.VerificationError) as error:
		verifier.run_command(["uv"], cwd=tmp_path, timeout=1)

	message = str(error.value)
	assert "[REDACTED]@pypi.example/simple" in message
	assert "token=[REDACTED]" in message
	assert "hunter2" not in message
	assert "abc123" not in message
	assert "ghp_abcdefghijklmnop" not in message
	assert "page=2" in message
	assert "expected Bearer token" in message


def test_failed_command_without_output_keeps_the_plain_message(verifier, monkeypatch, tmp_path):
	process = MagicMock()
	process.__enter__.return_value = process
	process.returncode = 3
	process.communicate.return_value = ("", " \n\t\n")
	monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **kw: process)

	with pytest.raises(verifier.VerificationError) as error:
		verifier.run_command(["uv"], cwd=tmp_path, timeout=1)

	assert str(error.value) == "uv 失败，退出码 3"


def test_probe_timeout_is_a_failed_json_report(probe_module, monkeypatch, capsys):
	import asyncio

	async def blocked(*args):
		await asyncio.Future()

	monkeypatch.setattr(probe_module, "probe", blocked)
	assert probe_module.main(["--version", VERSION, "--timeout", "0.01"]) == 1
	result = json.loads(capsys.readouterr().out)
	assert result["ok"] is False
	assert result["error"] == "TimeoutError"
