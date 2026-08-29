"""code 37 语境分类器契约测试 — 锁定「明确 token 过期才刷新，其余 fail closed」。"""
import pytest

from boss_agent_cli.api.zhipin_errors import classify_code_37


@pytest.mark.parametrize(
	("response", "expected"),
	[
		({"code": 37, "message": "stoken 已过期"}, "token_expired"),
		({"code": 37, "message": "登录状态已失效"}, "token_expired"),
		({"code": 37, "message": "环境存在异常"}, "environment_risk"),
		({"code": 37, "message": "请求失败"}, "environment_risk"),
		({"code": 37, "msg": "认证过期"}, "token_expired"),
	],
)
def test_classify_code_37_by_message(response: dict, expected: str) -> None:
	assert classify_code_37(response) == expected


@pytest.mark.parametrize(
	"response",
	[
		{"code": 37},
		{"code": 37, "message": ""},
		{"code": 37, "message": None},
		{"code": 37, "message": 123},
		{},
	],
)
def test_ambiguous_code_37_fails_closed_as_environment_risk(response: dict) -> None:
	"""message 缺失 / 为空 / 非字符串时必须保守归为环境风险。"""
	assert classify_code_37(response) == "environment_risk"
