import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from types import TracebackType
from typing import Any, cast

_SEARCH_TTL = 86400  # 24 hours
_MAX_SEARCH_CACHE = 100


class CacheStore:
	def __init__(self, db_path: Path, *, search_ttl_seconds: int = _SEARCH_TTL) -> None:
		self._db_path = db_path
		self._search_ttl = search_ttl_seconds
		db_path.parent.mkdir(parents=True, exist_ok=True)
		self._conn = sqlite3.connect(str(db_path))
		self._conn.execute("PRAGMA journal_mode=WAL")
		self._init_tables()

	def _init_tables(self) -> None:
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
			CREATE TABLE IF NOT EXISTS job_desc_cache (
				job_id TEXT PRIMARY KEY,
				description TEXT NOT NULL,
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
				tags TEXT DEFAULT '',
				note TEXT DEFAULT '',
				created_at REAL NOT NULL,
				PRIMARY KEY (security_id, job_id)
			);
			CREATE TABLE IF NOT EXISTS resume_job_links (
				resume_name TEXT NOT NULL,
				security_id TEXT NOT NULL,
				job_id TEXT NOT NULL,
				job_title TEXT NOT NULL,
				company TEXT NOT NULL,
				status TEXT NOT NULL DEFAULT 'prepared',
				notes TEXT DEFAULT '',
				linked_at REAL NOT NULL,
				updated_at REAL NOT NULL,
				PRIMARY KEY (resume_name, security_id, job_id)
			);
			CREATE TABLE IF NOT EXISTS recruiter_applications (
				id TEXT PRIMARY KEY,
				geek_id TEXT,
				job_id TEXT,
				status TEXT,
				resume_shared INTEGER DEFAULT 0,
				applied_at TEXT,
				cached_at TEXT
			);
			CREATE TABLE IF NOT EXISTS recruiter_jobs (
				job_id TEXT PRIMARY KEY,
				title TEXT,
				status TEXT,
				applicant_count INTEGER DEFAULT 0,
				cached_at TEXT
			);
			CREATE TABLE IF NOT EXISTS crawl_runs (
				run_id TEXT PRIMARY KEY,
				params TEXT NOT NULL,
				status TEXT NOT NULL,
				stop_requested INTEGER NOT NULL DEFAULT 0,
				requests_attempted INTEGER NOT NULL DEFAULT 0,
				detail_requests_attempted INTEGER NOT NULL DEFAULT 0,
				elapsed_seconds INTEGER NOT NULL DEFAULT 0,
				next_page INTEGER NOT NULL,
				list_finished INTEGER NOT NULL DEFAULT 0,
				output_dir TEXT NOT NULL,
				error TEXT NOT NULL DEFAULT '',
				hook_results TEXT NOT NULL DEFAULT '[]',
				created_at REAL NOT NULL,
				updated_at REAL NOT NULL
			);
			CREATE TABLE IF NOT EXISTS crawl_jobs (
				run_id TEXT NOT NULL,
				job_key TEXT NOT NULL,
				selector TEXT,
				page_no INTEGER NOT NULL,
				payload TEXT NOT NULL,
				detail_done INTEGER NOT NULL DEFAULT 0,
				updated_at REAL NOT NULL,
				PRIMARY KEY (run_id, job_key)
			);
			CREATE TABLE IF NOT EXISTS crawl_budget (
				budget_key TEXT PRIMARY KEY,
				last_request_at REAL NOT NULL
			);
		""")
		self._migrate_shortlist_records()
		self._migrate_crawl_runs()
		self._migrate_crawl_jobs()

	def _migrate_shortlist_records(self) -> None:
		columns = {
			row[1]
			for row in self._conn.execute("PRAGMA table_info(shortlist_records)").fetchall()
		}
		if "tags" not in columns:
			self._conn.execute("ALTER TABLE shortlist_records ADD COLUMN tags TEXT DEFAULT ''")
		if "note" not in columns:
			self._conn.execute("ALTER TABLE shortlist_records ADD COLUMN note TEXT DEFAULT ''")
		self._conn.commit()

	def _migrate_crawl_runs(self) -> None:
		columns = {
			row[1]
			for row in self._conn.execute("PRAGMA table_info(crawl_runs)").fetchall()
		}
		if "list_finished" not in columns:
			self._conn.execute("ALTER TABLE crawl_runs ADD COLUMN list_finished INTEGER NOT NULL DEFAULT 0")
		if "stop_requested" not in columns:
			self._conn.execute("ALTER TABLE crawl_runs ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0")
		if "requests_attempted" not in columns:
			self._conn.execute("ALTER TABLE crawl_runs ADD COLUMN requests_attempted INTEGER NOT NULL DEFAULT 0")
		if "detail_requests_attempted" not in columns:
			self._conn.execute("ALTER TABLE crawl_runs ADD COLUMN detail_requests_attempted INTEGER NOT NULL DEFAULT 0")
		if "elapsed_seconds" not in columns:
			self._conn.execute("ALTER TABLE crawl_runs ADD COLUMN elapsed_seconds INTEGER NOT NULL DEFAULT 0")
		if "hook_results" not in columns:
			self._conn.execute("ALTER TABLE crawl_runs ADD COLUMN hook_results TEXT NOT NULL DEFAULT '[]'")
		self._conn.commit()

	def _migrate_crawl_jobs(self) -> None:
		columns = {row[1] for row in self._conn.execute("PRAGMA table_info(crawl_jobs)").fetchall()}
		if "selector" not in columns:
			self._conn.execute("ALTER TABLE crawl_jobs ADD COLUMN selector TEXT")
		missing = self._conn.execute(
			"SELECT run_id, job_key FROM crawl_jobs WHERE selector IS NULL OR selector = ''"
		).fetchall()
		for run_id, job_key in missing:
			self._conn.execute(
				"UPDATE crawl_jobs SET selector = ? WHERE run_id = ? AND job_key = ?",
				(self._new_crawl_selector(), run_id, job_key),
			)
		self._conn.execute(
			"CREATE UNIQUE INDEX IF NOT EXISTS crawl_jobs_run_selector ON crawl_jobs(run_id, selector)"
		)
		self._conn.commit()

	def _new_crawl_selector(self) -> str:
		return f"csel_{secrets.token_urlsafe(18)}"

	# ── DrissionPage crawl task state ────────────────────────────────

	def create_crawl_run(
		self,
		run_id: str,
		params: dict[str, Any],
		output_dir: str,
		*,
		next_page: int = 1,
		status: str = "running",
	) -> None:
		now = time.time()
		self._conn.execute(
			"INSERT INTO crawl_runs (run_id, params, status, next_page, output_dir, error, created_at, updated_at) "
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			(run_id, json.dumps(params, ensure_ascii=False, sort_keys=True), status, next_page, output_dir, "", now, now),
		)
		self._conn.commit()

	def get_crawl_run(self, run_id: str) -> dict[str, Any] | None:
		row = self._conn.execute(
			"SELECT run_id, params, status, stop_requested, requests_attempted, detail_requests_attempted, elapsed_seconds, "
			"next_page, list_finished, output_dir, error, hook_results, created_at, updated_at "
			"FROM crawl_runs WHERE run_id = ?",
			(run_id,),
		).fetchone()
		if row is None:
			return None
		return {
			"run_id": row[0], "params": json.loads(row[1]), "status": row[2], "stop_requested": bool(row[3]),
			"requests_attempted": int(row[4]), "detail_requests_attempted": int(row[5]), "elapsed_seconds": int(row[6]),
			"next_page": row[7], "list_finished": bool(row[8]), "output_dir": row[9], "error": row[10],
			"hook_results": json.loads(row[11]), "created_at": row[12], "updated_at": row[13],
		}

	def update_crawl_run_params(self, run_id: str, params: dict[str, Any]) -> None:
		self._conn.execute(
			"UPDATE crawl_runs SET params = ?, updated_at = ? WHERE run_id = ?",
			(json.dumps(params, ensure_ascii=False, sort_keys=True), time.time(), run_id),
		)
		self._conn.commit()

	def update_crawl_run(
		self,
		run_id: str,
		*,
		status: str,
		next_page: int,
		error: str = "",
		list_finished: bool | None = None,
		hook_results: list[dict[str, Any]] | None = None,
	) -> None:
		assignments = ["status = ?", "next_page = ?", "error = ?", "updated_at = ?"]
		values: list[Any] = [status, next_page, error, time.time()]
		if list_finished is not None:
			assignments.append("list_finished = ?")
			values.append(int(list_finished))
		if hook_results is not None:
			assignments.append("hook_results = ?")
			values.append(json.dumps(hook_results, ensure_ascii=False, sort_keys=True))
		values.append(run_id)
		self._conn.execute(
			f"UPDATE crawl_runs SET {', '.join(assignments)} WHERE run_id = ?",
			values,
		)
		self._conn.commit()

	def request_crawl_stop(self, run_id: str) -> bool:
		cursor = self._conn.execute(
			"UPDATE crawl_runs SET stop_requested = 1, updated_at = ? WHERE run_id = ?",
			(time.time(), run_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def clear_crawl_stop_request(self, run_id: str) -> None:
		self._conn.execute(
			"UPDATE crawl_runs SET stop_requested = 0, updated_at = ? WHERE run_id = ?",
			(time.time(), run_id),
		)
		self._conn.commit()

	def update_crawl_run_budget(
		self,
		run_id: str,
		*,
		requests_attempted: int,
		detail_requests_attempted: int,
		elapsed_seconds: int,
	) -> None:
		self._conn.execute(
			"UPDATE crawl_runs SET requests_attempted = ?, detail_requests_attempted = ?, elapsed_seconds = ?, updated_at = ? "
			"WHERE run_id = ?",
			(requests_attempted, detail_requests_attempted, elapsed_seconds, time.time(), run_id),
		)
		self._conn.commit()

	def put_crawl_job(self, run_id: str, job_key: str, page_no: int, payload: dict[str, Any], *, detail_done: bool) -> None:
		existing = self._conn.execute(
			"SELECT selector FROM crawl_jobs WHERE run_id = ? AND job_key = ?", (run_id, job_key)
		).fetchone()
		selector = str(existing[0]) if existing and existing[0] else self._new_crawl_selector()
		self._conn.execute(
			"INSERT OR REPLACE INTO crawl_jobs (run_id, job_key, selector, page_no, payload, detail_done, updated_at) "
			"VALUES (?, ?, ?, ?, ?, ?, ?)",
			(run_id, job_key, selector, page_no, json.dumps(payload, ensure_ascii=False, sort_keys=True), int(detail_done), time.time()),
		)
		self._conn.commit()

	def get_crawl_job(self, run_id: str, job_key: str) -> dict[str, Any] | None:
		row = self._conn.execute(
			"SELECT selector, page_no, payload, detail_done FROM crawl_jobs WHERE run_id = ? AND job_key = ?",
			(run_id, job_key),
		).fetchone()
		if row is None:
			return None
		return {"selector": row[0], "page_no": row[1], "payload": json.loads(row[2]), "detail_done": bool(row[3])}

	def list_crawl_jobs(self, run_id: str) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT job_key, selector, page_no, payload, detail_done FROM crawl_jobs WHERE run_id = ? ORDER BY page_no, rowid",
			(run_id,),
		).fetchall()
		return [
			{"job_key": row[0], "selector": row[1], "page_no": row[2], "payload": json.loads(row[3]), "detail_done": bool(row[4])}
			for row in rows
		]

	def get_crawl_budget(self, budget_key: str) -> float | None:
		row = self._conn.execute("SELECT last_request_at FROM crawl_budget WHERE budget_key = ?", (budget_key,)).fetchone()
		return float(row[0]) if row is not None else None

	def put_crawl_budget(self, budget_key: str, requested_at: float) -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO crawl_budget (budget_key, last_request_at) VALUES (?, ?)",
			(budget_key, requested_at),
		)
		self._conn.commit()

	@staticmethod
	def _normalize_shortlist_tags(tags: list[str]) -> list[str]:
		normalized: list[str] = []
		seen: set[str] = set()
		for tag in tags:
			clean = str(tag).strip()
			if not clean or clean in seen:
				continue
			normalized.append(clean)
			seen.add(clean)
		return normalized

	@classmethod
	def _serialize_shortlist_tags(cls, tags: list[str]) -> str:
		normalized = cls._normalize_shortlist_tags(tags)
		if not normalized:
			return ""
		return json.dumps(normalized, ensure_ascii=False, sort_keys=True)

	@classmethod
	def _deserialize_shortlist_tags(cls, raw: str | None) -> list[str]:
		if not raw:
			return []
		try:
			parsed = json.loads(raw)
		except json.JSONDecodeError:
			return cls._normalize_shortlist_tags(raw.split(","))
		if not isinstance(parsed, list):
			return []
		return cls._normalize_shortlist_tags([str(tag) for tag in parsed])

	@staticmethod
	def _make_search_key(params: dict[str, Any]) -> str:
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

	def get_search(self, params: dict[str, Any]) -> str | None:
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
		return cast("str", row[0])

	def put_search(self, params: dict[str, Any], response: str) -> None:
		key = self._make_search_key(params)
		self._conn.execute(
			"INSERT OR REPLACE INTO search_cache (cache_key, response, created_at) VALUES (?, ?, ?)",
			(key, response, time.time()),
		)
		self._conn.commit()
		self._evict_old_search_cache()

	# ── 职位描述缓存（welfare 详情比对复用，降低重复搜索的取详情请求量）──
	# 键用 job_id（encryptJobId，跨搜索稳定）；securityId 是每次请求重新生成的
	# 临时令牌、跨搜索不稳定，不能做缓存键。
	# 线程安全注意：sqlite 连接非线程安全，这两个方法只可在主线程调用，
	# 不要在 welfare 详情线程池的 worker 内访问。

	def get_job_desc(self, job_id: str) -> str | None:
		"""返回缓存的职位描述（命中且未过期），否则 None。"""
		if not job_id:
			return None
		row = self._conn.execute(
			"SELECT description, created_at FROM job_desc_cache WHERE job_id = ?",
			(job_id,),
		).fetchone()
		if row is None:
			return None
		if time.time() - row[1] > self._search_ttl:
			self._conn.execute("DELETE FROM job_desc_cache WHERE job_id = ?", (job_id,))
			self._conn.commit()
			return None
		return cast("str", row[0])

	def put_job_desc(self, job_id: str, description: str) -> None:
		"""缓存职位描述（仅当 job_id 与描述非空时写入）。"""
		if not job_id or not description:
			return
		self._conn.execute(
			"INSERT OR REPLACE INTO job_desc_cache (job_id, description, created_at) VALUES (?, ?, ?)",
			(job_id, description, time.time()),
		)
		self._conn.commit()

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

	def save_saved_search(self, name: str, params: dict[str, Any]) -> None:
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

	def get_saved_search(self, name: str) -> dict[str, Any] | None:
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

	def list_saved_searches(self) -> list[dict[str, Any]]:
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
	def _make_watch_job_key(item: dict[str, Any]) -> str:
		security_id = item.get("security_id") or item.get("securityId") or ""
		job_id = item.get("job_id") or item.get("encryptJobId") or ""
		if security_id or job_id:
			return f"{security_id}:{job_id}"
		raw = json.dumps(item, sort_keys=True, ensure_ascii=False)
		return hashlib.sha256(raw.encode()).hexdigest()

	def record_watch_results(self, search_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
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

	def add_shortlist(self, item: dict[str, Any]) -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO shortlist_records "
			"(security_id, job_id, title, company, city, salary, source, tags, note, created_at) "
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
			(
				item.get("security_id", ""),
				item.get("job_id", ""),
				item.get("title", ""),
				item.get("company", ""),
				item.get("city", ""),
				item.get("salary", ""),
				item.get("source", ""),
				self._serialize_shortlist_tags(item.get("tags", [])),
				item.get("note", ""),
				time.time(),
			),
		)
		self._conn.commit()

	def list_shortlist(self) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT security_id, job_id, title, company, city, salary, source, tags, note, created_at "
			"FROM shortlist_records ORDER BY created_at DESC"
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
				"tags": self._deserialize_shortlist_tags(row[7]),
				"note": row[8] or "",
				"created_at": row[9],
			}
			for row in rows
		]

	def set_shortlist_tags(self, security_id: str, job_id: str, tags: list[str]) -> bool:
		cursor = self._conn.execute(
			"UPDATE shortlist_records SET tags = ? WHERE security_id = ? AND job_id = ?",
			(self._serialize_shortlist_tags(tags), security_id, job_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def set_shortlist_note(self, security_id: str, job_id: str, note: str) -> bool:
		cursor = self._conn.execute(
			"UPDATE shortlist_records SET note = ? WHERE security_id = ? AND job_id = ?",
			(note, security_id, job_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def remove_shortlist(self, security_id: str, job_id: str) -> bool:
		cursor = self._conn.execute(
			"DELETE FROM shortlist_records WHERE security_id = ? AND job_id = ?",
			(security_id, job_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def link_resume_to_job(
		self,
		resume_name: str,
		security_id: str,
		job_id: str,
		job_title: str,
		company: str,
	) -> None:
		"""将简历与职位关联"""
		now = time.time()
		self._conn.execute(
			"INSERT OR REPLACE INTO resume_job_links "
			"(resume_name, security_id, job_id, job_title, company, status, notes, linked_at, updated_at) "
			"VALUES (?, ?, ?, ?, ?, 'prepared', '', ?, ?)",
			(resume_name, security_id, job_id, job_title, company, now, now),
		)
		self._conn.commit()

	def update_job_link_status(
		self,
		resume_name: str,
		security_id: str,
		job_id: str,
		status: str,
		notes: str = "",
	) -> bool:
		"""更新关联状态"""
		now = time.time()
		cursor = self._conn.execute(
			"UPDATE resume_job_links SET status = ?, notes = ?, updated_at = ? "
			"WHERE resume_name = ? AND security_id = ? AND job_id = ?",
			(status, notes, now, resume_name, security_id, job_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def get_resume_applications(self, resume_name: str) -> list[dict[str, Any]]:
		"""查看某份简历投递的所有职位"""
		rows = self._conn.execute(
			"SELECT resume_name, security_id, job_id, job_title, company, status, notes, linked_at, updated_at "
			"FROM resume_job_links WHERE resume_name = ? ORDER BY updated_at DESC",
			(resume_name,),
		).fetchall()
		return [
			{
				"resume_name": row[0],
				"security_id": row[1],
				"job_id": row[2],
				"job_title": row[3],
				"company": row[4],
				"status": row[5],
				"notes": row[6],
				"linked_at": row[7],
				"updated_at": row[8],
			}
			for row in rows
		]

	def get_job_resumes(self, security_id: str, job_id: str) -> list[dict[str, Any]]:
		"""查看某职位关联的所有简历版本"""
		rows = self._conn.execute(
			"SELECT resume_name, security_id, job_id, job_title, company, status, notes, linked_at, updated_at "
			"FROM resume_job_links WHERE security_id = ? AND job_id = ? ORDER BY updated_at DESC",
			(security_id, job_id),
		).fetchall()
		return [
			{
				"resume_name": row[0],
				"security_id": row[1],
				"job_id": row[2],
				"job_title": row[3],
				"company": row[4],
				"status": row[5],
				"notes": row[6],
				"linked_at": row[7],
				"updated_at": row[8],
			}
			for row in rows
		]

	def remove_job_link(self, resume_name: str, security_id: str, job_id: str) -> bool:
		"""移除简历职位关联"""
		cursor = self._conn.execute(
			"DELETE FROM resume_job_links WHERE resume_name = ? AND security_id = ? AND job_id = ?",
			(resume_name, security_id, job_id),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def close(self) -> None:
		self._conn.close()

	def __enter__(self) -> "CacheStore":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()
