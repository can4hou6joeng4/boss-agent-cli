"""Load API specs from YAML — single source of truth for endpoints, headers, and lookups."""

import importlib.resources
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import yaml


@dataclass(frozen=True)
class EndpointSpec:
	name: str
	method: str
	url: str
	referer: str
	effect: Literal["read", "write", "unknown"] = "unknown"
	replay_safe: bool = False


@dataclass(frozen=True)
class BossApiSpec:
	base_url: str
	web_pages: dict[str, str]
	default_headers: dict[str, str]
	response_codes: dict[str, int]
	endpoints: dict[str, EndpointSpec]
	lookups: dict[str, dict[str, str]]


def _short_repr(value: Any) -> str:
	"""配置值只用于错误信息：截断，避免超长标量把信封撑爆。"""
	rendered = repr(value)
	return rendered if len(rendered) <= 80 else rendered[:77] + "..."


def _require_mapping(value: Any, path: str, filename: str) -> dict[str, Any]:
	if not isinstance(value, dict):
		raise ValueError(f"{filename}: {path} 必须是映射，实际是 {type(value).__name__}")
	return value


def _require_text(value: Any, path: str, filename: str) -> str:
	if not isinstance(value, str) or not value:
		raise ValueError(f"{filename}: {path} 必须是非空字符串，实际是 {_short_repr(value)}")
	return value


def _coerce_response_code(value: Any, path: str, filename: str) -> int:
	"""response_codes 的值统一成 int（Issue #2446）。

	`stoken_expired: "37"` 是合法 YAML，但码表下游全部按整数比较：字符串会让
	风控码、限流码与 token 刷新静默失效，且没有任何告警。bool 是 int 子类，
	必须显式拒绝；超长数字串的 ``int()`` 抛 ``ValueError``，按配置错误收口。
	"""
	if type(value) is int:
		return value
	if isinstance(value, str):
		try:
			return int(value)
		except (TypeError, ValueError, OverflowError) as exc:
			raise ValueError(f"{filename}: {path} 必须是整数，实际是 {_short_repr(value)}") from exc
	raise ValueError(f"{filename}: {path} 必须是整数，实际是 {type(value).__name__}")


def _load_yaml(filename: str) -> dict[str, Any]:
	"""Load an API YAML file from package resources."""
	ref = importlib.resources.files("boss_agent_cli.api").joinpath(filename)
	try:
		text = ref.read_text(encoding="utf-8")
	except OSError as exc:
		raise ValueError(f"{filename}: 无法读取 API 配置：{exc}") from exc
	try:
		raw = yaml.safe_load(text)
	except (yaml.YAMLError, ValueError, OverflowError, RecursionError) as exc:
		# 非法 YAML 抛 YAMLError；超长标量 / 深嵌套另可能是裸 ValueError、
		# OverflowError 或 RecursionError（与本地 JSON 解析族同口径，Issue #2447）。
		raise ValueError(f"{filename}: 无法解析 YAML：{exc}") from exc
	return _require_mapping(raw, "顶层", filename)


def _absolute_url(base_url: str, value: str) -> str:
	"""Resolve either an absolute URL or a base-relative path."""
	parsed = urlparse(value)
	if parsed.scheme and parsed.netloc:
		return value
	if value.startswith("/"):
		return base_url + value
	return base_url + "/" + value


def _parse_api_spec(raw: dict[str, Any], filename: str = "boss.yaml") -> BossApiSpec:
	"""把 YAML 原始映射解析成类型化 spec；形状不符时报带文件名与路径的配置错误。

	所有校验都发生在导入期：包内 YAML 一次性损坏（打包错误、手工编辑）必须在
	启动时以可读信息失败，而不是在随机命令上以 ``TypeError`` / ``AttributeError``
	崩溃（Issue #2447）。
	"""
	raw = _require_mapping(raw, "顶层", filename)
	base_url = _require_text(raw.get("base_url"), "base_url", filename)

	raw_endpoints = _require_mapping(raw.get("endpoints", {}), "endpoints", filename)
	endpoints = {}
	for name, ep in raw_endpoints.items():
		path = f"endpoints.{name}"
		ep = _require_mapping(ep, path, filename)
		# url / path 至少一个须是非空字符串。空串、null、数字与布尔都是配置
		# 错误：``ep.get("url") or ep["path"]`` 曾把 ``url: ""`` 静默解析到首页
		# （Issue #2447）。
		relative: str | None = None
		for key in ("url", "path"):
			candidate = ep.get(key)
			if isinstance(candidate, str) and candidate:
				relative = candidate
				break
		if relative is None:
			raise ValueError(f"{filename}: {path} 的 url / path 至少需要一个非空字符串")
		url = _absolute_url(base_url, relative)
		referer = _absolute_url(base_url, _require_text(ep.get("referer", "/"), f"{path}.referer", filename))
		method = _require_text(ep.get("method", "GET"), f"{path}.method", filename).upper()
		effect = ep.get("effect", "unknown")
		if effect not in ("read", "write", "unknown"):
			raise ValueError(f"invalid endpoint effect for {name}: {effect}")
		replay_safe = ep.get("replay_safe", False) is True
		if replay_safe and effect != "read":
			raise ValueError(f"only explicit read endpoints may be replay_safe: {name}")
		endpoints[name] = EndpointSpec(
			name=name,
			method=method,
			url=url,
			referer=referer,
			effect=effect,
			replay_safe=replay_safe,
		)

	raw_web_pages = _require_mapping(raw.get("web_pages", {}), "web_pages", filename)
	web_pages = {
		key: _absolute_url(base_url, _require_text(value, f"web_pages.{key}", filename))
		for key, value in raw_web_pages.items()
	}

	raw_codes = _require_mapping(raw.get("response_codes", {}), "response_codes", filename)
	response_codes = {
		key: _coerce_response_code(value, f"response_codes.{key}", filename) for key, value in raw_codes.items()
	}

	raw_lookups = _require_mapping(raw.get("lookups", {}), "lookups", filename)
	lookups: dict[str, dict[str, str]] = {}
	for name, table in raw_lookups.items():
		path = f"lookups.{name}"
		table = _require_mapping(table, path, filename)
		lookups[name] = {
			_require_text(key, f"{path} 的键", filename): _require_text(value, f"{path}.{key}", filename)
			for key, value in table.items()
		}

	# Default headers with Origin and Referer
	raw_headers = _require_mapping(raw.get("default_headers", {}), "default_headers", filename)
	headers = {key: _require_text(value, f"default_headers.{key}", filename) for key, value in raw_headers.items()}
	headers["Origin"] = base_url
	headers["Referer"] = base_url + "/"

	return BossApiSpec(
		base_url=base_url,
		web_pages=web_pages,
		default_headers=headers,
		response_codes=response_codes,
		endpoints=endpoints,
		lookups=lookups,
	)


def load_boss_api_spec() -> BossApiSpec:
	"""Parse boss.yaml into typed spec."""
	return _parse_api_spec(_load_yaml("boss.yaml"), "boss.yaml")


# Module-level singleton
_spec: BossApiSpec | None = None


def get_spec() -> BossApiSpec:
	"""Get cached spec singleton."""
	global _spec
	if _spec is None:
		_spec = load_boss_api_spec()
	return _spec


# ── Recruiter spec ─────────────────────────────────────────────────────
_RECRUITER_SPEC: BossApiSpec | None = None


def get_recruiter_spec() -> BossApiSpec:
	"""Get cached recruiter spec singleton."""
	global _RECRUITER_SPEC
	if _RECRUITER_SPEC is None:
		_RECRUITER_SPEC = _parse_api_spec(_load_yaml("recruiter.yaml"), "recruiter.yaml")
	return _RECRUITER_SPEC


def endpoint_for_request(method: str, url: str) -> EndpointSpec | None:
	"""Resolve declared operation semantics; an unknown GET is not a safe read."""
	parsed = urlparse(url)
	canonical_url = parsed._replace(query="", fragment="").geturl()
	specs: list[BossApiSpec] = []
	for loader in (get_spec, get_recruiter_spec):
		try:
			specs.append(loader())
		except (ValueError, OSError, yaml.YAMLError):
			# 包内 YAML 损坏属于安装 / 打包问题。单个 spec 加载失败只让它名下的
			# 端点退回「未声明语义」（调用方按保守的 effect=unknown 处理，写请求
			# 不可重放），不让候选者请求因为 recruiter.yaml 损坏而整体失败
			# （Issue #2448）。
			continue
	for spec in specs:
		for endpoint in spec.endpoints.values():
			if endpoint.url == canonical_url and endpoint.method == method.upper():
				return endpoint
	return None
