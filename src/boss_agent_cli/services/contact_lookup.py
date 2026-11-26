from typing import Any, Mapping

from boss_agent_cli.services.chat_utils import stable_uid
from boss_agent_cli.services.friend_list_pages import friend_list_page_has_content, friend_list_page_items


def friend_security_id(item: Mapping[str, Any]) -> str:
	"""读取本次列表返回的 securityId；类型漂移按缺失处理。

	只认字符串：布尔 / 容器经 ``str()`` 后会变成 ``"True"`` / ``"{'a': 1}"``，
	发给平台写请求就是伪造身份。CLI 侧此前已按此口径收口（Issue #1812），
	wizard 侧仍会把 repr 文本发给 ``exchange_contact`` 等写接口（Issue #2543）。

	两个键按 ``securityId`` → ``security_id`` 顺序取第一个非空字符串：camelCase
	是「真值但不可用」（容器 / 数字 / 纯空白）时，旧实现 ``or`` 已经选中它，
	合法的 snake_case 回退被吞掉，exchange / mark 会报不可重试的
	``NETWORK_ERROR``（Issue #2723）。
	"""
	for key in ("securityId", "security_id"):
		raw = item.get(key)
		if isinstance(raw, str) and raw.strip():
			return raw.strip()
	return ""


def friend_uid(item: Mapping[str, Any]) -> str:
	"""读取本次列表返回的稳定 uid；布尔 / 容器 / 超长整数按缺失处理。

	与 ``chat_utils.stable_uid`` 同口径：超过 4300 位的整数连 ``str()`` 都会
	抛 ``ValueError``（Issue #2543）。
	"""
	return stable_uid(item.get("uid"))


def friend_display_name(item: Mapping[str, Any]) -> str:
	"""读取联系人显示名：只认非空白字符串（去首尾空白），否则回落 ``-``。

	``name`` 会进 ``exchange_contact`` 写请求与输出文案。CLI ``exchange`` 已按
	此口径收口（Issue #1812），但向导 ``exchange`` 仍用 ``str(name or "-")``
	把对象 / 数组 / 布尔 / NaN 的 Python repr 发给平台，``mark`` /
	``chat-summary`` / ``chatmsg`` 用 ``name or "-"`` 把容器原样写进信封
	（Issue #2748）。判定只留这一处真源。
	"""
	raw = item.get("name")
	if isinstance(raw, str):
		name = raw.strip()
		if name:
			return name
	return "-"


class FriendLookupLimitExceeded(RuntimeError):
	"""Raised when paginated friend lookup cannot prove completion safely."""


class FriendListShapeError(RuntimeError):
	"""响应缺少可识别的联系人列表容器，无法判断联系人是否存在（Issue #1978）。"""


def _lookup_identity(item: Mapping[str, Any]) -> tuple[str, str]:
	"""翻页去重键：稳定 uid + 职位 ID；没有 uid 时退回可用的 securityId。

	uid 是联系人主键，同一条目重复出现时职位 ID 也相同；带上职位 ID 是为了
	不把「同一联系人、不同职位各占一行」误判成重复页。securityId 每次请求
	轮换，只在完全没有 uid 时作为兜底，与 friend_security_id 的可用字符串
	口径一致（Issue #2883）。
	"""
	uid = friend_uid(item)
	if uid:
		job_id = item.get("encryptJobId")
		return uid, job_id.strip() if isinstance(job_id, str) else ""
	return "", friend_security_id(item)


def find_friend(
	platform: Any,
	identifier: str | int,
	*,
	start_page: int = 1,
	max_pages: int = 50,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
	"""分页遍历沟通列表，按 ``uid`` 或 ``security_id`` 查找联系人。

	返回 (friend_item, error_response)：
	- 找到联系人：返回 (item, None)
	- 平台返回失败响应：返回 (None, raw_response)
	- 遍历完成仍未找到：返回 (None, None)
	- 响应缺少列表容器：抛 FriendListShapeError，不能据此断言联系人不存在
	"""
	try:
		needle = str(identifier).strip()
	except ValueError:
		# 超过 4300 位的整数连 str() 都不可用；平台 uid 不可能是这种值。
		return None, None
	if not needle:
		return None, None

	page = start_page
	terminated = False
	seen_signatures: set[tuple[tuple[str, str], ...]] = set()
	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		if not platform.is_success(resp):
			return None, resp

		platform_data = platform.unwrap_data(resp)
		if not isinstance(platform_data, dict):
			# 页面数据形状漂移（如返回字符串）按空页处理，不把 AttributeError
			# 兜成 INTERNAL_ERROR（Issue #1141 同一口径）。
			platform_data = {}
		page_items = friend_list_page_items(platform_data)
		# 同一页先完整检查稳定 uid，再回退到轮换的 securityId。这样即使某个
		# securityId 恰好与另一条记录的 uid 文本相同，也不会命中错误联系人。
		# uid 只认非空字符串：布尔 / 容器漂移时 str() 会让不同联系人共用
		# 同一文本，匹配到错误对象（Issue #2063）。
		for item in page_items:
			if friend_uid(item) == needle:
				return item, None
		for item in page_items:
			# securityId 的匹配必须与下游 friend_security_id 同口径：漂移成整数 /
			# 容器的值不能被 stable_uid 转成文本后误命中，否则会找到一个写请求
			# 用不上的令牌（Issue #2883）；snake_case 也要按文档支持。
			if friend_security_id(item) == needle:
				return item, None

		# securityId 会在请求间轮换，不能用于重复页判定；有 uid 时必须优先用 uid。
		# 同一联系人的不同职位各占一行，签名要带上职位 ID，否则相邻两页只因
		# 职位不同而 uid 相同就会被当成重复页，漏掉后面的目标联系人（Issue #2883）。
		# 签名全为空时无法判断是否重复页，继续翻页，避免把缺字段的新页面
		# 当成重复页提前结束（与 services/friend_list_pages 的聚合守卫一致）。
		identities = [_lookup_identity(item) for item in page_items]
		has_more = platform_data.get("hasMore")
		if any(identity[0] or identity[1] for identity in identities):
			signature = tuple(identities)
			if signature in seen_signatures:
				if has_more is True:
					# 重复页仍明确声明有后续时遍历结果不可信：保留早停，
					# 但不能落成确定的「不存在」（Issue #2916）。
					raise FriendLookupLimitExceeded(
						"沟通列表出现重复页但仍声明有后续，无法确认联系人是否存在；"
						"请先用 boss chat 取到稳定 uid 再查询，不要重放同一 security_id 查询"
					)
				terminated = True
				break
			seen_signatures.add(signature)

		if not page_items:
			# 形状守卫先于内容守卫：没有可识别的列表容器时，兄弟键是字符串 /
			# 对象这类真值也不能当成「整页元素被跳过」继续翻页，否则单个畸形
			# 响应会翻满上限并放大平台请求（Issue #1978 / #2415）。
			if has_more is not False and not _has_list_container(platform_data):
				# 成功码但没有可识别的列表容器（zpData 缺失 / 非 dict / 键为
				# null 或漂移成字符串 / 对象）：这一页不能证明列表完整，不能
				# 落成「联系人不存在」。真正的空页 ``result: []`` 与显式
				# hasMore=false 仍按不存在处理。
				raise FriendListShapeError(
					"沟通列表响应不包含可识别的联系人列表，无法确认联系人是否存在；"
					"请刷新沟通列表后重试，不要据此判定联系人不存在"
				)
			if has_more is not False and friend_list_page_has_content(platform_data):
				# 容器非空但整页元素被跳过：不是空页，继续翻页，不能据此断言
				# 联系人不存在（Issue #1878）。
				page += 1
				continue
			terminated = True
			break
		if has_more is False:
			terminated = True
			break
		page += 1

	if not terminated:
		raise FriendLookupLimitExceeded(
			"沟通列表分页遍历超过上限，未能确认联系人是否存在；"
			"请先用 boss chat 取到稳定 uid 再查询，不要重放同一 security_id 查询"
		)
	return None, None


def _has_list_container(platform_data: dict[str, Any]) -> bool:
	"""本页是否带有可识别的列表容器（result / friendList 至少一个是 list）。"""
	return isinstance(platform_data.get("result"), list) or isinstance(platform_data.get("friendList"), list)


# 向后兼容：旧名称保留为别名（匹配逻辑已放宽为 uid 或 security_id）
find_friend_by_security_id = find_friend
