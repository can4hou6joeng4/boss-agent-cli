"""TTY 展示与请求间隔配置（Issue #2754–#2757）。

四条边界：

- #2754：``boss status`` 在终端显示 ``logged in as None``；``--live`` 把类型漂移的
  ``name`` 原样写进 ``user_name``；
- #2755：通用表格把键存在而值为 ``None`` / 列表 / 对象的单元格按 Python repr 显示；
- #2756：未知时间戳（哨兵 ``0`` / 布尔）被当成 1970 年；
- #2757：``RATE_LIMITED`` 排障指引把间隔调到默认值的十分之一，``config set`` 的
  数组写法被拒绝。
"""

from __future__ import annotations

import datetime
import io
import json
import re
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.commands.config_cmd import _parse_value
from boss_agent_cli.commands.doctor import _RISK_FREE_DELAY_FLOOR, _risk_lock_check_payload
from boss_agent_cli.config import DEFAULT_REQUEST_DELAY, DEFAULTS
from boss_agent_cli.display import (
	_snapshot_time_label,
	cell_text,
	render_job_detail,
	render_list_result,
	render_simple_list,
	render_status,
)
from boss_agent_cli.index_cache import get_index_snapshot
from boss_agent_cli.main import cli

_MISSING = object()
_SYNTHETIC_TOKEN = {"cookies": {"wt2": "synthetic"}, "stoken": "synthetic"}
_KNOWN_TS = 1_780_000_000
_DOCS = Path(__file__).resolve().parents[1] / "docs"


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 200) -> io.StringIO:
	"""把 display.console 换成可读的缓冲区，并让命令走 TTY 渲染而不是信封。"""
	stream = io.StringIO()
	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=width))
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	return stream


def _first_line(stream: io.StringIO) -> str:
	return stream.getvalue().strip().splitlines()[0]


def _row_cells(output: str, marker: str) -> list[str]:
	"""取表格里含 marker 的那一行，按列拆成去掉首尾空白的单元格。"""
	for line in output.splitlines():
		if "│" in line and marker in line:
			return [cell.strip() for cell in line.split("│")[1:-1]]
	raise AssertionError(f"表格里没有含 {marker!r} 的行：\n{output}")


def _json(data_dir: Path, *argv: str) -> tuple[int, dict[str, Any]]:
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *argv])
	envelope: dict[str, Any] = json.loads(result.stdout)
	return result.exit_code, envelope


# ── #2754：登录状态里的用户名 ─────────────────────────────────────────


@pytest.mark.parametrize(
	"user_name",
	[_MISSING, None, "", "   ", {"n": 1}, ["x"], True, 42],
	ids=["missing", "null", "empty", "blank", "dict", "list", "bool", "int"],
)
def test_render_status_without_usable_name_prints_only_logged_in(monkeypatch: pytest.MonkeyPatch, user_name: Any) -> None:
	stream = _tty(monkeypatch)
	data: dict[str, Any] = {"logged_in": True}
	if user_name is not _MISSING:
		data["user_name"] = user_name

	render_status(data)

	assert stream.getvalue().strip() == "logged in"


@pytest.mark.parametrize("user_name", ["测试用户", "  测试用户  "], ids=["plain", "padded"])
def test_render_status_with_name_prints_the_trimmed_name(monkeypatch: pytest.MonkeyPatch, user_name: str) -> None:
	stream = _tty(monkeypatch)

	render_status({"logged_in": True, "user_name": user_name})

	assert stream.getvalue().strip() == "logged in as 测试用户"


def test_render_status_logged_out_never_mentions_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)

	render_status({"logged_in": False, "user_name": "测试用户"})

	output = stream.getvalue()
	assert "not logged in" in output
	assert "测试用户" not in output


@patch("boss_agent_cli.commands.status.AuthManager")
def test_status_default_path_does_not_print_none_in_terminal(
	mock_auth_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""没做在线验证就不知道用户名：终端只说已登录，不能把 null 打成 ``None``。"""
	mock_auth_cls.return_value.check_status.return_value = _SYNTHETIC_TOKEN
	stream = _tty(monkeypatch)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "status"])

	assert result.exit_code == 0, result.output
	assert _first_line(stream) == "logged in"
	assert "as None" not in stream.getvalue()


@patch("boss_agent_cli.commands.status.AuthManager")
def test_status_default_path_keeps_null_user_name_in_envelope(mock_auth_cls: Any, tmp_path: Path) -> None:
	"""信封契约不变：非 live 的 ``user_name`` 仍是 null，而不是占位文本。"""
	mock_auth_cls.return_value.check_status.return_value = _SYNTHETIC_TOKEN

	exit_code, envelope = _json(tmp_path, "status")

	assert exit_code == 0
	assert envelope["data"]["live"] is False
	assert envelope["data"]["user_name"] is None


class _UserInfoPlatform:
	"""只实现 ``status --live`` 用到的接口。"""

	def __init__(self, zp_data: Any, *, code: int = 0) -> None:
		self.zp_data = zp_data
		self.code = code

	def __enter__(self) -> _UserInfoPlatform:
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def user_info(self) -> dict[str, Any]:
		return {"code": self.code, "zpData": self.zp_data}

	@staticmethod
	def is_success(response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	@staticmethod
	def unwrap_data(response: dict[str, Any]) -> Any:
		return response.get("zpData")

	@staticmethod
	def parse_error(response: dict[str, Any]) -> tuple[str, str]:
		return "RATE_LIMITED", "too fast"


def _live_status(data_dir: Path, platform: _UserInfoPlatform, *, json_mode: bool) -> Any:
	argv = ["--data-dir", str(data_dir), *(["--json"] if json_mode else []), "status", "--live"]
	with patch("boss_agent_cli.commands.status.AuthManager") as auth_cls, patch(
		"boss_agent_cli.commands.status.get_platform_instance", return_value=platform
	):
		auth_cls.return_value.check_status.return_value = _SYNTHETIC_TOKEN
		return CliRunner().invoke(cli, argv)


@pytest.mark.parametrize(
	"name",
	[_MISSING, None, "", "   ", {"n": 1}, ["x"], True, False, 42, 1.5],
	ids=["missing", "null", "empty", "blank", "dict", "list", "true", "false", "int", "float"],
)
def test_status_live_falls_back_when_name_is_not_usable_text(tmp_path: Path, name: Any) -> None:
	"""显式 null 与类型漂移和键缺失同样回落，不把对象 / 布尔写进 ``user_name``。"""
	zp_data = {} if name is _MISSING else {"name": name}

	result = _live_status(tmp_path, _UserInfoPlatform(zp_data), json_mode=True)

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert data["live"] is True
	assert data["user_name"] == "未知用户"


@pytest.mark.parametrize("name", ["tester", "  tester  "], ids=["plain", "padded"])
def test_status_live_keeps_real_name_without_padding(tmp_path: Path, name: str) -> None:
	result = _live_status(tmp_path, _UserInfoPlatform({"name": name}), json_mode=True)

	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["data"]["user_name"] == "tester"


@pytest.mark.parametrize(
	("zp_data", "expected"),
	[({"name": None}, "logged in as 未知用户"), ({"name": {"n": 1}}, "logged in as 未知用户"), ({"name": "tester"}, "logged in as tester")],
	ids=["null", "dict", "text"],
)
def test_status_live_terminal_line_never_shows_python_repr(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zp_data: dict[str, Any], expected: str
) -> None:
	stream = _tty(monkeypatch)

	result = _live_status(tmp_path, _UserInfoPlatform(zp_data), json_mode=False)

	assert result.exit_code == 0, result.output
	assert _first_line(stream) == expected


def test_status_live_platform_failure_reports_error_without_user_name(tmp_path: Path) -> None:
	"""错误路径不变：在线验证被限流时是错误信封，不带半成品 data。"""
	result = _live_status(tmp_path, _UserInfoPlatform({"name": "tester"}, code=9), json_mode=True)

	assert result.exit_code == 1
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	assert envelope["data"] is None
	assert envelope["error"]["code"] == "RATE_LIMITED"


@patch("boss_agent_cli.commands.status.AuthManager")
def test_status_not_logged_in_is_still_auth_required(mock_auth_cls: Any, tmp_path: Path) -> None:
	mock_auth_cls.return_value.check_status.return_value = None

	exit_code, envelope = _json(tmp_path, "status")

	assert exit_code == 1
	assert envelope["error"]["code"] == "AUTH_REQUIRED"


# ── #2755：通用表格的单元格 ───────────────────────────────────────────


@pytest.mark.parametrize(
	("value", "expected"),
	[
		(None, "-"),
		([], "-"),
		((), "-"),
		({}, "-"),
		(["远程", "双休"], "远程, 双休"),
		(("远程",), "远程"),
		([1, 2.5], "1, 2.5"),
		(["Go", 3], "Go, 3"),
		([True], "[true]"),
		([None], "[null]"),
		(["远程", None], '["远程", null]'),
		([{"a": 1}], '[{"a": 1}]'),
		({"query": "后端", "city": None}, '{"query": "后端", "city": null}'),
		("", ""),
		("文本", "文本"),
		(0, "0"),
		(1.5, "1.5"),
		(True, "True"),
		(False, "False"),
	],
	ids=[
		"none", "empty-list", "empty-tuple", "empty-dict", "str-list", "str-tuple", "number-list", "mixed-scalars",
		"bool-list", "null-list", "list-with-null", "list-of-dict", "dict", "empty-str", "str", "zero", "float",
		"true", "false",
	],
)
def test_cell_text(value: Any, expected: str) -> None:
	assert cell_text(value) == expected


def test_cell_text_survives_values_json_cannot_encode() -> None:
	"""循环引用不是 JSON 数据能产生的形状：按原样兜底，不让渲染抛异常。"""
	looped: list[Any] = [{"k": 1}]
	looped.append(looped)

	assert cell_text(looped) == str(looped)
	assert cell_text([{"when": datetime.date(2026, 1, 2)}]) == '[{"when": "2026-01-02"}]'


@pytest.mark.parametrize("shape", ["scalar", "list", "dict"])
def test_cell_text_does_not_raise_on_oversized_integer(shape: str) -> None:
	"""超过转换上限的整数在 ``str()`` / ``json.dumps`` 时抛 ValueError：不能崩渲染。"""
	huge = 10**5000
	value: Any = {"scalar": huge, "list": [huge], "dict": {"n": huge}}[shape]

	assert isinstance(cell_text(value), str)


def test_simple_list_renders_present_but_empty_cells_like_missing_ones(monkeypatch: pytest.MonkeyPatch) -> None:
	"""``.get(key, "-")`` 只挡键缺失；键存在而值为 None / 空列表时同样显示 ``-``。"""
	stream = _tty(monkeypatch)
	columns = [("title", "title", ""), ("tags", "tags", ""), ("note", "note", "")]

	render_simple_list(
		[
			{"title": "甲职位", "tags": ["远程", "双休"], "note": None},
			{"title": "乙职位", "tags": [], "note": ""},
			{"title": "丙职位"},
		],
		"shortlist",
		columns,
	)

	output = stream.getvalue()
	assert _row_cells(output, "甲职位") == ["1", "甲职位", "远程, 双休", "-"]
	assert _row_cells(output, "乙职位") == ["2", "乙职位", "-", ""]
	assert _row_cells(output, "丙职位") == ["3", "丙职位", "-", "-"]
	assert "None" not in output
	assert "['" not in output


def test_simple_list_renders_nested_containers_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)

	render_simple_list([{"name": "甲", "params": {"query": "后端"}}], "presets", [("name", "name", ""), ("params", "params", "")])

	assert _row_cells(stream.getvalue(), "甲") == ["1", "甲", '{"query": "后端"}']


def test_simple_list_list_cells_stay_literal_under_markup(monkeypatch: pytest.MonkeyPatch) -> None:
	"""列表元素同样是外部文本：拼接后仍按字面显示，不被解析成样式或超链接。"""
	stream = io.StringIO()
	monkeypatch.setattr(
		"boss_agent_cli.display.console",
		Console(file=stream, force_terminal=True, width=200, color_system="truecolor"),
	)

	render_simple_list(
		[{"tags": ["[/bold]", "[link=https://example.invalid/x]点我[/link]"]}], "shortlist", [("tags", "tags", "")]
	)

	plain = re.sub(r"\x1b\[[0-9;]*m", "", stream.getvalue())
	assert "\x1b]8;" not in stream.getvalue()
	assert "[/bold]" in plain
	assert "[link=" in plain


def test_simple_list_still_skips_non_object_rows(monkeypatch: pytest.MonkeyPatch) -> None:
	"""错误路径不变：非对象条目被跳过，全部被跳过时给出空态而不是空表格。"""
	stream = _tty(monkeypatch)

	render_simple_list([None, 5, "x"], "shortlist", [("tags", "tags", "")])  # type: ignore[list-item]

	assert stream.getvalue().strip() == "no shortlist"


def test_diff_rows_show_dash_for_absent_side(monkeypatch: pytest.MonkeyPatch) -> None:
	"""``resume diff`` 的一侧为 None / 空列表时显示 ``-``，而不是 ``None`` / ``[]``。"""
	stream = _tty(monkeypatch)

	render_list_result(
		[
			{"field": "summary", "left": None, "right": "五年后端经验"},
			{"field": "skills", "left": ["Go", "Kafka"], "right": []},
		],
		"diff",
		[("字段", "field", ""), ("甲", "left", ""), ("乙", "right", "")],
	)

	output = stream.getvalue()
	assert _row_cells(output, "summary") == ["1", "summary", "-", "五年后端经验"]
	assert _row_cells(output, "skills") == ["2", "skills", "Go, Kafka", "-"]


def test_resume_diff_terminal_shows_dash_and_json_for_structured_sides(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""端到端：一侧没填求职意向 / 模块时，``resume diff`` 不再显示 ``None`` / ``[]`` / dict repr。"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	exported = tmp_path / "a.json"
	assert _json(data_dir, "resume", "init", "--name", "a", "--template", "default")[0] == 0
	assert _json(data_dir, "resume", "export", "a", "--format", "json", "-o", str(exported))[0] == 0
	document = json.loads(exported.read_text(encoding="utf-8"))
	document["data"]["name"] = "b"
	document["data"]["job_intention"] = {
		"title": "求职意向",
		"icon": "mdi:target",
		"items": [{"label": "职位", "value": "后端"}],
		"show_background": True,
	}
	document["data"]["modules"] = [{"id": "m1", "title": "技能", "icon": "", "rows": [{"tags": ["Go", "Kafka"]}]}]
	imported = tmp_path / "b.json"
	imported.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
	exit_code, envelope = _json(data_dir, "resume", "import", str(imported))
	assert exit_code == 0, envelope
	stream = _tty(monkeypatch)

	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "resume", "diff", "a", "b"])

	assert result.exit_code == 0, result.output
	output = stream.getvalue()
	# 列：# / 字段 / a / b；a 一侧是 null 与空数组。
	assert _row_cells(output, "job_intention")[2] == "-"
	assert _row_cells(output, "modules")[2] == "-"
	assert '"title":' in output
	assert "'title'" not in output
	assert "None" not in output
	assert "True" not in output


def test_shortlist_list_terminal_shows_tags_as_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	for argv in (
		["shortlist", "add", "sid_demo_1", "job_demo_1", "--title", "甲职位", "--company", "某公司", "--tags", "远程,双休"],
		["shortlist", "add", "sid_demo_2", "job_demo_2", "--title", "乙职位", "--company", "某公司"],
	):
		exit_code, envelope = _json(tmp_path, *argv)
		assert exit_code == 0, envelope
	stream = _tty(monkeypatch)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "shortlist", "list"])

	assert result.exit_code == 0, result.output
	output = stream.getvalue()
	# 列：# / title / company / city / salary / tags / note / source
	assert _row_cells(output, "甲职位")[5] == "远程, 双休"
	assert _row_cells(output, "乙职位")[5] == "-"
	assert "[]" not in output
	assert "['" not in output


def test_shortlist_list_envelope_keeps_tags_as_arrays(tmp_path: Path) -> None:
	"""只改终端渲染：信封里的 tags 仍是数组。"""
	_json(tmp_path, "shortlist", "add", "sid_demo_1", "job_demo_1", "--title", "甲职位", "--tags", "远程,双休")
	_json(tmp_path, "shortlist", "add", "sid_demo_2", "job_demo_2", "--title", "乙职位")

	exit_code, envelope = _json(tmp_path, "shortlist", "list")

	assert exit_code == 0
	assert {row["title"]: row["tags"] for row in envelope["data"]} == {"甲职位": ["远程", "双休"], "乙职位": []}


@pytest.mark.parametrize("group", ["preset", "watch"])
def test_saved_search_list_shows_params_as_json(group: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""``preset list`` / ``watch list`` 的条件列与 ``--json`` 同写法，不是 Python repr。"""
	exit_code, envelope = _json(tmp_path, group, "add", "daily", "golang")
	assert exit_code == 0, envelope
	stream = _tty(monkeypatch)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), group, "list"])

	assert result.exit_code == 0, result.output
	output = stream.getvalue()
	assert '"query": "golang"' in output
	assert "'query'" not in output
	assert "None" not in output


def test_config_list_terminal_marks_unset_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "list"])

	assert result.exit_code == 0, result.output
	output = stream.getvalue()
	for key in ("cdp_url", "export_dir", "request_budget_dir"):
		assert _row_cells(output, key)[1:] == [key, "（未设置）", "默认值"]
	# 区间保持数组写法：与 --json、文档默认值表一致，照抄就能写回。
	assert _row_cells(output, "request_delay")[2] == "[30.0, 60.0]"
	assert _row_cells(output, "batch_greet_delay")[2] == "[2.0, 5.0]"
	assert _row_cells(output, "automation")[2] == "（JSON，见下方）"
	assert "None" not in output


def test_config_list_envelope_keeps_null_and_arrays(tmp_path: Path) -> None:
	exit_code, envelope = _json(tmp_path, "config", "list")

	assert exit_code == 0
	values = {item["key"]: item["value"] for item in envelope["data"]["items"]}
	assert values["cdp_url"] is None
	assert values["request_delay"] == [30.0, 60.0]


@pytest.mark.parametrize(
	("argv", "expected_lines"),
	[
		(["config", "get", "cdp_url"], ["value: （未设置）", "default: （未设置）"]),
		(["config", "get", "request_delay"], ["value: [30.0, 60.0]", "default: [30.0, 60.0]"]),
		(["config", "set", "export_dir", "null"], ["value: （未设置）", "previous: （未设置）"]),
		(["config", "reset", "export_dir"], ["value: （未设置）", "restored: True"]),
		(["config", "set", "request_delay", "60,120"], ["value: [60.0, 120.0]", "previous: [30.0, 60.0]"]),
	],
	ids=["get-unset", "get-range", "set-null", "reset", "set-range"],
)
def test_config_result_panel_never_shows_python_repr(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], expected_lines: list[str]
) -> None:
	stream = _tty(monkeypatch)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *argv])

	assert result.exit_code == 0, result.output
	panel_lines = [line.strip("│ ").rstrip() for line in stream.getvalue().splitlines() if line.startswith("│")]
	for expected in expected_lines:
		assert expected in panel_lines
	assert "None" not in stream.getvalue()


def test_config_set_null_envelope_still_reports_null(tmp_path: Path) -> None:
	_json(tmp_path, "config", "set", "export_dir", "/tmp/exports")

	exit_code, envelope = _json(tmp_path, "config", "set", "export_dir", "null")

	assert exit_code == 0
	assert envelope["data"] == {"key": "export_dir", "value": None, "previous": "/tmp/exports"}


# ── #2756：未知时间戳 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
	"value",
	[0, 0.0, -1, -0.5, float("nan"), float("inf"), float("-inf"), 10**400, True, False, None, "1780000000", [1]],
	ids=["zero", "zero-float", "negative", "negative-float", "nan", "inf", "neg-inf", "huge", "true", "false", "none", "str", "list"],
)
def test_snapshot_time_label_treats_unknown_time_as_dash(value: Any) -> None:
	assert _snapshot_time_label(value) == "-"


def test_snapshot_time_label_formats_real_timestamp() -> None:
	expected = datetime.datetime.fromtimestamp(_KNOWN_TS).strftime("%Y-%m-%d %H:%M")

	assert _snapshot_time_label(_KNOWN_TS) == expected
	assert _snapshot_time_label(float(_KNOWN_TS)) == expected


def test_job_detail_does_not_date_legacy_index_snapshot_to_1970(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""旧版本写入的索引缓存没有 saved_at：读回是哨兵 0，终端显示「保存于 -」。"""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	(cache_dir / "index_cache.json").write_text(
		json.dumps({"source": "search:golang", "count": 1, "jobs": [{"security_id": "sid_demo_1", "job_id": "job_demo_1"}]}),
		encoding="utf-8",
	)
	snapshot = get_index_snapshot(tmp_path)
	assert snapshot is not None and snapshot["saved_at"] == 0
	stream = _tty(monkeypatch)

	render_job_detail({"title": "甲职位", "index": 1, "index_snapshot": snapshot})

	output = stream.getvalue()
	assert "保存于 -" in output
	assert "1970" not in output


def test_job_detail_shows_real_snapshot_time(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)

	render_job_detail({
		"title": "甲职位",
		"index": 1,
		"index_snapshot": {"source": "search:golang", "page": 1, "count": 1, "saved_at": _KNOWN_TS},
	})

	expected = datetime.datetime.fromtimestamp(_KNOWN_TS).strftime("%Y-%m-%d %H:%M")
	assert f"保存于 {expected}" in stream.getvalue()


@pytest.mark.parametrize(
	("raw", "expected"),
	[
		(True, 0.0),
		(False, 0.0),
		(None, 0.0),
		([1], 0.0),
		(-1, 0.0),
		(float("inf"), 0.0),
		(10**400, 0.0),
		(_KNOWN_TS, float(_KNOWN_TS)),
		(str(_KNOWN_TS), float(_KNOWN_TS)),
	],
	ids=["true", "false", "none", "list", "negative", "inf", "huge", "int", "numeric-str"],
)
def test_finite_timestamp(raw: Any, expected: float) -> None:
	assert cdp_risk_lock._finite_timestamp(raw) == expected


def _write_lock(data_dir: Path, locked_at: Any) -> None:
	cdp_risk_lock.lock_path(data_dir).write_text(
		json.dumps({"version": 1, "reason": "ENVIRONMENT_RISK", "platform_code": 37, "stoken_sha256": None, "locked_at": locked_at}),
		encoding="utf-8",
	)


@pytest.mark.parametrize("locked_at", [True, False, None, [1]], ids=["true", "false", "null", "list"])
def test_lock_status_reports_unknown_time_instead_of_epoch(tmp_path: Path, locked_at: Any) -> None:
	_write_lock(tmp_path, locked_at)

	status = cdp_risk_lock.lock_status(tmp_path, now=_KNOWN_TS + 100)

	assert status["locked"] is True
	assert status["corrupt"] is False
	assert "locked_at" not in status
	assert "age_seconds" not in status


def test_lock_status_keeps_real_time(tmp_path: Path) -> None:
	_write_lock(tmp_path, _KNOWN_TS)

	status = cdp_risk_lock.lock_status(tmp_path, now=_KNOWN_TS + 100)

	assert status["locked_at"] == datetime.datetime.fromtimestamp(_KNOWN_TS).strftime("%Y-%m-%d %H:%M:%S")
	assert status["age_seconds"] == 100


def test_lock_with_boolean_time_still_blocks(tmp_path: Path) -> None:
	"""只改诊断时间：锁的阻断语义不变，仍在传输构造前拒绝。"""
	_write_lock(tmp_path, True)

	with pytest.raises(PlatformRiskError):
		cdp_risk_lock.raise_if_locked(tmp_path)


def test_doctor_lock_detail_says_unknown_time(tmp_path: Path) -> None:
	_write_lock(tmp_path, True)

	payload = _risk_lock_check_payload(cdp_risk_lock.lock_status(tmp_path))

	assert payload["status"] == "warn"
	assert "未知时间" in payload["detail"]
	assert "1970" not in payload["detail"]


@patch("boss_agent_cli.commands.status.AuthManager")
def test_status_envelope_omits_epoch_time_for_boolean_locked_at(mock_auth_cls: Any, tmp_path: Path) -> None:
	mock_auth_cls.return_value.check_status.return_value = _SYNTHETIC_TOKEN
	_write_lock(tmp_path, True)

	exit_code, envelope = _json(tmp_path, "status")

	assert exit_code == 0
	lock = envelope["data"]["cdp_risk_lock"]
	assert lock["locked"] is True
	assert "locked_at" not in lock
	assert "age_seconds" not in lock


# ── #2757：请求间隔的写法与排障指引 ───────────────────────────────────


@pytest.mark.parametrize(
	"raw",
	["60,120", "60, 120", "[60, 120]", "[60.0,120.0]", " [ 60 , 120 ] ", "[\n  60,\n  120\n]"],
	ids=["comma", "comma-space", "array", "array-compact", "array-padded", "array-multiline"],
)
def test_parse_value_accepts_comma_and_array_forms(raw: str) -> None:
	assert _parse_value("request_delay", raw, DEFAULTS["request_delay"]) == [60.0, 120.0]


@pytest.mark.parametrize("key", ["request_delay", "batch_greet_delay"])
def test_displayed_range_can_be_written_back(key: str, tmp_path: Path) -> None:
	"""``config get`` 展示什么就能写回什么：Agent 原样回写不再是 INVALID_PARAM。"""
	exit_code, envelope = _json(tmp_path, "config", "get", key)
	assert exit_code == 0
	displayed = json.dumps(envelope["data"]["value"])

	exit_code, envelope = _json(tmp_path, "config", "set", key, displayed)

	assert exit_code == 0, envelope
	assert envelope["data"]["value"] == DEFAULTS[key]
	assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {key: DEFAULTS[key]}


@pytest.mark.parametrize(
	"raw",
	["abc", "[60, abc]", "[60; 120]", "[]", "[", "[60,120", "60,120]", "[[60,120]]", "60,,120"],
	ids=["text", "bad-element", "semicolon", "empty-array", "open-only", "unclosed", "unopened", "nested", "empty-element"],
)
def test_config_set_rejects_unparseable_range_without_writing(raw: str, tmp_path: Path) -> None:
	exit_code, envelope = _json(tmp_path, "config", "set", "request_delay", raw)

	assert exit_code == 1
	assert envelope["error"]["code"] == "INVALID_PARAM"
	message = envelope["error"]["message"]
	assert "request_delay" in message
	assert repr(raw) in message
	# 示例取默认值，两种写法都给：不再出现低于 doctor 告警线的 1.5,3.0。
	assert "'30,60'" in message
	assert "'[30, 60]'" in message
	assert "1.5" not in message
	assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize(
	"raw",
	["[60]", "[120, 60]", "[60, 120, 180]", "[nan, 60]", "[1e400, 2e400]", "[-5, 60]"],
	ids=["one-element", "reversed", "three-elements", "nan", "overflow", "negative"],
)
def test_config_set_array_form_is_still_range_validated(raw: str, tmp_path: Path) -> None:
	"""数组写法只是另一种输入形式：元素个数与取值范围照旧由校验把关。"""
	exit_code, envelope = _json(tmp_path, "config", "set", "request_delay", raw)

	assert exit_code == 1
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "request_delay 必须包含两个有限数值" in envelope["error"]["message"]
	assert not (tmp_path / "config.json").exists()


def test_range_error_example_uses_each_settings_own_default() -> None:
	with pytest.raises(ValueError) as raised:
		_parse_value("batch_greet_delay", "abc", DEFAULTS["batch_greet_delay"])

	assert "'2,5' 或 '[2, 5]'" in str(raised.value)


def _rate_limited_commands(doc_name: str) -> list[list[str]]:
	"""排障文档 ``RATE_LIMITED`` 一节代码块里的 boss 命令。"""
	text = (_DOCS / doc_name).read_text(encoding="utf-8")
	start = text.index("### `RATE_LIMITED`")
	section = text[start : text.index("\n### ", start + 1)]
	return [shlex.split(line) for line in section.splitlines() if line.startswith("boss ")]


@pytest.mark.parametrize("doc_name", ["troubleshooting.md", "troubleshooting.en.md"])
def test_rate_limited_guide_raises_the_interval_above_the_default(doc_name: str, tmp_path: Path) -> None:
	"""被限流后照文档操作必须是放慢，而不是把请求频率提高一个数量级。"""
	commands = _rate_limited_commands(doc_name)
	delay_command = next(argv for argv in commands if "--delay" in argv)
	config_command = next(argv for argv in commands if argv[1:4] == ["config", "set", "request_delay"])

	low, high = (float(part) for part in delay_command[delay_command.index("--delay") + 1].split("-"))
	assert low >= _RISK_FREE_DELAY_FLOOR
	assert (low, high) > DEFAULT_REQUEST_DELAY

	# 全局选项本身能通过校验（用本地命令验证，不执行文档里的 search）。
	exit_code, envelope = _json(tmp_path, "--delay", f"{low:g}-{high:g}", "config", "get", "request_delay")
	assert exit_code == 0, envelope

	# 文档里的全局配置命令照抄即可执行，写入的同样是高于默认值的区间。
	exit_code, envelope = _json(tmp_path, *config_command[1:])
	assert exit_code == 0, envelope
	written = envelope["data"]["value"]
	assert written[0] >= _RISK_FREE_DELAY_FLOOR
	assert tuple(written) > DEFAULT_REQUEST_DELAY


def test_rate_limited_guide_commands_match_across_languages() -> None:
	assert _rate_limited_commands("troubleshooting.md") == _rate_limited_commands("troubleshooting.en.md")
