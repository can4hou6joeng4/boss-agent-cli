"""Recruiter-side API endpoint constants — loaded from recruiter.yaml.

Endpoints sourced from newboss/boss-cli project (confirmed via reverse engineering).
"""

from boss_agent_cli.api.endpoints_loader import get_recruiter_spec

_spec = get_recruiter_spec()

BASE_URL = _spec.base_url

# Web page URLs (for Referer headers)
WEB_BOSS_CHAT = _spec.web_pages.get("boss_chat", f"{BASE_URL}/web/chat/index")
WEB_BOSS_RECOMMEND = _spec.web_pages.get("boss_recommend", f"{BASE_URL}/web/chat/recommend")
WEB_BOSS_SEARCH = _spec.web_pages.get("boss_search", f"{BASE_URL}/web/chat/search")


def _url(name: str) -> str:
    return _spec.endpoints[name].url


# ── 候选人列表与筛选 ────────────────────────────────
BOSS_FRIEND_LIST_URL = _url("boss_friend_list")
BOSS_FRIEND_DETAIL_URL = _url("boss_friend_detail")
BOSS_FRIEND_LABELS_URL = _url("boss_friend_labels")
BOSS_FRIEND_NOTE_URL = _url("boss_friend_note")

# ── 消息 / 聊天 ────────────────────────────────────
BOSS_LAST_MESSAGES_URL = _url("boss_last_messages")
BOSS_CHAT_HISTORY_URL = _url("boss_chat_history")
BOSS_SEND_MESSAGE_URL = _url("boss_send_message")
BOSS_SESSION_ENTER_URL = _url("boss_session_enter")

# ── 打招呼 / 新招呼 ────────────────────────────────
BOSS_GREET_LIST_URL = _url("boss_greet_list")
BOSS_GREET_REC_LIST_URL = _url("boss_greet_rec_list")
BOSS_GREET_NEW_LIST_URL = _url("boss_greet_new_list")

# ── 候选人搜索与简历查看 ────────────────────────────
BOSS_SEARCH_GEEK_URL = _url("boss_search_geek")
BOSS_VIEW_GEEK_URL = _url("boss_view_geek")
BOSS_CHAT_GEEK_INFO_URL = _url("boss_chat_geek_info")

# ── 职位管理 ────────────────────────────────────────
BOSS_JOB_LIST_URL = _url("boss_job_list")
BOSS_JOB_OFFLINE_URL = _url("boss_job_offline")
BOSS_JOB_ONLINE_URL = _url("boss_job_online")

# ── 交换联系方式 ────────────────────────────────────
BOSS_EXCHANGE_REQUEST_URL = _url("boss_exchange_request")
BOSS_EXCHANGE_CONTENT_URL = _url("boss_exchange_content")

# ── 面试 ────────────────────────────────────────────
BOSS_INTERVIEW_LIST_URL = _url("boss_interview_list")
BOSS_INTERVIEW_INVITE_URL = _url("boss_interview_invite")
BOSS_INTERVIEW_DETAIL_URL = _url("boss_interview_detail")

# ── 候选人操作 ──────────────────────────────────────
BOSS_MARK_UNSUITABLE_URL = _url("boss_mark_unsuitable")
BOSS_ADD_FRIEND_URL = _url("boss_add_friend")

# ── Response codes ──────────────────────────────────
CODE_SUCCESS = _spec.response_codes.get("success", 0)
CODE_STOKEN_EXPIRED = _spec.response_codes.get("stoken_expired", 37)
CODE_RATE_LIMITED = _spec.response_codes.get("rate_limited", 9)
CODE_ACCOUNT_RISK = _spec.response_codes.get("account_risk", 36)

# ── Headers + Referer ───────────────────────────────
DEFAULT_HEADERS = dict(_spec.default_headers)
REFERER_MAP = {ep.url: ep.referer for ep in _spec.endpoints.values()}
