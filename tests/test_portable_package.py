"""Portable package builder tests."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load_package_module() -> ModuleType:
	spec = importlib.util.spec_from_file_location(
		"package_portable",
		ROOT / "scripts" / "package_portable.py",
	)
	assert spec is not None
	assert spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	sys.modules["package_portable"] = module
	spec.loader.exec_module(module)
	return module


def test_parse_project_version_reads_pyproject() -> None:
	module = _load_package_module()

	version = module.parse_project_version(ROOT / "pyproject.toml")

	assert version
	assert version.count(".") >= 1


def test_create_portable_bundle_includes_installer_docs_and_wheel(tmp_path: Path) -> None:
	module = _load_package_module()
	project_root = tmp_path / "project"
	project_root.mkdir()
	(project_root / "pyproject.toml").write_text('[project]\nversion = "1.13.1"\n', encoding="utf-8")
	wheel = tmp_path / "boss_agent_cli-1.13.1-py3-none-any.whl"
	wheel.write_bytes(b"fake-wheel")

	result = module.create_portable_bundle(
		module.PortableConfig(
			project_root=project_root,
			output_root=tmp_path / "portable",
			platform="macos-arm64",
			wheel_path=wheel,
		)
	)

	assert result.bundle_dir.exists()
	assert result.archive_path.exists()
	assert (result.bundle_dir / "install.sh").exists()
	assert (result.bundle_dir / "bin" / "boss").exists()
	assert (result.bundle_dir / "bin" / "boss-doctor").exists()
	assert (result.bundle_dir / "README-PORTABLE.md").exists()
	assert (result.bundle_dir / "examples" / "opencode.json").exists()
	assert (result.bundle_dir / "examples" / "zhilian-recruiter.sh").exists()
	assert (result.bundle_dir / "wheels" / wheel.name).exists()

	install = (result.bundle_dir / "install.sh").read_text(encoding="utf-8")
	assert "uv tool install --force" in install
	assert "BOSS_AGENT_INSTALL_BROWSER" in install
	assert "patchright install chromium" in install
	opencode = (result.bundle_dir / "examples" / "opencode.json").read_text(encoding="utf-8")
	assert '"boss-mcp"' in opencode
	assert '"--data-dir"' in opencode

	with zipfile.ZipFile(result.archive_path) as archive:
		names = set(archive.namelist())
	assert any(name.endswith("/install.sh") for name in names)
	assert any(name.endswith(f"/wheels/{wheel.name}") for name in names)
	assert any(name.endswith("/examples/local-model.sh") for name in names)
	assert any(name.endswith("/examples/opencode.json") for name in names)
