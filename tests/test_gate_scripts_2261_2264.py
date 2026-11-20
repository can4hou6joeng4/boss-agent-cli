"""维护脚本门禁的可信度回归（Issue #2261–#2264）。

全部离线：只用合成输入与内存桩，不启动真实登录、不请求平台、不安装依赖。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


@pytest.fixture
def scripts(monkeypatch):
	monkeypatch.syspath_prepend(str(SCRIPTS))
	return {
		name: importlib.import_module(name)
		for name in ("smoke_p0", "_release_probe", "verify_release", "package_portable")
	}


class _CaptureStream:
	def __init__(self) -> None:
		self.chunks: list[str] = []

	def write(self, text: str) -> None:
		self.chunks.append(text)


class _LatinStream(_CaptureStream):
	"""模拟非 UTF-8 终端：只要出现非 ASCII 字符就抛 UnicodeEncodeError。"""

	def write(self, text: str) -> None:
		if not text.isascii():
			raise UnicodeEncodeError("latin-1", text, 0, len(text), "ordinal not in range(256)")
		super().write(text)


class _StubRunner:
	"""替换 SmokeRunner：记录构造参数并返回固定状态序列。"""

	instances: list["_StubRunner"] = []

	def __init__(self, steps, **kwargs) -> None:
		self.steps = steps
		self.kwargs = kwargs
		self.statuses = list(_StubRunner.statuses)
		_StubRunner.instances.append(self)

	def run(self) -> dict:
		return {"steps": [{"name": step.name, "status": status} for step, status in zip(self.steps, self.statuses)]}


def _stub_smoke(monkeypatch, module, statuses):
	_StubRunner.instances = []
	_StubRunner.statuses = statuses
	monkeypatch.setattr(module, "SmokeRunner", _StubRunner)
	return _StubRunner


# ── smoke_p0（Issue #2261） ─────────────────────────────────────────


@pytest.mark.parametrize(("raw", "expected"), [("", 30), ("   ", 30), ("7", 7), (" 12 ", 12)])
def test_smoke_timeout_defaults_and_accepts_positive(scripts, raw, expected):
	assert scripts["smoke_p0"].parse_timeout(raw) == expected


@pytest.mark.parametrize("raw", ["30s", "1.5", "0", "-5", "abc", "9" * 10, "9" * 5000])
def test_smoke_timeout_rejects_invalid(scripts, raw):
	with pytest.raises(scripts["smoke_p0"].SmokeConfigError):
		scripts["smoke_p0"].parse_timeout(raw)


@pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "Yes", "on", "Y", " 1 "])
def test_smoke_dry_run_accepts_case_insensitive_truthy(scripts, raw):
	assert scripts["smoke_p0"].parse_dry_run(raw) is True


@pytest.mark.parametrize("raw", ["", "0", "false", "No", "off"])
def test_smoke_dry_run_accepts_falsy(scripts, raw):
	assert scripts["smoke_p0"].parse_dry_run(raw) is False


@pytest.mark.parametrize("raw", ["live", "maybe", "2", "tru"])
def test_smoke_dry_run_refuses_unknown_values_instead_of_going_live(scripts, raw):
	with pytest.raises(scripts["smoke_p0"].SmokeConfigError):
		scripts["smoke_p0"].parse_dry_run(raw)


def test_smoke_main_treats_uppercase_true_as_dry_run(scripts, monkeypatch, capsys):
	module = scripts["smoke_p0"]
	monkeypatch.setenv("BOSS_SMOKE_DRY_RUN", "True")
	stub = _stub_smoke(monkeypatch, module, ["dry_run"] * 4)

	assert module.main() == 0
	assert stub.instances[0].kwargs["dry_run"] is True
	assert len(json.loads(capsys.readouterr().out)["steps"]) == 4


def test_smoke_main_exit_code_reflects_failed_steps(scripts, monkeypatch, capsys):
	module = scripts["smoke_p0"]
	monkeypatch.setenv("BOSS_SMOKE_DRY_RUN", "0")
	_stub_smoke(monkeypatch, module, ["contract_error", "pass", "env_error", "timeout"])

	assert module.main() == 1
	assert len(json.loads(capsys.readouterr().out)["steps"]) == 4


def test_smoke_main_invalid_env_exits_two_without_report(scripts, monkeypatch, capsys):
	module = scripts["smoke_p0"]
	monkeypatch.setenv("BOSS_SMOKE_TIMEOUT", "")
	monkeypatch.setenv("BOSS_SMOKE_TIMEOUT", "30s")

	assert module.main() == 2
	captured = capsys.readouterr()
	assert captured.out == ""
	assert "BOSS_SMOKE_TIMEOUT" in captured.err


def test_smoke_parse_envelope_survives_oversized_integer(scripts):
	payload, error = scripts["smoke_p0"].parse_envelope('{"ok": ' + "9" * 5000 + "}")

	assert payload is None
	assert error == "stdout was not a JSON envelope"


def test_smoke_runner_reports_undecodable_output_as_contract_error(scripts):
	module = scripts["smoke_p0"]

	def fake_run(command, cwd, capture_output, text, timeout, check):
		raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

	step = module.SmokeStep(
		name="doctor",
		platform="zhipin",
		purpose="decode",
		preconditions=["command:boss"],
		failure_classification="env_error",
		command=["boss", "doctor"],
	)
	result = module.SmokeRunner([step], run_command=fake_run).run()["steps"][0]

	assert result["status"] == "contract_error"
	assert "UTF-8" in result["detail"]


def test_smoke_contract_error_includes_stderr_without_leaking_security_id(scripts):
	module = scripts["smoke_p0"]

	def fake_run(command, cwd, capture_output, text, timeout, check):
		return module.CommandResult(
			returncode=3,
			stdout="Traceback (most recent call last):",
			stderr="boom for abc-SECURITY-ID-123\nsecond line",
		)

	step = module.SmokeStep(
		name="detail",
		platform="zhipin",
		purpose="stderr",
		preconditions=["command:boss"],
		failure_classification="command_error",
		command=["boss", "detail", "abc-SECURITY-ID-123"],
	)
	result = module.SmokeRunner([step], run_command=fake_run).run()["steps"][0]

	assert result["status"] == "contract_error"
	assert "boom" in result["detail"]
	assert "abc-SECURITY-ID-123" not in result["detail"]
	assert result["command"] == ["boss", "detail", "<redacted>"]


def test_smoke_subprocess_exchanges_utf8_regardless_of_locale(scripts, monkeypatch):
	module = scripts["smoke_p0"]
	seen = {}

	class Completed:
		returncode = 0
		stdout = "{}"
		stderr = ""

	def fake_run(command, **kwargs):
		seen.update(kwargs)
		return Completed()

	monkeypatch.setattr(module.subprocess, "run", fake_run)
	module.SmokeRunner([])._default_run_command(["boss"], ROOT, True, True, 5, False)

	assert seen["encoding"] == "utf-8"
	assert seen["errors"] == "replace"
	assert seen["env"]["PYTHONIOENCODING"] == "utf-8"


def test_smoke_report_falls_back_to_ascii_on_non_utf8_stream(scripts):
	stream = _LatinStream()
	scripts["smoke_p0"].write_report({"steps": [{"name": "doctor", "purpose": "验证"}]}, stream=stream)

	written = json.loads("".join(stream.chunks))
	assert written["steps"][0]["purpose"] == "验证"


# ── _release_probe（Issue #2262） ─────────────────────────────────


def test_probe_unwraps_exception_group_to_probe_error_message(scripts):
	probe = scripts["_release_probe"]
	group = BaseExceptionGroup("unhandled errors in a TaskGroup", [probe.ProbeError("MCP 工具清单与安装包 TOOLS 不一致")])

	assert probe.describe_error(group) == "MCP 工具清单与安装包 TOOLS 不一致"


def test_probe_describes_nested_groups_and_plain_errors(scripts):
	probe = scripts["_release_probe"]
	nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [KeyError("data")])])

	assert probe.describe_error(nested) == "KeyError: 'data'"
	assert probe.describe_error(TimeoutError()) == "TimeoutError"
	assert probe.describe_error(probe.ProbeError("")) == "安装环境不满足发布契约"


def test_probe_main_reports_group_message_instead_of_group_name(scripts, monkeypatch, capsys):
	probe_module = scripts["_release_probe"]

	async def boom(*args):
		raise BaseExceptionGroup("unhandled errors in a TaskGroup", [probe_module.ProbeError("MCP 工具清单不一致")])

	monkeypatch.setattr(probe_module, "probe", boom)

	assert probe_module.main(["--version", "1.2.3", "--timeout", "5"]) == 1
	result = json.loads(capsys.readouterr().out)
	assert result["ok"] is False
	assert result["error"] == "MCP 工具清单不一致"


# ── verify_release（Issue #2263） ─────────────────────────────────


def test_verify_release_accepts_non_utf8_child_output(scripts, tmp_path):
	verifier = scripts["verify_release"]

	output = verifier.run_command(
		[sys.executable, "-c", "import sys; sys.stderr.buffer.write('错误: 构建失败'.encode('gbk'))"],
		cwd=tmp_path,
		timeout=60,
	)

	assert output == ""


def test_verify_release_failure_records_type_and_message(scripts):
	verifier = scripts["verify_release"]

	assert verifier.describe_error(KeyError("platforms")) == "KeyError: 'platforms'"
	assert verifier.describe_error(verifier.VerificationError("产物目录为空")) == "产物目录为空"


def test_verify_release_report_falls_back_to_ascii_on_non_utf8_stream(scripts):
	verifier = scripts["verify_release"]
	stream = _LatinStream()

	verifier.write_report({"ok": False, "steps": [{"name": "source", "status": "failed"}]}, stream=stream)

	written = json.loads("".join(stream.chunks))
	assert written["steps"][0]["status"] == "failed"


# ── package_portable（Issue #2264） ───────────────────────────────


def _portable_project(tmp_path: Path, version: str = "1.13.1") -> Path:
	project_root = tmp_path / "project"
	project_root.mkdir()
	(project_root / "pyproject.toml").write_text(
		f'[tool.example]\nversion = "9.9.9"\n[project]\nversion = "{version}"\n',
		encoding="utf-8",
	)
	return project_root


def _wheel(tmp_path: Path, version: str) -> Path:
	wheel = tmp_path / f"boss_agent_cli-{version}-py3-none-any.whl"
	wheel.write_bytes(b"fake-wheel")
	return wheel


def test_parse_project_version_ignores_other_tables(scripts, tmp_path):
	assert scripts["package_portable"].parse_project_version(_portable_project(tmp_path) / "pyproject.toml") == "1.13.1"


def test_portable_build_rejects_mismatched_wheel_and_keeps_previous_bundle(scripts, tmp_path):
	module = scripts["package_portable"]
	project_root = _portable_project(tmp_path)
	output_root = tmp_path / "portable"
	config = module.PortableConfig(
		project_root=project_root, output_root=output_root, platform="macos-arm64", wheel_path=_wheel(tmp_path, "1.13.1")
	)
	first = module.create_portable_bundle(config)
	installer = (first.bundle_dir / "install.sh").read_text(encoding="utf-8")

	stale = _wheel(tmp_path, "1.0.0")
	with pytest.raises(module.PortableBuildError, match="不一致"):
		module.create_portable_bundle(
			module.PortableConfig(
				project_root=project_root, output_root=output_root, platform="macos-arm64", wheel_path=stale
			)
		)

	assert (first.bundle_dir / "install.sh").read_text(encoding="utf-8") == installer
	assert (first.bundle_dir / "wheels" / "boss_agent_cli-1.13.1-py3-none-any.whl").exists()
	assert not (output_root / ".boss-agent-cli-1.13.1.building").exists()


def test_portable_build_missing_wheel_keeps_previous_bundle(scripts, tmp_path):
	module = scripts["package_portable"]
	project_root = _portable_project(tmp_path)
	output_root = tmp_path / "portable"
	module.create_portable_bundle(
		module.PortableConfig(
			project_root=project_root, output_root=output_root, platform="macos-arm64", wheel_path=_wheel(tmp_path, "1.13.1")
		)
	)

	with pytest.raises(module.PortableBuildError, match="不存在"):
		module.create_portable_bundle(
			module.PortableConfig(
				project_root=project_root,
				output_root=output_root,
				platform="macos-arm64",
				wheel_path=tmp_path / "missing.whl",
			)
		)

	assert (output_root / "boss-agent-cli-1.13.1" / "install.sh").exists()
