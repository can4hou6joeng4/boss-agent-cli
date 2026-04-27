# 博客草稿：给 Claude 装上求职的手

> 面向 V2EX / 掘金 / 思否的中文技术博客草稿，可直接复制发布。

---

## 标题候选（A/B 测试）

- **A**：做了个 CLI 让 Claude 自动刷 BOSS 直聘
- **B**：开源了一个求职 Agent 工具：33 个顶层命令 + 7 个招聘者子命令 + 49 个 MCP tool，让 AI 替你找工作
- **C**：Agent First 的求职 CLI：从搜职位到 AI 回复草稿全链路

推荐 A，口语化最抓眼球。

---

## 正文

### 1. 缘起：为什么 Agent 需要专属 CLI

用 Claude/Cursor 这些 Agent 做过自动化的同学都知道，让 AI 操作一个有 UI 的网页有多痛苦：

- **HTML scraper** 选择器三天两头失效
- **Playwright 录制** 一次性很爽，跑批量就漂
- **API 逆向** 参数风控一上就歇菜

BOSS 直聘这种大流量平台更是重灾区。我花了几周时间把完整求职流程封装成了 CLI，核心设计原则只有一条：**每个命令输出结构化 JSON，Agent 直接 subprocess 调用、stdout 解析**。

### 2. 项目速览：`boss-agent-cli`

```bash
uv tool install boss-agent-cli==1.11.0
patchright install chromium
boss doctor  # 环境自检
boss login   # 四级降级登录，先试 Cookie，再试 CDP，再试 QR
boss search "golang" --city 上海 --welfare "双休,五险一金"
boss ai reply "请问什么时候方便聊一下？"
boss stats --days 30
```

输出全是 JSON 信封：

```json
{
  "ok": true,
  "schema_version": "1.0",
  "command": "search",
  "data": { "items": [...], "total": 42 },
  "pagination": { "page": 1, "page_size": 15 },
  "error": null,
  "hints": { "next_actions": ["boss show 1", "boss detail <sid>"] }
}
```

### 3. 三个技术决策

#### 3.1 四级降级登录

用户环境千差万别，单一登录方式一定翻车。按"快→慢"的顺序尝试：

1. **从本地 Chrome 提取 Cookie**（browser-cookie3）→ 秒级，免扫码
2. **CDP 连接用户 Chrome**（`--remote-debugging-port`）→ 真实指纹，自动生成 stoken
3. **httpx 直接拉 QR 码**在终端显示 → 不启动浏览器也能扫码
4. **patchright headless QR**（最后兜底）

4 条路径全跑一遍才报错，这是为什么 `boss login` 成功率接近 100%。

#### 3.2 --welfare 福利精准筛选

BOSS 直聘 API 不支持"双休 + 五险一金"这种复合筛选，必须二次过滤。难点在于职位卡片上的福利标签是**子集**——很多公司给了"双休"但没给"五险一金"，只查列表会漏。

方案：`ThreadPoolExecutor` 并行跑 3 个 detail 请求，每个都有完整 welfare 数组，命中全量 AND 筛选条件才保留。在共享 `RequestThrottle` 的保护下不会触发风控。

#### 3.3 MCP Server：49 个工具拉通 Claude Desktop

MCP（Model Context Protocol）是 Anthropic 推的标准，让任意 CLI/服务变成 Claude 可调用的工具。`mcp-server/server.py` 把当前 CLI 暴露成 49 个 MCP tool，除了求职链路，还覆盖招聘者侧操作和 AI 助手能力，stdio 协议直通 Claude Desktop。

配置 `~/Library/Application Support/Claude/claude_desktop_config.json`：

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

然后在 Claude Desktop 里直接说："帮我找上海 30K 以上的 Python 岗位，把头 5 个打上招呼"——Claude 会自己调用 `boss_search` → `boss_show` → `boss_greet` 完成全链路。

### 4. 可能更有意思的：AI 帮你聊 HR

`boss ai reply` 是本版本的新命令。招聘者发来消息，它基于你的简历和聊天上下文，生成 2-3 条不同风格的回复草稿：

```bash
$ boss ai reply "您什么时候方便聊一下？" --resume my-cv --tone 简洁专业
```

```json
{
  "intent_analysis": "招聘者希望确认初步沟通时间",
  "reply_drafts": [
    {
      "style": "简洁专业",
      "text": "您好，今晚 20:00-21:00 方便，请问您这边电话还是微信？",
      "suitable_when": "希望尽快推进"
    },
    {
      "style": "热情积极",
      "text": "非常期待！今晚 7 点后都可以，您看哪个时间段合适？",
      "suitable_when": "对岗位兴趣明确"
    }
  ],
  "key_points": ["明确时间段", "询问沟通方式"],
  "avoid": ["模糊承诺", "过度客套"]
}
```

### 5. 工程细节（给较真的同学）

- **测试**：pytest 1119 用例 / 覆盖率约 86% / 四个 Python 版本矩阵
- **防漂移**：元测试校验 main.py 注册命令与 schema 一致，新增命令不同步就挂 CI
- **质量门禁**：ruff check + pre-commit 本地钩子
- **发版**：SemVer + CHANGELOG + GitHub Release + PyPI 双通道
- **社区健康**：Code of Conduct + Security Policy + Issue/PR 模板 + Dependabot

### 6. 路线图

- `boss stats --format html` 交互式漏斗报表（**招募 Good First Issue**）
- MCP HTTP Streaming 支持
- `boss schema --format openai-tools` 兼容 OpenAI Functions 格式

### 7. 链接

- GitHub: https://github.com/can4hou6joeng4/boss-agent-cli
- PyPI: https://pypi.org/project/boss-agent-cli/
- Roadmap: https://github.com/can4hou6joeng4/boss-agent-cli/blob/master/ROADMAP.md

如果你也在被简历投递、跟进、回复这些事耗精力，或者在找 Agent 开发的真实项目练手，欢迎 Star/Fork/提 Issue。MIT 开源，所有数据本地存储，不上云，不分析，不外传。

---

## 投稿渠道 Checklist

- [ ] V2EX `/go/programmer`
- [ ] 掘金（标签：Python, AI, CLI）
- [ ] 思否（标签：Python, 命令行）
- [ ] 少数派（技术类）
- [ ] LinuxDo
