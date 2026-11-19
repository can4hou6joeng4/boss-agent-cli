"""watch / shortlist 的四项边界（Issue #1797 #1798 #1799 #1800）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import boss_command, positional_hint, shell_argument
from boss_agent_cli.main import cli


def _invoke(argv: list[str]) -> object:
	from click.testing import Result

	result: Result = CliRunner().invoke(cli, argv)
	payload = json.loads(result.output) if result.output.strip() else {}
	return payload


def _seed_watch(tmp_path: Path, name: str, params: object) -> None:
	db_path = tmp_path / "cache" / "boss_agent.db"
	# 先用 CacheStore 建表，再注入漂移记录：直接 sqlite3 建的空库没有 schema。
	with CacheStore(db_path) as cache:
		cache.save_saved_search(name, {"query": "seed"})
	conn = sqlite3.connect(db_path)
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", (json.dumps(params), name))
	conn.commit()
	conn.close()


# ── #1797：positional_hint 与后继命令 ────────────────────────


def test_positional_hint_quotes_leading_dash():
	assert positional_hint("-x") == "-- -x"
	assert positional_hint("正常名") == "正常名"
	assert positional_hint("我的 监控") == shell_argument("我的 监控")


def test_watch_add_hint_runs_leading_dash_name(tmp_path):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "--", "-x", "python"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert "watch run -- -x" in payload["hints"]["next_actions"][0]


def test_shortlist_add_hint_removes_leading_dash_ids(tmp_path):
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "add", "--", "-s1", "-j1"]
	)
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert "shortlist remove -- -s1 -j1" in payload["hints"]["next_actions"][1]


def test_hint_with_leading_dash_name_actually_runs(tmp_path):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "--", "-x", "python"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	hint = payload["hints"]["next_actions"][0]
	assert hint.startswith("boss ")
	replayed = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", *hint.removeprefix("boss ").split()],
	)
	# 未登录：能走到 AUTH_REQUIRED 就说明名字没被当成未知选项。
	replayed_payload = json.loads(replayed.output)
	assert replayed_payload["error"]["code"] == "AUTH_REQUIRED"
	assert "没有选项" not in replayed_payload["error"]["message"]


# ── #1798：query 缺失 / null / 空白的存量记录 ────────────────


@pytest.mark.parametrize("params", [{"query": None}, {}, {"query": ""}, {"query": "   "}])
def test_drifted_query_is_invalid_not_a_blank_search(tmp_path, params):
	_seed_watch(tmp_path, "wq", params)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "wq"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert "关键词不能为空" in payload["error"]["message"]
	assert any("watch remove wq" in action for action in payload["hints"]["next_actions"])


def test_valid_saved_watch_still_runs(tmp_path):
	_seed_watch(tmp_path, "ok-watch", {"query": "python"})
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "ok-watch"])
	# 未登录：参数校验通过后停在 AUTH_REQUIRED，而不是 SAVED_SEARCH_INVALID。
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTH_REQUIRED"


# ── #1799：tags 的 BLOB 容错 ─────────────────────────────────


def test_blob_tags_degrade_to_empty(tmp_path):
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.add_shortlist({"security_id": "s1", "job_id": "j1", "title": "t1", "company": "c1", "city": "ci", "salary": "20k", "source": "manual"})
	conn = sqlite3.connect(db_path)
	conn.execute(
		"INSERT INTO shortlist_records (security_id, job_id, title, company, city, salary, source, tags, note, lid, created_at)"
		" VALUES ('s3', 'j3', 't', 'c', 'ci', '20k', 'manual', X'00FF', 'n', '', '1')"
	)
	conn.commit()
	conn.close()
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is True
	items = payload["data"]["shortlist"] if isinstance(payload["data"], dict) and "shortlist" in payload["data"] else payload["data"]
	drifted = [item for item in items if item["security_id"] == "s3"]
	assert drifted, items
	assert not drifted[0].get("tags")
	# 好行的标注不被坏行锁死。
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "annotate", "s1", "j1", "--add-tag", "new"]
	)
	assert result.exit_code == 0, result.output


def test_undecodable_blob_tags_degrade_to_empty():
	assert CacheStore._deserialize_shortlist_tags(b"\x00\xff") == []
	assert CacheStore._deserialize_shortlist_tags(b'["a","b"]') == ["a", "b"]
	assert CacheStore._deserialize_shortlist_tags(None) == []


# ── #1800：watch run 的纯空白名 ──────────────────────────────


def test_watch_run_blank_name_is_invalid_param(tmp_path):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "  "])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "watch 名称不能为空"
	assert payload["hints"]["next_actions"] == [boss_command("watch list", data_dir=tmp_path)]


def test_watch_run_all_still_works(tmp_path):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "--all"])
	payload = json.loads(result.output)
	assert payload["ok"] is True or payload["error"]["code"] in {"AUTH_REQUIRED", "NO_SAVED_SEARCHES"}
