"""浏览器会话来源（browser source）策略表 —— 通道选择的唯一真源。

## 为什么存在这个模块

`BrowserSession._ensure_started()` 原本是一条硬编码的三级降级链
（Bridge → CDP → headless），中间只有两处插入缝隙。两个并行的 PR 各自在其中
一处插了一个模式短路（#404 的 ``browser_mode`` 插在 ``_try_bridge()`` 之前、
#388 的 ``existing_browser_only`` 插在它之后），中间隔着三行谁都没改的代码 ——
git 三方合并会干净通过、不留任何冲突标记，合并后类上就有了两个互不感知的模式
布尔，谁生效取决于插入位置的先后，且失效方向是 fail-open。

本模块把「用哪些通道、按什么顺序、要不要读本地凭据、能不能新建 context、
能不能启动浏览器、失败发什么错误码」全部收进一张冻结的策略表，
``_ensure_started`` 变成对 ``policy.channels`` 的单个循环。
此后新增来源 = 表里加一行，**物理上没有第二个插入点**。

契约详见 Issue #387 的 seam 回复。

## 取值是「来源类别」，不是传输名

``auto`` / ``existing-browser`` / ``stored-cookie`` 描述的是**登录态从哪来**，
不含 ``cdp`` / ``bridge`` 这类传输名。原因：Edge 走 Bridge 或另一个 CDP 端口、
Firefox 走 Marionette，一旦枚举里出现传输名，Firefox 落地那天必然要争
「是不是也该有个 marionette」——而枚举取值是删不掉的。
浏览器品牌与具体实例由后续的 ``--session <ref>`` 表达，永不进本枚举。

## 本模块不做的事

- **不暴露 CLI 选项。** ``--browser-source`` 由 PR #404 接线（含 ``main.py`` /
  ``config.py`` / ``SCHEMA_DATA["global_options"]`` / ``config_cmd`` 校验 /
  MCP 透传五处）。本模块只提供程序化入口，默认 ``auto``。
- **不新增错误码。** ``stored-cookie`` 复用早已在枚举里的 ``CDP_UNAVAILABLE``
  （``commands/schema.py``）。``existing-browser`` 那一行连同
  ``BROWSER_SESSION_NOT_FOUND`` 由 PR #388 一起落地（ROADMAP 已点名），
  在此之前不预先声明一个发不出来的死契约。
"""

from dataclasses import dataclass

CHANNEL_BRIDGE = "bridge"
CHANNEL_CDP = "cdp"
CHANNEL_HEADLESS = "headless"

#: 分发器认识的全部通道。新增通道要同时在 ``_ensure_started`` 的循环里加分支，
#: 门禁 ``test_every_declared_channel_is_dispatchable`` 会守住这一点。
KNOWN_CHANNELS = (CHANNEL_BRIDGE, CHANNEL_CDP, CHANNEL_HEADLESS)

DEFAULT_BROWSER_SOURCE = "auto"


@dataclass(frozen=True)
class BrowserSourcePolicy:
	"""一个来源取值的完整策略。冻结以防运行时被改写。"""

	name: str
	#: 有序通道白名单 —— 尝试顺序的唯一真源。禁止在分发器里再写顺序。
	channels: tuple[str, ...]
	#: 是否允许读取 ``auth/`` 里的加密凭据并注入浏览器。
	use_stored_credentials: bool
	#: 目标浏览器没有任何 context 时，能否新建一个并注入本地 Cookie。
	#: ``False`` 表示 fail-closed —— Issue #387 明确把「把 Cookie 复制到另一个
	#: Profile」列为反面方案（凭据暴露 + 会话竞争）。
	may_create_context: bool
	#: 能否由本进程 launch 浏览器（headless 兜底）。
	allow_browser_launch: bool
	#: 是否自动探测 ``localhost:9222`` 与 ``DevToolsActivePort``。
	#: ``False`` 表示只连用户显式给的 ``--cdp-url``，让「指定 CDP」名副其实。
	auto_probe_cdp: bool
	#: 通道耗尽时发出的错误码。``None`` = 保持 master 行为：原样上抛，
	#: 由 ``display.handle_auth_errors`` 的兜底转成 ``NETWORK_ERROR``。
	#: 只有非 ``auto`` 的来源才 fail-closed —— 这条是结构性不变量，见
	#: ``fail_closed`` 属性与门禁 ``test_only_auto_is_fail_open``。
	failure_code: str | None
	failure_message: str = ""
	recovery_action: str = ""
	#: 给真人的自然语言指引（``hints.operator_actions``，TTY 下只渲染这一路）。
	operator_actions: tuple[str, ...] = ()
	#: 给 Agent 的可执行后继命令（``hints.next_actions``）。
	next_actions: tuple[str, ...] = ()

	@property
	def fail_closed(self) -> bool:
		"""是否 fail-closed。等价于「不是 auto」——见 Issue #387 决策 c。"""
		return self.failure_code is not None

	def allows(self, channel: str) -> bool:
		return channel in self.channels


POLICIES: dict[str, BrowserSourcePolicy] = {
	# ── 默认路径：行为与本模块引入前逐字一致 ────────────────────────────
	# 任何对这一行的改动都是破坏性变更：它决定了所有存量用户与所有无浏览器
	# CI 环境的行为。失败时 failure_code=None 保证信封仍是既有的 NETWORK_ERROR。
	"auto": BrowserSourcePolicy(
		name="auto",
		channels=(CHANNEL_BRIDGE, CHANNEL_CDP, CHANNEL_HEADLESS),
		use_stored_credentials=True,
		may_create_context=True,
		allow_browser_launch=True,
		auto_probe_cdp=True,
		failure_code=None,
	),
	# ── 只用存储凭据 + 用户显式指定的 CDP 端点 ──────────────────────────
	# 服务于「专用调试 profile」工作流（原 #404 的 --browser-mode cdp-required）。
	# 与它的区别有三：不跳过 Bridge 这件事被写进了 channels 而不是藏在实现里；
	# 不自动探测端点（auto_probe_cdp=False），所以「指定 CDP」名副其实；
	# 空浏览器不新建 context 注入 Cookie（may_create_context=False）。
	"stored-cookie": BrowserSourcePolicy(
		name="stored-cookie",
		channels=(CHANNEL_CDP,),
		use_stored_credentials=True,
		may_create_context=False,
		allow_browser_launch=False,
		auto_probe_cdp=False,
		failure_code="CDP_UNAVAILABLE",
		failure_message="CDP 不可用（browser-source=stored-cookie 不会降级到 Bridge 或 headless）",
		recovery_action="以 --remote-debugging-port=9222 启动 Chrome 并在官方页面登录后重试",
		operator_actions=(
			"用 --remote-debugging-port=9222 启动 Chrome，并在该窗口内手动登录 BOSS 直聘",
			"确认 --cdp-url 指向的地址可访问；该来源不会自动探测 localhost:9222",
			"若该 Chrome 刚启动、还没有任何标签页，请先打开一个页面再重试",
		),
		next_actions=("boss doctor",),
	),
	# ── 复用用户已经运行的浏览器，不读取本地凭据 ────────────────────────
	# channels 必须包含 CDP：_try_connect 早已在复用用户 context 与已打开的
	# zhipin 页签（#406 还在加强这条路径），把 CDP 排除在「现有浏览器」之外
	# 会让 CDP 里已登录的用户拿到 BROWSER_SESSION_NOT_FOUND，属错误分类。
	"existing-browser": BrowserSourcePolicy(
		name="existing-browser",
		channels=(CHANNEL_BRIDGE, CHANNEL_CDP),
		use_stored_credentials=False,
		may_create_context=False,
		allow_browser_launch=False,
		auto_probe_cdp=True,
		failure_code="BROWSER_SESSION_NOT_FOUND",
		failure_message="未发现可复用的现有浏览器会话",
		recovery_action="boss doctor",
		operator_actions=(
			"在日常浏览器中打开 BOSS 直聘并确认当前页面已经登录",
			"连接 BOSS Agent Bridge 扩展，或确认已有 CDP 浏览器可连接后重试",
		),
		next_actions=("boss doctor",),
	),
}


class BrowserSourceUnavailable(RuntimeError):
	"""该来源允许的通道全部不可用。

	``code`` 由 policy 携带而不是由异常类携带 —— 这样 ``display`` 永远只需要
	一条 except 分支，新增来源不必再动 ``display.py``。
	"""

	def __init__(
		self,
		policy: BrowserSourcePolicy,
		attempted: tuple[str, ...] = (),
		detail: str = "",
	) -> None:
		self.policy = policy
		self.attempted = attempted
		self.code = policy.failure_code or "NETWORK_ERROR"
		tried = "/".join(attempted) if attempted else "无"
		suffix = f"：{detail}" if detail else ""
		super().__init__(f"{policy.failure_message}（已尝试通道 {tried}）{suffix}")


class BrowserSourceUnsupported(RuntimeError):
	"""该平台适配器没有浏览器通道，无法满足非 auto 的来源要求。

	命令层转成 ``NOT_SUPPORTED`` 信封（复用既有错误码，不新增）。
	"""

	def __init__(self, platform: str, source: str) -> None:
		self.platform = platform
		self.source = source
		super().__init__(f"平台 {platform} 没有浏览器通道，不支持 --browser-source {source}")


def resolve_policy(source: str | None) -> BrowserSourcePolicy:
	"""把来源名解析成策略；``None`` / 空串 → ``auto``。

	取值非法时抛 ``KeyError``，由调用方（CLI / config 层）转成参数错误。
	刻意不在这里静默回落到 auto —— 静默降级正是本模块要消灭的东西。
	"""
	name = (source or DEFAULT_BROWSER_SOURCE).strip().lower()
	if name not in POLICIES:
		raise KeyError(name)
	return POLICIES[name]
