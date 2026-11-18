"""熔断 `open` 字段类型漂移的回归测试（Issue #1242）。

`stop.json` 与 `state.json` 的 `circuit_breaker.open` 只接受布尔值：任何非布尔值
都必须 fail-closed 或报状态损坏，不能让 `0` / `null` 关闭停止开关，也不能让两个
读取器（`SafetyGuard` 的真值判断与 `location` 的 `is not True`）得到相反结论。
"""

import errno
import json
import os
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.commands.recruiter.location import _stop_reason

_MISSING = object()


def _store(
	tmp_path: Path,
	*,
	stop: object = _MISSING,
	state: object = _MISSING,
) -> AutomationStore:
	root = tmp_path / "automation"
	root.mkdir(parents=True, exist_ok=True)
	if state is not _MISSING:
		(root / "state.json").write_text(json.dumps(state), encoding="utf-8")
	if stop is not _MISSING:
		(root / "stop.json").write_text(json.dumps(stop), encoding="utf-8")
	return AutomationStore(tmp_path)


def _guard(store: AutomationStore) -> SafetyGuard:
	return SafetyGuard(AutomationConfig(), store.read_state(), dry_run=True, store=store)


@pytest.mark.parametrize("value", [0, 1, "true", "false", None])
def test_stop_latch_with_non_boolean_open_fails_closed(tmp_path, value):
	store = _store(tmp_path, stop={"open": value})

	latch = store.read_stop()
	assert latch["open"] is True
	assert latch["corrupt"] is True
	# 两个读取器对同一份损坏标记都按停止处理，不再出现一个放行一个拦截。
	assert _stop_reason(tmp_path)
	assert _guard(store).stop_reason()


@pytest.mark.parametrize("value", [True, False])
def test_stop_latch_with_boolean_open_keeps_meaning(tmp_path, value):
	store = _store(tmp_path, stop={"open": value, "reason": "manual"})

	assert store.read_stop()["open"] is value
	assert bool(_stop_reason(tmp_path)) is value


@pytest.mark.parametrize("value", [0, 1, "true", "false", None])
def test_state_breaker_with_non_boolean_open_is_state_error(tmp_path, value):
	store = _store(tmp_path, state={"autonomy": {"circuit_breaker": {"open": value}}})

	with pytest.raises(AutomationStateError):
		store.read_state()


def test_stop_latch_cannot_close_a_persisted_open_breaker(tmp_path):
	store = _store(
		tmp_path,
		state={"autonomy": {"circuit_breaker": {"open": True, "reason": "manual-stop"}}},
		stop={"open": 0},
	)

	guard = _guard(store)
	assert guard.stop_reason() == "stop.json 的 open 字段不是布尔值，已按熔断处理"
	guard.checkpoint()

	persisted = json.loads((tmp_path / "automation" / "state.json").read_text(encoding="utf-8"))
	assert persisted["autonomy"]["circuit_breaker"]["open"] is True


def test_resume_clears_a_corrupt_latch_after_explicit_operator_action(tmp_path):
	store = _store(tmp_path, stop={"open": "false"})

	assert store.resume() is True
	assert _guard(store).stop_reason() is None
	assert _stop_reason(tmp_path) is None


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO])
def test_stop_metadata_error_keeps_latch_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_number: int) -> None:
	store = _store(tmp_path, stop={"open": True}, state={"autonomy": {"circuit_breaker": {"open": True}}})
	stop_before = store.stop_path.read_bytes()
	state_before = store.state_path.read_bytes()

	def deny_lookup(get_info: Any) -> Any:
		def lookup(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
			if path in (store.stop_path, str(store.stop_path)):
				raise OSError(error_number, "测试路径状态读取失败", str(store.stop_path))
			return get_info(path, *args, **kwargs)

		return lookup

	with monkeypatch.context() as patch:
		patch.setattr(os, "stat", deny_lookup(os.stat))
		patch.setattr(os, "lstat", deny_lookup(os.lstat))
		latch = store.read_stop()
		assert latch["open"] is True
		assert latch["corrupt"] is True
		assert "stop.json" in latch["reason"]
		assert _guard(store).stop_reason() == latch["reason"]
		assert _stop_reason(tmp_path) == latch["reason"]
		with pytest.raises(AutomationStateError) as caught:
			store.resume()
		assert caught.value.path == store.stop_path
		assert "路径状态" in caught.value.detail

	assert store.stop_path.read_bytes() == stop_before
	assert store.state_path.read_bytes() == state_before


def test_write_state_rejects_non_boolean_open(tmp_path):
	store = _store(tmp_path)

	with pytest.raises(AutomationStateError):
		store.write_state({"autonomy": {"circuit_breaker": {"open": 0}}})
