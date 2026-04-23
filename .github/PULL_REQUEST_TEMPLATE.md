<!--
感谢你的贡献！请按以下模板填写。
Thanks for contributing! Please fill out the template below.
-->

## 关联 Issue / Related Issue

<!-- 使用 Closes / Fixes 关键字可自动关闭 Issue -->
<!-- e.g. Closes #123 -->

Closes #

## 变更说明 / Summary

<!-- 简要描述本次变更的内容、背景、动机 -->

## 变更类型 / Type

- [ ] feat: 新功能
- [ ] fix: Bug 修复
- [ ] refactor: 重构（不改变外部行为）
- [ ] perf: 性能优化
- [ ] docs: 文档
- [ ] test: 测试
- [ ] chore: 构建/依赖/配置
- [ ] ci: CI 工作流

## Breaking Change

- [ ] 本 PR **不包含** 破坏性变更
- [ ] 本 PR **包含** 破坏性变更（请在下方说明迁移路径）

<!-- 如果勾选"包含"，请描述：哪些命令 / 信封字段 / 错误码发生变化，用户需要如何迁移 -->

## 截图 / Screenshot

<!-- 如果包含 UI / HTML / Terminal 输出变化，粘贴 before/after 对比 -->

## 测试 / Test Plan

- [ ] `uv run pytest tests/ -q` 全部通过
- [ ] `uv run ruff check src/ tests/` 无报错
- [ ] 新增/修改的功能已有对应测试覆盖
- [ ] 本地跑过涉及命令（如有可粘贴输出）

## 文档与契约 / Docs & Contracts

- [ ] 如新增命令：已更新 `src/boss_agent_cli/commands/schema.py`
- [ ] 如变更 CLI 行为：已更新 `README.md` 和 `docs/capability-matrix.md`
- [ ] 如新增 MCP 工具：已更新 `mcp-server/server.py` 和 `mcp-server/README.md`
- [ ] 如新增错误码：已添加到 `schema.py` 的 `error_codes`
- [ ] 已更新 `CHANGELOG.md` 的 `[Unreleased]` 段落
- [ ] 如变更 Agent 主卖点或安装路径：已同步检查 `SKILL.md` / PyPI 版本 / GitHub Release 说明

## 安全与规范 / Safety & Convention

- [ ] commit message 格式: `type: 中文描述`（或英文等价）
- [ ] 无敏感信息（Token / 密码 / Cookie / 真实账号）
- [ ] 无新增外部 CDN / script 依赖（HTML 报表类特别注意）
