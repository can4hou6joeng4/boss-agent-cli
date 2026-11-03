import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

from boss_agent_cli.services.job_identity import deduplicate_job_result

_SEARCH_TTL = 86400  # 24 hours
_MAX_SEARCH_CACHE = 100


class SavedSearchParamsError(ValueError):
	"""`saved_searches.params` 不是可用的 JSON object（写坏或结构漂移）。

	预设 / watch 都是本地可重建数据：条目本身还在，但读不出参数就无法执行。
	调用方应输出信封并提示删除后重建，而不是让 `json.loads` 抛 traceback。
	"""

	def __init__(self, name: str, detail: str) -> None:
		super().__init__(f"预设 '{name}' 的参数无法解析（{detail}）")
		self.name = name
		self.detail = detail


# saved_searches.params 的已知键全部是字符串或 null（`active` 也一样）。
# 合法 JSON 但值类型漂移（数组、数字、对象）时，后续 `.split()` / `.strip()`
# 会抛 AttributeError，被顶层兜成 INTERNAL_ERROR；那既不是内部缺陷，也没有
# 「删掉重建」这条唯一可行的修复路径，必须在读取边界按结构损坏处理。
_SAVED_SEARCH_STRING_KEYS = (
	"query",
	"city",
	"salary",
	"experience",
	"education",
	"industry",
	"scale",
	"stage",
	"job_type",
	"welfare",
	"active",
)


def _decode_saved_params(name: str, raw: Any) -> dict[str, Any]:
	try:
		parsed = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
	except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
		raise SavedSearchParamsError(name, str(exc)) from exc
	if not isinstance(parsed, dict):
		raise SavedSearchParamsError(name, "顶层必须是 JSON object")
	for key in _SAVED_SEARCH_STRING_KEYS:
		value = parsed.get(key)
		if value is None or isinstance(value, str):
			continue
		if key == "welfare" and isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
			# `boss wizard candidate_watch` 早期版本会把福利条件按数组写进来，
			# `watch run` 随后在 `.split(",")` 上抛 AttributeError。读的时候按
			# CLI `--welfare` 的逗号分隔写法归一，旧记录仍然可用。
			parsed[key] = ",".join(item.strip() for item in value if item and item.strip())
			continue
		raise SavedSearchParamsError(name, f"{key} 必须是字符串（收到 {type(value).__name__}）")
	return cast("dict[str, Any]", parsed)


_RECORD_LABELS = {
	"crawl_run": "crawl 任务",
	"crawl_job": "crawl 结果",
	"workflow_run": "workflow 任务",
	"workflow_step": "workflow 步骤",
}


class CacheRecordInvalid(ValueError):
	"""本地任务记录里的 JSON 列无法解析（写坏或结构漂移）。

	crawl / workflow 的断点只存在本地：读不出记录就无法安全恢复，重试同样的
	命令只会再失败一次。调用方应输出信封并提示重新创建任务，而不是让
	`json.loads` 抛 traceback。
	"""

	def __init__(self, kind: str, record_id: str, column: str, detail: str) -> None:
		label = _RECORD_LABELS.get(kind, kind)
		super().__init__(f"本地 {label} {record_id} 的 {column} 无法解析（{detail}）")
		self.kind = kind
		self.record_id = record_id
		self.column = column


def _decode_record(kind: str, record_id: str, column: str, raw: Any, *, expected: type | None = None) -> Any:
	try:
		parsed = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
	except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
		raise CacheRecordInvalid(kind, record_id, column, str(exc)) from exc
	# 结构漂移（合法 JSON 但不是预期的容器）与语法损坏等价：后续按 dict / list
	# 使用时同样是 AttributeError traceback，必须在读取边界一次拦下。
	if expected is dict and not isinstance(parsed, dict):
		raise CacheRecordInvalid(kind, record_id, column, "顶层必须是 JSON object")
	if expected is list and not isinstance(parsed, list):
		raise CacheRecordInvalid(kind, record_id, column, "顶层必须是 JSON array")
	return parsed


def _decode_optional_record(
	kind: str, record_id: str, column: str, raw: Any, *, expected: type | None = None
) -> Any:
	return None if not raw else _decode_record(kind, record_id, column, raw, expected=expected)


def _validate_workflow_params(run_id: str, params: dict[str, Any]) -> dict[str, Any]:
	"""workflow_runs.params 的嵌套结构同样要在读取边界校验。

	顶层是合法 JSON object 但 `inputs` 被写成数组 / `requested_steps` 被写成
	字符串时，恢复任务会在 `dict(params["inputs"])` 抛 TypeError（traceback、
	没有信封），或把 `"auth_status"` 拆成单字符步骤。这类结构漂移与顶层损坏
	等价，统一按记录损坏处理。
	"""
	inputs = params.get("inputs")
	if inputs is not None and not isinstance(inputs, dict):
		raise CacheRecordInvalid("workflow_run", run_id, "params.inputs", "必须是 JSON object")
	steps = params.get("requested_steps")
	if steps is not None and (not isinstance(steps, list) or not all(isinstance(item, str) for item in steps)):
		raise CacheRecordInvalid("workflow_run", run_id, "params.requested_steps", "必须是字符串数组")
	return params


_SQLITE_HEADER = b"SQLite format 3\x00"


class CacheDatabaseInvalid(Exception):
	"""`cache/boss_agent.db` 不是可用的 SQLite 库（磁盘损坏、被别的文件占位）。

	缓存库只保存本地数据：打不开时命令应输出信封并提示备份后重建，而不是把
	`sqlite3.DatabaseError` traceback 甩给 Agent。用独立基类（不是 ValueError /
	RuntimeError）是为了不被命令层的兜底分类误吞。
	"""

	def __init__(self, path: Path, detail: str) -> None:
		super().__init__(f"本地缓存库 {path} 无法打开（{detail}）")
		self.path = path
		self.detail = detail


def _has_sqlite_header(path: Path) -> bool:
	try:
		with path.open("rb") as handle:
			return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
	except OSError:
		return False


class CacheStore:
	def __init__(self, db_path: Path, *, search_ttl_seconds: int = _SEARCH_TTL) -> None:
		self._db_path = db_path
		self._search_ttl = search_ttl_seconds
		db_path.parent.mkdir(parents=True, exist_ok=True)
		try:
			self._conn = sqlite3.connect(str(db_path))
			self._conn.execute("PRAGMA journal_mode=WAL")
			self._init_tables()
		except sqlite3.OperationalError as exc:
			# 库文件头正常时多为并发锁或磁盘压力，按原错误上抛（重试可能有效）；
			# 头不对才说明这个文件根本不是 SQLite 库，只能备份后重建。
			if _has_sqlite_header(db_path):
				raise
			raise CacheDatabaseInvalid(db_path, str(exc)) from exc
		except sqlite3.DatabaseError as exc:
			raise CacheDatabaseInvalid(db_path, str(exc)) from exc

	def _init_tables(self) -> None:
		self._conn.executescript("""
			CREATE TABLE IF NOT EXISTS greet_records (
				job_id TEXT NOT NULL,
				account TEXT NOT NULL DEFAULT '',
				security_id TEXT NOT NULL,
				greeted_at REAL NOT NULL,
				PRIMARY KEY (account, job_id, security_id)
			);
			CREATE TABLE IF NOT EXISTS recruiter_greet_records (
				geek_id TEXT NOT NULL,
				job_id TEXT NOT NULL,
				status TEXT NOT NULL,
				updated_at REAL NOT NULL,
				PRIMARY KEY (geek_id, job_id)
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
				job_id TEXT NOT NULL,
				account TEXT NOT NULL DEFAULT '',
				security_id TEXT NOT NULL,
				applied_at REAL NOT NULL,
				PRIMARY KEY (account, job_id, security_id)
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
				lid TEXT NOT NULL DEFAULT '',
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
				CREATE TABLE IF NOT EXISTS workflow_runs (
					run_id TEXT PRIMARY KEY,
					role TEXT NOT NULL,
					platform TEXT NOT NULL,
					goal TEXT NOT NULL,
					mode TEXT NOT NULL,
					status TEXT NOT NULL,
					current_step TEXT,
					params TEXT NOT NULL,
					last_result TEXT,
					error TEXT,
					created_at REAL NOT NULL,
					updated_at REAL NOT NULL
				);
				CREATE TABLE IF NOT EXISTS workflow_steps (
					run_id TEXT NOT NULL,
					step_name TEXT NOT NULL,
					ordinal INTEGER NOT NULL,
					status TEXT NOT NULL,
					result TEXT,
					error TEXT,
					started_at REAL,
					finished_at REAL,
					PRIMARY KEY (run_id, step_name)
				);
				CREATE INDEX IF NOT EXISTS workflow_steps_run_ordinal
					ON workflow_steps(run_id, ordinal);
			""")
		self._migrate_shortlist_records()
		self._migrate_crawl_runs()
		self._migrate_crawl_jobs()
		self._migrate_dedupe_keys()

	def _migrate_dedupe_keys(self) -> None:
		"""把 greet/apply 记录的键换成「账号指纹 + 稳定 job_id」。

		平台在每次搜索、推荐或列表请求里都会换发新的 securityId，
		encryptJobId（JobItem.job_id）才跨请求稳定。旧表以 security_id
		为主键，第二次请求拿到的令牌查不到上次的记录，跨会话去重因此必然
		失效，同一职位会被重复打招呼、重复投递。

		旧记录没有账号归属，account 留空；它们仍可按 security_id 命中，
		但不会被当成当前账号已验证的去重依据，避免跨账号误拦。
		"""
		tables = (("greet_records", "greeted_at"), ("apply_records", "applied_at"))

		def current_pk(table: str) -> set[str]:
			return {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall() if row[5]}

		wanted = {"account", "job_id", "security_id"}
		if all(current_pk(table) == wanted for table, _ in tables):
			return
		# 需要迁移时才取写锁。整个重建过程放在一个写事务里：迁移中途崩溃不会留下
		# 缺表或半迁移状态，并发的第二个进程拿到锁后会看到新主键，不会重复迁移
		# 把账号指纹抹掉。
		self._conn.execute("BEGIN IMMEDIATE")
		try:
			for table, time_column in (("greet_records", "greeted_at"), ("apply_records", "applied_at")):
				columns = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
				# table_info 按建表列序返回，pk 字段是该列在主键中的序号，不是主键顺序；
				# 必须按集合比较，否则每次打开都会重建表并把 account 抹回空串。
				pk_columns = {row[1] for row in columns if row[5]}
				if pk_columns == {"account", "job_id", "security_id"}:
					continue
				account_select = "COALESCE(account, '')" if any(row[1] == "account" for row in columns) else "''"
				self._conn.execute(f"DROP TABLE IF EXISTS {table}_new")
				self._conn.execute(f"""
					CREATE TABLE {table}_new (
						job_id TEXT NOT NULL,
						account TEXT NOT NULL DEFAULT '',
						security_id TEXT NOT NULL,
						{time_column} REAL NOT NULL,
						PRIMARY KEY (account, job_id, security_id)
					)
				""")
				self._conn.execute(f"""
					INSERT OR REPLACE INTO {table}_new (job_id, account, security_id, {time_column})
						SELECT job_id, {account_select}, security_id, {time_column}
						FROM {table} WHERE job_id != ''
				""")
				self._conn.execute(f"DROP TABLE {table}")
				self._conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
			self._conn.commit()
		except Exception:
			self._conn.rollback()
			raise

	def _migrate_shortlist_records(self) -> None:
		columns = {
			row[1]
			for row in self._conn.execute("PRAGMA table_info(shortlist_records)").fetchall()
		}
		if "tags" not in columns:
			self._conn.execute("ALTER TABLE shortlist_records ADD COLUMN tags TEXT DEFAULT ''")
		if "note" not in columns:
			self._conn.execute("ALTER TABLE shortlist_records ADD COLUMN note TEXT DEFAULT ''")
		if "lid" not in columns:
			# 未记录的访问上下文保持未知；不按 job_id 合并或替换历史记录。
			self._conn.execute("ALTER TABLE shortlist_records ADD COLUMN lid TEXT NOT NULL DEFAULT ''")
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
			"run_id": row[0],
			"params": _decode_record("crawl_run", run_id, "params", row[1], expected=dict),
			"status": row[2],
			"stop_requested": bool(row[3]),
			"requests_attempted": int(row[4]), "detail_requests_attempted": int(row[5]), "elapsed_seconds": int(row[6]),
			"next_page": row[7], "list_finished": bool(row[8]), "output_dir": row[9], "error": row[10],
			"hook_results": _decode_record("crawl_run", run_id, "hook_results", row[11], expected=list),
			"created_at": row[12], "updated_at": row[13],
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
		return {
			"selector": row[0], "page_no": row[1],
			"payload": _decode_record("crawl_job", job_key, "payload", row[2], expected=dict), "detail_done": bool(row[3]),
		}

	def list_crawl_jobs(self, run_id: str) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT job_key, selector, page_no, payload, detail_done FROM crawl_jobs WHERE run_id = ? ORDER BY page_no, rowid",
			(run_id,),
		).fetchall()
		return [
			{
				"job_key": row[0], "selector": row[1], "page_no": row[2],
				"payload": _decode_record("crawl_job", str(row[0]), "payload", row[3], expected=dict), "detail_done": bool(row[4]),
			}
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

	def reserve_crawl_budget(self, budget_key: str, *, now: float, interval: float) -> float:
		"""Atomically reserve the next request slot and return seconds to wait.

		``BEGIN IMMEDIATE`` serializes reservations made by separate CLI processes
		that share this SQLite database.  The stored timestamp may be in the future:
		it represents an already-reserved request slot, not merely request history.
		"""
		try:
			self._conn.execute("BEGIN IMMEDIATE")
			row = self._conn.execute(
				"SELECT last_request_at FROM crawl_budget WHERE budget_key = ?",
				(budget_key,),
			).fetchone()
			last_reserved = float(row[0]) if row is not None else None
			reserved_at = now if last_reserved is None else max(now, last_reserved + interval)
			self._conn.execute(
				"INSERT OR REPLACE INTO crawl_budget (budget_key, last_request_at) VALUES (?, ?)",
				(budget_key, reserved_at),
			)
			self._conn.commit()
		except Exception:
			self._conn.rollback()
			raise
		return max(0.0, reserved_at - now)

	# ── Shared wizard / headless workflow state ─────────────────────

	def create_workflow_run(
		self,
		run_id: str,
		*,
		role: str,
		platform: str,
		goal: str,
		mode: str,
		params: dict[str, Any],
		steps: tuple[str, ...],
	) -> None:
		now = time.time()
		params_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
		with self._conn:
			self._conn.execute(
				"INSERT INTO workflow_runs "
				"(run_id, role, platform, goal, mode, status, current_step, params, last_result, error, created_at, updated_at) "
				"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
				(run_id, role, platform, goal, mode, "pending", None, params_json, None, None, now, now),
			)
			self._conn.executemany(
				"INSERT INTO workflow_steps "
				"(run_id, step_name, ordinal, status, result, error, started_at, finished_at) "
				"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
				[
					(run_id, step_name, ordinal, "pending", None, None, None, None)
					for ordinal, step_name in enumerate(steps)
				],
			)

	def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
		row = self._conn.execute(
			"SELECT run_id, role, platform, goal, mode, status, current_step, params, last_result, error, created_at, updated_at "
			"FROM workflow_runs WHERE run_id = ?",
			(run_id,),
		).fetchone()
		if row is None:
			return None
		return {
			"run_id": row[0],
			"role": row[1],
			"platform": row[2],
			"goal": row[3],
			"mode": row[4],
			"status": row[5],
			"current_step": row[6],
			"params": _validate_workflow_params(
				run_id, _decode_record("workflow_run", run_id, "params", row[7], expected=dict)
			),
			"last_result": _decode_optional_record("workflow_run", run_id, "last_result", row[8], expected=dict),
			"error": _decode_optional_record("workflow_run", run_id, "error", row[9], expected=dict),
			"created_at": row[10],
			"updated_at": row[11],
		}

	def list_workflow_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
		runs, _ = self.list_workflow_runs_with_corrupt(limit=limit)
		return runs

	def list_workflow_runs_with_corrupt(self, *, limit: int = 20) -> tuple[list[dict[str, Any]], list[str]]:
		"""按更新时间列出 workflow，跳过记录损坏的行并回报它们的 run_id。

		单行写坏不该让整个任务列表不可用：坏行本身也无法恢复，保留条目名称
		交给调用方提示重建。
		"""
		rows = self._conn.execute(
			"SELECT run_id FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
			(max(1, limit),),
		).fetchall()
		runs: list[dict[str, Any]] = []
		corrupt: list[str] = []
		for row in rows:
			run_id = str(row[0])
			try:
				run = self.get_workflow_run(run_id)
			except CacheRecordInvalid:
				corrupt.append(run_id)
				continue
			if run is not None:
				runs.append(run)
		return runs, corrupt

	def list_workflow_steps(self, run_id: str) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT step_name, ordinal, status, result, error, started_at, finished_at "
			"FROM workflow_steps WHERE run_id = ? ORDER BY ordinal",
			(run_id,),
		).fetchall()
		return [
			{
				"step_name": row[0],
				"ordinal": row[1],
				"status": row[2],
				"result": _decode_optional_record("workflow_step", f"{run_id}/{row[0]}", "result", row[3], expected=dict),
				"error": _decode_optional_record("workflow_step", f"{run_id}/{row[0]}", "error", row[4], expected=dict),
				"started_at": row[5],
				"finished_at": row[6],
			}
			for row in rows
		]

	def update_workflow_run(
		self,
		run_id: str,
		*,
		status: str,
		current_step: str | None,
		last_result: dict[str, Any] | None = None,
		error: dict[str, Any] | None = None,
		preserve_stopped: bool = False,
	) -> bool:
		cursor = self._conn.execute(
			"UPDATE workflow_runs SET status = CASE WHEN ? AND status = 'stopped' THEN status ELSE ? END, "
			"current_step = ?, last_result = ?, error = ?, updated_at = ? "
			"WHERE run_id = ?",
			(
				preserve_stopped,
				status,
				current_step,
				json.dumps(last_result, ensure_ascii=False, sort_keys=True) if last_result is not None else None,
				json.dumps(error, ensure_ascii=False, sort_keys=True) if error is not None else None,
				time.time(),
				run_id,
			),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def update_workflow_step(
		self,
		run_id: str,
		step_name: str,
		*,
		status: str,
		result: dict[str, Any] | None = None,
		error: dict[str, Any] | None = None,
	) -> bool:
		now = time.time()
		started_at = now if status == "running" else None
		finished_at = now if status in {"completed", "failed", "waiting_input", "stopped"} else None
		cursor = self._conn.execute(
			"UPDATE workflow_steps SET status = ?, result = ?, error = ?, "
			"started_at = COALESCE(started_at, ?), finished_at = COALESCE(?, finished_at) "
			"WHERE run_id = ? AND step_name = ?",
			(
				status,
				json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None,
				json.dumps(error, ensure_ascii=False, sort_keys=True) if error is not None else None,
				started_at,
				finished_at,
				run_id,
				step_name,
			),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def request_workflow_stop(self, run_id: str) -> bool:
		cursor = self._conn.execute(
			"UPDATE workflow_runs SET status = ?, updated_at = ? WHERE run_id = ? "
			"AND status NOT IN (?, ?)",
			("stopped", time.time(), run_id, "completed", "stopped"),
		)
		self._conn.commit()
		return cursor.rowcount > 0

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

	def is_greeted(self, security_id: str = "", *, job_id: str = "", account: str = "") -> bool:
		"""查询招呼记录：有账号作用域和 job_id 时按两者查，否则按 security_id。

		securityId 每次请求都会轮换，不能用于跨请求去重；encryptJobId 稳定，
		但只按它合并会把另一个账号对同一职位的打招呼算到当前账号上，所以
		两者必须同时有账号指纹（AuthManager.account_fingerprint）。
		"""
		if account and job_id:
			row = self._conn.execute(
				"SELECT 1 FROM greet_records WHERE account = ? AND job_id = ?",
				(account, job_id),
			).fetchone()
		elif security_id:
			row = self._conn.execute(
				"SELECT 1 FROM greet_records WHERE security_id = ?",
				(security_id,),
			).fetchone()
		else:
			return False
		return row is not None

	def claim_recruiter_greet(self, geek_id: str, job_id: str) -> str | None:
		"""原子预约首次招呼；重复调用返回状态，不依赖会轮换的 securityId。

		招聘职位和候选人的加密 ID 组成业务键，与求职者 greet_records 隔离。
		进程退出或响应丢失时保留 pending，禁止自动再次发送。
		"""
		with self._conn:
			cursor = self._conn.execute(
				"INSERT OR IGNORE INTO recruiter_greet_records VALUES (?, ?, ?, ?)",
				(geek_id, job_id, "pending", time.time()),
			)
			if cursor.rowcount:
				return None
			row = self._conn.execute(
				"SELECT status FROM recruiter_greet_records WHERE geek_id = ? AND job_id = ?",
				(geek_id, job_id),
			).fetchone()
			return str(row[0])

	def record_recruiter_greet(self, geek_id: str, job_id: str, *, status: Literal["sent", "quota_limited"] = "sent") -> None:
		with self._conn:
			self._conn.execute(
				"UPDATE recruiter_greet_records SET status = ?, updated_at = ? WHERE geek_id = ? AND job_id = ?",
				(status, time.time(), geek_id, job_id),
			)

	def release_recruiter_greet(self, geek_id: str, job_id: str) -> bool:
		"""撤销确定未发出的预约；只删 pending，已发送或结果未知的记录保持原样。

		仅用于「请求在调度前就被预算/通道挡下」的路径：调用方已证明请求没有离开本机，
		保留 pending 只会让这个候选人在瞬态故障后永久不可重试。
		"""
		with self._conn:
			cursor = self._conn.execute(
				"DELETE FROM recruiter_greet_records WHERE geek_id = ? AND job_id = ? AND status = 'pending'",
				(geek_id, job_id),
			)
			return bool(cursor.rowcount)

	def get_job_id(self, security_id: str) -> str | None:
		row = self._conn.execute(
			"SELECT job_id FROM greet_records WHERE security_id = ? ORDER BY greeted_at DESC LIMIT 1",
			(security_id,),
		).fetchone()
		return row[0] if row else None

	def record_greet(self, security_id: str, job_id: str, *, account: str = "") -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO greet_records (account, job_id, security_id, greeted_at) VALUES (?, ?, ?, ?)",
			(account, job_id, security_id, time.time()),
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
			"params": _decode_saved_params(str(row[0]), row[1]),
			"created_at": row[2],
			"updated_at": row[3],
		}

	def list_saved_searches(self) -> list[dict[str, Any]]:
		items, _ = self.list_saved_searches_with_corrupt()
		return items

	def list_saved_searches_with_corrupt(self) -> tuple[list[dict[str, Any]], list[str]]:
		"""列出参数可用的预设，并单独返回参数损坏的条目标记。

		列表命令不能因为一条坏数据整体失败，也不能让坏条目假装不存在：
		调用方据此提示删除重建。
		"""
		rows = self._conn.execute(
			"SELECT name, params, created_at, updated_at FROM saved_searches ORDER BY updated_at DESC"
		).fetchall()
		items: list[dict[str, Any]] = []
		corrupt: list[str] = []
		for row in rows:
			try:
				params = _decode_saved_params(str(row[0]), row[1])
			except SavedSearchParamsError:
				corrupt.append(str(row[0]))
				continue
			items.append(
				{
					"name": row[0],
					"params": params,
					"created_at": row[2],
					"updated_at": row[3],
				}
			)
		return items, corrupt

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
	def _make_watch_job_key(item: dict[str, Any], *, account: str = "") -> str:
		security_id = item.get("security_id") or item.get("securityId") or ""
		job_id = item.get("job_id") or item.get("encryptJobId") or ""
		# 有账号指纹时按稳定的 encryptJobId 去重：securityId 每次搜索都会轮换，
		# 旧的复合键会让同一职位在每轮 watch 里反复算作新增。没有账号指纹时
		# 保留旧复合键，避免把无归属的旧记录当成当前账号的已见记录。
		if account and job_id:
			return f"{account}:{job_id}"
		if security_id or job_id:
			return f"{security_id}:{job_id}"
		raw = json.dumps(item, sort_keys=True, ensure_ascii=False)
		return hashlib.sha256(raw.encode()).hexdigest()

	def record_watch_results(self, search_name: str, items: list[dict[str, Any]], *, account: str = "") -> dict[str, Any]:
		# 只去重本轮完整列表项；有账号指纹时跨 securityId 轮换按稳定 job_id 合并。
		items = deduplicate_job_result(items)
		now = time.time()
		new_items = []
		seen_count = 0
		for item in items:
			job_key = self._make_watch_job_key(item, account=account)
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

	def is_applied(self, security_id: str, job_id: str, *, account: str = "") -> bool:
		"""判断是否已投递：有账号作用域和 job_id 时按两者查，否则按 security_id。

		理由同 is_greeted：securityId 每次请求轮换，job_id 稳定但不能跨账号合并。
		"""
		if account and job_id:
			row = self._conn.execute(
				"SELECT 1 FROM apply_records WHERE account = ? AND job_id = ?",
				(account, job_id),
			).fetchone()
		elif security_id and job_id:
			# 没有账号作用域时保留旧语义：(security_id, job_id) 组合命中
			row = self._conn.execute(
				"SELECT 1 FROM apply_records WHERE security_id = ? AND job_id = ?",
				(security_id, job_id),
			).fetchone()
		elif security_id:
			row = self._conn.execute(
				"SELECT 1 FROM apply_records WHERE security_id = ?",
				(security_id,),
			).fetchone()
		else:
			row = None
		return row is not None

	def record_apply(self, security_id: str, job_id: str, *, account: str = "") -> None:
		self._conn.execute(
			"INSERT OR REPLACE INTO apply_records (account, job_id, security_id, applied_at) VALUES (?, ?, ?, ?)",
			(account, job_id, security_id, time.time()),
		)
		self._conn.commit()

	def is_shortlisted(self, security_id: str, job_id: str) -> bool:
		row = self._conn.execute(
			"SELECT 1 FROM shortlist_records WHERE security_id = ? AND job_id = ?",
			(security_id, job_id),
		).fetchone()
		return row is not None

	def add_shortlist(self, item: dict[str, Any]) -> dict[str, Any]:
		"""加入候选池；同一主键重复加入只刷新本次给出的字段。

		`INSERT OR REPLACE` 会把整行替换掉，连同用户后来在本地打的标签、备注和
		首次加入时间一起清空。重新 `shortlist add` 同一职位（脚本重跑、换设备后
		再导一次）就会静默丢掉这些本地数据。这里改成冲突时按字段更新：本次没给
		空值的列保留原值，`created_at` 始终保留首次加入时间。
		"""
		security_id = str(item.get("security_id") or "")
		job_id = str(item.get("job_id") or "")
		source = str(item.get("source") or "")
		lid_value = item.get("lid")
		lid = lid_value if isinstance(lid_value, str) else ""
		if not lid:
			lid = self._crawl_shortlist_lid(security_id, job_id, source)
		self._conn.execute(
			"INSERT INTO shortlist_records "
			"(security_id, job_id, title, company, city, salary, source, tags, note, lid, created_at) "
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
			"COALESCE(NULLIF(?, ''), (SELECT lid FROM shortlist_records WHERE security_id = ? AND job_id = ?), ''), ?) "
			"ON CONFLICT(security_id, job_id) DO UPDATE SET "
			"title = COALESCE(NULLIF(excluded.title, ''), shortlist_records.title), "
			"company = COALESCE(NULLIF(excluded.company, ''), shortlist_records.company), "
			"city = COALESCE(NULLIF(excluded.city, ''), shortlist_records.city), "
			"salary = COALESCE(NULLIF(excluded.salary, ''), shortlist_records.salary), "
			"source = COALESCE(NULLIF(excluded.source, ''), shortlist_records.source), "
			"tags = COALESCE(NULLIF(excluded.tags, ''), shortlist_records.tags), "
			"note = COALESCE(NULLIF(excluded.note, ''), shortlist_records.note), "
			"lid = excluded.lid",
			(
				security_id,
				job_id,
				item.get("title", ""),
				item.get("company", ""),
				item.get("city", ""),
				item.get("salary", ""),
				source,
				self._serialize_shortlist_tags(item.get("tags", [])),
				item.get("note", ""),
				lid,
				security_id,
				job_id,
				time.time(),
			),
		)
		self._conn.commit()
		stored = self.get_shortlist_item(security_id, job_id)
		return stored if stored is not None else {}

	def get_shortlist_item(self, security_id: str, job_id: str) -> dict[str, Any] | None:
		"""按主键读取一条候选池记录，字段形状与 `list_shortlist` 一致。"""
		row = self._conn.execute(
			"SELECT security_id, job_id, title, company, city, salary, source, tags, note, created_at, lid "
			"FROM shortlist_records WHERE security_id = ? AND job_id = ?",
			(security_id, job_id),
		).fetchone()
		if row is None:
			return None
		return self._shortlist_row_to_dict(row)

	def add_shortlist_batch(self, items: list[dict[str, Any]], source: str = "favorites") -> dict[str, int]:
		"""批量导入候选池：按 job_id 去重，刷新 favorites 访问 ID，缺主键则跳过。

		保留既有按 job_id 去重兼容行为（实测 C3 的两次同职位请求 sid 不同）；
		此历史逻辑尚无已验证的账号/角色作用域，不能据此建立跨账号身份。
		已有同来源记录刷新 security_id 与同一输入的 lid，但保留用户标签、备注、来源
		和 created_at；其他来源记录保持不变，不推断访问上下文有效期。
		`with self._conn:` 上下文保证中途异常自动回滚（deferred 隔离下避免部分批次
		被下一次 commit 连带提交）。返回 {imported_count, existing_count, skipped_count}。
		"""
		imported_count = 0
		existing_count = 0
		skipped_count = 0
		with self._conn:
			existing_by_job_id: dict[str, tuple[str, str]] = {
				str(row[1]): (str(row[0]), str(row[2]))
				for row in self._conn.execute(
					"SELECT security_id, job_id, source FROM shortlist_records WHERE job_id != '' "
					"ORDER BY created_at ASC"
				).fetchall()
			}
			for item in items:
				security_id = str(item.get("security_id") or "")
				job_id = str(item.get("job_id") or "")
				lid_value = item.get("lid")
				lid = lid_value if isinstance(lid_value, str) else ""
				if not security_id or not job_id:
					skipped_count += 1
					continue
				if job_id in existing_by_job_id:
					old_security_id, old_source = existing_by_job_id[job_id]
					if old_source == source and old_security_id != security_id:
						self._conn.execute(
							# 原兼容刷新保留；动态上下文必须来自同一条输入，缺 lid 时不能拼上旧 lid。
							"UPDATE shortlist_records SET security_id = ?, lid = ? "
							"WHERE security_id = ? AND job_id = ?",
							(security_id, lid, old_security_id, job_id),
						)
						existing_by_job_id[job_id] = (security_id, old_source)
					elif old_source == source and old_security_id == security_id and lid:
						self._conn.execute(
							"UPDATE shortlist_records SET lid = ? WHERE security_id = ? AND job_id = ?",
							(lid, security_id, job_id),
						)
					existing_count += 1
					continue
				cursor = self._conn.execute(
					"INSERT OR IGNORE INTO shortlist_records "
					"(security_id, job_id, title, company, city, salary, source, tags, note, lid, created_at) "
					"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
					(
						security_id,
						job_id,
						str(item.get("title", "") or ""),
						str(item.get("company", "") or ""),
						str(item.get("city", "") or ""),
						str(item.get("salary", "") or ""),
						item.get("source") or source,
						self._serialize_shortlist_tags(item.get("tags", [])),
						str(item.get("note", "") or ""),
						lid,
						time.time(),
					),
				)
				if cursor.rowcount == 1:
					imported_count += 1
					existing_by_job_id[job_id] = (security_id, str(item.get("source") or source))
				else:
					existing_count += 1
		return {
			"imported_count": imported_count,
			"existing_count": existing_count,
			"skipped_count": skipped_count,
		}

	def list_shortlist(self) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT security_id, job_id, title, company, city, salary, source, tags, note, created_at, lid "
			"FROM shortlist_records ORDER BY created_at DESC"
		).fetchall()
		return [self._shortlist_row_to_dict(row) for row in rows]

	def _shortlist_row_to_dict(self, row: Any) -> dict[str, Any]:
		return {
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
			"lid": row[10] or self._crawl_shortlist_lid(str(row[0] or ""), str(row[1] or ""), str(row[6] or "")),
		}

	def _crawl_shortlist_lid(self, security_id: str, job_id: str, source: str) -> str:
		"""复用同一 crawl 记录的上下文；不推断其他来源的 lid。"""
		if not source.startswith("crawl:") or not job_id or not security_id:
			return ""
		run_id = source.removeprefix("crawl:")
		if not run_id:
			return ""
		try:
			item = self.get_crawl_job(run_id, job_id)
		except (ValueError, TypeError):
			return ""
		if item is None:
			return ""
		payload = item.get("payload")
		if not isinstance(payload, dict):
			return ""
		if str(payload.get("job_id") or "") != job_id or str(payload.get("security_id") or "") != security_id:
			return ""
		lid = payload.get("lid")
		return lid if isinstance(lid, str) else ""

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
		"""将简历与职位关联；同一主键重复关联只刷新本次给出的字段。

		`INSERT OR REPLACE` 会整行替换：重复 `resume link` 同一职位（脚本重跑、先把
		职位批量关联一遍再补标题）会把投递状态打回 `prepared`、清空备注，并把首次
		关联时间改成当前时间；本次没传 `--title` / `--company` 时还会用空串覆盖原值。
		冲突时按字段更新，`status` / `notes` / `linked_at` 始终保留。
		"""
		now = time.time()
		self._conn.execute(
			"INSERT INTO resume_job_links "
			"(resume_name, security_id, job_id, job_title, company, status, notes, linked_at, updated_at) "
			"VALUES (?, ?, ?, ?, ?, 'prepared', '', ?, ?) "
			"ON CONFLICT(resume_name, security_id, job_id) DO UPDATE SET "
			"job_title = COALESCE(NULLIF(excluded.job_title, ''), resume_job_links.job_title), "
			"company = COALESCE(NULLIF(excluded.company, ''), resume_job_links.company), "
			"updated_at = excluded.updated_at",
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

	def remove_resume_links(self, resume_name: str) -> int:
		"""删除一份简历的全部职位关联，返回删除条数。

		关联表以简历名为业务键、没有外键约束：简历文件被删除后这些行仍然存在，
		同名重建简历会直接继承旧的投递状态。
		"""
		cursor = self._conn.execute(
			"DELETE FROM resume_job_links WHERE resume_name = ?",
			(resume_name,),
		)
		self._conn.commit()
		return cursor.rowcount

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
