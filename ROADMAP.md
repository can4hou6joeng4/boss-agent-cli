# Roadmap

本文档记录 boss-agent-cli 的中长期规划。欢迎对任何方向提 Issue 或 PR。

## 已发布

- ✅ v1.8.x（2026-04-20，patch 连发）：严格类型检查白名单 3 → 61（81% 覆盖）+ Python 嵌入 API + ai interview-prep / chat-coach + digest Markdown + Cursor/Windsurf 接入 + 4 家 AI 聚合入口 + 英文贡献指南
- ✅ v1.8.0 (2026-04-19)：AI 沟通与面试扩展（ai interview-prep / ai chat-coach）+ 协议服务扩展至 43 工具
- ✅ v1.7.0 (2026-04-17)：聊天回复草稿 + 投递漏斗 + 协议服务扩展至 41 工具

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## 🎯 近期（v1.8.x）

### 数据可视化
- [x] `boss stats --format html` 输出交互式漏斗报表（v1.7.1）
- [x] `boss digest --format md` 每日摘要邮件/飞书可直接发送（v1.8.1）
- [x] codecov badge 集成到 README（v1.7.1）

### Agent 集成
- [ ] MCP 服务支持 HTTP streaming（stdio 已支持）— Issue #48 外部贡献者认领中
- [x] Codex / Cursor / Windsurf 专用接入示例（v1.8.1，docs/integrations/ 全覆盖）
- [x] OpenAI Functions 格式导出 `boss schema --format openai-tools`（v1.7.1）

### 智能能力
- [x] `boss ai chat-coach` — 基于聊天记录给出沟通技巧建议（v1.8.0）
- [x] `boss ai interview-prep` — 基于 JD 生成模拟面试题（v1.8.0）
- [x] 支持 Claude 4.7 / GPT-5 最新模型（v1.8.2，provider 扩至 openrouter/qwen/zhipu/siliconflow）

## 🔮 中期（v2.0）

### 架构演进
- [x] mypy 严格模式全量接入 — **100% 完成**（66/66 业务模块全部 `disallow_untyped_defs + disallow_any_generics + warn_return_any` 严格化，v1.9.1）
- [x] 类型签名导出到 `stubs/`，供下游 IDE 使用（v1.8.6，py.typed + canonical `__all__` + 16 条契约测试）
- [ ] Bridge 协议从 HTTP/WS 升级为 gRPC — 先做调研（Issue #96）再启动实现

### 生态扩展
- [ ] Web UI（React + Tailwind），适合非 Agent 用户
- [ ] 浏览器扩展深度集成 BOSS 直聘原生页面
- [ ] 多平台支持：拉勾 / 智联 / 猎聘适配器 — 先做 API 调研（Issue #90，拉勾已完成 → [docs/research/platforms/lagou.md](docs/research/platforms/lagou.md) 结论「不建议接入」；智联 / 猎聘待认领）再启动实现

### 社区建设
- [ ] 中文 + 英文视频 demo
- [ ] 提交到 [awesome-agents](https://github.com/kyrolabs/awesome-agents) / [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
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
