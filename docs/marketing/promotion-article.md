# 让 AI Agent 辅助整理职位：boss-agent-cli 开源实践

> 双休、五险一金、年终奖——这些福利条件你还在一个个手动翻看 BOSS 直聘？

## 痛点

在 BOSS 直聘找工作，你可能经历过这些：

1. **福利信息碎片化**——"双休"可能写在标签里，也可能藏在职位描述的最后一段。你搜 100 个职位，得点进去一个个看
2. **重复劳动**——每天刷推荐、搜关键词、看详情、整理候选岗位，动作完全一样
3. **信息不对称**——招聘者的活跃度、公司的真实福利，都需要多次操作才能获取到

如果有个工具能帮你：**搜索职位时自动过滤「双休+五险一金」，查看详情，加入本地候选池，导出整理好的 CSV**——而投递和沟通仍由你在官方页面手动完成呢？

## boss-agent-cli 是什么

一句话：**专为 AI Agent 设计的 BOSS 直聘求职 CLI 工具**。

```bash
# 搜索广州的 Golang 职位，要求双休+五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 查看详情并加入本地候选池
boss detail <security_id> --job-id <job_id>
boss shortlist add <security_id> <job_id>
```

所有输出都是结构化 JSON，AI Agent 可以直接解析执行。也可以当普通 CLI 用，终端会渲染成漂亮的表格。

## 核心特色：福利精准筛选

这是目前市面上找不到的功能。`--welfare "双休,五险一金"` 的工作原理：

1. 先检查职位的福利标签（`welfareList`）
2. 标签没命中？按需读取详情，在职位描述里继续匹配
3. 多条件用逗号分隔，AND 逻辑——所有条件都满足才返回
4. 在低请求量和节流边界内补充翻页，减少漏掉匹配结果

每个返回结果都带 `welfare_match` 字段，告诉你是标签命中还是描述命中。

## 技术方案：为什么不用 Selenium

市面上类似工具大多用 Selenium 或 Puppeteer。我们选了不同的路：

**默认低风险边界**：项目默认启用低风险辅助模式，阻断打招呼、投递、联系方式交换、聊天记录读取、招聘者候选人处理等敏感链路。

**双通道架构**：
- 用户主动触发的职位搜索/详情 → CLI 输出结构化 JSON
- 投递、沟通、候选人处理 → 回到官方页面手动完成

**CDP / 浏览器能力**：仅作为登录兼容路径保留，不用于规避平台风控或重试被平台拦截的操作。

**登录兼容路径**：
1. 优先复用用户本地登录态
2. 需要时连接用户主动启动的浏览器调试端口
3. 最后回到用户扫码登录

## AI Agent 集成

这才是这个项目的真正价值。所有命令输出 JSON 信封：

```json
{
  "ok": true,
  "command": "search",
  "data": [...],
  "hints": {"next_actions": ["boss detail <security_id>"]}
}
```

Agent 调用 `boss schema` 一次，就能理解命令参数、返回格式和默认低风险阻断边界。`hints.next_actions` 告诉 Agent 下一步该做什么。错误响应包含 `recovery_action`，Agent 可以据此提示用户恢复登录或调整输入。

### Claude Code / MCP 一键集成

```bash
claude mcp add boss-agent -- uvx --from boss-agent-cli[mcp] boss-mcp
```

接入后 Agent 自动获得调用能力。你只需要说：

> "帮我搜广州的 Golang 职位，要双休五险一金，找到合适的加入候选池"

Agent 会自动执行 `status → search → detail → shortlist` 的低风险本地整理链路。

## 安装

```bash
# 安装 CLI
uv tool install boss-agent-cli

# 安装浏览器引擎
patchright install chromium

# 验证
boss doctor
boss login
boss search "你的目标职位"
```

## 数据安全

- Token 使用 Fernet 对称加密 + PBKDF2 机器绑定密钥，换机器无法解密
- 所有数据存储在本地 `~/.boss-agent/`，不上传任何服务器
- 开源代码，可以自行审计

## 写在最后

这个项目的初衷很简单：**找工作已经够累了，不应该把时间浪费在重复操作上**。

如果你觉得有用，欢迎 [star 支持](https://github.com/can4hou6joeng4/boss-agent-cli)。Issue 和 PR 都欢迎。

---

GitHub: https://github.com/can4hou6joeng4/boss-agent-cli
License: MIT
