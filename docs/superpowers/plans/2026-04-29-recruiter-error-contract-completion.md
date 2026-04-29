# Recruiter Error Contract Completion Plan

**Date:** 2026-04-29
**Branch:** `feat/recruiter-error-contract-completion`
**Goal:** 收平 recruiter 侧剩余没有接入 schema error contract 的两个命令，让平台错误继续保留真实 `code/message`，同时补齐 `recoverable` 与 `recovery_action`。

## Scope

### In Scope

- `src/boss_agent_cli/commands/recruiter/candidates.py`
- `src/boss_agent_cli/commands/recruiter/reply.py`
- `tests/test_recruiter_commands.py`

### Out Of Scope

- recruiter 侧新增分页能力
- recruiter 侧成功路径数据结构调整
- candidate 侧其他命令重构

## Task 1: `recruiter-candidates` 错误契约统一

- 平台返回错误时，继续使用 `parse_error()` 的真实 `code/message`
- 同步从 `schema.error_codes` 派生 `recoverable` 与 `recovery_action`

## Task 2: `recruiter-reply` 错误契约统一

- 平台返回错误时，继续使用 `parse_error()` 的真实 `code/message`
- 同步从 `schema.error_codes` 派生 `recoverable` 与 `recovery_action`

## Task 3: 验证

至少执行：

- `env UV_CACHE_DIR=/tmp/boss-agent-cli-uv-cache uv run pytest -q tests/test_recruiter_commands.py`
- 如需要，再补跑：
  - `env UV_CACHE_DIR=/tmp/boss-agent-cli-uv-cache uv run pytest -q`

## Delivery Notes

- 保持为一个可独立审查的小 PR
- commit message 遵循：`fix: 中文描述`
