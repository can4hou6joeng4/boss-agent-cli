"""简历行类型漂移与本地失败语义的回归测试。

覆盖三个已确认缺陷：

- Issue #1370：``rows[].content/tags`` 的类型漂移不能让 ``resume_to_text`` 抛错，
  合法输入的文本输出必须逐字节不变；
- Issue #1369：ai / agent 读简历时损坏与缺失必须分开报，空名在入口拒绝；
- Issue #1366：agent crawl 的 profile 路径与导出失败是本地确定性失败，
  不能兜成「安装 crawl 依赖」的 ``CRAWL_UNAVAILABLE``。
"""

import json
import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlExportError, CrawlProfilePathInvalid
from boss_agent_cli.crawler.service import CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.resume.models import (
	JobIntentionItem,
	JobIntentionSection,
	PersonalInfoItem,
	PersonalInfoSection,
	ResumeData,
	ResumeModule,
	resume_to_text,
)


def _invoke(runner: CliRunner, tmp_path: Path, *args: str):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _resume_with_rows(rows: list[dict]) -> ResumeData:
	return ResumeData(
		name="张三",
		title="开发工程师",
		modules=[ResumeModule(id="module", title="模块", rows=rows)],
	)


def _row_lines(rows: list[dict]) -> list[str]:
	return resume_to_text(_resume_with_rows(rows)).splitlines()


# ── Issue #1370：rows[].content / tags 类型漂移 ──────────────────────────


@pytest.mark.parametrize(
	("row", "expected_tail"),
	[
		# content 是标量：str() 后使用，不再让 for 循环抛 TypeError。
		({"type": "richtext", "content": 123}, ["123"]),
		({"type": "text", "content": 123}, ["123"]),
		# content 是数组：只拼接字符串元素。
		({"type": "richtext", "content": ["第一行", 1, None]}, ["第一行"]),
		({"type": "text", "content": ["a", 1]}, ["a"]),
		# content 是字符串：整串使用，不再逐字符拆行。
		({"type": "richtext", "content": "整段文本"}, ["整段文本"]),
		({"type": "text", "content": "整段文本"}, ["整段文本"]),
		# tags 是字符串：整串使用，不再拆成 "a, b, c"。
		({"type": "tags", "tags": "abc"}, ["abc"]),
		# tags 是数组：只拼接字符串元素。
		({"type": "tags", "tags": ["a", 1, "b"]}, ["a, b"]),
		# tags 其它类型：忽略。
		({"type": "tags", "tags": 123}, []),
	],
)
def test_row_content_type_drift_is_normalized(row: dict, expected_tail: list[str]) -> None:
	assert _row_lines([row]) == ["张三", "开发工程师", "", "模块", *expected_tail]


def test_resume_to_text_legal_input_is_byte_identical() -> None:
	"""合法简历文本必须与归一前完全一致（AI 提示词输入不能悄悄变）。"""
	resume = ResumeData(
		name="张三",
		title="Python 开发工程师",
		personal_info=PersonalInfoSection(
			items=[
				PersonalInfoItem(label="邮箱", value="test@example.com"),
				PersonalInfoItem(label="手机", value="13800138000"),
			],
		),
		job_intention=JobIntentionSection(
			items=[
				JobIntentionItem(label="期望职位", value="Python 开发"),
				JobIntentionItem(label="期望城市", value="广州"),
			],
		),
		modules=[
			ResumeModule(
				id="skills",
				title="技能特长",
				rows=[
					{"type": "tags", "tags": ["Python", "Go", "Rust"]},
					{"type": "richtext", "content": ["精通 Python 后端开发"]},
					{"type": "text", "content": ["项目 A"]},
				],
			),
		],
	)
	expected = "\n".join([
		"张三",
		"Python 开发工程师",
		"",
		"邮箱: test@example.com",
		"手机: 13800138000",
		"",
		"求职意向",
		"期望职位: Python 开发",
		"期望城市: 广州",
		"",
		"技能特长",
		"Python, Go, Rust",
		"精通 Python 后端开发",
		"项目 A",
	])
	assert resume_to_text(resume) == expected


# ── Issue #1369：损坏 / 缺失 / 空名 ─────────────────────────────────────


def _write_corrupt_resume(tmp_path: Path, name: str = "broken") -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir(parents=True, exist_ok=True)
	(resumes / f"{name}.json").write_text("{bad json", encoding="utf-8")


def _fake_ai_service(monkeypatch) -> None:
	# 简历加载发生在 AI 调用之前：提供非 None 服务即可走到目标分支。
	monkeypatch.setattr("boss_agent_cli.commands.ai_cmd._require_ai_service", lambda ctx: object())


def test_ai_polish_corrupt_resume_reports_file_invalid(tmp_path: Path, monkeypatch) -> None:
	_write_corrupt_resume(tmp_path)
	_fake_ai_service(monkeypatch)

	result = _invoke(CliRunner(), tmp_path, "ai", "polish", "broken")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "ai"
	error = payload["error"]
	assert error["code"] == "RESUME_FILE_INVALID"
	assert "broken" in error["message"]
	assert "JSON" in error["message"]
	assert "resume delete broken" in error["recovery_action"]
	assert [shlex.split(action) for action in payload["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "resume", "delete", "broken"],
		["boss", "--data-dir", str(tmp_path.resolve()), "resume", "list"],
	]


@pytest.mark.parametrize("name", ["", "   "])
def test_ai_polish_empty_resume_name_is_invalid_param(tmp_path: Path, monkeypatch, name: str) -> None:
	_fake_ai_service(monkeypatch)

	result = _invoke(CliRunner(), tmp_path, "ai", "polish", name)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "简历名称不能为空"
	assert [shlex.split(action) for action in payload["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "resume", "list"],
	]


def test_ai_polish_missing_resume_stays_not_found(tmp_path: Path, monkeypatch) -> None:
	_fake_ai_service(monkeypatch)

	result = _invoke(CliRunner(), tmp_path, "ai", "polish", "ghost")

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=True,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)


def _complete_run(tmp_path: Path, run_id: str = "run-1") -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run(run_id, _settings(tmp_path).as_dict(), str(tmp_path / "crawl" / "runs" / run_id))
		cache.update_crawl_run(run_id, status="completed", next_page=2, list_finished=True)


def test_agent_crawl_corrupt_resume_reports_file_invalid(tmp_path: Path) -> None:
	_complete_run(tmp_path)
	_write_corrupt_resume(tmp_path)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--run-id", "run-1", "--resume", "broken"
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "agent.crawl"
	error = payload["error"]
	assert error["code"] == "RESUME_FILE_INVALID"
	assert "broken" in error["message"]
	assert "JSON" in error["message"]
	assert "resume delete broken" in error["recovery_action"]
	assert [shlex.split(action) for action in payload["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "resume", "delete", "broken"],
		["boss", "--data-dir", str(tmp_path.resolve()), "resume", "list"],
	]


def test_agent_crawl_missing_resume_stays_not_found(tmp_path: Path) -> None:
	_complete_run(tmp_path)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--run-id", "run-1", "--resume", "ghost"
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.parametrize("name", ["", "   "])
def test_agent_crawl_empty_resume_name_is_invalid_param_before_crawling(
	tmp_path: Path, monkeypatch, name: str
) -> None:
	"""空名必须在入口拒绝：不能先跑完采集再报「简历 '' 不存在」。"""
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent.CrawlService.create_and_run",
		lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("空简历名不该开始采集")),
	)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--query", "AI", "--city", "杭州", "--resume", name
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "简历名称不能为空"
	assert [shlex.split(action) for action in payload["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "resume", "list"],
	]


# ── Issue #1407：简历校验必须先于采集 / 短名单导入 ──────────────────────


def test_agent_crawl_missing_resume_is_rejected_before_new_crawl(tmp_path: Path, monkeypatch) -> None:
	"""--query 路径：非空但不存在的简历名不能先采集再报错（Issue #1407）。"""
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent.CrawlService.create_and_run",
		lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("简历缺失不该开始采集")),
	)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--query", "AI", "--city", "杭州", "--resume", "ghost"
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"
	assert not (tmp_path / "cache" / "boss_agent.db").exists(), "简历缺失不该创建 crawl run"


def test_agent_crawl_corrupt_resume_is_rejected_before_new_crawl(tmp_path: Path, monkeypatch) -> None:
	"""损坏简历同样要在采集前报 RESUME_FILE_INVALID（Issue #1407）。"""
	_write_corrupt_resume(tmp_path)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent.CrawlService.create_and_run",
		lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("简历损坏不该开始采集")),
	)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--query", "AI", "--city", "杭州", "--resume", "broken"
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "RESUME_FILE_INVALID"
	assert not (tmp_path / "cache" / "boss_agent.db").exists(), "简历损坏不该创建 crawl run"


def test_agent_crawl_missing_resume_is_rejected_before_shortlist_import(tmp_path: Path, monkeypatch) -> None:
	"""--run-id 路径：简历缺失不能先导入短名单再报错（Issue #1407）。"""
	_complete_run(tmp_path)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent.import_crawl_shortlist",
		lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("简历缺失不该导入短名单")),
	)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--run-id", "run-1", "--resume", "ghost"
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "RESUME_NOT_FOUND"
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.list_shortlist() == []


def test_agent_crawl_corrupt_resume_is_rejected_before_shortlist_import(tmp_path: Path, monkeypatch) -> None:
	"""--run-id 路径的损坏简历同样要先于短名单导入（Issue #1407）。"""
	_complete_run(tmp_path)
	_write_corrupt_resume(tmp_path)
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent.import_crawl_shortlist",
		lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("简历损坏不该导入短名单")),
	)

	result = _invoke(
		CliRunner(), tmp_path, "agent", "crawl", "--run-id", "run-1", "--resume", "broken"
	)

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "RESUME_FILE_INVALID"
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.list_shortlist() == []


# ── Issue #1366：agent crawl 的本地确定性失败 ───────────────────────────


def _seed_login(tmp_path: Path) -> None:
	from boss_agent_cli.auth.token_store import TokenStore

	TokenStore(tmp_path / "auth").save({
		"cookies": {"wt2": "test-wt2", "wbg": "1"},
		"user_agent": "test-agent",
		"stoken": "",
	})


def _init_resume(runner: CliRunner, tmp_path: Path) -> None:
	"""建立一份可读简历：简历校验已前移到采集之前（Issue #1407）。"""
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "test-resume", "--template", "default"],
	)
	assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
	("failure", "expected_recovery"),
	[
		(CrawlProfilePathInvalid("采集配置目录被同名文件占用"), "chrome-profile"),
		(CrawlExportError("无法写入输出目录: 目标路径是同名文件"), "输出目录"),
	],
	ids=["profile-path-invalid", "export-failed"],
)
def test_agent_crawl_local_failures_are_local_io_error(
	tmp_path: Path, monkeypatch, failure: RuntimeError, expected_recovery: str
) -> None:
	_seed_login(tmp_path)
	runner = CliRunner()
	_init_resume(runner, tmp_path)

	def _raise(self, settings):
		raise failure

	monkeypatch.setattr("boss_agent_cli.commands.agent.CrawlService.create_and_run", _raise)

	result = _invoke(
		runner, tmp_path, "agent", "crawl", "--query", "AI", "--city", "杭州", "--resume", "test-resume"
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "agent.crawl"
	error = payload["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
	assert error["message"] == str(failure)
	assert expected_recovery in error["recovery_action"]
	assert "安装" not in error["recovery_action"]
