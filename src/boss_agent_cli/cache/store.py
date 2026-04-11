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
			CREATE TABLE IF NOT EXISTS saved_searches (
				name TEXT PRIMARY KEY,
				params TEXT NOT NULL,
				created_at REAL NOT NULL,
				updated_at REAL NOT NULL
			);
			CREATE TABLE IF NOT EXISTS watch_hits (
				search_name TEXT NOT NULL,
				job_key TEXT NOT NULL,
				payload TEXT NOT NULL,
				first_seen_at REAL NOT NULL,
				last_seen_at REAL NOT NULL,
				PRIMARY KEY (search_name, job_key)
			);
			CREATE TABLE IF NOT EXISTS apply_records (
				security_id TEXT NOT NULL,
				job_id TEXT NOT NULL,
				applied_at REAL NOT NULL,
				PRIMARY KEY (security_id, job_id)
			);
			CREATE TABLE IF NOT EXISTS shortlist_records (
				security_id TEXT NOT NULL,
				job_id TEXT NOT NULL,
				title TEXT NOT NULL,
				company TEXT NOT NULL,
				city TEXT NOT NULL,
				salary TEXT NOT NULL,
				source TEXT NOT NULL,
				created_at REAL NOT NULL,
				PRIMARY KEY (security_id, job_id)
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

	def save_saved_search(self, name: str, params: dict) -> None:
		now = time.time()
		existing = self._conn.execute(
			"SELECT created_at FROM saved_searches WHERE name = ?",
			(name,),
		).fetchone()
		created_at = existing[0] if existing else now
		self._conn.execute(
			"INSERT OR REPLACE INTO saved_searches (name, params, created_at, updated_at) VALUES (?, ?, ?, ?)",
			(name, json.dumps(params, ensure_ascii=False, sort_keys=True), created_at, now),
		)
		self._conn.commit()

	def get_saved_search(self, name: str) -> dict | None:
		row = self._conn.execute(
			"SELECT name, params, created_at, updated_at FROM saved_searches WHERE name = ?",
			(name,),
		).fetchone()
		if row is None:
			return None
		return {
			"name": row[0],
			"params": json.loads(row[1]),
			"created_at": row[2],
			"updated_at": row[3],
		}

	def list_saved_searches(self) -> list[dict]:
		rows = self._conn.execute(
			"SELECT name, params, created_at, updated_at FROM saved_searches ORDER BY updated_at DESC"
		).fetchall()
		return [
			{
				"name": row[0],
				"params": json.loads(row[1]),
				"created_at": row[2],
				"updated_at": row[3],
			}
			for row in rows
		]

	def delete_saved_search(self, name: str) -> bool:
		cursor = self._conn.execute(
			"DELETE FROM saved_searches WHERE name = ?",
			(name,),
		)
		self._conn.execute(
			"DELETE FROM watch_hits WHERE search_name = ?",
			(name,),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	@staticmethod
	def _make_watch_job_key(item: dict) -> str:
		security_id = item.get("security_id") or item.get("securityId") or ""
		job_id = item.get("job_id") or item.get("encryptJobId") or ""
		if security_id or job_id:
			return f"{security_id}:{job_id}"
		raw = json.dumps(item, sort_keys=True, ensure_ascii=False)
		return hashlib.sha256(raw.encode()).hexdigest()

	def record_watch_results(self, search_name: str, items: list[dict]) -> dict:
		now = time.time()
		new_items = []
		seen_count = 0
		for item in items:
			job_key = self._make_watch_job_key(item)
			payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
			row = self._conn.execute(
				"SELECT 1 FROM watch_hits WHERE search_name = ? AND job_key = ?",
				(search_name, job_key),
			).fetchone()
			if row is None:
				new_items.append(item)
				self._conn.execute(
					"INSERT INTO watch_hits (search_name, job_key, payload, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
					(search_name, job_key, payload, now, now),
				)
			else:
				seen_count += 1
				self._conn.execute(
					"UPDATE watch_hits SET payload = ?, last_seen_at = ? WHERE search_name = ? AND job_key = ?",
					(payload, now, search_name, job_key),
				)
		self._conn.commit()
		return {
			"new_count": len(new_items),
			"seen_count": seen_count,
			"new_items": new_items,
			"total_count": len(items),
		}

	def is_applied(self, security_id: str, job_id: str) -> bool:
		row = self._conn.execute(
			"SELECT 1 FROM apply_records WHERE security_id = ? AND job_id = ?",
			(security_id, job_id),
		).fetchone()
		return row is not None

	def record_apply(self, security_id: str, job_id: str) -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO apply_records (security_id, job_id, applied_at) VALUES (?, ?, ?)",
			(security_id, job_id, time.time()),
		)
		self._conn.commit()

	def is_shortlisted(self, security_id: str, job_id: str) -> bool:
		row = self._conn.execute(
			"SELECT 1 FROM shortlist_records WHERE security_id = ? AND job_id = ?",
			(security_id, job_id),
		).fetchone()
		return row is not None

	def add_shortlist(self, item: dict) -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO shortlist_records (security_id, job_id, title, company, city, salary, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			(
				item.get("security_id", ""),
				item.get("job_id", ""),
				item.get("title", ""),
				item.get("company", ""),
				item.get("city", ""),
				item.get("salary", ""),
				item.get("source", ""),
				time.time(),
			),
		)
		self._conn.commit()

	def list_shortlist(self) -> list[dict]:
		rows = self._conn.execute(
			"SELECT security_id, job_id, title, company, city, salary, source, created_at FROM shortlist_records ORDER BY created_at DESC"
		).fetchall()
		return [
			{
				"security_id": row[0],
				"job_id": row[1],
				"title": row[2],
				"company": row[3],
				"city": row[4],
				"salary": row[5],
				"source": row[6],
				"created_at": row[7],
			}
			for row in rows
		]

	def remove_shortlist(self, security_id: str, job_id: str) -> bool:
		cursor = self._conn.execute(
			"DELETE FROM shortlist_records WHERE security_id = ? AND job_id = ?",
			(security_id, job_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def close(self):
		self._conn.close()

	def __enter__(self):
		return self

	def __exit__(self, *args):
		self.close()
