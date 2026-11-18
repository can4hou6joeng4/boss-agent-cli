"""配置文件读取路径状态的 fail-closed 回归测试（Issue #1695）。

路径状态读不出来时不能把配置当成空/默认值，否则随后的整文件写回会
丢掉其他设置；非普通文件也不能直接读取。
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.commands.config_cmd import _load_user_overrides
from boss_agent_cli.commands.crawl import _save_crawl_config
from boss_agent_cli.config import DEFAULTS, load_config
from boss_agent_cli.services import json_files
from boss_agent_cli.services.json_files import read_bytes_if_regular


def _config_path(tmp_path: Path) -> Path:
	return tmp_path / "config.json"


def _write_config(tmp_path: Path, payload: dict[str, Any]) -> Path:
	path = _config_path(tmp_path)
	path.write_text(json.dumps(payload), encoding="utf-8")
	return path


def _fail_lstat_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_lstat = Path.lstat

	def lstat_fails(self: Path) -> Any:
		if self == path:
			raise PermissionError("permission denied")
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)


def test_read_bytes_if_regular_distinguishes_missing_and_unreadable(tmp_path: Path) -> None:
	missing = tmp_path / "missing.json"
	assert read_bytes_if_regular(missing) is None

	regular = tmp_path / "regular.json"
	regular.write_bytes(b'{"ok": true}')
	assert read_bytes_if_regular(regular) == b'{"ok": true}'

	directory = tmp_path / "directory.json"
	directory.mkdir()
	with pytest.raises(OSError) as caught:
		read_bytes_if_regular(directory)
	assert caught.value.filename == str(directory)

	dangling = tmp_path / "dangling.json"
	dangling.symlink_to(tmp_path / "absent")
	with pytest.raises(OSError):
		read_bytes_if_regular(dangling)


def test_read_bytes_if_regular_does_not_read_fifo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	fifo = tmp_path / "fifo.json"
	fifo.write_bytes(b"placeholder")
	monkeypatch.setattr(json_files, "path_mode", lambda path: stat.S_IFIFO)

	def must_not_read(self: Path) -> bytes:
		raise AssertionError(f"不能读取非普通文件: {self}")

	monkeypatch.setattr(Path, "read_bytes", must_not_read)

	with pytest.raises(OSError):
		read_bytes_if_regular(fifo)


def test_user_overrides_raise_when_metadata_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	path = _write_config(tmp_path, {"log_level": "debug"})
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(PermissionError):
		_load_user_overrides(path)
	assert json.loads(path.read_text(encoding="utf-8")) == {"log_level": "debug"}


def test_load_config_propagates_metadata_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	path = _write_config(tmp_path, {"log_level": "debug"})
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(PermissionError):
		load_config(path)


def test_load_config_missing_file_uses_defaults(tmp_path: Path) -> None:
	assert load_config(tmp_path / "missing.json")["log_level"] == DEFAULTS["log_level"]


def test_ai_load_config_raises_and_save_does_not_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	store = AIConfigStore(tmp_path)
	ai_config = tmp_path / "ai" / "config.json"
	original = {"ai_provider": "deepseek", "ai_model": "offline-model"}
	ai_config.write_text(json.dumps(original), encoding="utf-8")
	_fail_lstat_for(ai_config, monkeypatch)

	with pytest.raises(PermissionError):
		store.load_config()
	with pytest.raises(PermissionError):
		store.save_config(ai_temperature=0.1)
	assert json.loads(ai_config.read_text(encoding="utf-8")) == original


def test_ai_load_config_still_tolerates_corrupt_json(tmp_path: Path) -> None:
	store = AIConfigStore(tmp_path)
	(tmp_path / "ai" / "config.json").write_text('{"ai_provider":', encoding="utf-8")

	assert store.load_config()["ai_provider"] is None


def test_crawl_save_raises_and_preserves_file_when_metadata_unreadable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	path = _write_config(tmp_path, {"crawl": {"max_requests": 3}, "log_level": "debug"})
	original = path.read_bytes()
	_fail_lstat_for(path, monkeypatch)

	with pytest.raises(PermissionError):
		_save_crawl_config(tmp_path, {"max_requests": 5})
	assert path.read_bytes() == original
