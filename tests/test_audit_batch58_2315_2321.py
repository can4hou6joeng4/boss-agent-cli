"""审计批次五十八：候选人动作预约释放、内核缓存与导出边界（Issue #2315–#2321）。

- 预约释放失败不能替换原始异常，也不能把「平台未接受」报成结果未知（#2315）；
- ``NETWORK_ERROR``（5xx / 超时）不是平台的明确拒绝，写请求必须保留 pending（#2316）；
- doctor 的缓存枚举在 cwd 删除 / HOME 解析失败 / 路径含 NUL 时不能崩溃（#2317）；
- 内核「已安装」必须是带安装标记的目录，缓存根按驱动平台优先级选择（#2318）；
- 深嵌套 ``jobType`` 的 ``RecursionError`` 按字段缺失处理（#2319）；
- wizard 的空白 job_id / security_id 报 INVALID_PARAM（#2320）；
- 导出入口的 None diff、Markdown 摘要换行与 JSON 代理字符边界（#2321）。

全部离线：平台、缓存与浏览器探测都是本地替身。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
import types
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.api.models import employment_type_from_raw, safe_text
from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services import browser_runtime
from boss_agent_cli.services.candidate_actions import (
	CandidateActionReleaseFailed,
	classify_platform_response,
	run_candidate_action,
)
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.job_card import build_job_from_card
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


def _db(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


def _claim_state(tmp_path: Path, job_id: str, action: str) -> str | None:
	"""只读查询预约状态：``claim_candidate_action`` 会顺带插入 pending，不能用来断言。"""
	connection = sqlite3.connect(_db(tmp_path))
	try:
		row = connection.execute(
			"SELECT status FROM candidate_action_claims WHERE job_id = ? AND action = ?",
			(job_id, action),
		).fetchone()
	finally:
		connection.close()
	return str(row[0]) if row is not None else None


def _raise(exc: BaseException):
	def send():
		raise exc

	return send


# ── #2315：预约释放失败 ───────────────────────────────────────────


def test_exception_path_release_failure_preserves_original_exception(tmp_path: Path) -> None:
	"""释放写失败不能替换原始异常，否则「未发出」会被命令层报成结果未知。"""
	original = AuthRequired("未登录，请先登录")
	with CacheStore(_db(tmp_path)) as cache:
		with patch.object(
			cache,
			"release_candidate_action",
			side_effect=sqlite3.OperationalError("database is locked"),
		):
			with pytest.raises(AuthRequired) as caught:
				run_candidate_action(
					cache,
					security_id="sec-1",
					job_id="job-1",
					action="greet",
					send=_raise(original),
				)

	assert caught.value is original
	notes = getattr(caught.value, "__notes__", [])
	assert any("释放候选人动作预约失败" in note for note in notes)
	# SQLite 写失败确实无法撤销 pending，但错误码仍是原始的可恢复错误。
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


class _RejectingPlatform:
	def __init__(self, code: str = "RATE_LIMITED") -> None:
		self.code = code

	def is_success(self, response: Any) -> bool:
		return False

	def parse_error(self, response: Any) -> tuple[str, str]:
		return self.code, "平台拒绝"


def test_retryable_release_failure_becomes_local_state_error(tmp_path: Path) -> None:
	"""平台明确拒绝但预约没删掉：报本地状态错误，而不是让下一次调用撞 pending。"""
	platform = _RejectingPlatform()
	with CacheStore(_db(tmp_path)) as cache:
		with patch.object(
			cache,
			"release_candidate_action",
			side_effect=sqlite3.OperationalError("disk I/O error"),
		):
			with pytest.raises(CandidateActionReleaseFailed) as caught:
				run_candidate_action(
					cache,
					security_id="sec-1",
					job_id="job-1",
					action="greet",
					send=lambda: {"code": 429},
					classify=lambda response: classify_platform_response(platform, response),
				)

	assert isinstance(caught.value.cause, sqlite3.OperationalError)
	assert caught.value.__cause__ is caught.value.cause
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


def _cli_platform(code: str = "RATE_LIMITED") -> MagicMock:
	platform = MagicMock()
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *args: None
	platform.is_success.return_value = False
	platform.parse_error.return_value = (code, "平台拒绝")
	return platform


def test_cli_greet_reports_release_failure_as_local_state_error(tmp_path: Path) -> None:
	"""命令信封落在 LOCAL_STATE_WRITE_FAILED，而不是结果未知或通用网络错误。"""
	platform = _cli_platform()
	with (
		patch("boss_agent_cli.commands.greet.AuthManager"),
		patch("boss_agent_cli.commands.greet.get_platform_instance") as factory,
		patch(
			"boss_agent_cli.cache.store.CacheStore.release_candidate_action",
			side_effect=sqlite3.OperationalError("database is locked"),
		),
	):
		factory.return_value.__enter__ = lambda self: platform
		factory.return_value.__exit__ = lambda self, *args: None
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "greet", "sec-1", "job-1"],
		)

	assert result.exit_code == 1, result.output
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	assert "本地预约未释放" in error["message"]
	assert error["details"]["request_sent"] is False


# ── #2316：NETWORK_ERROR 不是明确拒绝 ─────────────────────────────


def test_network_error_response_is_outcome_unknown(tmp_path: Path) -> None:
	"""5xx / 超时可能已在平台落库：保留 pending，禁止自动重放。"""
	platform = _RejectingPlatform("NETWORK_ERROR")
	with CacheStore(_db(tmp_path)) as cache:
		result = run_candidate_action(
			cache,
			security_id="sec-1",
			job_id="job-1",
			action="greet",
			send=lambda: {"code": 500},
			classify=lambda response: classify_platform_response(platform, response),
		)

	assert result == {"code": 500}
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


def test_explicit_rejection_still_releases_claim(tmp_path: Path) -> None:
	"""回归：限流等明确拒绝仍释放预约，允许限流窗口结束后重试。"""
	platform = _RejectingPlatform("RATE_LIMITED")
	with CacheStore(_db(tmp_path)) as cache:
		run_candidate_action(
			cache,
			security_id="sec-1",
			job_id="job-1",
			action="greet",
			send=lambda: {"code": 429},
			classify=lambda response: classify_platform_response(platform, response),
		)

	assert _claim_state(tmp_path, "job-1", "greet") is None


def test_cli_greet_network_error_response_is_outcome_unknown(tmp_path: Path) -> None:
	"""信封必须禁止重试，不能给「网络错误、稍后重试」而让 Agent 撞上 pending。"""
	platform = _cli_platform("NETWORK_ERROR")
	with (
		patch("boss_agent_cli.commands.greet.AuthManager"),
		patch("boss_agent_cli.commands.greet.get_platform_instance") as factory,
	):
		factory.return_value.__enter__ = lambda self: platform
		factory.return_value.__exit__ = lambda self, *args: None
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "greet", "sec-1", "job-1"],
		)

	assert result.exit_code == 1, result.output
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "ACTION_UNCONFIRMED"
	assert error["recoverable"] is False
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


class _NetworkPlatform:
	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		return {"code": 500}

	def is_success(self, response: Any) -> bool:
		return False

	def unwrap_data(self, response: Any) -> dict[str, Any]:
		return {}

	def parse_error(self, response: Any) -> tuple[str, str]:
		return "NETWORK_ERROR", "server error"


def test_wizard_network_error_write_is_outcome_unknown(tmp_path: Path) -> None:
	"""wizard 写动作的 5xx 响应同样禁止重放，预约保持 pending。"""
	platform = _NetworkPlatform()
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args: platform,
	)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_greet"](
			context, {"security_id": "sec-1", "job_id": "job-1"}, {}
		)

	assert caught.value.code == "ACTION_UNCONFIRMED"
	assert caught.value.replay_safe is False
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


# ── #2317：缓存枚举失败不能打穿 doctor ────────────────────────────


@pytest.mark.parametrize(
	"error",
	[
		OSError("current working directory was deleted"),
		ValueError("embedded null byte"),
		RuntimeError("Unable to determine home directory"),
	],
)
def test_cache_enumeration_failure_is_reported_not_raised(
	monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
	def boom() -> list[Path]:
		raise error

	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", boom)

	assert browser_runtime.installed_browser_dirs() == []
	# 枚举整体失败同样不能落成「未安装」：doctor 按占位条目报「无法确认」
	# 而不是引导重复下载（Issue #2559）。
	assert browser_runtime.unreadable_browser_cache_dirs() == [browser_runtime._CACHE_ROOT_UNKNOWN]


def test_browser_cache_root_with_nul_is_unreadable(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
	root = Path(str(tmp_path / "cache") + "\x00tail")
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [root])

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == [root]


# ── #2318：已安装判定与缓存根优先级 ──────────────────────────────


def _make_install(root: Path, name: str) -> Path:
	entry = root / name
	entry.mkdir(parents=True)
	(entry / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
	return entry


def test_only_complete_install_directories_count(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
	root = tmp_path / "cache"
	root.mkdir()
	good = _make_install(root, "chromium-1300")
	empty = root / "chromium-1301"
	empty.mkdir()
	plain_file = root / "chromium-1302"
	plain_file.write_text("", encoding="utf-8")
	dangling = root / "chromium-1303"
	dangling.symlink_to(tmp_path / "missing")
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [root])

	assert browser_runtime.installed_browser_dirs() == [good]
	assert set(browser_runtime.unreadable_browser_cache_dirs()) == {empty, plain_file, dangling}


def test_symlink_to_complete_install_is_usable(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
	real = _make_install(tmp_path / "real", "chromium-1300")
	root = tmp_path / "cache"
	root.mkdir()
	link = root / "chromium-1300"
	link.symlink_to(real)
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [root])

	assert browser_runtime.installed_browser_dirs() == [link]
	assert browser_runtime.unreadable_browser_cache_dirs() == []


def test_cache_root_that_is_a_regular_file_is_unreadable(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
	root = tmp_path / "ms-playwright"
	root.write_text("", encoding="utf-8")
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [root])

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == [root]


@pytest.mark.parametrize(
	("payload", "expected"),
	[
		("1300", "1300"),
		(1300, "1300"),
		(True, None),
		(False, None),
		({"revision": 1300}, None),
		([1300], None),
		("", None),
		("   ", None),
	],
)
def test_browsers_json_revision_shape_drift_is_unreadable(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: Any, expected: str | None,
) -> None:
	driver = tmp_path / "patchright_pkg" / "driver" / "package"
	driver.mkdir(parents=True)
	(driver / "browsers.json").write_text(
		json.dumps({"browsers": [{"name": "chromium", "revision": payload}]}),
		encoding="utf-8",
	)
	fake = types.ModuleType("patchright")
	fake.__file__ = str(tmp_path / "patchright_pkg" / "__init__.py")
	monkeypatch.setitem(sys.modules, "patchright", fake)

	assert browser_runtime.patchright_chromium_revision() == expected


# ── #2319：深嵌套 jobType ────────────────────────────────────────


class _RecursiveText:
	"""``str()`` 时抛 RecursionError，模拟深嵌套容器的文本化失败。"""

	def __str__(self) -> str:
		raise RecursionError("maximum recursion depth exceeded")


def test_recursion_error_during_text_conversion_is_treated_as_missing() -> None:
	value = _RecursiveText()

	assert safe_text(value) == ""
	assert employment_type_from_raw(value) == ""
	job = build_job_from_card({"jobName": "后端", "jobType": value}, security_id="sec-1", greeted=False)
	assert job["employment_type"] == ""
	assert job["title"] == "后端"


# ── #2320：wizard 空白标识 ───────────────────────────────────────


@pytest.mark.parametrize("action", ["candidate_greet", "candidate_apply"])
@pytest.mark.parametrize("field", ["security_id", "job_id"])
def test_wizard_blank_candidate_identifier_is_invalid_param(action: str, field: str) -> None:
	inputs = {"security_id": "sec-1", "job_id": "job-1"}
	inputs[field] = "   "

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS[action](MagicMock(), inputs, {})

	assert caught.value.code == "INVALID_PARAM"
	assert field in str(caught.value)


# ── #2321：chat_export 公共边界 ─────────────────────────────────


def _friend(**overrides: Any) -> dict[str, Any]:
	friend = {
		"name": "HR",
		"title": "T",
		"brand_name": "C",
		"initiated_by": "对方主动",
		"last_msg": "-",
		"last_time": "-",
		"msg_status": "已读",
		"uid": 1,
		"security_id": "s",
		"encrypt_job_id": "j",
		"unread": 0,
	}
	friend.update(overrides)
	return friend


def test_markdown_export_accepts_none_diff_result() -> None:
	text = render_export([_friend()], "md", None, None, None)  # type: ignore[arg-type]

	assert "# BOSS 直聘沟通列表" in text
	assert "HR" in text


def test_markdown_diff_summary_cannot_inject_lines() -> None:
	diff = {
		"is_first": False,
		"prev_date": "2026-01-01\n## 伪造标题",
		"added": [],
		"removed": [],
		"new_unread": [],
	}
	text = render_export([], "md", None, None, diff)
	lines = text.splitlines()

	assert sum("较上次" in line for line in lines) == 1
	assert not any(line.startswith("## 伪造标题") for line in lines)


def test_json_export_sanitizes_lone_surrogates() -> None:
	text = render_export([_friend(name="\ud800")], "json", None, None, {})

	# 严格 UTF-8 写盘不能再抛 UnicodeEncodeError；代理字符归一为 JSON 转义。
	text.encode("utf-8")
	assert "\\ud800" in text
	json.loads(text)
