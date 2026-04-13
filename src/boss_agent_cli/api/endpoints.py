"""API endpoint constants and lookup tables — compatibility layer over boss.yaml.

All constants are loaded from boss.yaml at import time. Existing code that imports
SEARCH_URL, CITY_CODES, etc. continues to work unchanged.
"""
from boss_agent_cli.api.endpoints_loader import get_spec

_spec = get_spec()

BASE_URL = _spec.base_url

# ── Web page URLs (for dynamic Referer) ──────────────────────────────
WEB_GEEK_BASE = _spec.web_pages.get("geek_base", f"{BASE_URL}/web/geek")
WEB_GEEK_JOB_URL = _spec.web_pages.get("geek_job", f"{WEB_GEEK_BASE}/job")
WEB_GEEK_RECOMMEND_URL = _spec.web_pages.get("geek_recommend", f"{WEB_GEEK_BASE}/recommend")
WEB_GEEK_CHAT_URL = _spec.web_pages.get("geek_chat", f"{WEB_GEEK_BASE}/chat")
WEB_GEEK_RESUME_URL = _spec.web_pages.get("geek_resume", f"{WEB_GEEK_BASE}/resume")

# ── API endpoints ────────────────────────────────────────────────────
def _url(name: str) -> str:
	return _spec.endpoints[name].url

SEARCH_URL = _url("search")
RECOMMEND_URL = _url("recommend")
DETAIL_URL = _url("detail")
GREET_URL = _url("greet")
JOB_CARD_URL = _url("job_card")
USER_INFO_URL = _url("user_info")
RESUME_BASEINFO_URL = _url("resume_baseinfo")
RESUME_EXPECT_URL = _url("resume_expect")
DELIVER_LIST_URL = _url("deliver_list")
FRIEND_LIST_URL = _url("friend_list")
INTERVIEW_DATA_URL = _url("interview_data")
JOB_HISTORY_URL = _url("job_history")
CHAT_HISTORY_URL = _url("chat_history")
FRIEND_LABEL_ADD_URL = _url("friend_label_add")
FRIEND_LABEL_DELETE_URL = _url("friend_label_delete")
EXCHANGE_REQUEST_URL = _url("exchange_request")

# ── API response codes ──────────────────────────────────────────────
CODE_SUCCESS = _spec.response_codes.get("success", 0)
CODE_STOKEN_EXPIRED = _spec.response_codes.get("stoken_expired", 37)
CODE_RATE_LIMITED = _spec.response_codes.get("rate_limited", 9)
CODE_ACCOUNT_RISK = _spec.response_codes.get("account_risk", 36)

# ── Browser-like headers ─────────────────────────────────────────────
DEFAULT_HEADERS = dict(_spec.default_headers)

# ── Endpoint → Referer mapping ───────────────────────────────────────
REFERER_MAP = {ep.url: ep.referer for ep in _spec.endpoints.values()}

# ── Lookup tables ───────────────────────────────────────────────────
CITY_CODES: dict[str, str] = _spec.lookups.get("city", {})
SALARY_CODES: dict[str, str] = _spec.lookups.get("salary", {})
EXPERIENCE_CODES: dict[str, str] = _spec.lookups.get("experience", {})
EDUCATION_CODES: dict[str, str] = _spec.lookups.get("education", {})
SCALE_CODES: dict[str, str] = _spec.lookups.get("scale", {})
INDUSTRY_CODES: dict[str, str] = _spec.lookups.get("industry", {})
STAGE_CODES: dict[str, str] = _spec.lookups.get("stage", {})
JOB_TYPE_CODES: dict[str, str] = _spec.lookups.get("job_type", {})
