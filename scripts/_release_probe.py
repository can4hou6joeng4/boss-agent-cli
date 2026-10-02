"""在目标虚拟环境中执行无平台请求的 CLI/MCP 安装验证。"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from smoke_p0 import parse_envelope, validate_envelope_result


class ProbeError(Exception):
	"""安装环境不满足发布契约。"""


def require(condition: bool, message: str) -> None:
	if not condition:
		raise ProbeError(message)


def envelope(text: str, returncode: int | None = None) -> dict[str, Any]:
	payload, error = parse_envelope(text)
	if error or payload is None:
		raise ProbeError(error or "缺少信封")
	code = returncode if returncode is not None else (0 if payload["ok"] else 1)
	if error := validate_envelope_result(payload, code):
		raise ProbeError(error)
	return payload


def check_tools(actual: list[str], expected: list[str]) -> None:
	require(bool(expected) and sorted(actual) == sorted(expected), "MCP 工具清单与安装包 TOOLS 不一致")


def check_preview(preview: dict[str, Any], rejected: dict[str, Any]) -> None:
	require(preview.get("ok") is True and isinstance(preview.get("data"), dict), "MCP 预览未成功")
	require(preview["data"].get("dry_run") is True and preview["data"].get("sent") is False, "MCP 预览没有保证不发送")
	require(rejected.get("ok") is False and isinstance(rejected.get("error"), dict), "未确认操作未被拒绝")
	require(rejected["error"].get("code") == "CONFIRMATION_REQUIRED", "未确认操作返回错误类型不正确")


async def probe(version: str, data_dir: Path, timeout: float) -> dict[str, Any]:
	import boss_agent_cli
	from boss_agent_cli.mcp_tools import TOOLS
	from boss_agent_cli.platforms import list_platforms
	from mcp import ClientSession, StdioServerParameters
	from mcp.client.stdio import stdio_client

	prefix = Path(sys.prefix).resolve()
	require(sys.prefix != sys.base_prefix, "探针必须在隔离虚拟环境中运行")
	require(Path(boss_agent_cli.__file__).resolve().is_relative_to(prefix), "导入来自虚拟环境之外，可能混入开发源码")
	package = importlib.metadata.distribution("boss-agent-cli")
	require(Path(package.locate_file("")).resolve().is_relative_to(prefix), "包元数据来自虚拟环境之外")
	require(package.version == boss_agent_cli.__version__ == version, "安装版本与目标版本不一致")
	require("bridge" not in package.metadata.get_all("Provides-Extra", []), "安装包仍声明 bridge extra")
	require(importlib.util.find_spec("boss_agent_cli.bridge") is None, "安装包仍包含 Bridge 模块")
	bin_dir = Path(sys.executable).parent
	boss = bin_dir / ("boss.exe" if os.name == "nt" else "boss")
	env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), "PYTHONUTF8": "1"}
	for key in ("PYTHONPATH", "PYTHONHOME"):
		env.pop(key, None)
	for command in (("schema", "--format", "native"), ("platforms",)):
		completed = subprocess.run(
			[str(boss), "--data-dir", str(data_dir), "--json", *command],
			cwd=data_dir,
			env=env,
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=min(timeout / 4, 20),
		)
		result = envelope(completed.stdout, completed.returncode)
		require(
			result["ok"] is True and result["command"] == command[0] and isinstance(result["data"], dict),
			f"CLI {command[0]} 检查失败",
		)
		if command[0] == "platforms":
			require(
				sorted(item["name"] for item in result["data"]["platforms"]) == list_platforms(),
				"CLI 平台清单与注册表不一致",
			)
		else:
			require(bool(result["data"].get("commands")), "CLI schema 命令清单为空")

	server = StdioServerParameters(
		command=sys.executable,
		args=["-m", "boss_agent_cli.mcp_server", "--data-dir", str(data_dir)],
		cwd=data_dir,
		env=env,
	)
	arguments = dict(
		geek_id="fixture-geek",
		job_id="fixture-job",
		expect_id="fixture-expect",
		lid="fixture-lid",
		security_id="fixture-security",
		message="离线预览测试",
	)
	async with stdio_client(server) as streams:
		async with ClientSession(*streams) as session:
			initialized = await session.initialize()
			require(initialized.server_info.name == "boss-agent-cli", "MCP 服务身份不匹配")
			listed = await session.list_tools()
			check_tools([tool.name for tool in listed.tools], [tool.name for tool in TOOLS])
			preview = await session.call_tool("boss_hr_greet", {**arguments, "dry_run": True})
			rejected = await session.call_tool("boss_hr_greet", arguments)
			check_preview(envelope(preview.content[0].text), envelope(rejected.content[0].text))
	require(not (data_dir / "cache/boss_agent.db").exists(), "预览或未确认操作创建了业务缓存")
	return {
		"ok": True,
		"version": version,
		"mcp_version": importlib.metadata.version("mcp"),
		"tool_count": len(TOOLS),
		"platforms": list_platforms(),
		"cli": "passed",
		"mcp": "passed",
		"preview_sent": False,
		"unapproved_code": "CONFIRMATION_REQUIRED",
	}


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--version", required=True)
	parser.add_argument("--timeout", type=float, default=60)
	args = parser.parse_args(argv)
	if not math.isfinite(args.timeout) or args.timeout <= 0:
		parser.error("timeout 必须为有限正数")
	try:
		with tempfile.TemporaryDirectory(prefix="boss-release-probe-") as temporary:
			result = asyncio.run(asyncio.wait_for(probe(args.version, Path(temporary), args.timeout), args.timeout))
	except Exception as exc:
		result = {
			"ok": False,
			"version": args.version,
			"error": str(exc) if isinstance(exc, ProbeError) else type(exc).__name__,
		}
	print(json.dumps(result, ensure_ascii=False))
	return 0 if result["ok"] else 1


if __name__ == "__main__":
	raise SystemExit(main())
