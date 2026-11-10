"""Recruiter automation fusion tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.automation.adapters import build_automation_adapter
from boss_agent_cli.automation.boss_adapter import BossRecruiterAutomationPlatform
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy, automation_config_from_dict
from boss_agent_cli.automation.decision import decide_action
from boss_agent_cli.automation.execution import status_for_decision
from boss_agent_cli.automation.models import (
	ActionResult,
	AutomationMode,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	EventStatus,
	ReviewItem,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.main import cli


class FakeBossRecruiter:
	def __init__(self) -> None:
		self.sent: list[tuple[int, str]] = []
		self.exchanges: list[tuple[int, int]] = []
		self.history_calls: list[int] = []

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict:
		return {"code": 0, "zpData": {"result": [{"friendId": 101, "name": "高分"}]}}

	def friend_list(
		self,
		page: int = 1,
		label_id: int = 0,
		job_id: str | None = None,
	) -> dict:
		return {"code": 0, "zpData": {"result": [{"friendId": 101, "name": "高分"}]}}

	def chat_history(
		self,
		gid: int,
		*,
		count: int = 20,
		max_msg_id: int | None = None,
	) -> dict:
		self.history_calls.append(gid)
		return {
			"code": 0,
			"zpData": {
				"result": [
					{
						"from": "geek",
						"content": "你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会",
					}
				]
			},
		}

	def send_message_by_friend(self, friend_id: int, content: str) -> dict:
		self.sent.append((friend_id, content))
		return {"code": 0, "zpData": {"friendId": friend_id}}

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict:
		self.exchanges.append((friend_id, exchange_type))
		return {"code": 0, "zpData": {"friendId": friend_id}}


class RecordingMockRecruiterAutomationPlatform(MockRecruiterAutomationPlatform):
	def __init__(self, name: str, conversations: list[Conversation]) -> None:
		super().__init__(name, conversations)
		self.messages: list[str] = []

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.messages.append(message)
		return super().execute_action(action, message, ref)


def _conversation(text: str, title: str = "张三") -> Conversation:
	return Conversation(
		title=title,
		incoming_messages=(text,),
		ordered_messages=(("incoming", text),),
		all_messages=(text,),
		fingerprint=ConversationFingerprint(title),
	)


def test_automation_config_from_empty_matches_dataclass_defaults() -> None:
	assert automation_config_from_dict({}) == AutomationConfig()
	assert automation_config_from_dict(None) == AutomationConfig()


@pytest.mark.parametrize(
	"raw, fragment",
	[
		({"max_per_tab": -1}, "max_per_tab"),
		({"max_actions_per_run": -5}, "max_actions_per_run"),
		({"max_consecutive_errors": -1}, "max_consecutive_errors"),
		({"auto_execute_threshold": -0.1}, "auto_execute_threshold"),
		({"auto_execute_threshold": 1.5}, "auto_execute_threshold"),
		({"human_review_threshold": -1}, "human_review_threshold"),
		(
			{"human_review_threshold": 0.9, "auto_execute_threshold": 0.5},
			"human_review_threshold",
		),
	],
)
def test_automation_config_rejects_out_of_range_numbers(raw, fragment) -> None:
	"""负数 max_per_tab 会让 `items[:max_per_tab]` 反转成「去掉末尾 N 个」；
	负数 auto_execute_threshold 则让所有通过初筛的写操作自动执行。"""
	with pytest.raises(ValueError, match=fragment):
		automation_config_from_dict(raw)


def test_automation_config_accepts_zero_limits() -> None:
	"""0 有明确语义（本轮不扫描 / 不执行 / 首次错误即熔断），不能被范围校验误伤。"""
	config = automation_config_from_dict(
		{"max_per_tab": 0, "max_actions_per_run": 0, "max_consecutive_errors": 0}
	)
	assert config.max_per_tab == 0
	assert config.max_actions_per_run == 0
	assert config.max_consecutive_errors == 0


def test_automation_config_ignores_removed_keys() -> None:
	"""已移除的死配置键不能让旧 config.json 加载失败。

	`platforms` 与 `stop_on_page_text` 从未有消费点：平台由全局 --platform 决定，
	页面风控由 api/zhipin_errors 的 URL / 页面内容检测负责。字段删除后，用户的
	旧 config.json 里可能仍存有它们，解析必须继续接受。
	"""
	config = automation_config_from_dict({"platforms": ["zhipin"], "stop_on_page_text": ["验证码"]})
	assert config == AutomationConfig()
	# 死字段不得被重新加回：配置项存在却不生效，与「已实现」的承诺相悖。
	assert not {"platforms", "stop_on_page_text"} & set(AutomationConfig.__dataclass_fields__)


def test_automation_config_rejects_unknown_allowed_actions() -> None:
	"""拼错的动作名必须报错，不能被静默丢弃。

	旧实现对非法值 `if item in allowed_action_values` 过滤后 `or DEFAULT_ALLOWED_ACTIONS`，
	写错一个枚举值反而回退到完整的默认写动作集——用户以为收紧了权限，实际全部放开。
	"""
	with pytest.raises(ValueError, match="allowed_actions"):
		automation_config_from_dict({"allowed_actions": ["send_follow_up", "send_folow_up"]})


def test_automation_config_rejects_non_list_allowed_actions() -> None:
	"""字符串会被逐字符迭代，必须按形状错误拒绝并给出字段名。"""
	with pytest.raises(ValueError, match="allowed_actions"):
		automation_config_from_dict({"allowed_actions": "send_follow_up"})


def test_automation_config_empty_allowed_actions_skips_every_action() -> None:
	"""空列表的语义是「什么都不允许」，执行层全部 SKIPPED。

	配置解析不得把它替换成默认动作集；需要禁用写操作时也应保持这一 fail-closed 行为。
	"""
	config = automation_config_from_dict({"allowed_actions": []})
	assert config.allowed_actions == ()

	conversation = _conversation("你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会")
	decision = decide_action(conversation, config, {})
	assert status_for_decision(config, decision, dry_run=False) is EventStatus.SKIPPED


@pytest.mark.parametrize(
	"raw",
	[
		{"tabs": "新招呼"},
		{"tabs": None},
		{"tabs": 3},
		{"tabs": ["新招呼", 1]},
	],
)
def test_automation_config_rejects_malformed_tabs(raw) -> None:
	"""tabs 必须是字符串数组。

	旧实现对值直接迭代：字符串 ``"新招呼"`` 被拆成 ``("新", "招", "呼")``，
	执行层匹配不到任何 tab，自动化空转却报成功；``null`` / 数字则抛未包装的
	TypeError，命令以内部错误退出。
	"""
	with pytest.raises(ValueError, match="tabs"):
		automation_config_from_dict(raw)


@pytest.mark.parametrize("key", ["questionnaire_message", "follow_up_message"])
@pytest.mark.parametrize("value", [None, 123, "", "   "])
def test_automation_config_rejects_malformed_messages(key, value) -> None:
	"""话术会原样发给候选人：null 经 str() 变成 "None"，空串等于发送空消息。"""
	with pytest.raises(ValueError, match=key):
		automation_config_from_dict({key: value})


def test_decision_sends_questionnaire_for_high_confidence_new_candidate() -> None:
	config = AutomationConfig()
	conversation = _conversation(
		"你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会"
	)

	decision = decide_action(conversation, config, {})

	assert decision.action is PlatformAction.SEND_QUESTIONNAIRE
	assert decision.confidence >= config.auto_execute_threshold
	assert decision.requires_human is False


def test_assist_and_training_modes_do_not_create_approval_gate() -> None:
	conversation = _conversation("你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会")
	for mode in (AutomationMode.ASSIST, AutomationMode.TRAINING):
		config = AutomationConfig(mode=mode)
		decision = decide_action(conversation, config, {})
		assert status_for_decision(config, decision, dry_run=False) is EventStatus.AUTO_EXECUTED


def test_hybrid_local_ai_rewrites_reply_without_changing_action(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AutomationStore(tmp_path)
	ai_store = AIConfigStore(tmp_path)
	ai_store.save_config(ai_provider="ollama", ai_model="qwen3:14b")
	ai_store.save_api_key("local")
	adapter = RecordingMockRecruiterAutomationPlatform(
		"zhipin",
		[_conversation("你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会", "高分")],
	)
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = {
		"choices": [
			{
				"message": {
					"content": json.dumps({
						"action": "send_questionnaire",
						"confidence": 0.88,
						"reply": "您好，方便的话想确认下近期是否看新的销售机会？",
						"reason": "local ai polished",
						"risk_flags": [],
					}, ensure_ascii=False)
				}
			}
		]
	}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response):
		report = run_automation_cycle(
			adapter,
			store,
			AutomationConfig(reply_strategy=ReplyStrategy.HYBRID),
			platform="zhipin",
			dry_run=False,
			limit=1,
		)

	assert report.events[0].action == PlatformAction.SEND_QUESTIONNAIRE.value
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert adapter.messages == ["您好，方便的话想确认下近期是否看新的销售机会？"]


def test_local_ai_parse_error_is_skipped_without_sending_or_review(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AutomationStore(tmp_path)
	ai_store = AIConfigStore(tmp_path)
	ai_store.save_config(ai_provider="ollama", ai_model="qwen3:14b")
	ai_store.save_api_key("local")
	adapter = RecordingMockRecruiterAutomationPlatform(
		"zhipin",
		[_conversation("你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会", "高分")],
	)
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = {"choices": [{"message": {"content": "not json"}}]}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response):
		report = run_automation_cycle(
			adapter,
			store,
			AutomationConfig(reply_strategy=ReplyStrategy.LOCAL_AI),
			platform="zhipin",
			dry_run=False,
			limit=1,
		)

	assert report.events[0].status == EventStatus.SKIPPED.value
	assert adapter.messages == []
	assert store.read_reviews() == []


def test_runner_dry_run_writes_events_without_review_queue(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	adapter = MockRecruiterAutomationPlatform(
		"zhipin",
		[
			_conversation(
				"你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会",
				"高分",
			),
			_conversation("不要再联系", "风险"),
		],
	)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
	)

	assert report.status == "OK"
	assert any(event.status == EventStatus.DRY_RUN.value for event in report.events)
	assert store.stats()["human_reviews"] == 0
	assert any(event.status == EventStatus.SKIPPED.value for event in report.events)
	assert store.stats()["dry_run"] >= 1


def test_runner_creates_interview_lead_after_contact_exchange(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	state = {
		"conversations": {
			"面试候选人": {"exchange_contact_at": "2026-06-23T00:00:00"}
		}
	}
	store.write_state(state)
	adapter = MockRecruiterAutomationPlatform(
		"zhipin",
		[
			_conversation(
				"我在上海做过3年销售，大专，想看机会，周二 14:00 面试",
				"面试候选人",
			)
		],
	)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
	)

	assert report.events[0].action == PlatformAction.CREATE_INTERVIEW_LEAD.value
	assert (tmp_path / "automation" / "interview-leads.csv").exists()


def test_agent_run_cli_returns_json_envelope(tmp_path: Path) -> None:
	# --dry-run 以 live=False 构造 zhipin 适配器（不建客户端、不连 CDP），
	# 扫描只产出诊断 ref，因此离线稳定得到 STOPPED_BY_SAFETY。
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"--role",
			"recruiter",
			"agent",
			"run",
			"--dry-run",
			"--limit",
			"1",
		],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["command"] == "agent.run"
	assert payload["data"]["platform"] == "zhipin"
	assert payload["data"]["events"][0]["status"] == "STOPPED_BY_SAFETY"


def test_agent_run_rejects_non_positive_limit(tmp_path: Path) -> None:
	"""`--limit` 为 0 或负数时不能进入扫描。

	负数会传到适配器的 `items[:limit]`，切片语义反转成「去掉最后 N 个」，
	等于处理除末尾 N 个以外的全部会话；0 则被 `limit or max_per_tab` 静默放大。
	两者都必须按参数错误拒绝。
	"""
	runner = CliRunner()
	for value in ("-1", "0"):
		result = runner.invoke(
			cli,
			[
				"--data-dir", str(tmp_path), "--json",
				"--role", "recruiter",
				"agent", "run", "--dry-run", "--limit", value,
			],
		)
		assert result.exit_code == 1, value
		payload = json.loads(result.output)
		assert payload["error"]["code"] == "INVALID_PARAM", value


def test_agent_train_rejects_non_positive_limit(tmp_path: Path) -> None:
	"""训练模式与 run 共用同一条扫描路径，同样拒绝非正的 `--limit`。"""
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json",
			"--role", "recruiter",
			"agent", "train", "--limit", "-1",
		],
	)
	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_runner_limit_zero_scans_nothing(tmp_path: Path) -> None:
	"""Python API 直接传 limit=0 时也不得回退到 max_per_tab。"""
	store = AutomationStore(tmp_path)
	adapter = MockRecruiterAutomationPlatform("zhipin")

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
		limit=0,
	)

	assert report.events == ()
	assert adapter.executed == []


def test_agent_stats_review_and_pending_commands_are_available(tmp_path: Path) -> None:
	runner = CliRunner()
	for args, command in [
		(["agent", "stats"], "agent.stats"),
		(["agent", "review", "list"], "agent.review.list"),
		(["agent", "pending", "list"], "agent.pending.list"),
	]:
			result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
			assert result.exit_code == 0
			payload = json.loads(result.output)
			assert payload["command"] == command


def test_review_approve_moves_item_to_pending_queue(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = "zhipin-candidate-1-send_follow_up"
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhipin",
			candidate_key="candidate-1",
			action=PlatformAction.SEND_FOLLOW_UP.value,
			status="review",
			confidence=0.7,
			reason="needs review",
			message="继续沟通",
		)
	)
	runner = CliRunner()

	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"agent",
			"review",
			"approve",
			review_id,
		],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "agent.review.approve"
	assert store.read_pending()[0].approved_review_id == review_id


def test_review_reject_marks_item_and_writes_skip_event(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = "zhipin-candidate-2-exchange_contact"
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhipin",
			candidate_key="candidate-2",
			action=PlatformAction.EXCHANGE_CONTACT.value,
			status="review",
			confidence=0.68,
			reason="needs review",
		)
	)
	runner = CliRunner()

	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"agent",
			"review",
			"reject",
			review_id,
			"--reason",
			"not a fit",
		],
	)

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "agent.review.reject"
	assert store.read_reviews()[0].status == "rejected"
	assert store.read_jsonl("action-log.jsonl")[0]["status"] == EventStatus.SKIPPED.value


def test_review_approve_missing_item_returns_envelope(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "review", "approve", "missing-id"],
	)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["command"] == "agent.review.approve"
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不存在或已处理" in payload["error"]["message"]
	assert payload["hints"]["next_actions"]


def test_review_reject_missing_item_returns_envelope(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "agent", "review", "reject", "missing-id"],
	)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["command"] == "agent.review.reject"
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不存在或已处理" in payload["error"]["message"]


def test_pending_actions_execute_before_new_conversation_scan(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = "zhipin-pending-candidate-send_follow_up"
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhipin",
			candidate_key="pending-candidate",
			action=PlatformAction.SEND_FOLLOW_UP.value,
			status="review",
			confidence=0.9,
			reason="approved follow-up",
			message="继续沟通",
		)
	)
	store.approve_review(review_id, "approved-ts")
	adapter = MockRecruiterAutomationPlatform(
		"zhipin",
		[_conversation("你好，我在上海做过3年销售，大专，想看机会", "新候选人")],
	)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
	)

	assert report.events[0].candidate_key == "pending-candidate"
	assert report.events[0].action == PlatformAction.SEND_FOLLOW_UP.value
	assert report.events[1].candidate_key == "新候选人"


def test_boss_adapter_uses_recruiter_platform_for_message_execution(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	recruiter = FakeBossRecruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert recruiter.history_calls == [101]
	assert recruiter.sent[0][0] == 101
	assert "近期是否看机会" in recruiter.sent[0][1]


class UnconfirmedFakeBossRecruiter(FakeBossRecruiter):
	"""页面动作已执行但结果未确认：可能已发出，禁止自动重试。"""

	def send_message_by_friend(self, friend_id: int, content: str) -> dict:
		self.sent.append((friend_id, content))
		return {
			"code": -1,
			"message": "发送消息未确认: 页面未确认送达",
			"__cli_error_code__": "ACTION_UNCONFIRMED",
			"__cli_error_details__": {"action": "reply"},
		}


class UnconfirmedEmptyScanBossRecruiter(UnconfirmedFakeBossRecruiter):
	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict:
		return {"code": 0, "zpData": {"result": []}}

	def friend_list(
		self,
		page: int = 1,
		label_id: int = 0,
		job_id: str | None = None,
	) -> dict:
		return {"code": 0, "zpData": {"result": []}}


def test_agent_stats_reports_corrupt_state(tmp_path: Path) -> None:
	state_dir = tmp_path / "automation"
	state_dir.mkdir()
	(state_dir / "state.json").write_text('{"conversations": ', encoding="utf-8")

	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "stats"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "state.json" in payload["error"]["message"]
	assert payload["error"]["recoverable"] is True
	assert payload["error"]["recovery_action"]


def test_agent_run_reports_corrupt_state(tmp_path: Path) -> None:
	state_dir = tmp_path / "automation"
	state_dir.mkdir()
	(state_dir / "state.json").write_text("[]", encoding="utf-8")

	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path), "--json",
			"--role", "recruiter",
			"agent", "run", "--dry-run", "--limit", "1",
		],
	)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "顶层必须是 JSON object" in payload["error"]["message"]


def test_corrupt_stop_latch_fails_closed(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	(store.root / "stop.json").write_text("not json", encoding="utf-8")

	stop = store.read_stop()
	assert stop["open"] is True
	assert stop["corrupt"] is True
	assert "无法解析" in stop["reason"]

	state = store.read_state()
	assert state["autonomy"]["circuit_breaker"]["open"] is True

	# 熔断开关未知时，新一轮自动化必须直接停下，不执行任何平台动作。
	adapter = MockRecruiterAutomationPlatform("zhipin")
	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)

	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert adapter.executed == []


def test_circuit_breaker_without_reason_reports_chinese_default(tmp_path: Path) -> None:
	"""熔断原因缺省时会写进 stop.json 并回显给用户，默认文案必须是中文。"""
	store = AutomationStore(tmp_path)
	store.write_state({"autonomy": {"circuit_breaker": {"open": True}}})
	assert store.read_stop()["reason"] == "熔断已打开"

	guard = SafetyGuard(AutomationConfig(), {"autonomy": {"circuit_breaker": {"open": True}}}, dry_run=True)
	assert guard.stop_reason() == "熔断已打开"


def test_corrupt_jsonl_lines_are_skipped(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	(store.root / "action-log.jsonl").write_text(
		'{"status": "AUTO_EXECUTED"}\nnot json\n[]\n',
		encoding="utf-8",
	)

	rows = store.read_jsonl("action-log.jsonl")

	assert rows == [{"status": "AUTO_EXECUTED"}]


def test_boss_adapter_stops_when_send_result_is_unconfirmed(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	recruiter = UnconfirmedFakeBossRecruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)

	assert len(recruiter.sent) == 1
	assert report.events[0].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	assert report.events[0].reason == "ACTION_UNCONFIRMED"
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert store.read_stop()["open"] is True

	# 未人工核对、未 --resume 时，新一轮必须直接停止，不得重发。
	retry_report = run_automation_cycle(
		BossRecruiterAutomationPlatform(recruiter),
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)
	assert len(recruiter.sent) == 1
	assert retry_report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value


def test_unconfirmed_pending_action_is_not_replayed_after_resume(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	review_id = "zhipin-unconfirmed-pending-send_follow_up"
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="ts",
			platform="zhipin",
			candidate_key="101",
			action=PlatformAction.SEND_FOLLOW_UP.value,
			status="review",
			confidence=0.9,
			reason="approved follow-up",
			message="继续沟通",
		)
	)
	store.approve_review(review_id, "approved-ts")
	recruiter = UnconfirmedEmptyScanBossRecruiter()

	report = run_automation_cycle(
		BossRecruiterAutomationPlatform(recruiter),
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
	)

	assert report.events[0].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	assert recruiter.sent == [(101, "继续沟通")]
	pending = store.read_pending()[0]
	assert pending.status == "verification-required"

	# 人工核对后显式 resume 也不能把未确认动作当成 pending 重发。
	assert store.resume() is True
	resumed = run_automation_cycle(
		BossRecruiterAutomationPlatform(recruiter),
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
	)
	assert resumed.events == ()
	assert recruiter.sent == [(101, "继续沟通")]


def test_boss_adapter_uses_recruiter_platform_for_contact_exchange(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.write_state({"conversations": {"101": {"follow_up_sent_at": "ts"}}})
	recruiter = FakeBossRecruiter()
	adapter = BossRecruiterAutomationPlatform(recruiter)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
		limit=1,
	)

	assert report.events[0].action == PlatformAction.EXCHANGE_CONTACT.value
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert recruiter.exchanges == [(101, 2)]


def test_boss_adapter_risk_warning_uses_registry_text() -> None:
	"""风控异常的平台响应原文可能带临时凭据，健康检查与事件原因只留登记表文案。"""
	from boss_agent_cli.api.client import AccountRiskError
	from boss_agent_cli.display import risk_error_message

	recruiter = MagicMock()
	recruiter.greet_list.side_effect = AccountRiskError("fixture-platform-response-token", is_cdp=True)
	adapter = BossRecruiterAutomationPlatform(recruiter)

	with pytest.raises(AccountRiskError):
		adapter.scan_conversations(["新招呼"], 5)

	warning = adapter.detect_safety_warning()
	assert warning == risk_error_message("ACCOUNT_RISK")
	assert "fixture-platform-response-token" not in warning


def test_adapter_factory_returns_real_platform_adapters() -> None:
	assert isinstance(build_automation_adapter("zhipin"), BossRecruiterAutomationPlatform)
	# 智联适配器已移除：旧名称落到通用的 mock 兜底，不再构造浏览器会话。
	assert isinstance(build_automation_adapter("zhilian"), MockRecruiterAutomationPlatform)


def test_schema_and_mcp_expose_agent_automation(tmp_path: Path) -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "schema"])
	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert "agent" in payload["data"]["commands"]
	assert payload["data"]["commands"]["agent"]["availability"]["recruiter_platforms"] == ["zhipin"]
	assert "CIRCUIT_BREAKER_OPEN" in payload["data"]["error_codes"]


def test_local_ai_null_content_falls_back_to_template_and_cycle_continues(tmp_path: Path, monkeypatch) -> None:
	"""#500：模型返回 content null 不再让整轮中断，排在后面的会话照常处理；上限跟随配置。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AutomationStore(tmp_path)
	ai_store = AIConfigStore(tmp_path)
	ai_store.save_config(ai_provider="vllm", ai_model="Qwen/Qwen3-8B", ai_max_tokens=2048)
	ai_store.save_api_key("local")
	text = "你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会"
	adapter = RecordingMockRecruiterAutomationPlatform(
		"zhipin",
		[_conversation(text, "候选人一"), _conversation(text, "候选人二")],
	)
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = {
		"choices": [
			{
				"index": 0,
				"finish_reason": "length",
				"message": {"role": "assistant", "content": None, "reasoning": "Okay"},
			}
		]
	}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response) as post:
		report = run_automation_cycle(
			adapter,
			store,
			AutomationConfig(reply_strategy=ReplyStrategy.LOCAL_AI),
			platform="zhipin",
			dry_run=True,
			limit=2,
		)

	assert len(report.events) == 2
	assert post.call_count == 2
	assert post.call_args.kwargs["json"]["max_tokens"] == 2048
	for event in report.events:
		assert "local ai unavailable" in event.reason
		assert "max_tokens=2048" in event.reason
		assert "template fallback" in event.reason
