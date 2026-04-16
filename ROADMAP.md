# Roadmap

本文档记录 boss-agent-cli 的中长期规划。欢迎对任何方向提 Issue 或 PR。

## 已发布

- ✅ v1.7.0 (2026-04-17)：聊天回复草稿 + 投递漏斗 + 协议服务扩展至 41 工具

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## 🎯 近期（v1.8.x）

### 数据可视化
- [ ] `boss stats --format html` 输出交互式漏斗报表
- [ ] `boss digest --format md` 每日摘要邮件/飞书可直接发送
- [ ] codecov badge 集成到 README

### Agent 集成
- [ ] MCP 服务支持 HTTP streaming（stdio 已支持）
- [ ] Codex / Cursor / Windsurf 专用接入示例
- [ ] OpenAI Functions 格式导出 `boss schema --format openai-tools`

### 智能能力
- [ ] `boss ai chat-coach` — 基于聊天记录给出沟通技巧建议
- [ ] `boss ai interview-prep` — 基于 JD 生成模拟面试题
- [ ] 支持 Claude 4.7 / GPT-5 最新模型

## 🔮 中期（v2.0）

### 架构演进
- [ ] mypy 严格模式全量接入
- [ ] 类型签名导出到 `stubs/`，供下游 IDE 使用
- [ ] Bridge 协议从 HTTP/WS 升级为 gRPC

### 生态扩展
- [ ] Web UI（React + Tailwind），适合非 Agent 用户
- [ ] 浏览器扩展深度集成 BOSS 直聘原生页面
- [ ] 多平台支持：拉勾 / 智联 / 猎聘适配器

### 社区建设
- [ ] 中文 + 英文视频 demo
- [ ] 提交到 [awesome-agents](https://github.com/kyrolabs/awesome-agents) / [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
- [ ] 贡献者指南英文版（`CONTRIBUTING.en.md`）

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
