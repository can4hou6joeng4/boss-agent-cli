#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
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
import re
import shutil
import stat
import subprocess
import sys
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
	"""Read the project version from pyproject.toml without extra dependencies."""
	text = pyproject_path.read_text(encoding="utf-8")
	match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
	if not match:
		raise PortableBuildError(f"version not found in {pyproject_path}")
	return match.group(1)


def create_portable_bundle(config: PortableConfig) -> PortableBuildResult:
	"""Create the portable directory and zip archive."""
	project_root = config.project_root.resolve()
	version = parse_project_version(project_root / "pyproject.toml")
	wheel_path = config.wheel_path or _build_wheel(project_root)
	bundle_name = f"boss-agent-cli-{version}"
	bundle_dir = config.output_root / bundle_name
	archive_path = project_root / "dist" / f"boss-agent-cli-portable-{version}-{config.platform}.zip"
	if bundle_dir.exists():
		shutil.rmtree(bundle_dir)
	bundle_dir.mkdir(parents=True)
	(bundle_dir / "wheels").mkdir()
	(bundle_dir / "bin").mkdir()
	(bundle_dir / "examples").mkdir()
	shutil.copy2(wheel_path, bundle_dir / "wheels" / wheel_path.name)
	_write_executable(bundle_dir / "install.sh", _install_sh(wheel_path.name))
	_write_executable(bundle_dir / "bin" / "boss", _boss_wrapper())
	_write_executable(bundle_dir / "bin" / "boss-doctor", _boss_doctor_wrapper())
	(bundle_dir / "README-PORTABLE.md").write_text(_portable_readme(version), encoding="utf-8")
	_write_executable(bundle_dir / "examples" / "zhilian-recruiter.sh", _zhilian_example())
	_write_executable(bundle_dir / "examples" / "zhipin-recruiter.sh", _zhipin_example())
	_write_executable(bundle_dir / "examples" / "local-model.sh", _local_model_example())
	(bundle_dir / "examples" / "opencode.json").write_text(_opencode_example(), encoding="utf-8")
	archive_path.parent.mkdir(parents=True, exist_ok=True)
	if archive_path.exists():
		archive_path.unlink()
	_zip_dir(bundle_dir, archive_path)
	return PortableBuildResult(bundle_dir=bundle_dir, archive_path=archive_path, wheel_path=wheel_path)


def _build_wheel(project_root: Path) -> Path:
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
	wheels = sorted((project_root / "dist").glob("boss_agent_cli-*.whl"), key=lambda item: item.stat().st_mtime)
	if not wheels:
		raise PortableBuildError("uv build did not produce a boss_agent_cli wheel")
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

if [[ "${{BOSS_AGENT_INSTALL_BROWSER:-0}}" == "1" ]]; then
  if uv tool run patchright install chromium; then
    echo "patchright chromium installed"
  else
    echo "patchright chromium install failed. You can retry: uv tool run patchright install chromium" >&2
  fi
else
  echo "browser kernel skipped. To install it now: BOSS_AGENT_INSTALL_BROWSER=1 ./install.sh"
  echo "manual retry command: uv tool run patchright install chromium"
  echo "if headless shell is missing: uv tool run patchright install chromium-headless-shell"
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
boss --platform zhilian --role recruiter agent stats
```

Project-local state:

```bash
boss --data-dir ./.boss-agent ai local status
boss --data-dir ./.boss-agent --platform zhilian --role recruiter agent run --dry-run --limit 1
```

## Windows notes

If `uv tool update-shell` times out, temporarily expose global tools in PowerShell:

```powershell
$env:PATH = "$env:USERPROFILE\\.local\\bin;$env:PATH"
```

If patchright reports a missing headless shell in the global tool environment:

```powershell
uv tool run patchright install chromium-headless-shell
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


def _zhilian_example() -> str:
	return """#!/usr/bin/env bash
set -euo pipefail
boss --data-dir ./.boss-agent --platform zhilian --role recruiter status --live
boss --data-dir ./.boss-agent --platform zhilian --role recruiter agent run --dry-run --limit 1
boss --data-dir ./.boss-agent --platform zhilian --role recruiter agent stats
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
