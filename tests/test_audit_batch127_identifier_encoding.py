"""审计批次 127：缓存层标识与平台文本的可逆绑定（Issue #2509）。

孤立代理项（平台 JSON 的 ``"\\udcff"``、argv 的非法字节）无法按 UTF-8 编码，
``sqlite3`` 绑定 TEXT 参数时抛 ``UnicodeEncodeError``，被全局兜成带磁盘指引的
``LOCAL_IO_ERROR``：

- 读路径（``is_greeted`` / ``is_applied`` / ``get_job_id``）：一张漂移卡片会让
  ``search`` / ``recommend`` 整条命令失败；
- 写路径（招呼 / 投递 / 招聘者招呼记录）：平台已确认发送后写去重记录失败；
- ``put_job_desc``：整条 ``--welfare`` 管线失败。

标识按可逆方式绑定：可编码保持 TEXT，不可编码绑定 ``surrogatepass`` BLOB；
转义成文本会与合法字面标识碰撞，这里逐项断言不会互相命中。
"""

from __future__ import annotations

from pathlib import Path

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.search import _refresh_greeted

_BAD = "\udcff"
_BAD_LITERAL = "\\udcff"


def _cache(tmp_path: Path) -> CacheStore:
	return CacheStore(tmp_path / "cache" / "boss_agent.db")


def test_greet_and_apply_lookups_tolerate_unencodable_identifiers(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		assert cache.is_greeted("sec", job_id=f"job{_BAD}", account="acct") is False
		assert cache.is_greeted(f"sec{_BAD}") is False
		assert cache.is_applied("sec", f"job{_BAD}", account="acct") is False
		assert cache.is_applied(f"sec{_BAD}", "job-1") is False
		assert cache.get_job_id(f"sec{_BAD}") is None
		assert cache.is_shortlisted(f"sec{_BAD}", f"job{_BAD}") is False


def test_record_greet_round_trips_unencodable_identifiers(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		cache.record_greet(f"sec{_BAD}", f"job{_BAD}", account="acct")

		assert cache.is_greeted(f"sec{_BAD}", job_id=f"job{_BAD}", account="acct") is True
		assert cache.is_greeted(f"sec{_BAD}") is True
		assert cache.get_job_id(f"sec{_BAD}") == f"job{_BAD}"
		# 转义后的字面标识不得命中这条记录（否则会误报「已招呼」）。
		assert cache.is_greeted(f"sec{_BAD_LITERAL}", job_id=f"job{_BAD_LITERAL}", account="acct") is False
		assert cache.get_job_id(f"sec{_BAD_LITERAL}") is None


def test_record_apply_round_trips_unencodable_identifiers(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		cache.record_apply(f"sec{_BAD}", f"job{_BAD}", account="acct")

		assert cache.is_applied(f"sec{_BAD}", f"job{_BAD}", account="acct") is True
		assert cache.is_applied(f"sec{_BAD}", f"job{_BAD}") is True
		assert cache.is_applied(f"sec{_BAD_LITERAL}", f"job{_BAD_LITERAL}", account="acct") is False


def test_candidate_action_claims_accept_unencodable_identifiers(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		assert cache.claim_candidate_action(f"sec{_BAD}", f"job{_BAD}", "greet", account="acct") is None
		assert cache.claim_candidate_action(f"sec{_BAD}", f"job{_BAD}", "greet", account="acct") == "pending"
		cache.mark_candidate_action_sent(f"sec{_BAD}", f"job{_BAD}", "greet", account="acct")
		assert cache.claim_candidate_action(f"sec{_BAD}", f"job{_BAD}", "greet", account="acct") == "sent"
		assert cache.release_candidate_action(f"job{_BAD}", "greet", account="acct") is False
		# 字面标识是另一把键，不继承 BLOB 预约。
		assert cache.claim_candidate_action(f"sec{_BAD_LITERAL}", f"job{_BAD_LITERAL}", "greet", account="acct") is None


def test_recruiter_greet_records_accept_unencodable_identifiers(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		assert cache.claim_recruiter_greet(f"geek{_BAD}", f"job{_BAD}") is None
		assert cache.claim_recruiter_greet(f"geek{_BAD}", f"job{_BAD}") == "pending"
		cache.record_recruiter_greet(f"geek{_BAD}", f"job{_BAD}")
		assert cache.claim_recruiter_greet(f"geek{_BAD}", f"job{_BAD}") == "sent"
		assert cache.release_recruiter_greet(f"geek{_BAD}", f"job{_BAD}") is False
		assert cache.claim_recruiter_greet(f"geek{_BAD_LITERAL}", f"job{_BAD_LITERAL}") is None


def test_job_desc_cache_accepts_unencodable_identifiers_and_text(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		cache.put_job_desc(f"job{_BAD}", f"desc{_BAD}")
		assert cache.get_job_desc(f"job{_BAD}") == f"desc{_BAD_LITERAL}"
		assert cache.get_job_desc(f"job{_BAD_LITERAL}") is None

		cache.put_job_desc("job-1", f"desc{_BAD}")
		assert cache.get_job_desc("job-1") == f"desc{_BAD_LITERAL}"


def test_location_record_accepts_unencodable_job_id(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		assert cache.claim_location_send(1) is None
		cache.record_location_send(1, job_id=f"job{_BAD}")
		assert cache.location_send_status(1) == "sent"
		assert cache.release_location_send(1) is False


def test_search_greeted_refresh_survives_unencodable_card(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		items = [{"security_id": f"sec{_BAD}", "job_id": f"job{_BAD}"}]
		assert _refresh_greeted(items, cache, "acct")[0]["greeted"] is False

		cache.record_greet(f"sec{_BAD}", f"job{_BAD}", account="acct")
		assert _refresh_greeted(items, cache, "acct")[0]["greeted"] is True


def test_normal_identifiers_stay_text_and_round_trip(tmp_path: Path) -> None:
	with _cache(tmp_path) as cache:
		cache.record_greet("sec-1", "job-1", account="acct")
		assert cache.is_greeted("sec-1", job_id="job-1", account="acct") is True
		assert cache.get_job_id("sec-1") == "job-1"
		storage = cache._conn.execute("SELECT typeof(job_id), typeof(security_id) FROM greet_records").fetchone()
		assert storage == ("text", "text")

		assert cache.claim_recruiter_greet("geek-1", "job-1") is None
		cache.record_recruiter_greet("geek-1", "job-1")
		assert cache.claim_recruiter_greet("geek-1", "job-1") == "sent"

		cache.put_job_desc("job-1", "普通描述")
		assert cache.get_job_desc("job-1") == "普通描述"
