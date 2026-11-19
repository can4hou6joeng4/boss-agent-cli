"""services 导出与联系人查找边界回归（Issue #1976–#1978）。"""

import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
import pytest

import boss_agent_cli.wizard.actions as actions
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.contact_lookup import FriendListShapeError, find_friend
from boss_agent_cli.services.job_export import write_export_file
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


def _friend(**overrides):
	friend = {
		"name": "HR",
		"title": "T",
		"brand_name": "C",
		"initiated_by": "对方主动",
		"last_msg": "-",
		"last_time": "-",
		"msg_status": "已读",
		"uid": 1,
		"security_id": "s",
		"encrypt_job_id": "j",
		"unread": 0,
	}
	friend.update(overrides)
	return friend


def _diff() -> dict:
	return {"is_first": True}


def _platform(data, *, pages=None):
	if pages is not None:
		iterator = iter(pages)

		def friend_list(*, page):
			return {"zpData": next(iterator)}
	else:

		def friend_list(*, page):
			return {"zpData": data}

	return SimpleNamespace(
		friend_list=friend_list,
		is_success=lambda response: True,
		unwrap_data=lambda response: response.get("zpData"),
	)


# ── #1976：chat 导出非有限数 ──────────────────────────────────────


def test_chat_export_non_finite_scalars_render_empty():
	"""1e400 / NaN 与 JSON 分支的 null 同口径，不再落成 inf / nan（#1976）。"""
	raw = json.loads('{"lastMsg": 1e400, "lastTime": NaN}')
	friend = _friend(last_msg=raw["lastMsg"], last_time=raw["lastTime"])

	item = json.loads(render_export([friend], "json", None, None, _diff()))[0]
	assert item["last_msg"] is None
	assert item["last_time"] is None

	csv_text = render_export([friend], "csv", None, None, _diff())
	row = next(csv.reader(io.StringIO(csv_text)))
	assert "inf" not in row and "nan" not in row

	md_text = render_export([friend], "md", None, None, _diff())
	assert "| inf |" not in md_text and "| nan |" not in md_text

	html_text = render_export([friend], "html", None, None, _diff())
	assert ">inf<" not in html_text and ">nan<" not in html_text


def test_chat_export_nested_non_finite_container_uses_null():
	"""容器分支先递归归一，不再写出非标准 Infinity / NaN（#1976）。"""
	friend = _friend(last_msg={"score": 1e400, "other": float("nan")})

	csv_text = render_export([friend], "csv", None, None, _diff())
	row = next(csv.DictReader(io.StringIO(csv_text)))
	assert json.loads(row["last_msg"]) == {"score": None, "other": None}
	assert "Infinity" not in csv_text and "NaN" not in csv_text

	md_text = render_export([friend], "md", None, None, _diff())
	assert "Infinity" not in md_text and "NaN" not in md_text

	html_text = render_export([friend], "html", None, None, _diff())
	assert "Infinity" not in html_text and "NaN" not in html_text


# ── #1977：job 导出容器分支 ──────────────────────────────────────


def test_job_export_container_normalizes_non_finite(tmp_path: Path):
	nested = json.loads('{"score": 1e400, "other": NaN}')
	csv_path = tmp_path / "out.csv"
	write_export_file([{"title": "T", "raw_job_type": nested, "skills": [nested]}], "csv", str(csv_path))
	csv_text = csv_path.read_text(encoding="utf-8")
	assert "Infinity" not in csv_text and "NaN" not in csv_text
	row = next(csv.DictReader(io.StringIO(csv_text)))
	assert json.loads(row["raw_job_type"]) == {"score": None, "other": None}
	assert json.loads(row["skills"]) == {"score": None, "other": None}

	json_path = tmp_path / "out.json"
	write_export_file([{"title": "T", "raw_job_type": nested}], "json", str(json_path))
	item = json.loads(json_path.read_text(encoding="utf-8"))[0]
	assert item["raw_job_type"] == {"score": None, "other": None}


# ── #1978：find_friend 第三态 ────────────────────────────────────


@pytest.mark.parametrize(
	"data",
	[None, [], "busy", {}, {"result": None}, {"hasMore": True}],
)
def test_find_friend_shape_error_when_no_list_container(data):
	"""成功码但没有任何 list 容器时不能宣称联系人不存在（#1978）。"""
	with pytest.raises(FriendListShapeError):
		find_friend(_platform(data), "10001", max_pages=3)


@pytest.mark.parametrize(
	"data",
	[
		{"result": []},
		{"result": [], "hasMore": False},
		{"friendList": [], "hasMore": False},
		{"hasMore": False},
		{"result": [{"uid": "other"}], "hasMore": False},
	],
)
def test_find_friend_empty_container_or_explicit_end_is_not_found(data):
	"""真正的空页与显式 hasMore=false 仍按「未找到」处理（#1978 回归护栏）。"""
	assert find_friend(_platform(data), "10001", max_pages=3) == (None, None)


def test_find_friend_shape_error_when_later_page_loses_container():
	pages = [{"result": [{"uid": "other"}], "hasMore": True}, {}]
	with pytest.raises(FriendListShapeError):
		find_friend(_platform(None, pages=pages), "10001", max_pages=3)


def test_find_friend_surrogate_tolerated_and_matching_still_works():
	"""孤立代理项不影响匹配，正常命中优先于任何形状判定（#1978 护栏）。"""
	data = {"result": [{"uid": "10001", "name": "坏 \ud800"}], "hasMore": False}
	friend, error_response = find_friend(_platform(data), "10001", max_pages=3)
	assert error_response is None
	assert friend is not None and friend["uid"] == "10001"


def test_wizard_maps_shape_error_to_recoverable_network_error(tmp_path: Path, monkeypatch):
	def _shape(*args, **kwargs):
		raise FriendListShapeError("沟通列表响应不包含可识别的联系人列表，无法确认联系人是否存在")

	monkeypatch.setattr(actions, "find_friend", _shape)
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as exc_info:
		actions._candidate_friend(object(), "10001", context=context)

	error = exc_info.value
	assert error.code == "NETWORK_ERROR"
	assert error.recoverable is True
	assert "chat" in error.recovery_action
	assert str(tmp_path.resolve()) in error.recovery_action


def test_chatmsg_maps_shape_error_to_recoverable_network_error():
	with patch(
		"boss_agent_cli.commands._contact_lookup.find_friend",
		side_effect=FriendListShapeError("沟通列表响应不包含可识别的联系人列表，无法确认联系人是否存在"),
	), patch("boss_agent_cli.commands.chatmsg.get_platform_instance"), patch(
		"boss_agent_cli.commands.chatmsg.AuthManager"
	):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(Path("/tmp/does-not-matter")), "chatmsg", "10001"]
		)

	assert result.exit_code == 1, result.stdout
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True
	assert "chat" in error["recovery_action"]
