# boss-agent-cli 项目架构详解

## 📋 项目概述

**boss-agent-cli** 是一个专为 AI Agent 设计的 BOSS 直聘本地辅助 CLI 工具，提供：
- 职位搜索与筛选
- 福利匹配（核心差异化功能）
- 本地候选池管理
- AI 求职增强
- 多平台抽象
- 默认低风险合规模式

---

## 🏗️ 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI 入口 (main.py)                     │
│                         (Click 框架)                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    合规护栏 (compliance.py)                     │
│            默认低风险模式，阻断敏感写操作                        │
└────────────────────────────┬────────────────────────────────────┘
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│   AuthManager   │ │  Platform 双注  │ │    BossClient   │
│   (auth/)       │ │   册表          │ │   (api/)        │
│  用户认证管理   │ │  (platforms/)   │ │  HTTP + 浏览器  │
└─────────────────┘ └─────────────────┘ └─────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────┐          ┌─────────────────┐
│ TokenStore      │          │ CacheStore      │
│ 令牌加密存储    │          │ SQLite 缓存     │
└─────────────────┘          └─────────────────┘
         │                              │
         └──────────────┬───────────────┘
                        ▼
              ┌─────────────────┐
              │  命令层 (commands/) │
              │  35+ 子命令注册    │
              └─────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  JSON 信封输出   │
              │  (output.py)    │
              └─────────────────┘
```

---

## 📂 目录结构详解

```
boss-agent-cli/
├── src/boss_agent_cli/               # 源代码主目录
│   ├── __init__.py                   # 包初始化，版本信息
│   ├── main.py                       # [核心] CLI 入口点
│   ├── config.py                     # [核心] 配置管理
│   ├── compliance.py                 # [核心] 合规边界控制
│   ├── output.py                     # [核心] JSON 信封输出
│   ├── display.py                    # 终端显示格式化
│   ├── hooks.py                      # 钩子总线系统
│   ├── mcp_server.py                 # MCP 服务器入口
│   ├── match_score.py                # 匹配分数计算
│   ├── digest.py                     # 摘要生成
│   ├── chat_summary.py               # 聊天摘要
│   │
│   ├── ai/                           # [模块] AI 服务层
│   │   ├── __init__.py
│   │   ├── config.py                 # AI 配置（OpenAI/Anthropic）
│   │   ├── prompts.py                # Prompt 模板库
│   │   └── service.py                # AI 服务核心
│   │
│   ├── api/                          # [模块] API 客户端层
│   │   ├── __init__.py
│   │   ├── client.py                 # [核心] BossClient 混合客户端
│   │   ├── browser_client.py         # 浏览器 CDP 客户端
│   │   ├── endpoints.py              # API 端点定义
│   │   ├── endpoints_loader.py       # YAML 端点加载
│   │   ├── recruiter_client.py       # 招聘者侧客户端
│   │   ├── zhilian_client.py         # 智联招聘客户端
│   │   ├── models.py                 # 数据模型定义
│   │   ├── throttle.py               # 请求节流器
│   │   ├── httpx_helpers.py          # HTTPX 辅助函数
│   │   ├── boss.yaml                 # BOSS 直聘 API 定义
│   │   └── recruiter.yaml            # 招聘者 API 定义
│   │
│   ├── auth/                         # [模块] 认证层
│   │   ├── __init__.py
│   │   ├── manager.py                # [核心] AuthManager 认证管理器
│   │   ├── token_store.py            # 令牌加密存储
│   │   ├── browser.py                # 浏览器认证
│   │   ├── qr_login.py               # 二维码登录
│   │   ├── cookie_extract.py         # Cookie 提取
│   │   └── health.py                 # 健康检查
│   │
│   ├── bridge/                       # [模块] 浏览器桥接层
│   │   ├── __init__.py
│   │   ├── daemon.py                 # 桥接守护进程
│   │   ├── client.py                 # 桥接客户端
│   │   └── protocol.py               # 桥接协议定义
│   │
│   ├── cache/                        # [模块] 缓存层
│   │   ├── __init__.py
│   │   └── store.py                  # SQLite WAL 缓存
│   │
│   ├── commands/                     # [模块] 命令层（35+ 子命令）
│   │   ├── __init__.py
│   │   ├── register.py               # 命令注册
│   │   ├── _platform.py              # 平台基类命令
│   │   ├── login.py                  # 登录
│   │   ├── logout.py                 # 登出
│   │   ├── status.py                 # 状态检查
│   │   ├── doctor.py                 # 诊断
│   │   ├── search.py                 # [核心] 职位搜索 + 福利筛选
│   │   ├── detail.py                 # 职位详情
│   │   ├── show.py                   # 展示缓存
│   │   ├── cities.py                 # 城市列表
│   │   ├── history.py                # 搜索历史
│   │   ├── shortlist.py              # 候选池
│   │   ├── stats.py                  # 统计
│   │   ├── watch.py                  # 监控
│   │   ├── preset.py                 # 预设
│   │   ├── resume_cmd.py             # 简历
│   │   ├── ai_cmd.py                 # [核心] AI 增强命令
│   │   ├── chat.py                   # 聊天
│   │   ├── apply.py                  # 投递
│   │   ├── greet.py                  # 打招呼
│   │   ├── exchange.py               # 联系方式交换
│   │   ├── export.py                 # 导出
│   │   ├── config_cmd.py             # 配置
│   │   ├── clean.py                  # 清理
│   │   ├── schema.py                 # Schema 自描述
│   │   ├── platforms.py              # 平台列表
│   │   └── recruiter/                # 招聘者子命令
│   │       ├── jobs.py               # 职位管理
│   │       ├── candidates.py         # 候选人
│   │       ├── chat.py               # 招聘者聊天
│   │       └── ...
│   │
│   ├── platforms/                    # [模块] 平台适配层
│   │   ├── __init__.py               # 平台注册表
│   │   ├── base.py                   # [核心] Platform 抽象基类
│   │   ├── recruiter_base.py         # RecruiterPlatform 基类
│   │   ├── zhipin.py                 # BOSS 直聘求职者适配
│   │   ├── zhipin_recruiter.py       # BOSS 直聘招聘者适配
│   │   ├── zhilian.py                # 智联招聘适配
│   │   └── qiancheng.py              # 前程无忧适配
│   │
│   └── resume/                       # [模块] 简历管理
│       ├── __init__.py
│       ├── models.py                 # 简历数据模型
│       ├── store.py                  # 简历存储
│       ├── templates.py              # 简历模板
│       └── export.py                 # 简历导出
│
├── mcp-server/                       # MCP 服务器
│   └── server.py                     # MCP 服务器入口
│
├── extension/                        # 浏览器扩展
│   ├── manifest.json
│   └── background.js
│
├── scripts/                          # 脚本工具
│   ├── smoke_p0.py                   # P0 冒烟测试
│   ├── quality_baseline.py           # 质量基线
│   └── probe_recruiter_chat.py       # 招聘者聊天前端探测
│
├── tests/                            # 测试套件（100+ 测试用例）
│   ├── conftest.py                   # pytest 配置
│   ├── test_*.py                     # 各模块测试
│   └── ...
│
├── docs/                             # 文档
│   ├── getting-started.md            # 快速上手
│   ├── agent-quickstart.md           # Agent 集成
│   ├── commands.md                   # 命令参考
│   ├── platform-abstraction.md       # 平台抽象设计
│   ├── platform-risk.md              # 平台风险边界
│   ├── troubleshooting.md            # 排障指南
│   └── ...
│
├── demo/                             # 演示
│   ├── demo-zh.gif                   # 中文演示
│   └── ...
│
└── pyproject.toml                    # 项目配置
```

---

## 🎯 核心概念

### 1. 合规边界（Compliance）

默认启用**低风险辅助模式**：
- ✅ 本地辅助、只读优先
- ❌ 打招呼、投递、联系方式交换默认阻断
- ❌ 招聘者候选人搜索 / 简历 / 聊天默认阻断
- 返回 `COMPLIANCE_BLOCKED` 错误码

### 2. JSON 信封输出

所有命令输出统一格式：
```json
{
  "ok": true,
  "data": {...},
  "pagination": {...},
  "error": {...},
  "hints": [...]
}
```

### 3. 平台抽象（Platform Abstraction）

双注册表设计：
- `Platform`：求职者接口
- `RecruiterPlatform`：招聘者接口

支持多平台：
- `zhipin`：BOSS 直聘（默认）
- `zhilian`：智联招聘
- `qiancheng`：前程无忧

---

## 🔑 核心模块详解

### 一、认证模块 (auth/)

#### 1.1 AuthManager
- **职责**：统一认证管理，令牌生命周期
- **核心方法**：
  - `login()` - 多种登录方式
  - `get_token()` - 获取加密令牌
  - `check_auth()` - 认证状态检查
- **安全特性**：Fernet + PBKDF2 机器绑定加密

#### 1.2 TokenStore
- **存储**：`~/.boss-agent/tokens.json`
- **加密**：AES-256-GCM
- **绑定**：机器指纹绑定

### 二、API 客户端 (api/)

#### 2.1 BossClient（混合客户端）
- **设计**：低风险用 httpx，高风险用浏览器
- **核心特性**：
  - 请求节流（高斯延迟）
  - 自动重试（3次）
  - Cookie 合并
  - 合规检查

#### 2.2 RequestThrottle
- **策略**：高斯分布延迟
- **范围**：可配置（如 1.5-3.0 秒）

### 三、平台适配 (platforms/)

#### 3.1 Platform 基类
```python
class Platform(ABC):
    # 元信息
    name: str
    display_name: str
    base_url: str
    
    # 核心方法
    @abstractmethod
    def search_jobs(self, keyword, **kwargs)
    @abstractmethod
    def get_job_detail(self, security_id, job_id)
    @abstractmethod
    def get_user_info(self)
```

#### 3.2 平台实现
- `ZhipinPlatform`：BOSS 直聘
- `ZhipinRecruiterPlatform`：BOSS 直聘招聘者
- `ZhilianPlatform`：智联招聘
- `QianchengPlatform`：前程无忧（占位）

### 四、命令层 (commands/)

#### 4.1 命令注册
- `register_candidate_commands()`：求职者命令
- `register_recruiter_commands()`：招聘者命令

#### 4.2 核心命令
| 命令 | 功能 |
|------|------|
| `search` | 职位搜索 + 福利筛选 |
| `detail` | 查看详情 |
| `shortlist` | 本地候选池 |
| `ai` | AI 增强（6个子命令） |
| `hr jobs` | 招聘者职位管理 |

### 五、AI 增强 (ai/)

#### 5.1 AI 命令
- `ai analyze-jd`：JD 分析
- `ai polish`：简历润色
- `ai optimize`：定向优化
- `ai fit`：候选池匹配
- `ai interview-prep`：模拟面试
- `ai chat-coach`：沟通指导

#### 5.2 AIService
- 支持 OpenAI / Anthropic
- Prompt 模板库
- Token 用量追踪

### 六、福利筛选（核心差异化）

#### 6.1 工作原理
```python
--welfare "双休,五险一金"
```
- 自动翻页补抓
- AND 逻辑真实匹配
- 本地匹配分排序

#### 6.2 匹配分数
- 精确匹配权重
- 同义词支持
- 组合优先

---

## 🔄 数据流程

### 典型搜索流程

```
1. 用户输入
   ↓
2. CLI 解析参数 → 加载配置
   ↓
3. 合规检查
   ↓
4. AuthManager 获取令牌
   ↓
5. Platform.search_jobs()
   ↓
6. BossClient 调用 API
   ↓
7. RequestThrottle 节流
   ↓
8. CacheStore 查询缓存
   ↓
9. 福利筛选（如指定 --welfare）
   ↓
10. JSON 信封输出
    ↓
11. 终端展示
```

### 候选池流程

```
1. detail → 提取信息
   ↓
2. shortlist add → 本地保存
   ↓
3. 标签/备注管理
   ↓
4. stats → 统计分析
   ↓
5. watch → 监控变化
```

---

## 🤖 Agent 集成方式

### 方式一：MCP（推荐）
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

### 方式二：Subprocess
```bash
boss schema  # 能力自描述
boss search "Python"
```

### 方式三：Python SDK
```python
from boss_agent_cli import AuthManager, BossClient

with BossClient(AuthManager(...)) as client:
    result = client.search_jobs("Python")
```

---

## 🛡️ 安全设计

### 1. 令牌安全
- Fernet 加密存储
- PBKDF2 密钥派生
- 机器指纹绑定

### 2. 合规边界
- 敏感操作默认阻断
- 平台风险分级
- 不规避风控

### 3. 请求节流
- 高斯分布延迟
- 避免被识别为机器人

---

## 📊 技术栈

| 组件 | 技术 |
|------|------|
| CLI 框架 | Click |
| HTTP 客户端 | httpx |
| 浏览器自动化 | patchright / CDP |
| 缓存 | SQLite WAL |
| 加密 | cryptography (Fernet) |
| AI | OpenAI / Anthropic API |
| 测试 | pytest |
| 类型 | Python >= 3.10 + py.typed |

---

## 🎓 快速入门路径

### 1. 环境准备
```bash
uv tool install boss-agent-cli
patchright install chromium
```

### 2. 基础流程
```bash
boss doctor
boss login
boss search "Python" --city 北京 --welfare "双休"
boss detail <id>
boss shortlist add <id>
```

### 3. 进阶
```bash
boss ai analyze-jd --id <id>
boss ai polish
boss stats
```

---

## 🔍 关键文件索引

| 路径 | 重要度 | 说明 |
|------|--------|------|
| `main.py` | ⭐⭐⭐⭐⭐ | CLI 入口 |
| `platforms/base.py` | ⭐⭐⭐⭐⭐ | 平台抽象设计 |
| `auth/manager.py` | ⭐⭐⭐⭐⭐ | 认证管理器 |
| `api/client.py` | ⭐⭐⭐⭐⭐ | 混合客户端 |
| `compliance.py` | ⭐⭐⭐⭐ | 合规边界 |
| `commands/search.py` | ⭐⭐⭐⭐ | 搜索 + 福利筛选 |
| `ai/service.py` | ⭐⭐⭐⭐ | AI 服务 |
| `output.py` | ⭐⭐⭐ | JSON 信封 |

---

## 📚 相关文档

- [快速上手](docs/getting-started.md)
- [命令参考](docs/commands.md)
- [Agent 集成](docs/agent-quickstart.md)
- [平台抽象](docs/platform-abstraction.md)
- [平台风险](docs/platform-risk.md)
- [排障指南](docs/troubleshooting.md)
