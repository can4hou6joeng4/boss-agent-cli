"""本轮审计修复：下载简历的错误分类、会话完整性判定、导出中断回滚。

- Issue #2271：`hr download-resume` 把请求预算库故障兜成可重试的 NETWORK_ERROR；
- Issue #2272：`history_self_message_state` 用过滤后的条数判断整页是否拿全；
- Issue #2274：`crawl` 导出被 Ctrl-C 打断时不回滚、不清理临时文件。
"""

import json
import os
import sqlite3
from pathlib import Path

from click.testing import CliRunner
import pytest

from boss_agent_cli.api.recruiter_resume import history_self_message_state, sent_by_self
from boss_agent_cli.crawler import exporter
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.main import cli

_DOWNLOAD_MODULE = "boss_agent_cli.commands.recruiter.download_resume"


class _FakeAuth:
	def __init__(self, *args, **kwargs):
		pass

	def get_token(self):
		return "token"


class _FakePlatform:
	def __init__(self, error: BaseException):
		self._error = error

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def download_resume_by_friend(self, *args, **kwargs):
		raise self._error


def _invoke_download(tmp_path: Path):
	return CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "hr", "download-resume", "123",
			"--message-id", "456", "--output", str(tmp_path / "resume.pdf")],
	)


def test_budget_database_error_is_local_not_network(tmp_path, monkeypatch):
	error = sqlite3.DatabaseError(f"file is not a database: {tmp_path}/request-budget.sqlite3")
	monkeypatch.setattr(f"{_DOWNLOAD_MODULE}.AuthManager", _FakeAuth)
	monkeypatch.setattr(
		f"{_DOWNLOAD_MODULE}.get_recruiter_platform_instance",
		lambda *args, **kwargs: _FakePlatform(error),
	)

	result = _invoke_download(tmp_path)

	assert result.exit_code == 1, result.output
	rejected = json.loads(result.output)["error"]
	assert rejected["code"] == "LOCAL_IO_ERROR"
	assert rejected["recoverable"] is False
	assert "request-budget.sqlite3" in rejected["recovery_action"]


def test_other_platform_errors_still_report_network(tmp_path, monkeypatch):
	monkeypatch.setattr(f"{_DOWNLOAD_MODULE}.AuthManager", _FakeAuth)
	monkeypatch.setattr(
		f"{_DOWNLOAD_MODULE}.get_recruiter_platform_instance",
		lambda *args, **kwargs: _FakePlatform(RuntimeError("unexpected")),
	)

	result = _invoke_download(tmp_path)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "NETWORK_ERROR"


def _message(mid: int, uid: int) -> dict:
	return {"mid": mid, "from": {"uid": uid}, "type": 1, "text": "hi"}


# 参数里的 friend_id 是候选人；from.uid 等于候选人 uid 表示对方发来的消息。
_FRIEND_ID = 456


def test_full_page_with_drifted_entry_is_not_proven_complete():
	payload = {"messages": [_message(2, _FRIEND_ID), "junk"], "hasMore": None}

	assert history_self_message_state(payload, _FRIEND_ID, page_size=2) is None


def test_short_page_with_drifted_entry_stays_complete():
	payload = {"messages": ["junk", _message(2, _FRIEND_ID)], "hasMore": None}

	assert history_self_message_state(payload, _FRIEND_ID, page_size=3) is False


def test_platform_has_more_false_still_proves_complete():
	payload = {"messages": ["junk", _message(2, _FRIEND_ID)], "hasMore": False}

	assert history_self_message_state(payload, _FRIEND_ID, page_size=2) is False


def test_full_page_of_objects_is_not_proven_complete():
	payload = {"messages": [_message(2, _FRIEND_ID), _message(3, _FRIEND_ID)], "hasMore": None}

	assert history_self_message_state(payload, _FRIEND_ID, page_size=2) is None


def test_own_message_short_circuits_drifted_page():
	payload = {"messages": ["junk", _message(2, 123)], "hasMore": None}

	assert history_self_message_state(payload, _FRIEND_ID, page_size=2) is True


def test_sent_by_self_unaffected():
	assert sent_by_self(_message(1, 123), _FRIEND_ID) is True
	assert sent_by_self(_message(1, _FRIEND_ID), _FRIEND_ID) is False


_ROWS = [{"title": "示例岗位", "company": "示例公司"}]


def _seed_old_outputs(output_dir: Path) -> None:
	for name in ("jobs.json", "jobs.csv", "jobs.xlsx"):
		(output_dir / name).write_text(f"OLD-{name}", encoding="utf-8")


def _hidden_jobs_files(output_dir: Path) -> list[str]:
	return sorted(path.name for path in output_dir.iterdir() if path.name.startswith(".jobs."))


def test_serialization_interrupt_cleans_temporaries(tmp_path, monkeypatch):
	_seed_old_outputs(tmp_path)

	def _interrupt(*args, **kwargs):
		raise KeyboardInterrupt

	monkeypatch.setattr(exporter, "_write_xlsx", _interrupt)

	with pytest.raises(KeyboardInterrupt):
		write_run_outputs(tmp_path, _ROWS)

	assert _hidden_jobs_files(tmp_path) == []
	assert (tmp_path / "jobs.json").read_text(encoding="utf-8") == "OLD-jobs.json"
	assert (tmp_path / "jobs.csv").read_text(encoding="utf-8") == "OLD-jobs.csv"


def test_replacement_interrupt_rolls_back_and_cleans(tmp_path, monkeypatch):
	_seed_old_outputs(tmp_path)
	real_replace = os.replace
	calls = {"count": 0}

	def _interrupting_replace(source, target):
		calls["count"] += 1
		if calls["count"] == 4:
			raise KeyboardInterrupt
		return real_replace(source, target)

	monkeypatch.setattr(exporter.os, "replace", _interrupting_replace)

	with pytest.raises(KeyboardInterrupt):
		write_run_outputs(tmp_path, _ROWS)

	assert _hidden_jobs_files(tmp_path) == []
	for name in ("jobs.json", "jobs.csv", "jobs.xlsx"):
		assert (tmp_path / name).read_text(encoding="utf-8") == f"OLD-{name}"
