"""模型上下文协议服务测试 — 覆盖工具定义、参数构建和调用逻辑。"""

import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock


# mcp 包可能未安装，用 mock 模块替代以允许导入 server.py
_mcp_mock = types.ModuleType("mcp")
_mcp_server = types.ModuleType("mcp.server")
_mcp_stdio = types.ModuleType("mcp.server.stdio")
_mcp_types = types.ModuleType("mcp.types")
_mcp_server.Server = MagicMock()
_mcp_stdio.stdio_server = MagicMock()
_mcp_types.TextContent = MagicMock()
_mcp_types.Tool = type("Tool", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
_mcp_mock.server = _mcp_server
_mcp_mock.types = _mcp_types

sys.modules.setdefault("mcp", _mcp_mock)
sys.modules.setdefault("mcp.server", _mcp_server)
sys.modules.setdefault("mcp.server.stdio", _mcp_stdio)
sys.modules.setdefault("mcp.types", _mcp_types)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
from server import TOOLS, _build_args, _run_boss  # noqa: E402


# ── 工具定义完整性 ──────────────────────────────────────────────────


def test_tools_not_empty():
	"""工具列表不应为空。"""
	assert len(TOOLS) > 0


def test_all_tools_have_name_and_description():
	"""每个工具都应有名称和描述。"""
	for tool in TOOLS:
		assert tool.name, "工具缺少名称"
		assert tool.description, f"{tool.name} 缺少描述"


def test_all_tools_have_input_schema():
	"""每个工具都应有输入模式定义。"""
	for tool in TOOLS:
		schema = tool.inputSchema
		assert isinstance(schema, dict), f"{tool.name} 缺少输入模式"
		assert schema.get("type") == "object", f"{tool.name} 输入模式类型应为 object"


def test_tool_names_follow_convention():
	"""所有工具名应以 boss_ 开头。"""
	for tool in TOOLS:
		assert tool.name.startswith("boss_"), f"{tool.name} 不符合命名规范"


def test_required_tools_present():
	"""核心工具应存在。"""
	names = {t.name for t in TOOLS}
	required = {
		"boss_status", "boss_doctor", "boss_search", "boss_detail",
		"boss_greet", "boss_chat", "boss_me", "boss_cities",
		"boss_chatmsg", "boss_chat_summary", "boss_mark", "boss_exchange",
		"boss_apply", "boss_batch_greet", "boss_show", "boss_pipeline",
		"boss_follow_up", "boss_digest", "boss_config", "boss_clean",
		"boss_stats", "boss_ai_reply",
		"boss_resume_list", "boss_resume_show",
		"boss_ai_analyze_jd", "boss_ai_optimize", "boss_ai_suggest",
		"boss_watch_list", "boss_watch_run",
		"boss_preset_list", "boss_shortlist_list",
	}
	missing = required - names
	assert not missing, f"缺少核心工具: {missing}"


def test_tool_count():
	"""工具总数应与当前注册一致。"""
	assert len(TOOLS) >= 23


def test_search_tool_requires_query():
	"""搜索工具应要求 query 参数。"""
	search = next(t for t in TOOLS if t.name == "boss_search")
	assert "query" in search.inputSchema.get("required", [])


def test_greet_tool_requires_both_ids():
	"""打招呼工具应要求 security_id 和 job_id。"""
	greet = next(t for t in TOOLS if t.name == "boss_greet")
	required = greet.inputSchema.get("required", [])
	assert "security_id" in required
	assert "job_id" in required


# ── 参数构建 ────────────────────────────────────────────────────────


def test_build_args_status():
	"""无参数命令应只返回命令名。"""
	assert _build_args("boss_status", {}) == ["status"]


def test_build_args_doctor():
	assert _build_args("boss_doctor", {}) == ["doctor"]


def test_build_args_cities():
	assert _build_args("boss_cities", {}) == ["cities"]


def test_build_args_interviews():
	assert _build_args("boss_interviews", {}) == ["interviews"]


def test_build_args_history():
	assert _build_args("boss_history", {}) == ["history"]


def test_build_args_search_basic():
	"""搜索只传 query 时应返回最小参数。"""
	args = _build_args("boss_search", {"query": "python"})
	assert args == ["search", "python"]


def test_build_args_search_with_options():
	"""搜索传多个选项时应正确拼接。"""
	args = _build_args("boss_search", {
		"query": "golang",
		"city": "北京",
		"salary": "20-40K",
		"page": 2,
	})
	assert "search" in args
	assert "golang" in args
	assert "--city" in args
	assert "北京" in args
	assert "--salary" in args
	assert "20-40K" in args
	assert "--page" in args
	assert "2" in args


def test_build_args_search_ignores_empty_options():
	"""空选项不应出现在参数中。"""
	args = _build_args("boss_search", {"query": "java", "city": "", "salary": None})
	assert "--city" not in args
	assert "--salary" not in args


def test_build_args_recommend_no_page():
	args = _build_args("boss_recommend", {})
	assert args == ["recommend"]


def test_build_args_recommend_with_page():
	args = _build_args("boss_recommend", {"page": 3})
	assert args == ["recommend", "--page", "3"]


def test_build_args_detail_basic():
	args = _build_args("boss_detail", {"security_id": "abc123"})
	assert args == ["detail", "abc123"]


def test_build_args_detail_with_job_id():
	args = _build_args("boss_detail", {"security_id": "abc", "job_id": "j456"})
	assert "--job-id" in args
	assert "j456" in args


def test_build_args_greet():
	args = _build_args("boss_greet", {"security_id": "s1", "job_id": "j1"})
	assert args == ["greet", "s1", "j1"]


def test_build_args_chat_no_options():
	args = _build_args("boss_chat", {})
	assert args == ["chat"]


def test_build_args_chat_with_all_options():
	args = _build_args("boss_chat", {"from_who": "boss", "days": 7, "page": 2})
	assert "--from" in args
	assert "boss" in args
	assert "--days" in args
	assert "7" in args
	assert "--page" in args


def test_build_args_me_no_section():
	args = _build_args("boss_me", {})
	assert args == ["me"]


def test_build_args_me_with_section():
	args = _build_args("boss_me", {"section": "resume"})
	assert args == ["me", "--section", "resume"]


# ── 新增工具参数构建 ────────────────────────────────────────────────


def test_build_args_chatmsg():
	args = _build_args("boss_chatmsg", {"security_id": "s1", "page": 2})
	assert args == ["chatmsg", "s1", "--page", "2"]


def test_build_args_chat_summary():
	args = _build_args("boss_chat_summary", {"security_id": "s1"})
	assert args == ["chat-summary", "s1"]


def test_build_args_mark():
	args = _build_args("boss_mark", {"security_id": "s1", "tag": "收藏"})
	assert args == ["mark", "s1", "--tag", "收藏"]


def test_build_args_mark_remove():
	args = _build_args("boss_mark", {"security_id": "s1", "tag": "收藏", "remove": True})
	assert "--remove" in args


def test_build_args_exchange():
	assert _build_args("boss_exchange", {"security_id": "s1"}) == ["exchange", "s1"]


def test_build_args_apply():
	assert _build_args("boss_apply", {"security_id": "s1", "job_id": "j1"}) == ["apply", "s1", "j1"]


def test_build_args_batch_greet():
	args = _build_args("boss_batch_greet", {"query": "python", "limit": 3, "dry_run": True})
	assert "batch-greet" in args
	assert "python" in args
	assert "--limit" in args
	assert "--dry-run" in args


def test_build_args_show():
	assert _build_args("boss_show", {"number": 5}) == ["show", "5"]


def test_build_args_pipeline():
	assert _build_args("boss_pipeline", {}) == ["pipeline"]


def test_build_args_follow_up():
	args = _build_args("boss_follow_up", {"days_stale": 7})
	assert args == ["follow-up", "--days-stale", "7"]


def test_build_args_digest():
	args = _build_args("boss_digest", {"days_stale": 5})
	assert args == ["digest", "--days-stale", "5"]


def test_build_args_config_list():
	assert _build_args("boss_config", {"action": "list"}) == ["config", "list"]


def test_build_args_config_set():
	args = _build_args("boss_config", {"action": "set", "key": "log_level", "value": "debug"})
	assert args == ["config", "set", "log_level", "debug"]


def test_build_args_clean():
	args = _build_args("boss_clean", {"dry_run": True, "all": True})
	assert "clean" in args
	assert "--dry-run" in args
	assert "--all" in args


# ── CLI 调用逻辑 ───────────────────────────────────────────────────


@patch("server.subprocess.run")
def test_run_boss_parses_json_output(mock_run):
	"""正常 JSON 输出应被正确解析。"""
	mock_run.return_value = MagicMock(
		stdout='{"ok": true, "data": {"status": "logged_in"}}',
		stderr="",
	)
	result = _run_boss("status")
	assert result["ok"] is True
	mock_run.assert_called_once()
	cmd = mock_run.call_args[0][0]
	assert cmd[0] == "boss"
	assert "--json" in cmd


@patch("server.subprocess.run")
def test_run_boss_handles_invalid_json(mock_run):
	"""非 JSON 输出应返回错误信封。"""
	mock_run.return_value = MagicMock(stdout="not json", stderr="some error")
	result = _run_boss("status")
	assert result["ok"] is False
	assert result["error"]["code"] == "CLI_ERROR"


@patch("server.subprocess.run")
def test_run_boss_handles_empty_output(mock_run):
	"""空输出应返回错误信封。"""
	mock_run.return_value = MagicMock(stdout="", stderr="command not found")
	result = _run_boss("status")
	assert result["ok"] is False
	assert "command not found" in result["error"]["message"]


@patch("server.subprocess.run")
def test_run_boss_passes_args(mock_run):
	"""参数应正确传递给 subprocess。"""
	mock_run.return_value = MagicMock(stdout='{"ok": true}', stderr="")
	_run_boss("search", "python", "--city", "北京")
	cmd = mock_run.call_args[0][0]
	assert cmd == ["boss", "--json", "search", "python", "--city", "北京"]


@patch("server.subprocess.run")
def test_run_boss_timeout(mock_run):
	"""应设置 120 秒超时。"""
	mock_run.return_value = MagicMock(stdout='{"ok": true}', stderr="")
	_run_boss("doctor")
	assert mock_run.call_args[1]["timeout"] == 120


# ── 新增工具 _build_args 覆盖（PR #41 扩展）─────────────────


def test_build_args_stats_default():
	assert _build_args("boss_stats", {}) == ["stats"]


def test_build_args_stats_with_days():
	args = _build_args("boss_stats", {"days": 7})
	assert args == ["stats", "--days", "7"]


def test_build_args_ai_reply_minimal():
	args = _build_args("boss_ai_reply", {"recruiter_message": "请问到岗时间？"})
	assert args == ["ai", "reply", "请问到岗时间？"]


def test_build_args_ai_reply_full():
	args = _build_args("boss_ai_reply", {
		"recruiter_message": "hello",
		"context": "previous chat",
		"resume": "test",
		"tone": "热情积极",
	})
	assert "--context" in args
	assert "previous chat" in args
	assert "--resume" in args
	assert "test" in args
	assert "--tone" in args
	assert "热情积极" in args


def test_build_args_resume_list():
	assert _build_args("boss_resume_list", {}) == ["resume", "list"]


def test_build_args_resume_show():
	assert _build_args("boss_resume_show", {"name": "default"}) == ["resume", "show", "default"]


def test_build_args_ai_analyze_jd():
	args = _build_args("boss_ai_analyze_jd", {"jd_text": "需要 Python", "resume": "my"})
	assert args == ["ai", "analyze-jd", "需要 Python", "--resume", "my"]


def test_build_args_ai_optimize():
	args = _build_args("boss_ai_optimize", {"resume": "my", "jd_text": "后端岗位"})
	assert args == ["ai", "optimize", "my", "--jd", "后端岗位"]


def test_build_args_ai_suggest():
	args = _build_args("boss_ai_suggest", {"resume": "my", "jd_text": "后端岗位"})
	assert args == ["ai", "suggest", "my", "--jd", "后端岗位"]


def test_build_args_watch_list():
	assert _build_args("boss_watch_list", {}) == ["watch", "list"]


def test_build_args_watch_run():
	assert _build_args("boss_watch_run", {"name": "daily"}) == ["watch", "run", "daily"]


def test_build_args_preset_list():
	assert _build_args("boss_preset_list", {}) == ["preset", "list"]


def test_build_args_shortlist_list():
	assert _build_args("boss_shortlist_list", {}) == ["shortlist", "list"]


def test_tool_count_after_pr41():
	"""协议服务工具总数应达到 40+（含 shortlist/preset/watch 的 add/remove）。"""
	assert len(TOOLS) >= 40


def test_build_args_shortlist_add():
	args = _build_args("boss_shortlist_add", {"security_id": "s1", "job_id": "j1"})
	assert args == ["shortlist", "add", "s1", "j1"]


def test_build_args_shortlist_remove():
	args = _build_args("boss_shortlist_remove", {"security_id": "s1", "job_id": "j1"})
	assert args == ["shortlist", "remove", "s1", "j1"]


def test_build_args_preset_add_minimal():
	args = _build_args("boss_preset_add", {"name": "p1", "query": "python"})
	assert args == ["preset", "add", "p1", "python"]


def test_build_args_preset_add_full():
	args = _build_args("boss_preset_add", {
		"name": "p1", "query": "golang",
		"city": "上海", "salary": "30-50K", "welfare": "双休",
	})
	assert "--city" in args and "上海" in args
	assert "--salary" in args and "30-50K" in args
	assert "--welfare" in args and "双休" in args


def test_build_args_preset_remove():
	assert _build_args("boss_preset_remove", {"name": "p1"}) == ["preset", "remove", "p1"]


def test_build_args_watch_add():
	args = _build_args("boss_watch_add", {"name": "w1", "query": "rust", "city": "北京"})
	assert args[:4] == ["watch", "add", "w1", "rust"]
	assert "--city" in args and "北京" in args


def test_build_args_watch_remove():
	assert _build_args("boss_watch_remove", {"name": "w1"}) == ["watch", "remove", "w1"]
