# Contributing

感谢你对 boss-agent-cli 的关注！English version: [CONTRIBUTING.en.md](CONTRIBUTING.en.md)

首次贡献前请先完成 [快速上手](docs/getting-started.md) 中的本地自检与开发者验证。

## 开发环境

```bash
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run pytest tests/ -v

# 启用本地提交质量门禁（推荐）
uv run pre-commit install
```

Python **≥ 3.10** 是最低要求。项目使用 [`uv`](https://github.com/astral-sh/uv) 管理依赖，`uv sync --all-extras` 会在本地 `.venv` 中安装运行时和开发依赖。

## 编码规范

- Python 源码缩进使用 **tab**。
- `pyproject.toml` 中的 `indent-width = 4` 表示格式工具的视觉宽度，不表示改为空格缩进。
- Python >= 3.10，使用 `X | Y` 联合类型。
- 命令输出必须保持 JSON 信封契约：stdout 只输出 Agent 可读 JSON，stderr 输出日志和进度。
- commit message：`type: 中文描述`（feat / fix / refactor / docs / test / chore / ci）。
- 类型检查：`uv run mypy src/boss_agent_cli`，CI 阻塞式门禁，新代码必须零 mypy 错误。
  - ✅ `feat: 新增配置管理命令`
  - ❌ `feat: add config command`（英文描述）
  - ❌ `feat: 新增 config 命令`（中英混杂）
  - 不要添加 `Co-authored-by` 尾注或任何 AI 署名行

## 本地验证

代码改动提交前尽量运行完整矩阵：

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/boss_agent_cli
uv run boss --help
uv run boss schema --format native
```

文档改动至少运行：

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

## 提交流程

1. **Fork** 本仓库并 clone 到本地。
2. 从 `master` 创建功能分支：`git checkout -b feat/your-feature`。
3. **先写测试**：写失败的测试，再写实现，再跑全套。
4. **本地 lint + 测试**：
   ```bash
   uv run ruff check src/ tests/ mcp-server/
   uv run pytest tests/ -q
   ```
5. **原子提交**：每个 commit 只做一件事。
6. **Push** 并向 `master` 发起 Pull Request。
7. **CI 全绿**才能合并：4 个 Python 版本（3.10–3.13）跑测试，加 lint / typecheck / docs / 安全扫描。

维护者会使用 squash merge，所以最终 squash 标题也要遵守上面的 commit 格式。

## 输出契约（不可破坏）

每个命令必须向 **stdout** 输出 JSON 信封：

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "search",
  "data": [...],
  "pagination": {...},
  "error": null,
  "hints": {...}
}
```

- `stdout` 只放 JSON，不要直接 `print()` 到 stdout。
- `stderr` 放日志和进度信息（受 `--log-level` 控制）。
- `exit 0` 表示成功（`ok=true`）。
- `exit 1` 表示失败（`ok=false`）。

出错时信封必须包含 `error.code`、`error.recoverable` 和 `error.recovery_action`。可用错误码见 `src/boss_agent_cli/commands/schema.py` 中 `SCHEMA_DATA["error_codes"]`（约 line 855）。

## 测试理念

- **鼓励 TDD**：先写测试再写实现。CI 覆盖率在 [Codecov](https://codecov.io/gh/can4hou6joeng4/boss-agent-cli) 追踪，基线 80%。
- **Mock 外部 I/O**：`AuthManager`、`BossClient`、`CacheStore`、`AIService` 是 mock 边界，测试不应真正调用 BOSS 直聘 API。
- **错误路径对等**：每条成功路径至少对应一条错误路径测试（认证过期、限流、参数非法等）。

## 维护者文档

- [Release Checklist](docs/maintainer/release-checklist.md)
- [Labels And Triage](docs/maintainer/labels.md)
- [Branch Protection](docs/maintainer/branch-protection.md)

## 添加新命令

1. 在 `src/boss_agent_cli/commands/` 下新建文件
2. 在 `main.py` 中注册命令
3. 在 `schema.py`（`SCHEMA_DATA["commands"]`）中添加命令描述
4. 在 `tests/test_commands.py` 或按命令名新建测试文件
5. 更新 `skills/boss-agent-cli/SKILL.md`（命令速查表）
6. 更新 `AGENTS.md`（CLI 不变量契约中的命令数）
7. 更新 `README.md` 和 `README.en.md`（命令参考表）
8. 更新对应模块的 `CLAUDE.md`
9. 如果命令对 Agent 通过 MCP 调用有用，还需在 `src/boss_agent_cli/mcp_server.py` 的 `TOOLS` 列表加 Tool 定义、在 `_build_args` 函数加分支（`mcp-server/server.py` 是 thin wrapper，会自动 re-export）

## 提交 Issue

请在 `.github/ISSUE_TEMPLATE/` 下选择对应模板：

- **bug_report**：附上 `boss doctor` 输出和版本号
- **feature_request**：描述使用场景和期望行为
- **documentation**：错别字、缺失文档、过时示例

## 非代码贡献

不写代码也能帮忙：

- 翻译改进（如 `README.en.md` 润色）
- 带复现步骤的 bug 报告
- 在新的 Agent 宿主中编写使用示例（见 `docs/integrations/`）
- 不同机器 / 系统 / Chrome 版本的性能测试结果

## 有问题？

欢迎在 [Discussions](https://github.com/can4hou6joeng4/boss-agent-cli/discussions) 发帖，或在相关 [Issue](https://github.com/can4hou6joeng4/boss-agent-cli/issues) 下留言。
