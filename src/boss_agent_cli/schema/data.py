"""`boss schema` 的静态能力描述：命令、全局选项、错误码与约定。"""

from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.api.endpoints import INDUSTRY_CODES
from boss_agent_cli.platforms import list_platforms
from boss_agent_cli.schema.error_codes import ERROR_CODES


SCHEMA_DATA = {
	"name": "boss-agent-cli",
	"description": "面向真人和 Agent 的招聘平台 CLI，共 39 个顶层命令；所有已实现能力均可直接调用。",
	"commands": {
		"login": {
			"description": "按当前平台登录（zhipin）；两种兼容运行模式共享相同能力，平台风控仍会停止当前流程。",
			"args": [],
			"options": {
				"--curl-file": {
					"type": "string",
					"default": None,
					"description": "从 Copy as cURL (bash) 文件导入 BOSS 登录态，- 表示标准输入；验证后替换原生会话，不执行原请求，不能与 --cdp 或 --cookie-source 混用",
				},
				"--timeout": {
					"type": "int",
					"default": 120,
					"minimum": 1,
					"description": "登录超时时间（秒）",
				},
				"--cdp": {
					"type": "bool",
					"default": False,
					"description": "强制 CDP 模式（跳过 Cookie 提取，CDP 不可用直接报错）",
				},
				"--force": {
					"type": "bool",
					"default": False,
					"description": "不复用任何既有登录态：跳过本地 Cookie 提取，CDP 下不扫描已登录 context 并清掉当前 context 内目标平台域 cookie 后重新登录（其他站点与其他 context 不动）；与 --cdp 正交，不能与 --curl-file / --cookie-source 混用",
				},
				"--cookie-source": {
					"type": "string",
					"default": None,
					"description": "指定浏览器提取 Cookie（如 chrome/firefox/edge），不指定则自动检测；不能与 --curl-file 或 --force 混用",
				},
			},
		},
		"platforms": {
			"description": "列出本地已注册平台与能力状态；只读本地元数据，不触发登录、浏览器、CDP 或网络请求",
			"args": [],
			"options": {
				"--platform": {
					"type": "string",
					"default": None,
					"description": "仅查看指定已注册平台",
				},
				"--capability": {
					"type": "string",
					"default": None,
					"description": "按现有本地能力矩阵反查平台状态；返回 available / not_supported，blocked_by_policy 仅保留空兼容分组",
					"choices": [
						"search",
						"detail",
						"recommend",
						"me",
						"status",
						"greet",
						"apply",
						"shortlist",
						"stats",
						"config",
						"schema",
					],
				},
			},
		},
		"status": {
			"description": "轻量检查当前登录态分层健康状态；默认不请求平台，--live 才执行一次只读在线验证",
			"args": [],
			"options": {
				"--live": {
					"type": "bool",
					"default": False,
					"description": "执行一次只读 user_info 在线验证；默认仅检查本地凭据完整性",
				},
			},
		},
		"doctor": {
			"description": "诊断本地运行环境、依赖、分层认证健康、CDP 可达性、BOSS 本地风险锁状态和网络连通性；锁未解除时不发送平台探测，浏览器通道不得用于规避平台风控",
			"args": [],
			"options": {
				"--live-probe": {
					"type": "bool",
					"default": False,
					"description": "显式执行低频只读平台探测，用于区分本地凭据完整但接口不可用的状态",
				},
				"--probe-cookies": {
					"type": "bool",
					"default": False,
					"description": "显式从本地浏览器提取 Cookie。默认不读取，避免 macOS 钥匙串弹窗",
				},
			},
		},
		"schema": {
			"description": "返回工具完整能力描述的 JSON",
			"args": [],
			"options": {
				"--format": {
					"type": "string",
					"default": "native",
					"description": "输出格式",
					"choices": ["native", "openai-tools", "anthropic-tools", "mcp-tools"],
				},
			},
		},
		"wizard": {
			"description": "启动真人纯向导，或通过 JSON 执行、恢复、查询和停止共享 workflow；role/platform/goal 取值见顶层 wizard_catalog",
			"args": [],
			"options": {
				"--input-json": {
					"type": "string",
					"default": None,
					"description": "包含 role/platform/goal/inputs/requested_steps 的 JSON object",
				},
				"--resume": {"type": "string", "default": None, "description": "恢复指定 workflow run_id"},
				"--status": {"type": "string", "default": None, "description": "查询指定 workflow run_id"},
				"--stop": {"type": "string", "default": None, "description": "停止指定 workflow run_id"},
				"--timeout": {"type": "float", "default": None, "minimum": 0.001, "description": "workflow 超时秒数"},
				"--max-retries": {"type": "int", "default": 0, "minimum": 0, "maximum": 10, "description": "可恢复步骤的最大重试次数"},
			},
		},
		"search": {
			"description": "按关键词和筛选条件搜索职位列表，可传入 BOSS 直聘搜索页 URL 复用网页筛选参数",
			"args": [
				{"name": "query", "required": False, "description": "搜索关键词；提供 --url 时可省略"},
			],
			"options": {
				"--url": {
					"type": "string",
					"default": None,
					"description": "BOSS 直聘搜索页 URL（可从网页复制完整筛选条件）",
				},
				"--preset": {
					"type": "string",
					"default": None,
					"description": "预设名称（从 boss preset add 保存）；显式筛选参数优先，preset 只补未提供的项",
				},
				"--city": {
					"type": "string",
					"default": None,
					"description": "城市名称（如 北京、上海）",
				},
				"--salary": {
					"type": "string",
					"default": None,
					"description": "薪资范围（如 10-20K）",
				},
				"--experience": {
					"type": "string",
					"default": None,
					"description": "经验要求（如 3-5年），支持逗号分隔多选",
				},
				"--education": {
					"type": "string",
					"default": None,
					"description": "学历要求（如 本科），支持逗号分隔多选",
				},
				"--industry": {
					"type": "string",
					"default": None,
					"description": "行业类型，支持逗号分隔多选",
					"choices": list(INDUSTRY_CODES),
				},
				"--scale": {
					"type": "string",
					"default": None,
					"description": "公司规模（如 100-499人），支持逗号分隔多选",
					"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
				},
				"--stage": {
					"type": "string",
					"default": None,
					"description": "融资阶段（如 已上市、A轮），支持逗号分隔多选",
					"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
				},
				"--job-type": {
					"type": "string",
					"default": None,
					"description": "职位类型（全职/兼职/实习），支持逗号分隔多选",
					"choices": ["全职", "兼职", "实习"],
				},
				"--welfare": {
					"type": "string",
					"default": None,
					"description": "福利筛选关键词（如 双休、五险一金）。启用后会逐个检查职位详情，自动翻页直到找到匹配结果",
					"examples": ["双休", "五险一金", "年终奖", "餐补", "住房补贴"],
				},
				"--active": {
					"type": "string",
					"default": None,
					"description": (
						"HR 活跃度下限，只保留不低于该档的职位。档位从高到低：在线/刚刚活跃 > 今日活跃 > 3日内活跃 > 本周活跃 > "
						"2周内活跃 > 本月活跃 > 近半年活跃 > 半年前活跃。online 只看列表（不查详情、只翻 1 页）；其余档位列表看不出时会逐个查职位详情"
						"（串行、带间隔，最多 3 页，更慢且风控风险更高，低风险场景建议 online），跳过缓存；"
						"活跃度文案无法识别的职位被排除并计入 hints.active_filter。结果带 boss_active_desc 字段"
					),
					"choices": ["online", "today", "3d", "week", "2w", "month", "half-year"],
					"examples": ["3d", "week", "3day", "3日"],
				},
				"--page": {
					"type": "int",
					"default": 1,
					"minimum": 1,
					"description": "页码",
				},
				"--with-score": {
					"type": "bool",
					"default": False,
					"description": "附加匹配分和原因",
				},
				"--sort": {
					"type": "string",
					"default": "relevance",
					"description": "排序方式：relevance 保持平台返回顺序；score 按本地 match_score 降序",
					"choices": ["relevance", "score"],
				},
				"--no-cache": {
					"type": "bool",
					"default": False,
					"description": "跳过缓存，强制请求接口",
				},
			},
		},
		"detail": {
			"description": "查看职位完整信息（职位描述、地址、招聘者信息）。传入 --job-id 走 httpx 快速通道（毫秒级），否则先查缓存、最后降级浏览器通道（秒级）",
			"args": [
				{
					"name": "security_id",
					"required": True,
					"description": "安全 ID，从 search/chat/recommend 结果中获取",
				},
			],
			"options": {
				"--job-id": {
					"type": "string",
					"default": "",
					"description": "职位加密 ID（从 search/chat 结果的 encrypt_job_id 获取，传入时走 httpx 快速通道，跳过浏览器）",
				},
				"--lid": {
					"type": "string",
					"default": "",
					"description": "列表项 ID（可选，从 search/recommend 结果的 lid 字段获取，提高匹配精度）",
				},
			},
		},
		"greet": {
			"description": "向指定招聘者打招呼。",
			"args": [
				{"name": "security_id", "required": True, "description": "安全 ID"},
				{"name": "job_id", "required": True, "description": "加密职位 ID"},
			],
			"options": {
				"--message": {
					"type": "string",
					"default": "",
					"description": "自定义打招呼消息",
				},
			},
		},
		"batch-greet": {
			"description": "搜索后按显式数量上限批量打招呼。",
			"args": [
				{"name": "query", "required": True, "description": "搜索关键词"},
			],
			"options": {
				"--city": {
					"type": "string",
					"default": None,
					"description": "城市名称",
				},
				"--salary": {
					"type": "string",
					"default": None,
					"description": "薪资范围",
				},
				"--experience": {
					"type": "string",
					"default": None,
					"description": "经验要求（如 3-5年）",
				},
				"--education": {
					"type": "string",
					"default": None,
					"description": "学历要求（如 本科）",
				},
				"--industry": {
					"type": "string",
					"default": None,
					"description": "行业类型",
					"choices": list(INDUSTRY_CODES),
				},
				"--scale": {
					"type": "string",
					"default": None,
					"description": "公司规模（如 100-499人）",
					"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
				},
				"--stage": {
					"type": "string",
					"default": None,
					"description": "融资阶段（如 已上市、A轮）",
					"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
				},
				"--job-type": {
					"type": "string",
					"default": None,
					"description": "职位类型（全职/兼职/实习）",
					"choices": ["全职", "兼职", "实习"],
				},
				"--count": {
					"type": "int",
					"default": 10,
					"minimum": 0,
					"description": "打招呼数量上限（最大 10）",
				},
				"--dry-run": {
					"type": "bool",
					"default": False,
					"description": "仅模拟执行，不实际打招呼",
				},
			},
		},
		"recommend": {
			"description": "基于用户登录态获取个性化职位推荐。",
			"args": [],
			"options": {
				"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				"--with-score": {"type": "bool", "default": False, "description": "附加匹配分和原因"},
			},
		},
		"export": {
			"description": "导出搜索结果为 HTML / CSV / JSON 文件，可传入 BOSS 直聘搜索页 URL 复用网页筛选参数",
			"args": [
				{"name": "query", "required": False, "description": "搜索关键词；提供 --url 时可省略"},
			],
			"options": {
				"--url": {
					"type": "string",
					"default": None,
					"description": "BOSS 直聘搜索页 URL（可从网页复制完整筛选条件）",
				},
				"--city": {"type": "string", "default": None, "description": "城市名称"},
				"--salary": {"type": "string", "default": None, "description": "薪资范围"},
				"--experience": {"type": "string", "default": None, "description": "经验要求，支持逗号分隔多选"},
				"--education": {"type": "string", "default": None, "description": "学历要求，支持逗号分隔多选"},
				"--industry": {"type": "string", "default": None, "description": "行业类型，支持逗号分隔多选"},
				"--scale": {"type": "string", "default": None, "description": "公司规模，支持逗号分隔多选"},
				"--stage": {"type": "string", "default": None, "description": "融资阶段，支持逗号分隔多选"},
				"--job-type": {"type": "string", "default": None, "description": "职位类型，支持逗号分隔多选"},
				"--count": {"type": "int", "default": 50, "minimum": 1, "description": "导出数量"},
				"--format": {
					"type": "string",
					"default": "csv",
					"description": "输出格式",
					"enum": ["html", "csv", "json"],
				},
				"-o, --output": {"type": "string", "default": None, "description": "输出文件路径（不指定则输出到 stdout JSON 信封）"},
				"--include-private": {
					"type": "bool",
					"default": False,
					"description": "CSV/JSON/stdout 保留明文平台标识和招聘者姓名；HTML 省略平台标识、招聘者和薪资",
				},
			},
		},
		"cities": {
			"description": "列出所有支持的城市",
			"args": [],
			"options": {},
		},
		"me": {
			"description": "获取当前登录用户的个人信息（基本信息、简历、求职期望、投递记录）",
			"args": [],
			"options": {
				"--section": {
					"type": "string",
					"default": None,
					"choices": ["user", "resume", "expect", "deliver"],
					"description": "只获取指定部分（不指定则获取全部）",
				},
				"--deliver-page": {
					"type": "int",
					"default": 1,
					"minimum": 1,
					"description": "投递记录页码",
				},
			},
		},
		"show": {
			"description": "按编号查看搜索/推荐结果中的职位详情（先 search/recommend 后使用）",
			"args": [
				{"name": "index", "required": True, "description": "搜索结果编号（1-based）"},
			],
			"options": {},
		},
		"history": {
			"description": "查看最近浏览过的职位",
			"args": [],
			"options": {
				"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
			},
		},
		"chat": {
			"description": "查看沟通列表或导出会话摘要。",
			"args": [],
			"options": {
				"--from": {
					"type": "string",
					"default": None,
					"description": "筛选发起方：boss=对方主动联系 / me=我主动打招呼",
					"choices": ["boss", "me"],
				},
				"--days": {
					"type": "int",
					"default": None,
					"minimum": 0,
					"maximum": 36500,
					"description": "只显示最近 N 天的记录",
				},
				"--export": {
					"type": "string",
					"default": None,
					"description": "导出格式：html=HTML / md=Markdown / csv=CSV / json=JSON",
					"choices": ["html", "md", "csv", "json"],
				},
				"-o, --output": {
					"type": "string",
					"default": None,
					"description": "输出文件路径（不指定则自动保存到 config.export_dir，默认 ~/Documents/files/boss，按日期命名同天覆盖）",
				},
				"--page": {
					"type": "int",
					"default": 1,
					"minimum": 1,
					"description": "页码",
				},
			},
		},
		"chatmsg": {
			"description": "查看与指定好友的聊天消息历史；--raw 输出保真结构化消息字段。",
			"args": [
				{
					"name": "security_id",
					"required": True,
					"description": "联系人的 uid（推荐，取自 chat 命令输出，跨请求稳定）或 security_id",
				},
			],
			"options": {
				"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				"--count": {"type": "int", "default": 20, "minimum": 1, "description": "每页消息数量"},
				"--raw": {
					"type": "bool",
					"default": False,
					"description": "保真输出结构化 body、链接、职位卡片字段和原始消息对象",
				},
			},
		},
		"chat-summary": {
			"description": "基于聊天历史生成结构化摘要与下一步建议。",
			"args": [
				{
					"name": "security_id",
					"required": True,
					"description": "联系人的 uid（推荐，取自 chat 命令输出，跨请求稳定）或 security_id",
				},
			],
			"options": {
				"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				"--count": {"type": "int", "default": 20, "minimum": 1, "description": "每页消息数量"},
			},
		},
		"mark": {
			"description": "给联系人添加或移除标签。",
			"args": [
				{
					"name": "security_id",
					"required": True,
					"description": "联系人的 uid（推荐，取自 chat 命令输出，跨请求稳定）或 security_id",
				},
			],
			"options": {
				"--label": {
					"type": "string",
					"required": True,
					"description": "标签名称或 ID",
					"enum": ["新招呼", "沟通中", "已约面", "已获取简历", "已交换电话", "已交换微信", "不合适", "收藏"],
				},
				"--remove": {"type": "boolean", "default": False, "description": "移除标签（默认为添加）"},
			},
		},
		"exchange": {
			"description": "请求交换联系方式（手机号或微信）。",
			"args": [
				{
					"name": "security_id",
					"required": True,
					"description": "联系人的 uid（推荐，取自 chat 命令输出，跨请求稳定）或 security_id",
				},
			],
			"options": {
				"--type": {
					"type": "string",
					"default": "phone",
					"description": "交换类型",
					"enum": ["phone", "wechat"],
				},
			},
		},
		"interviews": {
			"description": "查看面试邀请列表",
			"args": [],
			"options": {},
		},
		"logout": {
			"description": "退出登录，清除本地保存的登录态",
			"args": [],
			"options": {},
		},
		"watch": {
			"description": "本地保存搜索条件，并可通过 run 子命令增量拉取平台数据。",
			"args": [],
			"options": {
				"add": {
				"description": "保存一条可复用的搜索条件（名称 + 关键词 + 筛选），之后用 run 增量拉取。",
					"args": [
						{"name": "name", "required": True, "description": "watch 名称"},
						{"name": "query", "required": True, "description": "搜索关键词"},
					],
					"--city": {"type": "string", "default": None, "description": "城市名称"},
					"--salary": {"type": "string", "default": None, "description": "薪资范围"},
					"--experience": {"type": "string", "default": None, "description": "经验要求"},
					"--education": {"type": "string", "default": None, "description": "学历要求"},
					"--industry": {
						"type": "string",
						"default": None,
						"description": "行业类型（可选值见 boss schema）",
						"choices": list(INDUSTRY_CODES),
					},
					"--scale": {
						"type": "string",
						"default": None,
						"description": "公司规模",
						"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
					},
					"--stage": {
						"type": "string",
						"default": None,
						"description": "融资阶段",
						"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
					},
					"--job-type": {
						"type": "string",
						"default": None,
						"description": "职位类型",
						"choices": ["全职", "兼职", "实习"],
					},
					"--welfare": {"type": "string", "default": None, "description": "福利筛选"},
				},
				"list": {},
				"remove": {
				"description": "删除一条本地 watch；名字不存在时保持幂等（不报错）。",
					"args": [
						{"name": "name", "required": True, "description": "要删除的 watch 名称"},
					],
				},
				"run": {
				"description": "执行一条或全部 watch，把新增职位写入本地缓存。",
					"args": [
						{"name": "name", "required": False, "description": "只运行指定 watch；省略时运行全部"},
					],
					"--all": {"type": "bool", "default": False, "description": "跑所有已保存 watch；风控命中时停止后续 watch"},
				},
			},
			"subcommands": {
				"add": "保存一组搜索条件为 watch",
				"list": "列出已保存的 watch",
				"remove": "删除指定 watch",
				"run": "增量执行 watch 并报告新职位",
			},
		},
		"crawl": {
			"description": "可恢复的 DrissionPage 批量采集（子命令：configure/run/shortlist/start/status/results/resume/stop）；同 data_dir 风险锁在浏览器构造前阻断启动/恢复，新风险锁存并保存断点；仅读取/本地导入不受影响。",
			"args": [],
			"options": {
				"configure": {
				"description": "设置 crawl 专用 Chrome 和 Hook 配置。",
					"--chrome-path": {"type": "string", "default": None, "description": "chrome.exe 路径"},
					"--port": {"type": "int", "default": None, "minimum": 1, "maximum": 65535, "description": "Chrome 远程调试端口（1-65535）"},
					"--max-requests": {"type": "int", "default": None, "minimum": 1, "description": "每个 crawl run 的总请求上限（≥1）"},
					"--max-details": {"type": "int", "default": None, "minimum": 1, "description": "每个 crawl run 的详情请求上限（≥1）"},
					"--max-seconds": {"type": "int", "default": None, "minimum": 1, "description": "每个 crawl run 的墙钟秒数上限（≥1）"},
					"--max-retries": {"type": "int", "default": None, "minimum": 0, "description": "单请求的重试次数上限（≥0）"},
				},
				"run": {
				"description": "开始一个可恢复的 DP 批量采集任务。",
					"args": [
						{"name": "query", "required": True, "description": "搜索关键词"},
					],
					"--city": {"type": "string", "required": True, "description": "城市名称或数字城市代码"},
					"--pages": {"type": "int", "default": 5, "minimum": 1, "description": "严格正数页数上限"},
					"--with-detail": {"type": "bool", "default": False, "description": "串行补全所有职位的 job_card"},
					"--hook-profile": {
						"type": "string",
						"default": "none",
						"enum": ["screenshot-full", "none"],
						"description": "页面 Hook 配置；none 不注入脚本，screenshot-full 需同时给 --hook-dir",
					},
					"--hook-dir": {
						"type": "string",
						"default": None,
						"description": "screenshot-full 必填：用户已授权的原始 Hook 目录，须含 SHA256SUMS",
					},
					"--chrome-path": {
						"type": "string",
						"default": None,
						"description": "覆盖配置的 Chrome 可执行文件路径",
					},
					"--port": {
						"type": "int",
						"default": None,
						"minimum": 1,
						"maximum": 65535,
						"description": "覆盖配置的 Chrome 远程调试端口（1-65535）",
					},
				},
				"resume": {
				"description": "从已保存的页游标和详情队列恢复采集。",
					"args": [
						{"name": "run_id", "required": True, "description": "要恢复的 crawl run 标识"},
					],
					"--pages": {
						"type": "int",
						"default": None,
						"minimum": 1,
						"description": "覆盖原任务的严格正数页数上限",
					},
					"--with-detail": {
						"type": "bool",
						"default": False,
						"description": "补全已采职位和后续职位的 job_card",
					},
					"--background": {"type": "bool", "default": False, "description": "后台恢复并立即返回 run_id"},
				},
				"results": {
				"description": "读取 crawl 已采集结果，不打开浏览器。",
					"args": [
						{"name": "run_id", "required": True, "description": "crawl run 标识"},
					],
					"--page": {"type": "int", "default": None, "minimum": 1, "description": "仅返回指定采集页"},
					"--detail-status": {
						"type": "string",
						"default": None,
						"enum": ["completed", "pending"],
						"description": "按详情抓取状态筛选：completed=已抓取完整详情，pending=仅列表数据待抓取",
					},
				},
				"shortlist": {
				"description": "将 crawl 结果导入原项目的本地职位候选池。",
					"args": [
						{"name": "run_id", "required": True, "description": "crawl run 标识"},
					],
					"--selector": {
						"type": "string",
						"default": None,
						"description": "导入 results 返回的非敏感 selector，可重复传入",
					},
					"--all": {"type": "bool", "default": False, "description": "导入该 run 的全部可关联职位"},
					"--tags": {"type": "string", "default": "", "description": "写入候选池的本地标签，逗号分隔"},
					"--note": {"type": "string", "default": "", "description": "写入候选池的本地备注"},
				},
				"start": {
				"description": "创建并后台运行一个 crawl 任务；适合 MCP 的任务式调度。",
					"args": [
						{"name": "query", "required": True, "description": "搜索关键词"},
					],
					"--city": {"type": "string", "required": True, "description": "城市名称或数字城市代码"},
					"--pages": {"type": "int", "default": 5, "minimum": 1, "description": "严格正数页数上限"},
					"--with-detail": {"type": "bool", "default": False, "description": "串行补全所有职位的 job_card"},
					"--hook-profile": {
						"type": "string",
						# run / start 省略时都按 "none" 执行（`hook_profile or "none"`），
						# 两个子命令的 schema 默认值必须一致，否则 Agent 会以为 start
						# 的「未指定」等于某种额外行为。
						"default": "none",
						"enum": ["screenshot-full", "none"],
						"description": "页面 Hook 配置；none 不注入脚本，screenshot-full 需同时给 --hook-dir",
					},
					"--hook-dir": {
						"type": "string",
						"default": None,
						"description": "与 screenshot-full 一起显式指定的用户脚本目录，须含 SHA256SUMS",
					},
				},
				"status": {
				"description": "读取 crawl 任务状态，不打开浏览器。",
					"args": [
						{"name": "run_id", "required": True, "description": "crawl run 标识"},
					],
				},
				"stop": {
				"description": "请求运行中的 crawl 在下一安全点停止并保留 checkpoint。",
					"args": [
						{"name": "run_id", "required": True, "description": "要停止的 crawl run 标识"},
					],
				},
			},
			"subcommands": {
				"configure": "设置 crawl 专用 Chrome 路径、端口和固定预算",
				"run <query>": "开始可恢复的批量职位采集",
				"start <query>": "创建后台任务并立即返回 run_id（供 MCP 轮询）",
				"status <run_id>": "读取页游标、详情进度和风险状态",
				"results <run_id>": "读取已持久化职位结果",
				"resume <run_id>": "从已保存页游标和详情队列继续；不清除风险锁，须由用户确认官方目标页恢复后显式 clean --risk-lock",
				"stop <run_id>": "请求运行中的 crawl 在下一个安全点停止并保留断点",
				"shortlist <run_id>": "将 crawl 结果导入本地职位候选池",
			},
			"mcp_tools": [
				{
					"name": "boss_crawl_status",
					"description": "读取 crawl 页游标、职位数、详情进度和风险状态。",
					"inputSchema": {
						"type": "object",
						"properties": {
							"run_id": {"type": "string", "description": "crawl run 标识，由 boss crawl start 返回"},
						},
						"required": ["run_id"],
					},
				},
				{
					"name": "boss_crawl_results",
					"description": "读取持久化 crawl 职位，可按页码和详情状态筛选。",
					"inputSchema": {
						"type": "object",
						"properties": {
							"run_id": {"type": "string", "description": "crawl run 标识，由 boss crawl start 返回"},
							"page": {"type": "integer", "description": "只返回该 crawl 页的结果，省略则返回全部"},
							"detail_status": {
								"type": "string",
								"enum": ["completed", "pending"],
								"description": "按职位详情抓取状态筛选：completed 已补全详情，pending 仅有列表信息",
							},
						},
						"required": ["run_id"],
					},
				},
				{
					"name": "boss_crawl_shortlist",
					"description": "将 crawl results 返回的 selector 导入本地 shortlist，不请求 BOSS。",
					"inputSchema": {
						"type": "object",
						"properties": {
							"run_id": {"type": "string", "description": "crawl run 标识，由 boss crawl start 返回"},
							"selectors": {
								"type": "array",
								"items": {"type": "string"},
								"description": "要导入的职位 selector 列表，取自 boss_crawl_results 的返回；与 all 二选一",
							},
							"all": {
								"type": "boolean",
								"default": False,
								"description": "导入该 run 的全部可关联职位；与 selectors 二选一",
							},
							"tags": {"type": "string", "description": "写入候选池的本地标签，逗号分隔"},
							"note": {"type": "string", "description": "写入候选池的本地备注"},
						},
						"required": ["run_id"],
					},
				},
			],
		},
		"preset": {
			"description": "管理可复用搜索预设（子命令：add/list/remove）",
			"args": [],
			"options": {
				"add": {
				"description": "保存一组可复用的搜索参数，之后用 `boss search --preset <名称>` 调用。",
					"args": [
						{"name": "name", "required": True, "description": "预设名称"},
						{"name": "query", "required": True, "description": "搜索关键词"},
					],
					"--city": {"type": "string", "default": None, "description": "城市名称"},
					"--salary": {"type": "string", "default": None, "description": "薪资范围"},
					"--experience": {"type": "string", "default": None, "description": "经验要求"},
					"--education": {"type": "string", "default": None, "description": "学历要求"},
					"--industry": {
						"type": "string",
						"default": None,
						"description": "行业类型（可选值见 boss schema）",
						"choices": list(INDUSTRY_CODES),
					},
					"--scale": {
						"type": "string",
						"default": None,
						"description": "公司规模",
						"choices": ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
					},
					"--stage": {
						"type": "string",
						"default": None,
						"description": "融资阶段",
						"choices": ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
					},
					"--job-type": {
						"type": "string",
						"default": None,
						"description": "职位类型",
						"choices": ["全职", "兼职", "实习"],
					},
					"--welfare": {"type": "string", "default": None, "description": "福利筛选"},
					"--active": {
						"type": "string",
						"default": None,
						"description": "HR 活跃度下限：online / today / 3d / week / 2w / month / half-year",
						"choices": ["online", "today", "3d", "week", "2w", "month", "half-year"],
					},
				},
				"list": {},
				"remove": {
				"description": "删除一条本地搜索预设；名字不存在时保持幂等（不报错）。",
					"args": [
						{"name": "name", "required": True, "description": "要删除的预设名称"},
					],
				},
			},
			"subcommands": {
				"add": "保存一组搜索筛选为可复用预设",
				"list": "列出已保存的预设",
				"remove": "删除指定预设",
			},
		},
		"pipeline": {
			"description": "聚合聊天和面试数据生成候选进度视图。",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "minimum": 0, "description": "超过 N 天未推进则标记为 follow_up"},
			},
		},
		"follow-up": {
			"description": "基于聊天和面试数据筛出需要跟进的候选项。",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "minimum": 0, "description": "超过 N 天未推进则视为 follow_up"},
			},
		},
		"apply": {
			"description": "发起投递或立即沟通动作。",
			"args": [
				{"name": "security_id", "required": True, "description": "安全 ID"},
				{"name": "job_id", "required": True, "description": "加密职位 ID"},
			],
			"options": {
				"--lid": {
					"type": "string",
					"default": "",
					"description": "列表项 ID（可选，从 search/recommend 结果的 lid 字段获取）",
				},
			},
		},
		"shortlist": {
			"description": "管理本地职位候选池（子命令：add/list/annotate/compare/remove），支持本地标签、备注和离线对比",
			"args": [],
			"options": {
				"list": {},
				"add": {
				"description": "把一个职位加入本地候选池，可同时写标签与备注。",
					"args": [
						{"name": "security_id", "required": True, "description": "安全 ID"},
						{"name": "job_id", "required": True, "description": "加密职位 ID"},
					],
					"--title": {"type": "string", "default": "", "description": "职位名称"},
					"--company": {"type": "string", "default": "", "description": "公司名称"},
					"--city": {"type": "string", "default": "", "description": "城市"},
					"--salary": {"type": "string", "default": "", "description": "薪资"},
					"--source": {"type": "string", "default": "manual", "description": "来源，如 search/recommend/show/manual"},
					"--lid": {"type": "string", "default": "", "description": "与 security_id 同一列表项的访问上下文（可选；不推断有效期）"},
					"--tags": {"type": "string", "default": "", "description": "本地标签，逗号分隔"},
					"--note": {"type": "string", "default": "", "description": "本地备注"},
				},
				"annotate": {
				"description": "给候选池条目增删标签或替换备注；条目不存在时报错而不是静默成功。",
					"args": [
						{"name": "security_id", "required": True, "description": "安全 ID"},
						{"name": "job_id", "required": True, "description": "加密职位 ID"},
					],
					"--add-tag": {"type": "string", "default": None, "description": "添加本地标签，可重复"},
					"--remove-tag": {"type": "string", "default": None, "description": "移除本地标签，可重复"},
					"--note": {"type": "string", "default": None, "description": "替换本地备注"},
				},
				"compare": {
				"description": "并排比较候选池条目（可按标签过滤）。",
					"--tag": {"type": "string", "default": None, "description": "只比较包含该本地标签的候选职位"},
				},
				"remove": {
				"description": "从本地候选池中移除一个职位。",
					"args": [
						{"name": "security_id", "required": True, "description": "安全 ID"},
						{"name": "job_id", "required": True, "description": "加密职位 ID"},
					],
				},
			},
			"subcommands": {
				"add": "加入本地候选池，可附加本地标签和备注",
				"list": "列出本地候选池职位",
				"annotate": "更新候选职位的本地标签和备注",
				"compare": "本地对比候选职位，可按标签过滤",
				"remove": "从本地候选池移除职位",
			},
		},
		"favorites": {
			"description": "读取 BOSS 职位收藏并同步到本地候选池（子命令：list/sync）。list 远端只读预览并呈现职位有效状态，sync 仅将明确有效职位写入本地 shortlist（upsert）；默认低风险、用户主动触发。",
			"args": [],
			"options": {
				"list": {
				"description": "预览职位收藏单页（不落库）。",
					"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				},
				"sync": {},
			},
			"subcommands": {
				"list": "预览职位收藏单页及有效状态（不落库）",
				"sync": "同步明确有效的职位收藏到本地候选池（远端只读拉取，本地 upsert；刷新动态访问 ID 并保留首次收藏时间）",
			},
		},
		"digest": {
			"description": "汇总新增职位、待跟进会话和面试项的日报。",
			"args": [],
			"options": {
				"--days-stale": {"type": "int", "default": 3, "minimum": 0, "maximum": 36500, "description": "超过 N 天未推进则视为 follow_up"},
				"--format": {
					"type": "string",
					"default": "json",
					"enum": ["json", "md"],
					"description": "输出格式（json 信封 / md 可直发邮件飞书）",
				},
				"-o, --output": {
					"type": "string",
					"default": None,
					"description": "Markdown 输出路径（仅 --format md 时有效）",
				},
			},
		},
		"config": {
			"description": "查看和修改配置项（子命令：list/get/set/reset）",
			"args": [],
			"options": {
				"list": {},
				"get": {
				"description": "查看单个配置项的值。",
					"args": [
						{"name": "key", "required": True, "description": "配置项键名"},
					],
				},
				"set": {
				"description": "修改配置项。",
					"args": [
						{"name": "key", "required": True, "description": "配置项键名"},
						{"name": "value", "required": True, "description": "新的配置值"},
					],
				},
				"reset": {
				"description": "将配置项恢复为默认值。",
					"args": [
						{"name": "key", "required": True, "description": "要恢复默认值的配置项键名"},
					],
				},
			},
			"subcommands": {
				"list": "显示当前全部配置",
				"get": "查看单个配置项",
				"set": "修改配置项",
				"reset": "恢复配置项为默认值",
			},
		},
		"clean": {
			"description": "清理过期缓存和临时文件",
			"args": [],
			"options": {
				"--dry-run": {"type": "bool", "default": False, "description": "仅预览将清理的内容"},
				"--all": {"type": "bool", "default": False, "description": "清理全部缓存"},
				"--days": {"type": "int", "default": 30, "minimum": 0, "maximum": 36500, "description": "清理超过指定天数的快照和导出"},
				"--privacy": {
					"type": "bool",
					"default": False,
					"description": "清理本地敏感数据（登录会话、简历、聊天快照和导出文件）；清理后需要重新登录",
				},
				"--risk-lock": {
					"type": "bool",
					"default": False,
					"description": "用户确认 BOSS 官方目标业务页风险解除后显式清除本地风险锁；不解除服务器风险，Agent 不应自行执行",
				},
			},
		},
		"stats": {
			"description": "投递转化漏斗统计（只读聚合打招呼/投递/候选池/监控）",
			"args": [],
			"options": {
				"--days": {"type": "int", "default": 30, "minimum": 0, "maximum": 36500, "description": "统计窗口天数"},
				"--format": {
					"type": "string",
					"default": "json",
					"enum": ["json", "html"],
					"description": "输出格式：json（JSON 信封）或 html（自包含报表）",
				},
				"-o, --output": {
					"type": "string",
					"default": None,
					"description": "HTML 输出路径（仅 --format html 时有效）",
				},
			},
		},
		"resume": {
			"description": "本地简历管理（子命令：init/list/show/edit/delete/export/import/clone/diff/link/applications）",
			"args": [],
			"options": {
				"list": {},
				"init": {
				"description": "从默认模板初始化本地简历。",
					"--name": {"type": "string", "default": None, "description": "简历名称"},
					"--template": {"type": "string", "default": None, "description": "模板名称（如 default）"},
				},
				"show": {
				"description": "查看简历详情。",
					"args": [
						{"name": "name", "required": True, "description": "简历名称"},
					],
				},
				"edit": {
				"description": "编辑简历字段。",
					"args": [
						{"name": "name", "required": True, "description": "简历名称"},
					],
					"--field": {"type": "string", "required": True, "description": "字段路径（如 title, personal_info.layout）"},
					"--value": {"type": "string", "required": True, "description": "新值"},
				},
				"delete": {
				"description": "删除简历。",
					"args": [
						{"name": "name", "required": True, "description": "要删除的简历名称"},
					],
				},
				"export": {
				"description": "导出简历。",
					"args": [
						{"name": "name", "required": True, "description": "简历名称"},
					],
					"--format": {
						"type": "string",
						"default": "json",
						"description": "导出格式",
						"enum": ["pdf", "json", "html"],
					},
					"-o, --output": {"type": "string", "default": None, "description": "输出文件路径"},
				},
				"import": {
				"description": "导入 JSON 简历。",
					"args": [
						{"name": "file_path", "required": True, "description": "JSON 简历文件路径"},
					],
					"--force": {"type": "bool", "default": False, "description": "覆盖同名简历（会丢弃本地已有的编辑）"},
				},
				"clone": {
				"description": "复制简历为新版本。",
					"args": [
						{"name": "name", "required": True, "description": "源简历名称"},
						{"name": "new_name", "required": True, "description": "新简历名称"},
					],
				},
				"diff": {
				"description": "对比两份简历差异。",
					"args": [
						{"name": "name1", "required": True, "description": "第一份简历名称"},
						{"name": "name2", "required": True, "description": "第二份简历名称"},
					],
				},
				"link": {
				"description": "关联简历与职位。",
					"args": [
						{"name": "name", "required": True, "description": "简历名称"},
						{"name": "security_id", "required": True, "description": "安全 ID"},
						{"name": "job_id", "required": True, "description": "加密职位 ID"},
					],
					"--title": {"type": "string", "default": "", "description": "职位名称"},
					"--company": {"type": "string", "default": "", "description": "公司名称"},
				},
				"applications": {
				"description": "查看简历关联的所有职位。",
					"args": [
						{"name": "name", "required": True, "description": "简历名称"},
					],
				},
			},
			"subcommands": {
				"init": "从 BOSS 直聘简历或默认模板初始化本地简历",
				"list": "列出所有本地简历",
				"show": "查看简历详情",
				"edit": "编辑简历字段",
				"delete": "删除简历",
				"export": "导出为 PDF/JSON/HTML",
				"import": "导入 JSON 简历（兼容 wzdnzd/zine0 格式）",
				"clone": "复制简历为新版本",
				"diff": "对比两份简历差异",
				"link": "关联简历与职位",
				"applications": "查看简历关联的所有职位",
			},
		},
		"ai": {
			"description": "AI 简历优化、聊天回复与本地模型管理（子命令：config/local/analyze-jd/polish/optimize/suggest/fit/reply/interview-prep/chat-coach/suggest-keywords/resume-optimize/cover-letter）",
			"args": [],
			"options": {
				"config": {
				"description": "查看或设置 AI 服务配置。",
					"--provider": {
						"type": "string",
						"default": None,
						"description": "AI 提供商（openai/deepseek/moonshot/openrouter/qwen/zhipu/siliconflow/ollama/vllm/custom）",
					},
					"--model": {"type": "string", "default": None, "description": "模型名称（如 gpt-4o / deepseek-chat）"},
					"--api-key": {"type": "string", "default": None, "description": "API 密钥（将加密存储）"},
					"--base-url": {
						"type": "string",
						"default": None,
						"description": "自定义 API 基础地址（provider=custom 或自建代理时使用）",
					},
					"--temperature": {"type": "float", "default": None, "minimum": 0, "description": "生成温度"},
					"--max-tokens": {"type": "int", "default": None, "minimum": 1, "description": "最大令牌数"},
				},
				"local": {
				"description": "本地模型管理：查看配置、下载、导入与健康检查。",
					"status": {},
					"configure": {
					"description": "配置本地 OpenAI-compatible 模型服务。",
						"--runtime": {
							"type": "string",
							"required": True,
							"description": "本地运行时",
							"enum": ["ollama", "vllm"],
						},
						"--model": {"type": "string", "required": True, "description": "本地模型名称"},
						"--base-url": {"type": "string", "default": None, "description": "本地服务基础地址"},
					},
					"pull": {
					"description": "通过 Ollama 下载模型权重。",
						"--model": {"type": "string", "required": True, "description": "要下载的模型名称"},
						"--confirm-download": {
							"type": "bool",
							"default": False,
							"description": "确认下载并接受模型体积与来源",
						},
					},
					"import": {
					"description": "导入离线模型文件或目录到本地数据目录。",
						"--path": {"type": "string", "required": True, "description": "本地模型文件路径"},
						"--model": {"type": "string", "required": True, "description": "导入后的模型名称"},
					},
					"smoke": {},
				},
				"analyze-jd": {
				"description": "分析职位描述并评估简历匹配度。",
					"args": [
						{"name": "jd_text", "required": True, "description": "职位描述文本或 @文件路径"},
					],
					"--resume": {"type": "string", "required": True, "description": "对比的本地简历名称"},
				},
				"polish": {
				"description": "通用简历润色优化。",
					"args": [
						{"name": "resume_name", "required": True, "description": "要润色的本地简历名称"},
					],
				},
				"optimize": {
				"description": "基于目标职位描述优化简历。",
					"args": [
						{"name": "resume_name", "required": True, "description": "要优化的本地简历名称"},
					],
					"--jd": {"type": "string", "required": True, "description": "目标职位描述文本或 @文件路径"},
				},
				"suggest": {
				"description": "基于目标职位描述给出改进建议（不修改简历）。",
					"args": [
						{"name": "resume_name", "required": True, "description": "要分析的本地简历名称"},
					],
					"--jd": {"type": "string", "required": True, "description": "目标职位描述文本或 @文件路径"},
				},
				"fit": {
				"description": "基于本地简历和候选池缓存详情生成逐岗匹配报告。",
					"--resume": {"type": "string", "required": True, "description": "本地简历名称"},
					"--limit": {"type": "int", "default": 20, "minimum": 1, "description": "最多分析的候选池职位数"},
				},
				"reply": {
				"description": "基于招聘者消息生成回复草稿（2-3 条候选）。",
					"args": [
						{"name": "recruiter_message", "required": True, "description": "招聘者发来的消息文本"},
					],
					"--context": {"type": "string", "default": "", "description": "会话上下文（可选，或 @文件路径）"},
					"--resume": {"type": "string", "default": None, "description": "参考简历名称（可选）"},
					"--tone": {
						"type": "string",
						"default": "简洁专业",
						"description": "语气偏好",
						"enum": ["简洁专业", "热情积极", "谨慎确认"],
					},
				},
				"interview-prep": {
				"description": "基于目标职位生成模拟面试题。",
					"args": [
						{"name": "jd_text", "required": True, "description": "职位描述文本或 @文件路径"},
					],
					"--resume": {"type": "string", "default": None, "description": "参考简历名称（可选，根据简历真实经历定制问题）"},
					"--count": {"type": "int", "default": 10, "minimum": 1, "description": "题量"},
				},
				"chat-coach": {
				"description": "基于聊天记录给出沟通技巧诊断与下一步建议。",
					"args": [
						{"name": "chat_text", "required": True, "description": "聊天记录文本或 @文件路径"},
					],
					"--resume": {"type": "string", "default": None, "description": "参考简历名称（可选）"},
					"--style": {
						"type": "string",
						"default": "简洁专业",
						"description": "沟通风格偏好（如 简洁专业/积极主动/谨慎稳重）",
					},
				},
				"suggest-keywords": {
				"description": "基于候选池分析推荐搜索关键词组合。",
					"--limit": {"type": "int", "default": 20, "minimum": 1, "description": "候选池职位数上限"},
				},
				"resume-optimize": {
				"description": "基于目标岗位优化简历措辞（仅建议，不修改简历）。",
					"args": [
						{"name": "resume_name", "required": True, "description": "要优化的本地简历名称"},
					],
					"--jd": {"type": "string", "default": None, "description": "目标职位描述文本或 @文件路径"},
					"--job-id": {"type": "string", "default": None, "description": "从缓存读取职位描述的 job_id"},
				},
				"cover-letter": {
				"description": "基于本地简历与目标岗位起草求职信/自我介绍（仅草稿，不发送）。",
					"args": [
						{"name": "resume_name", "required": True, "description": "本地简历名称"},
					],
					"--jd": {"type": "string", "default": None, "description": "目标职位描述文本或 @文件路径"},
					"--job-id": {"type": "string", "default": None, "description": "从缓存读取职位描述的 job_id"},
					"--tone": {
						"type": "string",
						"default": "简洁专业",
						"description": "求职信语气",
						"enum": ["简洁专业", "热情积极", "谨慎稳重"],
					},
					"--lang": {
						"type": "string",
						"default": "zh",
						"description": "输出语言（zh 中文 / en 英文）",
						"enum": ["zh", "en"],
					},
				},
			},
			"subcommands": {
				"config": "配置 AI 服务提供商和模型",
				"local": "本地模型状态、配置、下载、导入和 smoke 测试",
				"analyze-jd": "分析职位描述并评估简历匹配度",
				"polish": "通用简历润色",
				"optimize": "基于目标职位描述优化简历",
				"suggest": "基于目标职位描述给出优化建议（不修改简历）",
				"fit": "fit --resume <name> [--limit N]：本地简历 × 候选池缓存详情的匹配报告",
				"reply": "基于招聘者消息生成回复草稿（2-3 条候选）",
				"interview-prep": "基于目标职位生成模拟面试题与准备建议",
				"chat-coach": "基于聊天记录诊断沟通状态并给出下一步建议",
				"suggest-keywords": "基于候选池分析推荐搜索关键词组合",
				"resume-optimize": "基于目标岗位优化简历措辞（仅建议，不修改简历）",
				"cover-letter": "基于本地简历与目标岗位起草求职信/自我介绍（仅草稿，不发送）",
			},
		},
		"agent": {
			"description": (
				"招聘自动化与候选人 crawl 编排入口。run/train 可直接执行满足阈值的动作；"
				"review/pending 仅管理旧版本遗留队列；crawl 可新建或分析已有 run。"
			),
			"args": [],
			"options": {
				"run": {
				"description": "运行一轮招聘自动化。",
					"--resume": {
						"type": "bool",
						"default": False,
						"description": "操作者确认官方验证完成后显式解除停止状态，保留已确认动作与断点；Agent 不应自行添加",
					},
					"--dry-run": {
						"type": "bool",
						"default": False,
						"description": "只演练自动化决策，不执行真实平台动作",
					},
					"--limit": {
						"type": "int",
						"default": None,
						"minimum": 1,
						"description": "本轮最多处理多少个会话",
					},
				},
				"crawl": {
				"description": "新建或读取 crawl 结果，导入候选池并执行 ai fit。",
					"--run-id": {"type": "string", "default": None, "description": "分析指定的已完成 crawl run"},
					"--query": {"type": "string", "default": None, "description": "新采集的职位关键词"},
					"--city": {"type": "string", "default": None, "description": "新采集城市；与 --query 同时使用"},
					"--pages": {"type": "int", "default": 5, "minimum": 1, "description": "新采集严格页数上限"},
					"--with-detail": {"type": "bool", "default": False, "description": "新采集时串行补职位详情"},
					"--resume": {"type": "string", "required": True, "description": "用于 ai fit 的本地简历名称"},
					"--limit": {"type": "int", "default": 10, "minimum": 1, "description": "按匹配分返回前 N 个职位"},
				},
				"train": {
				"description": "运行训练校准模式：默认演练，--live 直接执行满足阈值的动作。",
					"--dry-run": {"type": "bool", "default": True, "description": "训练模式默认只演练；与 --live 互斥"},
					"--live": {"type": "bool", "default": False, "description": "直接执行满足阈值的动作；只有操作者明确授权时才可用"},
					"--limit": {"type": "int", "default": None, "minimum": 1, "description": "本轮最多处理多少个会话"},
				},
				"stop": {
				"description": "打开招聘自动化熔断。",
					"--reason": {"type": "string", "default": "manual-stop", "description": "熔断原因"},
				},
				"control": {},
				"pending": {
					"description": "待执行动作队列。",
					"list": {},
				},
				"stats": {},
				"review": {
				"description": "管理旧版本遗留的人工复核队列。",
					"list": {},
					"approve": {
					"description": "处理旧版本复核项并写入兼容 pending 队列。",
						"args": [
							{"name": "review_id", "required": True, "description": "旧版本复核项 ID"},
						],
					},
					"reject": {
					"description": "拒绝一条旧版本人工复核动作，并记录跳过事件。",
						"args": [
							{"name": "review_id", "required": True, "description": "旧版本复核项 ID"},
						],
						"--reason": {"type": "string", "default": "human-rejected", "description": "拒绝原因"},
					},
				},
			},
			"subcommands": {
				"run": "运行一轮招聘自动化；--resume 显式恢复停止状态并保留断点",
				"train": "训练校准模式：默认演练，--live 直接执行满足阈值的动作",
				"review list": "查看旧版本遗留的人工复核队列",
				"review approve <id>": "处理旧版本复核项并写入兼容 pending 队列",
				"review reject <id>": "拒绝旧版本复核项并记录跳过事件",
				"pending list": "查看旧版本遗留的待执行动作队列",
				"stats": "查看招聘自动化统计",
				"control": "查看本地控制台入口信息",
				"stop": "打开招聘自动化熔断",
				"crawl": "候选人链路：新建或读取 crawl → shortlist → ai fit",
			},
		},
		"hr": {
			"description": "招聘者模式快捷命令。已实现的候选人搜索、简历、沟通、联系方式交换和消息发送在 assisted/research 下均可调用。",
			"args": [],
			"options": {
				"applications": {
				"description": "查看候选人投递申请列表。",
					"--job-id": {"type": "string", "default": None, "description": "按职位筛选"},
					"--label-id": {"type": "int", "default": 0, "minimum": 0, "description": "按标签筛选（0=全部, 1=新招呼, 2=沟通中）"},
					"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				},
				"resume": {
				"description": "查看候选人简历或请求交换联系方式。",
					"args": [
						{"name": "geek_id", "required": False, "description": "候选人 geek ID；省略时使用 --security-id"},
					],
					"--job-id": {"type": "string", "default": "", "description": "职位 ID"},
					"--security-id": {"type": "string", "default": None, "description": "安全 ID"},
					"--exchange": {"type": "bool", "default": False, "description": "请求交换联系方式"},
					"--type": {
						"type": "string",
						"default": "phone",
						"description": "交换类型：phone=手机号 / wechat=微信",
						"enum": ["phone", "wechat"],
					},
					"--uid": {"type": "int", "default": None, "description": "已弃用：旧的交换联系方式参数，当前被忽略"},
					"--gid": {"type": "int", "default": None, "description": "已弃用：旧的交换联系方式参数，当前被忽略"},
					"--friend-id": {"type": "int", "default": None, "minimum": 1, "description": "候选人 friendId（交换联系方式新路径）"},
					"--raw": {"type": "bool", "default": False, "description": "输出原始 API 数据（不解析）"},
				},
				"chat": {
				"description": "查看与候选人的沟通列表。",
					"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
					"--job-id": {"type": "string", "default": None, "description": "按职位筛选"},
					"--label-id": {"type": "int", "default": 0, "minimum": 0, "description": "按标签筛选（0=全部, 1=新招呼, 2=沟通中）"},
					"--summary-limit": {
						"type": "int",
						"default": 100,
						"minimum": 0,
						"maximum": 300,
						"description": "最多给前多少个会话补最近消息（每 50 个一次串行请求；0=不请求，上限 300）",
					},
				},
				"chatmsg": {
				"description": "与指定候选人的聊天消息历史。",
					"args": [
						{"name": "friend_id", "required": True, "description": "候选人会话 friend_id"},
					],
					"--count": {"type": "int", "default": 20, "minimum": 1, "description": "消息数量"},
					"--max-msg-id": {"type": "int", "default": None, "minimum": 1, "description": "向前翻页的最大消息 ID"},
				},
				"last-messages": {
				"description": "批量查看候选人最近消息摘要。",
					"--page": {"type": "int", "default": 1, "minimum": 1, "description": "沟通列表页码"},
					"--job-id": {"type": "string", "default": None, "description": "按职位筛选"},
					"--label-id": {"type": "int", "default": 0, "minimum": 0, "description": "按标签筛选（0=全部, 1=新招呼, 2=沟通中）"},
					"--friend-id": {"type": "int", "default": None, "minimum": 1, "description": "指定候选人会话 friend_id，可重复"},
					"--limit": {"type": "int", "default": 100, "minimum": 1, "maximum": 300, "description": "最多查询多少个会话（每 50 个一次串行请求，上限 300）"},
				},
				"reply": {
				"description": "回复候选人消息（friend_id 可从 chat/applications 获取）。",
					"args": [
						{"name": "friend_id", "required": True, "description": "候选人会话 friend_id"},
						{"name": "message", "required": True, "description": "回复内容"},
					],
				},
				"request-resume": {
				"description": "请求候选人分享附件简历。",
					"args": [
						{"name": "friend_id", "required": True, "description": "候选人会话 friend_id"},
					],
				},
				"candidates": {
				"description": "搜索候选人。",
					"args": [
						{"name": "query", "required": False, "description": "按关键词过滤候选人摘要；省略时列出全部"},
					],
					"--city": {"type": "string", "default": None, "description": "城市筛选（cityCode，如 101020100；-2=全国）"},
					"--job-id": {"type": "string", "default": None, "description": "按职位筛选"},
					"--experience": {"type": "string", "default": None, "description": "经验要求，如 -3,-3（应届）/ -1,-1（不限）"},
					"--degree": {"type": "string", "default": None, "description": "学历要求，如 201,201 / -1,-1"},
					"--age": {"type": "string", "default": None, "description": "年龄范围，如 20,25"},
					"--school-level": {"type": "string", "default": None, "description": "学校层次（如 1101）"},
					"--activeness": {"type": "string", "default": None, "description": "活跃度，如 2"},
					"--source": {"type": "string", "default": None, "description": "来源编码（默认 4）"},
					"--salary": {"type": "string", "default": None, "description": "薪资范围，如 -1,3"},
					"--select": {"type": "bool", "default": False, "description": "是否带 select=true"},
					"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				},
				"recommendations": {
				"description": "读取推荐牛人完整卡片和首次开聊参数。",
					"--job-id": {"type": "string", "required": True, "description": "招聘职位的加密 ID"},
					"--page": {"type": "int", "default": 1, "minimum": 1, "description": "页码"},
				},
				"greet": {
				"description": "建立候选人会话并发送首次招呼。",
					"--geek-id": {"type": "string", "required": True, "description": "recommendations 返回的 encryptGeekId"},
					"--job-id": {"type": "string", "required": True, "description": "recommendations 返回的 encryptJobId"},
					"--expect-id": {"type": "string", "required": True, "description": "recommendations 返回的 expectId"},
					"--lid": {"type": "string", "required": True, "description": "recommendations 返回的 lid"},
					"--security-id": {"type": "string", "required": True, "description": "recommendations 返回的 securityId"},
					"--suid": {"type": "string", "default": "", "description": "可选 suid；当前推荐接口通常为空"},
					"--message": {"type": "string", "required": True, "description": "首次招呼内容，不能为空"},
					"--yes": {
						"type": "bool",
						"default": False,
						"description": "操作者明确批准该候选人和话术后才可发送",
					},
					"--dry-run": {"type": "bool", "default": False, "description": "只预览，不发送"},
				},
				"accept-resume": {
				"description": "同意接收指定附件简历请求，不主动索要、不下载附件。",
					"args": [
						{"name": "friend_id", "required": True, "description": "候选人会话 friend_id"},
					],
					"--message-id": {"type": "int", "required": True, "minimum": 1, "description": "候选人发来的附件简历请求 mid"},
					"--yes": {"type": "bool", "default": False, "description": "操作者明确批准同意这条请求"},
					"--dry-run": {"type": "bool", "default": False, "description": "只预览，不请求平台"},
				},
				"download-resume": {
				"description": "检查权限后下载指定附件；不自动同意、不索要简历、不发送已读回执。",
					"args": [
						{"name": "friend_id", "required": True, "description": "候选人会话 friend_id"},
					],
					"--message-id": {
						"type": "int",
						"required": True,
						"minimum": 1,
						"description": "已收到的附件消息 mid，不是请求 mid",
					},
					"--output": {"type": "string", "required": True, "description": "本地输出文件路径，不覆盖已有文件"},
				},
				"jobs": {
				"description": "管理职位发布。",
					"list": {
					"description": "查看已发布的职位列表。",
						"--page": {
							"type": "int",
							"default": 1,
							"minimum": 1,
							"description": "职位管理列表页码，读取 data/list",
						},
					},
					"offline": {
					"description": "下线职位。",
						"args": [
							{"name": "job_id", "required": True, "description": "职位 ID"},
						],
					},
					"online": {
					"description": "上线职位。",
						"args": [
							{"name": "job_id", "required": True, "description": "职位 ID"},
						],
					},
					"detail": {
					"description": "查看职位详情（含完整 JD）。",
						"args": [
							{"name": "enc_job_id", "required": True, "description": "招聘职位的加密 ID"},
						],
					},
				},
				"whoami": {},
			},
			"subcommands": {
				"whoami": "查看当前登录态是否处于招聘者侧；identity 为 1 才算就绪，不切换身份",
				"applications": "查看候选人投递申请列表",
				"resume": "查看候选人在线简历或发起联系方式交换",
				"chat": "查看与候选人的沟通列表（含未读数和最近消息摘要）",
				"chatmsg": "查看与指定候选人的聊天消息历史",
				"last-messages": "批量查看候选人最近消息摘要",
				"jobs": "管理职位发布（list 读取职位管理 data/list，可用 --page；offline/online/detail）",
				"candidates": "搜索候选人",
				"reply": "回复候选人消息",
				"request-resume": "请求候选人分享附件简历",
				"accept-resume": "同意指定候选人的附件简历请求（需 --yes），不下载附件",
				"download-resume": "检查权限并下载已收到的附件简历，不自动同意请求",
				"recommendations": "读取推荐牛人完整卡片和首次开聊参数",
				"greet": "单次建立候选人会话并发送首次招呼（需 --yes），不修改已读状态",
			},
		},
	},
	"global_options": {
		"--data-dir": {
			"type": "string",
			"default": "~/.boss-agent",
			"description": "数据存储目录",
		},
		"--delay": {
			"type": "string",
			"default": "30-60",
			"description": "请求间隔范围（秒），如 30-60",
		},
		"--log-level": {
			"type": "string",
			"default": "error",
			"choices": ["error", "warning", "info", "debug"],
			"description": "日志级别",
		},
		"--cdp-url": {
			"type": "string",
			"default": None,
			"description": "Chrome CDP 调试地址（兼容保留）。不得用于规避平台风控或重试被平台拦截的操作。",
		},
		"--browser-source": {
			"type": "string",
			"default": "auto",
			"choices": list(BROWSER_SOURCES),
			"stability": "experimental",
			"description": "浏览器通道来源。auto 允许 CDP→headless 降级；existing-browser 只复用现有 CDP 浏览器中已打开的目标页，不读本地凭据、不导航、不启动浏览器，候选耗尽发 BROWSER_SESSION_NOT_FOUND；stored-cookie 为 fail-closed，只连 --cdp-url 指定的 CDP 端点，不自动探测、不降级、空浏览器不新建 context，不可用发 CDP_UNAVAILABLE；httpx-only 不打开任何浏览器，全部只读走 httpx，需要页面的能力发 BROWSER_DISABLED，且不得与浏览器通道共用同一 stoken（会得到 code 37）。不得用于规避平台风控。",
		},
		"--platform": {
			"type": "string",
			"default": "zhipin",
			"description": "招聘平台适配器（zhipin=BOSS 直聘求职者/招聘者均可用）",
			"choices": list_platforms(),
		},
		"--json": {
			"type": "bool",
			"default": False,
			"description": "强制 JSON 输出（即使在终端中，默认管道模式自动 JSON）。全局选项，必须写在子命令之前，例如 boss --json search <关键词>",
		},
		"--exit-codes": {
			"type": "string",
			"default": "envelope",
			"choices": ["envelope", "sysexits"],
			"description": "退出码风格。envelope（默认）保持 0=成功/1=失败；sysexits 按错误类别返回语义化退出码（77 认证；75 限流/预算/超时；78 参数/配置；69 风控/平台不支持；70 内部错误），stdout 信封不变",
		},
		"--role": {
			"type": "string",
			"default": "candidate",
			"description": "角色模式：candidate（求职者）/ recruiter（招聘者）",
			"choices": ["candidate", "recruiter"],
		},
	},
	"error_codes": ERROR_CODES,
	"conventions": {
		"stdout": "仅 JSON 结构化数据（信封格式）",
		"stderr": "日志和进度信息（通过 --log-level 控制）",
		"exit_code": {
			"0": "命令成功 (ok=true)",
			"1": "命令失败 (ok=false)",
			"sysexits": "可选 --exit-codes sysexits：0 成功；77 认证；75 限流/预算/超时；78 参数/配置；69 风控/平台不支持；70 内部错误；其余 1",
		},
		"hints": {
			"next_actions": "面向 AI Agent 的后继命令（boss xxx 形式），由 Agent 直接执行",
			"operator_actions": "面向真人操作者的自然语言指引，通常需要离开终端完成"
			"（扫码、在浏览器里调整条件、处理风控验证等）；Agent 应转述给操作者，TTY 下渲染到 stderr",
		},
		"command_vs_wizard": "单次、无状态的能力调用走顶层命令；需要跨步骤状态、可恢复、"
		"或中途需要把指引递给真人操作者的走 boss wizard（goal 取值见 wizard_catalog）",
	},
}
