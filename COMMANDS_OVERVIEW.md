# boss-agent-cli 命令总览

## 先回答你的问题

- **有没有聊天界面**：没有内置独立的网页聊天 UI。
- **有没有聊天相关能力**：有，体现在 `chat`、`chatmsg`、`chat-summary`、`hr chat` 这些命令，以及 `mcp-server/` 提供给 Claude Desktop / Cursor 的 MCP 接入。
- **这个项目更像什么**：更像“可被 Agent 调用的 CLI / MCP 工具集”，不是自己带完整前端聊天界面的智能体应用。

## 命令真源

- 面向人类的命令说明：`docs/commands.md`
- 实际注册入口：[register.py](file:///d:/download/py_test/boss-agent-cli/src/boss_agent_cli/commands/register.py)
- 机器可读能力真源：`boss schema`
- 你当前本地运行方式不是 `boss ...`，而是：

```bash
python run.py <command>
```

例如：

```bash
python run.py doctor
python run.py login
python run.py search "Python"
```

## 一、顶层命令

### 认证与环境

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `schema` | 输出完整能力描述 JSON | 是 |
| `login` | 登录平台 | 是 |
| `logout` | 退出登录 | 是 |
| `status` | 检查登录态 | 是 |
| `platforms` | 查看平台注册和能力状态 | 是 |
| `doctor` | 检查环境、依赖、登录态、网络 | 是 |

### 职位发现

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `search <query>` | 搜索职位 | 是 |
| `detail <security_id>` | 查看职位详情 | 是 |
| `show <编号>` | 查看上次搜索结果中的某条职位 | 是 |
| `recommend` | 推荐职位 | 受限 |
| `cities` | 查看支持城市 | 是 |
| `history` | 浏览历史 | 是 |

### 求职动作

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `greet <sid> <jid>` | 向招聘者打招呼 | 受限 |
| `batch-greet <query>` | 批量打招呼 | 受限 |
| `apply <sid> <jid>` | 发起投递 | 受限 |
| `exchange <sid>` | 请求交换联系方式 | 受限 |

### 沟通与跟进

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `chat` | 查看沟通列表 | 受限 |
| `chatmsg <sid>` | 查看聊天消息 | 受限 |
| `chat-summary <sid>` | 聊天摘要 | 受限 |
| `mark <sid>` | 打标签/标记联系人 | 受限 |
| `interviews` | 面试邀请 | 是 |

### 本地整理与监控

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `watch ...` | 搜索条件监控 | 部分可用 |
| `pipeline` | 流程状态聚合 | 受限 |
| `follow-up` | 跟进建议 | 受限 |
| `shortlist ...` | 候选池管理 | 是 |
| `preset ...` | 搜索预设管理 | 是 |
| `digest` | 沟通/流程摘要 | 受限 |
| `stats` | 漏斗统计 | 是 |

### 用户与简历

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `me` | 我的信息、简历、求职期望、投递记录 | 是 |
| `resume ...` | 本地简历管理 | 是 |

### AI 增强

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `ai ...` | AI 求职辅助命令组 | 是 |

### 系统管理

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `export` | 导出结果 | 是 |
| `config ...` | 配置管理 | 是 |
| `clean` | 清理缓存和临时文件 | 是 |

### 招聘者模式

| 命令 | 作用 | 默认是否可用 |
|------|------|--------------|
| `hr ...` | 招聘者快捷命令组 | 部分可用 |

## 二、子命令展开

### `watch`

| 子命令 | 作用 |
|--------|------|
| `watch add` | 保存一个监控搜索条件 |
| `watch list` | 列出所有监控条件 |
| `watch remove` | 删除监控条件 |
| `watch run` | 执行监控检查 |

说明：

- `add/list/remove` 是本地能力。
- `run` 默认可能被合规策略限制，因为它会自动拉取平台数据。

### `shortlist`

| 子命令 | 作用 |
|--------|------|
| `shortlist add` | 加入本地候选池 |
| `shortlist list` | 查看候选池 |
| `shortlist annotate` | 给候选职位加标签/备注 |
| `shortlist compare` | 对比候选职位 |
| `shortlist remove` | 移除候选职位 |

### `preset`

| 子命令 | 作用 |
|--------|------|
| `preset add` | 新增搜索预设 |
| `preset list` | 列出搜索预设 |
| `preset remove` | 删除搜索预设 |

### `resume`

| 子命令 | 作用 |
|--------|------|
| `resume init` | 初始化本地简历 |
| `resume list` | 列出本地简历 |
| `resume show` | 查看简历详情 |
| `resume edit` | 编辑简历字段 |
| `resume delete` | 删除简历 |
| `resume export` | 导出简历 |
| `resume import` | 导入简历 |
| `resume clone` | 复制简历 |
| `resume diff` | 比较两份简历差异 |
| `resume link` | 关联平台信息 |
| `resume applications` | 查看简历相关投递记录 |

### `ai`

| 子命令 | 作用 |
|--------|------|
| `ai config` | 配置 AI 服务 |
| `ai analyze-jd` | 分析 JD |
| `ai polish` | 润色简历 |
| `ai optimize` | 面向目标岗位优化简历 |
| `ai suggest` | 给出求职建议 |
| `ai reply` | 生成沟通回复草稿 |
| `ai interview-prep` | 生成面试准备内容 |
| `ai chat-coach` | 给出聊天沟通建议 |

### `config`

| 子命令 | 作用 |
|--------|------|
| `config list` | 查看当前配置 |
| `config set` | 设置配置项 |
| `config reset` | 恢复默认配置 |

### `hr`

| 子命令 | 作用 | 默认状态 |
|--------|------|----------|
| `hr applications` | 候选人申请列表 | 受限 |
| `hr resume` | 查看候选人在线简历/交换联系方式 | 受限 |
| `hr chat` | 招聘者沟通列表 | 受限 |
| `hr chatmsg` | 招聘者聊天记录 | 受限 |
| `hr last-messages` | 最近消息摘要 | 受限 |
| `hr jobs list` | 查看已发布职位 | 可用 |
| `hr jobs offline` | 下线职位 | 可用 |
| `hr jobs online` | 上线职位 | 可用 |
| `hr jobs detail` | 查看职位详情 | 可用 |
| `hr candidates <keyword>` | 搜索候选人 | 受限 |
| `hr reply <friend_id> <message>` | 回复候选人 | 受限 |
| `hr request-resume <friend_id>` | 请求附件简历 | 受限 |

## 三、哪些命令和“聊天”有关

虽然没有单独聊天界面，但这些命令都和聊天/沟通有关：

### 求职者侧

- `chat`
- `chatmsg`
- `chat-summary`
- `mark`
- `exchange`
- `ai reply`
- `ai chat-coach`

### 招聘者侧

- `hr chat`
- `hr chatmsg`
- `hr last-messages`
- `hr reply`
- `hr request-resume`

注意：

- 这类命令大多默认被低风险合规模式限制。
- 项目设计更偏向“辅助分析”和“本地整理”，不是鼓励自动批量沟通。

## 四、你本地最常用的启动方式

因为你当前没有把项目完整安装成 `boss` 命令，所以建议直接用：

```bash
cd d:\download\py_test\boss-agent-cli
python run.py --help
python run.py doctor
python run.py login
python run.py search "Python"
```

## 五、怎么看所有命令

### 看顶层命令

```bash
python run.py --help
```

### 看某个命令组选项

```bash
python run.py shortlist --help
python run.py resume --help
python run.py ai --help
python run.py hr --help
```

### 看机器可读完整能力

```bash
python run.py schema
```

## 六、命令来源文件

如果你想从源码里看“这些命令到底是哪里注册的”，重点看：

- [register.py](file:///d:/download/py_test/boss-agent-cli/src/boss_agent_cli/commands/register.py)
- [main.py](file:///d:/download/py_test/boss-agent-cli/src/boss_agent_cli/main.py)
- [commands.md](file:///d:/download/py_test/boss-agent-cli/docs/commands.md)

其中：

- `main.py` 是总入口
- `register.py` 负责把所有命令挂到 CLI 上
- `docs/commands.md` 是人类阅读版命令表

## 七、一句话总结

这个项目**没有独立聊天界面**，但**有聊天相关命令和 MCP 对接能力**；我已经把命令整理在这份文件里，你之后优先看这份即可。
