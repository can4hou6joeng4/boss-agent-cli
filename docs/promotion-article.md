# 让 AI Agent 自动帮你投简历：boss-agent-cli 开源实践

> 双休、五险一金、年终奖——这些福利条件你还在一个个手动翻看 BOSS 直聘？

## 痛点

在 BOSS 直聘找工作，你可能经历过这些：

1. **福利信息碎片化**——"双休"可能写在标签里，也可能藏在职位描述的最后一段。你搜 100 个职位，得点进去一个个看
2. **重复劳动**——每天刷推荐、搜关键词、看详情、打招呼，动作完全一样
3. **信息不对称**——招聘者的活跃度、公司的真实福利，都需要多次操作才能获取到

如果有个工具能帮你：**搜索职位时自动过滤「双休+五险一金」，批量打招呼，导出整理好的 CSV**——你只需要最终面试就好了呢？

## boss-agent-cli 是什么

一句话：**专为 AI Agent 设计的 BOSS 直聘求职 CLI 工具**。

```bash
# 搜索广州的 Golang 职位，要求双休+五险一金
boss search "Golang" --city 广州 --welfare "双休,五险一金"

# 获取个性化推荐
boss recommend

# 批量打招呼（先预览，再执行）
boss batch-greet "Python" --city 深圳 --dry-run
```

所有输出都是结构化 JSON，AI Agent 可以直接解析执行。也可以当普通 CLI 用，终端会渲染成漂亮的表格。

## 核心特色：福利精准筛选

这是目前市面上找不到的功能。`--welfare "双休,五险一金"` 的工作原理：

1. 先检查职位的福利标签（`welfareList`）
2. 标签没命中？自动调用详情接口，全文搜索职位描述
3. 多条件用逗号分隔，AND 逻辑——所有条件都满足才返回
4. 自动翻页（最多 5 页），不放过任何匹配结果

每个返回结果都带 `welfare_match` 字段，告诉你是标签命中还是描述命中。

## 技术方案：为什么不用 Selenium

市面上类似工具大多用 Selenium 或 Puppeteer。我们选了不同的路：

**反检测层**：使用 [patchright](https://github.com/nichochar/patchright)（Playwright 的反检测 fork），从二进制层面修补自动化标记。不是注入 JS 来伪装，而是让浏览器本身就不暴露自动化特征。

**双通道架构**：
- 高风险操作（搜索/打招呼）→ 浏览器通道（自动生成 stoken）
- 低风险操作（详情/状态）→ httpx 直连（毫秒级响应）

**CDP 模式**：可以连接你本地的 Chrome 浏览器（通过 DevTools Protocol），复用真实浏览器指纹和登录态。从根本上消除 stoken 过期问题。

**登录三级降级**：
1. 优先从本地浏览器提取 Cookie（免扫码）
2. CDP 模式自动探测
3. 最后才弹出扫码

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

Agent 调用 `boss schema` 一次，就能理解全部 19 个命令的参数和返回格式。`hints.next_actions` 告诉 Agent 下一步该做什么。错误响应包含 `recovery_action`，Agent 可以自动修复。

### Claude Code 一键集成

```bash
npx skills add can4hou6joeng4/boss-agent-cli
```

安装后 Agent 自动获得调用能力。你只需要说：

> "帮我搜广州的 Golang 职位，要双休五险一金，找到合适的自动打招呼"

Agent 会自动执行 `status → search → detail → greet` 的完整链路。

## 安装

```bash
# 安装 CLI
uv tool install git+https://github.com/can4hou6joeng4/boss-agent-cli.git

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
