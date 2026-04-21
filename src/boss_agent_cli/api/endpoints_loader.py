"""Load API spec from boss.yaml — single source of truth for endpoints, headers, and lookups."""
import importlib.resources
from dataclasses import dataclass
from typing import Any

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


def _load_yaml() -> dict[str, Any]:
	"""Load boss.yaml from package resources."""
	ref = importlib.resources.files("boss_agent_cli.api").joinpath("boss.yaml")
	result: dict[str, Any] = yaml.safe_load(ref.read_text(encoding="utf-8"))
	return result


def load_boss_api_spec() -> BossApiSpec:
	"""Parse boss.yaml into typed spec."""
	raw = _load_yaml()
	base_url = raw["base_url"]

	endpoints = {}
	for name, ep in raw.get("endpoints", {}).items():
		url = base_url + ep["path"]
		referer = base_url + ep.get("referer", "/")
		endpoints[name] = EndpointSpec(
			name=name,
			method=ep.get("method", "GET"),
			url=url,
			referer=referer,
		)

	# Expand web_pages to full URLs
	web_pages = {k: base_url + v for k, v in raw.get("web_pages", {}).items()}

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
		ref = importlib.resources.files("boss_agent_cli.api").joinpath("recruiter.yaml")
		raw: dict[str, Any] = yaml.safe_load(ref.read_text(encoding="utf-8"))
		base_url = raw["base_url"]

		endpoints = {}
		for name, ep in raw.get("endpoints", {}).items():
			url = base_url + ep["path"]
			referer = base_url + ep.get("referer", "/")
			endpoints[name] = EndpointSpec(
				name=name,
				method=ep.get("method", "GET"),
				url=url,
				referer=referer,
			)

		web_pages = {k: base_url + v for k, v in raw.get("web_pages", {}).items()}
		headers = dict(raw.get("default_headers", {}))
		headers["Origin"] = base_url
		headers["Referer"] = base_url + "/"

		_RECRUITER_SPEC = BossApiSpec(
			base_url=base_url,
			web_pages=web_pages,
			default_headers=headers,
			response_codes=raw.get("response_codes", {}),
			endpoints=endpoints,
			lookups=raw.get("lookups", {}),
		)
	return _RECRUITER_SPEC
