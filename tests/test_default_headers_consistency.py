"""默认头必须自洽：UA 声称的平台/版本要和 Client Hints、语言声明一致。"""

import json
import re

import pytest

from boss_agent_cli.api.endpoints import DEFAULT_HEADERS as CANDIDATE_HEADERS
from boss_agent_cli.api.recruiter_endpoints import DEFAULT_HEADERS as RECRUITER_HEADERS

_HEADER_SETS = {
	"boss.yaml": CANDIDATE_HEADERS,
	"recruiter.yaml": RECRUITER_HEADERS,
}


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_client_hint_platform_matches_user_agent(name: str) -> None:
	headers = _HEADER_SETS[name]
	user_agent = headers["User-Agent"]

	assert "Macintosh" in user_agent
	assert headers["sec-ch-ua-platform"] == '"macOS"', name


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_client_hint_brand_version_matches_user_agent(name: str) -> None:
	headers = _HEADER_SETS[name]
	major = re.search(r"Chrome/(\d+)", headers["User-Agent"])
	assert major is not None, name

	for version in re.findall(r'v="(\d+)"', headers["sec-ch-ua"]):
		if version == "8":  # greasing 品牌的版本是刻意无关的
			continue
		assert version == major.group(1), (name, headers["sec-ch-ua"])


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_user_agent_does_not_outrun_the_client_hint_template(name: str) -> None:
	"""UA 版本不能比 sec-ch-ua 里的品牌版本更新——那正是改前的问题。"""
	headers = _HEADER_SETS[name]
	ua_major = int(re.search(r"Chrome/(\d+)", headers["User-Agent"]).group(1))
	brand_versions = {int(v) for v in re.findall(r'"Chromium";v="(\d+)"', headers["sec-ch-ua"])}

	assert brand_versions == {ua_major}, name


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_headers_omit_values_the_real_browser_never_sends(name: str) -> None:
	headers = _HEADER_SETS[name]

	assert "DNT" not in headers, name
	assert "en" not in headers["Accept-Language"], name
	assert headers["Accept-Language"] == "zh-CN,zh;q=0.9", name
	assert "Not(A:Brand" not in headers["sec-ch-ua"], name
	assert headers["X-Requested-With"] == "XMLHttpRequest", name


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_repeated_builds_match_the_user_agent_claims(name: str) -> None:
	"""sec_ch_ua_brands 不应因为 UA 已对齐就把 greasing 品牌也改掉。"""
	from boss_agent_cli.api.httpx_helpers import sec_ch_ua_brands

	headers = _HEADER_SETS[name]
	rebuilt = sec_ch_ua_brands(headers["User-Agent"], headers["sec-ch-ua"])

	assert rebuilt == headers["sec-ch-ua"], name


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_headers_carry_the_observed_http2_priority(name: str) -> None:
	"""真机 147 条业务请求里 143 条带 ``priority: u=1, i``，是固定值。"""
	assert _HEADER_SETS[name]["priority"] == "u=1, i"


@pytest.mark.parametrize("name", sorted(_HEADER_SETS))
def test_headers_carry_the_observed_encoding_set(name: str) -> None:
	assert _HEADER_SETS[name]["Accept-Encoding"] == "gzip, deflate, br, zstd"


def test_doctor_warns_when_delay_is_below_the_observed_cadence() -> None:
	"""实测 15s 间隔连续 3 次会撞 37，90s 则通过；低于 30s 时必须给出提示。"""
	from click.testing import CliRunner

	from boss_agent_cli.commands.doctor import _RISK_FREE_DELAY_FLOOR
	from boss_agent_cli.main import cli

	result = CliRunner().invoke(cli, ["--delay", "5-10", "doctor"])
	payload = json.loads(result.output.splitlines()[-1])
	check = next(c for c in payload["data"]["checks"] if c["name"] == "request_delay")

	assert check["status"] == "warn"
	assert "--delay 30-60" in check["hint"]
	assert _RISK_FREE_DELAY_FLOOR == 30.0
	assert "--delay 5-10" not in check["hint"]


def test_doctor_accepts_a_cadence_above_the_floor() -> None:
	from click.testing import CliRunner

	from boss_agent_cli.main import cli

	result = CliRunner().invoke(cli, ["--delay", "45-90", "doctor"])
	payload = json.loads(result.output.splitlines()[-1])
	check = next(c for c in payload["data"]["checks"] if c["name"] == "request_delay")

	assert check["status"] == "ok"
