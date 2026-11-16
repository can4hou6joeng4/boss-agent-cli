"""本地 JSON 解析的极端合法输入（超长整数、深嵌套）不得冒泡成 traceback（Issue #1227）。

Python 3.11+ 的 ``json.loads`` 对超过 4300 位的整数字面量抛裸 ``ValueError``
（不是 ``JSONDecodeError``）；深嵌套数组在 3.11–3.13 抛 ``RecursionError``，3.14 起
解析器不再受限。两个边界都必须落回各模块自己的兜底语义：熔断 latch 继续
fail-closed、断点继续报各自的契约错误、配置类继续降级。深嵌套用例只断言与
解释器无关的不变量。
"""

import datetime
import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.local_models import LocalModelManifestError, model_registry_path, read_imported_models
from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.cache.store import (
	CacheRecordInvalid,
	CacheStore,
	SavedSearchParamsError,
	_decode_record,
	_decode_saved_params,
)
from boss_agent_cli.commands.wizard import _parse_input_json
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.store import ResumeImportError, ResumeStore
from boss_agent_cli.services.chat_snapshot import load_snapshot, save_snapshot_and_diff
from boss_agent_cli.wizard.models import WorkflowInputError

# 超过 Python 3.11+ 默认的 4300 位整数转换上限。
_OVERLONG_INT = "9" * 5000
_DEEP = "[" * 100_000 + "]" * 100_000


def _envelope(result):
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"]
	return payload


# ── 自动化控制文件 ──────────────────────────────────────────────────


def test_read_stop_fails_closed_on_overlong_integer(tmp_path):
	"""熔断 latch 读不出来必须 fail-closed，不能因为解析异常把 open=false 带出去。"""
	store = AutomationStore(tmp_path)
	store.stop_path.write_text('{"open": false, "pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	stop = store.read_stop()

	assert stop["open"] is True
	assert stop.get("corrupt") is True
	assert "stop.json" in stop["reason"]


def test_read_stop_never_raises_on_deep_nesting(tmp_path):
	store = AutomationStore(tmp_path)
	store.stop_path.write_text('{"pad": ' + _DEEP + "}", encoding="utf-8")

	assert isinstance(store.read_stop(), dict)


def test_read_state_reports_overlong_integer(tmp_path):
	store = AutomationStore(tmp_path)
	store.state_path.write_text('{"safety": {"consecutive_errors": ' + _OVERLONG_INT + "}}", encoding="utf-8")

	with pytest.raises(AutomationStateError) as excinfo:
		store.read_state()

	assert excinfo.value.path == store.state_path


def test_read_state_never_raises_on_deep_nesting(tmp_path):
	store = AutomationStore(tmp_path)
	store.state_path.write_text('{"conversations": {}, "pad": ' + _DEEP + "}", encoding="utf-8")

	try:
		state = store.read_state()
	except AutomationStateError:
		# 3.11–3.13 解析深嵌套抛 RecursionError，按状态损坏上报是预期。
		return
	assert isinstance(state, dict)


def test_read_jsonl_skips_overlong_integer_line(tmp_path):
	store = AutomationStore(tmp_path)
	(store.root / "rows.jsonl").write_text(
		'{"id": "ok"}\n{"pad": ' + _OVERLONG_INT + "}\n", encoding="utf-8"
	)

	assert store.read_jsonl("rows.jsonl") == [{"id": "ok"}]


def test_agent_stats_reports_corrupt_state_envelope(tmp_path):
	automation = tmp_path / "automation"
	automation.mkdir(parents=True)
	(automation / "state.json").write_text(
		'{"safety": {"consecutive_errors": ' + _OVERLONG_INT + "}}", encoding="utf-8"
	)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "agent", "stats"])

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["command"] == "agent.stats"
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"


def test_agent_stats_fails_closed_on_corrupt_stop_latch(tmp_path):
	automation = tmp_path / "automation"
	automation.mkdir(parents=True)
	(automation / "stop.json").write_text('{"open": false, "pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "agent", "stats"])

	assert result.exit_code == 0
	breaker = json.loads(result.output)["data"]["circuit_breaker"]
	assert breaker["open"] is True
	assert breaker["corrupt"] is True


# ── 简历文件 ────────────────────────────────────────────────────────


def test_resume_read_payload_reports_parse_failure(tmp_path):
	path = tmp_path / "x.json"
	path.write_text('{"name": "x", "pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	raw, error = ResumeStore._read_payload(path)

	assert raw is None
	assert error is not None and "JSON 读取失败" in error


def test_resume_show_reports_file_invalid(tmp_path):
	resumes = tmp_path / "resumes"
	resumes.mkdir()
	(resumes / "x.json").write_text('{"name": "x", "pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "resume", "show", "x"])

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "RESUME_FILE_INVALID"


def test_resume_import_reports_parse_failure(tmp_path):
	path = tmp_path / "import.json"
	path.write_text('{"name": "x", "pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	with pytest.raises(ResumeImportError):
		ResumeStore(tmp_path / "resumes").import_file(path)


# ── 聊天快照 ────────────────────────────────────────────────────────


def test_load_snapshot_returns_none_for_overlong_integer(tmp_path):
	path = tmp_path / "2026-11-15.json"
	path.write_text('{"pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	assert load_snapshot(str(path), Logger()) is None


def test_load_snapshot_never_raises_on_deep_nesting(tmp_path):
	path = tmp_path / "2026-11-15.json"
	path.write_text("[" + _DEEP + "]", encoding="utf-8")

	# 3.11–3.13 解析失败按 None；3.14 解析成功但元素不是 dict，过滤成空列表。
	result = load_snapshot(str(path), Logger())
	assert result is None or result == []


def test_save_snapshot_ignores_corrupt_daily_snapshot(tmp_path):
	today = datetime.date.today().isoformat()
	(tmp_path / f"{today}.json").write_text('{"pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	result = save_snapshot_and_diff(str(tmp_path), [{"uid": "synthetic-1"}], Logger())

	assert result["is_first"] is True
	assert json.loads((tmp_path / f"{today}.json").read_text(encoding="utf-8")) == [{"uid": "synthetic-1"}]


# ── SQLite JSON 列 ──────────────────────────────────────────────────


def test_decode_record_wraps_overlong_integer():
	with pytest.raises(CacheRecordInvalid) as excinfo:
		_decode_record("crawl_run", "run-1", "payload", '{"pad": ' + _OVERLONG_INT + "}")

	assert excinfo.value.column == "payload"


def test_decode_saved_params_wraps_overlong_integer():
	with pytest.raises(SavedSearchParamsError):
		_decode_saved_params("synthetic", '{"pad": ' + _OVERLONG_INT + "}")


def test_shortlist_tags_fall_back_to_comma_split_for_overlong_integer():
	tags = CacheStore._deserialize_shortlist_tags('{"pad": ' + _OVERLONG_INT + "}")

	assert len(tags) == 1
	assert tags[0].startswith('{"pad"')


# ── 风控锁 ──────────────────────────────────────────────────────────


def test_risk_lock_fails_closed_on_overlong_integer(tmp_path):
	lock = cdp_risk_lock.lock_path(tmp_path)
	lock.parent.mkdir(parents=True, exist_ok=True)
	lock.write_text('{"locked_at": ' + _OVERLONG_INT + "}", encoding="utf-8")

	result = cdp_risk_lock.read_lock(tmp_path)

	assert result is not None
	assert result.corrupt is True


def test_risk_lock_never_raises_on_deep_nesting(tmp_path):
	lock = cdp_risk_lock.lock_path(tmp_path)
	lock.parent.mkdir(parents=True, exist_ok=True)
	lock.write_text('{"pad": ' + _DEEP + "}", encoding="utf-8")

	assert cdp_risk_lock.read_lock(tmp_path) is not None


# ── 配置文件与用户输入 ──────────────────────────────────────────────


def test_config_with_overlong_integer_names_the_file(tmp_path):
	config_file = tmp_path / "config.json"
	config_file.write_text('{"log_level": ' + _OVERLONG_INT + "}", encoding="utf-8")

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "reset", "log_level"])

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert str(config_file) in payload["error"]["message"]
	assert "不是合法 JSON" in payload["error"]["message"]


def test_input_json_overlong_integer_is_rejected():
	with pytest.raises(WorkflowInputError):
		_parse_input_json('{"limit": ' + _OVERLONG_INT + "}")


# ── AI 配置与本地模型登记 ───────────────────────────────────────────


def test_ai_config_ignores_overlong_integer(tmp_path):
	store = AIConfigStore(tmp_path)
	store._config_path.write_text('{"ai_provider": ' + _OVERLONG_INT + "}", encoding="utf-8")

	assert store.load_config()["ai_provider"] is None


def test_imported_model_registry_degrades_on_overlong_integer(tmp_path):
	path = model_registry_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text('{"pad": ' + _OVERLONG_INT + "}", encoding="utf-8")

	assert read_imported_models(tmp_path) == []
	with pytest.raises(LocalModelManifestError):
		read_imported_models(tmp_path, strict=True)
