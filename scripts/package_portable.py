#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# --- How to run ---
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/package_portable.py [--platform macos-arm64] [--output dist/portable]
# 3. Or make executable and run:
#      chmod +x scripts/package_portable.py && ./scripts/package_portable.py
# ------------------

"""Build a portable installer bundle for boss-agent-cli."""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_PLATFORM: Final = "macos-arm64"


@dataclass(frozen=True, slots=True)
class PortableConfig:
	project_root: Path
	output_root: Path
	platform: str
	wheel_path: Path | None = None


@dataclass(frozen=True, slots=True)
class PortableBuildResult:
	bundle_dir: Path
	archive_path: Path
	wheel_path: Path


class PortableBuildError(Exception):
	"""Raised when the portable package cannot be built."""


def parse_project_version(pyproject_path: Path) -> str:
	"""Read the ``[project]`` version from pyproject.toml.

	正则取文件里第一个 ``version = "..."`` 会把其它表（如 ``[tool.example]``）
	的值当成项目版本（Issue #2264）；按 TOML 解析只看 ``[project]``。
	"""
	try:
		with pyproject_path.open("rb") as stream:
			data = tomllib.load(stream)
	except FileNotFoundError:
		raise PortableBuildError(f"找不到 {pyproject_path}") from None
	except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
		raise PortableBuildError(f"无法读取 {pyproject_path}：{exc}") from exc
	project = data.get("project")
	version = project.get("version") if isinstance(project, dict) else None
	if not isinstance(version, str) or not version.strip():
		raise PortableBuildError(f"version not found in {pyproject_path}")
	return version.strip()


def _validated_wheel(wheel_path: Path, version: str) -> Path:
	"""校验外部提供的 wheel：存在、是普通文件、版本前缀与项目一致。"""
	try:
		mode = wheel_path.lstat().st_mode
	except FileNotFoundError:
		raise PortableBuildError(f"指定的 wheel 不存在：{wheel_path}") from None
	if not stat.S_ISREG(mode):
		raise PortableBuildError(f"指定的 wheel 不是普通文件：{wheel_path}")
	if wheel_path.suffix != ".whl" or not wheel_path.name.startswith(f"boss_agent_cli-{version}-"):
		raise PortableBuildError(f"wheel {wheel_path.name} 与项目版本 {version} 不一致")
	return wheel_path


def create_portable_bundle(config: PortableConfig) -> PortableBuildResult:
	"""Create the portable directory and zip archive."""
	project_root = config.project_root.resolve()
	version = parse_project_version(project_root / "pyproject.toml")
	# 先校验再删除：--wheel 路径写错或版本不符时不能把上一份可用包删掉。
	wheel_path = config.wheel_path or _build_wheel(project_root, version)
	wheel_path = _validated_wheel(wheel_path, version)
	bundle_name = f"boss-agent-cli-{version}"
	bundle_dir = config.output_root / bundle_name
	archive_path = project_root / "dist" / f"boss-agent-cli-portable-{version}-{config.platform}.zip"
	staging = config.output_root / f".{bundle_name}.building"
	if staging.exists():
		shutil.rmtree(staging)
	try:
		staging.mkdir(parents=True)
		(staging / "wheels").mkdir()
		(staging / "bin").mkdir()
		(staging / "examples").mkdir()
		shutil.copy2(wheel_path, staging / "wheels" / wheel_path.name)
		_write_executable(staging / "install.sh", _install_sh(wheel_path.name))
		_write_executable(staging / "bin" / "boss", _boss_wrapper())
		_write_executable(staging / "bin" / "boss-doctor", _boss_doctor_wrapper())
		(staging / "README-PORTABLE.md").write_text(_portable_readme(version), encoding="utf-8")
		_write_executable(staging / "examples" / "zhipin-recruiter.sh", _zhipin_example())
		_write_executable(staging / "examples" / "local-model.sh", _local_model_example())
		(staging / "examples" / "opencode.json").write_text(_opencode_example(), encoding="utf-8")
		if bundle_dir.exists():
			shutil.rmtree(bundle_dir)
		staging.rename(bundle_dir)
		archive_path.parent.mkdir(parents=True, exist_ok=True)
		if archive_path.exists():
			archive_path.unlink()
		_zip_dir(bundle_dir, archive_path)
	except OSError as exc:
		shutil.rmtree(staging, ignore_errors=True)
		raise PortableBuildError(f"构建便携包失败：{exc}") from exc
	return PortableBuildResult(bundle_dir=bundle_dir, archive_path=archive_path, wheel_path=wheel_path)


def _build_wheel(project_root: Path, version: str) -> Path:
	result = subprocess.run(
		["uv", "build", "--wheel"],
		cwd=project_root,
		check=False,
		text=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
	)
	if result.returncode != 0:
		raise PortableBuildError(result.stderr.strip() or result.stdout.strip() or "uv build --wheel failed")
	# 只挑当前版本：dist/ 里残留的旧 wheel 可能 mtime 更新（恢复的产物、时钟偏移）。
	wheels = sorted(
		(project_root / "dist").glob(f"boss_agent_cli-{version}-*.whl"),
		key=lambda item: item.stat().st_mtime,
	)
	if not wheels:
		raise PortableBuildError(f"uv build 没有产出 {version} 的 boss_agent_cli wheel")
	return wheels[-1]


def _write_executable(path: Path, content: str) -> None:
	path.write_text(content, encoding="utf-8")
	mode = path.stat().st_mode
	path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _zip_dir(source: Path, archive_path: Path) -> None:
	with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		for path in sorted(source.rglob("*")):
			archive.write(path, path.relative_to(source.parent))


def _install_sh(wheel_name: str) -> str:
	return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
WHEEL="$ROOT_DIR/wheels/{wheel_name}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This portable bundle is built for macOS. Continue only if you know what you are doing." >&2
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it with: brew install uv" >&2
  exit 1
fi

uv tool install --force "$WHEEL"

if command -v boss >/dev/null 2>&1; then
  boss --help >/dev/null
fi

# 内核安装必须用运行 boss 的同一个解释器：`uv tool install` 只把 boss /
# boss-mcp 放进 PATH，而 `uv tool run patchright` 会独立解析一个 patchright
# 版本，可能与 wheel 内的修订版不一致。boss 入口脚本的 shebang 就是工具环境
# 解释器，直接用它调模块入口（Issue #1060）。
BOSS_PYTHON=""
if command -v boss >/dev/null 2>&1; then
  BOSS_PYTHON="$(sed -n '1s/^#!//p' "$(command -v boss)")"
fi
if [[ -z "$BOSS_PYTHON" || ! -x "$BOSS_PYTHON" ]]; then
  BOSS_PYTHON="$(command -v python3 || true)"
fi
KERNEL_INSTALL=("$BOSS_PYTHON" -m patchright install chromium)
KERNEL_INSTALL_SHELL=("$BOSS_PYTHON" -m patchright install chromium-headless-shell)

if [[ "${{BOSS_AGENT_INSTALL_BROWSER:-0}}" == "1" ]]; then
  if "${{KERNEL_INSTALL[@]}}"; then
    echo "patchright chromium installed"
  else
    echo "patchright chromium install failed. You can retry: ${{KERNEL_INSTALL[*]}}" >&2
  fi
else
  echo "browser kernel skipped. To install it now: BOSS_AGENT_INSTALL_BROWSER=1 ./install.sh"
  echo "manual retry command: ${{KERNEL_INSTALL[*]}}"
  echo "if headless shell is missing: ${{KERNEL_INSTALL_SHELL[*]}}"
fi

cat <<'MSG'
boss-agent-cli portable install complete.

Try:
  boss schema --format native
  boss ai local status
  boss --data-dir ./.boss-agent agent stats
MSG
"""


def _boss_wrapper() -> str:
	return """#!/usr/bin/env bash
set -euo pipefail
exec boss "$@"
"""


def _boss_doctor_wrapper() -> str:
	return """#!/usr/bin/env bash
set -euo pipefail
exec boss doctor "$@"
"""


def _portable_readme(version: str) -> str:
	return f"""# boss-agent-cli portable bundle

Version: {version}

This bundle installs the `boss` and `boss-mcp` CLI entry points from the included wheel.
It does not include login sessions, cookies, Chrome user data, cache files, or local model weights.

## Install

```bash
./install.sh
```

## Use in any project

Shared global state:

```bash
boss ai local status
boss --role recruiter agent stats
```

Project-local state:

```bash
boss --data-dir ./.boss-agent ai local status
boss --data-dir ./.boss-agent --role recruiter agent run --dry-run --limit 1
```

## Windows notes

If `uv tool update-shell` times out, temporarily expose global tools in PowerShell:

```powershell
$env:PATH = "$env:USERPROFILE\\.local\\bin;$env:PATH"
```

If patchright reports a missing headless shell in the global tool environment,
run the kernel install with the same interpreter that runs boss. `boss doctor`
prints the full runnable command for this installation:

```powershell
boss doctor   # e.g. <python> -m patchright install chromium-headless-shell
```

Before live platform checks, run `boss login`; `AUTH_REQUIRED` means no login
session is saved yet, not that the CLI is broken.

## Local model

```bash
boss ai local configure --runtime ollama --model qwen3:14b
boss ai local pull --model qwen3:14b --confirm-download
boss ai local smoke
```

## OpenCode

Copy `examples/opencode.json` into an OpenCode project to expose the installed
`boss-mcp` server with project-local state in `./.boss-agent`.

Model weights stay outside this bundle.
"""


def _zhipin_example() -> str:
	return """#!/usr/bin/env bash
set -euo pipefail
boss --data-dir ./.boss-agent --platform zhipin --role recruiter agent run --dry-run --limit 1
boss --data-dir ./.boss-agent --platform zhipin --role recruiter agent stats
"""


def _local_model_example() -> str:
	return """#!/usr/bin/env bash
set -euo pipefail
boss --data-dir ./.boss-agent ai local configure --runtime ollama --model qwen3:14b
boss --data-dir ./.boss-agent ai local status
"""


def _opencode_example() -> str:
	return """{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "boss-agent": {
      "type": "local",
      "command": [
        "boss-mcp",
        "--data-dir",
        "./.boss-agent"
      ],
      "enabled": true,
      "timeout": 10000
    }
  }
}
"""


def main(argv: Sequence[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Build a portable boss-agent-cli installer bundle")
	parser.add_argument("--platform", default=DEFAULT_PLATFORM, help="bundle platform suffix")
	parser.add_argument("--output", type=Path, default=Path("dist/portable"), help="portable bundle output directory")
	parser.add_argument("--wheel", type=Path, default=None, help="reuse an existing wheel instead of running uv build")
	args = parser.parse_args(argv)
	try:
		result = create_portable_bundle(
			PortableConfig(
				project_root=ROOT,
				output_root=(ROOT / args.output).resolve() if not args.output.is_absolute() else args.output,
				platform=args.platform,
				wheel_path=args.wheel.resolve() if args.wheel else None,
			)
		)
	except PortableBuildError as exc:
		print(f"error: {exc}", file=sys.stderr)
		return 1
	print(f"bundle: {result.bundle_dir}")
	print(f"archive: {result.archive_path}")
	print(f"wheel: {result.wheel_path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
