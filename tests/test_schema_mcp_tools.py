"""验证 boss schema --format mcp-tools 输出符合 MCP tools 协议。"""
import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_schema_mcp_tools_output_shape(tmp_path: Path) -> None:
	"""mcp-tools 格式应返回每个命令一个 MCP Tool 定义。"""
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
	assert len(tools) > 20  # 至少包含主要命令
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
