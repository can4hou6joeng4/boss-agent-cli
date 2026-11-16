"""crawl Hook 目录预检与非有限时间戳（Issue #1348 / #1349）。

两个契约点：

1. ``crawl run/start`` 必须在创建任务、打开浏览器之前校验 ``--hook-dir``
   内容：目录写错、空目录、清单缺失或脚本被改时返回 ``INVALID_PARAM``，
   且缓存里不留下任何 run。否则任务先落库，运行期注入失败只会被记成
   ``status=stopped`` 的成功信封，而 ``crawl resume`` 没有 ``--hook-dir``。
2. ``updated_at`` 的 ``inf`` / ``nan`` / 超大整数同样按 0 降级：``inf`` 会让
   queued 任务永远被当成「刚排队」，后台恢复返回成功却不启动 worker。
"""

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import (
	_awaiting_queued_worker,
	_prepare_background_resume,
	_tolerated_timestamp,
)
from boss_agent_cli.crawler.hooks import HOOK_SCRIPT_NAMES, verify_hook_profile
from boss_agent_cli.main import cli


def _write_scripts(root: Path) -> Path:
	root.mkdir(parents=True, exist_ok=True)
	for index, (_, filename) in enumerate(HOOK_SCRIPT_NAMES, 1):
		(root / filename).write_bytes(f"globalThis.hook{index} = true;\n".encode())
	return root


def _write_hooks(root: Path) -> Path:
	root.mkdir(parents=True, exist_ok=True)
	lines = []
	for index, (_, filename) in enumerate(HOOK_SCRIPT_NAMES, 1):
		payload = f"globalThis.hook{index} = true;\n".encode()
		(root / filename).write_bytes(payload)
		lines.append(f"{filename} {sha256(payload).hexdigest()}")
	(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
	return root


def _missing_hook_dir(tmp_path: Path) -> Path:
	return tmp_path / "missing-hooks"


def _empty_hook_dir(tmp_path: Path) -> Path:
	path = tmp_path / "empty-hooks"
	path.mkdir()
	return path


def _hook_dir_without_manifest(tmp_path: Path) -> Path:
	return _write_scripts(tmp_path / "no-manifest")


def _run_ids(tmp_path: Path) -> list[str]:
	db_path = tmp_path / "cache" / "boss_agent.db"
	if not db_path.exists():
		return []
	with sqlite3.connect(db_path) as connection:
		return [str(row[0]) for row in connection.execute("SELECT run_id FROM crawl_runs")]


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", *args])


def _explode(settings, calls: list[object]):  # pragma: no cover - 不应被调用
	calls.append(settings)
	raise AssertionError("Hook 预检失败不应构造浏览器")


# ── 缺陷 1：创建任务 / 打开浏览器之前校验 hook 目录 ─────────────────────


@pytest.mark.parametrize(
	"builder",
	[_missing_hook_dir, _empty_hook_dir, _hook_dir_without_manifest],
	ids=["missing-dir", "empty-dir", "no-manifest"],
)
def test_run_rejects_unusable_hook_dir_before_creating_run(tmp_path, monkeypatch, builder):
	transport_calls: list[object] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._transport_factory",
		lambda settings: _explode(settings, transport_calls),
	)
	result = _invoke(
		tmp_path,
		"run",
		"AI",
		"--city",
		"杭州",
		"--hook-profile",
		"screenshot-full",
		"--hook-dir",
		str(builder(tmp_path)),
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert error["recoverable"] is False
	assert "SHA256SUMS" in error["message"]
	# 参数问题必须在创建任务、打开浏览器之前拒绝。
	assert transport_calls == []
	assert _run_ids(tmp_path) == []


def test_start_rejects_unusable_hook_dir_before_queueing(tmp_path, monkeypatch):
	launched: list[str] = []
	transport_calls: list[object] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append(run_id),
	)
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._transport_factory",
		lambda settings: _explode(settings, transport_calls),
	)
	result = _invoke(
		tmp_path,
		"start",
		"AI",
		"--city",
		"杭州",
		"--hook-profile",
		"screenshot-full",
		"--hook-dir",
		str(tmp_path / "missing-hooks"),
	)

	assert result.exit_code == 1, result.output
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert error["recoverable"] is False
	assert launched == []
	assert transport_calls == []
	assert _run_ids(tmp_path) == []


def test_verify_hook_profile_accepts_complete_directory(tmp_path):
	root = _write_hooks(tmp_path / "hooks")

	results = verify_hook_profile("screenshot-full", root)

	assert [item.name for item in results] == [name for name, _ in HOOK_SCRIPT_NAMES]
	assert len(results) == 7
	assert all(item.success and item.sha256 for item in results)


def test_verify_hook_profile_rejects_modified_script(tmp_path):
	root = _write_hooks(tmp_path / "hooks")
	(root / "hook_clear.js").write_text("changed", encoding="utf-8")

	with pytest.raises(ValueError, match="hook_clear"):
		verify_hook_profile("screenshot-full", root)


def test_run_passes_precheck_for_valid_hook_dir(tmp_path, monkeypatch):
	"""合法目录必须通过预检，不能被新校验误伤。"""
	from boss_agent_cli.commands import crawl as crawl_module

	captured = {}

	class _Outcome:
		run_id = "run-hook"
		status = "completed"

		def as_dict(self):
			return {"run_id": self.run_id, "status": self.status, "output_paths": {}}

	def _capture(self, settings):
		captured["settings"] = settings
		return _Outcome()

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _capture)
	monkeypatch.setattr(crawl_module, "_seeded_transport_factory", lambda ctx: lambda settings: None)
	hook_dir = _write_hooks(tmp_path / "hooks")
	result = _invoke(
		tmp_path,
		"run",
		"AI",
		"--city",
		"杭州",
		"--hook-profile",
		"screenshot-full",
		"--hook-dir",
		str(hook_dir),
	)

	assert result.exit_code == 0, result.output
	assert captured["settings"].hook_dir == hook_dir


# ── 缺陷 2：非有限 / 溢出的时间戳按 0 降级 ─────────────────────────────


@pytest.mark.parametrize("value", ["1e400", "Infinity", "nan", float("inf"), float("-inf"), float("nan")])
def test_tolerated_timestamp_non_finite_returns_zero(value):
	assert _tolerated_timestamp(value) == 0.0


def test_tolerated_timestamp_huge_int_does_not_overflow():
	assert _tolerated_timestamp(10**400) == 0.0


def test_tolerated_timestamp_keeps_finite_values():
	assert _tolerated_timestamp(123.5) == 123.5
	assert _tolerated_timestamp("42") == 42.0
	assert _tolerated_timestamp(None) == 0.0


@pytest.mark.parametrize("updated_at", [float("inf"), "1e400", 10**400])
def test_awaiting_queued_worker_treats_non_finite_timestamp_as_stale(updated_at):
	assert _awaiting_queued_worker({"status": "queued", "updated_at": updated_at}) is False


def _seed_run(tmp_path: Path, *, status: str = "queued") -> None:
	params = {
		"query": "AI",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run("run-1", params, str(tmp_path / "crawl" / "run-1"), status=status)
	with sqlite3.connect(db_path) as connection:
		connection.execute("UPDATE crawl_runs SET updated_at = '1e400' WHERE run_id = 'run-1'")


def test_prepare_background_resume_launches_worker_for_non_finite_timestamp(tmp_path):
	"""inf 不能把 queued 任务永远留在「刚排队」窗口里（Issue #1349）。"""
	_seed_run(tmp_path, status="queued")

	status, should_launch = _prepare_background_resume(
		tmp_path, "run-1", pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
	)

	assert (status, should_launch) == ("queued", True)


def test_status_marks_non_finite_queue_as_overdue(tmp_path):
	_seed_run(tmp_path, status="queued")

	result = _invoke(tmp_path, "status", "run-1")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	next_actions = payload["hints"]["next_actions"]
	assert any("crawl resume run-1 --background" in action for action in next_actions)
