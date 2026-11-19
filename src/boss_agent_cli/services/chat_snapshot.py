"""Chat snapshot — JSON 快照保存与 diff 对比。"""

import datetime
import errno
import json
import os
import re
import time
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
	"""快照里的 unread 只认真正的整数，其余按 0 参与比较（Issue #1194）。

	当日快照由本工具写成整数；上一份快照被手工改过或来自旧格式时可能是
	字符串——``str`` 与 ``int`` 比较会抛 ``TypeError``，把整次导出打断。
	``bool`` 是 ``int`` 子类，但 ``True`` 不是「未读数」，与 ``int_code``
	同口径排除；``None`` 一直按 0 处理（既有行为）。
	"""
	value = item.get("unread")
	return value if type(value) is int else 0


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
	return not (isinstance(data, dict) and data.get("complete") is False)


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
	"""
	os.makedirs(snapshot_dir, exist_ok=True)
	today = datetime.date.today().isoformat()
	snapshot_path = os.path.join(snapshot_dir, f"{today}.json")

	# 读-合并-写必须在同一个临界区内：两个并发导出各自读到旧快照再各自覆盖，
	# 后写者会丢掉另一页的联系人，次日 diff 误报 added（Issue #1356）。
	merged = _merge_and_write_locked(snapshot_dir, snapshot_path, friends, replace=complete)

	# 查找上一次快照
	prev_path = _find_previous_snapshot(snapshot_dir, today)
	if prev_path is None:
		return {
			"is_first": True,
			"complete": complete,
			"baseline_complete": True,
			"added": [],
			"removed": [],
			"new_unread": [],
		}

	prev_friends = load_snapshot(prev_path, logger)
	if prev_friends is None:
		return {
			"is_first": True,
			"complete": complete,
			"baseline_complete": True,
			"added": [],
			"removed": [],
			"new_unread": [],
		}
	# 上一份快照不完整时它只记录了「看到了谁」：把没翻到的联系人当新增会误报
	# （Issue #1852）。不宣称新增，并在结果里标注基线不完整。
	baseline_complete = _read_complete_marker(prev_path)

	# 用稳定标识（uid 优先）做 key 对比
	curr_map = {contact_identity(item): item for item in merged if contact_identity(item)}
	prev_map = {contact_identity(item): item for item in prev_friends if contact_identity(item)}
	curr_ids = set(curr_map)
	prev_ids = set(prev_map)

	added = [curr_map[key] for key in (curr_ids - prev_ids)] if baseline_complete else []
	# 不完整的采集只能证明“看到了谁”，不能证明“谁消失了”。
	removed = [prev_map[key] for key in (prev_ids - curr_ids)] if complete else []

	# 新增未读：检测任何未读增量
	new_unread = []
	for key in (curr_ids & prev_ids):
		prev_unread = _unread_count(prev_map[key])
		curr_unread = _unread_count(curr_map[key])
		if curr_unread > prev_unread:
			new_unread.append(curr_map[key])

	return {
		"is_first": False,
		"complete": complete,
		"baseline_complete": baseline_complete,
		"prev_date": os.path.basename(prev_path).replace(".json", ""),
		"added": added,
		"removed": removed,
		"new_unread": new_unread,
	}


def _merge_and_write_locked(
	snapshot_dir: str,
	snapshot_path: str,
	friends: list[dict[str, Any]],
	*,
	replace: bool = False,
) -> list[dict[str, Any]]:
	"""在跨进程独占锁内完成读-合并-写，返回合并后的快照内容。

	锁拿不到时有限重试；重试耗尽按本地 IO 错误上报（``OSError`` 带 ``filename``），
	绝不在未持锁时读取基准或写入，避免并发导出互相覆盖（Issue #1356）。
	"""
	lock_path = run_lock_path(Path(snapshot_dir), f"chat-snapshot-{os.path.basename(snapshot_path)}")
	for attempt in range(_LOCK_ATTEMPTS):
		with exclusive_file_lock(lock_path) as acquired:
			if acquired:
				return _merge_and_write(snapshot_path, friends, replace=replace)
		if attempt + 1 < _LOCK_ATTEMPTS:
			time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)
	raise OSError(errno.EAGAIN, "当日聊天快照正被其他进程写入，等待互斥锁超时", snapshot_path)


def _merge_and_write(
	snapshot_path: str,
	friends: list[dict[str, Any]],
	*,
	replace: bool = False,
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
	if raw is not None and not replace:
		try:
			prev_today = json.loads(raw.decode("utf-8"))
			if isinstance(prev_today, list):
				for item in prev_today:
					if isinstance(item, dict) and contact_identity(item):
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

	# 保存合并后的快照；原子写避免中断后当日快照变成半截 JSON（下次读取会整个丢弃）。
	write_json_atomic(Path(snapshot_path), merged)
	# 快照本身保持 list 兼容旧读取方；完整性写在旁车文件里。完整采集覆盖当日
	# 快照后可宣称完整；不完整采集只有在当日已有完整快照时才保持完整标记
	# （此时当日快照仍覆盖完整列表的全部观察结果，Issue #1852）。
	if replace:
		stored_complete = True
	else:
		stored_complete = raw is not None and _read_complete_marker(snapshot_path)
	write_json_atomic(Path(_meta_path(snapshot_path)), {"complete": stored_complete})
	return merged


def load_snapshot(path: str, logger: Logger) -> list[dict[str, Any]] | None:
	"""加载并校验快照文件，返回 None 表示不可用。"""
	# 快照目录里的 FIFO / 设备直接 open() 会永久阻塞（Issue #1730）：与当天
	# 快照（#1698）同口径先按 lstat 判定类型。上一份快照只影响 diff 精度、
	# 不会被写回，所以这里沿用「警告并当作没有历史」的既有容错。
	try:
		raw = read_bytes_if_regular(Path(path))
	except OSError:
		logger.warning(f"无法读取快照: {path}")
		return None
	if raw is None:
		logger.warning(f"无法读取快照: {path}")
		return None
	try:
		data = json.loads(raw.decode("utf-8"))
	except (ValueError, RecursionError):
		logger.warning(f"无法读取快照: {path}")
		return None

	if not isinstance(data, list):
		logger.warning(f"快照格式异常（非数组）: {path}")
		return None

	# 过滤掉非 dict 项
	return [item for item in data if isinstance(item, dict)]


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
