"""clean 与 agent 后继提示、风险锁清理与部分失败的回归测试（Issue #1826–#1831）。"""

import json
import shlex
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.api.cdp_risk_lock import read_lock, write_lock
from boss_agent_cli.commands.agent import _public_fit_results
from boss_agent_cli.main import cli


def _invoke(*args: str, data_dir: Path):
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])
	return result.exit_code, json.loads(result.output)


# ── Issue #1826：以 - 开头的简历名，删除后继命令要有 -- 保护 ─────────────


def test_agent_resume_delete_hint_guards_dash_name(tmp_path: Path) -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir(parents=True)
	(resumes / "-draft.json").write_text("{bad json", encoding="utf-8")

	code, payload = _invoke("agent", "crawl", "--run-id", "run-1", "--resume", "-draft", data_dir=tmp_path)

	assert code == 1, payload
	assert payload["error"]["code"] == "RESUME_FILE_INVALID"
	delete_cmd = payload["hints"]["next_actions"][0]
	assert shlex.split(delete_cmd) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "resume", "delete", "--", "-draft",
	]
	assert f"或执行 {delete_cmd} 后重新创建" in payload["error"]["recovery_action"]


# ── Issue #1827：fit 结果缺 title 时不能 KeyError ───────────────────────


def test_public_fit_results_missing_title_with_empty_job_title() -> None:
	"""职位标题为空串时 selectors_by_title 含 "" 键，缺 title 的结果不能炸。"""
	jobs = [{"selector": "job-1", "title": ""}, {"selector": "job-2", "title": "后端"}]
	results = [
		{"score": 90},
		{"score": 80, "title": ""},
		{"score": 70, "title": "后端"},
		{"score": 60, "selector": "job-1"},
	]

	public = _public_fit_results(results, jobs)

	assert public[0] == {"score": 90}
	assert "selector" not in public[1]
	assert public[2]["selector"] == "job-2"
	assert public[3]["selector"] == "job-1"


# ── Issue #1828：clean 提示携带解析后的数据目录 ──────────────────────────


def test_clean_hint_resolves_relative_data_dir(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.chdir(tmp_path)
	relative = Path("rel-data")
	relative.mkdir()

	result = CliRunner().invoke(cli, ["--data-dir", str(relative), "--json", "clean"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert shlex.split(payload["hints"]["next_actions"][0]) == [
		"boss", "--data-dir", str(relative.resolve()), "doctor",
	]


# ── Issue #1829：悬空链接的锁要删除，local_lock_removed 以读取为准 ──────


def test_clean_risk_lock_removes_dangling_symlink(tmp_path: Path) -> None:
	dangling = tmp_path / "cdp_risk_lock.json"
	dangling.symlink_to(tmp_path / "missing-target.json")

	code, payload = _invoke("clean", "--risk-lock", data_dir=tmp_path)

	assert code == 0, payload
	assert payload["data"]["risk_lock"]["local_lock_removed"] is True
	assert not dangling.is_symlink()
	assert read_lock(tmp_path) is None


def test_clean_risk_lock_reports_when_lock_survives(tmp_path: Path) -> None:
	"""后备锁路径经由符号链接目录时跳过不删：必须如实报告锁仍在。"""
	outside = tmp_path / "outside"
	outside.mkdir()
	write_lock(outside, None)
	(tmp_path / "cache").symlink_to(outside, target_is_directory=True)

	code, payload = _invoke("clean", "--risk-lock", data_dir=tmp_path)

	assert code == 0, payload
	assert payload["data"]["risk_lock"]["local_lock_removed"] is False
	assert read_lock(tmp_path) is not None
	assert any("未能完全清除" in item for item in payload["hints"]["operator_actions"])


def test_clean_risk_lock_without_lock_reports_not_removed(tmp_path: Path) -> None:
	"""没有锁的目录空转仍报 false：保持文档里「数据目录选错」的探测器语义。"""
	code, payload = _invoke("clean", "--risk-lock", data_dir=tmp_path)

	assert code == 0, payload
	assert payload["data"]["risk_lock"]["local_lock_removed"] is False


def test_clean_risk_lock_not_reported_removed_on_dry_run(tmp_path: Path) -> None:
	write_lock(tmp_path, None)

	code, payload = _invoke("clean", "--risk-lock", "--dry-run", data_dir=tmp_path)

	assert code == 0, payload
	assert payload["data"]["risk_lock"]["local_lock_removed"] is False
	assert read_lock(tmp_path) is not None


# ── Issue #1830 / #1831：中途失败时带回已完成项与锁状态 ─────────────────


def test_clean_risk_lock_partial_failure_reports_lock_removed(tmp_path: Path) -> None:
	dangling = tmp_path / "cdp_risk_lock.json"
	dangling.symlink_to(tmp_path / "missing-target.json")
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir()
	(cache_dir / "boss_agent.db").write_bytes(b"not a sqlite database")

	code, payload = _invoke("clean", "--risk-lock", data_dir=tmp_path)

	assert code == 1, payload
	assert payload["error"]["code"] == "CACHE_DATABASE_INVALID"
	assert payload["data"]["risk_lock"]["local_lock_removed"] is True
	assert payload["data"]["results"][0]["target"] == "CDP 风控锁"
	assert "风控锁此前已清除" in payload["error"]["message"]
	assert read_lock(tmp_path) is None


def test_clean_partial_failure_lists_completed_items(tmp_path: Path) -> None:
	"""聊天快照目录被同名普通文件占位：失败信封要带回此前已完成项。"""
	(tmp_path / "chat-history").write_text("occupied", encoding="utf-8")

	code, payload = _invoke("clean", data_dir=tmp_path)

	assert code == 1, payload
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert [item["target"] for item in payload["data"]["results"]] == ["搜索缓存", "索引缓存"]
	assert payload["data"]["failed"][0]["target"] == "聊天快照"
	assert payload["data"]["failed"][0]["reason"]


# ── Issue #1832：以 - 开头的预设名，preset 后继命令要有 -- 保护 ─────────


def test_search_preset_not_found_hint_guards_dash_name(tmp_path: Path) -> None:
	code, payload = _invoke("search", "--preset", "-draft", data_dir=tmp_path)

	assert code == 1, payload
	assert payload["error"]["code"] == "SAVED_SEARCH_NOT_FOUND"
	add_hint = payload["hints"]["next_actions"][1]
	assert shlex.split(add_hint) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "preset", "add", "--", "-draft", "<关键词>",
	]


def test_search_preset_invalid_params_hints_guard_dash_name(tmp_path: Path) -> None:
	import sqlite3

	from boss_agent_cli.cache.store import CacheStore

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search("-draft", {"query": "AI"})
	# 参数列写成坏 JSON：get_saved_search 报 SAVED_SEARCH_INVALID。
	with sqlite3.connect(str(tmp_path / "cache" / "boss_agent.db")) as conn:
		conn.execute("UPDATE saved_searches SET params = '{bad' WHERE name = '-draft'")

	code, payload = _invoke("search", "--preset", "-draft", data_dir=tmp_path)

	assert code == 1, payload
	assert payload["error"]["code"] == "SAVED_SEARCH_INVALID"
	remove_hint, add_hint = payload["hints"]["next_actions"]
	prefix = ["boss", "--data-dir", str(tmp_path.resolve()), "preset"]
	assert shlex.split(remove_hint) == [*prefix, "remove", "--", "-draft"]
	assert shlex.split(add_hint) == [*prefix, "add", "--", "-draft", "<关键词>"]
