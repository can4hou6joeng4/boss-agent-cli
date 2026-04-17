# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Added
- `boss stats --format html -o <path>` 输出自包含交互式漏斗报表（纯 CSS + SVG，无外部 CDN）
- 协议服务文档补齐传输层章节（stdio 当前支持 / SSE 规划中）和贡献指引

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
