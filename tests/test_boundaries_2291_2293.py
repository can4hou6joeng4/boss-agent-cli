"""审计批次：索引快照的一次性读取与写入方身份、缓存标识的可编码性。

- Issue #2291：`show` 两次独立读取索引缓存，编号与 `index_snapshot` 可来自不同快照；
- Issue #2292：`search` / `recommend` 保存后回读快照，可能挂上他次搜索的身份；
- Issue #2293：缓存条目的标识含孤立代理字符时，坏值被带进平台请求，并兜成带磁盘指引的 `LOCAL_IO_ERROR`。
"""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli import index_cache
from boss_agent_cli.index_cache import (
	get_index_entry,
	get_index_info,
	get_index_snapshot,
	get_job_by_index,
	save_index,
	try_save_index,
)
from boss_agent_cli.main import cli

_ROW = {
	"security_id": "sid_fixture",
	"job_id": "job_fixture",
	"lid": "lid_fixture",
	"title": "职位",
	"company": "公司",
	"salary": "20K",
	"experience": "3-5年",
	"education": "本科",
	"city": "北京",
}


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	return instance


def _cache_mock(mock_cls):
	instance = _ctx_mock(mock_cls)
	instance.reserve_crawl_budget.return_value = 0.0
	instance.is_greeted.return_value = False
	instance.get_search.return_value = None
	return instance


def _snapshot(source, count, saved_at, security_id):
	return {
		"source": source,
		"page": 1,
		"count": count,
		"saved_at": saved_at,
		"jobs": [
			{"security_id": security_id, "job_id": f"job_{security_id}", "lid": "", "title": source}
		],
	}


# ── #2291：单次读取 ───────────────────────────────────────────────


def test_index_entry_reads_cache_once(tmp_path, monkeypatch):
	first = _snapshot("search:alpha", 1, 111, "sid_a")
	second = _snapshot("search:beta", 1, 222, "sid_b")
	calls = []

	def fake_load(data_dir):
		calls.append(data_dir)
		return first if len(calls) == 1 else second

	monkeypatch.setattr(index_cache, "_load_cache", fake_load)

	info, job = get_index_entry(tmp_path, 1)

	assert len(calls) == 1
	assert info["source"] == "search:alpha"
	assert info["count"] == 1
	assert job["security_id"] == "sid_a"


def test_show_keeps_info_and_job_from_the_same_snapshot(tmp_path, monkeypatch):
	# 第一次读取是旧快照，第二次已被并发搜索覆盖：旧实现会拿旧元信息配新职位。
	first = _snapshot("search:alpha", 1, 111, "sid_a")
	second = _snapshot("search:beta", 1, 222, "sid_b")
	calls = []

	def fake_load(data_dir):
		calls.append(data_dir)
		return first if len(calls) == 1 else second

	monkeypatch.setattr(index_cache, "_load_cache", fake_load)

	with patch("boss_agent_cli.commands.show.AuthManager"), patch(
		"boss_agent_cli.commands.show.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		client.job_card.return_value = {"code": 0, "zpData": {"jobCard": {"encryptJobId": "job_a", "jobName": "职位"}}}
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "show", "1"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert len(calls) == 1
	assert client.job_card.call_args.args[0] == "sid_a"
	assert payload["data"]["index_snapshot"]["source"] == "search:alpha"
	assert payload["data"]["index_snapshot"]["saved_at"] == 111


# ── #2292：写入方身份 ─────────────────────────────────────────────


def test_save_index_returns_the_snapshot_readers_see(tmp_path):
	snapshot = save_index(tmp_path, [dict(_ROW)], source="search:python", page=2)

	assert snapshot == get_index_snapshot(tmp_path)
	assert snapshot["source"] == "search:python"
	assert snapshot["page"] == 2
	assert snapshot["count"] == 1


def test_try_save_index_returns_none_when_write_fails(tmp_path):
	with patch("boss_agent_cli.index_cache.write_json_atomic", side_effect=PermissionError("readonly")):
		assert try_save_index(tmp_path, [dict(_ROW)], source="search") is None


def _search_page():
	return {
		"zpData": {
			"hasMore": False,
			"jobList": [
				{
					"encryptJobId": "job_1",
					"securityId": "sid_1",
					"jobName": "Python 工程师",
					"brandName": "示例",
					"salaryDesc": "20K",
				}
			],
		}
	}


@patch("boss_agent_cli.commands.search.try_save_index")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_search_uses_saved_snapshot_instead_of_rereading(
	mock_auth, mock_client_cls, mock_cache_cls, mock_save, tmp_path
):
	mock_save.return_value = {"source": "search:python", "page": 1, "count": 1, "saved_at": 321}
	client = _ctx_mock(mock_client_cls)
	client.search_jobs.return_value = _search_page()
	_cache_mock(mock_cache_cls)
	with patch("boss_agent_cli.index_cache.get_index_snapshot", side_effect=AssertionError("不应回读快照")):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "python"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["pagination"]["index_snapshot"] == {
		"source": "search:python",
		"page": 1,
		"count": 1,
		"saved_at": 321,
	}


@patch("boss_agent_cli.commands.recommend.try_save_index")
@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.get_platform_instance")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_uses_saved_snapshot_instead_of_rereading(
	mock_auth, mock_client_cls, mock_cache_cls, mock_save, tmp_path
):
	mock_save.return_value = {"source": "recommend", "page": 1, "count": 1, "saved_at": 654}
	client = _ctx_mock(mock_client_cls)
	client.recommend_jobs.return_value = _search_page()
	_cache_mock(mock_cache_cls)
	with patch("boss_agent_cli.index_cache.get_index_snapshot", side_effect=AssertionError("不应回读快照")):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "recommend"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["pagination"]["index_snapshot"] == {
		"source": "recommend",
		"page": 1,
		"count": 1,
		"saved_at": 654,
	}


@patch("boss_agent_cli.commands.search.try_save_index")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_search_omits_snapshot_when_save_is_stubbed_with_a_non_dict(
	mock_auth, mock_client_cls, mock_cache_cls, mock_save, tmp_path
):
	# 既有测试大量用 MagicMock 打桩 try_save_index：非 dict 返回值不能进信封。
	mock_save.return_value = MagicMock()
	client = _ctx_mock(mock_client_cls)
	client.search_jobs.return_value = _search_page()
	_cache_mock(mock_cache_cls)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "python"])

	assert result.exit_code == 0, result.output
	assert "index_snapshot" not in json.loads(result.output)["pagination"]


# ── #2293：缓存标识的可编码性 ─────────────────────────────────────


def test_job_read_sanitizes_unencodable_identifiers(tmp_path):
	save_index(
		tmp_path,
		[{"security_id": "sid\ud800", "job_id": "job\ud800", "lid": "lid\ud800", "title": "职位"}],
	)

	job = get_job_by_index(tmp_path, 1)

	assert job["security_id"] == ""
	assert job["job_id"] == ""
	assert job["lid"] == ""
	assert job["title"] == "职位"


def test_show_rejects_unencodable_security_id_before_request(tmp_path):
	save_index(tmp_path, [{"security_id": "sid\ud800", "job_id": "job_1", "lid": "lid_1"}])

	with patch("boss_agent_cli.commands.show.AuthManager"), patch(
		"boss_agent_cli.commands.show.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "show", "1"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "缺少 security_id" in payload["error"]["message"]
	client.job_card.assert_not_called()


def test_index_info_still_derives_count_from_jobs(tmp_path):
	save_index(tmp_path, [dict(_ROW), {**_ROW, "job_id": "job_2"}], source="search:x", page=None)

	info = get_index_entry(tmp_path, 5)[0]

	assert info["exists"] is True
	assert info["count"] == 2
	assert info["page"] is None
	# 越界与元信息来自同一份快照：count 不会与职位列表自相矛盾。
	assert get_index_entry(tmp_path, 5)[1] is None
	assert get_index_info(tmp_path)["count"] == 2
