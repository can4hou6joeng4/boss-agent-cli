# 快速配置启动指南

## 方法一：开发模式安装（推荐用于修改）

1. 在项目目录打开终端，安装项目依赖
```bash
cd d:\download\py_test\boss-agent-cli
pip install -e .
```

2. 验证安装
```bash
boss --help
boss --version
```

## 方法二：uv tool install（推荐用于使用）

```bash
uv tool install .
```

## 验证环境

```bash
boss doctor
```

## 登录 BOSS 直聘

```bash
boss login
```
会弹出浏览器，扫码登录即可。

## 验证登录状态

```bash
boss status
```

## 开始搜索

```bash
boss search "Python" --city 北京
```
