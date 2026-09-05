"""从浏览器 Copy as cURL (bash) 文本提取登录态，不执行原请求。"""

import re
import shlex
from typing import Any
from urllib.parse import urlsplit


_VALUE_OPTIONS = {
	"-b": "cookie", "--cookie": "cookie", "-H": "header", "--header": "header",
	"-A": "user-agent", "--user-agent": "user-agent", "--url": "url",
	"-X": "ignore", "--request": "ignore", "-d": "ignore", "--data": "ignore",
	"--data-raw": "ignore", "--data-binary": "ignore", "--data-urlencode": "ignore",
}
_FLAG_OPTIONS = {"--compressed", "--globoff", "-g", "--insecure", "-k"}


def parse_curl_auth(command: str) -> dict[str, Any]:
	"""只返回原生 TokenStore 字段；不保存请求头、URL 或业务请求体。"""
	parts = shlex.split(command.replace("\\\r\n", "").replace("\\\n", ""))
	if not parts or parts[0] != "curl":
		raise ValueError("需要浏览器 Copy as cURL (bash) 原始文本")
	values: dict[str, list[str]] = {"cookie": [], "header": [], "user-agent": [], "url": []}
	index = 1
	while index < len(parts):
		part = parts[index]
		index += 1
		if part in _FLAG_OPTIONS:
			continue
		option, separator, value = part.partition("=") if part.startswith("--") else (part, "", "")
		if part[:2] in _VALUE_OPTIONS and len(part) > 2 and not part.startswith("--"):
			option, separator, value = part[:2], "attached", part[2:]
		if option in _VALUE_OPTIONS:
			if not separator:
				if index >= len(parts):
					raise ValueError("cURL 选项缺少参数")
				value = parts[index]
				index += 1
			kind = _VALUE_OPTIONS[option]
			if kind != "ignore":
				values[kind].append(value)
		elif part.startswith("https://"):
			values["url"].append(part)
		else:
			raise ValueError("不支持此 cURL 语法，请使用 Copy as cURL (bash)")

	if len(values["url"]) != 1:
		raise ValueError("cURL 必须包含一个 BOSS 直聘请求 URL")
	url = urlsplit(values["url"][0])
	if url.scheme != "https" or url.hostname != "www.zhipin.com" or url.port not in (None, 443) or url.username or url.password:
		raise ValueError("仅支持 https://www.zhipin.com 的请求")
	for header in values["header"]:
		if any(char in header for char in "\r\n\0"):
			raise ValueError("cURL 请求头包含非法字符")
		name, separator, value = header.partition(":")
		if not separator:
			raise ValueError("仅支持内联请求头，不读取请求头文件")
		name = name.strip().lower()
		if name in ("cookie", "user-agent"):
			values[name].append(value.strip())
	cookies: dict[str, str] = {}
	for cookie_header in values["cookie"]:
		for item in cookie_header.split(";"):
			if not item.strip():
				continue
			name, separator, value = item.strip().partition("=")
			if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(char in value for char in "\r\n\0"):
				raise ValueError("仅支持内联 Cookie，不读取 Cookie 文件")
			if name in cookies and cookies[name] != value:
				raise ValueError("cURL 包含冲突的同名 Cookie")
			cookies[name] = value
	if not cookies.get("wt2"):
		raise ValueError("cURL Cookie 缺少 wt2")
	user_agent = values["user-agent"][-1] if values["user-agent"] else ""
	if any(char in user_agent for char in "\r\n\0"):
		raise ValueError("User-Agent 包含非法字符")
	return {"cookies": cookies, "stoken": cookies.get("__zp_stoken__", ""), "user_agent": user_agent}
