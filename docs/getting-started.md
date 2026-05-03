# 快速上手

这份文档只保留最短可验证路径。完整能力说明见 [README.md](../README.md)，Agent 接入见 [agent-quickstart.md](agent-quickstart.md)。

## 1. 安装

```bash
uv tool install boss-agent-cli
patchright install chromium
```

源码开发环境：

```bash
git clone https://github.com/can4hou6joeng4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run patchright install chromium
```

## 2. 本地自检

```bash
boss doctor
boss status
boss schema --format native
```

期望结果：

- `boss doctor` 返回 `ok:true` 或带有明确 `recovery_action` 的 `ok:false`。
- `boss status` 能说明当前登录态是否可用。
- `boss schema --format native` 返回 JSON 信封，并列出当前 CLI 能力。

## 3. 第一个只读命令

登录后先执行只读命令，不要从写操作开始排障。

```bash
boss search "Golang" --city 广州 --welfare "双休"
boss detail <security_id>
```

`security_id` 来自 `search` 返回的 JSON 数据。提交 Issue 时必须脱敏，不要粘贴真实 `security_id`、Cookie、Token、手机号、微信号、姓名或公司内部信息。

## 4. JSON 信封契约

所有 Agent 可读输出都应是单个 JSON 信封：

```json
{
	"ok": true,
	"schema_version": "1.0",
	"command": "schema",
	"data": {},
	"pagination": null,
	"error": null,
	"hints": null
}
```

失败时：

```json
{
	"ok": false,
	"schema_version": "1.0",
	"command": "status",
	"data": null,
	"pagination": null,
	"error": {
		"code": "AUTH_REQUIRED",
		"message": "未登录",
		"recoverable": true,
		"recovery_action": "boss login"
	},
	"hints": null
}
```

## 5. 开发者验证

修改代码前后使用同一组命令验证：

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/boss_agent_cli
uv run boss --help
uv run boss schema --format native
```

如果只改文档，至少运行：

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

## 6. 提交问题前

提交 Bug 时请提供：

- `boss --version`
- Python 版本
- 操作系统
- 平台：`zhipin` 或 `zhilian`
- 角色：`candidate` 或 `recruiter`
- 完整 JSON 信封，已脱敏
- `boss doctor` 输出，已脱敏

平台接口变化、登录失效、风控、Cookie/CDP、浏览器自动化问题，先阅读 [platform-risk.md](platform-risk.md)。
