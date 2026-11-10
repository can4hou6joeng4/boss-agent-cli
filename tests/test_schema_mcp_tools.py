"""验证 boss schema --format mcp-tools 输出符合 MCP tools 协议。"""
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def test_schema_mcp_tools_output_shape(tmp_path: Path) -> None:
	"""mcp-tools 格式应返回实际可调用的工具定义。"""
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "schema", "--format", "mcp-tools"],
	)
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["data"]["format"] == "mcp-tools"
	tools = envelope["data"]["tools"]
	assert isinstance(tools, list)
	assert len(tools) == len(build_tool_catalog())
	for tool in tools:
		assert "name" in tool
		assert "description" in tool
		assert "inputSchema" in tool
		assert tool["inputSchema"]["type"] == "object"
		assert tool["name"].startswith("boss_")


def test_schema_mcp_tools_exposes_chatmsg_raw_option(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "schema", "--format", "mcp-tools"],
	)
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	tool = next(item for item in envelope["data"]["tools"] if item["name"] == "boss_chatmsg")
	properties = tool["inputSchema"]["properties"]
	assert properties["raw"]["type"] == "boolean"
	assert "保真" in properties["raw"]["description"]


def test_all_tool_formats_declare_no_additional_properties(tmp_path: Path) -> None:
	"""三种导出格式都要带上与 call_tool 一致的顶层封闭声明。"""
	runner = CliRunner()
	for output_format in ("openai-tools", "anthropic-tools", "mcp-tools"):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "schema", "--format", output_format],
		)
		assert result.exit_code == 0, result.output
		tools = json.loads(result.output)["data"]["tools"]
		assert len(tools) == len(build_tool_catalog())
		schemas: list[tuple[str, dict]] = []
		for tool in tools:
			if "inputSchema" in tool:
				schemas.append((tool["name"], tool["inputSchema"]))
			elif "input_schema" in tool:
				schemas.append((tool["name"], tool["input_schema"]))
			else:
				schemas.append((tool["function"]["name"], tool["function"]["parameters"]))
		missing = [
			name for name, schema in schemas if schema.get("additionalProperties") is not False
		]
		assert not missing, f"{output_format} 未声明 additionalProperties: false：{missing}"


def test_native_schema_describes_explicit_agent_resume(tmp_path: Path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "schema"])
	assert result.exit_code == 0, result.output
	data = json.loads(result.output)["data"]
	option = data["commands"]["agent"]["options"]["run"]["--resume"]
	assert option["type"] == "bool"
	assert option["default"] is False
	assert "操作者确认官方验证" in option["description"]


def test_all_schema_formats_work_without_optional_mcp_sdk() -> None:
	"""在真实 CLI 入口阻止 SDK 导入，验证纯 CLI 安装仍能导出完整工具目录。"""
	source = dedent("""\
		import importlib.abc
		import json
		import sys
		import tempfile
		from click.testing import CliRunner

		blocked_imports = []
		class WithoutMCP(importlib.abc.MetaPathFinder):
			def find_spec(self, fullname, path=None, target=None):
				if fullname == "mcp" or fullname.startswith("mcp."):
					blocked_imports.append(fullname)
					raise ModuleNotFoundError("MCP SDK deliberately unavailable")

		sys.meta_path.insert(0, WithoutMCP())
		from boss_agent_cli.main import cli

		counts = {}
		with tempfile.TemporaryDirectory(prefix="boss-schema-without-mcp-") as data_dir:
			for output_format in ("native", "openai-tools", "anthropic-tools", "mcp-tools"):
				result = CliRunner().invoke(cli, ["--data-dir", data_dir, "schema", "--format", output_format])
				assert result.exit_code == 0, result.output
				payload = json.loads(result.output)
				assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
				assert payload["ok"] is True and payload["command"] == "schema"
				if output_format == "native":
					counts[output_format] = len(payload["data"]["commands"])
				else:
					counts[output_format] = len(payload["data"]["tools"])
		print(json.dumps({"blocked_imports": blocked_imports, "counts": counts}))
	""")
	result = subprocess.run(
		[sys.executable, "-c", source], capture_output=True, text=True, encoding="utf-8", timeout=60,
	)
	assert result.returncode == 0, result.stderr
	report = json.loads(result.stdout)
	assert report["blocked_imports"] == []
	for output_format in ("openai-tools", "anthropic-tools", "mcp-tools"):
		assert report["counts"][output_format] == len(build_tool_catalog())
