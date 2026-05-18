# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.1.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

如果你发现了安全漏洞，请**不要**在公开 Issue 中提交。

请通过以下方式私下报告：

1. 发送邮件至 can4hou6joeng4@163.com
2. 或使用 [GitHub Security Advisories](https://github.com/can4hou6joeng4/boss-agent-cli/security/advisories/new) 创建私密报告

请在报告中包含：
- 漏洞的详细描述
- 复现步骤
- 影响范围评估
- 如果可能，提供修复建议

我们会在 **48 小时内**确认收到报告，并在 **7 天内**给出初步评估。

## Security Considerations

本项目默认启用低风险辅助模式：本地辅助、只读优先、用户主动触发、不批量触达、不抓取平台数据。开发时请特别注意：

- **Cookie/Token 存储**：使用 Fernet 对称加密 + PBKDF2 机器绑定密钥
- **浏览器兼容通道**：仅用于用户主动登录和低风险兼容路径，所有浏览器操作限定在 `zhipin.com` 域名
- **本地数据**：缓存数据存储在 `~/.boss-agent/`，不上传任何数据到第三方服务
- **CSV/HTML 导出**：已实施公式注入防护和 XSS 防护

更多平台边界、Cookie/CDP、真实账号和 smoke test 风险边界见 [docs/platform-risk.md](docs/platform-risk.md)。
