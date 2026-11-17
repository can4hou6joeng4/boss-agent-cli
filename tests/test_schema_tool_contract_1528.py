"""工具契约与 native schema 的一致性（Issue #1528–#1536）。

`boss schema` 的 native 输出、`--format mcp-tools` 导出与 MCP `tools/list` 是同一份
契约的三个入口。缺少枚举、默认值、条件必填或 minLength 时，客户端会先认可一个
CLI 必然拒绝的调用；只读 native 与只读工具的 Agent 也会得到不同结论。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.capabilities import ALL_CAPABILITIES
from boss_agent_cli.commands.mark import _LABEL_MAP
from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _tools() -> dict:
	return {tool.name: tool for tool in build_tool_catalog()}


def _native() -> dict:
	result = CliRunner().invoke(cli, ["schema"])
	assert result.exit_code == 0, result.output
	return json.loads(result.output)["data"]


# ── #1528 / #1530：枚举取值域 ────────────────────────────────────────


def test_filter_enums_match_click_choices():
	"""batch-greet / preset add / watch add 的筛选键是 Click Choice，工具侧必须同域。"""
	tools = _tools()
	native_search = SCHEMA_DATA["commands"]["search"]["options"]
	expected = {
		"industry": native_search["--industry"]["choices"],
		"scale": native_search["--scale"]["choices"],
		"stage": native_search["--stage"]["choices"],
		"job_type": native_search["--job-type"]["choices"],
	}
	for tool_name in ("boss_batch_greet", "boss_preset_add", "boss_watch_add"):
		properties = tools[tool_name].input_schema["properties"]
		for key, choices in expected.items():
			assert properties[key]["enum"] == choices, (tool_name, key)


def test_mark_tag_enum_matches_label_map():
	"""boss_mark.tag 的枚举必须与 mark 命令的 _LABEL_MAP 逐值相等。"""
	tag = _tools()["boss_mark"].input_schema["properties"]["tag"]
	assert set(tag["enum"]) == set(_LABEL_MAP) | {str(value) for value in _LABEL_MAP.values()}
	assert tag["minLength"] == 1


def test_platforms_capability_choices_cover_every_capability():
	"""schema 的 --capability choices 与实现接受的能力集合相等（Issue #1530）。"""
	choices = SCHEMA_DATA["commands"]["platforms"]["options"]["--capability"]["choices"]

	assert choices == list(ALL_CAPABILITIES)
	for capability in ("show", "history", "interviews"):
		assert capability in choices
		assert capability in json.dumps(_native(), ensure_ascii=False)


# ── #1533：required 字符串边界 ──────────────────────────────────────


def test_required_strings_carry_min_length():
	"""required 字符串统一声明 minLength，唯一例外是 hr greet 的 lid。"""
	tools = _tools()
	missing = []
	for name, tool in tools.items():
		properties = tool.input_schema.get("properties") or {}
		for key in tool.input_schema.get("required") or []:
			prop = properties.get(key) or {}
			if prop.get("type") != "string":
				continue
			if name == "boss_hr_greet" and key == "lid":
				continue
			if prop.get("minLength") != 1:
				missing.append((name, key))
	assert missing == []


def test_hr_greet_lid_keeps_empty_string_allowed():
	"""lid 是「required 但平台可能给空值」的上下文字段，不能被 minLength 挡掉。"""
	lid = _tools()["boss_hr_greet"].input_schema["properties"]["lid"]
	assert "minLength" not in lid


# ── #1534：默认值 ──────────────────────────────────────────────────


def test_tool_defaults_match_cli_defaults():
	tools = _tools()
	assert tools["boss_agent_review_reject"].input_schema["properties"]["reason"]["default"] == "human-rejected"
	assert tools["boss_ai_cover_letter"].input_schema["properties"]["tone"]["default"] == "简洁专业"
	assert tools["boss_ai_cover_letter"].input_schema["properties"]["lang"]["default"] == "zh"
	assert tools["boss_favorites_list"].input_schema["properties"]["page"]["default"] == 1
	assert tools["boss_wizard"].input_schema["properties"]["max_retries"]["default"] == 0


def test_batch_greet_limit_keeps_documented_mcp_default():
	"""batch-greet 的 MCP 默认 5 与 native 的 10 不同，是有意的限制，描述里要写。"""
	tools = _tools()
	limit = tools["boss_batch_greet"].input_schema["properties"]["limit"]
	assert limit["default"] == 5
	assert SCHEMA_DATA["commands"]["batch-greet"]["options"]["--count"]["default"] == 10
	assert "默认最多 5 个" in tools["boss_batch_greet"].description


# ── #1531：dry_run 的跨入口差异 ─────────────────────────────────────


def test_agent_run_documents_mcp_dry_run_default():
	tools = _tools()
	agent_run = tools["boss_agent_run"]
	assert agent_run.input_schema["properties"]["dry_run"]["default"] is True
	assert "MCP 缺省 dry_run=true" in agent_run.description
	# native 描述同样要写清 MCP 缺省演练，避免只读 native 的 Agent 得出相反结论。
	native_dry_run = SCHEMA_DATA["commands"]["agent"]["options"]["run"]["--dry-run"]["description"]
	assert "MCP" in native_dry_run and "true" in native_dry_run


# ── #1532：条件必填与互斥 ──────────────────────────────────────────


def test_export_requires_query_or_url():
	schema = _tools()["boss_export"].input_schema
	assert {"required": ["query"]} in schema["anyOf"]
	assert {"required": ["url"]} in schema["anyOf"]


def test_ai_jd_tools_declare_mutual_exclusion():
	for name in ("boss_ai_cover_letter", "boss_ai_resume_optimize"):
		schema = _tools()[name].input_schema
		assert schema["not"] == {"required": ["jd_text", "job_id"]}, name


def test_hr_jobs_requires_job_id_for_online_offline():
	schema = _tools()["boss_hr_jobs"].input_schema
	assert schema["if"] == {"properties": {"action": {"enum": ["online", "offline"]}}, "required": ["action"]}
	assert schema["then"] == {"required": ["job_id"]}


def test_config_requires_key_for_keyed_actions():
	schema = _tools()["boss_config"].input_schema
	assert schema["if"]["properties"]["action"]["enum"] == ["get", "set", "reset"]
	assert schema["then"] == {"required": ["key"]}
	assert schema["allOf"][0]["then"] == {"required": ["key", "value"]}


def test_wizard_requires_run_id_for_control_actions():
	schema = _tools()["boss_wizard"].input_schema
	assert schema["if"]["properties"]["action"]["enum"] == ["resume", "status", "stop"]
	assert schema["then"] == {"required": ["run_id"]}


def test_ai_jd_mutual_exclusion_rejects_both_sources(tmp_path):
	"""CLI 层同样拒绝两个 JD 来源：不再静默用缓存 JD 覆盖显式 JD（Issue #1529）。"""
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "ai", "cover-letter", "简历", "--jd", "职位描述", "--job-id", "job-1"],
	)
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "二选一" in payload["error"]["message"]


# ── #1529 的反面：只给一个来源时仍走原路径 ──────────────────────────


def test_ai_jd_single_source_still_reaches_ai_configuration(tmp_path):
	"""互斥校验只在两个来源同时出现时生效，单个来源不改变原有分支顺序。"""
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "ai", "resume-optimize", "简历", "--jd", "职位描述"],
	)
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	# 未配置模型时落回既有配置提示；不应被互斥校验误拦。
	assert payload["error"]["code"] != "INVALID_PARAM" or "二选一" not in payload["error"]["message"]


# ── #1536：native 内嵌 crawl 工具与导出逐字段一致 ────────────────────


def test_native_embedded_crawl_tools_match_exported_tools():
	native = _native()
	result = CliRunner().invoke(cli, ["schema", "--format", "mcp-tools"])
	assert result.exit_code == 0, result.output
	exported = {tool["name"]: tool for tool in json.loads(result.output)["data"]["tools"]}

	embedded = native["commands"]["crawl"]["mcp_tools"]
	assert [tool["name"] for tool in embedded] == ["boss_crawl_status", "boss_crawl_results", "boss_crawl_shortlist"]
	for tool in embedded:
		assert tool == exported[tool["name"]], tool["name"]
		assert tool["inputSchema"]["additionalProperties"] is False
		assert tool["description"].count("可用性:") == 1


# ── #1535：agent 的 recruiter_platforms 语义 ────────────────────────


def test_agent_availability_explains_adapter_key_semantics():
	"""agent 的 recruiter_platforms 是适配器键（= --platform 取值），不是注册表名。"""
	native = _native()
	agent = native["commands"]["agent"]["availability"]
	assert agent["recruiter_platforms"] == ["zhipin"]
	assert "适配器键" in agent["note"]
	# hr 仍是招聘者注册表名：两种语义各自被写明，不再混用同一字段解释。
	assert "zhipin-recruiter" in native["commands"]["hr"]["availability"]["recruiter_platforms"]


# ── boss_schema 工具 ───────────────────────────────────────────────


def test_boss_schema_tool_is_read_only_and_reachable():
	from boss_agent_cli import mcp_server
	from boss_agent_cli.mcp_args import _build_args
	from boss_agent_cli.mcp_tools import TOOLS

	assert "boss_schema" in {tool.name for tool in TOOLS}
	assert "boss_schema" in mcp_server._READ_ONLY_TOOL_NAMES
	assert _build_args("boss_schema", {}) == ["schema"]
	assert _build_args("boss_schema", {"format": "mcp-tools"}) == ["schema", "--format", "mcp-tools"]
