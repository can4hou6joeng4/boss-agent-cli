import hashlib
import json
import math
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, NoReturn, TypeVar, cast, overload

from boss_agent_cli.services.job_identity import deduplicate_job_result

_SEARCH_TTL = 86400  # 24 hours
_MAX_SEARCH_CACHE = 100
_MAX_JOB_DESC_CACHE = 1000
# sqlite3.connect 的默认 busy timeout（秒）。单独成常量是为了让测试把等待压到毫秒级。
_BUSY_TIMEOUT_SECONDS = 5.0


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
	# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError，以及 Python 3.11+ 对
	# 超长整数字面量的位数上限；RecursionError 覆盖深嵌套（3.14 起解析器不再
	# 受限，不能按解释器行为区分）。两者都是确定性损坏，必须报参数无法解析。
	except (ValueError, TypeError, RecursionError) as exc:
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
	"shortlist": "候选池记录",
	"resume_link": "简历投递记录",
}


class CacheWriteRejected(ValueError):
	"""SQLite 拒绝写入（约束冲突、绑定值非法），库本身完好（Issue #1886）。

	唯一 / 非空约束冲突是输入或调用方问题，不是库损坏：不能落成
	``CacheDatabaseInvalid``，否则 CLI 会引导用户备份并删除整个缓存库，
	候选池、预设、watch 历史与任务断点全部作废。
	"""

	def __init__(self, db_path: Path, detail: str) -> None:
		super().__init__(f"本地缓存库 {db_path} 拒绝了本次写入（{detail}）")
		self.path = db_path


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
	except (ValueError, TypeError, RecursionError) as exc:
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


def _decode_text_column(
	kind: str, record_id: str, column: str, raw: Any, *, allow_none: bool = False
) -> str | None:
	"""TEXT 列的类型漂移在读取边界拦下（Issue #1885）。

	SQLite 的 TEXT 亲和性不强制类型：值被写成 BLOB 时读取返回 ``bytes``，
	进入信封后 ``_dumps_envelope`` 只能回退成不含结果、也不含修复指引的固定
	INTERNAL_ERROR，且成功路径照常 exit 0（``ok=false`` 但退出码 0）。
	BLOB 先按 UTF-8 还原（与 #1799 的 tags 同口径），还原失败或其它类型
	按记录损坏上报。
	"""
	if raw is None and allow_none:
		return None
	if isinstance(raw, str):
		return raw
	if isinstance(raw, (bytes, bytearray)):
		try:
			return bytes(raw).decode("utf-8")
		except UnicodeDecodeError as exc:
			raise CacheRecordInvalid(kind, record_id, column, f"不是有效的 UTF-8 文本（{exc}）") from exc
	raise CacheRecordInvalid(kind, record_id, column, f"期望文本，实际是 {type(raw).__name__}")


def _decode_int_column(kind: str, record_id: str, column: str, raw: Any) -> int:
	"""整数列的类型漂移在读取边界拦下并带上列名。

	SQLite 是动态类型：INTEGER 列里被写进文本（手工改库、早期版本写入）时，
	命令层的裸 ``int()`` 会抛 ``ValueError``——``BossCliGroup`` 只兜
	``TypeError / AttributeError / KeyError`` 一类，结果 stdout 为空、只剩
	traceback，连 JSON 信封都没有（Issue #1192）。
	"""
	if isinstance(raw, int) and not isinstance(raw, bool):
		return raw
	raise CacheRecordInvalid(kind, record_id, column, f"期望整数，实际是 {type(raw).__name__}")


def _decode_page_no(kind: str, record_id: str, raw: Any) -> int:
	"""页码读取边界：与 ``crawl_runs.next_page`` 同口径拒绝非正数。

	负页码会让导出与 ``crawl results`` 输出 ``"page": -3`` 这类不存在的位置，
	计数也随之失真（Issue #2031）。
	"""
	page_no = _decode_int_column(kind, record_id, "page_no", raw)
	if page_no < 1:
		raise CacheRecordInvalid(kind, record_id, "page_no", f"必须从第 1 页开始，实际是 {page_no}")
	return page_no


def _decode_bool_column(kind: str, record_id: str, column: str, raw: Any) -> bool:
	"""0/1 整数列；文本 ``"false"`` 被 ``bool()`` 当成 True 同样是类型漂移。"""
	if isinstance(raw, int) and not isinstance(raw, bool) and raw in (0, 1):
		return bool(raw)
	raise CacheRecordInvalid(kind, record_id, column, f"期望 0 或 1，实际是 {type(raw).__name__}")


def _decode_timestamp_column(
	kind: str, record_id: str, column: str, raw: Any, *, allow_none: bool = False
) -> float | None:
	"""时间戳列的数值漂移在读取边界拦下（Issue #1964）。

	REAL 列被写成 BLOB / 文本时，原样进入信封会让序列化兜底成固定
	``INTERNAL_ERROR`` 且成功路径照常 exit 0（与 #1885 的文本列同口径）。
	``inf`` / ``nan`` 仍按原样放行：输出层会把它们写成 null，不影响信封。
	"""
	if raw is None and allow_none:
		return None
	if isinstance(raw, (int, float)) and not isinstance(raw, bool):
		return float(raw)
	raise CacheRecordInvalid(kind, record_id, column, f"期望数值时间戳，实际是 {type(raw).__name__}")


def _tolerated_timestamp(raw: Any, *, default: float = 0.0) -> float:
	"""调度用时间戳的容错读取：漂移值按无历史处理（与 Issue #1206 同口径）。

	``crawl_budget.last_request_at`` 是本地节流元数据，不是任务断点：坏值不能
	拦停采集，更不能被兜成可重试错误。``inf`` / ``nan`` 也不能进入后续的
	``max()`` 与等待计算，否则一个坏值会让调度永远等下去（Issue #1212）。
	有限的远未来值同样超出合法预留窗口，由 ``reserve_crawl_budget`` 判定并
	覆盖自愈（Issue #1422）。
	"""
	try:
		parsed = float(raw)
	except (TypeError, ValueError):
		return default
	return parsed if math.isfinite(parsed) else default


#: ``crawl_budget`` 的合法未来预留窗口（Issue #1422）。
#:
#: 写入方最多把 ``last_request_at`` 推到 ``now + interval``；更远的有限未来值
#: 只能是漂移。它会让 ``reserve_crawl_budget`` 返回一个巨大的等待秒数，并把
#: 值继续推向未来，采集预算永远无法自愈。
_CRAWL_FUTURE_SKEW_SECONDS = 60.0

#: 本地缓存允许的最大未来预留窗口。写入方不应把 created_at 放到当前时间之后；
#: 更远的有限未来值只可能来自时钟回拨或外部写坏的库（Issue #1647）。
_CACHE_FUTURE_SKEW_SECONDS = 60.0


def _cache_entry_expired(created_at: Any, ttl: float) -> bool:
	"""缓存 TTL 判断；``created_at`` 漂移时按已过期处理（Issue #1214）。

	``search_cache`` / ``job_desc_cache`` 是本地可再生成的缓存：坏时间戳只应
	导致一次未命中并删除坏行，不能把 ``TypeError`` 兜成 ``INTERNAL_ERROR``，
	也不能让 crawl 详情阶段永远停在同一个坏行上。
	"""
	parsed = _tolerated_timestamp(created_at, default=-1.0)
	now = time.time()
	return parsed < 0 or parsed > now + _CACHE_FUTURE_SKEW_SECONDS or now - parsed > ttl


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
	if steps is not None:
		if not isinstance(steps, list) or not all(isinstance(item, str) for item in steps):
			raise CacheRecordInvalid("workflow_run", run_id, "params.requested_steps", "必须是字符串数组")
		if not steps:
			# 空计划恢复时不会执行任何步骤，runner 却会直接把它标成 completed：
			# 与 WizardInput.from_mapping 拒绝显式空数组同口径（Issue #1483）。
			raise CacheRecordInvalid(
				"workflow_run",
				run_id,
				"params.requested_steps",
				"不能是空数组；省略该字段才表示执行 goal 的全部步骤",
			)
	return params


_SQLITE_HEADER = b"SQLite format 3\x00"


class CacheDatabaseInvalid(Exception):
	"""`cache/boss_agent.db` 不是可用的 SQLite 库（磁盘损坏、被别的文件占位）。

	也用于打开成功后执行查询时发现的结构漂移（旧版本建的库缺后加的列、迁移
	中断）。缓存库只保存本地数据：命令应输出信封并提示备份后重建，而不是把
	`sqlite3.DatabaseError` traceback 甩给 Agent。用独立基类（不是 ValueError /
	RuntimeError）是为了不被命令层的兜底分类误吞。
	"""

	def __init__(self, path: Path, detail: str) -> None:
		super().__init__(f"本地缓存库 {path} 无法使用（{detail}）")
		self.path = path
		self.detail = detail


class CacheBusyError(Exception):
	"""缓存库正被其他连接写入，SQLite 在 busy timeout 内拿不到写锁。

	与 CacheDatabaseInvalid 区分：库文件完好，等写锁释放后重试即可。此前该错误
	按原样上抛——没有统一装饰器的命令（`crawl stop` 等）stdout 为空并带
	traceback，`crawl resume` 则被兜成可重试的 NETWORK_ERROR，让 Agent 把本地
	锁冲突当成平台网络问题反复重试（Issue #1118）。
	"""

	def __init__(self, path: Path | None, detail: str) -> None:
		location = f"本地缓存库 {path}" if path is not None else "本地缓存库"
		super().__init__(f"{location} 正被其他进程写入（{detail}）")
		self.path = path
		self.detail = detail


def database_path_problem(path: Path) -> str | None:
	"""SQLite 打开前的路径类型判定，返回问题描述；``None`` 表示可以打开。

	``sqlite3.connect`` 会跟随符号链接：链接指向空文件时数据库会直接写进目标
	（数据目录之外），悬空链接会新建目标。目录 / FIFO / 设备只会得到
	"unable to open database file"。``clean`` 已按同一口径拒绝符号链接库
	（#1712），所有打开入口必须一致（Issue #1739）。
	"""
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return None
	if stat.S_ISLNK(mode):
		return "数据库路径是符号链接；SQLite 会跟随链接把数据库写进目标文件"
	if not stat.S_ISREG(mode):
		return "数据库路径不是普通文件（目录 / FIFO / 设备）"
	return None


def is_cache_busy_error(exc: BaseException) -> bool:
	"""SQLite 写锁冲突的判定：busy timeout 内拿不到锁，等锁释放后可重试。"""
	if not isinstance(exc, sqlite3.OperationalError):
		return False
	message = str(exc).lower()
	return "locked" in message or "busy" in message


#: 本地 SQLite 文件/磁盘层面的写失败标记（Issue #1467）。
#:
#: ``attempt to write a readonly database`` 是目录或文件只读，``disk I/O error``
#: 是设备故障，``database or disk is full`` 是磁盘/配额耗尽，``unable to open
#: database file`` 是路径不可打开。它们都不是库内容损坏：删库重建既修不好，
#: 还会白白丢掉候选池与断点，所以必须保留原始 ``OperationalError``，由
#: main/display 的本地 IO 分支给出 ``LOCAL_IO_ERROR``（不可重试）指引。
_CACHE_LOCAL_IO_MARKERS: tuple[str, ...] = (
	"attempt to write a readonly database",
	"disk i/o error",
	"database or disk is full",
	"unable to open database file",
)


def is_cache_local_io_error(exc: BaseException) -> bool:
	"""判断 SQLite 错误是否来自本地文件/磁盘，而不是库内容损坏。

	只认 ``OperationalError``，且与 ``is_cache_busy_error`` 互斥：锁冲突优先按
	可重试的 ``CACHE_BUSY`` 处理（消息里同时带锁与 IO 字样时以锁为准）。
	"""
	if not isinstance(exc, sqlite3.OperationalError):
		return False
	if is_cache_busy_error(exc):
		return False
	message = str(exc).lower()
	return any(marker in message for marker in _CACHE_LOCAL_IO_MARKERS)


def _has_sqlite_header(path: Path) -> bool:
	try:
		with path.open("rb") as handle:
			return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
	except OSError:
		return False


_CursorT = TypeVar("_CursorT", bound=sqlite3.Cursor)


class _GuardedCursor(sqlite3.Cursor):
	"""把取数阶段的 SQLite 数据库错误转成缓存层异常（Issue #1350）。

	多页表的 B 树数据页损坏时，`execute()` 可能已经成功返回首行，直到
	`fetchone` / `fetchall` / 迭代下一行才抛 `sqlite3.DatabaseError`。这些读取
	路径不经过 `_GuardedConnection` 的执行期转换：`crawl status` / `results`
	会拿到裸 `sqlite3.DatabaseError` 只给通用 IO 指引，crawl 执行也把坏库当成
	可恢复的普通停止并无限 resume。这里按 `_translate` 同一口径转换——锁冲突
	仍走 `CacheBusyError`，SQL 写错的 `ProgrammingError` 保持原样上抛。

	只转换由 `_GuardedConnection` 创建的游标；直接拿普通连接构造的游标保持
	原生行为，不去包装 `sqlite3.Connection` 之外的路径。
	"""

	def __init__(self, connection: sqlite3.Connection, /) -> None:
		super().__init__(connection)
		self._guarded: _GuardedConnection | None = connection if isinstance(connection, _GuardedConnection) else None

	def _raise_translated(self, exc: sqlite3.DatabaseError) -> NoReturn:
		if self._guarded is None:
			raise exc
		self._guarded._raise_translated(exc)

	def _cursor_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
		"""调用底层 C 游标方法。

		`sqlite3.Cursor` 的方法不能直接 patch（C 扩展类型），取数阶段坏页的
		测试通过替换这里注入 `database disk image is malformed`。
		"""
		return getattr(super(), method)(*args, **kwargs)

	def _translated_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
		try:
			return self._cursor_call(method, *args, **kwargs)
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)

	def execute(self, *args: Any, **kwargs: Any) -> "_GuardedCursor":
		self._translated_call("execute", *args, **kwargs)
		return self

	def executemany(self, *args: Any, **kwargs: Any) -> "_GuardedCursor":
		self._translated_call("executemany", *args, **kwargs)
		return self

	def executescript(self, *args: Any, **kwargs: Any) -> "_GuardedCursor":
		self._translated_call("executescript", *args, **kwargs)
		return self

	def fetchone(self) -> tuple[Any, ...] | None:
		return cast("tuple[Any, ...] | None", self._translated_call("fetchone"))

	def fetchmany(self, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
		return cast("list[tuple[Any, ...]]", self._translated_call("fetchmany", *args, **kwargs))

	def fetchall(self) -> list[tuple[Any, ...]]:
		return cast("list[tuple[Any, ...]]", self._translated_call("fetchall"))

	def __iter__(self) -> "_GuardedCursor":
		return self

	def __next__(self) -> tuple[Any, ...]:
		return cast("tuple[Any, ...]", self._translated_call("__next__"))


class _GuardedConnection(sqlite3.Connection):
	"""把执行期的 SQLite 数据库错误转成缓存层异常。

	`CacheStore.__init__` 只包住打开与建表阶段；此后的查询出错——旧版本建的库
	缺少后加的列、B 树损坏、库被挂成只读——会以裸 `sqlite3.OperationalError`
	抛到命令层，没有统一装饰器的命令（`shortlist list` / `shortlist add`）
	stdout 为空并带 traceback（Issue #1174）。`clean` / `stats` 自己开的连接
	早已按 `CacheDatabaseInvalid` 归一（Issue #1139），这里把 CacheStore 的
	连接也收进同一口径。`execute` / `cursor` 返回的游标由 `_GuardedCursor`
	包装，取数阶段才暴露的坏页同样转换（Issue #1350）。

	两种错误保持原样上抛：busy 锁冲突由 `CacheBusyError` 单独表示（等锁重试，
	不是数据损坏）；`ProgrammingError` 是代码把 SQL 或绑定参数写错了，不能被
	包装成「本地数据损坏」让用户删库。连接级 `with` 的提交/回滚由 C 实现直接
	调用，不走 Python 覆盖的方法，所以 `__exit__` 也要包一层。
	"""

	db_path: Path
	# 迁移事务进行中：此时的约束冲突说明旧数据自身不兼容（如重复 selector
	# 建唯一索引失败），按库不可用处理，而不是调用方参数错误（Issue #1886）。
	migrating: bool = False

	def __init__(self, database: str, *args: Any, **kwargs: Any) -> None:
		super().__init__(database, *args, **kwargs)
		self.db_path = Path(database)

	def _translate(self, exc: sqlite3.DatabaseError) -> Exception | None:
		if isinstance(exc, sqlite3.ProgrammingError):
			return None
		if isinstance(exc, (sqlite3.IntegrityError, sqlite3.InterfaceError, sqlite3.DataError)):
			if getattr(self, "migrating", False):
				return CacheDatabaseInvalid(self.db_path, str(exc))
			# 约束冲突 / 绑定值非法是输入或调用方问题，库本身健康：
			# 落成 CacheDatabaseInvalid 会让 CLI 引导用户删掉整个缓存库（Issue #1886）。
			return CacheWriteRejected(self.db_path, str(exc))
		if is_cache_busy_error(exc):
			return CacheBusyError(self.db_path, str(exc))
		if is_cache_local_io_error(exc):
			# 只读目录、磁盘满、设备 IO 故障不是库损坏：保留原始 OperationalError，
			# 让 main/display 的本地 IO 分支输出 LOCAL_IO_ERROR，而不是误导用户
			# 备份并删除完好的缓存库（Issue #1467）。
			return None
		return CacheDatabaseInvalid(self.db_path, str(exc))

	def _raise_translated(self, exc: sqlite3.DatabaseError) -> NoReturn:
		translated = self._translate(exc)
		if translated is None:
			raise exc
		raise translated from exc

	@overload
	def cursor(self, factory: None = None) -> sqlite3.Cursor: ...

	@overload
	def cursor(self, factory: Callable[[sqlite3.Connection], _CursorT]) -> _CursorT: ...

	def cursor(self, factory: Callable[[sqlite3.Connection], Any] | None = None) -> Any:
		"""返回受保护游标；显式传入的自定义 factory 保持调用方语义。"""
		return super().cursor(_GuardedCursor if factory is None else factory)

	def _guarded_cursor(self) -> sqlite3.Cursor:
		return super().cursor(_GuardedCursor)

	def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
		# Connection.execute 的默认返回值是普通 sqlite3.Cursor，后续 fetchone /
		# fetchall 不受 _translate 保护；改为在受保护游标上执行（Issue #1350）。
		try:
			return self._guarded_cursor().execute(*args, **kwargs)
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)

	def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
		try:
			return self._guarded_cursor().executemany(*args, **kwargs)
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)

	def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
		try:
			return self._guarded_cursor().executescript(*args, **kwargs)
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)

	def commit(self) -> None:
		try:
			super().commit()
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)

	def rollback(self) -> None:
		try:
			super().rollback()
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_value: BaseException | None,
		traceback: TracebackType | None,
		/,
	) -> Literal[False]:
		try:
			return super().__exit__(exc_type, exc_value, traceback)
		except sqlite3.DatabaseError as exc:
			self._raise_translated(exc)


class CacheStore:
	def __init__(self, db_path: Path, *, search_ttl_seconds: int = _SEARCH_TTL) -> None:
		self._db_path = db_path
		self._search_ttl = search_ttl_seconds
		db_path.parent.mkdir(parents=True, exist_ok=True)
		# 符号链接 / 特殊文件不能作为缓存库：连接会在链接目标处创建或改写数据库
		# （Issue #1739，与 clean 的 #1712 同口径）。
		problem = database_path_problem(db_path)
		if problem is not None:
			raise CacheDatabaseInvalid(db_path, problem)
		try:
			self._conn = sqlite3.connect(
				str(db_path),
				timeout=_BUSY_TIMEOUT_SECONDS,
				factory=_GuardedConnection,
			)
			self._conn.execute("PRAGMA journal_mode=WAL")
			self._init_tables()
		except sqlite3.OperationalError as exc:
			# 库文件头正常时多为并发锁或磁盘压力：锁冲突等锁释放后重试即可，单独
			# 报 CACHE_BUSY；其余仍按原错误上抛（重试可能有效）。
			# 只读目录 / 磁盘满 / 无法打开等本地 IO 失败与库内容无关：连文件头都
			# 读不到时也不能归成 CACHE_DATABASE_INVALID 让人删库（Issue #1467）。
			if is_cache_local_io_error(exc):
				raise
			if _has_sqlite_header(db_path):
				if is_cache_busy_error(exc):
					raise CacheBusyError(db_path, str(exc)) from exc
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
			CREATE TABLE IF NOT EXISTS recruiter_location_records (
				friend_id TEXT NOT NULL PRIMARY KEY,
				job_id TEXT NOT NULL,
				name TEXT NOT NULL,
				status TEXT NOT NULL,
				updated_at REAL NOT NULL
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
			CREATE TABLE IF NOT EXISTS candidate_action_claims (
				account TEXT NOT NULL DEFAULT '',
				job_id TEXT NOT NULL,
				action TEXT NOT NULL,
				security_id TEXT NOT NULL DEFAULT '',
				status TEXT NOT NULL,
				updated_at REAL NOT NULL,
				PRIMARY KEY (account, job_id, action)
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

	@contextmanager
	def _migration_write_transaction(self) -> Iterator[None]:
		"""迁移的写事务：拿到写锁后调用方才重新读取 schema。

		``PRAGMA table_info`` 与 ``ALTER TABLE`` 分开执行时，两个进程可能都看到
		旧列并各自 ALTER，第二个拿到 ``duplicate column name``，被 ``__init__``
		误判成缓存库损坏（Issue #1510）。``BEGIN IMMEDIATE`` 把「检查 + 变更」
		串行化；等锁超时仍按 ``CacheBusyError`` 上报，真实失败（列类型不符、
		唯一索引冲突）原样上抛，不能吞成「无需迁移」。
		"""
		conn = self._conn
		conn.migrating = True
		try:
			self._conn.execute("BEGIN IMMEDIATE")
			try:
				yield
			except BaseException:
				self._conn.rollback()
				raise
			self._conn.commit()
		finally:
			conn.migrating = False

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
		with self._migration_write_transaction():
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
						FROM {table} WHERE job_id IS NOT NULL
				""")
				self._conn.execute(f"DROP TABLE {table}")
				self._conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

	def _migrate_shortlist_records(self) -> None:
		columns = {
			row[1]
			for row in self._conn.execute("PRAGMA table_info(shortlist_records)").fetchall()
		}
		if {"tags", "note", "lid"} <= columns:
			return
		# 无锁快路径只用于跳过已是新结构的库；真正变更前必须拿到写锁并重读列，
		# 否则并发打开的第二个进程会重复 ALTER 并抛 duplicate column（Issue #1510）。
		with self._migration_write_transaction():
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

	def _migrate_crawl_runs(self) -> None:
		columns = {
			row[1]
			for row in self._conn.execute("PRAGMA table_info(crawl_runs)").fetchall()
		}
		if {
			"list_finished",
			"stop_requested",
			"requests_attempted",
			"detail_requests_attempted",
			"elapsed_seconds",
			"hook_results",
		} <= columns:
			return
		with self._migration_write_transaction():
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

	def _migrate_crawl_jobs(self) -> None:
		if self._crawl_jobs_migration_done():
			return
		# 回填 + 唯一索引必须与列检查在同一写事务里：并发进程各自回填会让
		# ``CREATE UNIQUE INDEX`` 撞上重复 selector，或重复执行 ALTER。
		with self._migration_write_transaction():
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

	def _crawl_jobs_migration_done(self) -> bool:
		columns = {row[1] for row in self._conn.execute("PRAGMA table_info(crawl_jobs)").fetchall()}
		if "selector" not in columns:
			return False
		index = self._conn.execute(
			"SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'crawl_jobs_run_selector'"
		).fetchone()
		if index is None:
			return False
		return (
			self._conn.execute(
				"SELECT 1 FROM crawl_jobs WHERE selector IS NULL OR selector = '' LIMIT 1"
			).fetchone()
			is None
		)

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
		record = {
			"run_id": row[0],
			"params": _decode_record("crawl_run", run_id, "params", row[1], expected=dict),
			"status": _decode_text_column("crawl_run", run_id, "status", row[2]),
			"stop_requested": _decode_bool_column("crawl_run", run_id, "stop_requested", row[3]),
			"requests_attempted": _decode_int_column("crawl_run", run_id, "requests_attempted", row[4]),
			"detail_requests_attempted": _decode_int_column("crawl_run", run_id, "detail_requests_attempted", row[5]),
			"elapsed_seconds": _decode_int_column("crawl_run", run_id, "elapsed_seconds", row[6]),
			"next_page": _decode_int_column("crawl_run", run_id, "next_page", row[7]),
			"list_finished": _decode_bool_column("crawl_run", run_id, "list_finished", row[8]),
			"output_dir": _decode_text_column("crawl_run", run_id, "output_dir", row[9]),
			"error": _decode_text_column("crawl_run", run_id, "error", row[10], allow_none=True),
			"hook_results": _decode_record("crawl_run", run_id, "hook_results", row[11], expected=list),
			# crawl 的 updated_at 不参与契约校验（Issue #1192）：后台恢复只拿它做
			# 预检失败时的还原，漂移按 0 降级（Issue #1206），不能在这里报损坏而
			# 打断恢复；归一成数值也保证信封可序列化。
			"created_at": _tolerated_timestamp(row[12]),
			"updated_at": _tolerated_timestamp(row[13]),
		}
		# 范围与文本校验：负的 next_page 会让 resume 反复抓第 1 页并把
		# crawl_jobs.page_no 写成负数，负计数器会推迟预算触发；BLOB 的
		# output_dir 会让 wizard 的 Path() 抛 TypeError（Issue #1325）。
		if record["next_page"] < 1:
			raise CacheRecordInvalid(
				"crawl_run", run_id, "next_page", f"必须从第 1 页开始，实际是 {record['next_page']}"
			)
		# 上界校验：正常写入的 next_page 最多是 pages + 1（最后一页之后）。超大值
		# 会让 resume 的页循环一次都不执行，随即把空结果报成 completed，status 也
		# 会回显一个不存在的页数（Issue #2030）。params.pages 不合法时留给
		# CrawlSettings 的校验，这里只做能做的交叉检查。
		raw_pages = record["params"].get("pages")
		if (
			isinstance(raw_pages, int)
			and not isinstance(raw_pages, bool)
			and raw_pages >= 1
			and record["next_page"] > raw_pages + 1
		):
			raise CacheRecordInvalid(
				"crawl_run",
				run_id,
				"next_page",
				f"超过页数上限 pages + 1（{raw_pages + 1}），实际是 {record['next_page']}",
			)
		for column in ("requests_attempted", "detail_requests_attempted", "elapsed_seconds"):
			if record[column] < 0:
				raise CacheRecordInvalid("crawl_run", run_id, column, f"不能为负数，实际是 {record[column]}")
		for column in ("status", "output_dir", "error"):
			if not isinstance(record[column], str):
				raise CacheRecordInvalid(
					"crawl_run", run_id, column, f"期望字符串，实际是 {type(record[column]).__name__}"
				)
		# 输出目录必须是绝对路径：配置允许相对 `--data-dir`，但落盘的必须是解析
		# 后的绝对路径，否则换个 cwd 的 resume 会把产物写到无关目录（Issue #1481）。
		# 旧版本断点里的相对路径无法安全推断原始位置，按记录损坏 fail-closed。
		output_dir = record["output_dir"]
		if not output_dir.strip():
			raise CacheRecordInvalid(
				"crawl_run", run_id, "output_dir",
				"期望非空绝对路径，实际是空字符串；旧断点无法安全恢复，请重新执行 crawl start 创建任务",
			)
		# NUL 等控制字符能通过「非空绝对路径」检查，但会在 lstat / 导出时抛
		# ValueError，被命令层兜成 INVALID_PARAM，与 status 的 ok:true 口径冲突
		# （Issue #2194）。与 `params.browser.profile_path` 同口径 fail-closed
		# （Issue #1482）：合法路径不可能包含这些字符。
		if any(ord(char) < 0x20 for char in output_dir):
			raise CacheRecordInvalid(
				"crawl_run", run_id, "output_dir",
				f"期望绝对路径，实际包含控制字符 {output_dir!r}；旧断点无法安全恢复，请重新执行 crawl start 创建任务",
			)
		if not Path(output_dir).is_absolute():
			raise CacheRecordInvalid(
				"crawl_run", run_id, "output_dir",
				f"期望绝对路径，实际是相对路径 {output_dir!r}；旧断点无法安全恢复，请重新执行 crawl start 创建任务",
			)
		return record

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

	def mark_crawl_run_active(self, run_id: str, status: str) -> None:
		"""恢复已取得执行权时把状态落成 queued / running（Issue #1771）。

		只改状态、清掉上一轮的错误并刷新时间；断点游标与其他进程写入的停止标记
		保持不动，这样恢复窗口内到达的 `crawl stop` 才会被接受。
		"""
		self._conn.execute(
			"UPDATE crawl_runs SET status = ?, error = '', updated_at = ? WHERE run_id = ?",
			(status, time.time(), run_id),
		)
		self._conn.commit()

	def touch_crawl_run(self, run_id: str) -> None:
		"""刷新排队交接时间，不改变其他进程写入的停止标记。"""
		self._conn.execute("UPDATE crawl_runs SET updated_at = ? WHERE run_id = ?", (time.time(), run_id))
		self._conn.commit()

	def restore_crawl_stop_request(self, run_id: str, *, updated_at: float) -> None:
		"""预检失败时放回先前清除的 stop 标记，并还原清除前的时间戳。

		`clear_crawl_stop_request` 会更新 `updated_at`；失败路径要还原到清除前
		的记录，所以由调用方传入清除前的时间戳。清除之后若又有新的 stop 到达，
		标记保持 1、时间取两者较大的值，不能被旧快照覆盖（Issue #1042 / #1115）。
		"""
		self._conn.execute(
			"UPDATE crawl_runs SET updated_at = CASE WHEN stop_requested = 1 "
			"THEN MAX(updated_at, ?) ELSE ? END, stop_requested = 1 WHERE run_id = ?",
			(updated_at, updated_at, run_id),
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
			"selector": _decode_text_column("crawl_job", job_key, "selector", row[0]),
			"page_no": _decode_page_no("crawl_job", job_key, row[1]),
			"payload": _decode_record("crawl_job", job_key, "payload", row[2], expected=dict),
			"detail_done": _decode_bool_column("crawl_job", job_key, "detail_done", row[3]),
		}

	def list_crawl_jobs(self, run_id: str) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT job_key, selector, page_no, payload, detail_done FROM crawl_jobs WHERE run_id = ? ORDER BY page_no, rowid",
			(run_id,),
		).fetchall()
		records: list[dict[str, Any]] = []
		for row in rows:
			job_key = _decode_text_column("crawl_job", str(row[0]), "job_key", row[0])
			assert isinstance(job_key, str)
			records.append({
				"job_key": job_key,
				"selector": _decode_text_column("crawl_job", job_key, "selector", row[1]),
				"page_no": _decode_page_no("crawl_job", job_key, row[2]),
				"payload": _decode_record("crawl_job", job_key, "payload", row[3], expected=dict),
				"detail_done": _decode_bool_column("crawl_job", job_key, "detail_done", row[4]),
			})
		return records

	def get_crawl_budget(self, budget_key: str) -> float | None:
		row = self._conn.execute("SELECT last_request_at FROM crawl_budget WHERE budget_key = ?", (budget_key,)).fetchone()
		if row is None:
			return None
		last_request_at = _tolerated_timestamp(row[0], default=-1.0)
		return last_request_at if last_request_at >= 0 else None

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
			last_reserved = _tolerated_timestamp(row[0]) if row is not None else None
			# 有限的远未来值与文本 / nan / inf 一样按漂移处理：当作没有预留，
			# 本次预留会覆盖坏值，等待时间回到一个 interval 以内（Issue #1422）。
			if last_reserved is not None and last_reserved > now + interval + _CRAWL_FUTURE_SKEW_SECONDS:
				last_reserved = None
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
			"run_id": _decode_text_column("workflow_run", run_id, "run_id", row[0]),
			"role": _decode_text_column("workflow_run", run_id, "role", row[1]),
			"platform": _decode_text_column("workflow_run", run_id, "platform", row[2]),
			"goal": _decode_text_column("workflow_run", run_id, "goal", row[3]),
			"mode": _decode_text_column("workflow_run", run_id, "mode", row[4]),
			"status": _decode_text_column("workflow_run", run_id, "status", row[5]),
			"current_step": _decode_text_column("workflow_run", run_id, "current_step", row[6], allow_none=True),
			"params": _validate_workflow_params(
				run_id, _decode_record("workflow_run", run_id, "params", row[7], expected=dict)
			),
			"last_result": _decode_optional_record("workflow_run", run_id, "last_result", row[8], expected=dict),
			"error": _decode_optional_record("workflow_run", run_id, "error", row[9], expected=dict),
			"created_at": _decode_timestamp_column("workflow_run", run_id, "created_at", row[10]),
			"updated_at": _decode_timestamp_column("workflow_run", run_id, "updated_at", row[11]),
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
			try:
				run_id = _decode_text_column("workflow_run", str(row[0]), "run_id", row[0])
			except CacheRecordInvalid:
				# BLOB / NULL 的 run_id 此前被 str() 成 "b'...'" / "None"，按它查询
				# 必然查不到，条目从 runs 与 corrupt 两个名单同时消失（Issue #2056）。
				corrupt.append(str(row[0]))
				continue
			if run_id is None:  # allow_none=False 时不会发生；防御性兜底
				corrupt.append(str(row[0]))
				continue
			try:
				run = self.get_workflow_run(run_id)
			except CacheRecordInvalid:
				corrupt.append(run_id)
				continue
			if run is not None:
				runs.append(run)
			else:
				# SELECT 出这一行、按 run_id 却查不到：run_id 是解码后的 BLOB（原值是
				# "b'...'"，必然查不到），或行在两条语句之间被删除。无论如何都不能
				# 让它从 runs 与 corrupt 两个名单同时消失（Issue #2056）。
				corrupt.append(run_id)
		return runs, corrupt

	def list_workflow_steps(self, run_id: str) -> list[dict[str, Any]]:
		rows = self._conn.execute(
			"SELECT step_name, ordinal, status, result, error, started_at, finished_at "
			"FROM workflow_steps WHERE run_id = ? ORDER BY ordinal",
			(run_id,),
		).fetchall()
		records: list[dict[str, Any]] = []
		for row in rows:
			record_id = f"{run_id}/{row[0]}"
			step_name = _decode_text_column("workflow_step", record_id, "step_name", row[0])
			records.append({
				"step_name": step_name,
				# ordinal 此前原样透传：BLOB / 文本漂移进入信封后只能退化成
				# INTERNAL_ERROR，且成功路径 exit 0（Issue #2055）。与其余列同口径。
				"ordinal": _decode_int_column("workflow_step", record_id, "ordinal", row[1]),
				"status": _decode_text_column("workflow_step", record_id, "status", row[2]),
				"result": _decode_optional_record("workflow_step", record_id, "result", row[3], expected=dict),
				"error": _decode_optional_record("workflow_step", record_id, "error", row[4], expected=dict),
				"started_at": _decode_timestamp_column(
					"workflow_step", record_id, "started_at", row[5], allow_none=True
				),
				"finished_at": _decode_timestamp_column(
					"workflow_step", record_id, "finished_at", row[6], allow_none=True
				),
			})
		return records

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

	def set_workflow_step_error(self, run_id: str, step_name: str, error: dict[str, Any] | None) -> bool:
		"""只改步骤的 error，不动状态、结果与时间戳；``None`` 清空。"""
		cursor = self._conn.execute(
			"UPDATE workflow_steps SET error = ? WHERE run_id = ? AND step_name = ?",
			(
				json.dumps(error, ensure_ascii=False, sort_keys=True) if error is not None else None,
				run_id,
				step_name,
			),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	def request_workflow_stop(self, run_id: str) -> bool:
		"""幂等记录停止请求，让重试能补齐尚未完成的内部停止传播。"""
		cursor = self._conn.execute(
			"UPDATE workflow_runs SET status = ?, updated_at = ? WHERE run_id = ? "
			"AND status != ?",
			("stopped", time.time(), run_id, "completed"),
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
	def _deserialize_shortlist_tags(cls, raw: str | bytes | bytearray | None) -> list[str]:
		if not raw:
			return []
		if isinstance(raw, (bytes, bytearray)):
			# SQLite TEXT 列不强制类型：值被写成 BLOB 时不能在 str.split(",") 上
			# 抛 TypeError 让整池条目落成 INTERNAL_ERROR（Issue #1799）。
			try:
				raw = raw.decode("utf-8")
			except UnicodeDecodeError:
				return []
		try:
			parsed = json.loads(raw)
		# 超长整数 / 深嵌套与语法损坏等价：退回旧的逗号分隔写法。
		except (ValueError, RecursionError):
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
		"""把已确认发出的招呼记为 sent/quota_limited。

		预约行被外部清理（如 boss clean --all）时 UPDATE 会命中 0 行：此时本地
		没有任何已招呼记录，静默成功会让下一次 claim 再发一次真实招呼。按
		record_location_send 的口径抛错，由命令层映射成 GREET_RESULT_UNKNOWN。
		"""
		with self._conn:
			cursor = self._conn.execute(
				"UPDATE recruiter_greet_records SET status = ?, updated_at = ? WHERE geek_id = ? AND job_id = ?",
				(status, time.time(), geek_id, job_id),
			)
			if cursor.rowcount != 1:
				raise sqlite3.OperationalError("招聘者招呼记录不存在，无法标记发送结果")

	def location_send_status(self, friend_id: int) -> str | None:
		"""读取工作地点发送记录。没有记录时返回 None，不创建新行。"""
		row = self._conn.execute(
			"SELECT status FROM recruiter_location_records WHERE friend_id = ?",
			(str(friend_id),),
		).fetchone()
		return str(row[0]) if row else None

	def claim_location_send(self, friend_id: int) -> str | None:
		"""原子预约工作地点发送。已有记录时返回状态，禁止对同一会话再发一次。

		进程退出或响应丢失时保留 pending，后续调用不得自动重发。
		"""
		key = str(friend_id)
		with self._conn:
			cursor = self._conn.execute(
				"INSERT OR IGNORE INTO recruiter_location_records (friend_id, job_id, name, status, updated_at) "
				"VALUES (?, '', '', 'pending', ?)",
				(key, time.time()),
			)
			if cursor.rowcount:
				return None
			row = self._conn.execute(
				"SELECT status FROM recruiter_location_records WHERE friend_id = ?",
				(key,),
			).fetchone()
			return str(row[0]) if row else None

	def record_location_send(self, friend_id: int, *, job_id: str) -> None:
		"""把已确认发出的工作地点记为 sent。没有对应预约时抛错，避免假装记上了。

		只保留去重所需的会话与职位标识，不保存候选人姓名。
		"""
		with self._conn:
			cursor = self._conn.execute(
				"UPDATE recruiter_location_records SET status = 'sent', job_id = ?, name = '', updated_at = ? "
				"WHERE friend_id = ?",
				(job_id, time.time(), str(friend_id)),
			)
			if cursor.rowcount != 1:
				raise sqlite3.OperationalError("工作地点发送记录不存在，无法标记为已发送")

	def release_location_send(self, friend_id: int) -> bool:
		"""撤销确定未发出的预约。只删除 pending，已发送或结果未知的记录保持原样。"""
		with self._conn:
			cursor = self._conn.execute(
				"DELETE FROM recruiter_location_records WHERE friend_id = ? AND status = 'pending'",
				(str(friend_id),),
			)
			return bool(cursor.rowcount)

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
		if _cache_entry_expired(row[1], self._search_ttl):
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
		"""返回缓存的职位描述（命中且未过期），否则 None。

		``encryptJobId`` 漂移成 dict / 数字时按未命中处理：直接当 sqlite
		参数会抛 ProgrammingError 并穿出整条 ``--welfare`` 管线（Issue #1890）。
		"""
		if not isinstance(job_id, str) or not job_id:
			return None
		row = self._conn.execute(
			"SELECT description, created_at FROM job_desc_cache WHERE job_id = ?",
			(job_id,),
		).fetchone()
		if row is None:
			return None
		if _cache_entry_expired(row[1], self._search_ttl):
			self._conn.execute("DELETE FROM job_desc_cache WHERE job_id = ?", (job_id,))
			self._conn.commit()
			return None
		return cast("str", row[0])

	def put_job_desc(self, job_id: str, description: str) -> None:
		"""缓存非空职位描述，超过容量时淘汰保存时间最早的条目。

		非字符串键 / 描述同样按「没有可缓存内容」跳过（Issue #1890）。
		"""
		if not isinstance(job_id, str) or not job_id or not isinstance(description, str) or not description:
			return
		self._conn.execute(
			"INSERT OR REPLACE INTO job_desc_cache (job_id, description, created_at) VALUES (?, ?, ?)",
			(job_id, description, time.time()),
		)
		self._conn.commit()
		self._evict_old_job_desc_cache()

	def _evict_old_job_desc_cache(self) -> None:
		count = self._conn.execute("SELECT COUNT(*) FROM job_desc_cache").fetchone()[0]
		if count > _MAX_JOB_DESC_CACHE:
			excess = count - _MAX_JOB_DESC_CACHE
			self._conn.execute(
				"DELETE FROM job_desc_cache WHERE job_id IN "
				"(SELECT job_id FROM job_desc_cache ORDER BY created_at ASC, rowid ASC LIMIT ?)",
				(excess,),
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
		# name 是 TEXT 亲和性主键，但 SQLite 不强制类型：同内容的 BLOB 存储类
		# 与 TEXT 比较时永不相等，INSERT OR REPLACE 会留下一行 BLOB、再插一行
		# TEXT（Issue #2164，与 #2147 的读取兼容同族）。保存时先清掉同内容的
		# BLOB 漂移行，把记录归一成单一 TEXT 行。
		self._conn.execute("DELETE FROM saved_searches WHERE name = CAST(? AS BLOB)", (name,))
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
			# 同时匹配 BLOB 存储类：#2147 的读取兼容把 BLOB 名称解码后展示，
			# 但按键查找只用 `name = ?` 时永远找不到那一行，watch / preset
			# 会「看得到却运行不了」（Issue #2164）。
			"SELECT name, params, created_at, updated_at FROM saved_searches "
			"WHERE name = ? OR name = CAST(? AS BLOB)",
			(name, name),
		).fetchone()
		if row is None:
			return None
		decoded_name = _decode_text_column("saved_search", name, "name", row[0])
		assert isinstance(decoded_name, str)
		return {
			"name": decoded_name,
			"params": _decode_saved_params(decoded_name, row[1]),
			"created_at": _decode_timestamp_column("saved_search", decoded_name, "created_at", row[2]),
			"updated_at": _decode_timestamp_column("saved_search", decoded_name, "updated_at", row[3]),
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
				# TEXT 亲和性不强制类型：name 列被写成 BLOB 时原样进信封会让
				# 序列化兜成 INTERNAL_ERROR，且坏行不进 corrupt 名单
				# （Issue #2147，与 #1885 / #2056 同口径）。
				name = _decode_text_column("saved_search", str(row[0]), "name", row[0])
				assert isinstance(name, str)
				params = _decode_saved_params(name, row[1])
				created_at = _decode_timestamp_column("saved_search", name, "created_at", row[2])
				updated_at = _decode_timestamp_column("saved_search", name, "updated_at", row[3])
			except (SavedSearchParamsError, CacheRecordInvalid):
				corrupt.append(str(row[0]))
				continue
			items.append(
				{
					"name": name,
					"params": params,
					"created_at": created_at,
					"updated_at": updated_at,
				}
			)
		return items, corrupt

	def delete_saved_search(self, name: str) -> bool:
		cursor = self._conn.execute(
			# 与 get_saved_search 同口径：BLOB 存储类与 TEXT 永久不相等，只删
			# `name = ?` 会让 `watch remove` 报 removed: false 且记录留下
			# （Issue #2164）。
			"DELETE FROM saved_searches WHERE name = ? OR name = CAST(? AS BLOB)",
			(name, name),
		)
		self._conn.execute(
			"DELETE FROM watch_hits WHERE search_name = ? OR search_name = CAST(? AS BLOB)",
			(name, name),
		)
		self._conn.commit()
		return cursor.rowcount > 0

	@staticmethod
	def _watch_key_fragment(value: Any) -> str:
		"""键片段消毒：孤立代理项无法绑定进 SQLite TEXT，统一转义成 ASCII 反斜杠序列。"""
		text = value if isinstance(value, str) else str(value)
		return text.encode("utf-8", "backslashreplace").decode("utf-8")

	@classmethod
	def _make_watch_job_key(cls, item: dict[str, Any], *, account: str = "") -> str:
		security_id = cls._watch_key_fragment(item.get("security_id") or item.get("securityId") or "")
		job_id = cls._watch_key_fragment(item.get("job_id") or item.get("encryptJobId") or "")
		account = cls._watch_key_fragment(account)
		# 有账号指纹时按稳定的 encryptJobId 去重：securityId 每次搜索都会轮换，
		# 旧的复合键会让同一职位在每轮 watch 里反复算作新增。没有账号指纹时
		# 保留旧复合键，避免把无归属的旧记录当成当前账号的已见记录。
		# 账号指纹必须无条件入键：job_id 缺失时若丢掉账号，两个账号的已见记录
		# 会互相串扰，第二个账号首次看到同一职位不会出现在 new_items。
		if account and job_id:
			return f"{account}:{job_id}"
		if security_id or job_id:
			composite = f"{security_id}:{job_id}"
			return f"{account}:{composite}" if account else composite
		raw = json.dumps(item, sort_keys=True, ensure_ascii=True)
		digest = hashlib.sha256(raw.encode()).hexdigest()
		return f"{account}:{digest}" if account else digest

	def record_watch_results(self, search_name: str, items: list[dict[str, Any]], *, account: str = "") -> dict[str, Any]:
		# 只去重本轮完整列表项；有账号指纹时跨 securityId 轮换按稳定 job_id 合并。
		items = deduplicate_job_result(items)
		now = time.time()
		new_items = []
		seen_count = 0
		# payload 列只写不读，用 ensure_ascii=True 写入：平台 JSON 里的孤立代理项
		#（如 "\ud800"）json.loads 会接受，ensure_ascii=False 保留原字符后 sqlite3
		# 绑定抛 UnicodeEncodeError，整批 watch 结果丢弃。整轮包在事务里，中途
		# 异常回滚，不会把半截结果留给同一连接上的后续写去提交。
		with self._conn:
			for item in items:
				job_key = self._make_watch_job_key(item, account=account)
				payload = json.dumps(item, ensure_ascii=True, sort_keys=True)
				# 先 SELECT 再 INSERT 时，两个进程对同一个 watch 同时写入会都看到
				# 「不存在」，第二个 INSERT 撞主键抛 IntegrityError，被上层误判成
				# 缓存库损坏（Issue #1509）。INSERT OR IGNORE + UPDATE 的每条语句
				# 本身是原子的：抢到插入的一方记新增，另一方只刷新 payload / 时间。
				cursor = self._conn.execute(
					"INSERT OR IGNORE INTO watch_hits (search_name, job_key, payload, first_seen_at, last_seen_at) "
					"VALUES (?, ?, ?, ?, ?)",
					(search_name, job_key, payload, now, now),
				)
				if cursor.rowcount:
					new_items.append(item)
				else:
					seen_count += 1
					self._conn.execute(
						"UPDATE watch_hits SET payload = ?, last_seen_at = ? WHERE search_name = ? AND job_key = ?",
						(payload, now, search_name, job_key),
					)
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

	def claim_candidate_action(
		self,
		security_id: str,
		job_id: str,
		action: Literal["greet", "apply"],
		*,
		account: str = "",
	) -> str | None:
		"""原子预约求职者侧的 greet / apply；抢到返回 None，已有记录返回状态。

		空 ``job_id`` 不能预约：预约表以 ``(account, job_id, action)`` 为主键，
		空串会让所有职位共用一行，导致不同候选人被误报已处理（Issue #1645）。
		返回 ``"pending"`` 让调用方按结果未知处理，且不写任何全局键。

		``is_greeted`` / ``is_applied`` + 发送 + ``record_*`` 是跨进程的
		check-then-act：两个进程可以同时通过检查，各发一次真实请求
		（Issue #1508）。这里按 ``(account, job_id, action)`` 用
		``INSERT OR IGNORE`` 抢一个 pending 行，只有 rowcount=1 的那一方发送。

		旧的 greet_records / apply_records 仍然参与判断（历史去重不能丢），
		命中时返回 ``sent``；并发进程已经抢到 pending 时返回 ``pending``，
		调用方必须按结果未知处理，不能再发。
		"""
		if not job_id.strip():
			# 旧智能体依赖 claim 返回值判断；缺 ID 条目统一按「结果未知」拒绝，
			# 由调用方如实报告跳过，不能当成普通可重试失败（Issue #1645）。
			return "pending"
		legacy_seen = (
			self.is_greeted(security_id, job_id=job_id, account=account)
			if action == "greet"
			else self.is_applied(security_id, job_id, account=account)
		)
		if legacy_seen:
			return "sent"
		with self._conn:
			cursor = self._conn.execute(
				"INSERT OR IGNORE INTO candidate_action_claims "
				"(account, job_id, action, security_id, status, updated_at) "
				"VALUES (?, ?, ?, ?, 'pending', ?)",
				(account, job_id, action, security_id, time.time()),
			)
			if cursor.rowcount:
				return None
			row = self._conn.execute(
				"SELECT status FROM candidate_action_claims WHERE account = ? AND job_id = ? AND action = ?",
				(account, job_id, action),
			).fetchone()
		# 冲突行只可能来自已提交的另一个进程；读不到时按 pending 保守处理。
		return str(row[0]) if row is not None else "pending"

	def mark_candidate_action_sent(
		self,
		security_id: str,
		job_id: str,
		action: Literal["greet", "apply"],
		*,
		account: str = "",
	) -> None:
		"""平台确认成功后把预约 settle 成 sent（历史记录由调用方先写入）。"""
		if not job_id.strip():
			return
		with self._conn:
			self._conn.execute(
				"INSERT INTO candidate_action_claims "
				"(account, job_id, action, security_id, status, updated_at) "
				"VALUES (?, ?, ?, ?, 'sent', ?) "
				"ON CONFLICT(account, job_id, action) DO UPDATE SET "
				"security_id = excluded.security_id, status = 'sent', updated_at = excluded.updated_at",
				(account, job_id, action, security_id, time.time()),
			)

	def release_candidate_action(
		self, job_id: str, action: Literal["greet", "apply"], *, account: str = ""
	) -> bool:
		"""撤销确定未发出的预约；只删 pending，sent 与结果未知的记录保持原样。"""
		if not job_id.strip():
			return False
		with self._conn:
			cursor = self._conn.execute(
				"DELETE FROM candidate_action_claims "
				"WHERE account = ? AND job_id = ? AND action = ? AND status = 'pending'",
				(account, job_id, action),
			)
			return bool(cursor.rowcount)

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
			existing_by_job_id: dict[str, tuple[str, str]] = {}
			for row in self._conn.execute(
				"SELECT security_id, job_id, source FROM shortlist_records WHERE job_id != '' "
				"ORDER BY created_at ASC"
			).fetchall():
				# 去重键必须与读取展示路径同口径：BLOB 存储类经 str() 会变成
				# "b'J'"，与平台返回的 "J" 永不相等，同一职位被重复插入
				# （Issue #2165，与 #1885 / #2147 的 _decode_text_column 同源）。
				try:
					security_key = _decode_text_column("shortlist", str(row[0]), "security_id", row[0])
					job_key = _decode_text_column("shortlist", str(row[1]), "job_id", row[1])
					source_key = _decode_text_column("shortlist", str(row[2]), "source", row[2])
				except CacheRecordInvalid:
					# 无法解码的坏行不参与去重：它仍会出现在 list_shortlist 的
					# corrupt 名单里，不能因此让整批导入失败。
					continue
				if not job_key:
					continue
				existing_by_job_id[job_key] = (security_key or "", source_key or "")
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
						cursor = self._conn.execute(
							# 原兼容刷新保留；动态上下文必须来自同一条输入，缺 lid 时不能拼上旧 lid。
							# 同 job_id 可能已有两行（不同 security_id）：目标键若已存在，
							# 直接 UPDATE 会撞 (security_id, job_id) 主键并整批回滚。
							# OR IGNORE 冲突时返回 rowcount=0，再删掉被刷新的旧行、
							# 保留目标行（Issue #1466）。
							"UPDATE OR IGNORE shortlist_records SET security_id = ?, lid = ? "
							"WHERE security_id = ? AND job_id = ?",
							(security_id, lid, old_security_id, job_id),
						)
						if cursor.rowcount == 0:
							self._conn.execute(
								"DELETE FROM shortlist_records WHERE security_id = ? AND job_id = ?",
								(old_security_id, job_id),
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
		record_id = str(row[1] or row[0] or "")
		security_id = _decode_text_column("shortlist", record_id, "security_id", row[0])
		job_id = _decode_text_column("shortlist", record_id, "job_id", row[1])
		source = _decode_text_column("shortlist", record_id, "source", row[6])
		note = _decode_text_column("shortlist", record_id, "note", row[8], allow_none=True)
		lid = _decode_text_column("shortlist", record_id, "lid", row[10], allow_none=True)
		return {
			"security_id": security_id,
			"job_id": job_id,
			"title": _decode_text_column("shortlist", record_id, "title", row[2]),
			"company": _decode_text_column("shortlist", record_id, "company", row[3]),
			"city": _decode_text_column("shortlist", record_id, "city", row[4]),
			"salary": _decode_text_column("shortlist", record_id, "salary", row[5]),
			"source": source,
			"tags": self._deserialize_shortlist_tags(row[7]),
			"note": note or "",
			# REAL 列漂移成 BLOB 时不能原样进信封：序列化会兜成固定
			# INTERNAL_ERROR，与 #1964 的时间戳列同口径（Issue #2147）。
			"created_at": _decode_timestamp_column("shortlist", record_id, "created_at", row[9]),
			"lid": lid or self._crawl_shortlist_lid(security_id or "", job_id or "", source or ""),
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

		`security_id` 每次搜索请求都会轮换，不能当作跨请求的职位身份：同一简历对同一
		`job_id` 已有记录时，把该行迁到本次的新标识，而不是再插入一行 `prepared` 记录
		（Issue #901）。
		"""
		now = time.time()
		with self._conn:
			exact = self._conn.execute(
				"SELECT 1 FROM resume_job_links WHERE resume_name = ? AND security_id = ? AND job_id = ?",
				(resume_name, security_id, job_id),
			).fetchone()
			if exact is None:
				existing = self._conn.execute(
					"SELECT security_id FROM resume_job_links "
					"WHERE resume_name = ? AND job_id = ? ORDER BY updated_at DESC LIMIT 1",
					(resume_name, job_id),
				).fetchone()
				if existing is not None:
					self._conn.execute(
						"UPDATE resume_job_links SET security_id = ?, updated_at = ? "
						"WHERE resume_name = ? AND security_id = ? AND job_id = ?",
						(security_id, now, resume_name, str(existing[0]), job_id),
					)
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

	def _resume_link_row_to_dict(self, row: Any) -> dict[str, Any]:
		record_id = f"{row[0]}/{row[1]}/{row[2]}"
		return {
			"resume_name": _decode_text_column("resume_link", record_id, "resume_name", row[0]),
			"security_id": _decode_text_column("resume_link", record_id, "security_id", row[1]),
			"job_id": _decode_text_column("resume_link", record_id, "job_id", row[2]),
			"job_title": _decode_text_column("resume_link", record_id, "job_title", row[3]),
			"company": _decode_text_column("resume_link", record_id, "company", row[4]),
			"status": _decode_text_column("resume_link", record_id, "status", row[5]),
			"notes": _decode_text_column("resume_link", record_id, "notes", row[6], allow_none=True),
			# 与 #1964 同口径：REAL 时间戳漂移不能以 bytes 进信封（Issue #2147）。
			"linked_at": _decode_timestamp_column("resume_link", record_id, "linked_at", row[7]),
			"updated_at": _decode_timestamp_column("resume_link", record_id, "updated_at", row[8]),
		}

	def get_resume_applications(self, resume_name: str) -> list[dict[str, Any]]:
		"""查看某份简历投递的所有职位"""
		rows = self._conn.execute(
			"SELECT resume_name, security_id, job_id, job_title, company, status, notes, linked_at, updated_at "
			"FROM resume_job_links WHERE resume_name = ? ORDER BY updated_at DESC",
			(resume_name,),
		).fetchall()
		return [self._resume_link_row_to_dict(row) for row in rows]

	def get_job_resumes(self, security_id: str, job_id: str) -> list[dict[str, Any]]:
		"""查看某职位关联的所有简历版本"""
		rows = self._conn.execute(
			"SELECT resume_name, security_id, job_id, job_title, company, status, notes, linked_at, updated_at "
			"FROM resume_job_links WHERE security_id = ? AND job_id = ? ORDER BY updated_at DESC",
			(security_id, job_id),
		).fetchall()
		return [self._resume_link_row_to_dict(row) for row in rows]

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
