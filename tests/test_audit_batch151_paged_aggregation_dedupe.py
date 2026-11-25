"""多页聚合按条目去重（Issue #2753）。

沟通列表与职位收藏都按页码取数、聚合后交给下游统计。整页签名只能挡住「整页
重复」；翻页期间列表前移（有新会话 / 新收藏顶到最前）时，上一页末尾的条目会在
下一页开头再出现一次，两页只重叠一部分：

- 沟通列表：``pipeline`` / ``follow-up`` / ``digest`` 与向导同名步骤把同一联系人
  算两次。行里不带 uid，securityId 又每次请求轮换，调用方无法自行去重；
- 职位收藏：``favorites sync`` 的 ``existing_count`` / ``active_count`` 虚高，
  与实际落库的职位数对不上。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.favorites import FavoritesPageLimitExceeded, collect_favorites_items
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.friend_list_pages import collect_friend_list_items, collect_friend_list_page
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError

NOW_MS = 1_780_000_000_000

# 第 2 页开头重复第 1 页末尾：翻页期间有新会话 / 新收藏顶到最前，列表整体后移一位。
_OVERLAPPING_PAGES: dict[int, list[Any]] = {1: [1, 2, 3], 2: [3, 4, 5], 3: [6, 7, 9]}
_UNIQUE_IDS = [1, 2, 3, 4, 5, 6, 7, 9]


class _PagedPlatform:
	"""按页码返回固定内容的假平台；失败页返回限流响应。"""

	def __init__(
		self,
		pages: dict[int, list[Any]],
		*,
		error_page: int | None = None,
		endless: bool = False,
	) -> None:
		self.pages = pages
		self.error_page = error_page
		self.endless = endless
		self.calls: list[int] = []

	def __enter__(self) -> _PagedPlatform:
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def _has_more(self, page: int) -> bool:
		return self.endless or page < max(self.pages)

	def _failed(self, page: int) -> bool:
		return page == self.error_page

	@staticmethod
	def is_success(response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	@staticmethod
	def unwrap_data(response: dict[str, Any]) -> Any:
		return response.get("zpData")

	@staticmethod
	def parse_error(response: dict[str, Any]) -> tuple[str, str]:
		return "RATE_LIMITED", "too fast"


# ── 沟通列表 ──────────────────────────────────────────────────────────


def _contact(uid: Any, request_no: int) -> dict[str, Any]:
	"""同一联系人在每次请求里拿到不同的 securityId（与真实平台一致）。"""
	return {
		"uid": uid,
		"securityId": f"sid_{uid}_req{request_no}",
		"name": f"联系人{uid}",
		"brandName": f"公司{uid}",
		"title": "招聘经理",
		"relationType": 1,
		"unreadMsgCount": 1,
		"lastMsg": "您好",
		"lastTS": NOW_MS - 3_600_000,
		"encryptJobId": f"job_{uid}",
	}


class _FriendPlatform(_PagedPlatform):
	"""页面元素是 uid 时生成联系人，是 dict 时原样返回（用于构造漂移条目）。"""

	def friend_list(self, *, page: int = 1) -> dict[str, Any]:
		self.calls.append(page)
		if self._failed(page):
			return {"code": 9, "message": "too fast"}
		request_no = len(self.calls)
		items = [
			dict(entry) if isinstance(entry, dict) else _contact(entry, request_no)
			for entry in self.pages.get(page, [])
		]
		return {"code": 0, "zpData": {"result": items, "hasMore": self._has_more(page)}}

	def interview_data(self) -> dict[str, Any]:
		return {"code": 0, "zpData": {"interviewList": []}}


def test_overlapping_pages_keep_each_contact_once_in_original_order() -> None:
	platform = _FriendPlatform(_OVERLAPPING_PAGES)

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert platform.calls == [1, 2, 3]
	assert (pages_scanned, truncated) == (3, False)
	assert [item["uid"] for item in items] == _UNIQUE_IDS
	# 保留首条：第 2 页重复出现的 uid=3 不覆盖第 1 页拿到的那一条。
	assert [item["securityId"] for item in items if item["uid"] == 3] == ["sid_3_req1"]


def test_collect_friend_list_items_wrapper_is_deduplicated_too() -> None:
	items, error = collect_friend_list_items(_FriendPlatform(_OVERLAPPING_PAGES))

	assert error is None
	assert [item["uid"] for item in items] == _UNIQUE_IDS


def test_duplicate_contact_within_one_page_is_collapsed() -> None:
	items, error = collect_friend_list_items(_FriendPlatform({1: [1, 1, 2]}))

	assert error is None
	assert [item["uid"] for item in items] == [1, 2]


@pytest.mark.parametrize("second_form", ["10001", " 10001 "], ids=["string", "padded-string"])
def test_integer_and_string_uid_are_the_same_contact(second_form: str) -> None:
	"""真实平台给整数 uid，历史形态是字符串；两者是同一联系人（stable_uid 口径）。"""
	platform = _FriendPlatform({
		1: [10001, 10002],
		2: [{**_contact(10001, 2), "uid": second_form}, 10003],
	})

	items, error = collect_friend_list_items(platform)

	assert error is None
	assert [item["uid"] for item in items] == [10001, 10002, 10003]


@pytest.mark.parametrize(
	("first_job", "second_job"),
	[("job_a", "job_b"), ("job_a", None), (None, "job_b")],
	ids=["different-jobs", "second-without-job", "first-without-job"],
)
def test_same_contact_under_another_job_is_not_merged(first_job: Any, second_job: Any) -> None:
	"""去重会丢行，所以只合并能证明是同一条目的重复：职位对不上就两条都留。"""
	platform = _FriendPlatform({
		1: [{**_contact(1, 1), "encryptJobId": first_job}],
		2: [{**_contact(1, 2), "encryptJobId": second_job}, 2],
	})

	items, error = collect_friend_list_items(platform)

	assert error is None
	assert [(item["uid"], item["encryptJobId"]) for item in items] == [(1, first_job), (1, second_job), (2, "job_2")]


@pytest.mark.parametrize(
	("first_job", "second_job"),
	[(None, None), (5, {"id": 1}), ("", "   "), ("job_1", " job_1 ")],
	ids=["both-null", "both-drifted", "both-blank", "padded"],
)
def test_duplicate_contact_is_collapsed_when_job_ids_do_not_disagree(first_job: Any, second_job: Any) -> None:
	"""两份都没有可用职位 ID（或只差首尾空白）时按 uid 判定，仍然只留首条。"""
	platform = _FriendPlatform({
		1: [{**_contact(1, 1), "encryptJobId": first_job}],
		2: [{**_contact(1, 2), "encryptJobId": second_job}, 2],
	})

	items, error = collect_friend_list_items(platform)

	assert error is None
	assert [item["securityId"] for item in items] == ["sid_1_req1", "sid_2_req2"]


@pytest.mark.parametrize(
	"drifted_uid",
	[None, True, False, {"id": 1}, [1], 1.5, "", "   "],
	ids=["null", "true", "false", "dict", "list", "float", "empty", "blank"],
)
def test_contacts_without_usable_uid_are_all_kept(drifted_uid: Any) -> None:
	"""uid 不可用时无法证明两条是同一联系人：照旧全部保留（Issue #2063 的契约）。"""
	platform = _FriendPlatform({
		1: [{"uid": drifted_uid, "securityId": "sid_a", "name": "联系人甲"}, 1],
		2: [{"uid": drifted_uid, "securityId": "sid_b", "name": "联系人乙"}, 2],
	})

	items, error = collect_friend_list_items(platform)

	assert error is None
	assert [item["name"] for item in items] == ["联系人甲", "联系人1", "联系人乙", "联系人2"]


def test_contact_missing_uid_key_is_kept_on_every_page() -> None:
	platform = _FriendPlatform({
		1: [{"securityId": "sid_a", "name": "联系人甲"}],
		2: [{"securityId": "sid_b", "name": "联系人甲"}],
	})

	items, error = collect_friend_list_items(platform)

	assert error is None
	assert [item["securityId"] for item in items] == ["sid_a", "sid_b"]


def test_identical_page_still_ends_the_scan() -> None:
	"""整页重复的早停不变：平台忽略页码时不能把同一页抓满上限。"""
	platform = _FriendPlatform({1: [1, 2], 2: [1, 2], 3: [3]})

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert platform.calls == [1, 2]
	assert (pages_scanned, truncated) == (2, False)
	assert [item["uid"] for item in items] == [1, 2]


def test_page_of_known_contacts_in_new_order_does_not_end_the_scan() -> None:
	"""去重不是终止条件：整页都是已见过的联系人，只说明列表后移了不止一页。"""
	platform = _FriendPlatform({1: [1, 2, 3], 2: [3, 2], 3: [4]})

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert platform.calls == [1, 2, 3]
	assert (pages_scanned, truncated) == (3, False)
	assert [item["uid"] for item in items] == [1, 2, 3, 4]


def test_truncation_is_still_reported_after_deduplication() -> None:
	platform = _FriendPlatform(_OVERLAPPING_PAGES, endless=True)

	items, error, pages_scanned, truncated = collect_friend_list_page(platform, max_pages=2)

	assert error is None
	assert (pages_scanned, truncated) == (2, True)
	assert [item["uid"] for item in items] == [1, 2, 3, 4, 5]


def test_failed_page_after_overlap_discards_the_partial_result() -> None:
	"""错误路径不变：任一页失败时返回空列表与原始响应，不泄漏去重后的前缀。"""
	platform = _FriendPlatform({1: [1, 2, 3], 2: [3, 4, 5], 3: [6]}, error_page=3)

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert items == []
	assert error == {"code": 9, "message": "too fast"}
	assert (pages_scanned, truncated) == (3, False)


def _invoke(module: str, argv: list[str], data_dir: Path, platform: _PagedPlatform) -> Any:
	with patch(f"boss_agent_cli.commands.{module}.AuthManager"), patch(
		f"boss_agent_cli.commands.{module}.get_platform_instance", return_value=platform
	):
		return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *argv])


@pytest.mark.parametrize("command", ["pipeline", "follow-up"])
def test_pipeline_commands_count_each_contact_once(command: str, tmp_path: Path) -> None:
	platform = _FriendPlatform(_OVERLAPPING_PAGES)

	result = _invoke("pipeline", [command, "--now-ts-ms", str(NOW_MS)], tmp_path, platform)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert sorted(row["company"] for row in envelope["data"]) == [f"公司{uid}" for uid in _UNIQUE_IDS]
	assert envelope["pagination"]["total"] == len(_UNIQUE_IDS)
	assert envelope["pagination"]["pages_scanned"] == 3
	assert envelope["pagination"]["truncated"] is False


def test_digest_counts_each_contact_once(tmp_path: Path) -> None:
	platform = _FriendPlatform(_OVERLAPPING_PAGES)

	result = _invoke("digest", ["digest", "--now-ts-ms", str(NOW_MS)], tmp_path, platform)

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert data["new_match_count"] == len(_UNIQUE_IDS)
	assert data["follow_up_count"] == len(_UNIQUE_IDS)
	assert sorted(row["company"] for row in data["new_matches"]) == [f"公司{uid}" for uid in _UNIQUE_IDS]
	assert data["summary"] == "8 new matches, 8 follow-ups, 0 interviews"
	assert (data["chat_pages_scanned"], data["truncated"]) == (3, False)


@pytest.mark.parametrize(
	("module", "command"),
	[("pipeline", "pipeline"), ("pipeline", "follow-up"), ("digest", "digest")],
)
def test_commands_report_failed_page_after_overlap(module: str, command: str, tmp_path: Path) -> None:
	platform = _FriendPlatform({1: [1, 2, 3], 2: [3, 4, 5], 3: [6]}, error_page=3)

	result = _invoke(module, [command, "--now-ts-ms", str(NOW_MS)], tmp_path, platform)

	assert result.exit_code == 1
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	assert envelope["data"] is None
	assert envelope["error"]["code"] == "RATE_LIMITED"
	assert envelope["error"]["recoverable"] is True


def _wizard_context(tmp_path: Path, platform: _PagedPlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda name, auth, delay, cdp_url, browser_source=None: platform,
	)


def test_wizard_pipeline_counts_each_contact_once(tmp_path: Path) -> None:
	"""向导步骤与顶层命令共用同一聚合，修复必须同时覆盖两条入口。"""
	platform = _FriendPlatform(_OVERLAPPING_PAGES)

	result = DEFAULT_ACTIONS["candidate_pipeline"](_wizard_context(tmp_path, platform), {"now_ts_ms": NOW_MS}, {})

	assert result.data["total"] == len(_UNIQUE_IDS)
	assert sorted(row["company"] for row in result.data["items"]) == [f"公司{uid}" for uid in _UNIQUE_IDS]
	assert (result.data["chat_pages_scanned"], result.data["truncated"]) == (3, False)


def test_wizard_digest_counts_each_contact_once(tmp_path: Path) -> None:
	platform = _FriendPlatform(_OVERLAPPING_PAGES)

	result = DEFAULT_ACTIONS["candidate_digest"](_wizard_context(tmp_path, platform), {"now_ts_ms": NOW_MS}, {})

	assert result.data["new_match_count"] == len(_UNIQUE_IDS)
	assert result.data["follow_up_count"] == len(_UNIQUE_IDS)
	assert (result.data["chat_pages_scanned"], result.data["truncated"]) == (3, False)


@pytest.mark.parametrize("action_name", ["candidate_pipeline", "candidate_digest"])
def test_wizard_actions_report_failed_page_after_overlap(action_name: str, tmp_path: Path) -> None:
	platform = _FriendPlatform({1: [1, 2, 3], 2: [3, 4, 5], 3: [6]}, error_page=3)

	with pytest.raises(WorkflowActionError) as raised:
		DEFAULT_ACTIONS[action_name](_wizard_context(tmp_path, platform), {"now_ts_ms": NOW_MS}, {})

	assert raised.value.code == "RATE_LIMITED"
	assert raised.value.recoverable is True


# ── 职位收藏 ──────────────────────────────────────────────────────────


def _card(job: Any, request_no: int, *, status: int = 1) -> dict[str, Any]:
	return {
		"encryptJobId": f"job_{job}",
		"securityId": f"sid_{job}_req{request_no}",
		"jobName": f"职位{job}",
		"brandName": f"公司{job}",
		"cityName": "某市",
		"jobSalary": "20-30K",
		"jobLabels": ["双休"],
		"jobValidStatus": status,
	}


class _FavoritesPlatform(_PagedPlatform):
	"""页面元素是编号时生成有效卡片，是 dict 时原样返回。"""

	def job_favorites(self, *, page: int = 1, tag: int | None = None, is_active: bool = True) -> dict[str, Any]:
		self.calls.append(page)
		if self._failed(page):
			return {"code": 9, "message": "too fast"}
		request_no = len(self.calls)
		cards = [
			dict(entry) if isinstance(entry, dict) else _card(entry, request_no)
			for entry in self.pages.get(page, [])
		]
		return {"code": 0, "zpData": {"cardList": cards, "hasMore": self._has_more(page)}}


def _shortlist_rows(data_dir: Path) -> list[dict[str, Any]]:
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", "shortlist", "list"])
	assert result.exit_code == 0, result.output
	rows: list[dict[str, Any]] = json.loads(result.stdout)["data"]
	return rows


def test_overlapping_favorites_pages_keep_each_job_once() -> None:
	platform = _FavoritesPlatform(_OVERLAPPING_PAGES)

	cards, error = collect_favorites_items(platform)

	assert error is None
	assert platform.calls == [1, 2, 3]
	assert [card["encryptJobId"] for card in cards] == [f"job_{job}" for job in _UNIQUE_IDS]
	assert [card["securityId"] for card in cards if card["encryptJobId"] == "job_3"] == ["sid_3_req1"]


def test_duplicate_favorite_within_one_page_is_collapsed() -> None:
	cards, error = collect_favorites_items(_FavoritesPlatform({1: [1, 1, 2]}))

	assert error is None
	assert [card["encryptJobId"] for card in cards] == ["job_1", "job_2"]


@pytest.mark.parametrize(
	"drifted_job_id",
	[None, 5, True, {"id": 1}, "", "   "],
	ids=["null", "int", "bool", "dict", "empty", "blank"],
)
def test_favorites_without_usable_job_id_are_all_kept(drifted_job_id: Any) -> None:
	"""没有可用职位 ID 的卡片无法判断是否重复：照旧保留，由入库层按缺主键跳过。"""
	platform = _FavoritesPlatform({
		1: [{**_card("x", 1), "encryptJobId": drifted_job_id}, 1],
		2: [{**_card("x", 2), "encryptJobId": drifted_job_id}, 2],
	})

	cards, error = collect_favorites_items(platform)

	assert error is None
	assert [card["securityId"] for card in cards] == ["sid_x_req1", "sid_1_req1", "sid_x_req2", "sid_2_req2"]


def test_favorites_sync_counts_each_job_once(tmp_path: Path) -> None:
	platform = _FavoritesPlatform({1: [1, 2, 3], 2: [3, 4, 5]})

	result = _invoke("favorites", ["favorites", "sync"], tmp_path, platform)

	assert result.exit_code == 0, result.output
	# 此前同一批数据报 existing_count=1 / active_count=6：计数之和比收藏多一条。
	assert json.loads(result.stdout)["data"] == {
		"imported_count": 5,
		"existing_count": 0,
		"skipped_count": 0,
		"active_count": 5,
		"inactive_count": 0,
		"unknown_count": 0,
	}
	assert sorted(row["job_id"] for row in _shortlist_rows(tmp_path)) == [f"job_{job}" for job in (1, 2, 3, 4, 5)]


def test_favorites_sync_status_counts_follow_deduplicated_cards(tmp_path: Path) -> None:
	"""重复出现的失效职位只计一次失效，不抬高 inactive_count。"""
	platform = _FavoritesPlatform({
		1: [1, _card(2, 1, status=2)],
		2: [_card(2, 2, status=2), 3],
	})

	result = _invoke("favorites", ["favorites", "sync"], tmp_path, platform)

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert (data["imported_count"], data["existing_count"]) == (2, 0)
	assert (data["active_count"], data["inactive_count"], data["unknown_count"]) == (2, 1, 0)


def test_favorites_sync_failed_page_after_overlap_persists_nothing(tmp_path: Path) -> None:
	platform = _FavoritesPlatform({1: [1, 2, 3], 2: [3, 4, 5], 3: [6]}, error_page=3)

	result = _invoke("favorites", ["favorites", "sync"], tmp_path, platform)

	assert result.exit_code == 1
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "RATE_LIMITED"
	assert _shortlist_rows(tmp_path) == []


def test_favorites_page_limit_still_raises_when_pages_only_repeat_known_jobs() -> None:
	"""去重不是终止条件：页数上限耗尽仍然报错，不把不完整结果当成全量。"""
	platform = _FavoritesPlatform({1: [1, 2, 3], 2: [3, 2], 3: [2, 1]}, endless=True)

	with pytest.raises(FavoritesPageLimitExceeded):
		collect_favorites_items(platform, max_pages=3)

	assert platform.calls == [1, 2, 3]


def test_store_still_tolerates_duplicate_job_in_one_batch(tmp_path: Path) -> None:
	"""入库层的防护不随命令层去重而失效：同一批里重复的职位不撞主键、不重复插入。"""
	item = {"security_id": "sid_1", "job_id": "job_1", "title": "职位1", "source": "favorites"}

	with CacheStore(tmp_path / "boss_agent.db") as cache:
		result = cache.add_shortlist_batch([dict(item), dict(item)], source="favorites")
		rows = cache.list_shortlist()

	assert result == {"imported_count": 1, "existing_count": 1, "skipped_count": 0}
	assert [row["job_id"] for row in rows] == ["job_1"]
