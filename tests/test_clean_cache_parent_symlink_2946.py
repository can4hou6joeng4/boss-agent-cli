"""Issue #2946：清理缓存库不能经祖先链接改写数据目录外的数据库。"""

import json
from pathlib import Path
import sqlite3

from click.testing import CliRunner
import pytest

from boss_agent_cli.commands.clean import _clean_crawl_data, _clean_table, _open_cache_db
from boss_agent_cli.main import cli


def _linked_cache(tmp_path: Path) -> tuple[Path, Path]:
	data_dir = tmp_path / "demo-data"
	outside = tmp_path / "outside-cache"
	data_dir.mkdir()
	outside.mkdir()
	db_path = outside / "boss_agent.db"
	with sqlite3.connect(db_path) as conn:
		conn.execute("CREATE TABLE search_cache (response TEXT, created_at REAL)")
		conn.execute("INSERT INTO search_cache VALUES ('demo', 0)")
		conn.execute("CREATE TABLE crawl_runs (run_id TEXT)")
		conn.execute("INSERT INTO crawl_runs VALUES ('demo-run')")
	try:
		(data_dir / "cache").symlink_to(outside, target_is_directory=True)
	except OSError as exc:
		pytest.skip(f"当前环境不支持目录符号链接: {exc}")
	return data_dir, db_path


@pytest.mark.parametrize("args", [[], ["--all"], ["--privacy"], ["--all", "--dry-run"]])
def test_clean_refuses_symlinked_cache_parent(tmp_path: Path, args: list[str]) -> None:
	data_dir, db_path = _linked_cache(tmp_path)
	auth = data_dir / "auth" / "session.enc"
	auth.parent.mkdir()
	auth.write_bytes(b"demo-session")

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), "clean", *args])

	envelope = json.loads(result.output)
	assert result.exit_code == 1, envelope
	assert envelope["error"]["code"] == "LOCAL_IO_ERROR"
	assert auth.read_bytes() == b"demo-session"
	with sqlite3.connect(db_path) as conn:
		assert conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 1


@pytest.mark.parametrize("target", ["table", "crawl"])
def test_other_cache_cleanup_paths_refuse_parent_link(tmp_path: Path, target: str) -> None:
	data_dir, db_path = _linked_cache(tmp_path)

	with pytest.raises(OSError, match="符号链接"):
		if target == "table":
			_clean_table(data_dir, "search_cache", dry_run=False)
		else:
			_clean_crawl_data(data_dir, dry_run=False)

	with sqlite3.connect(db_path) as conn:
		assert conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0] == 1
		assert conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 1


def test_user_selected_data_dir_may_be_a_symlink(tmp_path: Path) -> None:
	real_root = tmp_path / "real-data"
	db_path = real_root / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True)
	with sqlite3.connect(db_path) as conn:
		conn.execute("CREATE TABLE demo (value TEXT)")
	chosen_root = tmp_path / "demo-data"
	try:
		chosen_root.symlink_to(real_root, target_is_directory=True)
	except OSError as exc:
		pytest.skip(f"当前环境不支持目录符号链接: {exc}")

	conn = _open_cache_db(chosen_root / "cache" / "boss_agent.db", root=chosen_root)
	try:
		assert conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 0
	finally:
		conn.close()
