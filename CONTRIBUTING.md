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

## 编码规范

- Python 源码缩进使用 **tab**。
- `pyproject.toml` 中的 `indent-width = 4` 表示格式工具的视觉宽度，不表示改为空格缩进。
- Python >= 3.10，使用 `X | Y` 联合类型。
- 命令输出必须保持 JSON 信封契约：stdout 只输出 Agent 可读 JSON，stderr 输出日志和进度。
- commit message：`type: 中文描述`（feat / fix / refactor / docs / test / chore / ci）。
- 类型检查：`uv run mypy src/boss_agent_cli`，CI 阻塞式门禁，新代码必须零 mypy 错误。

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

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feat/your-feature`
3. 编写测试 → 实现功能 → 确保测试通过
4. 提交并推送
5. 创建 Pull Request

## 维护者文档

- [Release Checklist](docs/maintainer/release-checklist.md)
- [Labels And Triage](docs/maintainer/labels.md)
- [Branch Protection](docs/maintainer/branch-protection.md)

## 添加新命令

1. 在 `src/boss_agent_cli/commands/` 下新建文件
2. 在 `main.py` 中注册命令
3. 在 `schema.py` 中添加命令描述
4. 在 `tests/test_commands.py` 中添加测试
5. 更新 `skills/boss-agent-cli/SKILL.md`（命令速查表）
6. 更新 `AGENTS.md`（CLI 不变量契约中的命令数）
7. 更新 `README.md`（命令参考表）
8. 更新对应模块的 `CLAUDE.md`
