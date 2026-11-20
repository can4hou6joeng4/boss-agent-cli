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
from functools import lru_cache
from typing import Any, Mapping

PROBE_ENV_KEY = "BOSS_EGRESS_PROBE_URL"

# Region -> expected language region subtag. The timezone side is derived from
# ``_TZ_REGION_HINTS`` at check time so the two mappings cannot drift.
# Deliberately small: an unlisted region yields "unknown", never a guessed
# expectation.
_REGION_EXPECTATIONS: dict[str, str] = {
	"CN": "CN",
	"HK": "HK",
	"TW": "TW",
	"SG": "SG",
	"JP": "JP",
	"KR": "KR",
	"US": "US",
	"GB": "GB",
	"DE": "DE",
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
	("America/New_York", "US"),
	("America/Chicago", "US"),
	("America/Denver", "US"),
	("America/Los_Angeles", "US"),
	("America/Phoenix", "US"),
	("America/Anchorage", "US"),
	# 夏威夷的 IANA 名是 Pacific/Honolulu；America/Honolulu 不存在于 tzdata，
	# 原先的条目永远不命中，出口矛盾会被漏检成 unknown（Issue #2129）。
	("Pacific/Honolulu", "US"),
	("America/Indiana/", "US"),
	("America/Kentucky/", "US"),
	("America/Detroit", "US"),
	("America/Boise", "US"),
	("America/Juneau", "US"),
	("America/Metlakatla", "US"),
	("America/Nome", "US"),
	("America/Sitka", "US"),
	("America/Yakutat", "US"),
	("Europe/London", "GB"),
	("Europe/Berlin", "DE"),
)


def language_region(accept_language: str) -> str:
	"""Return the region subtag of the first language tag, uppercase.

	``"zh-CN,zh;q=0.9"`` -> ``"CN"``; ``"zh"`` -> ``""``.

	3 位数字子标签是 BCP 47 的 UN M.49 地区（``es-419`` 拉美、``en-001``
	世界）：原值保留，但它与国家码不是同一坐标系，是否可比较由调用方判断
	（``evaluate_egress`` 只在两边都是 2 位字母地区时判矛盾，Issue #2280）。
	"""
	first = accept_language.split(",", 1)[0].strip()
	first = first.split(";", 1)[0].strip()
	parts = first.split("-")
	# BCP 47 的第二段既可能是地区（``zh-CN``），也可能是书写系统
	# （``zh-Hans`` / ``sr-Latn``）。把任意尾段当地区会得到 "HANS" 这种伪地区，
	# egress 一致性检查会把正常会话判成 mismatch 而阻断启动（Issue #2129）。
	# 跳过 4 位 ASCII 字母的 script；认 2 位 ASCII 字母地区与 3 位数字 M.49
	# 地区；遇到变体 / 扩展即停止。
	for part in parts[1:]:
		if len(part) == 4 and part.isascii() and part.isalpha():
			continue
		if len(part) == 2 and part.isascii() and part.isalpha():
			return part.upper()
		if len(part) == 3 and part.isascii() and part.isdigit():
			return part
		break
	return ""


@lru_cache(maxsize=256)
def _known_timezone(timezone: str) -> bool:
	"""时区名是否是系统 tzdata 里真实存在的 IANA 名称。"""
	import zoneinfo

	try:
		zoneinfo.ZoneInfo(timezone)
	except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
		return False
	return True


def timezone_region(timezone: str) -> str:
	"""Return the region implied by ``timezone``, or ``""`` when unknown.

	先确认名称真实存在再做前缀匹配：``Asia/ShanghaiBogus`` 这类伪造 / 拼错的
	名称命中前缀就会绕过「未知时区不得声称一致」的收尾判定（Issue #2279）。
	"""
	if not timezone or not _known_timezone(timezone):
		return ""
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

	expected_lang = _REGION_EXPECTATIONS[country]
	contradictions: list[str] = []

	if lang := language_region(accept_language):
		# 数字地区（419 / 001）是国家码的超集（UN M.49），不是同一坐标系：拉美
		# 西语会话从美国出口启动是正常组合，不能因此判矛盾（Issue #2280）。
		if len(lang) == 2 and lang != expected_lang:
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
	# 非空的未知时区不能返回 ok：`America/Toronto` 等时区此前被通配前缀误判为
	# 美国，收窄映射后若仍声称「一致」，等于用未知信息放行一致性门禁（Issue #1638）。
	if timezone.strip() and not timezone_region(timezone):
		return EgressVerdict(
			status="unknown",
			exit_country=country,
			detail=f"时区 {timezone.strip()} 不在已知映射表，无法确认与出口 {country} 一致",
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
	try:
		if own_client:
			import httpx

			# 构造也必须放在 try 内：代理环境变量畸形（如缺右括号的 IPv6）或缺少
			# socksio 会让构造期抛 InvalidURL / ImportError 之类的本地错误，逃出
			# 函数后经通用分支变成可重试的 NETWORK_ERROR，而探测的契约是任何失败
			# 都返回 unknown（Issue #2278）。
			client = httpx.Client(timeout=10)
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
