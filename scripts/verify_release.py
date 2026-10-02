"""检查发布产物，并按显式选项在隔离环境中验证安装；不执行发布。"""

from __future__ import annotations

import argparse
import ast
from email.parser import BytesParser
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import zipfile

from package_portable import parse_project_version


ROOT = Path(__file__).resolve().parents[1]
_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+\-]*\Z")


class VerificationError(Exception):
	"""发布检查失败，不携带子进程环境或凭据。"""


def run_command(
	argv: list[str],
	*,
	cwd: Path,
	timeout: float,
	env: dict[str, str] | None = None,
	allow_failure: bool = False,
) -> str:
	with subprocess.Popen(
		argv,
		cwd=cwd,
		env=env,
		text=True,
		encoding="utf-8",
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		start_new_session=os.name != "nt",
	) as process:
		try:
			stdout, _ = process.communicate(timeout=timeout)
		except subprocess.TimeoutExpired as exc:
			if os.name == "nt":
				try:
					subprocess.run(
						["taskkill", "/PID", str(process.pid), "/T", "/F"],
						stdout=subprocess.DEVNULL,
						stderr=subprocess.DEVNULL,
						timeout=10,
						check=False,
					)
				finally:
					process.kill()
			else:
				try:
					os.killpg(process.pid, signal.SIGKILL)
				except ProcessLookupError:
					pass
			process.communicate()
			raise VerificationError(f"{Path(argv[0]).name} 超时（{timeout:g} 秒）") from exc
		if process.returncode:
			if allow_failure:
				try:
					report = json.loads(stdout)
				except ValueError:
					report = None
				if isinstance(report, dict) and report.get("ok") is False:
					return stdout
			raise VerificationError(f"{Path(argv[0]).name} 失败，退出码 {process.returncode}")
		return stdout


def source_version(root: Path) -> str:
	version = parse_project_version(root / "pyproject.toml")
	tree = ast.parse((root / "src/boss_agent_cli/__init__.py").read_text(encoding="utf-8"))
	values = [
		ast.literal_eval(node.value)
		for node in tree.body
		if isinstance(node, ast.Assign)
		and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
	]
	if values != [version] or not _VERSION.fullmatch(version):
		raise VerificationError("项目版本与源码 __version__ 不一致或无效")
	return version


def check_git(root: Path, version: str, *, timeout: float, require_clean: bool, tag: str | None) -> dict[str, Any]:
	def git(*args: str) -> str:
		return run_command(["git", *args], cwd=root, timeout=timeout).strip()

	head = git("rev-parse", "HEAD")
	clean = not git("status", "--porcelain", "--untracked-files=all")
	if require_clean and not clean:
		raise VerificationError("工作树不干净，不能作为发布门禁证据")
	if tag and (tag != f"v{version}" or git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}") != head):
		raise VerificationError("标签版本或目标提交与当前源码不一致")
	return {"commit": head, "clean": clean, "tag": tag}


def check_member(name: str, version: str, *, sdist: bool, link: bool = False) -> None:
	path = PurePosixPath(name)
	parts = path.parts
	if link or path.is_absolute() or ".." in parts or "\\" in name or re.match(r"^[A-Za-z]:", name):
		raise VerificationError(f"不允许的归档路径：{name}")
	if sdist:
		if not parts or parts[0] != f"boss_agent_cli-{version}":
			raise VerificationError("源码包根目录与版本不一致")
		parts = parts[1:]
	blocked = {".git", ".claude", ".boss-agent", "__pycache__", "CLAUDE.md", "AGENTS.md"}
	if any(p in blocked or p.startswith((".venv", ".env")) for p in parts):
		raise VerificationError(f"产物含本地配置或生成文件：{name}")
	if parts and (parts[0] == "extension" or parts == ("config.json",)):
		raise VerificationError(f"产物含不应分发的文件：{name}")
	if any(parts[i : i + 2] == ("boss_agent_cli", "bridge") for i in range(len(parts))):
		raise VerificationError(f"产物含已移除组件：{name}")


def check_metadata(raw: bytes, version: str) -> None:
	metadata = BytesParser().parsebytes(raw)
	if metadata.get_all("Name") != ["boss-agent-cli"] or metadata.get_all("Version") != [version]:
		raise VerificationError("归档包名或版本元数据不匹配")
	if "bridge" in metadata.get_all("Provides-Extra", []):
		raise VerificationError("产物仍声明已移除的 bridge extra")


def check_artifacts(directory: Path, version: str) -> dict[str, Any]:
	wheels = sorted(directory.glob("*.whl"))
	sdists = sorted(directory.glob("*.tar.gz"))
	if len(wheels) != 1 or len(sdists) != 1:
		raise VerificationError("产物目录必须恰好包含一个 wheel 和一个源码包，不能混入旧版本")
	wheel, sdist = wheels[0], sdists[0]
	if not wheel.name.startswith(f"boss_agent_cli-{version}-") or sdist.name != f"boss_agent_cli-{version}.tar.gz":
		raise VerificationError("产物文件名与目标版本不一致")
	with zipfile.ZipFile(wheel) as archive:
		for member in archive.infolist():
			check_member(member.filename, version, sdist=False, link=stat.S_ISLNK(member.external_attr >> 16))
		metadata_name = f"boss_agent_cli-{version}.dist-info/METADATA"
		if archive.namelist().count(metadata_name) != 1:
			raise VerificationError("wheel 缺少唯一的版本元数据")
		if archive.getinfo(metadata_name).file_size > 1024 * 1024:
			raise VerificationError("wheel 元数据异常大")
		check_metadata(archive.read(metadata_name), version)
	with tarfile.open(sdist) as archive:
		members = archive.getmembers()
		for member in members:
			check_member(member.name, version, sdist=True, link=not (member.isfile() or member.isdir()))
		metadata_members = [m for m in members if m.name == f"boss_agent_cli-{version}/PKG-INFO"]
		if len(metadata_members) != 1 or metadata_members[0].size > 1024 * 1024:
			raise VerificationError("源码包缺少唯一或合理大小的元数据")
		stream = archive.extractfile(metadata_members[0])
		if stream is None:
			raise VerificationError("源码包元数据不是普通文件")
		with stream:
			check_metadata(stream.read(), version)
	files = []
	for path in (wheel, sdist):
		digest = hashlib.sha256()
		with path.open("rb") as stream:
			for chunk in iter(lambda: stream.read(1024 * 1024), b""):
				digest.update(chunk)
		files.append({"name": path.name, "sha256": digest.hexdigest(), "size": path.stat().st_size})
	return {"wheel": str(wheel.resolve()), "files": files}


def environment(venv: Path, *, windows: bool | None = None) -> tuple[Path, dict[str, str]]:
	windows = os.name == "nt" if windows is None else windows
	bin_dir = venv / ("Scripts" if windows else "bin")
	env = {
		key: value
		for key, value in os.environ.items()
		if key != "VIRTUAL_ENV" and not key.startswith(("PYTHON", "BOSS_", "UV_", "PIP_"))
	}
	env.update(PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""), PYTHONUTF8="1", PYTHONNOUSERSITE="1")
	return bin_dir / ("python.exe" if windows else "python"), env


def verify(args: argparse.Namespace, root: Path = ROOT) -> dict[str, Any]:
	steps = [{"name": name, "status": "not_run"} for name in ("source", "git", "artifacts", "venv", "install", "probe")]
	report: dict[str, Any] = {"ok": False, "schema_version": "1.0", "version": None, "steps": steps}

	def step(name: str, operation: Any) -> Any:
		item = next(item for item in steps if item["name"] == name)
		print(f"检查 {name}", file=sys.stderr)
		try:
			result = operation()
		except Exception as exc:
			item.update(status="failed", error=str(exc) if isinstance(exc, VerificationError) else type(exc).__name__)
			raise
		item.update(status="passed", result=result)
		return result

	try:
		version = args.pypi_version or step("source", lambda: source_version(root))
		report["version"] = version
		step(
			"git",
			lambda: check_git(root, version, timeout=args.timeout, require_clean=args.require_clean, tag=args.tag),
		)
		artifacts = step("artifacts", lambda: check_artifacts(args.artifacts, version)) if args.artifacts else None
		if args.fresh_install or args.pypi_version:
			uv = shutil.which("uv")
			with tempfile.TemporaryDirectory(prefix="boss-release-verify-") as temporary:
				work = Path(temporary)
				venv = work / "venv"
				python, env = environment(venv)

				def create_venv() -> dict[str, str]:
					if not uv:
						raise VerificationError("缺少 uv，无法创建隔离安装环境")
					run_command(
						[uv, "--no-config", "venv", "--python", args.python, str(venv)],
						cwd=work,
						env=env,
						timeout=args.timeout,
					)
					return {"python": args.python}

				step("venv", create_venv)
				requirement = f"boss-agent-cli[mcp]=={version}" if args.pypi_version else f"{artifacts['wheel']}[mcp]"
				step(
					"install",
					lambda: run_command(
						[
							uv,
							"--no-config",
							"pip",
							"install",
							"--python",
							str(python),
							"--index-url",
							"https://pypi.org/simple",
							requirement,
						],
						cwd=work,
						env=env,
						timeout=args.timeout,
					),
				)

				def probe() -> dict[str, Any]:
					output = run_command(
						[
							str(python),
							str(ROOT / "scripts/_release_probe.py"),
							"--version",
							version,
							"--timeout",
							str(args.timeout),
						],
						cwd=work,
						env=env,
						timeout=args.timeout + 5,
						allow_failure=True,
					)
					result = json.loads(output)
					if not isinstance(result, dict) or result.get("ok") is not True or result.get("version") != version:
						detail = result.get("error", "版本不匹配") if isinstance(result, dict) else "报告不是对象"
						raise VerificationError(f"安装探针失败：{detail}")
					if (
						result.get("cli") != "passed"
						or result.get("mcp") != "passed"
						or result.get("preview_sent") is not False
						or result.get("unapproved_code") != "CONFIRMATION_REQUIRED"
						or not isinstance(result.get("tool_count"), int)
						or result["tool_count"] <= 0
					):
						raise VerificationError("安装探针缺少完整的 CLI/MCP 验证证据")
					return result

				step("probe", probe)
		report["ok"] = True
	except Exception as exc:
		if not any(item["status"] == "failed" for item in steps):
			steps.append({"name": "environment", "status": "failed", "error": type(exc).__name__})
	return report


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	source = parser.add_mutually_exclusive_group(required=True)
	source.add_argument("--artifacts", type=Path)
	source.add_argument("--pypi-version")
	parser.add_argument("--fresh-install", action="store_true")
	parser.add_argument("--python", default="3.11")
	parser.add_argument("--timeout", type=float, default=180)
	parser.add_argument("--require-clean", action="store_true")
	parser.add_argument("--tag")
	parser.add_argument("--json", action="store_true", help="兼容显式 JSON 标志；报告始终输出 JSON")
	args = parser.parse_args(argv)
	if (
		not math.isfinite(args.timeout)
		or args.timeout <= 0
		or (args.pypi_version and not _VERSION.fullmatch(args.pypi_version))
	):
		parser.error("timeout 必须为正数，版本必须为不含路径或空白的版本号")
	if args.fresh_install and not args.artifacts:
		parser.error("--fresh-install 只适用于 --artifacts")
	report = verify(args)
	print(json.dumps(report, ensure_ascii=False))
	return 0 if report["ok"] else 1


if __name__ == "__main__":
	raise SystemExit(main())
