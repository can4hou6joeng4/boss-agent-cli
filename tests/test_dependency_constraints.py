"""安全依赖下界及 JWT 选项隔离回归，全部使用本地合成数据。"""

from importlib.metadata import requires, version

import jwt
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest


SECURITY_REQUIREMENTS = [
	("pyjwt", "mcp", "2.15.1", "3.0.0", ("2.13.0", "2.14.0", "2.15.0")),
	("urllib3", "crawl", "2.8.0", "3.0.0", ("1.26.0", "2.7.0")),
	("virtualenv", "dev", "21.7.13", "22.0.0", ("21.7.0", "21.7.11", "21.7.12")),
]


def package_requirement(name: str) -> Requirement:
	matches = [
		requirement
		for text in requires("boss-agent-cli") or []
		if canonicalize_name((requirement := Requirement(text)).name) == name
	]
	assert len(matches) == 1, f"{name} 必须有唯一的直接安全约束"
	return matches[0]


@pytest.mark.parametrize("name,extra,minimum,upper,excluded", SECURITY_REQUIREMENTS)
def test_security_floor_is_scoped_to_its_extra(name, extra, minimum, upper, excluded):
	requirement = package_requirement(name)
	assert requirement.specifier.contains(minimum)
	assert not requirement.specifier.contains(upper)
	for old_version in excluded:
		assert not requirement.specifier.contains(old_version)
	assert requirement.marker is not None
	for selected in ("", "mcp", "crawl", "dev"):
		assert requirement.marker.evaluate({"extra": selected}) is (selected == extra)


@pytest.mark.parametrize("name", [case[0] for case in SECURITY_REQUIREMENTS])
def test_installed_dependency_satisfies_security_constraint(name):
	"""门禁须安装 all-extras；缺少依赖不能被跳过或当作已验证。"""
	assert package_requirement(name).specifier.contains(version(name))


@pytest.mark.parametrize("method", ["decode", "decode_complete"])
def test_pyjwt_preserves_caller_options_and_rechecks_expiry(method):
	"""不验签读取不能污染同一选项对象后续的正常声明校验。"""
	key = "fixture-only-signing-key-not-for-production"
	encoded = jwt.encode({"sub": "fixture-subject", "exp": 1}, key, algorithm="HS256")
	options = {"verify_signature": False}
	decode = getattr(jwt, method)

	decode(encoded, options=options)
	assert options == {"verify_signature": False}

	options["verify_signature"] = True
	with pytest.raises(jwt.ExpiredSignatureError):
		decode(encoded, key, algorithms=["HS256"], options=options)
	assert options == {"verify_signature": True}
