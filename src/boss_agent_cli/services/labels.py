"""联系人标签名称与平台 ID 的唯一真源。

命令层（``boss mark``）与 wizard 的 ``candidate_mark`` 必须接受同一组标签：
两张表分叉时 wizard 会拒绝 CLI 支持的电话 / 微信 / 牛人发起标签，又会把
任意数字 ID 原样发给平台（Issue #1905）。
"""

from __future__ import annotations

LABEL_MAP: dict[str, int] = {
	"新招呼": 1,
	"沟通中": 2,
	"已约面": 3,
	"已获取简历": 4,
	"已交换电话": 5,
	"已交换微信": 6,
	"不合适": 7,
	"牛人发起": 8,
	"收藏": 11,
}

LABEL_NAMES: dict[int, str] = {value: name for name, value in LABEL_MAP.items()}


def brief_label(value: str, limit: int = 40) -> str:
	"""错误信息里回显的标签值：超长输入截断，信封不随输入规模膨胀（Issue #1437）。"""
	if len(value) <= limit:
		return value
	return f"{value[:limit]}…（共 {len(value)} 字符）"


def parse_label_id(label_input: str) -> int | None:
	"""把标签名称或数字 ID 解析为平台 ID；未知输入返回 ``None``。

``str.isdigit()`` 对 '①' / '²' 这类非 ASCII 数字也为真，但 ``int()`` 会抛
ValueError；4300 位以上的纯数字串同样让 ``int()`` 抛裸 ValueError
（Issue #1306 #1437），因此只接受 ASCII 数字串，并把长度上限交给调用方已有的
异常映射处理。数字 ID 必须是 ``LABEL_MAP`` 的成员：999 之类的任意数字既不
对应任何标签，也不能发给平台标签接口（Issue #1575）。
"""
	if label_input in LABEL_MAP:
		return LABEL_MAP[label_input]
	if label_input.isascii() and label_input.isdigit():
		try:
			label_id = int(label_input)
		except (ValueError, OverflowError):
			return None
		return label_id if label_id in LABEL_NAMES else None
	return None
