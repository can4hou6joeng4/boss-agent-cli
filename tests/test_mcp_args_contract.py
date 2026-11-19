"""MCP 工具参数映射契约：每个工具生成的 CLI argv 必须能被 Click 解析。

`_build_args` 是 MCP 与 CLI 之间唯一的手写映射层：参数名与选项名不一致
（`tag` → `--label`、`limit` → `--count`）、枚举值拼错、分支漏写 `--` 前缀时，
工具目录与 schema 都看不出问题，调用方只会在真实 MCP 会话里拿到 Click 的
用法错误。这里按每个工具的 `inputSchema` 自动生成样例参数，解析到目标
命令后跑一次 Argument/Options 解析（不执行命令回调），把映射错误挡在本地门禁。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import click
from click.testing import CliRunner
import pytest

from boss_agent_cli.commands import agent as agent_cmd_module
from boss_agent_cli.commands import ai_cmd as ai_cmd_module
from boss_agent_cli.commands import chat as chat_cmd_module
from boss_agent_cli.commands import chat_summary as chat_summary_cmd_module
from boss_agent_cli.commands import chatmsg as chatmsg_cmd_module
from boss_agent_cli.commands import clean as clean_cmd_module
from boss_agent_cli.commands import detail as detail_cmd_module
from boss_agent_cli.commands import digest as digest_cmd_module
from boss_agent_cli.commands import export as export_cmd_module
from boss_agent_cli.commands import greet as greet_cmd_module
from boss_agent_cli.commands import mark as mark_cmd_module
from boss_agent_cli.commands import me as me_cmd_module
from boss_agent_cli.commands import pipeline as pipeline_cmd_module
from boss_agent_cli.commands import stats as stats_cmd_module
from boss_agent_cli.commands import preset as preset_cmd_module
from boss_agent_cli.commands import recommend as recommend_cmd_module
from boss_agent_cli.commands import search as search_cmd_module
from boss_agent_cli.commands import shortlist as shortlist_cmd_module
from boss_agent_cli.commands import status as status_cmd_module
from boss_agent_cli.commands import watch as watch_cmd_module
from boss_agent_cli.commands.recruiter import (
	accept_resume as recruiter_accept_resume,
	applications as recruiter_applications,
	candidates as recruiter_candidates,
	chat as recruiter_chat,
	download_resume as recruiter_download_resume,
	location as recruiter_location,
	recommendations as recruiter_recommendations,
	reply as recruiter_reply,
	request_resume as recruiter_request_resume,
	whoami as recruiter_whoami,
)
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_tools import TOOLS


# 这些筛选在部分命令里是 Click Choice（如 batch-greet），schema 只声明 string；
# 样例值必须落在可选值集合内，解析测试才能验证映射而不是被 Choice 拦下。
_FILTER_SAMPLE_VALUES = {
	"industry": "电子商务",
	"scale": "100-499人",
	"stage": "A轮",
	"job_type": "全职",
}


def _sample_value(prop: dict[str, Any], name: str = "") -> Any:
	"""按 JSON Schema 生成一个合法的样例值；枚举取第一个，与 CLI Choice 对齐。"""
	if "enum" in prop:
		return prop["enum"][0]
	if prop.get("type") == "array":
		return [_sample_value(prop.get("items") or {})]
	if name in _FILTER_SAMPLE_VALUES:
		return _FILTER_SAMPLE_VALUES[name]
	return {
		"string": "sample",
		"integer": 1,
		"number": 1.0,
		"boolean": False,
		"object": {},
	}.get(prop.get("type"), "sample")


#: action 条件键矩阵（Issue #1493）：JSON Schema 无法表达「某键只在某个 action
#: 下生效」，按全部 properties 生成的样例必须按 action 裁剪，否则会被映射层的
#: 条件校验拒绝。与 mcp_args 的实现独立维护，避免测试跟着实现一起漂移。
_ACTION_SCOPED_KEYS: dict[str, dict[str, set[str]]] = {
	"boss_wizard": {
		"run": {"role", "platform", "goal", "inputs", "requested_steps"},
		"resume": {"run_id"},
		"status": {"run_id"},
		"stop": {"run_id"},
	},
	"boss_config": {
		"list": set(),
		"get": {"key"},
		"set": {"key", "value"},
		"reset": {"key"},
	},
	"boss_hr_jobs": {
		"list": {"page"},
		"online": {"job_id"},
		"offline": {"job_id"},
	},
}
_ACTION_SHARED_KEYS = {"action", "timeout", "max_retries"}


def _sample_arguments(tool, *, action: str | None = None) -> dict[str, Any]:
	"""按所选 action 裁剪样例参数：只保留该 action 实际使用的键。"""
	properties = tool.input_schema.get("properties") or {}
	arguments = {key: _sample_value(prop, key) for key, prop in properties.items()}
	if action is not None:
		arguments["action"] = action
	scoped = _ACTION_SCOPED_KEYS.get(tool.name)
	effective = arguments.get("action")
	if scoped is not None and isinstance(effective, str) and effective in scoped:
		arguments = {
			key: value
			for key, value in arguments.items()
			if key in _ACTION_SHARED_KEYS or key in scoped[effective]
		}
	return arguments


def _resolve_command(argv: list[str]) -> tuple[click.Command, list[str]]:
	"""把 argv 拆成「命令路径」与「参数」：沿 Group 的子命令往下钻。"""
	command: click.Command = cli
	rest = list(argv)
	while isinstance(command, click.Group):
		assert rest, f"命令路径不完整：{argv}"
		child = command.commands.get(rest[0])
		assert child is not None, f"{argv} 中的 {rest[0]!r} 不是已注册命令"
		command = child
		rest = rest[1:]
	return command, rest


@pytest.mark.parametrize("tool", TOOLS, ids=[tool.name for tool in TOOLS])
def test_tool_arguments_parse_with_cli(tool) -> None:
	arguments = _sample_arguments(tool)

	argv = _build_args(tool.name, arguments)

	command, rest = _resolve_command(argv)
	# make_context 只做参数解析与类型校验，不执行命令回调。
	ctx = command.make_context(command.name, rest)
	ctx.close()


@pytest.mark.parametrize("tool", TOOLS, ids=[tool.name for tool in TOOLS])
@pytest.mark.parametrize("literal", ["-synthetic", "--help", "--"])
def test_tool_positionals_keep_option_like_text_literal(tool, literal: str) -> None:
	"""工具字符串到达 Click 位置参数时必须保持字面值（Issue #1102）。"""
	properties = tool.input_schema.get("properties") or {}
	text_values = {
		key: f"sample_{key}"
		for key, prop in properties.items()
		if prop.get("type") == "string" and "enum" not in prop and key not in _FILTER_SAMPLE_VALUES
	}
	# config 的 get / set 与 hr jobs 的 online / offline 才有位置参数，不能只测 list。
	actions = properties.get("action", {}).get("enum", [None])
	for action in actions:
		case = _sample_arguments(tool, action=action)
		case.update({key: text_values[key] for key in case if key in text_values})
		command, rest = _resolve_command(_build_args(tool.name, case))
		with command.make_context(command.name, rest) as baseline:
			expected_params = dict(baseline.params)
		for parameter in command.params:
			if not isinstance(parameter, click.Argument):
				continue
			key = next((key for key, value in text_values.items() if expected_params[parameter.name] == value), None)
			if key is None:
				continue
			argv = _build_args(tool.name, {**case, key: literal})
			parsed_command, rest = _resolve_command(argv)
			try:
				with parsed_command.make_context(parsed_command.name, rest) as parsed:
					assert parsed.params == {**expected_params, parameter.name: literal}, argv
			except (click.ClickException, click.exceptions.Exit) as exc:
				pytest.fail(f"{tool.name}.{key} 未按字面传入：{argv}；{exc}")


@pytest.mark.parametrize("name", ["-synthetic", "--help", "--"])
def test_mcp_preset_option_like_names_reach_cli_callbacks(name, tmp_path, monkeypatch) -> None:
	"""通过真实 CLI 信封验证预设名、关键词和筛选项，缓存保持 mock。"""
	cache = MagicMock()
	cache.__enter__.return_value = cache
	cache.delete_saved_search.return_value = True
	monkeypatch.setattr(preset_cmd_module, "CacheStore", lambda *_: cache)
	runner = CliRunner()
	prefix = ["--json", "--data-dir", str(tmp_path)]
	arguments = {"name": name, "query": "-synthetic", "city": "北京"}
	added = runner.invoke(cli, [*prefix, *_build_args("boss_preset_add", arguments)])
	assert added.exit_code == 0, added.output
	payload = json.loads(added.output)
	assert payload["ok"] is True
	assert payload["data"]["name"] == name
	cache.save_saved_search.assert_called_once()
	saved_name, saved_params = cache.save_saved_search.call_args.args
	assert saved_name == name
	assert saved_params["query"] == "-synthetic"
	assert saved_params["city"] == "北京"

	invalid = runner.invoke(cli, [*prefix, *_build_args("boss_preset_add", {**arguments, "query": ""})])
	assert invalid.exit_code == 1, invalid.output
	assert json.loads(invalid.output)["error"]["code"] == "INVALID_PARAM"
	cache.save_saved_search.assert_called_once()

	removed = runner.invoke(cli, [*prefix, *_build_args("boss_preset_remove", {"name": name})])
	assert removed.exit_code == 0, removed.output
	assert json.loads(removed.output)["data"]["removed"] is True
	cache.delete_saved_search.assert_called_once_with(name)


# 单工具 ↔ 单命令的 hr 工具：inputSchema 的属性集合必须与 Click 参数逐一相等。
# 新增 CLI 选项却忘了同步 schema（或反之）时，MCP 调用方要么看不到可用参数，
# 要么声明了 CLI 不认识的参数。`hr chat --summary-limit` / `hr last-messages --limit`
# 就是因此长期不可达。
_HR_SINGLE_COMMAND_TOOLS = {
	"boss_hr_whoami": recruiter_whoami.whoami_cmd,
	"boss_hr_applications": recruiter_applications.applications_cmd,
	"boss_hr_candidates": recruiter_candidates.candidates_cmd,
	"boss_hr_recommendations": recruiter_recommendations.recommendations_cmd,
	"boss_hr_greet": recruiter_recommendations.greet_cmd,
	"boss_hr_location": recruiter_location.location_cmd,
	"boss_hr_chat": recruiter_chat.recruiter_chat_cmd,
	"boss_hr_chatmsg": recruiter_chat.recruiter_chatmsg_cmd,
	"boss_hr_last_messages": recruiter_chat.recruiter_last_messages_cmd,
	"boss_hr_reply": recruiter_reply.reply_cmd,
	"boss_hr_request_resume": recruiter_request_resume.request_resume_cmd,
	"boss_hr_accept_resume": recruiter_accept_resume.accept_resume_cmd,
	"boss_hr_download_resume": recruiter_download_resume.download_resume_cmd,
}

# resume / exchange 共用一条 CLI 命令却被拆成两个工具；jobs 用 action 合并
# list / online / offline，detail 用位置参数。结构不同，不做集合相等比较。
_HR_STRUCTURED_TOOLS = {
	"boss_hr_resume",
	"boss_hr_exchange",
	"boss_hr_jobs",
	"boss_hr_jobs_detail",
}


def test_hr_completeness_table_covers_every_hr_tool() -> None:
	hr_tools = {tool.name for tool in TOOLS if tool.name.startswith("boss_hr_")}
	assert set(_HR_SINGLE_COMMAND_TOOLS) | _HR_STRUCTURED_TOOLS == hr_tools


@pytest.mark.parametrize(
	("tool_name", "command"),
	list(_HR_SINGLE_COMMAND_TOOLS.items()),
	ids=list(_HR_SINGLE_COMMAND_TOOLS),
)
def test_hr_tool_schema_lists_every_cli_parameter(tool_name: str, command: click.Command) -> None:
	schema = next(tool for tool in TOOLS if tool.name == tool_name).input_schema
	properties = set((schema.get("properties") or {}).keys())
	parameters = {param.name for param in command.params if param.name}
	missing_in_schema = sorted(parameters - properties)
	extra_in_schema = sorted(properties - parameters)
	assert not missing_in_schema and not extra_in_schema, (
		f"{tool_name} 的 inputSchema 与 CLI 参数不一致："
		f"CLI 有但 schema 未声明 {missing_in_schema}；schema 声明但 CLI 没有 {extra_in_schema}"
	)


# 核心读取工具同样要求 schema 与 CLI 参数对齐；CLI 参数名与 schema 不同名时
# 显式登记别名，确实不暴露的参数写明原因，避免新增筛选条件只加在 CLI 里。
_CORE_SINGLE_COMMAND_TOOLS = {
	"boss_search": (
		search_cmd_module.search_cmd,
		{"sort_mode": "sort", "search_url": "url"},
		# query / url / preset 由 anyOf 保证至少提供一个（Issue #1648）。
		set(),
	),
	"boss_recommend": (recommend_cmd_module.recommend_cmd, {}, set()),
	"boss_batch_greet": (greet_cmd_module.batch_greet_cmd, {"count": "limit"}, set()),
}


def _assert_schema_matches_command(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	schema = next(tool for tool in TOOLS if tool.name == tool_name).input_schema
	properties = set((schema.get("properties") or {}).keys())
	parameters = {
		aliases.get(param.name, param.name)
		for param in command.params
		if param.name and param.name not in hidden
	}
	missing_in_schema = sorted(parameters - properties)
	extra_in_schema = sorted(properties - parameters)
	assert not missing_in_schema and not extra_in_schema, (
		f"{tool_name} 的 inputSchema 与 CLI 参数不一致："
		f"CLI 有但 schema 未声明 {missing_in_schema}；schema 声明但 CLI 没有 {extra_in_schema}"
	)


@pytest.mark.parametrize(
	("tool_name", "command", "aliases", "hidden"),
	[(tool_name, *spec) for tool_name, spec in _CORE_SINGLE_COMMAND_TOOLS.items()],
	ids=list(_CORE_SINGLE_COMMAND_TOOLS),
)
def test_core_tool_schema_lists_every_cli_parameter(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	_assert_schema_matches_command(tool_name, command, aliases, hidden)


# 本地搜索工具（preset / watch / shortlist）之前只暴露了最小参数集：筛选条件
# 只能写进 CLI 预设/监控，MCP 调用方看不到；候选池元数据也只能留空。这里用
# 同一套集合相等断言把三个工具的参数面钉住。`shortlist annotate` 的 CLI 选项
# 可重复（`--add-tag`），MCP 侧用数组，登记别名。
_LOCAL_SINGLE_COMMAND_TOOLS = {
	"boss_preset_add": (preset_cmd_module.preset_add_cmd, {}, set()),
	"boss_watch_add": (watch_cmd_module.watch_add_cmd, {}, set()),
	"boss_shortlist_add": (shortlist_cmd_module.shortlist_add_cmd, {}, set()),
	"boss_shortlist_annotate": (
		shortlist_cmd_module.shortlist_annotate_cmd,
		{"add_tag": "add_tags", "remove_tag": "remove_tags"},
		set(),
	),
	"boss_shortlist_compare": (shortlist_cmd_module.shortlist_compare_cmd, {}, set()),
}


@pytest.mark.parametrize(
	("tool_name", "command", "aliases", "hidden"),
	[(tool_name, *spec) for tool_name, spec in _LOCAL_SINGLE_COMMAND_TOOLS.items()],
	ids=list(_LOCAL_SINGLE_COMMAND_TOOLS),
)
def test_local_tool_schema_lists_every_cli_parameter(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	_assert_schema_matches_command(tool_name, command, aliases, hidden)


# 只读查询工具的参数面同样要钉住：`status --live`、`me --deliver-page`、
# `chat-summary --page/--count`、`chat --export/-o` 都是 CLI 早已支持却长期
# 没进 MCP schema 的旋钮。`chat` / `chatmsg` 与 CLI 参数名不同，登记别名；
# `pipeline` / `follow-up` 的 `--now-ts-ms` 是测试钩子，`doctor` 的在线探测
# 与浏览器 Cookie 提取属于真人确认的敏感操作，均显式不暴露。
_READ_SINGLE_COMMAND_TOOLS = {
	"boss_status": (status_cmd_module.status_cmd, {}, set()),
	"boss_detail": (detail_cmd_module.detail_cmd, {}, set()),
	"boss_chat": (
		chat_cmd_module.chat_cmd,
		{"export_fmt": "format", "output_path": "output_file"},
		set(),
	),
	"boss_me": (me_cmd_module.me_cmd, {}, set()),
	"boss_chat_summary": (chat_summary_cmd_module.chat_summary_cmd, {}, set()),
	"boss_pipeline": (pipeline_cmd_module.pipeline_cmd, {}, {"now_ts_ms"}),
	"boss_follow_up": (pipeline_cmd_module.follow_up_cmd, {}, {"now_ts_ms"}),
}


@pytest.mark.parametrize(
	("tool_name", "command", "aliases", "hidden"),
	[(tool_name, *spec) for tool_name, spec in _READ_SINGLE_COMMAND_TOOLS.items()],
	ids=list(_READ_SINGLE_COMMAND_TOOLS),
)
def test_read_tool_schema_lists_every_cli_parameter(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	_assert_schema_matches_command(tool_name, command, aliases, hidden)


# 本地维护与自动化工具：`clean` 只暴露保留天数，`--privacy`（清登录会话/简历）
# 与 `--risk-lock`（清风控锁）必须由真人判断，不进 MCP；`agent train` 的
# `--live` 会绕过默认演练直接执行动作，同样不暴露。把这些「有意不暴露」
# 写进守卫，未来新增参数时不会靠删字段悄悄绕过。
_MAINTENANCE_SINGLE_COMMAND_TOOLS = {
	"boss_clean": (clean_cmd_module.clean_cmd, {"clean_all": "all"}, {"privacy", "risk_lock"}),
	"boss_agent_run": (agent_cmd_module.run_cmd, {}, set()),
	"boss_agent_train": (agent_cmd_module.train_cmd, {}, {"dry_run", "live"}),
}


@pytest.mark.parametrize(
	("tool_name", "command", "aliases", "hidden"),
	[(tool_name, *spec) for tool_name, spec in _MAINTENANCE_SINGLE_COMMAND_TOOLS.items()],
	ids=list(_MAINTENANCE_SINGLE_COMMAND_TOOLS),
)
def test_maintenance_tool_schema_lists_every_cli_parameter(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	_assert_schema_matches_command(tool_name, command, aliases, hidden)


# MCP 侧为了可读性使用与 Click 参数不同的名字（`format` / `output_file` /
# `raw` / `tag` / `resume`），这些别名过去只写在 `_build_args` 里，schema
# 与 CLI 任何一侧增删参数都不会被发现。这里逐项登记，锁定映射后的集合相等。
_ALIASED_SINGLE_COMMAND_TOOLS = {
	"boss_chatmsg": (chatmsg_cmd_module.chatmsg_cmd, {"show_raw": "raw"}, set()),
	"boss_export": (
		export_cmd_module.export_cmd,
		{"fmt": "format", "output": "output_file", "search_url": "url"},
		set(),
	),
	"boss_digest": (
		digest_cmd_module.digest_cmd,
		{"output_format": "format", "output_path": "output"},
		{"now_ts_ms"},
	),
	"boss_stats": (
		stats_cmd_module.stats_cmd,
		{"output_format": "format", "output_path": "output_file"},
		set(),
	),
	"boss_ai_fit": (ai_cmd_module.ai_fit_cmd, {"resume_name": "resume"}, set()),
	"boss_mark": (mark_cmd_module.mark_cmd, {"label": "tag"}, set()),
}


@pytest.mark.parametrize(
	("tool_name", "command", "aliases", "hidden"),
	[(tool_name, *spec) for tool_name, spec in _ALIASED_SINGLE_COMMAND_TOOLS.items()],
	ids=list(_ALIASED_SINGLE_COMMAND_TOOLS),
)
def test_aliased_tool_schema_lists_every_cli_parameter(
	tool_name: str, command: click.Command, aliases: dict[str, str], hidden: set[str],
) -> None:
	_assert_schema_matches_command(tool_name, command, aliases, hidden)


# ── 枚举参数的显式空串必须到达 CLI（Issue #982） ──────────────────────────
# CLI 对每个 enum 选项都有取值校验，空串会得到 INVALID_PARAM + 可选值列表。
# 映射层用真值判断时，空串被化装成「没传」，工具静默落回默认值：`stats(format="")`
# 返回 json 而不是报错、`search(sort="")` 按默认相关性排序。
_ENUM_EMPTY_CASES: list[tuple[str, dict[str, Any], list[str]]] = [
	("boss_search", {"query": "python", "sort": ""}, ["--sort", ""]),
	("boss_crawl_results", {"run_id": "r1", "detail_status": ""}, ["--detail-status", ""]),
	("boss_chat", {"from_who": ""}, ["--from", ""]),
	("boss_chat", {"format": ""}, ["--export", ""]),
	("boss_me", {"section": ""}, ["--section", ""]),
	("boss_export", {"query": "python", "format": ""}, ["--format", ""]),
	("boss_digest", {"format": ""}, ["--format", ""]),
	("boss_stats", {"format": ""}, ["--format", ""]),
	("boss_ai_reply", {"recruiter_message": "hi", "tone": ""}, ["--tone", ""]),
	("boss_ai_cover_letter", {"resume": "r", "tone": "", "lang": ""}, ["--tone", "", "--lang", ""]),
	("boss_hr_exchange", {"friend_id": "f", "type": ""}, ["--type", ""]),
]

# preset / watch / batch-greet 的筛选键在 CLI 层是 Click Choice，但 MCP schema
# 只声明 string（可选值见 boss schema），不能靠 schema enum 守卫发现（Issue #1180）。
_CHOICE_FILTER_EMPTY_CASES: list[tuple[str, dict[str, Any], str]] = [
	("boss_preset_add", {"name": "n", "query": "q", "industry": ""}, "--industry"),
	("boss_watch_add", {"name": "n", "query": "q", "stage": ""}, "--stage"),
	("boss_batch_greet", {"query": "q", "scale": ""}, "--scale"),
	("boss_batch_greet", {"query": "q", "job_type": ""}, "--job-type"),
]


@pytest.mark.parametrize(
	("tool_name", "arguments", "flag"),
	_CHOICE_FILTER_EMPTY_CASES,
	ids=[f"{name}-{flag}" for name, _, flag in _CHOICE_FILTER_EMPTY_CASES],
)
def test_choice_filter_empty_string_reaches_cli_verbatim(tool_name, arguments, flag) -> None:
	"""筛选键的空串必须到达 CLI 的 Choice 校验（与 #982 同一口径，Issue #1180）。"""
	argv = _build_args(tool_name, arguments)
	assert flag in argv, argv
	assert argv[argv.index(flag) + 1] == "", argv
	command, rest = _resolve_command(argv)
	with pytest.raises(click.BadParameter):
		command.make_context(command.name, rest)


@pytest.mark.parametrize(
	("tool_name", "arguments", "expected_tail"),
	_ENUM_EMPTY_CASES,
	ids=[f"{name}-{list(args)}" for name, args, _ in _ENUM_EMPTY_CASES],
)
def test_enum_empty_string_reaches_cli_verbatim(
	tool_name: str, arguments: dict[str, Any], expected_tail: list[str],
) -> None:
	"""显式空串的枚举参数原样下发，让 CLI 的取值诊断回到 Agent（Issue #982）。"""
	argv = _build_args(tool_name, arguments)
	assert argv[-len(expected_tail) :] == expected_tail, argv

	# 目标命令必须真的把空串判成非法取值，而不是静默接受。
	# Click 的 BadParameter 消息是英文的；CLI 的中文诊断在命令层再包一层，
	# 这里只验证「空串确实到达了 Choice 校验」。
	command, rest = _resolve_command(argv)
	with pytest.raises(click.BadParameter):
		command.make_context(command.name, rest)


@pytest.mark.parametrize(
	("tool_name", "arguments", "absent_flags"),
	[
		("boss_search", {"query": "python", "sort": None}, ["--sort"]),
		# search 的自由文本筛选空串是「关闭该项筛选」的显式信号，不再省略
		# （Issue #2023，见 tests/test_mcp_estimate_audit_2023.py）。
		("boss_chat", {"format": None, "output_file": None}, ["--export", "--output"]),
		("boss_me", {"section": None}, ["--section"]),
		("boss_stats", {"format": None}, ["--format"]),
		("boss_ai_cover_letter", {"resume": "r", "tone": "", "lang": None}, ["--lang"]),
		("boss_hr_exchange", {"friend_id": "f", "type": None}, ["--type"]),
		("boss_preset_add", {"name": "n", "query": "q", "industry": None}, ["--industry"]),
		("boss_batch_greet", {"query": "q", "scale": None}, ["--scale"]),
	],
	ids=[
		"search-null-sort",
		"chat-null",
		"me-null",
		"stats-null",
		"ai-empty-tone-null-lang",
		"hr-exchange-null",
		"preset-add-null-industry",
		"batch-greet-null-scale",
	],
)
def test_non_enum_empty_and_none_values_stay_omitted(
	tool_name: str, arguments: dict[str, Any], absent_flags: list[str],
) -> None:
	"""``null`` 与自由文本的空串仍等价于未传（与 Issue #958 一致），行为不变。"""
	argv = _build_args(tool_name, arguments)
	for flag in absent_flags:
		assert flag not in argv, f"{flag} 不该出现：{argv}"


@pytest.mark.parametrize("tool", TOOLS, ids=[tool.name for tool in TOOLS])
def test_enum_values_are_never_silently_replaced_by_a_default(tool) -> None:
	"""非法的枚举值必须被拒绝，不能静默落回默认分支（Issue #1002）。

	映射层允许两种处理：自己抛 ``ValueError`` / ``KeyError``；或把原值原样写进
	argv，让 Click 的 Choice 或命令层校验返回 ``INVALID_PARAM``。禁止的是第三种：
	值被丢掉或替换成别的分支（``boss_wizard`` 曾把 ``resum`` 当成新建运行）。
	"""
	properties = tool.input_schema.get("properties") or {}
	for key, prop in properties.items():
		if not isinstance(prop, dict) or "enum" not in prop:
			continue
		arguments = _sample_arguments(tool)
		arguments[key] = "bogus-enum-value"
		try:
			argv = _build_args(tool.name, arguments)
		except (KeyError, ValueError):
			continue
		assert any("bogus-enum-value" in str(item) for item in argv), (
			f"{tool.name}.{key} 的非法取值被静默丢弃或被替换：{argv}"
		)
		try:
			command, rest = _resolve_command(argv)
			ctx = command.make_context(command.name, rest)
			ctx.close()
		except (click.ClickException, SystemExit):
			continue
		except AssertionError:
			# action 是位置参数里的一层子命令（config / hr jobs）：非法值解析不到
			# 已注册子命令，真实 CLI 会回 "No such command"，同样属于拒绝。
			continue
		# Click 未拒绝时，值必须仍在 argv 里，命令层的校验才有机会拒绝它。
		assert any("bogus-enum-value" in str(item) for item in argv), f"{tool.name}.{key}: {argv}"


def test_every_enum_schema_property_is_covered_by_the_empty_value_rule() -> None:
	"""schema 里声明了 enum 的字符串参数必须登记在 ``_ENUM_KEYS``。

	新增带取值校验的枚举参数时，映射层若还用真值判断，显式空串会被静默吞掉；
	这条守卫让漏登记在本地门禁现形，而不是等 Agent 拿到 ``ok:true`` 的默认结果。
	"""
	from boss_agent_cli import mcp_args

	enum_keys: set[str] = set()
	for tool in TOOLS:
		properties = tool.input_schema.get("properties") or {}
		enum_keys.update(
			key for key, prop in properties.items() if "enum" in prop and prop.get("type") == "string"
		)
	# 以下枚举键不走 `_optional`：
	# - `active`：`resolve_active_level("")` 返回 None，空值本来就等于未传；
	# - `action` / `role`：位置参数语义（wizard / config / hr jobs 的命令路由），
	#   不是 `--flag value`；空串由各自的 KeyError / 路由分支处理；
	# - `tag`：mark 的映射无条件下发 `--label`，空串本来就到达 CLI 并被拒绝。
	assert enum_keys - {"active", "action", "role", "tag"} <= set(mcp_args._ENUM_KEYS), (
		f"schema 声明了枚举但映射层未登记：{sorted(enum_keys - set(mcp_args._ENUM_KEYS))}"
	)
	# 这些筛选键在 preset / watch / batch-greet 上是 Click Choice，schema 只声明
	# string（可选值见 boss schema），枚举守卫覆盖不到，单独钉住（Issue #1180）。
	assert {"industry", "scale", "stage", "job_type"} <= set(mcp_args._ENUM_KEYS)
