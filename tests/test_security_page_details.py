"""security_page 的错误里必须带上命中的页面路径（Issue #488）。

否则用户只能报告 `risk_source=security_page`，无法区分 verify.html（安全校验）
与 security.html（风险页），维护者也就没法判断成因。
"""

import pytest

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.zhipin_errors import platform_risk_page_response, raise_platform_risk_response


@pytest.mark.parametrize(
	"path",
	["/web/passport/zp/verify.html", "/web/passport/zp/security.html"],
)
def test_security_page_response_carries_the_path(path: str) -> None:
	response = platform_risk_page_response(url=f"https://www.zhipin.com{path}")

	assert response is not None
	assert response["__cli_risk_source__"] == "security_page"
	assert response["path"] == path


def test_security_page_error_details_include_the_path() -> None:
	response = platform_risk_page_response(url="https://www.zhipin.com/web/passport/zp/verify.html")

	with pytest.raises(AccountRiskError) as caught:
		raise_platform_risk_response(response)

	assert caught.value.details["risk_source"] == "security_page"
	assert caught.value.details["path"] == "/web/passport/zp/verify.html"


def test_ordinary_page_has_no_path_in_details_contract() -> None:
	"""没有命中时函数返回 None，不受新增字段影响。"""
	assert platform_risk_page_response(url="https://www.zhipin.com/web/geek/jobs") is None
