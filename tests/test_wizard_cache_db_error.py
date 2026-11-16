"""wizard 遇到缓存库损坏时与 CLI 同口径报 CACHE_DATABASE_INVALID（Issue #1362）。

缓存库坏页 / 非 SQLite 内容是确定性本地失败：runner 的通用兜底原来把它映射成
可重试的 NETWORK_ERROR +「重试当前 run_id」，Agent 会在同一个坏库上无限重试。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.cache.store import CacheDatabaseInvalid
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.renderer import ERROR_LABELS, RECOVERY_LABELS


def _invoke_wizard(tmp_path, goal: str, inputs: dict) -> tuple[int, dict]:
	payload = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": goal,
		"inputs": inputs,
	}
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "wizard", "--input-json", json.dumps(payload)],
	)
	return result.exit_code, json.loads(result.stdout)


def _seed_login(tmp_path) -> None:
	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent"}
	)


def test_wizard_maps_cache_database_invalid_to_its_own_code(tmp_path, monkeypatch):
	_seed_login(tmp_path)
	path = tmp_path / "cache" / "boss_agent.db"

	class _Boom:
		def resume(self, *args, **kwargs):
			raise CacheDatabaseInvalid(path, "database disk image is malformed")

	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions._crawl_service",
		lambda context, cache: _Boom(),
	)

	exit_code, payload = _invoke_wizard(tmp_path, "crawl_resume", {"run_id": "run-1"})

	assert exit_code == 1, payload
	error = payload["error"]
	assert error["code"] == "CACHE_DATABASE_INVALID"
	# 登记表：备份后删库重建，不是重试同一条 run_id。
	assert "备份" in error["recovery_action"]
	assert "删除" in error["recovery_action"]
	assert "NETWORK_ERROR" not in json.dumps(payload, ensure_ascii=False)


def test_cache_database_invalid_has_tty_labels():
	assert ERROR_LABELS["CACHE_DATABASE_INVALID"]
	assert "boss_agent.db" in RECOVERY_LABELS["CACHE_DATABASE_INVALID"]
