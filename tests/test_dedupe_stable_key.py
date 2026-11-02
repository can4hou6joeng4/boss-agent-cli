"""greet/apply 去重键：账号指纹 + 稳定的 encryptJobId，而不是轮换的 securityId。

securityId 在每次搜索、推荐、列表请求里都会换发新值，以它为主键时，
第二次请求查不到上次写下的记录，同一职位会被重复打招呼、重复投递。
只用 encryptJobId 又会让同一台机器上的另一个账号误命中，所以两者
必须同时带账号作用域（``AuthManager.account_fingerprint``）。
"""

import json
import sqlite3
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


@pytest.fixture()
def store(tmp_path):
	s = CacheStore(tmp_path / "cache" / "test.db")
	yield s
	s.close()


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	return instance


def _raw_job(security_id: str, job_id: str = "job_1") -> dict:
	return {
		"encryptJobId": job_id,
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"salaryDesc": "20K",
		"cityName": "北京",
		"areaDistrict": "海淀区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Golang"],
		"welfareList": [],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": security_id,
	}


# ── CacheStore：账号作用域 + 稳定键 ─────────────────────────────────


def test_greet_dedupe_ignores_rotated_security_id_for_same_account(store):
	store.record_greet("sec_first", "job_1", account="acct_a")
	assert store.is_greeted("sec_first", job_id="job_1", account="acct_a") is True
	# 下一次请求换发了新的 securityId，账号和职位还是同一个
	assert store.is_greeted("sec_second", job_id="job_1", account="acct_a") is True
	assert store.is_greeted("sec_second", job_id="job_2", account="acct_a") is False


def test_greet_dedupe_does_not_cross_accounts(store):
	store.record_greet("sec_first", "job_1", account="acct_a")
	assert store.is_greeted("sec_second", job_id="job_1", account="acct_b") is False
	# 即使 securityId 恰好相同，也不能把 A 账号的记录算到 B 头上
	assert store.is_greeted("sec_first", job_id="job_1", account="acct_b") is False


def test_greet_without_account_falls_back_to_security_id(store):
	store.record_greet("sec_first", "job_1")
	assert store.is_greeted("sec_first") is True
	assert store.is_greeted("sec_second") is False
	assert store.is_greeted("") is False


def test_greet_without_job_id_falls_back_to_security_id(store):
	store.record_greet("sec_first", "job_1", account="acct_a")
	assert store.is_greeted("sec_first", account="acct_a") is True
	assert store.is_greeted("sec_second", account="acct_a") is False


def test_apply_dedupe_ignores_rotated_security_id_for_same_account(store):
	store.record_apply("sec_first", "job_1", account="acct_a")
	assert store.is_applied("sec_first", "job_1", account="acct_a") is True
	assert store.is_applied("sec_second", "job_1", account="acct_a") is True
	assert store.is_applied("sec_second", "job_2", account="acct_a") is False


def test_apply_dedupe_does_not_cross_accounts(store):
	store.record_apply("sec_first", "job_1", account="acct_a")
	assert store.is_applied("sec_second", "job_1", account="acct_b") is False


def test_apply_without_account_falls_back_to_security_id(store):
	store.record_apply("sec_first", "job_1")
	assert store.is_applied("sec_first", "") is True
	assert store.is_applied("sec_second", "") is False


def test_reopening_store_keeps_account_scope(tmp_path):
	"""迁移是幂等的：再次打开不能把已写入的账号指纹抹回空串。"""
	db = tmp_path / "cache" / "dedupe.db"
	with CacheStore(db) as cache:
		cache.record_greet("sec_first", "job_1", account="acct_a")
		cache.record_apply("sec_first", "job_1", account="acct_a")
	with CacheStore(db) as cache:
		assert cache.is_greeted("sec_second", job_id="job_1", account="acct_a") is True
		assert cache.is_applied("sec_second", "job_1", account="acct_a") is True
		assert cache._conn.execute("SELECT account FROM greet_records").fetchone()[0] == "acct_a"
		assert cache._conn.execute("SELECT account FROM apply_records").fetchone()[0] == "acct_a"


# ── 旧库迁移 ─────────────────────────────────────────────────────────


def test_legacy_tables_migrate_without_attributing_rows_to_an_account(tmp_path):
	db = tmp_path / "cache" / "legacy.db"
	db.parent.mkdir(parents=True)
	conn = sqlite3.connect(str(db))
	conn.executescript("""
		CREATE TABLE greet_records (
			security_id TEXT PRIMARY KEY,
			job_id TEXT NOT NULL,
			greeted_at REAL NOT NULL
		);
		CREATE TABLE apply_records (
			security_id TEXT NOT NULL,
			job_id TEXT NOT NULL,
			applied_at REAL NOT NULL,
			PRIMARY KEY (security_id, job_id)
		);
	""")
	conn.execute("INSERT INTO greet_records VALUES ('old_sec', 'job_1', 1.0)")
	conn.execute("INSERT INTO apply_records VALUES ('old_sec', 'job_1', 1.0)")
	conn.commit()
	conn.close()

	store = CacheStore(db)
	try:
		# 旧记录没有账号归属：仍能按旧 securityId 查到，但不冒充当前账号的去重依据
		assert store.is_greeted("old_sec") is True
		assert store.is_applied("old_sec", "job_1") is True
		assert store.is_greeted("old_sec", job_id="job_1", account="acct_a") is False
		assert store.is_applied("old_sec", "job_1", account="acct_a") is False
		assert store.get_job_id("old_sec") == "job_1"
		assert store._conn.execute("SELECT COUNT(*) FROM greet_records").fetchone()[0] == 1
		assert store._conn.execute("SELECT COUNT(*) FROM apply_records").fetchone()[0] == 1
	finally:
		store.close()

	# 再次打开不应重复迁移，数据仍在
	store = CacheStore(db)
	try:
		assert store.is_greeted("old_sec") is True
		assert store.is_applied("old_sec", "job_1") is True
	finally:
		store.close()


def test_legacy_migration_drops_rows_without_job_id(tmp_path):
	db = tmp_path / "cache" / "legacy.db"
	db.parent.mkdir(parents=True)
	conn = sqlite3.connect(str(db))
	conn.execute(
		"CREATE TABLE greet_records (security_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, greeted_at REAL NOT NULL)"
	)
	conn.execute("INSERT INTO greet_records VALUES ('sec', '', 1.0)")
	conn.commit()
	conn.close()

	store = CacheStore(db)
	try:
		assert store.is_greeted("sec") is False
	finally:
		store.close()


# ── 命令层：跨请求不重复，跨账号不误拦 ───────────────────────────────


@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
def test_batch_greet_does_not_re_greet_after_security_id_rotation(mock_auth, mock_platform, legacy_args):
	mock_auth.return_value.account_fingerprint.return_value = "acct_a"
	mock_client = _ctx_mock(mock_platform)
	mock_client.name = "zhipin"
	mock_client.greet.return_value = {"code": 0, "zpData": {}}
	mock_client.is_success.return_value = True

	runner = CliRunner()

	def run(security_id: str) -> dict:
		mock_client.search_jobs.return_value = {"zpData": {"jobList": [_raw_job(security_id)]}}
		return json.loads(runner.invoke(cli, [*legacy_args, "batch-greet", "go", "--count", "5"]).output)

	first = run("sec_first")
	assert first["data"]["total_greeted"] == 1

	# 平台在第二次搜索里换发了新的 securityId；同一账号的同一职位不应再打招呼
	second = run("sec_second")
	assert second["data"]["total_greeted"] == 0
	assert mock_client.greet.call_count == 1

	# 另一个账号在同一台机器上搜索，不应被上个账号的记录误拦
	mock_auth.return_value.account_fingerprint.return_value = "acct_b"
	third = run("sec_third")
	assert third["data"]["total_greeted"] == 1
	assert mock_client.greet.call_count == 2


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
def test_apply_rejects_same_job_after_security_id_rotation(mock_auth, mock_platform, legacy_args):
	mock_auth.return_value.account_fingerprint.return_value = "acct_a"
	mock_client = _ctx_mock(mock_platform)
	mock_client.apply.return_value = {"code": 0, "zpData": {}}
	mock_client.is_success.return_value = True

	runner = CliRunner()
	first = runner.invoke(cli, [*legacy_args, "apply", "sec_first", "job_1"])
	assert first.exit_code == 0

	second = runner.invoke(cli, [*legacy_args, "apply", "sec_second", "job_1"])
	payload = json.loads(second.output)
	assert payload["error"]["code"] == "ALREADY_APPLIED"
	assert mock_client.apply.call_count == 1

	# 换一个账号投同一职位是正常操作，不能被本地记录拦下
	mock_auth.return_value.account_fingerprint.return_value = "acct_b"
	third = runner.invoke(cli, [*legacy_args, "apply", "sec_third", "job_1"])
	assert third.exit_code == 0
	assert mock_client.apply.call_count == 2
