"""watch active 校验、shortlist 多位置参数与导出严格 JSON（Issue #1847 #1848 #1851 #1853）。"""

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import positional_args
from boss_agent_cli.main import cli
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.job_export import render_html_export, write_export_file
from boss_agent_cli.services.json_files import strict_json_text, write_json_atomic


def _seed_watch(tmp_path: Path, name: str, params: dict) -> None:
	db_path = tmp_path / "cache" / "boss_agent.db"
	with CacheStore(db_path) as cache:
		cache.save_saved_search(name, {"query": "seed"})
	with sqlite3.connect(db_path) as conn:
		conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", (json.dumps(params), name))


# ── Issue #1847：存量 watch 的 active 取值 ────────────────────────────


def test_drifted_active_is_invalid_not_network_error(tmp_path: Path) -> None:
	_seed_watch(tmp_path, "wa", {"query": "python", "active": "bogus"})

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "wa"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert "活跃度" in payload["error"]["message"]
	assert any("watch remove" in action for action in payload["hints"]["next_actions"])


def test_drifted_active_type_is_invalid(tmp_path: Path) -> None:
	_seed_watch(tmp_path, "wa", {"query": "python", "active": ["3day"]})

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "wa"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert "active" in payload["error"]["message"]


# ── Issue #1848：多位置参数只加一次 -- ────────────────────────────────


def test_positional_args_adds_single_double_dash() -> None:
	assert positional_args("-a", "b") == "-- -a b"
	assert positional_args("a", "-b") == "-- a -b"
	assert positional_args("a", "b") == "a b"
	assert positional_args("-a", "-b") == "-- -a -b"
	assert positional_args("我的 名字", "j1") == "'我的 名字' j1"


def test_shortlist_double_dash_hint_single_prefix(tmp_path: Path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "add", "--", "-s1", "-j1"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	remove_hint = payload["hints"]["next_actions"][1]
	assert "shortlist remove -- -s1 -j1" in remove_hint
	assert "-- -s1 -- -j1" not in remove_hint

	replayed = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", *remove_hint.removeprefix("boss ").split()]
	)
	replayed_payload = json.loads(replayed.output)
	assert replayed_payload["ok"] is True, replayed_payload
	assert replayed_payload["data"]["removed"] is True


# ── Issue #1851：导出与本地状态不写裸 Infinity / NaN ──────────────────


def test_chat_export_json_is_strict() -> None:
	text = render_export(
		[{"name": "x", "score": float("inf"), "last_time": float("nan")}],
		"json",
		None,
		None,
		{"is_first": True},
	)

	assert "Infinity" not in text
	assert "NaN" not in text
	assert json.loads(text)[0]["score"] is None
	assert json.loads(text)[0]["last_time"] is None


def test_write_json_atomic_normalizes_non_finite(tmp_path: Path) -> None:
	path = tmp_path / "state.json"

	write_json_atomic(path, {"score": float("inf"), "nested": [float("-inf")]})

	loaded = json.loads(path.read_text(encoding="utf-8"))
	assert loaded == {"score": None, "nested": [None]}
	assert "Infinity" not in path.read_text(encoding="utf-8")


def test_strict_json_text_forbids_non_finite() -> None:
	assert json.loads(strict_json_text({"score": float("nan")})) == {"score": None}


# ── Issue #1853：服务层类型漂移与静默失败 ────────────────────────────


def _friend(**overrides: object) -> dict:
	base = {
		"name": "x",
		"brand_name": "c",
		"title": "t",
		"last_time": "2026-11-19",
		"msg_status": "已读",
		"last_msg": "hi",
		"initiated_by": "我主动",
	}
	base.update(overrides)
	return base


def test_chat_export_md_ignores_non_int_unread() -> None:
	text = render_export([_friend(unread="3")], "md", None, None, {"is_first": True})

	assert "S1" in text


def test_chat_export_text_formats_render_containers_as_json() -> None:
	friends = [_friend(last_msg={"text": "你好"})]

	for fmt in ("csv", "md", "html"):
		text = render_export(friends, fmt, None, None, {"is_first": True})
		assert "{'text'" not in text, fmt


def test_job_export_html_does_not_render_none_cells() -> None:
	text = render_html_export([{"title": "T", "skills": None, "welfare": None}])

	assert "<td>None</td>" not in text


def test_write_export_file_rejects_unknown_format(tmp_path: Path) -> None:
	with pytest.raises(ValueError):
		write_export_file([{"title": "T"}], "md", str(tmp_path / "out.md"))
