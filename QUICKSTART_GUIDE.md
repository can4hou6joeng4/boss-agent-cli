# boss-agent-cli 快速入门指南

## 🚀 5分钟上手

### 1. 安装
```bash
# 使用 uv（推荐）
uv tool install boss-agent-cli

# 或者使用 pip
pip install boss-agent-cli

# 安装浏览器内核（用于登录）
patchright install chromium
```

### 2. 运行环境检查
```bash
boss doctor
```

### 3. 登录
```bash
boss login
```
会弹出浏览器，扫码登录即可。

### 4. 搜索职位
```bash
# 基础搜索
boss search "Python"

# 带福利筛选（核心功能！）
boss search "Python" --city 北京 --welfare "双休,五险一金"

# 按匹配分排序
boss search "Python" --welfare "双休,五险一金" --sort score
```

### 5. 查看详情
```bash
boss detail <security_id>
```

### 6. 加入候选池
```bash
boss shortlist add <security_id> <job_id> --tags 后端,远程 --notes "薪资合适"
```

### 7. 查看统计
```bash
boss stats
```

---

## 🎯 核心功能详解

### 1️⃣ 福利筛选（最核心！）

#### 工作原理
```
搜索 → 多页补抓 → 匹配福利 → 按匹配分排序
```

#### 使用示例
```bash
# 简单单福利
boss search "Python" --welfare "双休"

# 多福利（AND 逻辑，必须同时满足）
boss search "Python" --welfare "双休,五险一金,弹性工作"

# 从预设选择
boss preset
boss search "Python" --preset my-favorite
```

#### 匹配分数规则
- 精确匹配 → 高分
- 同义词匹配 → 中分
- 部分匹配 → 低分
- 组合匹配 → 额外加分

### 2️⃣ 本地候选池

#### 候选池操作
```bash
# 加入候选池
boss shortlist add <security_id> <job_id> --tags 远程 --notes "CEO 直招"

# 查看候选池
boss shortlist list

# 按标签筛选
boss shortlist list --tag 远程

# 对比多个职位
boss shortlist compare --tag 远程

# 导出候选池
boss export shortlist --format json
```

#### 候选池字段
- `security_id` / `job_id` - 职位唯一标识
- `tags` - 标签（可多个）
- `notes` - 备注
- `timestamp` - 添加时间
- `match_score` - 福利匹配分（如有）

### 3️⃣ AI 求职增强

#### AI 命令列表
```bash
# JD 分析
boss ai analyze-jd --id <security_id>

# 简历润色
boss ai polish

# 定向优化（为特定职位优化简历）
boss ai optimize --id <security_id>

# 候选池匹配（找出最适合你的）
boss ai fit

# 模拟面试
boss ai interview-prep --id <security_id>

# 沟通指导（帮你准备给 HR 发消息）
boss ai chat-coach --id <security_id>
```

#### 配置 AI
```bash
boss config set openai-api-key sk-xxx
boss config set model gpt-4
```

### 4️⃣ 多平台支持

#### 切换平台
```bash
# 单次使用
boss --platform zhilian search "Python"

# 设为默认
boss config set platform zhilian
```

#### 支持平台
| 平台 | 求职者 | 招聘者 |
|------|--------|--------|
| BOSS 直聘 | ✅ | ✅ |
| 智联招聘 | 🟡 | ❌ |
| 前程无忧 | 🚧 | ❌ |

---

## 📊 典型工作流

### 求职工作流
```
1. boss preset                ← 设置常用筛选条件
2. boss search                ← 搜索职位 + 福利筛选
3. boss detail <id>           ← 查看感兴趣的职位
4. boss shortlist add <id>    ← 加入候选池
5. boss ai optimize <id>      ← AI 优化简历
6. boss watch                 ← 监控候选池变化
7. boss stats                 ← 查看统计
```

### 探索工作流
```
1. boss search "Python" --city 上海
2. boss show                   ← 查看缓存的搜索结果
3. boss history                ← 查看搜索历史
4. boss shortlist compare      ← 对比候选职位
```

---

## ⚙️ 配置详解

### 配置文件位置
```
~/.boss-agent/config.json
```

### 常用配置项
```bash
# 查看所有配置
boss config list

# 设置默认城市
boss config set default_city 北京

# 设置默认薪资
boss config set default_salary "20-40K"

# 设置请求延迟
boss config set request_delay 1.5 3.0

# 设置日志级别
boss config set log_level info

# 重置为默认
boss config reset
```

---

## 🔒 合规边界（重要！）

### 默认低风险模式
- ✅ 允许：搜索、查看详情、本地候选池、查看推荐
- ❌ 阻断：打招呼、投递、交换联系方式、聊天、候选人搜索

### 低风险模式的好处
- 安全，不会触发平台风控
- 专注于辅助，而不是自动化
- 符合平台服务条款

### 手动操作
```
# 浏览到职位页面
boss detail <id> --open

# 然后在浏览器中手动操作
```

---

## 🛠️ 诊断与排障

### 环境检查
```bash
boss doctor
```

### 登录状态
```bash
boss status
boss status --live  # 实时检查
```

### 常见问题

#### Q: 提示认证过期？
```bash
boss login
```

#### Q: 提示风控拦截？
- 等待一段时间再试
- 减少请求频率
- 在浏览器手动操作几次

#### Q: Cookie 提取失败？
```bash
boss doctor --live-probe
```

---

## 🤖 Agent 集成

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
# 1. 获取能力自描述
boss schema

# 2. 调用命令
boss search "Python" --format json
```

### 方式三：Python SDK
```python
from boss_agent_cli import AuthManager, BossClient

# 使用上下文管理器
with BossClient(AuthManager()) as client:
    result = client.search_jobs("Python", city="北京")
    print(result)
```

---

## 📚 进阶主题

### 1. 预设（Preset）管理
```bash
# 创建预设
boss preset create my-preset --welfare "双休,五险一金" --city 上海

# 使用预设
boss search "Python" --preset my-preset

# 列出预设
boss preset list
```

### 2. 监控（Watch）
```bash
# 监控候选池变化
boss watch --interval 300

# 有变化时发通知
boss watch --notify
```

### 3. 数据导出
```bash
# 导出候选池
boss export shortlist --format json

# 导出搜索历史
boss export history --format csv

# 自定义导出目录
boss export shortlist --dir ./exports
```

---

## 💡 使用技巧

### 1. 利用缓存
```bash
# 查看缓存的搜索结果
boss show

# 从缓存重新筛选
boss show --filter "双休"
```

### 2. 标签系统
```bash
# 使用标签分类
boss shortlist add --tags 远程,高薪,国企

# 按标签查看
boss shortlist list --tag 远程
```

### 3. 笔记功能
```bash
boss shortlist add --notes "需要 3 年经验，HR 态度很好"
```

---

## 🎓 学习路径

### 第1天：基础操作
- ✅ 安装
- ✅ 登录
- ✅ 基础搜索
- ✅ 查看详情

### 第2-3天：进阶功能
- ✅ 福利筛选
- ✅ 候选池管理
- ✅ 标签和笔记
- ✅ 统计分析

### 第4-7天：AI 增强
- ✅ AI 配置
- ✅ JD 分析
- ✅ 简历优化
- ✅ 模拟面试

### 第2周+：Agent 集成
- ✅ MCP 集成
- ✅ Python SDK
- ✅ 自定义工作流

---

## 📖 更多文档

- [完整项目架构](./PROJECT_FRAMEWORK.md)
- [命令参考](./docs/commands.md)
- [Agent 集成](./docs/agent-quickstart.md)
- [平台风险](./docs/platform-risk.md)
- [排障指南](./docs/troubleshooting.md)

---

祝你求职顺利！🎯
