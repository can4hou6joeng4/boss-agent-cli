"""MCP 简历导出 / 导入 / 字段编辑工具（Issue #2991）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import _estimated_requests, _write_interrupt_recovery
from boss_agent_cli.mcp_tools import TOOLS


def _tool(name: str):
	return next(tool for tool in TOOLS if tool.name == name)


def test_resume_export_import_edit_tools_are_registered() -> None:
	names = {tool.name for tool in TOOLS}
	assert {"boss_resume_export", "boss_resume_import", "boss_resume_edit"} <= names


def test_resume_export_schema_declares_formats_and_output() -> None:
	schema = _tool("boss_resume_export").input_schema
	assert schema["required"] == ["name"]
	assert set(schema["properties"]) == {"name", "format", "output_file"}
	assert schema["properties"]["format"]["enum"] == ["pdf", "json", "html"]
	assert schema["properties"]["format"]["default"] == "json"
	assert "浏览器内核" in _tool("boss_resume_export").description


def test_resume_import_schema_defaults_force_to_false() -> None:
	schema = _tool("boss_resume_import").input_schema
	assert schema["required"] == ["file_path"]
	assert schema["properties"]["force"]["default"] is False
	assert "丢弃本地已有编辑" in _tool("boss_resume_import").description


def test_resume_edit_schema_requires_non_empty_value() -> None:
	schema = _tool("boss_resume_edit").input_schema
	assert schema["required"] == ["name", "field", "value"]
	# 必填字符串由 schema 构建流程补 minLength: 1（Issue #1533）；清空字段仍走 CLI。
	assert schema["properties"]["value"]["minLength"] == 1


def test_build_args_for_resume_export() -> None:
	assert _build_args("boss_resume_export", {"name": "源"}) == ["resume", "export", "源"]
	assert _build_args(
		"boss_resume_export", {"name": "源", "format": "json", "output_file": "/tmp/x.json"}
	) == ["resume", "export", "源", "--format", "json", "--output", "/tmp/x.json"]
	# 以 -- 开头的输出路径合并为单个 token，避免被当成开关。
	assert _build_args(
		"boss_resume_export", {"name": "源", "output_file": "--x.json"}
	) == ["resume", "export", "源", "--output=--x.json"]
	# 以连字符开头的简历名放到 -- 之后。
	assert _build_args(
		"boss_resume_export", {"name": "-源", "format": "json"}
	) == ["resume", "export", "--format", "json", "--", "-源"]


def test_build_args_for_resume_import_and_edit() -> None:
	assert _build_args("boss_resume_import", {"file_path": "/tmp/a.json"}) == [
		"resume",
		"import",
		"/tmp/a.json",
	]
	assert _build_args("boss_resume_import", {"file_path": "/tmp/a.json", "force": True}) == [
		"resume",
		"import",
		"/tmp/a.json",
		"--force",
	]
	assert _build_args("boss_resume_import", {"file_path": "-a.json", "force": False}) == [
		"resume",
		"import",
		"--",
		"-a.json",
	]
	assert _build_args(
		"boss_resume_edit", {"name": "源", "field": "title", "value": ""}
	) == ["resume", "edit", "源", "--field", "title", "--value", ""]


def test_resume_export_import_edit_do_not_estimate_platform_requests() -> None:
	for argv in (
		("resume", "export", "源"),
		("resume", "import", "/tmp/a.json"),
		("resume", "edit", "源", "--field", "title", "--value", "x"),
	):
		assert _estimated_requests(argv) is None


def test_resume_export_import_edit_interrupt_recovery_stays_local() -> None:
	export_message = _write_interrupt_recovery(("resume", "export", "源", "--format", "json"))
	assert "导出已中断" in export_message
	assert "不产生平台写入" in export_message
	import_message = _write_interrupt_recovery(("resume", "import", "/tmp/a.json"))
	assert "boss_resume_list" in import_message
	assert "不产生平台写入" in import_message
	edit_message = _write_interrupt_recovery(("resume", "edit", "源", "--field", "title", "--value", "x"))
	assert "boss_resume_show" in edit_message
	assert "不产生平台写入" in edit_message


def test_resume_export_import_edit_round_trip_offline(tmp_path) -> None:
	"""init → export → import → edit → show 全流程不访问平台。"""
	runner = CliRunner()
	prefix = ["--json", "--data-dir", str(tmp_path)]
	export_path = tmp_path / "exported.json"

	created = runner.invoke(cli, [*prefix, *_build_args("boss_resume_init", {"name": "源简历"})])
	assert created.exit_code == 0, created.output

	exported = runner.invoke(
		cli,
		[*prefix, *_build_args("boss_resume_export", {"name": "源简历", "format": "json", "output_file": str(export_path)})],
	)
	assert exported.exit_code == 0, exported.output
	assert export_path.is_file()
	payload = json.loads(export_path.read_text(encoding="utf-8"))
	# 导出的 JSON 是带 version / data / metadata 的简历包络；import 读取 data.name。
	payload["data"]["name"] = "导入副本"
	import_path = tmp_path / "import.json"
	import_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

	imported = runner.invoke(
		cli, [*prefix, *_build_args("boss_resume_import", {"file_path": str(import_path)})]
	)
	assert imported.exit_code == 0, imported.output
	assert json.loads(imported.stdout)["data"]["name"] == "导入副本"

	edited = runner.invoke(
		cli,
		[*prefix, *_build_args("boss_resume_edit", {"name": "导入副本", "field": "title", "value": "高级后端"})],
	)
	assert edited.exit_code == 0, edited.output

	shown = runner.invoke(cli, [*prefix, *_build_args("boss_resume_show", {"name": "导入副本"})])
	assert shown.exit_code == 0, shown.output
	assert json.loads(shown.stdout)["data"]["title"] == "高级后端"

	# 同名导入默认被拒绝；force=true 覆盖且不要求先删除。
	repeated = runner.invoke(
		cli, [*prefix, *_build_args("boss_resume_import", {"file_path": str(import_path)})]
	)
	assert repeated.exit_code == 1
	assert json.loads(repeated.stdout)["error"]["code"] == "RESUME_ALREADY_EXISTS"
	forced = runner.invoke(
		cli,
		[*prefix, *_build_args("boss_resume_import", {"file_path": str(import_path), "force": True})],
	)
	assert forced.exit_code == 0, forced.output
