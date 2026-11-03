"""未登录的调用不能占用共享请求预算。

请求预算在 `_request` 的预算窗口内才读取凭据，于是没有任何凭据、根本不会发出
请求的调用也会记一个名额，并把 `next_at` 推后一个 interval（默认 `request_delay`
下限 30 秒）。紧接着的下一条命令会先阻塞到 `request_wait_timeout` 超时，再以
`REQUEST_BUDGET_EXHAUSTED`「请求节流等待超时，请求未发出」失败，掩盖真正的
`AUTH_REQUIRED`——用户看到的既不是正确原因，也要白等半分钟。
"""

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _error_code(result) -> str:
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	return parsed["error"]["code"]


def _attempted_requests(tmp_path: Path) -> int:
	"""读取本机预算库里已经登记的请求名额数；库或表不存在时按 0 计。"""
	db = tmp_path / "request-budget.sqlite3"
	if not db.exists():
		return 0
	conn = sqlite3.connect(str(db))
	try:
		row = conn.execute("SELECT COUNT(*) FROM request_attempts").fetchone()
	except sqlite3.OperationalError:
		return 0
	finally:
		conn.close()
	return int(row[0])


def test_unauthenticated_request_does_not_book_budget_slot(tmp_path):
	"""没有登录态时请求不会发出，不能占用名额、把下一条命令卡在冷却里。"""
	result = _invoke(tmp_path, "pipeline")

	assert _error_code(result) == "AUTH_REQUIRED"
	assert _attempted_requests(tmp_path) == 0


def test_next_command_still_reports_auth_required_not_budget(tmp_path):
	"""上一条未登录命令之后，下一条仍应立刻报 AUTH_REQUIRED。"""
	assert _error_code(_invoke(tmp_path, "pipeline")) == "AUTH_REQUIRED"

	result = _invoke(tmp_path, "digest")

	assert _error_code(result) == "AUTH_REQUIRED"
