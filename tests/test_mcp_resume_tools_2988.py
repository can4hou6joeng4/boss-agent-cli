"""MCP 简历管理工具：clone / diff / link / applications（Issue #2988）。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_READ_ONLY_TOOL_NAMES,
	_estimated_requests,
	_write_interrupt_recovery,
)
from boss_agent_cli.mcp_tools import TOOLS


def _tool(name: str):
	return next(tool for tool in TOOLS if tool.name == name)


def test_resume_management_tools_are_registered() -> None:
	names = {tool.name for tool in TOOLS}
	assert {
		"boss_resume_clone",
		"boss_resume_diff",
		"boss_resume_link",
		"boss_resume_applications",
	} <= names


def test_resume_clone_schema_requires_both_names() -> None:
	schema = _tool("boss_resume_clone").input_schema
	assert set(schema["properties"]) == {"name", "new_name"}
	assert schema["required"] == ["name", "new_name"]
	assert schema["properties"]["new_name"]["minLength"] == 1
	assert schema["additionalProperties"] is False


def test_resume_diff_schema_is_read_only_pair() -> None:
	schema = _tool("boss_resume_diff").input_schema
	assert schema["required"] == ["name1", "name2"]
	assert "只读" in _tool("boss_resume_diff").description


def test_resume_link_schema_requires_job_identity() -> None:
	schema = _tool("boss_resume_link").input_schema
	assert schema["required"] == ["name", "security_id", "job_id"]
	assert set(schema["properties"]) == {"name", "security_id", "job_id", "title", "company"}
	assert "不产生平台请求" in _tool("boss_resume_link").description


def test_resume_applications_schema_requires_name() -> None:
	schema = _tool("boss_resume_applications").input_schema
	assert schema["required"] == ["name"]
	assert schema["properties"]["name"]["minLength"] == 1


def test_build_args_for_resume_management_tools() -> None:
	assert _build_args("boss_resume_clone", {"name": "源", "new_name": "副本"}) == [
		"resume",
		"clone",
		"源",
		"副本",
	]
	assert _build_args("boss_resume_diff", {"name1": "a", "name2": "b"}) == ["resume", "diff", "a", "b"]
	assert _build_args(
		"boss_resume_link",
		{"name": "简历", "security_id": "sid", "job_id": "jid", "title": "后端", "company": "示例"},
	) == ["resume", "link", "简历", "sid", "jid", "--title", "后端", "--company", "示例"]
	assert _build_args("boss_resume_applications", {"name": "简历"}) == [
		"resume",
		"applications",
		"简历",
	]


def test_build_args_protects_hyphen_leading_positionals() -> None:
	clone_args = _build_args("boss_resume_clone", {"name": "-源", "new_name": "--副本"})
	assert clone_args == ["resume", "clone", "--", "-源", "--副本"]
	link_args = _build_args(
		"boss_resume_link", {"name": "-简历", "security_id": "sid", "job_id": "jid", "title": "后端"}
	)
	assert link_args == ["resume", "link", "--title", "后端", "--", "-简历", "sid", "jid"]
	applications_args = _build_args("boss_resume_applications", {"name": "-简历"})
	assert applications_args == ["resume", "applications", "--", "-简历"]


def test_resume_management_tools_do_not_estimate_platform_requests() -> None:
	# 四个工具都只读 / 写本机，CLI 不发出平台请求，保持宿主默认超时。
	for argv in (
		("resume", "clone", "源", "副本"),
		("resume", "diff", "a", "b"),
		("resume", "link", "简历", "sid", "jid"),
		("resume", "applications", "简历"),
	):
		assert _estimated_requests(argv) is None


def test_resume_read_tools_are_classified_read_only() -> None:
	assert "boss_resume_diff" in _READ_ONLY_TOOL_NAMES
	assert "boss_resume_applications" in _READ_ONLY_TOOL_NAMES
	assert "boss_resume_clone" not in _READ_ONLY_TOOL_NAMES
	assert "boss_resume_link" not in _READ_ONLY_TOOL_NAMES


def test_resume_write_interrupt_recovery_stays_local() -> None:
	clone_message = _write_interrupt_recovery(("resume", "clone", "源", "副本"))
	assert "boss_resume_list" in clone_message
	assert "不产生平台写入" in clone_message
	link_message = _write_interrupt_recovery(("resume", "link", "简历", "sid", "jid"))
	assert "boss_resume_applications" in link_message
	assert "不产生平台写入" in link_message


def test_resume_management_round_trip_offline(tmp_path) -> None:
	"""init → clone → diff → link → applications 全流程不访问平台。"""
	runner = CliRunner()
	prefix = ["--json", "--data-dir", str(tmp_path)]

	created = runner.invoke(cli, [*prefix, *_build_args("boss_resume_init", {"name": "源简历"})])
	assert created.exit_code == 0, created.output

	cloned = runner.invoke(
		cli, [*prefix, *_build_args("boss_resume_clone", {"name": "源简历", "new_name": "副本"})]
	)
	assert cloned.exit_code == 0, cloned.output
	assert json.loads(cloned.stdout)["ok"] is True

	listed = runner.invoke(cli, [*prefix, *_build_args("boss_resume_list", {})])
	assert {item["name"] for item in json.loads(listed.stdout)["data"]} == {"源简历", "副本"}

	diffed = runner.invoke(
		cli, [*prefix, *_build_args("boss_resume_diff", {"name1": "源简历", "name2": "副本"})]
	)
	assert diffed.exit_code == 0, diffed.output
	fields = {row["field"] for row in json.loads(diffed.stdout)["data"]["diffs"]}
	# 副本只有托管元数据与名称不同，内容字段不应出现差异。
	assert "name" in fields
	assert fields <= {"name", "created_at", "updated_at"}

	linked = runner.invoke(
		cli,
		[
			*prefix,
			*_build_args(
				"boss_resume_link",
				{
					"name": "源简历",
					"security_id": "demo-security",
					"job_id": "demo-job",
					"title": "后端工程师",
					"company": "示例公司",
				},
			),
		],
	)
	assert linked.exit_code == 0, linked.output

	applications = runner.invoke(
		cli, [*prefix, *_build_args("boss_resume_applications", {"name": "源简历"})]
	)
	assert applications.exit_code == 0, applications.output
	rows = json.loads(applications.stdout)["data"]
	assert [(row["security_id"], row["job_id"]) for row in rows] == [("demo-security", "demo-job")]
	assert rows[0]["job_title"] == "后端工程师"
	assert rows[0]["company"] == "示例公司"
