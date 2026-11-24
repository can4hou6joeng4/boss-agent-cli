"""批次 149 审计修复回归（Issue #2566–#2569）。

覆盖四条边界：
- 风险锁 `persisted` 必须任一处落盘即为真（主锁 / 组目录两向）；
- 读请求确定性 4xx 不得落成可重试的 `NETWORK_ERROR`；
- 导出层只把真的无法转字符串的整数归 null；
- 简历目录不可读时列表必须上抛，不能静默报告为空。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api._base_client import (
	PlatformStatusRejected,
	_BaseHttpClient,
	parameter_status_code,
)
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.display import handle_auth_errors
from boss_agent_cli.main import cli
from boss_agent_cli.resume.models import dict_to_resume
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.services.json_files import strict_json_text

_CANNOT_MAKE_UNREADABLE = os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0

_READ_URL = "https://www.zhipin.com/wapi/zpgeek/pc/recommend/job/list.json"
_WRITE_URL = "https://www.zhipin.com/wapi/zpgeek/friend/add.json"


class _StubAuth:
	def __init__(self, data_dir: Path) -> None:
		self.data_dir = data_dir
		self._platform = "zhipin"

	def get_token(self) -> dict[str, object]:
		return {"cookies": {}, "stoken": ""}


class _ProbeClient(_BaseHttpClient):
	"""只装配 `_request` / 风险锁记录需要的属性（不发真实请求）。"""

	_BASE_URL = "https://www.zhipin.com"
	_DEFAULT_HEADERS: dict[str, str] = {}
	_REFERER_MAP: dict[str, str] = {}
	_CODE_STOKEN_EXPIRED = 7

	def __init__(self, data_dir: Path, handler=None) -> None:
		self._auth = _StubAuth(data_dir)
		self._cdp_url = None
		self._browser_source = None
		self._request_budget = None
		self._unpersisted_risk_lock = None
		self._throttle = SimpleNamespace(wait=lambda: None, mark=lambda: None)
		self._client = httpx.Client(
			base_url="https://www.zhipin.com",
			transport=httpx.MockTransport(handler or (lambda request: httpx.Response(200, json={"code": 0}))),
			cookies={},
			headers={},
		)


# ── 1. 风险锁 persisted（#2566）────────────────────────────────────────


def _record_error(client: _ProbeClient, budget=None) -> PlatformRiskError:
	client._request_budget = budget
	error = PlatformRiskError("ACCOUNT_RISK", details={"platform_code": 36})
	client._record_cdp_risk_lock(None, error=error)
	return error


def test_persisted_true_when_only_group_lock_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	group_dir = tmp_path / "group"
	group_dir.mkdir()
	real_write = cdp_risk_lock.write_lock

	def write_main_fails(directory, *args, **kwargs):
		if Path(directory) == data_dir:
			raise OSError("fixture: 主位置不可写")
		return real_write(directory, *args, **kwargs)

	def record_group(**kwargs):
		real_write(group_dir, None, reason=kwargs["reason"], platform_code=kwargs["platform_code"])

	monkeypatch.setattr(cdp_risk_lock, "write_lock", write_main_fails)
	client = _ProbeClient(data_dir)
	error = _record_error(client, SimpleNamespace(root=group_dir, record_risk=record_group))

	assert error.details["risk_lock"]["persisted"] is True
	assert client._unpersisted_risk_lock is None
	assert (group_dir / cdp_risk_lock.LOCK_FILENAME).exists()


def test_persisted_true_when_main_lock_succeeds_but_group_fails(tmp_path: Path) -> None:
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	group_dir = tmp_path / "group"
	group_dir.mkdir()

	def record_group(**_kwargs):
		raise OSError("fixture: 组目录不可写")

	client = _ProbeClient(data_dir)
	error = _record_error(client, SimpleNamespace(root=group_dir, record_risk=record_group))

	assert error.details["risk_lock"]["persisted"] is True
	assert client._unpersisted_risk_lock is None
	assert (data_dir / cdp_risk_lock.LOCK_FILENAME).exists()


def test_persisted_false_only_when_every_location_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	group_dir = tmp_path / "group"
	group_dir.mkdir()

	def write_fails(*_args, **_kwargs):
		raise OSError("fixture: 所有位置不可写")

	def record_group(**_kwargs):
		raise OSError("fixture: 组目录不可写")

	monkeypatch.setattr(cdp_risk_lock, "write_lock", write_fails)
	client = _ProbeClient(data_dir)
	error = _record_error(client, SimpleNamespace(root=group_dir, record_risk=record_group))

	assert error.details["risk_lock"]["persisted"] is False
	assert client._unpersisted_risk_lock is not None


# ── 2. 读请求 4xx（#2567）────────────────────────────────────────────────


def _status_handler(status: int):
	return lambda request: httpx.Response(status, json={"code": 1, "message": "fixture"})

@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_read_request_4xx_raises_deterministic_status_error(tmp_path: Path, status: int) -> None:
	client = _ProbeClient(tmp_path, _status_handler(status))

	with pytest.raises(PlatformStatusRejected) as error:
		client._request("GET", _READ_URL)

	assert error.value.status_code == status
	assert isinstance(error.value, ValueError)


def test_read_request_500_stays_retryable_network_error(tmp_path: Path) -> None:
	client = _ProbeClient(tmp_path, _status_handler(500))

	with pytest.raises(httpx.HTTPStatusError) as error:
		client._request("GET", _READ_URL)

	assert error.value.response.status_code == 500
	assert not isinstance(error.value, PlatformStatusRejected)


def test_read_request_408_stays_retryable(tmp_path: Path) -> None:
	client = _ProbeClient(tmp_path, _status_handler(408))

	with pytest.raises(httpx.HTTPStatusError) as error:
		client._request("GET", _READ_URL)

	assert not isinstance(error.value, PlatformStatusRejected)


def test_write_request_4xx_keeps_outcome_unknown(tmp_path: Path) -> None:
	client = _ProbeClient(tmp_path, _status_handler(400))

	result = client._request("POST", _WRITE_URL, data={"securityId": "fixture"})

	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert result["__cli_error_details__"]["replay_safe"] is False


@pytest.mark.parametrize("status", [400, 406, 409, 410, 422])
def test_parameter_status_code_still_recognises_rejected_status(status: int) -> None:
	assert parameter_status_code(PlatformStatusRejected(status)) == status


@pytest.mark.parametrize("status", [404, 405, 412, 500])
def test_parameter_status_code_ignores_non_parameter_status(status: int) -> None:
	assert parameter_status_code(PlatformStatusRejected(status)) is None


def _decorated_error(ctx, error):
	@handle_auth_errors("search")
	def impl(ctx):
		raise error

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)
	return mock_err.call_args.kwargs


def test_decorator_reports_parameter_4xx_as_invalid_param(tmp_path: Path) -> None:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path, "platform": "zhipin"}

	kwargs = _decorated_error(ctx, PlatformStatusRejected(400))

	assert kwargs["code"] == "INVALID_PARAM"
	assert kwargs["recoverable"] is False
	assert "HTTP 400" in kwargs["message"]


def test_decorator_reports_non_parameter_4xx_as_internal_error(tmp_path: Path) -> None:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path, "platform": "zhipin"}

	kwargs = _decorated_error(ctx, PlatformStatusRejected(404))

	assert kwargs["code"] == "INTERNAL_ERROR"
	assert kwargs["recoverable"] is False


# ── 3. 超长整数导出（#2568）────────────────────────────────────────────


def test_export_keeps_4300_digit_integer_that_json_can_serialize() -> None:
	value = 2**14284
	assert len(str(value)) == 4300
	assert json.dumps({"n": value})

	parsed = json.loads(strict_json_text({"n": value}, null_oversized_ints=True))
	assert parsed == {"n": value}


def test_export_nulls_integer_beyond_4300_digits() -> None:
	value = 10**4300

	parsed = json.loads(strict_json_text({"n": value}, null_oversized_ints=True))

	assert parsed == {"n": None}


def test_export_keeps_4300_digit_negative_integer_with_sign_in_str() -> None:
	value = -(2**14284)
	assert len(str(value)) == 4301  # 负号占一个字符，但数字位仍是 4300
	assert json.dumps({"n": value})

	parsed = json.loads(strict_json_text({"n": value}, null_oversized_ints=True))

	assert parsed == {"n": value}


def test_export_nulls_negative_integer_beyond_4300_digits() -> None:
	value = -(10**4300)

	parsed = json.loads(strict_json_text({"n": value}, null_oversized_ints=True))

	assert parsed == {"n": None}


def test_export_nulls_oversized_integer_inside_nested_structures() -> None:
	payload = {"rows": [{"n": 10**4300, "keep": 2**14284}]}
	parsed = json.loads(strict_json_text(payload, null_oversized_ints=True))

	assert parsed["rows"][0]["n"] is None
	assert parsed["rows"][0]["keep"] == 2**14284


# ── 4. 简历目录枚举（#2569）─────────────────────────────────────────────


@pytest.mark.skipif(_CANNOT_MAKE_UNREADABLE, reason="当前平台无法用权限位制造不可读目录")
def test_unreadable_resume_dir_raises_instead_of_empty_list(tmp_path: Path) -> None:
	root = tmp_path / "resumes"
	root.mkdir()
	store = ResumeStore(root)
	store.save(dict_to_resume({"name": "张三", "title": "测试", "rows": []}))
	root.chmod(0o300)
	try:
		with pytest.raises(OSError):
			store.list_all_with_corrupt()
	finally:
		root.chmod(0o700)


def test_missing_resume_dir_still_reads_as_empty(tmp_path: Path) -> None:
	root = tmp_path / "resumes"
	store = ResumeStore(root)
	root.rmdir()

	assert store.list_all_with_corrupt() == ([], [])


def test_empty_resume_dir_reads_as_empty(tmp_path: Path) -> None:
	assert ResumeStore(tmp_path / "resumes").list_all_with_corrupt() == ([], [])


def test_corrupt_resume_file_stays_in_corrupt_list(tmp_path: Path) -> None:
	root = tmp_path / "resumes"
	root.mkdir()
	(root / "broken.json").write_text("{not json", encoding="utf-8")
	store = ResumeStore(root)
	store.save(dict_to_resume({"name": "张三", "title": "测试", "rows": []}))

	items, corrupt = store.list_all_with_corrupt()

	assert corrupt == ["broken"]
	assert [item["name"] for item in items] == ["张三"]


@pytest.mark.skipif(_CANNOT_MAKE_UNREADABLE, reason="当前平台无法用权限位制造不可读目录")
def test_resume_list_cli_reports_local_io_error_when_dir_unreadable(tmp_path: Path) -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir()
	(resumes / "keep.json").write_text(json.dumps({"name": "张三", "title": "测试", "rows": []}), encoding="utf-8")
	resumes.chmod(0o300)
	try:
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume", "list"])
	finally:
		resumes.chmod(0o700)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False
