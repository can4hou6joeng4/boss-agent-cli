"""平台 ``friendSource`` 字段漂移按缺失处理（Issue #1314）。

``friendSource`` 只是回传给标签接口的定位参数，缺失时已按 0 处理；但平台把它
返回成非数字字符串 / 对象 / Infinity 时，裸 ``int()`` 抛出的 ``ValueError``
会被 wizard runner 兜成可重试的 ``NETWORK_ERROR``（重试同一份数据必然复现），
``TypeError`` 则落成 ``INTERNAL_ERROR``。现在非数字值一律按 0 处理。
"""

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS


class _Platform:
	def __init__(self, friend_source):
		self.friend_source = friend_source
		self.calls: list[tuple] = []

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def friend_list(self, *, page=1):
		return {
			"code": 0,
			"data": {
				"result": [{
					"uid": 10001,
					"securityId": "sid_current",
					"name": "联系人甲",
					"friendSource": self.friend_source,
				}],
				"hasMore": False,
			},
		}

	def friend_label(self, uid, label_id, friend_source=0, remove=False):
		self.calls.append(("friend_label", uid, label_id, friend_source, remove))
		return {"code": 0, "data": {"updated": True}}

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]


def _context(tmp_path, platform):
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args, **kwargs: platform,
	)


@pytest.mark.parametrize("bad_value", ["abc", ["1"], {"src": 1}, float("inf"), float("nan")])
def test_friend_source_drift_falls_back_to_zero(tmp_path, bad_value):
	platform = _Platform(bad_value)

	DEFAULT_ACTIONS["candidate_mark"](
		_context(tmp_path, platform), {"security_id": "sid_current", "label": "沟通中", "remove": False}, {}
	)

	assert ("friend_label", "10001", 2, 0, False) in platform.calls


def test_numeric_friend_source_string_is_honored(tmp_path):
	platform = _Platform("1")

	DEFAULT_ACTIONS["candidate_mark"](
		_context(tmp_path, platform), {"security_id": "sid_current", "label": "沟通中", "remove": False}, {}
	)

	assert ("friend_label", "10001", 2, 1, False) in platform.calls
