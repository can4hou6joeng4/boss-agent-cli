# 🎉 boss-agent-cli 已成功配置！

## 📁 项目位置
```
d:\download\py_test\boss-agent-cli\
```

## 🚀 快速启动命令

### 方式一：使用 `run.py` 启动（推荐）
```bash
cd d:\download\py_test\boss-agent-cli
python run.py --help
python run.py doctor
```

### 方式二：所有命令的运行方式
所有 `boss xxx` 命令，替换为：
```bash
python run.py xxx
```

---

## 🔐 登录 BOSS 直聘

```bash
python run.py login
```

这会打开浏览器让你扫码登录。

---

## 📊 登录成功后常用命令

```bash
# 检查登录状态
python run.py status

# 搜索职位
python run.py search "Python" --city 北京

# 搜索 + 福利筛选（核心功能）
python run.py search "Python" --city 北京 --welfare "双休,五险一金"

# 查看职位详情
python run.py detail <security_id>

# 加入候选池
python run.py shortlist add <security_id> <job_id> --tags 远程

# 查看统计
python run.py stats
```

---

## 📚 文档索引

- `INSTALL_GUIDE.md` - 安装指南
- `QUICKSTART_GUIDE.md` - 5分钟快速入门
- `PROJECT_FRAMEWORK.md` - 完整架构文档

---

## ⚠️ 注意事项

1. 如果需要浏览器登录功能，需要安装 chromium：
```bash
pip install patchright
patchright install chromium
```

2. 推荐先试用小模型/简单功能，确认没问题再深入！

---

## 🎯 下一步行动

```bash
# 1. 登录（必须先做）
python run.py login

# 2. 检查登录状态
python run.py status

# 3. 搜索职位（试试水）
python run.py search "Python"
```

祝你使用愉快！🚀
