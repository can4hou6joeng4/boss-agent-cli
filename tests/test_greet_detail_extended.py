"""greet 和 detail 命令扩展测试 — 覆盖核心求职动作的正常和异常路径。"""

import json
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from boss_agent_cli.main import cli


# ── 辅助 ─────────────────────────────────────────────────────────────

_PATCHES = {
	"auth": "boss_agent_cli.commands.greet.AuthManager",
	"client": "boss_agent_cli.commands.greet.get_platform_instance",
	"platform": "boss_agent_cli.commands.greet.get_platform_instance",
	"cache": "boss_agent_cli.commands.greet.CacheStore",
}


def _invoke_greet(*args, tmp_path, cache_greeted=False, greet_side_effect=None, greet_result=None, is_success=True, parse_error=("UNKNOWN", "")):
	runner = CliRunner()
	with (
		patch(_PATCHES["auth"]),
		patch(_PATCHES["platform"]) as mock_get_platform,
		patch(_PATCHES["cache"]) as mock_cache_cls,
	):
		mock_cache = MagicMock()
		mock_cache.is_greeted.return_value = cache_greeted
		mock_cache_cls.return_value.__enter__ = lambda s: mock_cache
		mock_cache_cls.return_value.__exit__ = MagicMock(return_value=False)

		mock_platform = MagicMock()
		if greet_side_effect:
			mock_platform.greet.side_effect = greet_side_effect
		else:
			mock_platform.greet.return_value = greet_result if greet_result is not None else {"code": 0, "zpData": {}}
		mock_platform.is_success.return_value = is_success
		mock_platform.parse_error.return_value = parse_error
		mock_get_platform.return_value.__enter__ = lambda s: mock_platform
		mock_get_platform.return_value.__exit__ = MagicMock(return_value=False)

		cli_args = ["--data-dir", str(tmp_path), *args]
		result = runner.invoke(cli, cli_args)
	parsed = json.loads(result.output) if result.output.strip() else None
	return result.exit_code, parsed, mock_cache, mock_platform


# ── greet 命令 ──────────────────────────────────────────────────────


def test_greet_success(tmp_path):
	"""打招呼成功应返回成功信封并记录。"""
	code, parsed, mock_cache, mock_client = _invoke_greet(
		"greet", "sid1", "jid1", tmp_path=tmp_path,
	)
	assert code == 0
	assert parsed["ok"] is True
	assert parsed["data"]["security_id"] == "sid1"
	mock_client.greet.assert_called_once_with("sid1", "jid1", "")
	mock_cache.record_greet.assert_called_once_with("sid1", "jid1")


def test_greet_failure_does_not_record_cache(tmp_path):
	"""业务失败码不应误记成功。"""
	code, parsed, mock_cache, mock_client = _invoke_greet(
		"greet", "sid1", "jid1", tmp_path=tmp_path,
		greet_result={"code": 401, "message": "unauthorized"},
		is_success=False,
		parse_error=("AUTH_EXPIRED", "unauthorized"),
	)
	assert code == 1
	assert parsed["error"]["code"] == "AUTH_EXPIRED"
	mock_cache.record_greet.assert_not_called()


def test_greet_already_greeted(tmp_path):
	"""已打招呼应返回错误码。"""
	code, parsed, _, _ = _invoke_greet(
		"greet", "sid1", "jid1", tmp_path=tmp_path, cache_greeted=True,
	)
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "ALREADY_GREETED"


def test_greet_with_custom_message(tmp_path):
	"""自定义消息应传递给客户端。"""
	code, parsed, _, mock_client = _invoke_greet(
		"greet", "sid1", "jid1", "--message", "你好", tmp_path=tmp_path,
	)
	assert code == 0
	mock_client.greet.assert_called_once_with("sid1", "jid1", "你好")


def test_greet_records_to_cache(tmp_path):
	"""成功打招呼后应记录到缓存。"""
	_, _, mock_cache, _ = _invoke_greet(
		"greet", "sid1", "jid1", tmp_path=tmp_path,
	)
	mock_cache.record_greet.assert_called_once()


def test_greet_hints_present(tmp_path):
	"""成功后应包含下一步建议。"""
	code, parsed, _, _ = _invoke_greet(
		"greet", "sid1", "jid1", tmp_path=tmp_path,
	)
	assert "hints" in parsed
	assert len(parsed["hints"]["next_actions"]) > 0


# ── batch-greet 命令 ────────────────────────────────────────────────


def _invoke_batch_greet(*args, tmp_path, search_result=None, greeted_ids=None, is_success=True, parse_error=("UNKNOWN", "")):
	runner = CliRunner()
	greeted = set(greeted_ids or [])
	with (
		patch(_PATCHES["auth"]),
		patch(_PATCHES["client"]) as mock_client_cls,
		patch(_PATCHES["cache"]) as mock_cache_cls,
	):
		mock_cache = MagicMock()
		mock_cache.is_greeted.side_effect = lambda sid: sid in greeted
		mock_cache_cls.return_value.__enter__ = lambda s: mock_cache
		mock_cache_cls.return_value.__exit__ = MagicMock(return_value=False)

		mock_client = MagicMock()
		mock_client.search_jobs.return_value = search_result or {
			"zpData": {"jobList": [
				{"encryptJobId": "j1", "securityId": "s1", "jobName": "前端", "brandName": "字节",
				 "salaryDesc": "20K", "cityName": "北京", "jobExperience": "3年",
				 "jobDegree": "本科", "bossName": "张经理", "bossTitle": "技术总监",
				 "activeTimeDesc": "刚刚活跃"},
				{"encryptJobId": "j2", "securityId": "s2", "jobName": "后端", "brandName": "阿里",
				 "salaryDesc": "30K", "cityName": "杭州", "jobExperience": "5年",
				 "jobDegree": "本科", "bossName": "李总", "bossTitle": "架构师",
				 "activeTimeDesc": "今日活跃"},
			]},
		}
		mock_client.is_success.return_value = is_success
		mock_client.parse_error.return_value = parse_error
		mock_client.unwrap_data.side_effect = lambda payload: payload.get("zpData") if "zpData" in payload else payload.get("data")
		mock_client_cls.return_value.__enter__ = lambda s: mock_client
		mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

		cli_args = ["--data-dir", str(tmp_path), *args]
		result = runner.invoke(cli, cli_args)
	parsed = json.loads(result.output) if result.output.strip() else None
	return result.exit_code, parsed


def test_batch_greet_dry_run(tmp_path):
	"""预览模式应列出候选但不实际打招呼。"""
	code, parsed = _invoke_batch_greet(
		"batch-greet", "python", "--dry-run", tmp_path=tmp_path,
	)
	assert code == 0
	assert parsed["data"]["dry_run"] is True
	assert parsed["data"]["count"] == 2


def test_batch_greet_skips_greeted(tmp_path):
	"""已打招呼的应被跳过。"""
	code, parsed = _invoke_batch_greet(
		"batch-greet", "python", "--dry-run", tmp_path=tmp_path,
		greeted_ids=["s1"],
	)
	assert parsed["data"]["count"] == 1


def test_batch_greet_respects_count_limit(tmp_path):
	"""应尊重数量上限。"""
	code, parsed = _invoke_batch_greet(
		"batch-greet", "python", "--dry-run", "--count", "1", tmp_path=tmp_path,
	)
	assert parsed["data"]["count"] == 1


def test_batch_greet_reports_platform_error(tmp_path):
	"""搜索接口明确失败时，不应伪装成空候选成功。"""
	code, parsed = _invoke_batch_greet(
		"batch-greet", "python", "--dry-run", tmp_path=tmp_path,
		search_result={"code": 500, "message": "service unavailable"},
		is_success=False,
		parse_error=("UPSTREAM_ERROR", "service unavailable"),
	)
	assert code == 1
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "UPSTREAM_ERROR"


# ── detail 命令 ─────────────────────────────────────────────────────

_DETAIL_PATCHES = {
	"auth": "boss_agent_cli.commands.detail.AuthManager",
	"client": "boss_agent_cli.commands.detail.get_platform_instance",
	"cache": "boss_agent_cli.commands.detail.CacheStore",
}

_DETAIL_RESULT = {
	"zpData": {
		"jobInfo": {
			"encryptJobId": "j1", "jobName": "前端工程师",
			"postDescription": "负责前端开发",
			"salaryDesc": "20-30K", "cityName": "北京",
			"experienceName": "3-5年", "degreeName": "本科",
			"address": "朝阳区望京", "jobLabels": ["前端"],
		},
		"jobDetail": "详细职位描述",
		"bossInfo": {
			"name": "张经理", "title": "技术总监",
			"activeTimeDesc": "刚刚活跃",
		},
		"brandComInfo": {
			"brandName": "字节跳动", "industryName": "互联网",
			"scaleName": "10000人以上", "stageName": "已上市",
		},
	},
}


def _invoke_detail(*args, tmp_path, detail_result=None, cache_job_id=None):
	runner = CliRunner()
	with (
		patch(_DETAIL_PATCHES["auth"]),
		patch(_DETAIL_PATCHES["client"]) as mock_client_cls,
		patch(_DETAIL_PATCHES["cache"]) as mock_cache_cls,
	):
		mock_cache = MagicMock()
		mock_cache.get_job_id.return_value = cache_job_id
		mock_cache.is_greeted.return_value = False
		mock_cache_cls.return_value.__enter__ = lambda s: mock_cache
		mock_cache_cls.return_value.__exit__ = MagicMock(return_value=False)

		mock_client = MagicMock()
		mock_client.job_detail.return_value = detail_result or _DETAIL_RESULT
		mock_client.job_card.return_value = {"zpData": {"jobCard": {
			"encryptJobId": "j1", "jobName": "前端工程师",
			"postDescription": "负责前端开发",
			"salaryDesc": "20-30K", "cityName": "北京",
			"experienceName": "3-5年", "degreeName": "本科",
			"address": "朝阳区望京", "jobLabels": ["前端"],
			"brandName": "字节跳动", "bossName": "张经理",
			"bossTitle": "技术总监", "activeTimeDesc": "刚刚活跃",
		}}}
		def unwrap_data_side_effect(payload):
			return payload.get("zpData")
		mock_client.unwrap_data.side_effect = unwrap_data_side_effect
		mock_client_cls.return_value.__enter__ = lambda s: mock_client
		mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

		cli_args = ["--data-dir", str(tmp_path), *args]
		result = runner.invoke(cli, cli_args)
	parsed = json.loads(result.output) if result.output.strip() else None
	return result.exit_code, parsed


def test_detail_basic(tmp_path):
	"""查看职位详情应返回完整信息。"""
	code, parsed = _invoke_detail("detail", "sid1", tmp_path=tmp_path)
	assert code == 0
	assert parsed["ok"] is True


def test_detail_with_job_id(tmp_path):
	"""传入职位编号应走快速通道。"""
	code, parsed = _invoke_detail("detail", "sid1", "--job-id", "j1", tmp_path=tmp_path)
	assert code == 0
	assert parsed["ok"] is True


def test_detail_json_envelope(tmp_path):
	"""输出应符合信封格式。"""
	code, parsed = _invoke_detail("detail", "sid1", tmp_path=tmp_path)
	assert "ok" in parsed
	assert "command" in parsed
	assert "data" in parsed
	assert parsed["command"] == "detail"
