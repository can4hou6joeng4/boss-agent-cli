# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

## [1.8.4] - 2026-04-20

### Changed
- **类型检查门禁升级为阻塞式**（#88）— mypy 12 baseline errors 全部清零，CI `typecheck` job 去掉 `continue-on-error`，新代码必须零 mypy 错误才能合入 master

### Fixed
- `api/browser_client.py` × 8：patchright 类属性加 `Any` 类型注解
- `auth/manager.py`：登录方法 `token` 变量显式声明为 `dict | None`
- `bridge/daemon.py`：Popen `kwargs` 显式声明为 `dict[str, Any]`
- `commands/chat_utils.py`：`RELATION_LABELS` key 类型放宽为 `object`
- `resume/models.py`：for 循环变量 `item` 改名 `ji_item` 避免类型混淆

## [1.8.3] - 2026-04-20

### Added
- 英文版贡献者指南 `CONTRIBUTING.en.md`（#84） — 对齐中文版全量章节，并明确说明 commit message 纯中文描述约定
- 静态类型检查接入（#86）— `mypy` 依赖 + 宽松基线配置 + `typecheck` CI 非阻塞 job；`output` / `config` / `hooks` 三个纯工具模块启用严格模式（`disallow_untyped_defs`）
- `docs/integrations/ai-models.md` 作为 ROADMAP v2.0 社区建设的英文贡献者入口被纳入索引

### Changed
- `CONTRIBUTING.md` 首行补 `CONTRIBUTING.en.md` 链接
- `CONTRIBUTING` 双语版增加 mypy 本地跑法提示
- `output.py` / `hooks.py` 补齐类型注解（`emit_success` / `emit_error` / `Logger.*` / `SyncHook.__init__` / `BailHook.__init__`）

### 测试覆盖率二次冲刺（#85）
- `commands/logout.py` 86% → 100%
- `commands/show.py` 85% → ~98%
- `commands/mark.py` 86% → ~95%
- `commands/me.py` 88% → 100%
- `match_score.py` 93% → 100%
- `pipeline_state.py` 93% → 100%
- 全量测试 **893 → 911**（+18）

### Fixed
- （无 bug 修复）

## [1.8.2] - 2026-04-20

### Added
- AI Provider 扩展 4 家，覆盖主流国内外聚合入口：
  - `openrouter` — Anthropic Claude 4.7 / OpenAI GPT-5 / Google Gemini 等全家桶聚合
  - `qwen` — 通义千问 DashScope OpenAI 兼容入口
  - `zhipu` — 智谱 GLM-4.6 开放平台
  - `siliconflow` — 硅基流动聚合推理
- 新建 `docs/integrations/ai-models.md` 推荐模型与入口表，给 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 / GLM-4.6 等最新模型最短接入路径

### Changed
- `ai config` 命令 `--provider` / `--model` 帮助文案同步最新 provider 列表
- `docs/agent-hosts.md` 索引补入 AI 模型入口文档链接
- `README.md` AI 命令表下方加推荐模型引用

### Fixed
- （无 bug 修复）

### 测试覆盖率冲刺（独立主题）
- `commands/display.py` 30% → **100%**（+21 测试）
- `commands/status.py` 73% → **95%**（+2 测试）
- `commands/ai_cmd.py` 77% → **84%**（+30 测试，剩余是 ctx.exit 后的防御性死代码不做硬刷）
- `tests/test_ai_config.py` 新增 4 条 provider base_url 断言
- 整体测试 **835 → 893**（+58），覆盖率 **85% → 88%**

## [1.8.1] - 2026-04-19

### Added
- `boss digest --format md [-o <path>]` — 每日摘要 Markdown 输出，可直接贴邮件/飞书发送；核心指标表 + 新匹配 / 待跟进 / 面试三段落，空数据写「暂无」占位
- `docs/integrations/cursor.md` — Cursor Composer Agent 接入示例（MCP 推荐 + `.cursor/rules` 兜底）
- `docs/integrations/windsurf.md` — Windsurf Cascade Agent 接入示例（MCP 推荐 + `.windsurfrules` 兜底）
- `docs/agent-hosts.md` 宿主索引表补入 Cursor / Windsurf 两条
- `.gitignore` 精准忽略本地专属的社区发帖草稿（`docs/blog/linuxdo-promo.md`）
- 协议服务 `boss_digest` 工具新增 `format` / `output` 参数
- `tests/test_digest_command.py` 新增 5 条 md 输出路径测试、`tests/test_mcp_server.py` 新增 2 条 `_build_args` 测试（828 → 835，+7）

### Changed
- ROADMAP v1.8.x「数据可视化」和「Agent 集成」分区各勾选一项完成
- `digest` 命令描述同步更新说明支持 JSON / Markdown 两种输出
- `tests/test_agent_host_examples.py` meta 测试覆盖新增两份集成示例

### Fixed
- （无 bug 修复）

## [1.8.0] - 2026-04-19

### Added
- `boss ai interview-prep <jd_text>` — 基于目标职位生成模拟面试题与准备建议，支持 `--resume` 参考简历、`--count` 控制题量；返回分类（技术/行为/情景）、参考回答框架、考察点、难度、高优先级准备项
- `boss ai chat-coach <chat_text>` — 基于聊天记录诊断沟通状态并给出下一步建议，支持 `--resume`、`--style` 偏好；输出阶段判断、招聘者意图、优劣势、可直发消息模板、需避免的雷区
- 协议服务新增 `boss_ai_interview_prep` / `boss_ai_chat_coach` 两个工具（41 → 43）
- Prompt 模板新增 `INTERVIEW_PREP_PROMPT` / `CHAT_COACH_PROMPT`

### Changed
- ROADMAP v1.8.x 智能能力分区两项勾选完成
- README / capability-matrix / SKILL 同步新增两条 AI 能力
- schema 中 `ai` 命令子命令列表由 6 → 8

### Fixed
- （无 bug 修复）

## [1.7.2] - 2026-04-19

### Added
- `docs/integrations/python-sdk.md` — Python SDK 直调集成样例（OpenAI + Anthropic 两套 ~150 行可运行代码），Agent 宿主索引表同步补入口
- README 中英文版均链接认可 LINUX DO 社区

### Changed
- 测试覆盖率大幅提升：总体 **80% → 84%**（+4 点）
  - `api/client.py` 63% → **97%**（+37 测试）
  - `commands/greet.py` 65% → **97%**（+8 测试）
  - `commands/export.py` 47% → **98%**（+13 测试）
  - `auth/manager.py` 74% → **96%**（+14 测试）
  - `auth/token_store.py` 81% → **100%**（+14 测试）
  - `commands/login.py` 83% → **100%**（+6 测试）
- 总测试数 **710 → 802**（+92）

### Fixed
- 移除误跟踪的 `.coverage` artifact 并扩展 `.gitignore` 忽略覆盖率文件
- Dependabot 批量升级 5 个 GitHub Actions 版本（`checkout`/`upload-pages-artifact`/`configure-pages`/`deploy-pages`/`gh-release`）

## [1.7.1] - 2026-04-17

### Added
- `boss schema --format openai-tools` 和 `--format anthropic-tools` — 一键导出 OpenAI Functions / Claude Tool Use 兼容的 JSON Schema，免手动转换即可喂给 SDK
- `boss stats --format html -o <path>` 输出自包含交互式漏斗报表（纯 CSS + SVG，无外部 CDN）
- 协议服务文档补齐传输层章节（stdio 当前支持 / SSE 规划中）和贡献指引
- Issue / PR 模板全面升级：bug_report 强制 doctor 输出和版本号，feature_request 新增贡献意愿字段，新增 documentation 专用模板，PR 模板新增 Closes 关联和 Breaking Change 声明
- 英文版说明补齐 Troubleshooting 章节（诊断清单 / 登录 / 浏览器 / 搜索 / 错误码 / 术语表），对齐中文版
- Codecov 覆盖率追踪接入，基线 80%，每次 PR 自动上报
- 开发容器（`.devcontainer/devcontainer.json`）支持 GitHub Codespaces 一键启动
- `ROADMAP.md` + 4 个 Issue（含 2 个 good-first-issue）招募外部贡献者
- 本地提交质量门禁 `.pre-commit-config.yaml`（ruff + 通用 hooks）

### Changed
- CI 支持 `workflow_dispatch` 手动触发
- 协议服务 `awesome-mcp-servers` 投稿材料和多语言投稿模板整理

### Fixed
- 清理 `tests/test_qr_login.py` 未使用 import，修复 ruff lint 失败

## [1.7.0] - 2026-04-17

### Added
- 新增 `boss ai reply` 命令 — 基于招聘者消息生成 2-3 条回复草稿，支持简历参考和语气偏好
- 新增 `boss stats` 命令 — 投递转化漏斗统计，只读聚合打招呼/投递/候选池/监控数据
- 协议服务扩展：新增 18 个工具覆盖简历管理、智能能力、状态管理增删（23→41）
- 元测试：main.py 注册命令与 SCHEMA_DATA 对齐的防漂移校验
- 本地提交质量门禁：新增 `.pre-commit-config.yaml`（ruff check + 通用 hooks）
- 新增英文版 README（`README.en.md`），README 首屏加入语言切换
- 能力矩阵文档补齐简历管理、智能能力、数据洞察三大分区
- README 加入 CHANGELOG 导航入口

### Changed
- 能力矩阵命令总数对齐到当前状态

### Fixed
- 清理 `tests/test_qr_login.py` 未使用 import，修复 ruff lint 失败

## [1.6.0] - 2026-04-14

### Added
- 新增 `resume` 命令组 — 本地简历管理，支持初始化、列表、查看、编辑、删除、导出、导入、克隆、版本比对
- 新增 `ai` 命令组 — 智能简历优化，支持配置、JD 分析、润色、优化、建议五个子命令，覆盖 OpenAI/Claude/Gemini/通义千问/DeepSeek 多模型
- 简历数据模型、本地存储、模板渲染、多格式导出（HTML/Markdown/PDF/DOCX）
- AI 服务模块：多模型配置、密钥加密存储、提示词模板、对话补全
- 模型上下文协议服务从十一个工具扩展至二十三个，覆盖全部命令

### Changed
- 协议服务文档按功能分类并补齐全部工具说明
- 能力矩阵补齐配置管理和缓存清理命令

## [1.5.0] - 2026-04-14

### Added
- 新增 `clean` 命令 — 清理过期缓存和临时文件，支持预览和全量模式
- 模型上下文协议服务新增二十九个测试覆盖工具定义和参数构建

### Changed
- 收窄八处网络和解析模块的异常捕获为具体类型
- 统一调试协议默认地址为单一常量引用

## [1.4.0] - 2026-04-14

### Added
- 新增 `config` 命令组 — 查看、设置、重置配置项，支持类型自动推断
- 新增类型标记文件，下游项目可获得类型检查支持
- 新增版本查询选项，终端输入即可查看当前版本
- 缓存模块新增保存搜索、增量监控、投递记录、候选池四表扩展测试
- 浏览器桥接模块新增协议结构和客户端重试逻辑测试
- 测试数量从三百六十八增至四百二十七

### Changed
- 安装指引统一覆盖三种安装方式
- 能力矩阵文档按功能分类并补齐全部命令
- 清理仓库内部开发计划文档

## [1.3.0] - 2026-04-13

### Added
- 新增 `watch` 命令组 — 保存搜索条件并执行增量监控，自动标出新职位
- 新增 `pipeline` 命令 — 汇总沟通和面试数据，构建求职流水线全景视图
- 新增 `follow-up` 命令 — 筛选超时未推进的联系人，生成跟进提醒
- 新增 `apply` 命令 — 发起投递/立即沟通动作，幂等设计防止重复投递
- 新增 `shortlist` 命令组 — 管理职位候选池，支持添加/列表/移除
- 新增 `chat-summary` 命令 — 对沟通消息生成结构化摘要
- 新增 `preset` 命令组 — 管理可复用搜索预设，保存常用参数组合一键复用
- 新增 `digest` 命令 — 每日摘要，综合流水线、跟进、统计信息
- 搜索结果新增匹配分和匹配原因输出
- 快速入门文档和冒烟测试框架
- 多宿主集成示例文档（Claude Code / Codex / Shell Agent）
- 接口合约和错误码一致性测试
- 高风险链路测试覆盖补齐

### Changed
- 开源仓库元信息优化，补充英文摘要和变更记录

### Fixed
- 检测风控状态码并输出明确错误标识（此前静默失败）
- 调试协议模式复用用户上下文，规避自动化检测
- 扩展优先复用已有招聘页而非空白自动化页
- 职位详情快速通道失败时自动降级到浏览器通道（此前误报"职位不存在"）
- 搜索分页边界条件修正

## [1.2.0] - 2026-04-09

### Added
- CI 新增 ruff lint 质量门禁步骤
- CI 矩阵新增 Python 3.13 支持
- 新增 bridge/display/endpoints_loader/index_cache 四模块测试（123→182 用例）
- SKILL.md 命令速查表补全至 19 个命令

### Changed
- chat.py 拆分为 chat_export/chat_snapshot/chat_utils 三子模块（655→227 行）
- 浏览器超时从裸数字提取为命名常量
- search_filters 异常捕获从 Exception 收窄为具体类型
- client.py 根据运行平台动态设置请求头
- daemon.py 文件句柄改为 with ���句防泄漏
- 安装命令改为从 GitHub 源码安装

### Fixed
- CLAUDE.md 缩进规范、模块索引、技术栈、架构图与代码对齐
- README 配置文档补全 cdp_url/export_dir 字段
- .gitignore 排除 .trellis/.agents 目录

## [1.1.0] - 2026-04-03

### Added
- 新增 `boss me` 命令 — 获取当前登录用户的基本信息、简历、求职期望、投递记录
- 跨平台 Agent Skill 体系 — 支持 Codex / Claude Code / Gemini CLI / OpenCode / OpenClaw
- `.agents/skills/` symlink 供 Codex / OpenCode 发现 skill
- pyproject.toml 补全 authors、keywords、classifiers、urls 元数据

### Fixed
- 修复 `boss me` 命令 AuthManager 路径拼接和 emit_error 参数问题
- 消息模板标准化 — hints 补全 + 参数引用修正 + recovery_action 统一

### Changed
- SKILL.md 重构为 AgentSkills 标准格式
- skill 目录从 `skills/SKILL.md` 迁移到 `skills/boss-agent-cli/SKILL.md`

## [0.1.0] - 2026-03-20

### Added
- 核心 CLI 框架（Click）+ JSON 信封输出协议
- `boss login` — patchright 反检测浏览器扫码登录 + 本地浏览器 Cookie 自动提取
- `boss status` — 检查登录态
- `boss search` — 职位搜索（支持城市 / 薪资 / 经验 / 学历 / 规模筛选）
- `boss search --welfare` — 福利精准筛选（双休、五险一金等，逗号分隔 AND 逻辑，自动翻页）
- `boss recommend` — 基于简历的个性化职位推荐
- `boss detail` — 职位完整详情（描述、地址、招聘者信息）
- `boss greet` — 向招聘者打招呼
- `boss batch-greet` — 批量打招呼（上限 10，支持 dry-run 预览）
- `boss export` — 导出搜索结果为 CSV / JSON
- `boss cities` — 列出 40 个支持城市
- `boss schema` — 工具能力自描述 JSON（Agent 调用入口）
- Token 加密存储（Fernet + PBKDF2 机器绑定密钥）
- SQLite WAL 缓存（搜索历史 100 条上限 + 已打招呼记录）
- 高斯分布请求延迟 + 指数退避 403 重试
- GitHub Actions CI（多 OS + 多 Python 版本）
