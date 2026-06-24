# 博客草稿：给 Agent 一个低风险求职工具箱

> 面向 V2EX / 掘金 / 思否的中文技术博客草稿，可按渠道再裁剪。

---

## 标题候选

- **A**：给 Claude / Cursor 接一个低风险求职 CLI
- **B**：boss-agent-cli：35 个顶层命令 + 35 个默认低风险 MCP 工具
- **C**：Agent First 的求职 CLI：搜索、筛选、整理候选岗位

推荐 A，能直接说明这是 Agent 接入工具，不暗示自动投递或自动沟通。

---

## 正文

### 1. 缘起：Agent 不适合直接操作招聘网页

用 Claude/Cursor 这些 Agent 处理重复工作很自然，但招聘平台不是普通网页表单：

- HTML scraper 容易随页面结构漂移失效
- Playwright 录制适合演示，不适合长期维护
- 命中平台风控时，继续换自动化通道重试是不该做的事

所以我把适合 Agent 的部分收敛成 CLI：**每个命令只向 stdout 输出 JSON 信封，Agent 解析结构化结果；敏感动作回到官方平台由用户手动完成**。

### 2. 项目速览

```bash
uv tool install boss-agent-cli
patchright install chromium
boss doctor
boss login
boss search "golang" --city 上海 --welfare "双休,五险一金"
boss detail <security_id> --job-id <job_id>
boss shortlist add <security_id> <job_id>
```

典型输出：

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "search",
  "data": { "items": [], "total": 0 },
  "pagination": { "page": 1, "page_size": 15 },
  "error": null,
  "hints": { "next_actions": ["boss detail <security_id>"] }
}
```

当前能力面以 `boss schema` 为准：35 个顶层命令，`hr` 下 9 个一级招聘者子命令，MCP 默认暴露 35 个低风险工具。

### 3. 三个设计决策

#### 3.1 低风险辅助模式默认开启

项目默认聚焦本地辅助、只读优先、用户主动触发。不自动打招呼、不自动投递、不批量触达、不读取候选人个人数据链路；相关命令默认返回 `COMPLIANCE_BLOCKED`，并提示回到官方页面手动完成。

#### 3.2 福利筛选尽量用本地和低请求量补充

`--welfare "双休,五险一金"` 会先看职位卡片已有福利字段，必要时再读取详情补充判断，最终按 AND 逻辑过滤。这个功能的价值在于减少人工翻看，而不是扩大请求量或绕过平台限制。

#### 3.3 MCP 是主要 Agent 接入口

MCP 配置示例：

```json
{
  "mcpServers": {
    "boss-agent": {
      "command": "uvx",
      "args": ["--from", "boss-agent-cli[mcp]", "boss-mcp"]
    }
  }
}
```

接入后可以让 Agent 做低风险链路，例如：搜索岗位、查看详情、加入本地候选池、生成面试准备材料。投递、打招呼、联系方式交换和招聘者候选人处理仍然回到官方平台。

### 4. 工程侧边界

- JSON 信封和 `boss schema` 是 Agent 契约源
- zhipin 已覆盖求职者链路；zhilian 支持候选者侧只读 + 本地辅助对等；qiancheng 仍是 `NOT_SUPPORTED` 占位
- CI 覆盖 Python 3.10 / 3.11 / 3.12 / 3.13、ruff、mypy、文档一致性和 CodeQL
- 不引入遥测、埋点或云同步；采用度量只看 PyPI 下载量和 GitHub Insights

### 5. 链接

- GitHub: https://github.com/can4hou6joeng4/boss-agent-cli
- PyPI: https://pypi.org/project/boss-agent-cli/
- Roadmap: https://github.com/can4hou6joeng4/boss-agent-cli/blob/master/ROADMAP.md

如果你也在找 Agent 开发的真实场景，欢迎 Star/Fork/提 Issue。MIT 开源，数据默认留在本机。

---

## 投稿渠道 Checklist

- [ ] V2EX `/go/programmer`
- [ ] 掘金（标签：Python, AI, CLI）
- [ ] 思否（标签：Python, 命令行）
- [ ] 少数派（技术类）
- [ ] LinuxDo
