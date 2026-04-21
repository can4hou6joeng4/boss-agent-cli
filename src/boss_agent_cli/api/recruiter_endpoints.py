"""Recruiter-side API endpoint constants — loaded from recruiter.yaml."""

from boss_agent_cli.api.endpoints_loader import get_recruiter_spec

_spec = get_recruiter_spec()

BASE_URL = _spec.base_url

# Web page URLs
WEB_BOSS_BASE = _spec.web_pages.get("boss_base", f"{BASE_URL}/web/boss")
WEB_BOSS_RECOMMEND = _spec.web_pages.get("boss_recommend", f"{WEB_BOSS_BASE}/recommend/geeks")
WEB_BOSS_CHAT = _spec.web_pages.get("boss_chat", f"{WEB_BOSS_BASE}/chat")
WEB_BOSS_JOBS = _spec.web_pages.get("boss_jobs", f"{WEB_BOSS_BASE}/job")


def _url(name: str) -> str:
	return _spec.endpoints[name].url


# Chat endpoints
BOSS_FRIEND_LIST_URL = _url("boss_friend_list")
BOSS_CHAT_HISTORY_URL = _url("boss_chat_history")
BOSS_SEND_MESSAGE_URL = _url("boss_send_message")

# Application endpoints
BOSS_RECOMMEND_GEEKS_URL = _url("boss_recommend_geeks")

# Resume endpoints
BOSS_GEEK_RESUME_URL = _url("boss_geek_resume")
BOSS_REQUEST_RESUME_URL = _url("boss_request_resume")

# Job management endpoints
BOSS_JOB_LIST_URL = _url("boss_job_list")
BOSS_JOB_DETAIL_URL = _url("boss_job_detail")
BOSS_JOB_PUBLISH_URL = _url("boss_job_publish")
BOSS_JOB_EDIT_URL = _url("boss_job_edit")
BOSS_JOB_CLOSE_URL = _url("boss_job_close")

# Response codes
CODE_SUCCESS = _spec.response_codes.get("success", 0)
CODE_STOKEN_EXPIRED = _spec.response_codes.get("stoken_expired", 37)
CODE_RATE_LIMITED = _spec.response_codes.get("rate_limited", 9)
CODE_ACCOUNT_RISK = _spec.response_codes.get("account_risk", 36)

# Headers + Referer
DEFAULT_HEADERS = dict(_spec.default_headers)
REFERER_MAP = {ep.url: ep.referer for ep in _spec.endpoints.values()}
