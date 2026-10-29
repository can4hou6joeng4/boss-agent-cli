"""Detect an egress/locale contradiction before a workflow starts.

A session claims a locale (``Accept-Language``) and a timezone, and it exits
through some address. When those disagree the client is telling the platform two
incompatible things at once. This module makes that disagreement explicit: it
never "corrects" the locale, because silently rewriting a Chinese site's
``Accept-Language`` to match a foreign exit is its own kind of tell.

The probe is opt-in (``BOSS_EGRESS_PROBE_URL``). Without it nothing is sent
anywhere and the verdict is ``unknown`` rather than a fabricated ``ok``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

PROBE_ENV_KEY = "BOSS_EGRESS_PROBE_URL"

# Region -> (language region subtag, timezone prefix). Deliberately small: an
# unlisted region yields "unknown", never a guessed expectation.
_REGION_EXPECTATIONS: dict[str, tuple[str, str]] = {
	"CN": ("CN", "Asia/Shanghai"),
	"HK": ("HK", "Asia/Hong_Kong"),
	"TW": ("TW", "Asia/Taipei"),
	"SG": ("SG", "Asia/Singapore"),
	"JP": ("JP", "Asia/Tokyo"),
	"KR": ("KR", "Asia/Seoul"),
	"US": ("US", "America/"),
	"GB": ("GB", "Europe/London"),
	"DE": ("DE", "Europe/Berlin"),
}

# Timezone prefixes that are unambiguous enough to infer a region from.
_TZ_REGION_HINTS: tuple[tuple[str, str], ...] = (
	("Asia/Shanghai", "CN"),
	("Asia/Chongqing", "CN"),
	("Asia/Urumqi", "CN"),
	("Asia/Hong_Kong", "HK"),
	("Asia/Taipei", "TW"),
	("Asia/Singapore", "SG"),
	("Asia/Tokyo", "JP"),
	("Asia/Seoul", "KR"),
	("America/", "US"),
	("Europe/London", "GB"),
	("Europe/Berlin", "DE"),
)


def language_region(accept_language: str) -> str:
	"""Return the region subtag of the first language tag, uppercase.

	``"zh-CN,zh;q=0.9"`` -> ``"CN"``; ``"zh"`` -> ``""``.
	"""
	first = accept_language.split(",", 1)[0].strip()
	first = first.split(";", 1)[0].strip()
	parts = first.split("-")
	if len(parts) < 2:
		return ""
	return parts[-1].upper()


def timezone_region(timezone: str) -> str:
	"""Return the region implied by ``timezone``, or ``""`` when unknown."""
	for prefix, region in _TZ_REGION_HINTS:
		if timezone.startswith(prefix):
			return region
	return ""


@dataclass(frozen=True)
class EgressVerdict:
	"""Outcome of one contradiction check."""

	status: str  # "ok" | "mismatch" | "unknown"
	exit_country: str = ""
	contradictions: tuple[str, ...] = field(default_factory=tuple)
	detail: str = ""

	@property
	def refused(self) -> bool:
		return self.status == "mismatch"

	def as_dict(self) -> dict[str, Any]:
		return {
			"status": self.status,
			"exit_country": self.exit_country,
			"contradictions": list(self.contradictions),
			"detail": self.detail,
		}


class EgressContradictionError(RuntimeError):
	"""Raised when the workflow must not start because the egress contradicts."""

	def __init__(self, verdict: EgressVerdict) -> None:
		super().__init__(verdict.detail or "出口与声明的语言/时区矛盾")
		self.verdict = verdict


def evaluate_egress(
	*,
	exit_country: str,
	accept_language: str,
	timezone: str,
) -> EgressVerdict:
	"""Compare the exit region against the locale and timezone the session claims.

	Only contradicts when both sides are known. An unknown exit region or an
	unlisted country produces ``unknown`` so a missing probe never blocks work.
	"""
	country = (exit_country or "").strip().upper()
	if len(country) != 2:
		return EgressVerdict(status="unknown", exit_country=country, detail="未取得出口地区，跳过一致性检查")
	if country not in _REGION_EXPECTATIONS:
		return EgressVerdict(status="unknown", exit_country=country, detail=f"出口地区 {country} 不在已知表中，无法判断")

	expected_lang, expected_tz = _REGION_EXPECTATIONS[country]
	contradictions: list[str] = []

	if lang := language_region(accept_language):
		if lang != expected_lang:
			contradictions.append(f"Accept-Language 地区为 {lang}，出口在 {country}")
	if tz := timezone_region(timezone):
		if tz != country:
			contradictions.append(f"时区落在 {tz}，出口在 {country}")

	if contradictions:
		return EgressVerdict(
			status="mismatch",
			exit_country=country,
			contradictions=tuple(contradictions),
			detail="；".join(contradictions),
		)
	return EgressVerdict(status="ok", exit_country=country, detail=f"出口 {country} 与语言/时区一致")


def probe_egress(
	*,
	url: str | None = None,
	environ: Mapping[str, str] | None = None,
	client: Any = None,
) -> EgressVerdict:
	"""Fetch the exit country from an operator-supplied probe endpoint.

	``BOSS_EGRESS_PROBE_URL`` must return a JSON object with a two-letter
	``country``/``country_code`` field. Nothing is requested when it is unset.
	"""
	source = os.environ if environ is None else environ
	target = url or source.get(PROBE_ENV_KEY, "").strip()
	if not target:
		return EgressVerdict(status="unknown", detail=f"未配置 {PROBE_ENV_KEY}，未探测出口")

	own_client = client is None
	if own_client:
		import httpx

		client = httpx.Client(timeout=10)
	try:
		response = client.get(target)
		payload = response.json()
	except Exception as exc:
		return EgressVerdict(status="unknown", detail=f"出口探测失败：{type(exc).__name__}")
	finally:
		if own_client and client is not None:
			client.close()

	country = ""
	if isinstance(payload, dict):
		for key in ("country", "country_code", "countryCode"):
			value = payload.get(key)
			if isinstance(value, str) and len(value.strip()) == 2:
				country = value.strip().upper()
				break
	return EgressVerdict(status="unknown", exit_country=country, detail="已取得出口地区")


def ensure_egress_consistent(
	*,
	exit_country: str,
	accept_language: str,
	timezone: str,
) -> EgressVerdict:
	"""Raise :class:`EgressContradictionError` when the session contradicts itself."""
	verdict = evaluate_egress(
		exit_country=exit_country,
		accept_language=accept_language,
		timezone=timezone,
	)
	if verdict.refused:
		raise EgressContradictionError(verdict)
	return verdict
