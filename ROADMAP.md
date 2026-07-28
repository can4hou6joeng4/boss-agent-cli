# Roadmap

本文档记录 boss-agent-cli 的中长期规划。欢迎对任何方向提 Issue 或 PR。

## 已发布

- ✅ v1.18.0（2026-07-29）：可用 Docker / Compose 镜像与 CI 构建门禁 + MCP schema 契约修复与 `mcp_server` 拆分 + 离线 evals 进 P0 + ruff 规则钉死与依赖刷新（rich 15）
- ✅ v1.17.0（2026-07-27）：`boss favorites list/sync` 职位收藏同步 + 受限 Research Mode 的可恢复 crawl 工作流 + 实习岗位类型字段与筛选映射修复
- ✅ v1.16.0（2026-07-17）：显式 `operating_mode` 双模式契约（`assisted` / `research`）+ 合规护栏升级为不可变能力策略注册表，CLI / schema / MCP 过滤从同一真源派生
- ✅ v1.15.0（2026-07-14）：`boss ai cover-letter` 求职信草稿 + 死配置键与死码清理 + 智联页面域名校验加固（精确 hostname 匹配）
- ✅ v1.14.0（2026-06-25）：shortlist 本地标签 / 备注 / 离线对比 + `ai fit` / `ai suggest-keywords` / `ai resume-optimize` + 转向 MCP-first + 搜索 `match_score`
- ✅ v1.13.x（2026-06-11 ~ 06-16）：platforms 能力状态语义 + login 链路错误码补齐 + welfare 详情本地缓存 + 双语 README 重构为导航型；Agent Skill 迁出至独立仓库 [boss-skill](https://github.com/can4hou6joeng4/boss-skill)（Breaking Change）
- ✅ v1.12.0（2026-06-09）：MCP 三种传输（stdio / SSE / HTTP streaming）+ `boss_export` + 51job / `qiancheng` 占位适配器
- ✅ v1.11.0（2026-04-23）：招聘者模式（`--role recruiter`）全套 CLI 命令组 + `BossRecruiterClient` 双通道客户端 + `RecruiterPlatform` 抽象
- ✅ v1.10.x（2026-04-21）：Platform 抽象落地 —— ABC + 注册表 + `--platform` 全局选项 + 20 个命令全量迁移，`commands/` 下不再直接引用 `BossClient`
- ✅ v1.9.x（2026-04-20）：mypy 严格模式全量接入（66/66 业务模块）+ Python 嵌入 API + `py.typed` 类型导出
- ✅ v1.8.x（2026-04-19 ~ 04-20）：AI 沟通与面试扩展（`ai interview-prep` / `ai chat-coach`）+ Cursor / Windsurf 接入 + 英文贡献指南
- ✅ v1.7.0（2026-04-17）：聊天回复草稿 + 投递漏斗

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## 🎯 近期（当前主线）

### 数据可视化
- [x] `boss stats --format html` 输出交互式漏斗报表（v1.7.1）
- [x] `boss digest --format md` 每日摘要邮件/飞书可直接发送（v1.8.1）
- [x] codecov badge 集成到 README（v1.7.1）

### Agent 集成
- [x] MCP 服务支持 HTTP streaming / SSE / stdio 三种传输（2026-04-27，PR #160）
- [x] Codex / Cursor / Windsurf 专用接入示例（v1.8.1，docs/integrations/ 全覆盖）
- [x] OpenAI Functions 格式导出 `boss schema --format openai-tools`（v1.7.1）

### 智能能力
- [x] `boss ai chat-coach` — 基于聊天记录给出沟通技巧建议（v1.8.0）
- [x] `boss ai interview-prep` — 基于 JD 生成模拟面试题（v1.8.0）
- [x] 支持 Claude 4.7 / GPT-5 最新模型（v1.8.2，provider 扩至 openrouter/qwen/zhipu/siliconflow）

## 🔮 中期（v2.0）

### 治理与合规
- [x] 默认低风险辅助模式：敏感命令默认阻断并交接回平台官网（ADR 0001）
- [x] 显式 `operating_mode` 双模式契约：`assisted` / `research` 单一真源驱动 CLI、schema 与 MCP 过滤（v1.16.0，ADR 0002）
- [x] 受限 Research Mode 采集：固定请求 / 详情 / 墙钟 / 重试预算 + SQLite checkpoint + 停止开关（v1.17.0）

### 架构演进
- [x] mypy 严格模式全量接入 — **100% 完成**（66/66 业务模块全部 `disallow_untyped_defs + disallow_any_generics + warn_return_any` 严格化，v1.9.1）
- [x] 类型签名导出到 `stubs/`，供下游 IDE 使用（v1.8.6，py.typed + canonical `__all__` + 16 条契约测试）
- [ ] Bridge 协议从 HTTP/WS 升级为 gRPC — 调研已完成（Issue #96 · [docs/research/bridge-grpc.md](docs/research/bridge-grpc.md)），**结论：暂不迁移**（localhost 单用户场景无性能收益 + MV3 扩展兼容性风险高 + 依赖膨胀 8MB）。重启调研的 5 个触发条件已明确

### 生态扩展
- [ ] Web UI（React + Tailwind），适合非 Agent 用户
- [ ] 浏览器扩展深度集成 BOSS 直聘原生页面
- [ ] 多平台支持：拉勾 / 智联 / 猎聘适配器 — API 调研已全部完成（Issue #90 已闭环 · [docs/research/platforms/](docs/research/platforms/)）。结论：**智联候选者侧已接入**（只读 search/detail/recommend/user_info + 写操作 greet/apply，详见下方 Week 2-3 子项）；拉勾、猎聘经评估不建议接入；51job 仍在 research backlog。
  - [x] Week 1a：Platform ABC 骨架 + BossPlatform adapter（#129，零行为变化）
  - [x] Week 1b：`--platform` 全局 CLI 选项 + `get_platform_instance` helper + schema 暴露 current_platform
  - [x] Week 1c：命令层全量迁移到 Platform 接口（**20 个命令**：greet / apply / batch-greet / interviews / detail / show / me / recommend / chat / chatmsg / mark / exchange / pipeline / digest / search / export / chat_summary / history / status / watch）
  - [x] Week 1d：ZhilianPlatform stub 接入注册表（抽象自证，包络适配完整实现，P0/P1/P2 暂 NotImplementedError）
  - [x] Week 2：ZhilianPlatform 只读实现（search / detail / recommend / user_info）
  - [x] Week 3：ZhilianPlatform 写操作（greet / apply）+ 文档 + MCP 适配
  - [x] Week 4：招聘者侧能力评估完成 → **暂不接入**（接入条件 0/4 满足，保留 RecruiterPlatform 骨架待社区信号重启；详见 `docs/research/platforms/zhaopin-recruiter-evaluation.md`）
  - [ ] 51job / 前程无忧：占位适配器已落地（全量能力返回稳定 `NOT_SUPPORTED` 包络，v1.12.0–v1.13.0），但真实接口仍在 research backlog——候选者侧只读入口和脱敏测试样本明确前不进入真实运行路径（详见 `docs/research/platforms/51job.md`）

### 社区建设
- [ ] 更完整的中文 + 英文视频 demo / 发布素材（当前已有 `demo/demo-zh.gif` / `demo/demo-en.gif` + 对应 `demo/demo-zh.tape` / `demo/demo-en.tape` 终端演示）
- [x] [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) 收录（PR #4992，2026-04-26 合并）
- [x] [awesome-agents](https://github.com/kyrolabs/awesome-agents) PR #423 已闭环——由 bot 无 review 直接关闭，同分区存在批量静默关单现象，结论为暂不重投（详见 `docs/marketing/awesome-submissions.md`）
- [ ] 视时机决定是否进入 [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)（该仓库只接受 Web UI issue 表单，禁止 gh CLI）
- [x] 贡献者指南英文版（`CONTRIBUTING.en.md`，v1.8.3）

## 💡 长期愿景

**让 AI Agent 真正成为求职助理**，而不是工具调用生成器：
- Agent 自主完成"搜索 → 筛选 → 打招呼 → 跟进 → 面试准备"全链路
- 用户只需描述期望（"我想找 30K 以上的远程 Python 岗位"），Agent 自动执行
- 数据完全本地化，隐私和合规第一

## 🤝 如何参与

1. 在 Issue 标 `good first issue` / `help wanted` 的任务里认领
2. 对某个方向有兴趣 → 发 Issue 讨论设计
3. 发现 bug / 文档错误 → 直接发 PR
4. 不写代码也能贡献：测试报告、使用场景、翻译

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

> Roadmap 本身是活文档，每次 minor 版本发布时更新。
