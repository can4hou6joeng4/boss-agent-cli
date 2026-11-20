"""Chat snapshot — JSON 快照保存与 diff 对比。"""

import datetime
import errno
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from boss_agent_cli.services.chat_utils import contact_identity
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path
from boss_agent_cli.services.json_files import read_bytes_if_regular, write_json_atomic
from boss_agent_cli.output import Logger

_SNAPSHOT_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: 完整性旁车文件后缀：快照本身保持 list 形态兼容旧读取方；
#: 不占用 `.json` 后缀，`chat-history/*.json` 仍然只匹配快照本体。
_META_SUFFIX = ".meta"

#: 当日快照锁被占用时的有限重试；锁只包住一次读-合并-写，正常持有时长是毫秒级。
_LOCK_ATTEMPTS = 100
_LOCK_RETRY_INTERVAL_SECONDS = 0.05


def _unread_count(item: dict[str, Any]) -> int:
	"""快照里的 unread 只认真正的非负整数，其余按 0 参与比较（Issue #1194 / #2180）。

	当日快照由本工具写成整数；上一份快照被手工改过或来自旧格式时可能是
	字符串——``str`` 与 ``int`` 比较会抛 ``TypeError``，把整次导出打断。
	``bool`` 是 ``int`` 子类，但 ``True`` 不是「未读数」，与 ``int_code``
	同口径排除；``None`` 一直按 0 处理（既有行为）。负数没有平台语义：
	历史快照里的 ``-1`` 恢复成 ``0`` 会被算成「新增未读」（Issue #2180），
	与字符串 / 布尔等漂移值同口径按 0。
	"""
	value = item.get("unread")
	return value if type(value) is int and value >= 0 else 0


def _meta_path(snapshot_path: str) -> str:
	return snapshot_path[: -len(".json")] + _META_SUFFIX if snapshot_path.endswith(".json") else snapshot_path + _META_SUFFIX


def _read_complete_marker(snapshot_path: str) -> bool:
	"""读取快照的完整性标记；旧快照没有旁车文件时按完整处理（保持原语义）。

	标记存在且明确为 ``false`` 时返回 False：不完整采集只记录「看到了谁」，
	不能作为判断新增的基线（Issue #1852）。标记损坏或读不出来时同样按
	不完整处理——无法证明基线完整就不能宣称新增。
	"""
	try:
		raw = read_bytes_if_regular(Path(_meta_path(snapshot_path)))
	except FileNotFoundError:
		return True
	except OSError:
		return False
	if raw is None:
		return True
	try:
		data = json.loads(raw.decode("utf-8"))
	except (ValueError, RecursionError):
		return False
	# 只认显式 true：0 / "false" / null / 缺键 / 整个 meta 不是对象，都不能
	# 证明基线完整；旧实现只拦字面 False，非布尔漂移会把上一份不完整快照
	# 当成完整基线并误报新增（Issue #1911）。
	return isinstance(data, dict) and data.get("complete") is True


def save_snapshot_and_diff(
	snapshot_dir: str,
	friends: list[dict[str, Any]],
	logger: Logger,
	*,
	complete: bool = True,
) -> dict[str, Any]:
	"""保存当日 JSON 快照（uid 优先，旧数据回退 security_id）并与上次对比。

	``complete`` 表示本次观察是否覆盖完整沟通列表（第 1 页且平台明确
	``hasMore=false``）。不完整时只记录观察结果，不推断联系人已消失：
	筛选结果或部分页面缺少“该联系人不在列表中”的证据（Issue #911）。
	缺少可用身份的条目会使本次观察降级为不完整，写入时与当天已有条目合并
	（Issue #2179）。
	"""
	os.makedirs(snapshot_dir, exist_ok=True)
	today = datetime.date.today().isoformat()
	snapshot_path = os.path.join(snapshot_dir, f"{today}.json")

	# 服务边界过滤非对象条目：平台列表页混入 null / 字符串 / 数字时，
	# contact_identity 会在 .get 上抛 AttributeError，整次快照与导出失败
	# （与 chat_export 的渲染边界同口径，Issue #2221）。丢弃过条目即不完整。
	usable_friends = [item for item in friends if isinstance(item, dict)]
	dropped_entries = len(usable_friends) != len(friends)

	# 有条目因缺少身份被丢弃时不能声称完整（Issue #2179）：既不推断「已消失」，
	# 也不把 complete=true 写进当日快照——否则次日会把这份不完整快照当基线。
	current_complete = (
		complete and not dropped_entries and all(contact_identity(item) for item in usable_friends)
	)

	# 读、合并和写入共用一把锁，避免并发导出覆盖另一页联系人（#1356）。
	with _snapshot_lock(snapshot_path):
		merged = _merge_and_write(snapshot_path, usable_friends, replace=current_complete)

	prev_path = _find_previous_snapshot(snapshot_dir, today)
	previous = _load_previous_snapshot(prev_path, logger) if prev_path is not None else None
	if previous is None or prev_path is None:
		return {
			"is_first": True,
			"complete": current_complete,
			"baseline_complete": True,
			"added": [],
			"removed": [],
			"new_unread": [],
		}
	prev_friends, baseline_complete = previous

	# 用稳定标识（uid 优先）做 key 对比
	curr_map = {contact_identity(item): item for item in merged if contact_identity(item)}
	prev_map = {contact_identity(item): item for item in prev_friends if contact_identity(item)}
	curr_ids = set(curr_map)
	prev_ids = set(prev_map)

	added = [curr_map[key] for key in (curr_ids - prev_ids)] if baseline_complete else []
	# 不完整的采集只能证明“看到了谁”，不能证明“谁消失了”。
	removed = [prev_map[key] for key in (prev_ids - curr_ids)] if current_complete else []

	# 新增未读：检测任何未读增量
	new_unread = []
	for key in (curr_ids & prev_ids):
		prev_unread = _unread_count(prev_map[key])
		curr_unread = _unread_count(curr_map[key])
		if curr_unread > prev_unread:
			new_unread.append(curr_map[key])

	return {
		"is_first": False,
		"complete": current_complete,
		"baseline_complete": baseline_complete,
		"prev_date": os.path.basename(prev_path).replace(".json", ""),
		"added": added,
		"removed": removed,
		"new_unread": new_unread,
	}


@contextmanager
def _snapshot_lock(snapshot_path: str) -> Iterator[None]:
	"""为同一天的快照及其完整性标记获取跨进程锁，等待时间有限。"""
	path = Path(snapshot_path)
	lock_path = run_lock_path(path.parent, f"chat-snapshot-{path.name}")
	for attempt in range(_LOCK_ATTEMPTS):
		with exclusive_file_lock(lock_path) as acquired:
			if acquired:
				yield
				return
		if attempt + 1 < _LOCK_ATTEMPTS:
			time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)
	raise OSError(errno.EAGAIN, "聊天快照正被其他进程占用，等待互斥锁超时", snapshot_path)


def _load_previous_snapshot(path: str, logger: Logger) -> tuple[list[dict[str, Any]], bool] | None:
	# 同一把日锁覆盖两次读取，避免与跨日写入交错，把不同版本的快照和标记拼在一起（#2181）。
	try:
		with _snapshot_lock(path):
			friends, dropped = _load_snapshot_detailed(path, logger)
			if friends is None:
				return None
			return friends, not dropped and _read_complete_marker(path)
	except OSError:
		logger.warning(f"无法锁定上一份快照: {path}")
		return None


def _merge_and_write(
	snapshot_path: str,
	friends: list[dict[str, Any]],
	*,
	replace: bool,
) -> list[dict[str, Any]]:
	"""调用方必须持有当日快照锁。

	``replace=True`` 表示本次是完整采集：它就是当天的完整列表，直接替换当日
	快照。继续与旧内容合并会把当天早些时候见过、现在已消失的联系人留在快照里，
	同日重跑报不出「已消失」，推迟到次日才报（Issue #1791）。
	"""
	# 合并已有的当日快照（解决分页覆盖问题）
	existing: dict[str, dict[str, Any]] = {}
	# 只有「当天还没有快照」才从空历史开始。路径元数据读不出来、目录 / FIFO、
	# 已有文件不可读时上抛 OSError：把它们当成没有历史会让随后的原子写覆盖
	# 已合并的分页结果，次日 diff 误报联系人消失（Issue #1698）。
	raw = read_bytes_if_regular(Path(snapshot_path))
	today_complete = False
	if raw is not None and not replace:
		try:
			prev_today = json.loads(raw.decode("utf-8"))
			if isinstance(prev_today, list):
				usable = [
					item
					for item in prev_today
					if isinstance(item, dict) and contact_identity(item)
				]
				# 合并时保留可用条目。只要丢弃过条目，就不能沿用旧的完整性标记。
				today_complete = len(usable) == len(prev_today)
				for item in usable:
					existing[contact_identity(item)] = item
		except (ValueError, RecursionError):
			# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError 与 3.11+ 的超长
			# 整数上限；RecursionError 覆盖深嵌套。坏快照按没有历史处理。
			pass

	# 用当前数据更新（新数据优先）
	for item in friends:
		key = contact_identity(item)
		if key:
			existing[key] = item

	merged = list(existing.values())

	# 快照本身保持 list 兼容旧读取方；完整性写在旁车文件里。完整采集覆盖当日
	# 快照后可宣称完整；不完整采集只有在当日已有完整快照时才保持完整标记
	# （此时仍覆盖先前完整列表的观察结果，#1852）。整份快照必须解析成功，
	# 才能继续信任既有的完整性标记（#1912、#2179）。
	stored_complete = replace or (today_complete and _read_complete_marker(snapshot_path))
	# 快照与旁车文件是两次独立原子写：先落保守标记再写快照，最后写真实标记。
	# 进程在两次写之间被杀时旁车文件保持 false，部分页快照不会被当成完整基线
	# （Issue #1912 的崩溃窗口）。
	meta_path = Path(_meta_path(snapshot_path))
	write_json_atomic(meta_path, {"complete": False})
	# 原子写避免中断后当日快照变成半截 JSON（下次读取会整个丢弃）。
	write_json_atomic(Path(snapshot_path), merged)
	write_json_atomic(meta_path, {"complete": stored_complete})
	return merged


def load_snapshot(path: str, logger: Logger) -> list[dict[str, Any]] | None:
	"""加载并校验快照文件，返回 None 表示不可用。"""
	return _load_snapshot_detailed(path, logger)[0]


def _load_snapshot_detailed(
	path: str, logger: Logger
) -> tuple[list[dict[str, Any]] | None, bool]:
	"""加载快照并报告是否丢弃过无法识别的条目（Issue #2179）。

	返回 ``(条目, 是否丢弃)``。第一项为 ``None`` 表示整份快照不可用；
	混合列表里缺身份（``contact_identity`` 为空）的条目被丢弃时第二项为
	True——丢弃过的旧快照可能包含后来补齐身份的联系人，不能证明「新增」。
	"""
	# 快照目录里的 FIFO / 设备直接 open() 会永久阻塞（Issue #1730）：与当天
	# 快照（#1698）同口径先按 lstat 判定类型。上一份快照只影响 diff 精度、
	# 不会被写回，所以这里沿用「警告并当作没有历史」的既有容错。
	try:
		raw = read_bytes_if_regular(Path(path))
	except OSError:
		logger.warning(f"无法读取快照: {path}")
		return None, False
	if raw is None:
		logger.warning(f"无法读取快照: {path}")
		return None, False
	try:
		data = json.loads(raw.decode("utf-8"))
	except (ValueError, RecursionError):
		logger.warning(f"无法读取快照: {path}")
		return None, False

	if not isinstance(data, list):
		logger.warning(f"快照格式异常（非数组）: {path}")
		return None, False

	items = [item for item in data if isinstance(item, dict) and contact_identity(item)]
	# 非空数组但零个可用对象（手工改坏 / 旧格式漂移）与坏 JSON 同口径：
	# 当作没有历史，而不是「完整但为空的基线」，否则次日全员误报新增（Issue #1993）。
	# 「可用」与 _merge_and_write 同口径：必须带 identity，无身份字段的 dict
	# 让 prev_map 为空，下次完整采集仍会全员误报新增（Issue #2111）。
	dropped = len(items) != len(data)
	if data and not items:
		logger.warning(f"快照没有可用条目: {path}")
		return None, dropped
	return items, dropped


def _find_previous_snapshot(snapshot_dir: str, today: str) -> str | None:
	"""找到 today 之前最近的一份快照文件路径。

	只接受 `YYYY-MM-DD.json` 命名的文件。同目录里手动备份出来的 `backup.json`
	之类一旦混进来，旧实现会按字符串排序选中它（"b…" 排在 "2026-…" 之后），
	读到非快照内容后整次 diff 静默退化成「首份快照」。未来日期的文件
	（拷入的、系统时钟回拨留下的）同样跳过，否则会被当成「上一次」。
	"""
	snapshots = []
	for fname in os.listdir(snapshot_dir):
		if not fname.endswith(".json"):
			continue
		stem = fname[: -len(".json")]
		if not _SNAPSHOT_NAME_RE.fullmatch(stem) or stem >= today:
			continue
		try:
			datetime.date.fromisoformat(stem)
		except ValueError:
			continue
		snapshots.append(fname)
	if not snapshots:
		return None
	snapshots.sort(reverse=True)
	return os.path.join(snapshot_dir, snapshots[0])
