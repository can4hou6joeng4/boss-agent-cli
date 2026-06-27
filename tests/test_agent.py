"""Agent 层单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from boss_agent_cli.agent.config import AgentConfig
from boss_agent_cli.agent.orchestrator import AgentOrchestrator
from boss_agent_cli.agent.toolkit import AgentToolkit
from boss_agent_cli.agent.utils import parse_llm_json
from boss_agent_cli.main import cli
from boss_agent_cli.tools.filter_tools import FilterTools


def test_parse_llm_json_strips_markdown_fence():
	raw = '```json\n{"recommend": true}\n```'
	assert parse_llm_json(raw)["recommend"] is True


def test_filter_tools_rejects_outsourcing():
	ft = FilterTools(min_salary=10, exclude_outsourcing=True)
	job = {"title": "Java 开发", "company": "某外包科技公司", "salary": "20-30K", "city": "北京"}
	filtered, reason = ft.should_filter_job(job)
	assert filtered is True
	assert "外包" in reason


def test_filter_tools_rejects_low_salary():
	ft = FilterTools(min_salary=20)
	job = {"title": "开发", "company": "正规公司", "salary": "10-15K", "city": "北京"}
	filtered, reason = ft.should_filter_job(job)
	assert filtered is True
	assert "薪资" in reason


def test_filter_tools_accepts_good_job():
	ft = FilterTools(min_salary=15)
	job = {"title": "Python", "company": "正规互联网", "salary": "25-35K", "city": "上海"}
	filtered, _ = ft.should_filter_job(job)
	assert filtered is False


def test_agent_config_assets(tmp_path: Path):
	cfg = AgentConfig(tmp_path)
	cfg.configure_deepseek("sk-test", "deepseek-chat")
	cfg.save_assets(resume_path="/tmp/resume.pdf")
	assets = cfg.get_assets()
	assert assets["resume_path"] == "/tmp/resume.pdf"
	assert cfg.is_configured()


def test_toolkit_filter_jobs():
	platform = MagicMock()
	job_tools = MagicMock()
	chat_tools = MagicMock()
	filter_tools = FilterTools(min_salary=20)
	job_agent = MagicMock()
	chat_agent = MagicMock()
	agent_config = MagicMock()
	agent_config.get_assets.return_value = {}

	toolkit = AgentToolkit(
		platform, job_tools, chat_tools, filter_tools, job_agent, chat_agent, agent_config,
	)
	jobs = [
		{"title": "A", "company": "外包公司", "salary": "30K", "city": "北京"},
		{"title": "B", "company": "正规公司", "salary": "25-35K", "city": "北京"},
	]
	result = toolkit.execute("filter_jobs", {"jobs": jobs, "use_llm": False})
	assert result["ok"] is True
	assert result["kept_count"] == 1
	assert result["kept"][0]["title"] == "B"


def test_orchestrator_single_turn_no_tools():
	ai = MagicMock()
	ai.chat_completion.return_value = {
		"content": "任务完成",
		"tool_calls": [],
		"assistant_message": {"role": "assistant", "content": "任务完成"},
	}
	toolkit = MagicMock()
	toolkit.openai_tools.return_value = []

	orch = AgentOrchestrator(ai, toolkit, max_rounds=3)
	result = orch.run("测试目标")
	assert result["ok"] is True
	assert result["summary"] == "任务完成"
	ai.chat_completion.assert_called_once()


def test_orchestrator_executes_tool_then_replies():
	ai = MagicMock()
	toolkit = MagicMock()
	toolkit.openai_tools.return_value = [{"type": "function", "function": {"name": "search_jobs"}}]
	toolkit.execute.return_value = {"ok": True, "count": 0, "jobs": []}

	ai.chat_completion.side_effect = [
		{
			"content": None,
			"tool_calls": [{"id": "c1", "name": "search_jobs", "arguments": '{"query": "Python"}'}],
			"assistant_message": {
				"role": "assistant",
				"content": None,
				"tool_calls": [{
					"id": "c1",
					"type": "function",
					"function": {"name": "search_jobs", "arguments": '{"query": "Python"}'},
				}],
			},
		},
		{
			"content": "没有找到岗位",
			"tool_calls": [],
			"assistant_message": {"role": "assistant", "content": "没有找到岗位"},
		},
	]

	orch = AgentOrchestrator(ai, toolkit, max_rounds=5)
	result = orch.run("搜索 Python")
	assert result["ok"] is True
	assert "没有找到" in result["summary"]
	toolkit.execute.assert_called_once_with("search_jobs", {"query": "Python"})


def test_agent_cli_test_not_configured(tmp_path: Path):
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "test"])
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
