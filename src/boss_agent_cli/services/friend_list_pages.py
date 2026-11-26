from typing import Any

from boss_agent_cli.api.models import dict_items
from boss_agent_cli.services.chat_utils import stable_uid


MAX_FRIEND_LIST_PAGES = 50


def friend_list_page_items(platform_data: dict[str, Any]) -> list[dict[str, Any]]:
	"""取沟通列表页里的联系人条目。

	``result`` 只有能取出至少一个对象元素时才优先使用；平台把 ``result`` 漂移成
	对象 / 数字 / 非空字符串时，``result or friendList`` 会选中这个真值容器，
	``dict_items`` 返回空列表而合法的 ``friendList`` 永远不会被读取（Issue #1351）。
	``result`` 是 ``[None]`` / ``[5]`` 这类只含坏元素的非空列表时同理（Issue #1402）：
	元素级漂移由 ``dict_items`` 跳过，跳过之后为空就回退 ``friendList``。
	"""
	return friend_list_page_observation(platform_data)[0]


def friend_list_page_observation(platform_data: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
	"""返回联系人及所用列表是否完整解析，保留原有的容器回退顺序。"""
	for key in ("result", "friendList"):
		raw = platform_data.get(key)
		if isinstance(raw, list):
			items = dict_items(raw)
			if items:
				return items, len(items) == len(raw)

	# 至少有一个列表容器且整页无内容，才可认作平台明确返回的空页。
	has_list = any(isinstance(platform_data.get(key), list) for key in ("result", "friendList"))
	return [], has_list and not friend_list_page_has_content(platform_data)


def friend_list_page_has_content(platform_data: dict[str, Any]) -> bool:
	"""列表容器是否非空（哪怕元素全被跳过）。

	``friend_list_page_items`` 返回空列表有两种含义：平台本来就给了空页，或
	容器非空但元素全是 null / 数字 / 字符串。后者不是「列表结束」，翻页方
	据此继续请求，不能宣称结果完整或联系人不存在（Issue #1878）。

	只认 list 容器：``result`` / ``friendList`` 漂移成字符串 / 对象时
	``dict_items`` 取不出条目，它们不是「列表还有内容」的证据。把这种兄弟键
	当成有内容会让单页响应被连续翻满上限、放大平台请求（Issue #2415）。
	"""
	for key in ("result", "friendList"):
		raw = platform_data.get(key)
		if isinstance(raw, list) and raw:
			return True
	return False


def _row_identity(item: dict[str, Any]) -> tuple[str, str] | None:
	"""多页聚合的去重键：uid + 职位 ID；uid 不可用时返回 ``None``，不参与去重。

	uid 是联系人主键（见 ``chat_utils.contact_identity``），同一条目被重复返回时
	职位 ID 也必然相同。键里带上职位 ID 是因为去重会丢行：「同一联系人、不同职位
	各占一行」无法离线证伪，宁可少并也不误并。securityId 每次请求轮换，不能
	参与判定；两份都没有可用职位 ID 时退回只按 uid 判定。
	"""
	uid = stable_uid(item.get("uid"))
	if not uid:
		return None
	job_id = item.get("encryptJobId")
	return uid, job_id.strip() if isinstance(job_id, str) else ""


def collect_friend_list_items(
	platform: Any,
	*,
	start_page: int = 1,
	max_pages: int = MAX_FRIEND_LIST_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
	"""安全聚合多页沟通列表，保留原始顺序。

	返回 (items, error_response)：
	- 成功聚合：([items...], None)
	- 任一页平台失败：([], raw_response)

	调用方需要判断结果是否完整时，用 :func:`collect_friend_list_page`。
	"""
	items, error, _pages_scanned, _truncated = collect_friend_list_page(
		platform, start_page=start_page, max_pages=max_pages
	)
	return items, error


def collect_friend_list_page(
	platform: Any,
	*,
	start_page: int = 1,
	max_pages: int = MAX_FRIEND_LIST_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, int, bool]:
	"""聚合多页沟通列表，并显式报告是否因页数上限而截断。

	返回 (items, error_response, pages_scanned, truncated)：

	- 翻到平台 ``hasMore=False``、重复页或可识别的空列表容器：``truncated=False``，
	  结果完整；成功响应缺少列表容器时无法证明列表结束，继续翻页（Issue #2927）；
	  重复页仍明确声明有后续时保留早停，但标记 ``truncated=True``（Issue #2956）；
	- 翻满 ``max_pages`` 且平台仍声称有下一页：``truncated=True``，items 只是前
	  ``max_pages`` 页。调用方必须把这一点告诉 Agent / 用户，不能当成完整统计
	  （Issue #885）。

	同一条目（同一 uid 且同一职位）重复出现时只保留首条（Issue #2753）。
	"""
	items: list[dict[str, Any]] = []
	page = start_page
	seen_signatures: set[tuple[tuple[str, str], ...]] = set()
	seen_rows: set[tuple[str, str]] = set()
	pages_scanned = 0
	truncated = False

	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		pages_scanned += 1
		if not platform.is_success(resp):
			return [], resp, pages_scanned, False

		platform_data = platform.unwrap_data(resp)
		if not isinstance(platform_data, dict):
			platform_data = {}
		# 列表元素混入 null / 数字 / 字符串时只跳过坏元素（Issue #1141 同一口径），
		# 否则下游 build_pipeline_items / _friend_record 读键会整条命令崩溃。
		# 同时保留完整性信号：没有可识别列表容器的成功页不能证明列表结束
		# （Issue #2927），只有平台明确给出空列表或 hasMore=False 才能停。
		page_items, page_complete = friend_list_page_observation(platform_data)
		if not page_items:
			if platform_data.get("hasMore") is not False and not page_complete:
				# 整页元素被跳过，或根本没有列表容器：不是「平台确认的空页」，
				# 继续翻页（Issue #1878、#2927）。翻满上限时同样要标记截断，
				# 否则调用方会把前缀当完整结果（Issue #2006）。
				if pages_scanned >= max_pages:
					truncated = True
				page += 1
				continue
			break

		# 整页签名必须与行去重同口径（Issue #2872）：同一联系人对应不同职位时
		# 是两条合法记录，不能因为 uid 相同就把下一页当成重复页丢掉。
		# securityId 在每次请求间都会轮换，只能作为 uid 缺失时的回退标识；
		# 签名全为空时无法判断是否重复，继续聚合，避免把缺字段的新页面当成
		# 重复页丢掉。uid 必须是非空字符串：布尔 / 容器漂移会让各页签名相同，
		# 整页联系人被当成重复页丢弃且 reported complete（Issue #2063）。
		row_keys = [_row_identity(item) for item in page_items]
		identities = [
			key if key is not None
			else (stable_uid(item.get("uid")) or stable_uid(item.get("securityId")) or "", "")
			for item, key in zip(page_items, row_keys)
		]
		if any(uid for uid, _ in identities):
			signature = tuple(identities)
			if signature in seen_signatures:
				# 重复页仍明确声明有后续时无法证明列表完整：保留早停避免重复请求，
				# 但结果只是前缀，必须与联系人查找同口径标记不完整（Issue #2956）。
				if platform_data.get("hasMore") is True:
					truncated = True
				break
			seen_signatures.add(signature)
		# 整页签名只挡「整页重复」。沟通列表按最近消息排序、按页码取数：翻页期间
		# 有新消息把某个会话顶到最前，上一页末尾的条目就会在下一页开头再出现
		# 一次，两页只重叠一部分。pipeline 行不带 uid，securityId 又每次请求轮换，
		# 下游无法自行去重，所以在这里保留首条（与职位侧的
		# job_identity.deduplicate_job_result 同口径，Issue #2753）。
		for item, row_key in zip(page_items, row_keys):
			if row_key is not None:
				if row_key in seen_rows:
					continue
				seen_rows.add(row_key)
			items.append(item)

		if platform_data.get("hasMore") is False:
			break
		if pages_scanned >= max_pages:
			# 上限耗尽而平台仍未说「没有下一页」：结果只是前缀，必须显式标记。
			truncated = True
		page += 1

	return items, None, pages_scanned, truncated
