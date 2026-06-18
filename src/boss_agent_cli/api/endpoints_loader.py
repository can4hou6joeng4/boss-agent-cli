"""Load API specs from YAML — single source of truth for endpoints, headers, and lookups."""
import importlib.resources
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import yaml


@dataclass(frozen=True)
class EndpointSpec:
	name: str
	method: str
	url: str
	referer: str


@dataclass(frozen=True)
class BossApiSpec:
	base_url: str
	web_pages: dict[str, str]
	default_headers: dict[str, str]
	response_codes: dict[str, int]
	endpoints: dict[str, EndpointSpec]
	lookups: dict[str, dict[str, str]]


def _load_yaml(filename: str) -> dict[str, Any]:
	"""Load an API YAML file from package resources."""
	ref = importlib.resources.files("boss_agent_cli.api").joinpath(filename)
	result: dict[str, Any] = yaml.safe_load(ref.read_text(encoding="utf-8"))
	return result


def _absolute_url(base_url: str, value: str) -> str:
	"""Resolve either an absolute URL or a base-relative path."""
	parsed = urlparse(value)
	if parsed.scheme and parsed.netloc:
		return value
	if value.startswith("/"):
		return base_url + value
	return base_url + "/" + value


def _parse_api_spec(raw: dict[str, Any]) -> BossApiSpec:
	base_url = raw["base_url"]

	endpoints = {}
	for name, ep in raw.get("endpoints", {}).items():
		url = _absolute_url(base_url, ep.get("url") or ep["path"])
		referer = _absolute_url(base_url, ep.get("referer", "/"))
		endpoints[name] = EndpointSpec(
			name=name,
			method=ep.get("method", "GET"),
			url=url,
			referer=referer,
		)

	web_pages = {k: _absolute_url(base_url, v) for k, v in raw.get("web_pages", {}).items()}

	# Default headers with Origin and Referer
	headers = dict(raw.get("default_headers", {}))
	headers["Origin"] = base_url
	headers["Referer"] = base_url + "/"

	return BossApiSpec(
		base_url=base_url,
		web_pages=web_pages,
		default_headers=headers,
		response_codes=raw.get("response_codes", {}),
		endpoints=endpoints,
		lookups=raw.get("lookups", {}),
	)


def load_boss_api_spec() -> BossApiSpec:
	"""Parse boss.yaml into typed spec."""
	return _parse_api_spec(_load_yaml("boss.yaml"))


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
		_RECRUITER_SPEC = _parse_api_spec(_load_yaml("recruiter.yaml"))
	return _RECRUITER_SPEC


# ── Zhilian spec ───────────────────────────────────────────────────────
_ZHILIAN_SPEC: BossApiSpec | None = None


def get_zhilian_spec() -> BossApiSpec:
	"""Get cached Zhilian spec singleton."""
	global _ZHILIAN_SPEC
	if _ZHILIAN_SPEC is None:
		_ZHILIAN_SPEC = _parse_api_spec(_load_yaml("zhilian.yaml"))
	return _ZHILIAN_SPEC
