import hashlib
import json
import sqlite3
import time
from pathlib import Path

_SEARCH_TTL = 86400  # 24 hours
_MAX_SEARCH_CACHE = 100


class CacheStore:
	def __init__(self, db_path: Path, *, search_ttl_seconds: int = _SEARCH_TTL):
		self._db_path = db_path
		self._search_ttl = search_ttl_seconds
		db_path.parent.mkdir(parents=True, exist_ok=True)
		self._conn = sqlite3.connect(str(db_path))
		self._conn.execute("PRAGMA journal_mode=WAL")
		self._init_tables()

	def _init_tables(self):
		self._conn.executescript("""
			CREATE TABLE IF NOT EXISTS greet_records (
				security_id TEXT PRIMARY KEY,
				job_id TEXT NOT NULL,
				greeted_at REAL NOT NULL
			);
			CREATE TABLE IF NOT EXISTS search_cache (
				cache_key TEXT PRIMARY KEY,
				response TEXT NOT NULL,
				created_at REAL NOT NULL
			);
		""")

	@staticmethod
	def _make_search_key(params: dict) -> str:
		raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
		return hashlib.sha256(raw.encode()).hexdigest()

	def is_greeted(self, security_id: str) -> bool:
		row = self._conn.execute(
			"SELECT 1 FROM greet_records WHERE security_id = ?",
			(security_id,),
		).fetchone()
		return row is not None

	def get_job_id(self, security_id: str) -> str | None:
		row = self._conn.execute(
			"SELECT job_id FROM greet_records WHERE security_id = ?",
			(security_id,),
		).fetchone()
		return row[0] if row else None

	def record_greet(self, security_id: str, job_id: str) -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO greet_records (security_id, job_id, greeted_at) VALUES (?, ?, ?)",
			(security_id, job_id, time.time()),
		)
		self._conn.commit()

	def get_search(self, params: dict) -> str | None:
		key = self._make_search_key(params)
		row = self._conn.execute(
			"SELECT response, created_at FROM search_cache WHERE cache_key = ?",
			(key,),
		).fetchone()
		if row is None:
			return None
		if time.time() - row[1] > self._search_ttl:
			self._conn.execute("DELETE FROM search_cache WHERE cache_key = ?", (key,))
			self._conn.commit()
			return None
		return row[0]

	def put_search(self, params: dict, response: str) -> None:
		key = self._make_search_key(params)
		self._conn.execute(
			"INSERT OR REPLACE INTO search_cache (cache_key, response, created_at) VALUES (?, ?, ?)",
			(key, response, time.time()),
		)
		self._conn.commit()
		self._evict_old_search_cache()

	def _evict_old_search_cache(self) -> None:
		count = self._conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
		if count > _MAX_SEARCH_CACHE:
			excess = count - _MAX_SEARCH_CACHE
			self._conn.execute(
				"DELETE FROM search_cache WHERE cache_key IN "
				"(SELECT cache_key FROM search_cache ORDER BY created_at ASC LIMIT ?)",
				(excess,),
			)
			self._conn.commit()

	def close(self):
		self._conn.close()

	def __enter__(self):
		return self

	def __exit__(self, *args):
		self.close()
