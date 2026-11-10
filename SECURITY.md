# Security Policy

## Supported Versions

只有最新的 minor 版本会收到安全修复。项目发布频繁（见 [CHANGELOG.md](CHANGELOG.md)），
不维护历史分支——修复以新版本形式发出，请升级到最新版而不是期待补丁回移。

| Version | Supported          |
|---------|--------------------|
| 3.2.x   | :white_check_mark: |
| < 3.2   | :x:                |

升级：`uv tool install --upgrade boss-agent-cli`（或 `pip install -U boss-agent-cli`）。

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

历史配置 `operating_mode=assisted|research` 仅为兼容保留，两种模式对所有已实现能力权限一致；真正的边界由以下机制保证：

- **平台风控是硬边界**：命中账号风险 / 环境风险 / 安全页时停止当前 workflow 并保存 checkpoint，不无界重试，也不经 CDP / patchright / Cookie 提取绕过风控。
- **长流程有界**：受 timeout、retry、预算、checkpoint 与 stop 约束；批量打招呼、批量采集等能力需要用户显式触发并设置上限。
- **Cookie/Token 存储**：使用 Fernet 对称加密 + PBKDF2 机器绑定密钥
- **浏览器兼容通道**：仅用于用户主动登录和低风险兼容路径，所有浏览器操作限定在 `zhipin.com` 域名
- **本地数据**：缓存数据存储在 `~/.boss-agent/`，不上传任何数据到第三方服务
- **CSV/HTML 导出**：已实施公式注入防护和 XSS 防护

更多平台边界、Cookie/CDP、真实账号和 smoke test 风险边界见 [docs/platform-risk.md](docs/platform-risk.md)。
