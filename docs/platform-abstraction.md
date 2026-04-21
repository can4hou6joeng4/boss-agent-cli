# Platform 抽象设计与迁移 SOP

> 本文档沉淀 Issue #129 Week 1 的全量经验。未来接入新平台（Lagou / Liepin / 其他）或做类似重构时的可复用 playbook。

## 为什么需要 Platform 抽象

**底层逻辑**：原始代码 30+ 个命令都直接 `with BossClient(...) as client:`。这条隐式依赖让后续接入第二个招聘平台的成本失控——每增加一家平台需要把 30+ 命令的请求构造重新来一遍。

**抽象的颗粒度选择**：把"平台"作为第一公民抽象（不是"API endpoint"或"认证流程"），让平台内部的协议差异（响应包络、错误码、加密等）可以完全封闭在平台实现里。

## Platform ABC 契约（核心四类方法）

### 1. 基础元信息
```python
name: str            # "zhipin" / "zhilian" / ...
display_name: str    # "BOSS 直聘" / "智联招聘" / ...
base_url: str
```

### 2. 响应包络适配（每平台必须实现）
```python
def is_success(self, response: dict) -> bool
def unwrap_data(self, response: dict) -> Any
def parse_error(self, response: dict) -> tuple[str, str]  # (统一错误码, 原始消息)
```

不同平台的差异举例：
- BOSS 直聘：`code == 0` 表示成功，数据在 `zpData` key
- 智联招聘：`code == 200` 表示成功，数据在 `data` key
- 错误码映射到统一枚举：`AUTH_EXPIRED / RATE_LIMITED / ACCOUNT_RISK / TOKEN_REFRESH_FAILED / UNKNOWN`

### 3. P0 只读能力（抽象方法，强制实现）
```python
@abstractmethod
def search_jobs(self, query: str, **filters: Any) -> dict
@abstractmethod
def job_detail(self, job_id: str) -> dict
@abstractmethod
def recommend_jobs(self, page: int = 1) -> dict
@abstractmethod
def user_info(self) -> dict
```

### 4. P0+ / P1 / P2 能力（默认 NotImplementedError）
```python
# P0+：简历 / 投递 / 历史 / 面试 / 聊天（基类默认 NotImplementedError）
def resume_baseinfo(self) -> dict
def resume_expect(self) -> dict
def deliver_list(self, page: int = 1) -> dict
def job_card(self, security_id: str, lid: str = "") -> dict
def interview_data(self) -> dict
def job_history(self, page: int = 1) -> dict
def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict
def friend_label(self, friend_id: str, label_id: int, friend_source: int = 0, remove: bool = False) -> dict
def exchange_contact(self, security_id: str, uid: str, friend_name: str, exchange_type: int = 1) -> dict

# P1：写操作
def greet(self, security_id: str, job_id: str, message: str = "") -> dict
def apply(self, security_id: str, job_id: str, lid: str = "") -> dict

# P2：沟通
def friend_list(self, page: int = 1) -> dict
```

### 5. 资源生命周期
```python
def close(self) -> None                      # 委托给底层 client.close()
def __enter__(self) -> "Platform": ...       # with 上下文管理器支持
def __exit__(self, ...) -> None: ...
```

## 命令迁移模板

**底层逻辑**：命令层从"直用 BossClient"升级到"经 Platform 抽象调用"，让命令代码对平台无感。

### 迁移前
```python
from boss_agent_cli.api.client import BossClient

def cmd(ctx):
    data_dir = ctx.obj["data_dir"]
    logger = ctx.obj["logger"]
    delay = ctx.obj["delay"]
    cdp_url = ctx.obj.get("cdp_url")

    auth = AuthManager(data_dir, logger=logger)
    with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
        result = client.search_jobs("Python", city="广州")
```

### 迁移后
```python
from boss_agent_cli.commands._platform import get_platform_instance

def cmd(ctx):
    data_dir = ctx.obj["data_dir"]
    logger = ctx.obj["logger"]

    auth = AuthManager(data_dir, logger=logger)
    with get_platform_instance(ctx, auth) as platform:
        result = platform.search_jobs("Python", city="广州")
```

**净收益**：减少 3 行样板代码（delay / cdp_url / BossClient 构造），调用面不变。

## 测试 Mock 位点规则

### 迁移前
```python
@patch("boss_agent_cli.commands.X.BossClient")
def test_something(mock_client_cls):
    mock_client = _ctx_mock(mock_client_cls)
    mock_client.search_jobs.return_value = {...}
```

### 迁移后
```python
@patch("boss_agent_cli.commands.X.get_platform_instance")
def test_something(mock_get_platform):
    mock_platform = _ctx_mock(mock_get_platform)
    mock_platform.search_jobs.return_value = {...}
```

**底层逻辑**：patch 的是命令模块里的 import 别名，不是源模块。所以只需跟随命令文件的 import 方式变化。

### 批量 sed 替换命令
```bash
for cmd in greet apply detail me recommend ...; do
  sed -i '' "s/boss_agent_cli.commands.$cmd.BossClient/boss_agent_cli.commands.$cmd.get_platform_instance/g" tests/*.py
done
```

## 新增平台接入 SOP

### Step 1：研究报告（docs/research/platforms/<name>.md）

覆盖 7 项（样板见 [lagou.md](research/platforms/lagou.md)）：
1. 登录链路
2. 核心 API 端点清单
3. 请求签名 / 防爬机制
4. 响应结构
5. CDP / Browser 通道可行性
6. 风控触发阈值
7. 与 BOSS 直聘的协议差异矩阵

### Step 2：注册 stub（自证抽象设计对齐）
```python
# src/boss_agent_cli/platforms/<name>.py
class MyPlatform(Platform):
    name = "myname"
    display_name = "平台中文名"
    base_url = "https://..."
    
    def is_success(self, r): ...  # 按研究报告的响应结构
    def unwrap_data(self, r): ...
    def parse_error(self, r): ...
    
    # P0/P1/P2 方法全部抛 NotImplementedError("Week 2 待实现")
```

### Step 3：注册到 `platforms/__init__.py`
```python
_REGISTRY: dict[str, type[Platform]] = {
    "zhipin": BossPlatform,
    "zhilian": ZhilianPlatform,
    "myname": MyPlatform,   # 新增
}
```

### Step 4：契约测试 `tests/test_<name>_stub.py`
- Platform 注册表验证（4 条）
- 基础元信息（3 条）
- 包络适配（10+ 条）
- Stub 行为（每个抽象方法抛 NotImplementedError）
- CLI 集成（`boss --platform <name> schema` 通）

### Step 5：真实现迭代
- 先 P0（search / detail / recommend / user_info）
- 再 P1（greet / apply，如需要）
- 最后 P2（友好度可选）

## 不变量契约

以下是 Platform 抽象的 breaking change 红线：

1. **基类 `__init__(client: Any)` 签名不变** — 子类可窄化但不能加必填参数
2. **抽象方法签名不变** — 只能添加新方法，不能改现有方法签名
3. **错误码映射枚举** — 对齐 CLAUDE.md 错误码枚举的范围
4. **`with` 上下文语义** — `__exit__` 必须调用 `close()`
5. **Python 嵌入 API 导出** — `Platform` / `BossPlatform` / `ZhilianPlatform` / `get_platform` / `list_platforms` 通过 `from boss_agent_cli import ...` 始终可达

## 参考

- [Issue #129 — Week 1 设计 + 实施](https://github.com/can4hou6joeng4/boss-agent-cli/issues/129)
- [Issue #140 — Week 2 Zhilian 真实现追踪](https://github.com/can4hou6joeng4/boss-agent-cli/issues/140)
- [Issue #90 — 多平台 API 调研](https://github.com/can4hou6joeng4/boss-agent-cli/issues/90)
- PR #131 / #132 / #133 / #134 / #135 / #136 / #137 / #138 / #139 / #141 — Week 1 全部 PR
