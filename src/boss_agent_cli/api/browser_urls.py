"""浏览器通道共用的 URL 常量与平台域名校验（api / auth / automation / doctor 共用的单一实现）。"""

from urllib.parse import urlparse

DEFAULT_CDP_URL = "http://localhost:9222"

_ZHIPIN_HOST = "zhipin.com"
_ZHILIAN_HOST = "zhaopin.com"


def _is_platform_url(url: str, expected_host: str) -> bool:
	"""精确 hostname 校验：只接受该 host 及其子域，拒绝子串陷阱。"""
	host = urlparse(url).hostname
	if host is None:
		return False
	host = host.rstrip(".").lower()
	return host == expected_host or host.endswith(f".{expected_host}")


def is_zhipin_url(url: str) -> bool:
	return _is_platform_url(url, _ZHIPIN_HOST)


def is_zhilian_url(url: str) -> bool:
	return _is_platform_url(url, _ZHILIAN_HOST)
