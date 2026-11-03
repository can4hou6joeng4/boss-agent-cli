# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Fixed
- `boss wizard --input-json` 的输入错误只说自己错、不说该填什么，Agent 拿到的是死胡同：未知 `goal` 报「角色 candidate 不支持 goal 'bogus'」、`requested_steps` 里混进非法步骤报「requested_steps 不属于 goal 'job_search': bogus_step」，两条消息都不列合法取值，Agent 只能再调一次 `boss schema` 自己比对；同一组错误在非交互模式下的信封 `hints` 还是 `null`（`_wizard_error` 从不传 hints），而 `wizard` 正是「跨步骤、可恢复」的那类命令，提示缺失意味着恢复链在这里断掉。现在两条报错都追加「可选: …」（`goal` 按角色列全量、步骤列出该 goal 的合法步骤，与 `require_supported_platform` 的口径一致），非交互错误的 `hints.next_actions` 给出可直接执行的 `boss schema`。
- 天数值无界时，`boss stats --days` / `boss clean --days` / `boss chat --days` / `boss digest --days-stale` 能把 CLI 直接打崩或误报成网络错误：`time.time() - days * 86400` 与 `timedelta(days=…)` 都要把天数转成 float/有界整数，传 `10**400` 这类整数时前者抛 `OverflowError`、后者超范围，而 `boss stats` 没有 `handle_auth_errors` 装饰器、也不在 `main.py` 的 OSError 网兜里，stdout 只有一个 traceback，违反「每条命令只输出一个信封」的契约；有装饰器的命令则被 `except Exception` 兜成可重试的 `NETWORK_ERROR`，Agent 会对着必然复现的输入反复重试。现在四个选项都限制在 0–36500 天（100 年，超出返回 `INVALID_PARAM`），并把 `OverflowError` 归入 `INTERNAL_ERROR_TYPES`（确定性异常、不可重试），`main.py` 也补了同类兜底，未装饰的本地命令不会再漏出 traceback。
- `hints.next_actions` 里混着说明文字，Agent 照抄执行换回 `INVALID_PARAM`：契约说这个字段是「Agent 直接执行的后继命令」（`docs/commands.md`），但 `boss search` / `boss recommend` / `boss favorites list` / `boss history` / `boss detail` / `boss show` / `boss chat` / `boss chatmsg` / `boss mark` / `boss exchange` / `boss greet` / `boss status` / `boss login` / `boss logout` / `boss config` / `boss platforms` / `boss cities` / `boss clean` / `boss stats` / `boss digest` / `boss export` / `boss crawl` / `boss agent review` / `boss resume init` / 多个 `boss ai` 与 `boss hr` 命令给出的却是 `boss xxx — 说明`、「使用 boss xxx 查看…」、「A 或 B」、「boss export <query> -o file.csv — 导出到文件」这类不可执行文本，`search` 这类高频命令的提示甚至全部如此。现在 `next_actions` 只保留逐条可执行的命令（平台前缀统一由 `boss_command_for_ctx` 生成），说明性文字移到 `hints.operator_actions`；风控契约（`display.RISK_ERROR_CONTRACTS`）里的停止指令本就是禁止性指引，保持原样。新增 `tests/test_cli_invariants.py::test_next_actions_hold_executable_commands_only` 静态扫描命令源码，`next_actions` 字面量出现 ` — `、「使用 」前缀或「A 或 B」组合时直接失败。
- 三处提示文案把 `preset` / `watch` 的关键词写成了 `--query` 选项：`boss preset list` 空列表、`boss watch list` 空列表、`boss ai suggest-keywords` 的 `hints.next_actions` / 下一步提示都给出 `boss preset add <name> --query <关键词>` / `boss watch add <name> --query <关键词>`，而 `query` 是位置参数，照抄执行只会换回 `INVALID_PARAM`「No such option '--query'」。现在统一成 `boss preset add <name> <关键词>` / `boss watch add <name> <关键词>`，与 `boss search --preset` 报错时的提示、`docs/commands.md` 的写法一致。
- 只写命令组、没给子命令的调用（`boss resume` / `boss shortlist` / `boss hr` / `boss agent pending`）把整份英文帮助塞进信封的 `error.message`：Click 的 `NoArgsIsHelpError` 消息就是 usage 全文（「Usage: …」「Options: …」几百字符），`recovery_action` 只有「修正参数」，`hints` 为空，Agent 既读不到「缺子命令」这个原因，也拿不到可执行的下一步。现在改成一句中文说明（点名完整的命令路径与可选子命令），并在 `hints.next_actions` 里列出每条可直接执行的 `boss <命令> <子命令>`；`boss config` 这类 `invoke_without_command` 的命令组与根命令 `boss`（默认进入向导）不受影响。
- 子命令的用法错误在信封 `command` 字段里报成叶子子命令名而不是命令名：`boss resume show`（缺参数）报 `show`、`boss shortlist list --bogus` 报 `list`、`boss hr whoami --bogus` 报 `whoami`，而同一命令成功时的信封是 `resume` / `shortlist` / `hr`；这些叶子名不在 `boss schema` 的能力清单里，Agent 按 `command` 归档或路由时会看到一个不存在的命令（`resume show` 是 Agent 最常踩的缺参数路径）。Click 把用法错误记在出错那一层的 context 上，现在沿 context 链上溯到根 context 的直接子级，用法错误统一归到最外层子命令；全局选项层面的错误（如 `boss --bogus schema`）仍报 `boss`，与既有契约一致。
- 本地写入类命令接受空字符串参数并写入无法使用的记录：`boss shortlist add "" ""`、`boss resume link default "" ""`、`boss watch add w ""`、`boss preset add p ""`、`boss resume init --name ""`、`boss resume clone base ""` 都返回 `ok: true`，把空 `security_id` / `job_id` / `query` / 简历名原样写进本地状态——之后 `shortlist list` / `resume applications` 多出一行空数据，`resume list` 多出一份没有名字、`resume show ` 无法点名的简历，`watch list` / `preset list` 存下一条空关键词（等于不筛选）的条目，而提示里的 `boss shortlist remove ` / `boss apply  ` 都没有可执行参数。Agent 从搜索结果里取字段失败、或模板拼接出空值时最容易走到这里。现在这六个命令在写盘前校验（按 `strip()` 判空，纯空格同样拒绝），返回 `INVALID_PARAM`，本地不留记录。
- `boss preset add` 不校验薪资 / 经验 / 学历筛选：`--salary 天价` 这类未知取值被原样存进预设并返回 `ok: true`，之后每次 `boss search --preset <名>` 都在参数解析处失败（`INVALID_PARAM`「未知薪资范围」），而错误消息不提预设名、`recovery_action` 只说「修正参数」——用户这次没传任何筛选条件，看不出该删的是哪条本地记录（同组的 `watch add` 早已用 `resolve_search_code_params` 在保存时校验）。现在 `preset add` 与 `watch add` 用同一套校验（城市、薪资、经验、学历、行业、规模、阶段、职位类型），非法取值在写盘前返回 `INVALID_PARAM`、不落库；旧版本已存下的非法预设在 `search --preset` 报错时追加「来自 preset '<名>'，可用 boss preset remove <名> 删除后重建」。
- 预设 / watch 的参数是合法 JSON 但值类型漂移时，命令报成 `INTERNAL_ERROR` 或 `AUTH_REQUIRED` 而不是本地记录损坏：`saved_searches.params` 的已知键（`query` / `city` / … / `welfare` / `active`）应当是字符串或 null，写成数组或对象都能通过 `json.loads`，直到 `_parse_watch_filters` 调 `.split()` 才抛 `AttributeError`，被顶层兜成「不要重试」的内部错误——而唯一的修复方式是删掉这条本地记录重建。现在 `_decode_saved_params` 在读取边界校验这些键的类型，按已有的 `SAVED_SEARCH_INVALID` 返回，消息点名具体字段与收到的类型；`watch run --all` 也不再静默跳过坏条目：`data.skipped` 与 `hints.corrupt` 会列出本次未执行的 watch。同时 `boss wizard` 的 `candidate_watch` 不再把 `welfare` 按数组写进缓存（正是这条路径会写出 `watch run` 无法读取的记录）：写入时按 CLI `--welfare` 的逗号分隔写法归一，其它参数类型不对在写入时返回 `INVALID_PARAM`；早期版本留下的数组形式福利条件在读取时自动归一，旧记录仍可直接 `watch run`。
- `boss resume import <file>` 在文件里的 `name` 与已有简历同名时静默整份覆盖：`import_file` 解析后直接 `save()`，用户在当前简历上做的编辑全部丢掉，命令仍返回 `ok: true`、`action: "import"`，看不出发生了替换（`resume init` / `resume clone` 遇到同名都是 `RESUME_ALREADY_EXISTS`）。现在默认拒绝同名导入，返回 `RESUME_ALREADY_EXISTS` 并提示改 `name` 字段或加 `--force`；`--force` 显式覆盖时才写盘，`boss schema` 同步声明该选项。
- `boss resume delete` 只删简历文件、不清理该简历的职位关联：`resume_job_links` 以简历名为业务键且没有外键约束，删掉 `default` 后用同一个名字重建（`resume init` 的默认名就是 `default`），`boss resume applications default` 会把旧简历投过的职位连同 `applied` 状态和备注一起报成新简历的记录。现在删除时同步清理该简历的全部关联，信封里的 `data.removed_links` 给出清理条数；其它简历的关联不受影响。
- `boss resume link` 重复关联同一职位会静默清空投递状态和备注：`link_resume_to_job` 用 `INSERT OR REPLACE` 整行替换，主键 `(resume_name, security_id, job_id)` 命中时先删后插，已经推进到 `applied` 的 `status` 打回 `prepared`、`notes` 清空、`linked_at` 改成当前时间；第二次关联没带 `--title` / `--company` 时还会用空串覆盖原有的职位名和公司名。现在冲突时改为按字段更新：本次显式给出非空值的 `job_title` / `company` 才覆盖，`status` / `notes` / `linked_at` 始终保留，只刷新 `updated_at`。
- `boss agent run`（以及 `agent train --live`）在未登录时抛出 `AuthRequired` traceback，stdout 没有任何信封：`run_cmd` / `train_cmd` 没有挂 `handle_auth_errors`（同组的 `agent crawl` 挂了），自动化适配器扫描会话时抛出的登录异常一路冒到顶层，`exit 1` 却只有 stderr 的堆栈，MCP 子进程解析失败后统一回成 `CLI_ERROR`，Agent 完全看不到「先登录」这个真正原因。现在两者与其它命令走同一个装饰器，返回 `AUTH_REQUIRED` 信封（可恢复，`recovery_action` 为 `boss login`），`agent train` 默认的 dry-run 行为不变。
- `boss shortlist add` 重复加入同一职位会静默清空本地标签、备注和首次加入时间：`add_shortlist` 用 `INSERT OR REPLACE` 整行替换，主键 `(security_id, job_id)` 命中时先删后插，用户在 `shortlist annotate` 里打的标签、写的备注和 `created_at` 一起没了，命令仍返回 `action: "add"` 和一份看起来正常的记录——脚本重跑一次 `shortlist add`、或换设备后再导一次就会遇到。现在冲突时改为按字段更新：本次显式给出非空值的列才覆盖（`title` / `company` / `city` / `salary` / `source` / `tags` / `note` / `lid`），`created_at` 保留首次加入时间；响应改为回显存储后的整行，重复 add 时能看到保留下来的标注。
- `boss resume export <name> --format json` 在简历文件损坏（手写坏、字段类型写错）时报成 `LOCAL_IO_ERROR`「本地文件读写失败」：这条路径先用 `exists()` 判断、再让 `export_json` 里的 `get()` 返回 `None` 后抛 `FileNotFoundError`，被顶层 `OSError` 网兜接走，恢复指引让人去查磁盘与权限；同一份文件在 `resume show` / `resume export --format html|pdf` / `resume clone` 里都是 `RESUME_NOT_FOUND`。现在 json 导出同样先 `get()` 再分支，四种格式与其它子命令口径一致。
- 未登录时的调用会占用共享请求预算，把下一条命令卡住 30 秒并报错成限流：`_request` 在预算窗口内才读取凭据，所以一次根本不会发出请求、只会得到 `AUTH_REQUIRED` 的调用，也记了一个名额并把 `next_at` 推后一个 interval（`request_delay` 下限 30 秒）。例如 `boss pipeline`（未登录，`AUTH_REQUIRED`）之后 30 秒内再跑 `boss digest`，会先阻塞到 `request_wait_timeout` 超时，再以「请求节流等待超时，请求未发出」的 `REQUEST_BUDGET_EXHAUSTED` 失败——原因不对，还白等半分钟。现在在申请预算之前先取一次凭据（成功时命中缓存），未登录直接返回 `AUTH_REQUIRED`，不登记名额、不影响后续命令。
- `boss history` 与 `boss favorites list` 在 `hasMore` 为假时仍在 `hints.next_actions` 里建议 `--page N+1`：Agent 按提示执行只会拿到空页，再看到同样的提示，形成空转循环；同一命令的终端渲染（`hint_next`）早已按 `hasMore` 隐藏「more」，两条路径口径相反。`search` / `recommend` 一直是对的。现在两处都只在 `pagination.has_more` 为真时追加下一页提示，`history` 的 TTY 守卫也改用同一个变量。
- `boss ai` 系列命令在模型返回合法 JSON 但不是对象时抛 `AttributeError` traceback：`_call_ai` 只处理了 `JSONDecodeError`，对顶层是数组 / 字符串 / 数字的结果直接 `cast` 成 dict；`ai fit`、`ai keywords`、`ai cover-letter` 随后的 `result.setdefault(...)` 会崩掉，stdout 没有信封。现在非对象结果同样按 `AI_PARSE_ERROR` 返回（可恢复，提示重试），消息里点名收到的类型。
- `boss stats` 与 `stats --format html` 在缓存库损坏时抛 `sqlite3.DatabaseError` traceback：`_collect_stats` 直接 `sqlite3.connect` 打开库，`_safe_count` / `_count_since` 只吞缺表的 `OperationalError`，文件被别的格式占位时 `SELECT` 抛出的 `DatabaseError` 一路冒到顶层，stdout 没有信封（`--format html -o` 同样如此）。现在在读取边界把 `sqlite3.DatabaseError` 归一成 `CacheDatabaseInvalid`，由 `main.py` 统一返回 `CACHE_DATABASE_INVALID` 与备份重建指引；损坏库不会留下半份 HTML 报表。
- `boss clean` 绕过 `CacheStore` 直接操作 SQLite，遇到旧版本数据目录或损坏的库时抛 traceback 而不是信封：`clean --privacy` 在只有 `search_cache` 的旧库上会因 `crawl_runs` 不存在抛 `sqlite3.OperationalError`，`--all` 同理；库文件损坏时则更糟——登录态、简历、聊天快照已经先被删掉，命令才在 `sqlite3.execute` 处崩掉，用户既看不到错误码也不知道哪些文件已清理。现在打开库时把 `sqlite3.DatabaseError` 归一成 `CACHE_DATABASE_INVALID`（命令启动先做一次只读探测，删除动作之前就失败），缺表按「没有可清理项」处理，`clean` 在旧库上正常返回结果。
- `boss stats --format html` 的 `data.bytes` 报的是字符数而不是字节数：同一份中文报表实际落盘 12KB，信封里却写 4KB 左右，与 digest / export 的 UTF-8 字节数口径不一致，Agent 据此判断载荷大小时会低估 3 倍。现在 `-o` 与 `--json` 两条路径都按 `len(html_text.encode("utf-8"))` 返回，并与文件实际大小一致。
- `boss export <query> --format html` 在未指定 `-o` 时不输出 HTML，而是返回 `{"format": "html", "count": N, "jobs": [...]}` 的职位记录数组：`html_file_output = bool(output and fmt == "html")` 把 HTML 渲染限定在落盘路径，`--format html > jobs.html` 拿到的是 JSON，MCP `boss_export` 传 `format: "html"` 不传 `output_file` 时同样拿到非 HTML 载荷。现在 `--format html` 始终按 HTML 渲染：无 `-o` 且未强制 `--json` 时直出 HTML（便于重定向），显式 `--json` 时放进信封 `data.content`（附 `bytes` 与 `count`）；`-o` 行为不变。
- `boss ai local import --path <目录> --model ..` 会把模型复制到数据目录根并可能删掉同名目录：把模型名折成 `models/` 下单层目录的 `_safe_model_dir` 允许 `.`（`qwen3.5` 这类名字需要），于是 `..` 原样通过，目标变成 `models/..`（即 `~/.boss-agent` 根）；源目录同名时先 `shutil.rmtree(target)` 再复制，`auth` / `resumes` / `cache` 这类目录会被直接删除并换成模型文件。`.` / `---` / 空名同样会落回 `models/` 自身或数据目录根。现在只由点与连字符组成的模型名按 `INVALID_PARAM` 拒绝（不复制、不改登记表），正常带点的名字（`qwen3.5`）行为不变。
- `boss ai local pull --model <x> --confirm-download` 在没装 Ollama 的机器上返回 `LOCAL_IO_ERROR`（不可恢复，指引是「确认目标路径是可写目录、目录权限与磁盘空间正常」）：`subprocess.run(["ollama", ...])` 抛的是 `FileNotFoundError`，被顶层 `OSError` 网兜按本地文件读写失败处理，而这正是最需要「先装 Ollama」提示的路径。现在在命令内捕获 `OSError`，按已登记的 `LOCAL_MODEL_PULL_FAILED` 返回（可恢复，消息点名 ollama，指引确认已安装且在 `PATH` 中后重试）。
- `boss digest --format md` 与 `boss stats --format html` 在未指定 `-o` 时无条件把正文写到 stdout，连显式 `--json` 也不例外：stdout 不再是 JSON 信封，`exit 0` 却没有任何 `data`。人工路径（TTY 或 `> report.md` 重定向）需要这种行为，但 `--json` 是硬契约——MCP 子进程总是带 `--json` 调用 CLI，`boss_digest` 传 `format: "md"`、不传 `output` 时拿到的是裸 Markdown，`_parse_cli_output` 解析失败后统一回成 `CLI_ERROR` /「命令执行失败」，Agent 完全看不见日报正文；`boss --json stats --format html` 同样破坏「stdout 只有一个信封」。现在区分两者：仅当 stdout 被管道接走（未强制 `--json`）时保持直出，显式 `--json` 则把正文放进信封的 `data.content`（附 `format` 与 `bytes`）；`-o` 行为不变。
- `boss schema` 只校验并声明了数值选项的下界：`wizard --max-retries` 的上限 10、`crawl configure` / `crawl run` 的 `--port` 上限 65535、`hr chat --summary-limit` 与 `hr last-messages --limit` 的上限 300 在 Click 层都会被拒绝，schema 里却只有 `minimum`。Agent 照 schema 传 `--port 70000` / `--summary-limit 999` 只会换回 `INVALID_PARAM`。现在补齐 `maximum`，并把契约测试扩展为 `minimum` / `maximum` 双向一致。
- `-o` / `--output` 的输出路径语义在各命令间不一致：`boss chat` 会展开 `~` 并创建父目录，而 `boss digest` / `boss stats` / `boss resume export` / `boss export` / `hr download-resume` 直接把字面量路径交给写入函数，`wizard candidate_export` 虽然建父目录却不展开 `~`。MCP 通过子进程调用 CLI、参数不经 shell，`boss_digest` 的 `output` 传 `~/digest.md` 会在当前目录建出一个 `~` 目录；`-o reports/2026/jobs.csv` 这类父目录尚不存在的路径则直接失败。现在统一走 `services/output_path.prepare_output_path`：展开 `~`、按需创建父目录；创建失败仍按各命令原有的文件错误契约上报（CLI 导出为 `LOCAL_IO_ERROR`，`hr download-resume` 为 `INVALID_PARAM`）。
- `boss mark` / `chatmsg` / `exchange` 等服务通过 `services/contact_lookup.find_friend` 分页定位联系人，重复页守卫用整页条目的稳定标识做签名，而条目缺少 `uid` 与 `securityId` 时签名恒为空：第二页一进来就被判成「平台又返回了同一页」，遍历提前终止，后续页里的联系人被上层报成「联系人不存在」。现在签名全为空时不再据此早停，继续翻页，直到命中、`hasMore` 为假或达到页数上限（与 `services/friend_list_pages` 的聚合守卫一致）。
- `boss schema` 的能力清单漏了多个已实现的子命令：`watch list` / `preset list` 在 `options` 与 `subcommands` 里都不存在（`preset` 的描述却宣称有 add/list/remove），`shortlist` / `config` / `resume` 的 `list`、`agent` 的 `control` / `pending` / `stats`、`ai local` 的 `status` / `smoke`、`hr whoami` 则只在 `subcommands` 描述里出现或完全缺席。Agent 按 `boss schema` 枚举能力时会以为这些命令不存在。现在无参数子命令以空对象占位补齐，`watch` / `preset` 补上结构化的 `subcommands`，并新增两条契约测试：结构化 `subcommands` 的键集合必须与实际一级子命令一致，`options` 必须覆盖每个已注册子命令。
- `boss chat` 的跨天 diff 找「上一次快照」时只看 `.json` 后缀再按文件名倒序：同目录里手动备份出来的 `backup.json`（`"b…"` 排在 `"2026-…"` 之后）会被选中，读到非数组内容后整次对比静默退化成「首份快照」，`added` / `removed` / `new_unread` 全是空；未来日期的快照文件（从别处拷入的、系统时钟回拨留下的）同样会被当成「上一次」。现在只接受 `YYYY-MM-DD.json` 命名、日期合法且早于今天的文件，非快照 JSON 与未来日期一律跳过。
- `boss schema` 里有两处与可执行行为不符：全局 `--delay` 的默认值仍写着 `1.5-3.0`，而实际生效的是 `config.DEFAULT_REQUEST_DELAY`（30-60），`--delay` 的帮助与错误示例也停在低节奏取值；`boss digest --format` / `boss stats --format` 在 Click 层只接受 `json` / `md` 与 `json` / `html`，schema 却没声明 `enum`，Agent 照 schema 可能构造 `--format markdown` 并换回 `INVALID_PARAM`。现在默认值与示例统一为 `30-60`，两个 `--format` 补上 `enum`，并新增契约测试：Click 用 `Choice` 限制的选项必须在 schema 里声明同一组取值，全局 `--delay` 默认值必须由 `DEFAULT_REQUEST_DELAY` 推导。
- `boss login --timeout` 由 `default=120` 让 Click 推断成整数，没有下界：`--timeout 0` / `--timeout -5` 会直接进入登录流程，扫码等待的 `range(timeout)` 一次都不循环，立即以「扫码登录超时（0秒）」收尾（负数原样进文案），用户看到的是登录超时而不是参数错误。现在用 `IntRange(min=1)` 在解析阶段拒绝，返回 `INVALID_PARAM` 且不构造 `AuthManager`、不启动浏览器；`boss schema` 的 `minimum` 同步补齐。
- 多页聚合的重复页守卫会误伤缺字段的条目：`boss digest` / `pipeline` / `follow-up` 用 `securityId` 序列判断「平台又返回了同一页」，但 `securityId` 每次请求都会轮换（`services/contact_lookup.py` 早已据此改用 `uid`），签名永远命中不了，平台一旦不认 `page` 参数就会把同一页抓满 50 次，结果里全是重复联系人；而条目里没有 `securityId` 时签名恒为空元组，第二页会被当成重复页直接丢掉，`hasMore` 仍为 true 也不再翻页。`boss favorites list` / `sync` 的 `encryptJobId` 签名有同样的空签名误判。现在沟通列表按 `uid`（缺失时回退 `securityId`）生成签名，两处签名全为空时都不再据此早停，交给 `hasMore` / 页数上限收尾；新增 `tests/test_friend_list_pages.py` 与收藏页回归用例固定这些情况。
- `boss schema` 只声明了选项名和类型，没有声明取值下界：35 个已在 Click 层用 `IntRange` / `FloatRange` 约束的选项（`--page`、`--count`、`--days`、`--days-stale`、`--limit`、`--message-id`、`wizard --timeout`、`crawl` 的预算选项等）在 schema 里仍是任意整数，Agent 照 schema 会构造 `--page -1` 这类必然被 `INVALID_PARAM` 拒绝的 argv。现在这 35 处补上 `minimum`，并新增契约测试`test_schema_option_minimums_match_click_ranges`：双向比对 schema 的 `minimum` 与 Click 的 `IntRange.min` / `FloatRange.min`，缺下界、多下界、数值不一致都会失败。
- 招聘者命令的 `friend_id` 参数仍是裸 `int`：`boss hr chatmsg 0`、`hr reply 0 <消息>`、`hr request-resume 0`、`hr last-messages --friend-id 0`、`hr resume --exchange --friend-id 0` 会把 `friendId=0` 发给平台（同组的 `accept-resume` / `download-resume` 早已是 `IntRange(min=1)`）。现在统一为 `IntRange(min=1)`，非法值在解析阶段返回 `INVALID_PARAM` 且不发出请求。另外 `hr resume --uid` / `--gid` 自 #217 起就已被忽略，但 help 与 `boss schema` 仍写着「交换联系方式时需要」：Agent 照 schema 构造 argv 会先传这两个参数，再收到「需要 --friend-id」的参数错误白跑一次。现在两处都标明已弃用、请改用 `--friend-id`。
- `tests/test_system_proxy.py` 的模块文档字符串里有一处编码事故：「CI runner 环境干净」被写成「环境」+ 三个 U+FFFD +「净」，终端里只显示成方块，门禁一直全绿。已修正，并新增`tests/test_text_encoding.py` 作为全仓文本编码守卫：`src` / `tests` / `docs` / `scripts` / `mcp-server` / `evals` / `.github` 与根目录文档中任一文本文件含 U+FFFD 或不是合法 UTF-8 即失败，并点名文件与行号。
- 仍有一批「页码 / 标签 / 游标 / 数量」选项是裸 `type=int`：`boss me --deliver-page -3` 把负页码原样发给投递记录接口；`hr applications` / `hr chat` / `hr last-messages` 的 `--label-id` 与 `hr chatmsg --max-msg-id` 接受负数（`--max-msg-id 0` 还会在客户端被 `if max_msg_id:` 静默丢弃，翻页变成重取最新一页）；`ai interview-prep "<jd>" --count 0` 生成「0 道题」的 prompt；`ai config --max-tokens -1` / `--temperature -0.5` 会写进本机配置，之后每次 AI 调用都把非法参数发给服务商，失败信息来自网络层，看不出根源是配置。现在这些选项分别用 `IntRange(min=1)`（页码、消息游标、题量、令牌数）、`IntRange(min=0)`（标签 id，0 表示全部）与 `FloatRange(min=0)`（温度）在解析阶段拒绝，返回 `INVALID_PARAM` 且不发出请求、不落盘；`boss schema` 里这些选项及 `hr` 组其余数值选项的 `minimum` 同步补齐。
- 平台返回无法归类的失败时，信封里的 `error.code` 是 `UNKNOWN`，而它没有登记在错误码表里：`boss schema` 的 `error_codes` 查不到这个码，`recovery_action` 直接是 `null`，Agent 拿到一个「不可恢复但没有任何下一步」的错误（`boss search` / `recommend` / `detail` / `greet` / `apply` 等走 `handle_platform_error_output` 的命令都会这样）。现在 `UNKNOWN` 已登记（不可恢复；指引先在官方页面核对本次操作是否生效、用 `boss doctor` 检查登录态与网络，确认后再执行一次，仍失败再提交 issue），并新增守卫测试：平台层分类器与各平台 `_ERROR_CODE_MAP` 能产出的每个码都必须出现在 `boss schema` 的错误码表里；`docs/troubleshooting.md` / `.en.md` 的错误码表同步补上该行。
- `boss export` / `boss batch-greet` 的筛选参数没有校验：未知城市（`--city 火星`）被原样交给 `search_jobs`，在请求构造阶段抛 `ValueError` 后兜底成可重试的 `NETWORK_ERROR` +「重试」，拿同一个非法城市重试永远修不好；`batch-greet` 的 `--salary` / `--experience` / `--education` 未知取值被静默丢弃，等于不筛选就直接打招呼（写操作）；`boss export golang --count 0`（或负数）一行不导，却仍以 `ok: true`、`count: 0` 宣告成功。现在搜索类命令共用 `search_filters.ensure_supported_city` 校验城市（`watch add` 的文案随之一致），`batch-greet` 与 `watch add` 同时校验薪资 / 经验 / 学历标签（`watch add` 存下的就是每次 run 的筛选条件，先存下未知取值等于之后每次 run 都在不筛选的结果上报告新职位），非法值在发出任何请求之前返回 `INVALID_PARAM`；`export --count` 用 `IntRange(min=1)` 拒绝 0 与负数。
- 页码 / 条数 / 窗口天数类 CLI 选项没有取值域校验，非法值会原样进入请求或计算：`boss search golang --page -1` 把 `page=-1` 发给平台、`boss chatmsg <uid> --count 0` 请求 0 条消息、`boss chat --days -1` 把 cutoff 算到未来（永远返回空结果且 `ok: true`）、`boss stats --days -1` 与 `boss digest` / `pipeline` / `follow-up --days-stale -1` 把所有记录当成过期，`hr applications` / `candidates` / `chat` / `chatmsg` / `last-messages` 的 `--page` / `--count` 同样如此。现在这些选项统一用 `click.IntRange`（`--page` / `--count` 至少 1，`--days` / `--days-stale` 至少 0），非法值在解析阶段返回 `INVALID_PARAM` 信封，不会发出任何请求。
- `workflow_runs.params` 只校验了顶层是 JSON object，嵌套结构漂移同样会把 `--resume` 打成 traceback：`inputs` 被写成数组时 `_plan_from_run` 在 `dict(...)` 上抛 `TypeError: cannot convert dictionary update sequence element #0 to a sequence`，stdout 没有任何信封；`requested_steps` 被写成字符串 `"auth_status"` 时 `tuple(...)` 静默把步骤名拆成单个字符，恢复任务返回 `WORKFLOW_PLAN_MISMATCH`（「run_id 已绑定到不同的 workflow plan」），而不是说明记录已损坏。现在在缓存库读取边界统一校验这两个字段，结构漂移按 `CACHE_RECORD_INVALID` 返回（可恢复，指明是 `params.inputs` / `params.requested_steps` 无法解析，指引重新创建任务）；任务列表遇到这类记录同样跳过并在 stderr 点名。
- `boss wizard --input-json '{"inputs": {"max_pages": -3}}'` 会让 `run_search_pipeline` 的 `range(max_pages)` 一次都不循环：workflow 以 `completed` 收尾，结果却是空列表，看起来像「没搜到职位」；`page` 为 0 / 负数时页码被原样作为 `page` 参数发给平台；`count: 0` 又被 `if not raw` 当成缺省悄悄换成默认的 20 条。现在 wizard 的整数输入除类型校验外还校验下界（`page` / `max_pages` / `count` / `friend_id` 至少为 1，`label_id` / `days_stale` / `max_msg_id` 至少为 0），越界按 `INVALID_PARAM` 返回并点名字段与收到的值；显式写的 0 不再被当成缺省值（`count: 0` 报错而不是取 20），`bool` 与小数同样按类型错误拒绝（`int(True)` 会静默变成 1、`int(2.5)` 会截断成 2）。
- `boss wizard` 的失败信封仍是中英混排：缺输入时 `error.message` 是 `workflow input 'gid' is required`，Ctrl-C 中断时是 `workflow interrupted; verify any unfinished operation before starting another run`，标签 / watch 动作 / 城市写错时分别是 `unknown label: ...`、`unknown watch action: ...`、`unknown city: ...`，紧邻的 `recovery_action` 却都是中文。现在这些文案统一为中文（`workflow 缺少输入 gid`、`workflow 被中断；开始新的运行前请先核对未完成的操作`、`未知标签: ...（可选: …）` 等），并新增源码扫描测试覆盖 `wizard/` 下所有 `WorkflowActionError` / `WorkflowInputError` 的静态文案（`INTERNAL_ERROR` 保留异常原文用于排障）。
- `boss wizard --input-json '{"inputs": {"page": "abc"}}'` 把输入类型写错时，action 层的裸 `ValueError` 落到 runner 的通用兜底，被映射成 `NETWORK_ERROR` + 「重试当前 run_id」——同一条输入重试多少次都是同一个错误，Agent 会反复重跑。现在 `page` / `max_pages` / `count` / `label_id` / `days_stale` / `friend_id` 等整数输入统一经 `_int_input` / `_required_int` 转换，类型错误抛 `WorkflowInputError`，runner 按登记表映射为 `INVALID_PARAM`（消息点名具体字段与收到的值），不再被当成平台故障。
- `config.json` 里 `automation` 的数值字段没有范围校验，写坏后直接改变写操作行为：`auto_execute_threshold` 为负数时所有通过初筛的动作都自动执行（发问卷、交换联系方式），`human_review_threshold` 为负数时连低置信度决策也放行，`max_per_tab` 为负数会让 `items[:max_per_tab]` 反转成「去掉末尾 N 个」。现在两个置信度阈值必须落在 [0, 1] 且 `human_review_threshold <= auto_execute_threshold`，`max_per_tab` / `max_actions_per_run` / `max_consecutive_errors` 必须是非负整数（0 仍表示「不扫描 / 不执行 / 首次错误即熔断」）；`config set automation` 写入时即返回 `automation 配置无效: ...` 且不落盘，`agent run/train` 读到坏配置时给同一个修复入口。
- `boss agent run --limit -1` 会处理除最后 1 个以外的全部会话：`--limit` 透传到适配器的 `items[:max_per_tab]`，负数切片语义反转成「去掉末尾 N 个」，而这是会自动发问卷、交换联系方式的写操作路径；`--limit 0` 又被 `limit or config.max_per_tab` 静默放大成默认的 20 个。现在 `agent run` / `agent train` 的 `--limit` 用 `IntRange(min=1)` 拒绝 0 与负数（MCP 工具 schema 同步加 `minimum: 1`），`run_automation_cycle` 按 `is None` 判断默认值并把负数归一为 0，Python API 直接传 `limit=0` 也不会回退。
- `boss batch-greet --count 0` 仍会真实发出 1 条招呼：候选收集先 `append` 再判断 `len(candidates) >= count`，上限为 0 时第一个候选已经进列表，非 dry-run 下就是一次写操作；负数上限永远不成立，同样会走完整批。现在上限在下一次收集之前判断（`--count 0` 收集到 0 个候选），负数按参数错误返回 `INVALID_PARAM` 信封且不发任何请求。
- `boss clean --days -1` 会把 cutoff 算到未来（`time.time() - days * 86400`），目录里所有文件都被当成「超过 -1 天」，一次执行删光 `chat-history` 与 `chat-export` 下的全部快照和导出文件。现在 `--days` 用 `IntRange(0)` 拒绝负数，按参数错误返回信封且不动任何文件。
- `boss config set log_level verbose` 报成功，而 `Logger` 对未知级别静默回退到 `error` 阈值：配置里写着 `verbose`，实际什么日志都不显示。现在 `config set` 校验 `log_level`（与 `--log-level` 的 `Choice` 共用 `output.LOG_LEVELS`），非法值返回 `log_level 必须是以下值之一: error, warning, info, debug` 且不落盘。
- `boss config set role admin` 返回 `ok: true` 并把值写进 `config.json`，而启动期校验只接受 `candidate` / `recruiter`：之后每条命令都在启动阶段返回 `INVALID_PARAM`，连 `boss config reset role` 也一样，只能靠 `boss --role candidate config reset role` 脱困。现在 `config set` 与 `--role` 的 `Choice`、启动期校验共用 `config.ROLES`，写入非法值直接返回 `role 必须是以下值之一: candidate, recruiter` 且不落盘。
- 招聘者聊天页写操作的失败信封是中英混排：`boss hr chat` 发消息未确认时 `error.message` 以 `send_message_by_friend unconfirmed:` 开头，交换联系方式失败 / 未确认时是 `exchange_request_by_friend failed:` / `unconfirmed:`，紧跟着的却是中文恢复指引；页面返回结构异常时的默认文案同样是英文（`unexpected page result`、`unexpected result: ...`）。现在统一为中文（`发送消息未确认: ...`、`交换联系方式请求失败: ...`、`页面未返回可用的结果`），并新增源码扫描测试，防止新增的聊天页失败文案再混入英文。
- 顶层文件读写兜底把所有子命令的 `OSError` 都记成 `command: "boss"`：`boss stats --format html -o /不存在/x.html` 的信封是 `{"command": "boss", "error": {"message": "boss 本地文件读写失败: ..."}}`，按 `command` 过滤日志或分发的调用方认不出是哪个子命令。现在与启动期的缓存库兜底一样，从 argv 取实际子命令名（`command: "stats"`、`stats 本地文件读写失败: ...`）。
- `boss chat --export json -o <路径>` 只接受当前目录或数据目录下的文件：`-o ~/Desktop/chat.json`、`-o /tmp/chat.json` 这类绝对路径被 `os.path.relpath` 算出的 `..` 判成「输出路径不安全」并拒绝（`recoverable: false`），与 `digest` / `stats` 的 `-o` 行为不一致。现在显式给出的 `-o` 一律照写，同时补上 `~` 展开——旧代码对 `-o ~/chat.json` 会在当前目录建一个名叫 `~` 的目录。
- `config.json` 的 `export_dir` 写成数字 / 数组时，`boss chat --export json` 在 `os.path.expanduser` 上抛 `TypeError` traceback，stdout 没有任何信封。现在按参数错误返回中文信封（`export_dir 必须是目录路径字符串或 null，收到 9`），`recovery_action` 直接给出只修这一项的 `boss config reset export_dir`。
- `config.json` 里的 `role` 写成数字或无效值（如 `"admin"`）时没有校验：`boss doctor` 把它拼进诊断命令时 `shlex.quote` 抛 `TypeError: expected string or bytes-like object, got 'int'`，其它命令则静默按求职者处理，与 `--role` 只接受 `candidate` / `recruiter` 不一致。现在启动期统一按参数错误返回中文信封（`参数 --role 的值无效: 不支持的角色 5，可选: candidate, recruiter`），`--role` 覆盖后仍可执行 `boss config reset role` 修复；`--role` 的 `Choice` 与该校验共用同一取值域常量。
- `config.json` 里的 `cdp_url` 不是字符串（手工改成数字，或旧版本写坏结构）时，`boss doctor` 在拼接诊断命令时对值执行 `shlex.quote`，直接抛 `TypeError: expected string or bytes-like object, got 'int'`，stdout 没有任何信封。现在启动期就按参数错误返回中文信封（`参数 --cdp-url 的值无效: 必须是调试地址字符串或 null，收到 9`），`--cdp-url` 覆盖后仍可执行 `boss config reset cdp_url` 修复。
- `config.json` 里的 `browser_source` 不是字符串（手工改成数字、数组，或旧版本写坏）时，启动阶段直接对值调用 `.strip()`，所有命令都抛 `AttributeError: 'int' object has no attribute 'strip'` traceback，连用来修复它的 `boss config reset browser_source` 也跑不了。现在非字符串值按参数错误返回中文信封（`不支持的浏览器通道 5，可选: auto, stored-cookie, httpx-only, existing-browser`），`--browser-source auto` 覆盖后仍可执行修复命令。
- `boss platforms` 的过滤参数与 wizard 的平台校验仍有英文：`platforms --platform zhilian` 返回 `unknown platform 'zhilian', supported: zhipin`，`platforms --capability nope` 返回 `unknown capability ...`，`boss wizard` 对已移除平台的旧 run 返回 `... supported: zhipin`，与 `--platform` 全局选项已改好的中文文案不一致。现在统一为「不支持的平台 'zhilian'，可选: zhipin」「不支持的能力 ...」「角色 candidate 不支持平台 ...，可选: ...」。
- 启动期与 `config set` 的参数校验把英文原文漏进信封：`boss --delay 5-1 status` 的消息是 `Invalid value for --delay: delay must be a finite range like 1.5-3.0 with 0 <= low <= high`，`boss config set request_delay abc` / `request_max_per_minute abc` 会直接透出 `could not convert string to float: 'abc'`，既不中文也没点名配置项；`--platform` 写成已移除的平台时消息同样以 `unknown platform ...` 开头。现在这些自写文案统一为中文（`参数 --delay 的值无效: 必须是形如 1.5-3.0 的数值区间，且满足 0 <= 下限 <= 上限`、`request_max_per_minute 必须是整数，收到 'abc'`、`不支持的平台 'zhilian'，可选: zhipin`），Click 自带的英文校验文案保持原样、不会被拼上中文前缀。
- 本地缓存库文件损坏时所有命令都 traceback：`cache/boss_agent.db` 被写坏、被别的文件占位（如复制中断、磁盘异常）后，`CacheStore` 初始化抛 `sqlite3.DatabaseError: file is not a database`，而异常发生在命令自己的 `try` 之外，`boss preset list`、`boss watch list`、`boss shortlist`、`boss crawl`、`boss wizard` 等几乎全部本地状态命令都没有信封。现在启动期统一兜底为新登记的 `CACHE_DATABASE_INVALID`（可恢复，指明库文件路径，备份后删除即可自动重建，并说明本地数据会一起作废），`envelope.command` 取实际子命令名；并发锁（`database is locked`）等可重试错误仍按原错误上抛，不会被误报成库文件损坏。
- 本地 crawl / workflow 记录损坏时直接 traceback：`crawl_runs.params` / `hook_results`、`crawl_jobs.payload`、`workflow_runs.params` / `last_result` / `error`、`workflow_steps.result` 被写坏（手工改库、磁盘异常、旧版本中断）后，`boss crawl status` / `crawl results` / `crawl resume` / `crawl shortlist` 与 `boss wizard --status` / `--resume` / `--stop` 都抛 `JSONDecodeError`，stdout 没有任何信封；`boss wizard` 的任务列表遇到一条坏记录就整体不可用。现在这些读取统一走类型化异常并返回新登记的 `CACHE_RECORD_INVALID`（可恢复，指明重建任务的方式），合法 JSON 但容器类型漂移（如 `params` 不是 object、`hook_results` 不是数组）同样在读取边界拦下；任务列表跳过坏记录并在 stderr 点名，交给用户重建而不是静默消失。
- 本地保存的预设 / watch 参数（`saved_searches.params`）被写坏后，`boss preset list` / `boss watch list` 直接抛 `JSONDecodeError` traceback、stdout 没有任何信封；`boss search --preset` / `boss watch run` 被兜成 `NETWORK_ERROR` +「重试」，Agent 会针对一条本地数据问题无限重试，wizard 复用 watch 时也落进通用兜底。现在列表命令跳过坏条目并在 `hints.corrupt` / `operator_actions` 里点名；按名称使用坏条目时返回新登记的 `SAVED_SEARCH_INVALID`（可恢复，提示删除后重建），wizard 返回同一错误码，`watch run --all` 只跑参数可用的条目。
- 预算 / 时限停止的默认文案仍是英文，会原样进入信封 `error.message`：`RequestBudgetExceeded` 的 message 形如 `request pacing exhausted its wait timeout before dispatch`，紧邻的 `recovery_action` 却是登记表里的中文，Agent 与真人看到的是混排文案；`boss wizard` 的 `WORKFLOW_TIMEOUT`、`boss ai local import` 的来源缺失、招聘者自动化的熔断默认原因（`circuit breaker is open`）同样如此。现在这些默认文案统一为中文（如「请求节流等待超时，请求未发出」「workflow 超过 60 秒未完成」「模型路径不存在」「熔断已打开」），并新增源码扫描测试，保证 `RequestBudgetExceeded` 的新抛错点不会把英文文案漏进信封。
- `boss agent review approve/reject` 处理不存在的复核项时用 `raise click.ClickException`，被组层兜底成 `command: "boss"`、英文消息（`review item not found or not reviewable: ...`），与其它子命令的信封不一致。现在按实际命令名输出 `INVALID_PARAM` 与中文消息，并给出 `boss agent review list` 的后继指引。
- 搜索缓存条目损坏时 `boss search` 直接 traceback：`search_cache.response` 被写坏（手工改库、磁盘异常）时 `json.loads` 抛 `JSONDecodeError`，结构漂移（缺少 `data` 或 `data` 不是数组）时 `result["data"]` 抛 `KeyError`，同一条查询会一直失败且没有清理指引。现在坏条目按缓存未命中处理，重新搜索后用新结果覆盖同键条目。
- 招聘自动化状态文件损坏时没有信封：`automation/state.json` 被手工编辑写坏或在旧版本留下半截内容后，`boss agent run` / `agent stats` 直接抛 `JSONDecodeError` traceback；`action-log.jsonl` 等日志里有一行坏数据就会让 `stats` / `pending list` 整体失败。更严重的是 `automation/stop.json`（独立熔断开关）读不出来时被当作「没有熔断」，自动化会在状态未知的情况下继续向平台发送消息。现在状态文件损坏返回新登记的 `AUTOMATION_STATE_INVALID`（可恢复，指明文件与备份建议），单行损坏的 JSONL 跳过该行，熔断开关读不出来一律按已熔断处理并带上 `corrupt` 标记与原因。
- `boss ai` 各命令的 `@文件路径` 参数只检查文件是否存在：GBK 保存的 JD / 聊天记录、误选的 PDF 或图片会抛 `UnicodeDecodeError` traceback，传目录抛 `IsADirectoryError`，权限不足抛 `PermissionError`，stdout 没有任何信封。现在八条支持 `@文件` 的命令（`analyze-jd`、`optimize`、`suggest`、`reply`、`interview-prep`、`chat-coach`、`resume-optimize`、`cover-letter`）共用同一个读取入口：非 UTF-8 给 `INVALID_PARAM` 并提示转存为 UTF-8，目录给 `INVALID_PARAM`，读取失败给 `LOCAL_IO_ERROR`。
- 本地 JSON 状态仍是「直接覆盖写目标文件」：`config.json`、简历、模型登记表、索引缓存、聊天快照在写入中途被 Ctrl-C、进程被杀或磁盘写满时，会留下半截文件或空文件——`config.json` 坏掉会让之后每条命令都卡在启动阶段，简历/登记表/缓存坏掉则要人工修复。现在这些写入统一走 `services/json_files.write_json_atomic`：同目录临时文件写满并 `fsync` 后再 `os.replace` 原子替换，失败时原文件保持不变、临时文件被清理；`config set/reset` 写盘失败仍返回 `LOCAL_IO_ERROR`。
- 搜索/推荐结果缓存 `cache/index_cache.json` 结构损坏（顶层不是对象、`jobs` 不是数组、条目不是对象、非 UTF-8）时，`boss show <编号>` 把 `AttributeError` 兜成 `INTERNAL_ERROR`，恢复指引是「先升级到最新版本；仍出现时提交 issue」——真正该做的是重新搜索一次。现在这类缓存按「没有缓存」处理：`show` 返回 `INVALID_PARAM` 并提示先执行 `boss search` / `boss recommend`。
- 本地模型登记表 `models/registry.json` 损坏（`ai local import` 写文件时被中断、手工编辑写坏）后，`boss ai local status` 与 `ai local import` 都直接抛 `JSONDecodeError` traceback；写入方还会用空表覆盖原文件，把还能人工修复的登记条目丢掉。现在 `ai local status` 按空表降级显示，`ai local import` 在复制模型文件前先校验登记表，损坏时返回新登记的 `MODEL_REGISTRY_INVALID`（可恢复，指明文件与重建方式）且不改动原文件；登记表改为先写临时文件再 `os.replace`，中断不会留下半截 JSON。
- `boss resume import` 对上不合法的输入文件会直接抛 traceback、stdout 没有任何信封：GBK 保存的 JSON（或误选的 PDF / 图片）触发 `UnicodeDecodeError`，顶层是数组或字符串触发 `AttributeError`，`name` 写成数字时崩在 `_safe_filename` 的 `int.replace` 上；`boss resume list` / `resume show` 遇到非 UTF-8 或字段类型写错的简历文件同样 traceback。现在导入失败返回 `INVALID_PARAM` 信封并给出文件路径与具体原因（UTF-8 转码、顶层必须是 JSON 对象、缺少字符串 `name`、JSON 语法错误的行列），列表跳过读不出来的文件，单份读取出错按 `RESUME_NOT_FOUND` 处理。
- `config.json` 内容损坏（写一半被中断、手工编辑写坏语法）时报错只说 `Invalid value for config.json: Expecting value: line 1 column 14`：既没有文件路径，也没有说明「所有命令都卡在启动阶段，`config set/reset` 也用不了」。现在启动阶段的错误消息给出配置文件路径、出错行列与修复指引（修正或删除该文件，删除后配置回到默认值）；文件不是 JSON 对象时同样带上路径。
- 本地文件读写失败被当成可重试的网络错误：`boss chat --export json` 的 `export_dir` 指向同名文件时信封是 `NETWORK_ERROR` + `recoverable:true`，Agent 会无限重试同一条命令；`boss config set` 遇到只读数据目录、`resume export` 写文件失败则直接抛 traceback；`config.json` 本身读不出来（权限不足、路径被同名目录占用）时，异常发生在 CLI 启动阶段，所有命令都拿不到信封，连 `boss config reset` 也跑不了。现在新增错误码 `LOCAL_IO_ERROR`（`recoverable:false`，提示检查路径、权限与磁盘空间）：文件系统异常按它返回，`ConnectionError` / `TimeoutError` 等套接字错误仍按 `NETWORK_ERROR`；`config.json` 读取失败在启动阶段返回 `INVALID_PARAM` 并给出文件路径；没有统一装饰器覆盖的写文件路径由 CLI 顶层兜底成信封。
- 本地「已打招呼 / 已投递」去重以每次请求都会轮换的 `securityId` 为主键，跨请求必然失效：`batch-greet` 换个 `securityId` 就会把同一职位再打一次招呼，`search --skip-greeted` 与职位结果里的 `greeted` 标记也查不到上次的记录。现在去重键改为「账号指纹 + 稳定 `encryptJobId`」：账号指纹取登录 `wt2` Cookie 的 SHA-256 前 16 位（不落盘 Cookie 本身），`greet` / `apply` / `batch-greet` / `search` / `detail` / `show` / `recommend` 与求职者 wizard 共用同一作用域。同一台机器上换账号不会误命中另一个账号的记录；没有登录态时保留旧的 `securityId` 匹配行为。旧缓存库在打开时自动迁移，迁移在单个写事务里完成；旧记录没有账号归属，仍可按原 `securityId` 查到，但不会当作当前账号的去重依据，升级后同一职位最多重新记录一次。
- `search` 的 24 小时结果缓存键不含账号：同一台机器换账号后会直接看到上一个账号缓存的结果与 `greeted` 标记；缓存命中又不重算 `greeted`，打完招呼的职位在缓存过期前仍然显示未打招呼。现在缓存键带上账号指纹（搜索缓存 schema 升到 4，旧缓存自然失效），命中后按当前账号、稳定 `encryptJobId` 重新计算每个职位的 `greeted`，其余字段仍复用缓存、不额外请求平台。
- `watch` 的已见记录仍以 `securityId` + `jobId` 复合键保存，而 `securityId` 每轮搜索都会轮换：同一职位每次 `watch run` 都被当作新职位重复通知。现在登录态下按「账号指纹 + 稳定 `encryptJobId`」去重，换账号后各自的已见记录互不影响；没有登录态时保留旧的复合键行为。旧记录没有账号归属，升级后每个职位会重新通知一次。
- 单条 `boss greet` 在平台返回风控码（如 code 36）时仍输出 `recoverable: true` 和「重试」，与「命中风控立即停止、不无界重试」的硬边界相反，Agent 可能据此重试写操作。现在与 `apply` / `batch-greet` 一样走 `RISK_ERROR_CONTRACTS`：风控时 `recoverable: false`，`recovery_action` 与 `hints` 给出停止和人工处理指引；无法识别的业务失败仍按 `NETWORK_ERROR` 提示重试。
- `export` 搜索失败、`status --live` 用户信息失败与 `batch-greet` 搜索失败三条路径把 `recoverable` 硬编码为 `false`，也不给 `recovery_action`：登录过期（`AUTH_EXPIRED` / `AUTH_REQUIRED`）或限流（`RATE_LIMITED`）时 Agent 被告知不可恢复，风控码则缺少停止指引。现在统一走 `handle_platform_error_output`，按错误码登记表给出 `recoverable` / `recovery_action`，风控码的 `next_actions` 取自 `RISK_ERROR_CONTRACTS`；`status` 的 `auth_health` / `checks` 诊断字段随 `extra_hints` 保留，`detail` 的风控信封也补上同一份停止指引。
- `boss wizard` 的导出动作（`candidate_export`）没有把账号指纹传给搜索管线，登录态下 JSON 导出里的 `greeted` 全部是 `false`，与 `search` / `candidate_search` 的结果不一致。现在与其它求职者动作一样传入当前账号作用域。
- `search` 与 `watch run` 捕获 `SearchPipelinePlatformError` 后同样把 `recoverable` 硬编码为 `false` 且不给 `recovery_action`：响应字典形态的风控码（如 code 36）没有停止指引，登录过期也无法提示重新登录。`watch run --all` 还会把单个 watch 的风控收进汇总结果继续跑下一个 watch，向平台继续发请求。现在两条命令都按错误契约输出；`--all` 遇到风控立即停批，已完成的 watch 结果随 `error.details` 带回。
- 招聘者侧的 `hr download-resume` / `hr accept-resume` / `hr greet` 只捕获 `AccountRiskError`，而 `EnvironmentRiskError` / `EnvironmentRiskLockedError` 与其平级：环境风控会掉进兜底分支——`download-resume` 回 `NETWORK_ERROR` 并提示“检查网络后重试”，`accept-resume` 误报成“结果未知”，`hr greet` 也把风控当成未确认结果。现在三条写路径都按 `PlatformRiskError` 基类捕获，风控码的 `recoverable` / `recovery_action` / `next_actions` 统一取自 `RISK_ERROR_CONTRACTS`；响应字典形态的风控码与异常形态给同一份停止指引，非风控写失败仍保留“先核对、勿重试”的原有约束。
- `me` 没有 `@handle_auth_errors` 包裹，平台在获取个人信息 / 简历 / 求职期望 / 投递记录时抛出的风控异常会落到 `NETWORK_ERROR` 兜底：`recoverable: true` +「重试」。现在按 `PlatformRiskError` 单独输出，`recovery_action` / `next_actions` 取自 `RISK_ERROR_CONTRACTS`，不再提示重试。
- `boss doctor --live-probe` 的在线探测命中风控时，异常会落进通用兜底被记成「探测异常」并原样带出平台响应文本，随后还继续向另一个身份发招聘者接口请求；报告里的 `cdp_risk_lock` 仍是探测前的快照，`hints.next_actions` 还会建议再跑 `status --live` / `doctor`。现在 `PlatformRiskError` 与响应字典形态的风控码都在第一条命中处停止，检查项与 `hints` 按 `RISK_ERROR_CONTRACTS` 给出停止指引，不再透传异常文本；探测后重读本地风险锁，`data.live_probe_risk` 标记命中码，并抑制后续任何会触发平台请求的建议。
- `hr chat` 读取聊天页快照（`chat_list_snapshot`）时把包括风控与预算停止在内的所有异常都当成「增强失败」吞掉，随后继续发 `userLastMsg` 请求；`boss wizard` 的登录门在 `login` 命中风控后也只显示失败面板，然后回到「现在登录 / 仅看本地功能 / 退出」菜单，等于邀请用户重试已被风控拦截的登录。现在两处都按 `is_request_stop` / `PlatformRiskError` 立即停止：`hr chat` 直接输出风控信封且不再请求最近消息，登录门展示停止指引后退出向导。
- 共享错误装饰器与 `boss login` 的风控信封仍用 `str(exc)` 作为 `error.message`，而风控异常的消息来自平台响应原文（`raise_platform_risk_response` 直接取平台 `message`），可能带临时凭据。现在两处都改用 `risk_error_message()`：`error.message` 只含错误码登记表文案，停止指引仍由 `recovery_action` / `hints` 承载，与 `me`、`hr download-resume` 等命令一致。
- 风控信封的消息脱敏只覆盖了异常路径：`search`、`watch`、`detail` 与共用 `handle_platform_error_output` 的三十余条命令在平台以响应字典返回风控码时，仍把平台响应原文当作 `error.message`。现在由 `platform_error_message()` 统一决定文案：风控码只给错误码登记表内容，普通错误码继续保留平台消息或命令兜底文案。
- 预算 / 时限停止（`RequestBudgetExceeded`、请求控制停止、`WORKFLOW_STOPPED` / `WORKFLOW_TIMEOUT`）在四条命令里被当成普通失败：`detail` 的 httpx 快速通道失败后会改用浏览器通道再发一次请求；`batch-greet` 会 sleep 后重试并向下一批候选人继续打招呼；`hr download-resume` 回 `NETWORK_ERROR` 并提示“检查网络后重试”；`me` 同样落进 `NETWORK_ERROR` 兜底。停止信号本应终止当前 workflow、不产生新请求。现在四处都按 `is_request_stop` / `RequestBudgetExceeded` 立即停止：`batch-greet` 输出 `ok:false` 信封并带上已完成的 `details`，`detail` 不再降级通道，`hr download-resume` 与 `me` 按错误码登记表给出 `recoverable` / `recovery_action`。
- 风控信封的消息脱敏还漏了三处会进入 JSON 输出的路径：`boss wizard` 运行器把 `PlatformRiskError` 的 `str(exc)` 写进 `error.message`；招聘者自动化适配器把同类文本存进健康检查的 `warning`（进而成为 `hr agent run` 事件原因）；爬虫的运行结果 `error` 字段与 `CrawlRiskError.from_response()` 直接拼接平台响应 `message`。这些文本来自平台响应原文，可能带临时凭据。现在四处都改用 `risk_error_message()`，只保留错误码登记表文案与停止指引；爬虫本地产生的风险提示（如未登录）不受影响。
- 招聘者侧还有两条风控路径会把平台原文或成功信封带出去：`hr greet` 在平台以响应字典返回风控码时把 `platform_message` 原样写进 `error.details`；`hr chat` 的 `userLastMsg` 补充摘要在命中风控码时只记成 `partial_error` / `last_messages_error`，仍输出 `ok:true`，审计 `hr last-messages` 也继续返回部分结果。现在 `hr greet` 的风控错误不再保留平台原文，`userLastMsg` 的风控码与异常形态一样立即终止，输出风控信封并停止后续请求。
- `boss wizard` 的动作层 `_unwrap()` 在平台以响应字典返回风控码时，虽然已经按 `RISK_ERROR_CONTRACTS` 给出不可恢复与停止指引，但 `WorkflowActionError.message` 仍是 `parse_error()` 返回的平台原文，最终写进 workflow 的 `error.message`。现在风控码改用 `risk_error_message()`，只保留错误码登记表文案；非风控码（参数错误、鉴权失败等）仍保留平台说明。
- `hr accept-resume` 与 `hr greet` 把「请求在调度前就被预算 / 通道挡下」也报成结果未知：`RequestBudgetExceeded` 明确标注 `request_sent: false`，前者却回 `RESUME_ACCEPT_RESULT_UNKNOWN` 让人去官方页面核对一个不存在的操作，后者还保留 pending 预约，把这个候选人永久锁成「结果未知」——通道恢复、预算冷却结束后也无法重试。现在两条写路径都按停止契约输出 `REQUEST_BUDGET_EXHAUSTED` / `WORKFLOW_TIMEOUT`；`hr greet` 在预算停止与 `BrowserSourceUnavailable`（通道全部不可用）时撤销尚未发出的预约（只删 pending，已发送与结果未知的记录保持不动）。
- 求职者侧的 `greet` / `apply` 在平台已确认成功、随后本地记账（`record_greet` / `record_apply`）失败时，会把整个命令降级成 `NETWORK_ERROR`（`recoverable: true`、「重试」）：重跑会绕过 `is_greeted` / `is_applied` 检查，把同一次打招呼或投递再提交一遍。`batch-greet` 更直接——记账异常落在打招呼的重试分支里，1～2 秒后就会把同一个招呼再发一次。现在本地记账失败输出新错误码 `LOCAL_STATE_WRITE_FAILED`（不可恢复，说明操作已生效、禁止自动重发）；`batch-greet` 停止剩余候选人，已发出的条目带 `recorded: false`，不再重试或继续。
- 招聘者自动化的页面写动作返回 `ACTION_UNCONFIRMED`（动作可能已生效、发送未确认）时，适配器把它当成普通 `blocked`：本轮事件落成 `STOPPED_BY_SAFETY`，已登记的 `PLATFORM_VERIFICATION_REQUIRED` 状态从未产生；对应的待执行项又保留为 `pending`，下一轮 `agent run` 会对同一候选人自动重发招呼或换联系方式，`--resume` 之后也一样。现在未确认结果记为 `PLATFORM_VERIFICATION_REQUIRED` 并打开熔断，待执行项标记为 `verification-required`、不再回到待执行队列，`agent stats` 的近期错误也包含该状态。
- `boss wizard` 的 `candidate_apply` / `candidate_greet` 在平台已确认成功、随后本地记账（`record_apply` / `record_greet`）失败时，异常落进 runner 的通用兜底，workflow 报成 `NETWORK_ERROR`（`recoverable: true`，「重试当前 run_id」）：重试或 `--resume` 会绕过 `is_applied` / `is_greeted` 检查，把同一次投递或打招呼再提交一遍。现在与单次命令一致，输出 `LOCAL_STATE_WRITE_FAILED`（不可恢复，说明操作已生效、禁止自动重发），`error.details` 带 `applied` / `greeted` 与 `recorded: false`。
- `boss wizard --resume <run_id>` 会重跑失败步骤，包括结果未知的写动作：`candidate_apply` / `candidate_greet` 的本地记账失败，以及招聘者的 `recruiter_reply` / `recruiter_request_resume` / `recruiter_exchange_contact` 返回 `ACTION_UNCONFIRMED`，恢复时会再次执行同一动作。现在这类步骤错误带 `replay_safe: false`，`--resume` 保持原失败状态并返回原核对指引，不再重跑；该标记同时阻止 runner 的步骤内重试。
- `boss schema` 的错误码登记表漏了三个实际会出现在信封里的错误码：钩子阻止 `HOOK_BLOCKED`、模型下载确认 `CONFIRM_DOWNLOAD_REQUIRED` 与 Ollama 拉取失败 `LOCAL_MODEL_PULL_FAILED`；其中 `HOOK_BLOCKED` 的 `recovery_action` 还是 `null`。现在三码都已登记，钩子阻止时按登记表返回可执行的恢复指引。
- `boss ai local import` 的 `MODEL_SOURCE_NOT_FOUND`（模型源路径不存在）会出现在错误信封里，却未登记在 `boss schema` 的错误码表。同时 `tests/test_schema_contract.py` 的覆盖率断言依赖一份手工错误码清单，新增码不会自动被检查——上一轮的 `HOOK_BLOCKED` 等三码正是这样漏掉的。现在该码已登记，`ai.local.import` 的 `recoverable` / `recovery_action` 取自登记表；测试改为扫描源码中形如错误码的字符串字面量，任何未登记的新码都会让测试失败。
- 两条恢复指引指向了无效操作：`crawl start` 后台任务拉起失败（OSError）时要求「设置 operating_mode=research」，但该模式门槛已不再限制任何能力，照做不会改变结果；`resume export --format pdf` 失败时提示 `uv run playwright install chromium`，而 PDF 导出实际用 patchright 渲染。现在 crawl 的指引改为检查磁盘空间与数据目录权限后按 run_id 恢复（没有 run_id 时重新 `boss crawl start`），PDF 导出的 `recoverable` / `recovery_action` 取自错误码登记表，提示 `patchright install chromium`。
- `boss logout` 的异常路径把本地登录态清除失败（如磁盘满、权限不足）报成 `NETWORK_ERROR`，还标记 `recoverable: false` 且不给 `recovery_action`：Agent 既不会重试也不知道该检查什么。现在改用新登记的 `LOGOUT_FAILED`（可恢复，指引检查数据目录权限与磁盘空间后重试）。`boss export` 未提供 query / `--url` 时返回的 `INVALID_PARAM` 却被标成 `recoverable: true`，Agent 原样重试只会再次失败；现在与登记表一致标为不可恢复，保留修正参数的恢复指引。
- 命令层大量错误信封只传 `code` / `message`，`recoverable` 与 `recovery_action` 因此落成 `false` / `null`，与 `boss schema` 的错误码登记表矛盾：例如 `INVALID_PARAM` 已登记「修正参数」，Agent 却拿不到任何指引；联系人查找的网络错误被标成不可恢复；招聘者 `accept-resume` / `download-resume` 的平台不支持同样被标成不可恢复。现在 `envelope_error` 与 `handle_error_output` 在调用方未显式声明时从登记表补齐元数据，TTY 与 JSON 两条路径看到同一份恢复契约；显式传入的 `recoverable: false`（如写操作结果未知）仍优先，不会被登记表覆盖。
- `boss wizard` 的 TTY 菜单只要存在失败任务就显示「恢复已有任务 / 重试失败任务」，包括失败步骤标记 `replay_safe: false`、动作可能已生效的 run：选中后 `--resume` 只会返回原失败，用户得不到任何进展。现在这类 run 不再进入恢复 / 重试选择器（`boss wizard --resume <run_id>` 仍按原规则返回核对指引），只在「查看任务状态」里标注「结果未确认，需人工核对」。
- 错误码登记表的 `recovery_action` 文案会随命令演进失效（#540 修过两条指向无效操作的指引），但此前没有测试守住。现在 `tests/test_schema_contract.py` 把恢复指引引用的 `boss ...` 命令与反引号片段中的选项钉在 Click 命令树上：登记表写了不存在的命令或已改名的选项，测试直接失败。
- `boss wizard` 的风控恢复文案与错误码登记表矛盾：`ACCOUNT_RISK` / `ENVIRONMENT_RISK` / `ENVIRONMENT_RISK_LOCKED` 只说「检查账号状态」「等几分钟后再继续」，没有提到本地风险锁需要由用户执行 `boss clean --risk-lock` 才会解除——用户照做会在锁未解除时反复重试同一个必然失败的步骤。现在三条向导文案都给出显式解锁命令。登记表里 `CIRCUIT_BREAKER_OPEN` 的「人工确认平台状态后恢复」和 `WORKFLOW_TIMEOUT` 的「使用返回的 run_id 恢复 workflow」也没有给出入口，现在分别指向 `boss agent run --resume` 与 `boss wizard --resume <run_id>`。
- `boss schema` 中 `crawl` 的子命令声明漏了 `shortlist`：Agent 读 schema 时看不到「把 crawl 结果导入本地候选池」这条能力，只能从 `boss crawl --help` 或文档里发现。现在声明与实际注册的 8 个子命令一致，并新增测试把 description 里「子命令：a/b/c」的声明与 `hr.subcommands` 结构化字段都钉在 Click 命令树上，防止后续增删子命令时再次漂移。
- `boss crawl resume --background` 在后台上拉失败（`OSError`，通常来自磁盘满或数据目录权限）时只提示「执行 boss crawl resume 重试」，同一环境下重试大概率再次失败；`crawl start` 的同类失败已经改为先检查磁盘空间与目录权限。现在两条路径给出同一份恢复指引。
- `boss crawl resume --background` 对已完成的 run 仍会拉起后台进程并报告 `status: queued`：后台进程发现没有更多页和待补详情后立即退出，调用方却以为任务重新排队。现在与 `CrawlService.resume` 的前置条件一致——没有更多页、也没有 `--with-detail` 待补详情时直接返回 `status: completed` 并指向 `boss crawl results`，不拉起进程；请求更多页或仍有待补详情时照常恢复。
- `boss schema` 中 `agent` 把 `agent run` 的 `--resume` / `--dry-run` / `--limit` 写成命令级选项：Agent 按 schema 构造 `boss agent --resume` 会被 Click 直接拒绝。现在与 `crawl` 一样放进 `run` 分组。同时 `chat` 的输出路径键写成 `-o/--output`，与 `digest` / `stats` 的 `-o, --output` 不一致，转 JSON Schema 时会得到错误的参数名；现在统一。新增测试校验 schema 声明的每个选项都落在实际命令或子命令上，防止再次声明不可执行的用法。
- `boss schema` 的选项声明只覆盖了部分命令：`login --cookie-source`、`search --preset`、`export --include-private`、`clean --privacy`、`shortlist add` 的职位字段、`crawl configure/start` 与 `hr greet` 的开聊参数等公开能力没有进入能力真源，Agent 读 schema 时看不到；`hr jobs --page` 又平铺在 `jobs` 分组上，按 schema 构造 `boss hr jobs --page` 会被 Click 拒绝。现在 `boss schema` 递归声明各层子命令选项（`hr jobs list --page` 落到 `jobs.list`），并加测试双向校验：声明的选项必须可执行，可执行的公开选项必须声明（测试专用、历史兼容与内部隐藏项显式豁免）。
- `boss crawl status` 对 `risk_stopped` 的 run 只给出 `checkpoint.resume_command`，而风险锁会先于浏览器构造阻断 `resume`（`raise_if_locked`）：Agent 照 checkpoint 重试只会再次撞锁，用户也看不到解锁入口。现在 `checkpoint.resume_requires_operator_unlock` 标记该前置条件（不把解锁命令写进机器可执行的 `resume_command`），`hints.operator_actions` 给出 `boss clean --risk-lock` 等人工解锁指引与恢复后的显式 resume 命令；TTY 的下一步也不再展示无效的 `crawl stop`。
- `boss schema` 的位置参数只在部分顶层命令声明：`watch add/remove/run`、`preset add/remove`、`config get/set/reset`、`crawl run/start/resume/status/stop/results/shortlist`、`shortlist add/annotate/remove`、`resume show/edit/delete/export/import/clone/diff/link/applications`、`ai analyze-jd/polish/optimize/suggest/reply/interview-prep/chat-coach/resume-optimize/cover-letter`、`agent review approve/reject` 以及 `hr` 的简历、沟通、候选人、职位管理子命令都没有 `args` 声明，Agent 读 schema 时看不到必填的位置参数，照 schema 构造的 argv 会被 Click 拒绝。现在全部补齐（含 `hr jobs` 与 `agent review` 这种不带 `options` 的分组），并新增测试双向校验声明的参数名、顺序与 `required` 都必须与 Click 命令树一致。
- `boss ai cover-letter` 在推荐语生成后给出的后继命令是 `boss ai interview-prep --jd <jd_text> --resume <name>`，而 `interview-prep` 的 JD 是位置参数、没有 `--jd` 选项：Agent 照 `hints.next_actions` 执行会被 Click 以参数非法拒绝。现在改为 `boss ai interview-prep <jd_text> --resume <name>`，并在 `tests/test_ai_cover_letter.py` 钉住该后继命令可执行。
- MCP 的 `boss_mark` 工具把 CLI 的 `--label` 选项写成 `--tag`：任何一次真实调用都会被 Click 以 `NoSuchOption` 拒绝，工具自发布起不可用。原先的测试只断言 `args[0]` 是已注册命令、参数断言又照抄了错误选项，所以一直没发现。现在映射改为 `--label`，并新增测试用 Click 命令树实际解析全部 78 个工具生成的 argv（覆盖未知选项、缺失参数与非法取值），不再只看首个子命令名。
- 另外两处 MCP 工具 schema 与 CLI 不一致：`boss_batch_greet` 的 `limit` 被映射到不存在的 `--limit`（CLI 选项是 `--count`），调用方只要传 `limit` 就会被 Click 拒绝，且不传时会落到 CLI 默认的 10、比工具声明的 5 多一倍；`boss_me` 的 `section` 枚举把 `user` 写成 `info`，客户端按 schema 取第一个值同样被拒绝。现在 `limit` 落到 `--count` 并显式下发默认 5，`section` 枚举改为 `user`；`docs/commands.md` 与 `.en.md` 中 batch-greet 的 `--limit` 同步改为 `--count`，并新增测试逐个校验工具 schema 声明的枚举值都能被对应 CLI 接受。
- MCP 客户端漏传条件必填参数时（如 `boss_wizard` 的 `action=resume/status/stop` 少了 `run_id`、`boss_hr_jobs` 的 `online/offline` 少了 `job_id`），参数映射直接按下标取值抛 `KeyError`，SDK 把它变成 JSON-RPC 的 `Internal server error`：Agent 既看不到缺了哪个参数，也拿不到恢复指引。现在 `call_tool` 捕获该异常并按 CLI 错误信封返回工具错误（`INVALID_PARAM`、缺失参数名、`recoverable` / `recovery_action` 取自错误码登记表）。同时所有工具结果的 `is_error` 改为镜像信封的 `ok`：以前 CLI 返回 `ok:false`（登录过期、风控停止、命中确认门等）时 MCP 客户端看到的是一次成功的工具调用。
- MCP 工具会静默丢弃 inputSchema 之外的参数：`boss_apply` / `boss_greet` 没有 `dry_run` 属性，调用方多传这个参数时它被忽略，命令照常真实提交投递或打招呼——把「预览」变成写操作。现在 `call_tool` 按 `tools/list` 声明的属性校验参数，未知参数直接返回 `INVALID_PARAM` 工具错误并列出参数名，不再触达 CLI。
- 工具描述把可用性说明拼成「可用性: 可用性: roles=...」：`availability_note()` 返回值已带前缀，`build_tool_catalog()` 与 `_decorate_tool_descriptions()` 又各自加了一层。MCP `tools/list` 与 `boss schema --format openai-tools/anthropic-tools/mcp-tools` 导出给 Agent 的描述都带着重复前缀，按前缀解析可用性元数据会出错。现在两处都只拼接一次；兼容入口 `_decorate_tool_descriptions()` 改为幂等，重复调用不再叠加说明。
- `boss config set automation foo` / `boss config set crawl foo` 会把结构化配置项写成字符串并返回 `ok:true`，随后 `boss agent run` / `train` 在 `automation_config_from_dict()` 的 `.get()` 上抛 `AttributeError`，直接以 traceback 退出、stdout 没有任何信封。现在结构化配置项只接受 JSON object（如 `boss config set automation '{"mode": "training"}'`），非法值返回 `INVALID_PARAM` 且不写配置文件；手工编辑 `config.json` 造成该键不是对象时，`agent run` / `train` 按信封返回 `INVALID_PARAM` 并给出 `boss config reset automation` 修复入口。
- `boss config set crawl` 只校验 JSON 对象形状：`{"max_requests": 0}`、`{"cdp_port": 70000}`、`{"chrome_path": 5}` 都会返回 `ok:true` 并写进 `config.json`，之后每次 `crawl run` / `start` 或向导采集都报 `INVALID_PARAM`。现在 `config set crawl` 与采集路径共用 `crawl_int_setting` / `crawl_cdp_port` / `crawl_chrome_path` 校验，非法值在写入时即被拒绝且不落盘。
- `boss wizard` 的采集动作不校验 `hook_profile` 输入：向导没有 `hook-dir` 输入，`screenshot-full`（或任意拼写错误）会在 `create_and_run` 内部抛 `ValueError`，被 runner 兜底成可重试的 `NETWORK_ERROR`，恢复提示还让 Agent 重跑同一个 run。现在向导只接受 `none`，其它值返回 `INVALID_PARAM` 并指向带 `--hook-dir` 的 `boss crawl run`；`crawl_resume` 的 `pages` 输入同样按 ≥ 1 的整数校验，不再静默忽略 `0` 或把非数字落成 `NETWORK_ERROR`。
- `boss wizard` 的采集动作仍按 `int(cfg.get(...) or default)` 读取 `crawl` 配置：`boss crawl configure --max-retries 0` 在向导或 `boss wizard --input-json` 的 `crawl_start` 里被改回 `1`，与 `crawl run` / `start` 不一致；手工编辑写入错误类型或越界值时，`ValueError` / `TypeError` 落进 runner 兜底，workflow 报成可重试的 `NETWORK_ERROR`，重试永远修不好配置。现在向导与 CLI 共用同一组配置校验（`max_retries` 保留显式 `0`，其余值域同 `run` / `start`），非法配置返回 `INVALID_PARAM` 并指向 `boss config reset crawl`；`pages` 输入同样要求 ≥ 1 的整数。
- `boss crawl configure --max-retries 0` 会把 `0` 写进配置，但 `crawl run` / `start` 用 `int(cfg.get(...) or 1)` 读取，把 `0` 当成缺省值悄悄改回 `1`——用户显式要求的「不重试」从未生效；`max_requests` 等写入 `0` 时同样被默认值覆盖。手工或 `config set crawl` 写入错误类型 / 越界值时，`TypeError` 落在通用兜底，返回 `INTERNAL_ERROR` 并建议升级 CLI。现在整数配置按类型与范围校验：`max_retries` 保留显式 `0`，`max_requests` / `max_details` / `max_seconds` 要求 ≥ 1，`cdp_port` 限制在 1-65535，`chrome_path` 必须是字符串或 `null`；非法值返回 `INVALID_PARAM` 并指出配置项。
- `boss config set automation '{"mode": "nonsense"}'` 通过了对象形状校验照样落盘，随后 `boss agent run` / `train` 在 `AutomationMode(...)` 处抛 `ValueError` 并以 traceback 退出；非数值阈值、不可迭代的 `allowed_actions` 等也走同一条路径。现在 `config set` 用正式解析器 `automation_config_from_dict()` 校验 `automation` 内容，非法值返回 `INVALID_PARAM` 且不写文件；手工写入的非法内容由 `agent run` / `train` 按信封返回 `INVALID_PARAM`，同样给出 `boss config reset automation` 修复入口。

## [3.2.0] - 2026-11-01

**升级须知**

- `--industry` 改用平台行业码，14 个对不上平台的旧标签已移除：保存过这些标签的预设和 watch 会返回 `INVALID_PARAM`，报错会给出相近的可选值；也可以直接传数字行业码，或用 `--url`。
- `--experience`、`--salary` 的码已按网页筛选器校正，同样的标签现在搜到的是正确的档位，结果会和以前不同。预设和 watch 存的是标签，升级后自动使用新码。
- CLI 缺陷与平台数据结构异常改报 `INTERNAL_ERROR`（不可恢复），不再是可重试的 `NETWORK_ERROR`。按错误码决定是否重试的 Agent 需要相应调整。
- 依赖下限改为 `browser-cookie3>=0.20.0`，`pip install -U boss-agent-cli` 会一并升级它。

### Added
- `--city` 新增「全国」（平台的不限城市码 `100010000`）和嘉兴、徐州、香港，共 44 个城市。「全国」的码在 jackwener/boss-cli 与 ThisZhouS/jobwork 中一致，后者在真实搜索请求里用过；另外三个取自 jackwener/boss-cli，其城市表与本项目共有的 38 项完全一致。本地预筛与匹配评分把「全国」当作没有城市条件：此前就算平台支持，本地预筛也会因为职位城市名不含「全国」而把结果全部排除，评分里也会给每个职位记一条「城市不匹配」。
- `boss login --cookie-source <浏览器>` 没提取到时，stderr 会说明是读取失败（附异常类型与原因）还是该浏览器没有登录 Cookie。拿不到解密密钥时按平台说明原因（macOS 钥匙串未授权、Windows 上新版 Chrome / Edge 的 App-Bound 加密、Linux 密钥环未解锁），并提示改用 `--cdp` 或 `--curl-file`。自动探测全部落空时，只汇总无法解密的浏览器。参考 jackwener/boss-cli 的提取诊断及其 #9、#15。

### Changed
- 命令兜底不再把 `TypeError` / `AttributeError` / `KeyError` / `IndexError` 报成 `NETWORK_ERROR`（`recoverable: true`、建议「重试」）。这类错误来自 CLI 缺陷或平台返回了未预期的数据结构，同样的输入必然得到同样的结果；重试的若是平台请求，还会白白消耗请求预算。现在归为新错误码 `INTERNAL_ERROR`（不可恢复，`error.message` 带异常类型），`--exit-codes sysexits` 下退出码为 70（EX_SOFTWARE）。命令装饰器、wizard 运行器与 `me` 的兜底都已区分；其他异常仍为 `NETWORK_ERROR`，行为不变。
- Agent 集成文档（`docs/agent-quickstart.md`、Claude Code、Codex、Cursor、Windsurf、Shell Agent）默认改为只读流程：搜岗、看详情、整理本地候选池、导出与基于已确认事实写草稿。`boss apply` / `boss greet` / `boss batch-greet` 等平台写操作，只在用户当前对话点名具体岗位、并在操作前再次确认时执行一次；不建外发队列、不无人值守批量、不复用同一话术、不重试结果不明的写操作；遇到风控、验证页或限流时保存 checkpoint 并停止。`tests/test_agent_host_examples.py` 断言各宿主文档都写明这条边界。(#480)

### Fixed
- `--experience` 的经验码整体错位一档：`1年以内` 原发 `101`（平台含义为「经验不限」），`1-3年` 原发 `103`（实为「1 年以内」），依此类推。现按 zhipin.com 搜索页 `experience=` 参数改为 `1年以内=103` … `10年以上=107`，`应届=102`，并新增 `在校生=108`、`经验不限=101`。参考 jackwener/boss-cli#6。
- 已装 browser-cookie3 0.20.0 以前版本的环境里，`boss login` 与 `boss doctor --probe-cookies` 的 Cookie 提取直接抛 `AttributeError`：3.0.1 起加入的 Arc 加载器在 0.20.0 才出现，旧版缺失，登录因此走不到 CDP / 扫码降级。`pip install -U boss-agent-cli` 默认不升级已满足旧下限 `>=0.16.2` 的依赖，老用户升级后会命中这个问题。依赖下限改为 `>=0.20.0`；加载器改为按名字获取，缺失的浏览器在自动检测中跳过，显式指定时提示升级。
- macOS 上 `boss login` 的自动探测不再为没有登录记录的 Chromium 系浏览器逐个弹出钥匙串授权框。browser-cookie3 在找 Cookie 文件之前就读钥匙串，而且不设超时，装了几个 Chromium 浏览器就可能弹几次。现在先以只读方式查看 Cookie 库中明文的域名和名称两列，确认没有 `zhipin.com` 的 `wt2` 时跳过该浏览器，并在 stderr 说明跳过了哪些；找不到库或无法读取时保持原行为。参考 jackwener/boss-cli#35。
- `--salary` 的低三档错位一档：`3K以下` 原发 `401`（不是网页档位），`3-5K` 原发 `402`（实为「3K以下」），`5-10K` 原发 `403`（实为「3-5K」），`10-15K` 原发 `404`（实为「5-10K」，且网页没有 10-15K 档）。现按网页筛选器改为 `3K以下=402`、`3-5K=403`、`5-10K=404`，`10-20K=405`、`20-50K=406`、`50K以上=407` 不变；`10-15K` 保留给旧预设，按包含它的 10-20K 档请求，本地预筛仍按 10K 下限过滤。`--education` 补上 `初中及以下=209`、`中专/中技=208`、`高中=206`：这三档此前在本地阈值比较里存在，却不能作为筛选条件。多选时同码标签只发一次。依据为公开爬虫项目 ThisZhouS/jobwork 从筛选器 `ka` 属性取值并做过在线探测的码表，学历码与 jackwener/boss-cli 一致。
- `--industry` 的行业码表大面积错误：25 个标签里只有「互联网」对得上平台，例如「人工智能」发出的 `100901` 实为「机械设备/机电/重工」，「金融」发出的 `100101` 实为「广告/公关/会展」，整百的类目码则与标签整体错开一个大类。现改为平台行业码：职位数据里 `industry` 与 `brandIndustry` 成对出现的 96 条取值，`industry=` 搜索参数接受同一套码，取自 ThisZhouS/jobwork 从真实职位列表整理并在线探测过的结果。无法对应到这套码的 14 个旧标签（金融、软件/信息服务、区块链、证券/基金、教育培训、房地产、汽车、物流/运输、广告/传媒、消费品、制造业、能源/环保、政府/非营利、农业）已移除，用到它们的预设与 watch 会返回 `INVALID_PARAM`，而不是继续静默搜出别的行业；仍可直接传数字行业码或用 `--url`。`search` / `batch-greet` 在 schema 中的 `--industry` 可选值改为直接取自码表；`batch-greet` / `preset` / `watch` 的 help 不再展开整张表。
- 未知的筛选标签（行业、薪资、经验等）报错时附上字面最接近的可选值，例如 `未知行业类型: 汽车，相近的可选值: 汽车零部件、汽车经销商…`。
- `boss login` 在 CDP 不可用时不再先走「QR 纯 httpx」通道。该通道只在终端打印二维码 ID，从不展示可扫描的二维码，任何一次登录都不可能经由它完成，却会先向登录服务发出 randkey / getqrcode 请求；过期提示里提到的 `boss login --qr` 选项也并不存在。纯 httpx 扫码即使走通，也拿不到页面 JS 生成的 `__zp_stoken__`（参考 jackwener/boss-cli#2、#16）。现在 CDP 不可用时直接打开浏览器扫码；`auth/qr_login.py` 随之删除，YAML 中的 QR 端点保留为声明。
- 按文档配置 `boss ai config --provider openai --model gpt-5` 后，`boss ai` 的请求全部被拒（HTTP 400）：请求体固定带 `max_tokens` 和 `temperature: 0.7`，而 OpenAI 推理模型（o 系列、gpt-5 及之后）要求 `max_completion_tokens`，temperature 只接受默认值。现在按模型名切换：这些模型改发 `max_completion_tokens`、不发 `temperature`；Kimi K2.5 及之后的型号（temperature 由平台固定）也不再发送 `temperature`；经 OpenRouter 的 `vendor/model` 形式仍发 `max_tokens`。HTTP 错误的 `error.message` 带上服务端返回的原因（遮掉疑似密钥、截断到 200 字），`recovery_action` 按 401/403、400/404/422、429、5xx 给出对应的下一步，不再一律提示检查网络和密钥。(#499)
- 模型返回 `content: null` 时（例如 vLLM 开 `--reasoning-parser` 后思考阶段就用完输出上限），`boss ai` 各命令与 `boss ai local smoke` 抛出 traceback、stdout 没有 JSON 信封；招聘自动化的 `run_automation_cycle` 被 `AttributeError` 打断，排在后面的会话都没有处理。现在 `AIService.chat` 只返回字符串：正文为 null，或因 `finish_reason=length` 为空时抛 `AIServiceError`，命令输出 `AI_API_ERROR` 信封，自动化按「本地 AI 不可用」回退模板、在原因里写明，并继续处理后面的会话；返回 200 但响应体不是 JSON 时同样输出 `AI_API_ERROR`。`ai local smoke`、自动化回复与 wizard 的 AI 步骤改为使用 `boss ai config` 配置的输出上限（smoke 原先写死 32、自动化写死 512，wizard 还忽略了 temperature 配置），「调大 max_tokens」的建议才对它们生效。(#500)
- macOS 上只在非默认浏览器配置（如 Chrome 的 Profile 1）里登录 BOSS 时，Cookie 提取永远读不到：browser-cookie3 只读每个浏览器的第一个配置（通常是 Default）。#494 的预检已经确认其他配置有登录记录，loader 却仍读 Default，`--cookie-source` 还会误报「没有登录 Cookie」。现在把含登录记录的 Cookie 库直接交给 loader：按 browser-cookie3 的顺序（稳定版先于测试版，Default 先于 Profile N）取第一个，Default 有登录记录时行为不变；钥匙串仍只读一次，成功时 stderr 注明读的是哪个配置。参考 jackwener/boss-cli#12、#14。
- 平台响应里的字段为显式 null 时（如 `"jobList": null`、`"welfareList": null`、`"bossInfo": null`），多个命令会崩溃，并被兜底成提示「重试」的 `NETWORK_ERROR`：`search`（含 `--welfare`、`--with-score`）、`recommend`、`history`、`interviews` 在列表为 null 时报错；`detail` 遇到 `bossInfo` 或 `brandComInfo` 为 null 时，会吞掉快速通道的解析异常、改走浏览器通道再请求一次平台，最后误报 `JOB_NOT_FOUND`。现在平台数据在边界处统一归一化（新增 `api.models.field_text` / `field_list` / `field_dict`）：null 列表当作空结果，null 文本字段输出空串，输出信封里的职位字段保持字符串 / 列表类型。

## [3.1.0] - 2026-10-30

### Added
- 新增 `--browser-source httpx-only`：通道白名单为空，结构上不打开任何浏览器；`search` / `recommend` / `detail` / `friend_list` / `chat_history` 全部走 httpx，需要页面的能力返回 `BROWSER_DISABLED`。线上稳定性尚未证实，只作为显式选择，不做默认也不做自动降级；失败时 `hints.operator_actions` 提示回退 `auto`。
- 新增 `api/proxy.py` 的 `resolve_proxy()`：httpx 与每个 Chromium 启动点共用同一出口。此前 Chromium 完全忽略 `HTTPS_PROXY`/`ALL_PROXY`，登录走真实 IP 而请求走代理 IP。
- 新增 `auth/page_identity.py`：从真实页面请求采集 `token` / `zp_token` 并随会话加密落盘，httpx 与浏览器两条通道回放同一组值。
- 新增 `api/egress.py` 与 `BOSS_EGRESS_PROBE_URL`：出口地区与声明的语言/时区矛盾时 `CrawlService.create()` 拒绝开跑；未配置时不发任何探测流量，判为 `unknown` 且不阻断。
- `boss doctor` 新增 `request_delay` 检查：请求间隔下限低于 30 秒时 warn 并给出建议值。
- 新增三份研究文档：`docs/research/zhipin-request-identity-2026-10-29.md`（147 条业务请求的逐 header 规则）、`zhipin-cdp-free-read-path-2026-10-29.md`（只读链路脱离 CDP 的可行性、code 37 受控对照与恢复步骤）、`upstream-antidetection-evaluation-2026-10-29.md`（ruyijs / Safarier 的评估与不集成理由）。

- 搜索结果新增 `job_url`：由 `encryptJobId` 拼出 `https://www.zhipin.com/job_detail/<id>.html`，不额外请求平台。导出脱敏时与 `job_id` 一起替换为 `[REDACTED]`。
- 新增 `boss hr whoami` / MCP `boss_hr_whoami`：只读 `getUserInfo`，用 `identity` 判断当前登录态是否在招聘者侧。`identity` 为 1 时 `ready` 为 true；账号资料字段 `recruit` 只以 `profile_recruit` 回显，不参与判断。双身份账号停在求职者侧时返回 `ready: false` 和官方页面切换指引，命令本身不切换身份。
- 新增跨实例、CLI/MCP 子进程与 crawl 的本地共享请求预算：默认单个主动操作持锁至响应分类完成，滑动 60 秒最多 20 次，总等待上限 30 秒；`request_budget_dir` 可显式分组，组内采用统一限制并共享风险阻断。HTTP 429 / code 9 和有效 `Retry-After` 持久化冷却，进程退出只释放执行锁；预算超限返回 `REQUEST_BUDGET_EXHAUSTED`，保留 checkpoint 后显式恢复。主动操作计量不覆盖浏览器自主资源、轮询、自动重定向或认证内部的每条网络请求，不按公网 IP 自动合并任务。
- 招聘者 `agent run --resume` / MCP `boss_agent_run.resume` 提供显式恢复入口，保留确认结果与队列，普通运行不清除停止状态。
- 招聘者 `hr chat` 的 hints 新增 `total_unread`：聊天页显示的未读总数，读自页面 geek-list 组件的 `uncountTab$` 第 1 项（实测与页面 UI 一致，覆盖全部会话，不限于已加载的部分）；同时给出原始分项计数 `unread_by_tab` 和来源 `total_unread_source`（`page.uncountTab$[1]`）。只读页面内存，不额外发请求；页面不可用（非 CDP 或没打开聊天页）时三项均为 `null`（Refs #456）。
- `boss search` 新增 `--active <档位>`，按 HR 活跃度筛选职位（#442）。档位 `online` / `today` / `3d` / `week` / `2w` / `month` / `half-year`，也认 `3day`、`3日` 等写法；平台文案按「在线/刚刚活跃 > 今日活跃 > 3日内活跃 > 本周活跃 > 2周内活跃 > 本月活跃 > 近半年活跃 > 半年前活跃」比较。列表带 `activeTimeDesc` 或 `bossOnline` 为在线时直接判断，否则查 job_card；和 `--welfare` 同用时一次详情请求同时判断两项。启用后跳过搜索缓存，无法识别的文案被排除并写进 `hints.active_filter`。职位结果新增 `boss_active_desc` 字段（`boss_active` 不变，搜索缓存 schema 升到 3），`preset add`、MCP `boss_search` / `boss_preset_add` 与向导求职流程同步支持。
- 替换仓库 README 与项目站点的展示动画为 48 秒极光发布会成片，提供 MP4、轻量动图、新封面和可交互源工程；站点播放期间保持当前画面，切换到终端演示时暂停视频。
- 新增维护者发布验证脚本：默认离线核对版本、wheel/sdist 元数据与分发文件，显式启用后可在临时环境全新解析依赖、验证 CLI/MCP，或从 PyPI 安装指定版本复验；输出结构化步骤结果，不执行发布或真实平台请求。
- 新增仓库根 `llms.txt`：面向 LLM 的单页能力入口（能力真源、信封契约、运行边界与链接），指向 `boss schema` 与关键文档；不引入会与 schema 漂移的静态工具清单。
- `boss login` 的浏览器 Cookie 提取从 6 个扩展到 10 个：新增 Vivaldi / Arc / LibreWolf / Safari，自动检测优先级不变、新浏览器作为后备；macOS Safari 需要完全磁盘访问，读取失败时静默尝试下一个浏览器。
- 新增 `docs/research/opencli-benchmark-2026-10-15.md`（含英文对照）：对标 OpenCLI / boss-cli / opencli-mcp / xhs-cli 的差距矩阵、已确认缺陷（文档漂移）与分批改进方案；分层规则复核更正见方案 §3.3。
- 新增全局 `--exit-codes {envelope,sysexits}`（默认 envelope，0/1 契约不变）：sysexits 模式按错误类别返回语义化退出码（77 认证；75 限流/预算/超时；78 参数/配置；69 风控/平台不支持；其余 1），usage error 通过解析前预扫描同一选项；stdout 信封不受影响。

### Changed
- **行为变更：默认请求间隔 `--delay` 从 `1.5-3.0` 改为 `30-60`**（`config.DEFAULT_REQUEST_DELAY`，原先 10 处字面量收敛为一个常量）。受控对照：15 秒间隔连续 3 次 `search` 会在第 3 次命中 code 37「您的环境存在异常」，30 秒起两轮共 6/6 通过。单条只读命令只发一次请求、首次调用不等待（`boss status` 仍约 0.3 秒）；受影响的是分页、批量详情与 crawl。该下限来自本机观测，不是平台公布阈值。
- 请求身份对齐真实页面抓包：`User-Agent`、`sec-ch-ua*`、`Accept-Language`、`Accept-Encoding`、`priority`，补上页面自带的 `token`、`zp_token`、`traceid` 与 `X-Requested-With`；`sec-ch-ua-platform` 改由 User-Agent 推导（原先按宿主操作系统，Windows 上会发出「UA 说 macOS、Client Hint 说 Windows」的矛盾组合）。
- `search` 改为真机的 `POST` + `application/x-www-form-urlencoded` 表单体，字段集合与顺序按抓包固定，缺省筛选项发空串而非省略；`recommend` 保持 GET。
- httpx 通道改用 HTTP/2（依赖改为 `httpx[socks,http2]`，新增 `brotli`、`zstandard`），`Accept-Encoding` 的声明与可解码能力保持一致。
- Chromium 启动加入 `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` 与 `--enforce-webrtc-ip-permission-check`，避免 WebRTC 用真实地址发送 UDP。

- `boss doctor` 默认不再读取浏览器 Cookie，避免 macOS 钥匙串授权弹窗。需要核对浏览器登录态时使用 `boss doctor --probe-cookies`。`boss status` 与 `boss search` 本来就只看本地凭据。
- `hr jobs list` 改为读取职位管理页的 `data/list` 接口，并用 `--page` 分页。MCP `boss_hr_jobs` 的 `page` 只在 list 时传给该分页。`boss schema` 同步登记该选项。
- 招聘者 `chat_geek_info` 改为沟通页当前参数：`uid`、`securityId`，以及调用方提供时的 `geekSource`。没有命令调用这个方法。
- `boss doctor --live-probe` 复用 `hr whoami` 的身份判断：`identity` 为 0 时先记下求职者侧登录态，不继续请求招聘者接口；`identity` 为 1 时才做招聘者只读探测。命令不切换身份。
- MCP、OpenAI 和 Anthropic 工具格式统一使用不依赖 MCP SDK 的共享目录，工具导出由旧的不完整命令映射改为与运行时一致的 77 个工具；原生 schema 保留 39 个顶层命令。保留工具参数结构的脱敏例外仅限 schema 明确声明的内置参数路径，真实业务数据和敏感默认值/示例仍脱敏。
- 恢复面向 PR 的 `CI` 工作流（参照 Wholiver/metis 的 Test 工作流）：`pull_request` 与推送 `master` 时运行 ruff、mypy、pre-commit、CLI/schema 冒烟和 Python 3.10 / 3.12 / 3.14 测试矩阵；只读权限、同一 PR 新推送取消旧运行、不使用任何 secret，外部贡献者的 fork PR 可直接运行。pre-commit 的 end-of-file-fixer 排除 star-history 自动生成的 SVG，并补上 `wizard/renderer.py` 结尾换行，使 `pre-commit run --all-files` 在 master 上可以通过；贡献指南同步更新 CI 说明，去掉已失效的 Codecov 描述。
- 同步项目站点中英文正文与 `llms.txt` 的 v3.0.0 版本、命令与 MCP 数量、双角色工作流及平台风险停止／显式恢复说明，移除过时的模式级阻断宣传；修正发布会源工程的本地预览服务目录。
- 文档对齐：`CLAUDE.md` 与 `docs/maintainer/branch-protection.md` 同步 2026-10-09 已恢复的 PR CI（lint + Python 3.10 / 3.12 / 3.14 测试矩阵；required status checks 仍未配置）；`pyproject.toml` 注释改指本地 `scripts/verify_release.py --fresh-install`；中英文 README 与 `CLAUDE.md` 的测试计数更新为 2900+。

### Fixed
- 复用自动探测到的 CDP 浏览器前，先比对 `wt2` 与本地凭据：不同账号时拒绝复用其会话，改用本地凭据自建浏览器，避免拿 A 账号的登录态执行 B 账号的请求（实测会立刻得到 code 37）。
- `security_id` 可以合法以 `-` 开头（实测 328 字符的 base64url 取值），此前会被 Click 当成未知选项，`boss detail` 直接传时报 `No such option`；现在为未声明的横杠开头取值补 `--`。
- 候选者侧 referer 由 `/web/geek/job` 更正为真实页面 `/web/geek/jobs`（`web_pages` 与 5 处 referer 一并修正）。
- 抓包脚本判定目标页改用 host 解析，不再用子串匹配（CodeQL `py/incomplete-url-substring-sanitization`）。

- `boss login` 的中间提示不再宣称「检测到登录成功」：扫码确认到 `wt2` 出现只是**识别**，之后还要校验页面、等 3 秒、回首页提取，任一步命中官方验证页都会停下。现在统一为「已识别到扫码确认，正在校验并保存凭证…」，真正的「登录成功（…）」仍由命令层在凭据落盘后输出（#488）。
- `security_page` / 访问受限页的错误详情补上 `risk_source` 命中的页面路径（`error.details.path`）：此前只有 `risk_source`，用户无法区分 `verify.html`（安全校验）与 `security.html`（风险页），维护者也拿不到定位依据（#488）。
- `boss search` 等浏览器通道在冷启动时只对首页做一次 15 秒 `page.goto`，首页卡住就以 `NETWORK_ERROR` 原样抛出 `Page.goto Timeout 15000ms`（Discussion #250）。现在与登录预热对齐：超时后若不是安全验证页，就跳到 `about:blank` 再试一次；仍然失败则返回 `BROWSER_NAV_TIMEOUT`，说明要先打开官网首页或刷新登录态。`--url` 仍只解析筛选条件，搜索请求之前还是要先打开首页。把 `--json` 等全局选项写在子命令后面时，`INVALID_PARAM` 会提示该选项必须写在子命令之前。
- 修正节流完成时间覆盖后续预约、高斯采样突破间隔下限及 crawl 分类别预算边界；使用单调时钟保持预约，crawl 首页种子导航、分页、页面 fetch 和 CDP fallback 分别申请预算。等待及发送前检查停止、风险和截止时间，停止信号不触发重试或通道降级；写后确认受阻保留 `ACTION_UNCONFIRMED` 和 `confirmation_stop`，失败下载仍记节流，429 下载不读正文或创建文件。crawl 在中断或清理失败前保存进度，清理异常保留原风险或预算停止状态。
- 校验 `--delay`、延迟范围和共享预算配置的有限数值、正整数及绝对路径约束；非法配置不落盘，保留配置修复入口。预算组有效风险状态进入 `status` / `doctor`，显式清风险锁保留预算及冷却，风险写盘失败仍阻断当前实例。
- 浏览器风险识别续查：聊天页 `/wapi/` 的 HTTP 403 在响应头事件立即停止，不等正文；错误/缺失 Content-Type 的受限 HTML 仍生成脱敏分类元数据。业务 JSON 先解析原码，避免成功响应中的 HTML 说明触发误锁；纯文本业务响应同样检查风险，外站和普通静态资源不误锁。
- 补齐 code 31（IP/环境限制）、32（账号限制）及官方 `403.html` 访问受限页：API、浏览器、认证与 crawl 在刷新、重登清 Cookie 或继续导航前停止并保存原码/未知状态；隐藏文案不用于猜测当前原因。当前网页 code 31 已只读观察，32 仅有静态与离线证据；页面展示时间和移动端可用均不触发自动解锁。
- 风险锁改为显式本地恢复：code 35/36、环境类或含糊的 code 37、HTTP 403 和官方 verify/security 安全页在候选、招聘者与认证通道锁存原始风险原因及可用业务码；httpx、headless、裸 CDP、令牌变化和历史 `--ignore-risk-lock` 均不能绕过已记录锁。用户确认目标业务页恢复后执行 `boss clean --risk-lock` 再显式恢复任务，清理不代表平台解封，再次风险立即重锁。code 37 刷新要求凭据与明确过期语义同时存在，风险/安全验证优先；code 35/security 路径与含糊 token 分类为静态证据和离线回归，未声称真实账号线上解除已验证。
- 独立 crawl 与 API 共用同 `data_dir` 风险锁，在浏览器构造、连接和后台启动前阻断已锁任务；新风险持久化原始原因/业务码，未知码保持 `null`，code 38 明确属于身份资料流程。风险 outcome、已采集队列和 checkpoint 保留，人工恢复放在 `operator_actions`，resume 不清锁；本地结果与已完成 run 导入仍可用。
- 候选人搜索、推荐、批量沟通与 watch 在同次结果/分页流程中按非空职位 ID 去重，保留首条完整 `security_id/lid`，不按职位 ID 合并不同账号的历史记录。候选池可选 `lid` 经兼容 SQLite 扩展保存，CLI/MCP/schema、crawl/favorites 导入同步；favorites 更新访问上下文时不拼接旧 `lid`。
- `recommend` 保留缺失/不明确的分页标记为 `has_more=null`，只有平台明确返回布尔 `true` 才给出下一页提示，避免上层无依据继续请求；兼容 `total` 仍是本页条数，新增 `total_kind` 明确其范围。
- 风险响应在刷新、登录降级或采集 fallback 前停止：原始 code 36、环境类/不明确 code 37、HTTP 403 和安全页共享错误契约；Cookie、二维码各阶段、CDP 探测/刷新、招聘者动作前后回读与自动化读响应均原样传播风险，保存可用 checkpoint，不继续请求或更换通道。
- 端点显式声明读写与可重放性质，GET 变更和未知操作不再按方法推断可重试；fetch/响应体与 CDP renderer 加有界截止。浏览器上下文销毁、httpx 断链/损坏响应等未知写结果保留 `ACTION_UNCONFIRMED`，候选人与招聘者均不提示自动重发；招聘者准确绑定目标好友，不强制开启禁用编辑器，不以 outgoing WS 或未知手机号动作编号确认成功。
- 自动化使用独立原子停止标志并在动作边界重新读取，逐项保存已确认防重结果，风险和中断保留进度；wizard 成功结果先落盘再处理超时，旧更新不能覆盖 stop。MCP handler 改用异步子进程，长运行期间控制工具仍可响应，取消/超时有界中断并回收进程，保留副作用未知标记与 checkpoint 宽限。
- 搜索缓存命中重建本次排序的编号索引，`lid` 随编号、show/detail/job-card 传递，crawl 候选池仅从同 run/job/security 的既有本机缓存恢复 `lid`，兼容旧数据。福利/活跃度筛选先跳过已沟通过职位，有限需求按需串行并在结果足够时停止；apply 原始风险分支不再提示自动重试。
- 招聘者 `hr chat` 打开聊天页后页面来源仍是 0 条、`unread` 全为 `null`（#456 后续）。实测聊天页（Vue 2 + vue-rx）的会话列表挂在 geek-list 组件实例的 `list$` 和父组件 chat 的 `allList$` 订阅上（`$observables`），不在原快照扫描的 `$data` / computed / `$store` 里；列表项自带 `newMsgCount`、`lastText`、`lastTS`、`lastMsgStatus`、`lastIsSelf`。快照脚本现在先读 vue-rx 订阅（仍只读页面内存、字段白名单），页面已加载的会话直接给出未读数、最后一条消息和时间，没覆盖到的会话再走 `userLastMsg`；`hints.summary_sources.page_snapshot` 给出数据来源路径、已加载条数和页面是否还有更多（`has_more`）。页面只保存已滚动加载的会话，未加载的会话拿不到未读数，仍为 `null`。另外修正快照脚本从未真正执行的问题：`evaluate_js` 不带参数时只把函数表达式求值成函数对象而不调用，快照因此一直为空，现在带参数调用。
- 招聘者 `hr chat` 的最近消息补全实测全部失败（`last_messages_error: 未知的非法参数`，356 条都退回 `friend_list`，#456 后续）。对照前端代码和 3 个 friendId 的实测：`userLastMsg` 参数本身没错（表单 `src=0&friendIds=a,b,c`），问题是把整页 356 个 friendId 一次塞进去，前端自己每批不超过 100 个，并按 friendSource 分组（店长直聘好友 `src=1`）。现在按 friendSource 分组、每批 50 个串行请求，默认只补前 100 个会话（最多 2 次请求），`hr chat --summary-limit`（0–300，0=不请求）可调，任一批失败立即停止并在 `hints.summary_sources` 记录 `last_messages_error` / `last_messages_error_code` / `last_messages_requests` / `last_messages_skipped`；`hr last-messages` 同样分批并新增 `--limit`。按实测返回形状（`uid`、`lastTS`、`lastMsgInfo.showText/fromId/status/msgTime`）解析：时间优先用毫秒时间戳而不是「12:00」展示串，候选人发来的最后一条消息不再显示成「发送中」（`msg_status` 只对自己发出的消息有值）。该接口不返回未读数，`unread` 仍只来自聊天页快照。
- 风控锁（#453）补齐聊天页动作和登录覆盖。招聘者 `hr reply` / `hr request-resume` / `hr resume --exchange` 在执行前仅检查本地锁，不为核对令牌建立 CDP 连接；未解除时不执行页面动作。页面的 code 35/36/37 或安全页会锁存并保留原始原因，`error.details.page_risk` 保留接口路径和码。登录、导入和刷新也在平台请求前检查同一锁；历史 `--ignore-risk-lock` 参数保留兼容但不放行，用户确认官方目标业务页恢复后须执行 `boss clean --risk-lock` 显式清除本地阻断。
- 招聘者 `hr chat` 的未读数、最后一条消息、时间不再全是 `null` / `未知` / `-`。实测沟通列表接口（filterByLabel）只返回 friendId、姓名、updateTime 等基础字段，原实现的补全又没对上数据。现在 CDP 模式下先读聊天页 geek-list 组件里已加载的会话列表（只读页面内存、不发请求，字段按白名单挑选）；页面没覆盖到的会话才调用一次 `userLastMsg`（单次、串行），并兼容消息对象（按 from/to uid 对应候选人）和 `{friendId: 消息}` 映射等返回形状；都拿不到时字段为 `null`，`last_time` 退回 `updateTime` 并标注 `last_time_source`。每条记录新增 `summary_source`，`hints.summary_sources` 给出来源统计和最近消息接口错误。`hr last-messages` 同步改为未知字段输出 `null`。
- 招聘者 `hr resume --exchange` 在换电话/换微信按钮还没解锁时不再走一遍页面动作、最后报含糊的 `ACTION_UNCONFIRMED`。实测平台要求招聘者先在会话里发过消息才解锁这两个按钮（求简历不受限），未解锁时 `handleExChange` 照常返回但什么都没发。现在交换前先检查按钮组件的禁用状态（`disabled` / `aria-disabled` 属性、`disabled`/`gray` 等 class、根元素 `pointer-events: none`、组件 props/data 的禁用标记）；页面判断不了时，用动作前读到的完整聊天记录里是否有自己发出的消息兜底。确定未解锁时不调用 `handleExChange`，返回新错误码 `EXCHANGE_NOT_AVAILABLE`（`recoverable=true`，提示先 `boss hr reply` 或 `boss hr request-resume` 后再试），`error.details` 带 `availability`、`availability_signals`、`history_self_message`；信号拿不准时不拦截。按钮看起来可用但聊天记录里没有自己的消息时仍按原流程执行，若最终未确认，`ACTION_UNCONFIRMED` 的提示会点出按钮可能尚未解锁。
- 招聘者 `hr resume --exchange --type wechat` 实际已发出换微信请求却返回 `ACTION_UNCONFIRMED`（#443 后续）。实测这类请求在聊天记录里是不带文案的动作卡片（`body.type=4`、`action.aid=32`），原判定只认文案。现在聊天记录和 WS 帧都按动作卡片的 aid 判定：aid 32 = 换微信，aid 37 = 求附件简历；换手机号的 aid 尚无样本，暂时接受除这两个之外任意 aid 的卡片。卡片必须是自己发出、动作后新出现（mid 不在动作前快照里，且时间不早于动作开始 60 秒以上）。没有 WS 证据时聊天记录回读改为轮询（首读加 3 次重试，共约 6 秒），`error.details` 新增 `history_attempts`；换微信时出现的 aid 37 卡片也计入 `resume_request_detected`。
- CDP 模式不再用 httpx 带着浏览器登录态访问平台（#452 / #451 后续）。实测 `--cdp-url` 下 `job_card` 先走 httpx（Cookie 从 Chrome 拷出、`__zp_stoken__` 作查询参数），与浏览器通道并发，Chrome 里的 stoken 随即被判异常，之后 CLI 的浏览器请求一律 code 37，而手动浏览因页面重新校验换了 stoken 仍正常。现在配了 `--cdp-url`、显式 `--browser-source existing-browser` / `stored-cookie`，或本进程已接上 CDP Chrome 时，`BossClient` / `BossRecruiterClient` 的所有平台读写都只走浏览器通道（`job_card` 不再 httpx 优先，`detail`、向导详情、收藏、投递记录、聊天等读取一并改走浏览器），没有浏览器实现的操作（招聘者附件下载）返回 `NOT_SUPPORTED`；`search` / `watch` / 向导的福利、活跃度补详情在 CDP 下改为串行并按 `CrawlBudget` 间隔；`crawl` 的列表兜底和详情改为在采集页签内同源请求，去掉 httpx 回退。风险锁沿用历史 `cdp_risk_lock` 名称，摘要仅作诊断；锁存后所有 BOSS 请求在本地拒绝，账号风险保留 `ACCOUNT_RISK`，环境锁保留 `ENVIRONMENT_RISK_LOCKED`。`boss doctor` / `boss status` 展示本地状态，只有用户确认目标业务页恢复并执行 `boss clean --risk-lock` 后才能显式继续；令牌变化不会自动解锁，HTTP/headless 也不能绕过已有锁。`detail` 命中风控后不再换通道补请求。
- 收紧 `boss search --active` 的请求量（#451 回归，#442）。实测列表项不带 `activeTimeDesc`，原实现会固定翻 5 页并对离线 HR 的职位 3 线程并发取 job_card，`--active 3d` 跑约 28 秒后触发平台风控 code 37。现在 `--active online` 只看列表的 `bossOnline`，不查详情、只翻 1 页，请求量与普通搜索相同；其余档位先看 1 页，结果不足 10 条（或 `limit`）且本页仍有职位要查详情才继续，最多 3 页；在线行直接通过任何档位。需要查详情时改为串行，每次请求前按列表请求的节奏（5–10 秒）间隔，CDP / 指定浏览器来源时只走浏览器 job_card，不再 httpx 与浏览器混用；与 `--welfare` 同用时同样保守，单独 `--welfare` 行为不变。`bossOnline` 统一按 `True` / `1` / `"1"` 判为在线（`JobItem.boss_active` 与活跃度判断共用），`hints.active_filter` 新增 `detail_lookups` 提示实际查了多少个详情。`boss watch` 和向导求职流程的默认翻页同步调整。
- 招聘者 `hr request-resume` / `hr resume --exchange` 不再把已生效的动作误报为 `unexpected page result`，换微信也不再顺带发出求附件简历请求（#443）。确认按钮只在当前组件和本次动作新弹出的可见弹层里按文字「确定」查找，没有新弹层就不点；求简历同时认新旧两种文案（含「想要一份您的附件简历」），并在动作后只读回查聊天记录。页面动作执行但找不到发送证据时返回新错误码 `ACTION_UNCONFIRMED`（`recoverable=false`，先 `boss hr chatmsg <friend_id>` 核实、勿直接重试），页面日志、确认状态和 WS 统计放进 `error.details`；同时修正 CDP 文本帧被当作 base64 解码的问题。
- 定向更新安全依赖：`[mcp]` 的 PyJWT 锁定到 2.15.1、`[crawl]` 的 urllib3 锁定到 2.8.0、`[dev]` 的 virtualenv 锁定到 21.7.13，并仅连带更新 python-discovery 到 1.6.1。在对应 extra 中声明安全版本下界，避免全新安装回退到已知风险版本，不扩大基础运行依赖；补齐依赖作用域和 JWT 调用者选项隔离/过期声明校验回归。
- `boss config set platform` 在写入前按当前注册表拒绝已移除、未知或空平台值；失败不创建配置文件，也不改写已有内容，保留显式 `--platform zhipin` 修复旧配置的入口。
- `boss doctor` 将可执行后继命令与真人操作指引分别放入 `next_actions` / `operator_actions`，命令保留数据目录、平台、浏览器来源和 CDP 上下文；TTY 显示真人指引。源码质量工具检查和维护脚本建议仅面向本项目源码运行，安装包用户不再被要求拥有开发工具链。

## [3.0.1] - 2026-10-23

### Fixed
- 招聘者在线简历接口 `/wapi/zpjob/view/geek/info` 返回「旧简历接口已下线」时，`hr resume` 与 wizard 改为 `ENDPOINT_DEPRECATED`（不可恢复，停止重试），并在 `error.details.tracking_issue` 指向 #472。聊天发送旧端点 #217 仍走同一错误码，跟踪链接改到各自的 `details`，不再写死在 `recovery_action`。`recovery_action` 文案改为查看 `error.details.tracking_issue`，错误码本身不变。
- CDP 回读在线简历时，已解开的 `resumeInfo` 在弹层内部的 `new-chat-resume` 上，外层 `transition` 的 `$children` 为空。读取改为同时扫描弹层里的 Vue 实例；`geekBaseInfo` 只保留解析器会用到的字段。教育经历同时认 `degreeName` / `startDateDesc` / `endDateDesc`。
- `hr resume` 的在线简历请求改为 `uid`、`encryptJid`、`securityId`，并带 `X-Requested-With: XMLHttpRequest`。不再发送插件时期的参数名，也不生成 `sigx`。登录态沟通页的完整正文由页面自己请求 `GET /wapi/zpjob/view/geek/info/v2`（含 wasm 的 `challenge`）；CLI 不复刻该请求。CDP 下若旧接口仍返回下线文案，且已打开 `/web/chat/index`，再调用页面自己的 `newChatResumeViewer`，只读取 securityId 匹配的白名单字段并关闭弹层。没有聊天页或没有可读正文时，仍返回 `ENDPOINT_DEPRECATED`。

## [3.0.0] - 2026-10-02

### Changed（含 Breaking Change）
- **移除 Browser Bridge。** 删除本地 daemon、Chrome 扩展、Python `boss_agent_cli.bridge` 包与 `[bridge]` extra，移除 `boss doctor` 的七项 `bridge_*` 诊断；不再探测或连接旧 Bridge 服务。浏览器来源 `auto` 的通道顺序改为 CDP → headless，`existing-browser` 仅复用已有 CDP 浏览器中的目标页；`stored-cookie` 仍只连接显式指定的 CDP 端点，来源约束和风控停止契约不变。
  - `chat` / `chatmsg` 默认继续使用本地凭据的 httpx 通道；需要不读取本地凭据的现有浏览器会话时，请显式使用 `boss --browser-source existing-browser --cdp-url http://localhost:9222 chat`，并在该浏览器中手动打开和登录 BOSS 直聘。
  - **升级 CLI 不会停止旧 daemon，也不会卸载浏览器扩展。** 请手动停止旧 daemon（包括其他虚拟环境中的实例），在 `chrome://extensions` 禁用或移除旧扩展，并从安装配置中删除 `[bridge]` extra。CDP 调试端口仅限本机可信环境使用，不得用于规避平台风控。
- **移除智联招聘（`zhilian`）平台。** 求职者侧适配器（`ZhilianPlatform` / `ZhilianClient` / `zhilian.yaml`）、招聘者侧 `agent` 浏览器 / CDP 自动化（`automation/zhilian_*`）、`ZhilianPlatform` 包级公开导出、schema 与 MCP 描述中的平台可用性、能力矩阵、向导标签、研究文档和演示内容一并删除；BOSS 直聘（`zhipin`）成为唯一注册平台，多平台抽象（`Platform` / `RecruiterPlatform` 注册表与 `--platform` 选项）保留。`boss --platform zhilian ...`（含 `--role recruiter agent run`、`status --live`、`hr ...`、`platforms --platform zhilian`）以及用 `boss-mcp --platform zhilian` 启动的 MCP 宿主，现在都返回七键 `INVALID_PARAM` 信封（`unknown platform 'zhilian', supported: zhipin`）。
  - 迁移：若 `config.json` 里写着 `platform: zhilian`，所有命令都会被拒绝，运行 `boss --platform zhipin config set platform zhipin` 改回默认平台。
  - `boss doctor` 不再额外探测 zhaopin.com（`network_zhilian` 检查项移除）；招聘者 `status --live` 的智联 selector 健康检查及其 `SELECTOR_HEALTH_FAILED` 错误、`selector_health` 字段随之移除。
  - 升级前以 `zhilian` 保存的 wizard run（含 MCP `boss_wizard` 恢复）在恢复时同样返回 `INVALID_PARAM`，不再读取遗留登录态；请以 `zhipin` 新建 run。
  - 便携包不再附带 `examples/zhilian-recruiter.sh`。
  - 本地遗留的 `~/.boss-agent/auth/zhilian/` 登录态与自动化存储中的智联条目不再被读取，也不会被 zhipin 运行执行，可手动删除。

### Changed
- 内部重构：`boss schema` 的能力描述、错误码登记表与格式转换从命令模块 `commands/schema.py` 拆到独立的 `boss_agent_cli.schema` 包（`data` / `error_codes` / `availability` / `formats`），`display` 与 `mcp_tools` 不再反向依赖命令层；CLI 输出、`boss schema` 四种格式与 MCP 工具面均逐字节不变。
- 内部重构：`commands/` 只保留 Click 命令与 `_` 前缀的 CLI 支撑模块；聊天导出 / 快照、联系人查找、职位导出、job_card 映射、浏览器内核探测等可复用逻辑移入 `boss_agent_cli.services`，平台实例构造移入 `platforms/factory.py`，wizard 不再调用命令模块的私有函数；行为不变。
- 内部重构：CDP 默认地址与 zhipin / zhilian 域名校验移到 `api/browser_urls.py`，`api/browser_client.py` 不再 import `auth.browser` 的私有符号，打断 api ↔ auth 运行时 import 循环；行为不变。
- 清理无运行时调用方的代码：`automation/zhilian_browser_actions.py`（与智联自动化平台 `execute_action` 重复的动作分派，#379 后已无调用方）、`automation.events.stable_action_id`、Bridge daemon 的 `start_daemon_background` / `stop_daemon` 与未使用的 `BridgeNotRunning` / `BridgeExtensionDisconnected`、`ai.local_models.parse_model_manifest`，以及 wizard renderer 中三个未被调用的渲染函数。原先经 `execute_browser_action` 覆盖智联浏览器 session 的用例改走运行时真实路径 `ZhilianRecruiterAutomationPlatform.execute_action`；`compliance` 中的历史兼容函数按设计保留。

### Fixed
- `boss chat` 现在公开跨请求稳定的联系人 `uid`，`chatmsg` / `chat-summary` / `mark` / `exchange` 优先按 `uid` 重新解析联系人并使用本次沟通列表返回的动态 `securityId`；沟通快照、diff 与导出映射同步改用 `uid` 主键，避免轮换令牌导致联系人查找必然失败和快照重复。既有 `security_id` 参数名与无 `uid` 的旧快照仍兼容，但旧令牌仅作兜底且可能已经失效。
- 移除通用 CI / Docs workflow 后，同步清理失效的 README 状态徽章、远端门禁文档与工作流存在性断言；PR 和发版改为明确执行维护者本地质量门禁，避免主线测试固定读取已删除文件。

## [2.0.1] - 2026-09-29

### Fixed
- `boss chat` 现在公开跨请求稳定的联系人 `uid`，`chatmsg` / `chat-summary` / `mark` / `exchange` 优先按 `uid` 重新解析联系人并使用本次沟通列表返回的动态 `securityId`；沟通快照、diff 与导出映射同步改用 `uid` 主键，避免轮换令牌导致联系人查找必然失败和快照重复。既有 `security_id` 参数名与无 `uid` 的旧快照仍兼容，但旧令牌仅作兜底且可能已经失效。

## [2.0.0] - 2026-09-15

### Changed（含 Breaking Change）
- **移除未完成的前程无忧 / 51job（`qiancheng`）占位平台。** 候选者平台注册表、`51job` 别名、`QianchengPlatform` 包级公开导出、schema 可发现性、能力矩阵、向导标签、研究文档和演示内容一并删除；`boss platforms` 同时删除闲置的 `placeholder_only` 图例和 `placeholder` 空分组。`boss --platform qiancheng ...` 与 `boss --platform 51job ...` 现在返回七键 `INVALID_PARAM` 信封，不再返回占位 `NOT_SUPPORTED`。**迁移**：将配置中的 `platform` 改为 `zhipin` 或 `zhilian`；从 Python 导入 `QianchengPlatform` 的下游代码需要移除该依赖。
- **移除 README 的赞助与推广展示位，并移除 `atlas` provider 接入。** README 两语顶部的 Atlas Cloud 展示位（含 UTM 追踪链接与 logo 资产）和 Doloffer 推广块整体删除；`PROVIDER_BASE_URLS` 移除 `atlas`，`--provider` 帮助、`docs/integrations/ai-models.md` 两语与 CONTRIBUTING 的相关表述同步。这是维护者主动移除赞助关系，不是 CONTRIBUTING「端点不可用」条款下的移除，所以按变更通知记录。**迁移**：已保存 `ai_provider=atlas` 的配置在 `boss ai config` 里会看到 `resolved_base_url` 为空，请改为 `boss ai config --provider custom --base-url https://api.atlascloud.ai/v1 --model <model> --api-key <key>`，服务本身仍可作为自定义 OpenAI 兼容端点使用。
- **code 37 改为按响应语境分类（对外契约变更）。** 此前所有 code 37 一律全局映射为 `TOKEN_REFRESH_FAILED`
  （`recoverable=true`，恢复动作 `boss login`）。现在只有文案明确指向 token/stoken 过期的 code 37 保持该行为；
  环境风险文案及语义不明确的 code 37 一律发新的 `ENVIRONMENT_RISK`（`recoverable=false`），立即停止且不刷新、
  不重试、不提示重新登录。候选人与招聘者的 httpx 刷新判定与平台 adapter 使用同一分类器
  （`boss_agent_cli.api.zhipin_errors.classify_code_37`）；候选人浏览器通道额外在环境风控上直接终止。
  按错误码分支的下游 Agent 请新增 `ENVIRONMENT_RISK` 终止分支，绝不对其自动登录/刷新/重试。

### Added
- **`login --cdp` 复用现有登录态前做一次只读有效性探测（Issue #424 第二步）。** 复用命中后在同一页面里用浏览器自身会话请求用户信息接口（不读本地凭据，与 `existing-browser` 约束一致）：通过才落盘；服务端已失效则抛 `ReusedSessionStaleError`，`AuthManager` 自动改走 `--force` 路径重登，不再把死凭证当「登录成功」保存；探测命中 code 36 / 环境类 code 37 直接抛 `AccountRiskError` / `EnvironmentRiskError`，`login` 命令按与其他命令相同的风控契约返回 `ACCOUNT_RISK` / `ENVIRONMENT_RISK`（`recoverable=false`），且不再被降级到 QR / patchright 兜底再登一次。页面未就绪或探测网络失败按「未验证」继续并提示。目前只探测 zhipin，智联复用仍只看 cookie 存在性。
- `boss login --force`：不复用任何既有登录态（Issue #424 第一步）。跳过本地浏览器 Cookie 提取；CDP 路径不扫描已登录 context，清掉当前 context 内目标平台域的 cookie（其他站点与其他 context 不动）后打开登录页重新扫码，堵住「服务端已失效的 `wt2` 被复用路径当成已登录、用户没有重登入口」的死循环。与 `--cdp` 正交，不能与 `--curl-file` / `--cookie-source` 混用；`login` 的选项互斥错误现在按 `INVALID_PARAM` 返回而不是 `NETWORK_ERROR`。
- `boss login --curl-file` 支持从浏览器 cURL 文本导入 BOSS 登录态，验证后复用原生加密存储，不执行原请求或保存业务请求体。
- 招聘者模式新增推荐牛人完整卡片和需明确批准的首次招呼；使用单次直连请求及候选人／职位原子防重，不建立 MQTT 连接或发送已读回执。
- 新增 `hr accept-resume` / `hr download-resume` 及对应 MCP 工具：核对候选人与消息后同意请求，检查权限并下载已收到附件；复用原生认证，不自动重试写请求、不覆盖已有文件。
- **公开 `--browser-source` 浏览器来源选项**（取值 `auto` / `existing-browser` / `stored-cookie`，默认
  `auto` 行为不变）。`stored-cookie` 为 fail-closed 严格模式：只连接 `--cdp-url` 指定的 CDP 端点，
  不自动探测 `localhost:9222`/`DevToolsActivePort`、不降级 Bridge/headless、空浏览器不新建 context
  注入 Cookie，不可用即发 `CDP_UNAVAILABLE`；`existing-browser` 只复用现有浏览器（Bridge/CDP），
  不读本地凭据、不启动浏览器，失败发 `BROWSER_SESSION_NOT_FOUND`。stoken 静默刷新同样遵守该策略
  （#418）。CLI / config / `boss schema` / MCP / wizard 全链路透传；`schema` 的 `global_options`
  声明该选项并标注 `stability: experimental`，输出带 `current_browser_source`。非 `auto` 来源用于
  无浏览器通道的平台（当前为 zhilian）时返回 `NOT_SUPPORTED`。由 @iqjiy 提出并发现相关问题，
  策略表与契约见 Issue #387 / PR #410。
- **公开职位 lid 与浏览器职位卡片接口。** `JobItem` 新增 `lid` 字段（解析自列表响应
  `raw.lid`，缺失时为空串，并随 `to_dict()` 序列化）——取 JD 全文需要 `securityId` + `lid`
  两个参数，此前走 CLI 的调用方拿不到 `lid`。`BossClient` 新增 `job_card_browser(security_id, lid)`，
  强制走浏览器通道获取职位卡片；既有 `job_card()` 的 httpx 优先行为完全不变。

### Fixed
- **复用用户页签时撞上手动导航不再挂起（Issue #423）。** `BrowserSession.request()` 的 `evaluate(fetch)` 捕获 patchright 的 `Execution context was destroyed` / `Target closed` 类异常后，做一次有界 `wait_for_load_state` 恢复并重试同一请求**一次**；仍不就绪返回 `code: -1` 错误信封，重试再撞上同类异常原样上抛。健康路径零额外往返（不加等待），其他 evaluate 错误不重试。风控响应不经此路径，#419 契约不受影响。
- `--browser-source` 的取值域在 `boss schema` 与 `boss-mcp` 两处不再手写三元组，统一从 `api/browser_source.py` 的策略表推导（与 CLI / `config set` 一致，`schema` 里 `choices` 的顺序随之改为策略表顺序），新增来源时不会再漂移；schema 描述补上 `existing-browser` 候选耗尽发 `BROWSER_SESSION_NOT_FOUND`。补齐 config.json 非法 / 带大小写空白的 `browser_source` 值与 MCP 透传的测试。
- 招聘者写操作在 #403 的 code 37 分类下仍不刷新、不重试；招呼遭遇环境风控时仅提示官方页面人工处理，不再建议调用聊天接口。附件同意的未知结果改用已声明的 `RESUME_ACCEPT_RESULT_UNKNOWN`，确认提示复用通用恢复契约。
- 附件下载的登录与网络错误采用 schema 恢复指引，二进制 HTTP 401/403 映射为 `AUTH_REQUIRED`；以排他创建替代硬链接落盘，保留不覆盖与写入失败清理，补齐推荐牛人 CLI 成功和失败测试。
- 修复招聘者首次招呼将 `code=0` 的权益拦截页误判为成功：识别开聊业务拒绝，返回 `GREET_LIMIT`、`sent=false` 及脱敏平台提示；保留拒绝状态并阻止重复发送，不影响其他端点的成功判断。
- 招聘者招呼与附件操作保留登录失效、令牌刷新失败和账号风控分类，未知发送结果停止重试；最近消息未知未读数不覆盖会话真实计数，支持 `lastMsgInfo.showText`。
- **复用已登录 CDP 会话并消除首次导航竞态。** `login --cdp` 现在跨所有 browser context
  按精确 hostname / cookie domain 校验搜索已有 BOSS 登录态（`wt2`/`at`）：命中时复用该
  context 与既有平台页签，不导航登录页、不轮询等待；cookie 中已含 `__zp_stoken__` 时
  优先读取 cookie jar，仅在缺失且页面经有界 `wait_for_load_state` 确认可加载后才做页面
  提取。UA 采集按路径 gate：未登录时在已加载的登录页上提前采集（沿用 #390 顺序），复用
  新建页签时在首页确认加载后采集，复用既有页签时与 stoken 共用同一次有界就绪检查——
  导航卡住的页面 UA 与 stoken 都不会被 evaluate（避免 patchright 永久挂起）。清理时只
  关闭本次调用新建的页面。智联未登录时复用既有 recruiter 页签不再被导航走；智联已登录
  但无 recruiter 页签时新建页签也会回各自 home（修复 `x-zp-client-id` 在 about:blank 上
  读取失效导致的 `TokenRefreshFailed`）。API 浏览器会话（`BrowserSession._try_connect`）
  新建 CDP 页面改为等待 `domcontentloaded`，修复首次搜索出现 `execution context destroyed`
  的竞态；并为该路径补「卡住不 evaluate」门禁——goto 超时不再被 `except` 吞掉后直接
  `return True`，而是标记 `_page_ready=False`，`request()` 在 `evaluate(fetch)` 前先做有界
  `wait_for_load_state` 恢复，仍不就绪则返回错误信封而非永久挂起（与 #390 同一规则）。
  多 context 复用时在终端打出选中的 context 序号与「账号指纹」（登录态 cookie 值的不可逆
  哈希前缀，不泄露 cookie），缓解多窗口/多 profile 下的账号歧义。#390 的导航超时/重试、
  `_safe_user_agent`、`_warm_home_for_runtime` 与 cookie-jar 兜底全部保留。
- **风控类异常在三个执行器中的终止语义收敛（Issue #419）。** `AccountRiskError` / `EnvironmentRiskError`
  现在共享基类 `PlatformRiskError`（带稳定 `code`），新增风控码只需登记子类。此前三处执行器会反转或吞掉终止语义：
  福利筛选线程池的 `except Exception` 会吞掉浏览器通道抛出的风控异常并继续扫描整页；`batch-greet` 靠
  「频率」字样判停批，code 36 会被重试一次后继续下一个候选人，code 37 环境风控则被打成 `ok:true` +
  `stopped_reason="RATE_LIMITED"`；wizard runner 的兜底把两者降级成 `NETWORK_ERROR` + 「重试当前 run_id」。
  现在三处都立即停止、不重试、不继续下一项：线程池取消尚未开始的详情请求并原样上抛；`batch-greet` 命中风控时
  信封改为 `ok:false` + 对应错误码 + `recoverable=false`，已成功项随 `error.details.greeted` 带回；
  wizard runner 走专门分支，`recoverable=false`，恢复动作与单次命令路径逐字一致（`display.RISK_ERROR_CONTRACTS`
  为唯一真源）。wizard 的 `_classify_action_error` 对风控码不再落到「稍后重试」兜底；runner 同时把
  `BrowserSourceUnavailable` / `BrowserSourceUnsupported` 映射为策略错误码 / `NOT_SUPPORTED`，不再兜成 `NETWORK_ERROR`。
- **stoken 静默刷新现在遵守 `browser_source` 策略表（Issue #387 seam 收尾）。** #410 把浏览器通道
  选择收进策略表后，httpx 通道的 stoken 刷新（`_base_client._request` → `AuthManager.force_refresh`）
  仍绕过策略：`stored-cookie` 下 stoken 过期照样会「CDP 不可用，降级到 headless」，起一个 headless
  Chromium 带着本地 Cookie 访问平台。现在 `force_refresh` 接收 `browser_source`：fail-closed 来源
  不再自动探测默认 CDP 端口（没给 `--cdp-url` 直接失败）、CDP 不可用时不再降级 headless，
  抛带策略错误码（`CDP_UNAVAILABLE`）的 `BrowserSourceUnavailable`；智联显式来源下本地 Cookie 失效时
  同样 fail-closed，不再打开登录页等扫码。`auto` / 未传参行为逐字不变。该参数尚未暴露到 CLI
  （由 PR #404 接线），当前只影响程序化调用；信封层把该异常映射为策略错误码的 `display` 分支
  随 PR #388 落地，在此之前兜底仍为 `NETWORK_ERROR`。
- 修复 Bridge 已连接日常浏览器时 `chat` / `chatmsg` 仍先要求 CLI 本地 `wt2` 的问题（#386）。
  Bridge 连接作为本切片的现有浏览器使用信号，走 `existing-browser` 策略的 Bridge → CDP
  白名单，不读取本地凭据、不新建 context、不启动或降级到 headless；候选耗尽返回
  `BROWSER_SESSION_NOT_FOUND` + `boss doctor`，真人浏览器指引放在 `operator_actions`。
  Bridge 未连接时保留原 httpx、stoken 刷新和限流重试语义；本地无凭据仍返回
  `AUTH_REQUIRED` + `boss login`。中英文风险边界与能力矩阵同步说明：连接存在不等于已验证目标页登录。

### Changed
- ROADMAP 中 `BrowserSessionProvider` 条目那句「绝不启动新实例、绝不触发登录、绝不静默回退
  CDP/headless」按 #387 的决定限定为**仅在显式来源下**成立，`auto` 保持既有降级链；中英同步。

## [1.20.0] - 2026-09-03

> **升级提示（仅影响 `mcp` extra）**：`mcp` 依赖的**下界**从 `1.0.0` 提到 `2.1.0`
> （区间 `mcp>=2.1.0,<3.0.0`）。主推的 `uvx --from boss-agent-cli[mcp] boss-mcp`
> 每次现解析临时环境，不受影响；受影响的只有「把 `boss-agent-cli[mcp]` 装进一个
> 还钉着 `mcp<2` 的长期共享环境」这种场景——请一并放开该环境里的 `mcp` 约束。
> **MCP 线格式与工具集合一字未变**，宿主侧无需任何改动。

### Changed
- **适配 mcp 2.x Server API（Issue #398），依赖改为 `mcp>=2.1.0,<3.0.0`。** mcp 2.0 移除了
  `@server.list_tools()` / `@server.call_tool()` 装饰器，改为
  `add_request_handler(method, params_type, handler)`，且 handler 签名与返回类型一并改变
  （`(ctx, params)` → `ListToolsResult` / `CallToolResult`）。SSE 与 streamable HTTP 两条传输
  实现原样保留——本次只做适配，换实现是独立决策。
  **MCP 线格式一字未变**：`Tool` 的字段名内部从 `inputSchema` 改成 `input_schema`，但 pydantic
  别名保留，序列化仍输出 `inputSchema`，宿主侧无感知；源码里 71 处构造 kwarg 随之改名（mypy 的
  pydantic 插件按字段名生成 `__init__`，不认别名）。
  **上界保留**：上一次声明无上界导致所有全新安装解析到新大版本并崩溃，而 CI 锁着旧版本永远看不到。
- `CONTRIBUTING.md` / `.en.md` 新增「新增 AI provider」一节，把 provider 的准入与移除写成明规则：
  文档只写可用一次 API 调用证伪的陈述（不接受安全姿态、定价承诺、评级与会静默过期的规模数字）；
  服务商自荐需提供可验证归属（企业域名邮箱或官方公开双向引用，不追溯）；端点连续两个 minor
  版本不可用即直接移除，不走弃用周期、不算破坏性变更。第三条是前两条能够宽松的前提。
- 按上述规则对齐既有条目：`docs/integrations/ai-models.md` / `.en.md` 的 Atlas Cloud 段落移除
  59 个模型的全量清单（会随服务商上下线静默失真且无测试可守）与链接上的 UTM 追踪参数，
  定性措辞压回与 `openrouter` 同一语域，并补回「可用模型以服务端实际支持为准」。
  README 的赞助展示位不受影响——那是已披露的赞助关系，与 provider 文档条目是两回事。
- **浏览器通道选择收敛为策略表驱动的单一分发点**（Issue #387 seam 第 2 片的前置改造，无对外行为变更）。
  `BrowserSession._ensure_started` 原本是硬编码的 Bridge → CDP → headless 三级降级链，
  中间只有两处插入缝隙；两个并行 PR 各自在其中一处插模式短路时，git 三方合并会干净通过、
  不留冲突标记，合并后类上会出现两个互不感知的模式布尔，谁生效取决于插入位置且失效方向是
  fail-open。现新增 `api/browser_source.py`：一张冻结的 `BrowserSourcePolicy` 表声明
  「通道白名单 / 是否读本地凭据 / 能否新建 context / 能否启动浏览器 / 是否自动探测 CDP /
  失败发什么码」，`_ensure_started` 变成对 `policy.channels` 的单个循环，类上只保留一个
  `_policy` 属性。此后新增来源 = 表里加一行，物理上没有第二个插入点。
  **`auto` 为默认且行为逐字不变**（三级降级链、headless 兜底、失败仍走既有 `NETWORK_ERROR` 兜底），
  本次不暴露任何 CLI 选项、不新增任何错误码。新增 22 条结构门禁锁定上述性质。

### Fixed
- **修复设了 SOCKS 系统代理时每一条 httpx 命令都失败**（#412）。全仓四处 `httpx.Client`
  都用默认 `trust_env=True`，即刻意尊重用户的系统代理；但 httpx 只有装了 `socksio` 才支持
  `socks5://` scheme，否则在**构造 client 时**就抛 `ImportError`。于是设了
  `ALL_PROXY=socks5://...` 的用户，`boss status` / `detail` / `favorites` 等每一条只读命令
  都会失败——而那个 ImportError 被 `display.handle_auth_errors` 的兜底转成
  `NETWORK_ERROR` + `recovery_action="重试"`，错误码与恢复动作双错，且这是个永远不会
  因重试而改变的本地环境问题。依赖改为 `httpx[socks]`（拉 `socksio`，35KB、纯 Python、
  零传递依赖），让代理**能用**而不是失败得好看。
  CI runner 环境干净、没有代理变量，这条路径在门禁上永远是绿的，故补
  `tests/test_system_proxy.py` 显式注入代理环境变量把它变成可观测的。
- `boss ai config` 查看与写入现在都回显 `resolved_base_url`（解析后真正生效的端点）。此前查看模式
  直出 `load_config()`，用户只设 `--provider` 时 `ai_base_url` 为空，实际地址来自
  `PROVIDER_BASE_URLS` 查表却从不显示；而 `--provider` 没有 `click.Choice` 校验（自由字符串），
  名字相近的 provider 记混或拼错时，API key 与简历全文会被发到另一家而界面上看不出任何差别。
  该命令此前零测试覆盖，一并补齐 7 条。
- 修复 `BrowserSession` 在 `_start_headless()` 或 playwright driver 启动抛错时泄漏 driver 进程：
  `_pw` 已 `start()` 却永远不会 `stop()`，下一次 `request()` 会再起一个。现在启动失败路径统一
  释放 driver。
- 修复 `BrowserSession.close()` 之后通道标志残留：原实现只置 `_started = False`，`_is_cdp` /
  `_is_bridge` 保留上一轮的值，导致 close 后重启时一个 headless 轮次可能走进 CDP 拆卸分支
  而不关闭 browser。`close()` 同时改为真正幂等（已关闭时直接返回，不重复拆卸）。
- 修复 TTY 下节流等待期间进度条静默不动的观感问题（#400）：`CrawlBudget.wait()` 在等待前把剩余秒数报给 `SearchProgress`，进度条显示一次性「节流等待 Ns…」提示；非 TTY / `--json` 路径与等待 0 秒场景输出完全不变。
- 修复扫码登录在「首页预热」阶段卡住时永久挂起：BOSS 直聘首页在自动化下偶发长时间不完成加载，`page.goto` 超时后若仍对页面执行 `page.evaluate`（取 UA / stoken），patchright 会因执行上下文无法建立而无限阻塞，导致 `boss login` 卡死且不落盘凭证。现在 UA 改在已加载的登录页上提前采集；`_warm_home_for_runtime` 返回首页是否真正加载完成，仅在确认加载后才对页面 evaluate 提取 stoken，否则回退读取 cookie jar；并对首页导航超时增加「跳 `about:blank` 重置卡死导航后重试一次」，显著降低风控验证页/加载缓慢导致的 `Page.goto: Timeout 15000ms exceeded` 误报。`login_via_cdp` / `refresh_stoken` / `refresh_stoken_via_cdp` 同步加固；CDP 登录/刷新 stoken 也改为优先页面 evaluate、失败回退 cookie jar（安全验证跳转期间 `domcontentloaded` 可能未触发，但 `__zp_stoken__` 已写入 cookie jar）。
- 修复 BOSS 收藏接口在 `isActive=true` 下仍混入失效职位时的静默误判：`favorites list` 现输出规范化 `job_status` 与状态计数，`favorites sync` 仅导入明确有效职位并报告 active/inactive/unknown 数量；缺失或未知状态不再默认有效。

## [1.19.1] - 2026-08-27

### Fixed
- **修复全新安装的 `boss-mcp` 完全起不来。** `mcp` 此前声明为无上界的 `mcp>=1.0.0`，
  而 mcp 2.0 重写了 `Server` API——`@server.list_tools()` / `@server.call_tool()` 全部移除，
  改为 `add_request_handler`。`mcp_server` 在模块层就用这些装饰器，因此任何解析到 2.x 的
  全新安装都直接 `AttributeError: 'Server' object has no attribute 'call_tool'`，
  `boss-mcp` 连启动都做不到。已收紧为 `mcp>=1.0.0,<2.0.0`。
  这不是 1.19.0 引入的：1.18.0 在 mcp 2.x 下有完全相同的崩溃，只是此前没人从 PyPI
  全新装一次验过。issue #377 的报告人当时用了 `--with 'mcp<2.0'`，所以他们看到的是
  `-32601` 而不是崩溃。
  mcp 2.x 的适配另开 issue 跟踪，放开上界之前不做。

### Added
- CI 新增 `fresh_install` job：**刻意不走 `uv.lock`**，装构建产物、让依赖按 `pyproject`
  的约束重新解析，再真跑一次 MCP 会话。此前所有 job 都锁着 1.x 依赖，因此
  「CI 全绿、用户全崩」可以长期共存——这个 job 守的就是**门禁看到的与用户拿到的**
  之间那道缝。红灯验证：移除 `mcp` 上界后该 job 以「initialize 没有收到响应」失败。

## [1.19.0] - 2026-08-27

### Fixed
- 修正 CI docker job 的 `tools/list` 断言存在竞态：`printf` 写完三行就关闭 stdin，
  MCP 服务器可能在回 `tools/list` 之前就因 EOF 退出，读端拿到空串报 `JSONDecodeError`
  （已在 CI 上真实发生）。改为写完后保持 stdin 一段时间，并按 JSON-RPC `id` 认响应而非
  依赖行序；`tools/list` 无响应时给出明确断言而不是解析错误。**一道会随机变红的门禁
  等于没有门禁**——它会挡住无关的合并，正如同批修掉的 PTY 用例那样。
- 修正 Docs 工作流的 `Check whitespace` 对**落后于 master 的 PR 一律假红**：
  `git fetch --no-tags --depth=1 origin <base>` 只取到 base 的 tip 并留下浅克隆边界，
  落后的 PR 分支算不出 merge base，`git diff --check A...B` 直接以
  `fatal: origin/master...HEAD: no merge base` 退出 128——报出来像空白字符违规，
  实际是这一步崩了。#382 / #383 / #390 三个外部 PR 的 `docs` 红灯都是这个原因，
  且恰好是等得最久、落后最多的贡献者最容易撞上，还会被当成他们自己的问题。
  去掉 `--depth=1` 即可（checkout 已用 `fetch-depth: 0`，这里只是补 `origin/<base>` 引用）。
- **修复 MCP `tools/list` 从未注册：v1.18.0 的 MCP 入口对任何 host 都不可用。**
  `mcp_server.list_tools` 缺失 `@server.list_tools()` 装饰器，`initialize` 握手正常，但 client
  第一次列工具即 `-32601 Method not found`——73 个工具一个都拿不到。回归由 1.18.0 的
  `mcp_server` 拆分引入，该拆分合并 33 分钟后即发版，没有留下暴露窗口。
  四道门禁同时失效，故本次连门禁一起补（见下方 Added）：`tests/test_mcp_server.py` 用 mock 模块
  顶替整个 `mcp` 包，装饰器在不在原理上观测不到；CI 的 stdio 握手只断言 `initialize`；
  eval 跑离线 fixture 绕开协议层；`__all__` + `mcp-server/server.py` 的 re-export 让这个
  未注册的孤儿函数在 ruff / mypy 眼里完全正常。感谢 @xie-good 报告、@LiuLin1220 修复。
- 脱敏放宽：`_SENSITIVE_KEY_PARTS` 是子串匹配，`ai_max_tokens` / `token_expires_in` 只因键名含
  `token` 就被替换成 `[REDACTED]`。凭据必然是字符串或容器，故把 bool 豁免补完整为
  `(bool, int, float, None)`，并加护栏测试锁定 `stoken` / `api_key` / `cookies` 等仍被脱敏。
- 修正三个终端呈现缺陷：结果列表菜单每页重绘任务摘要框（此前被 `clear_before` 清掉）；
  `render_run` 新增 `with_preview` 开关，浏览列表时职位不再被摘要框与可选菜单列两遍；
  `search` 长任务在 TTY 下注入 `SearchProgress` 显示实时进度（管道 / `--json` 仍传原 logger）。
- 修正 `tests/test_wizard.py` 的 PTY 用例假红：启动同步循环 5s 超时后**照样发送按键**，
  此时 prompt_toolkit 尚未接管输入，按键被丢弃，最终报成「没退出」——把「启动慢」误诊为
  「交互坏」，且不留任何线索。现在必须确认菜单真渲染出来才发按键，否则以「菜单未渲染」
  失败并附上已捕获输出；期限放宽到 30s / 20s 并可用环境变量覆盖（命中同步标记即跳出，
  快机器仍约 1s）。此前它曾在 CI 的 P0 baseline job 上超时并挡住合并，而同一 commit 的
  五个测试矩阵 job 全过。

### Added
- MCP handler 注册守卫 `tests/test_mcp_handler_registration.py`：不查装饰器语法，直接问运行时
  注册表 `server.request_handlers`。「装饰器名 → request 类型」的映射**从 SDK 自身反推**
  （在全新 `Server` 上应用装饰器后 diff 注册表键），因此 SDK 将来新增 `list_prompts` /
  `read_resource` 等 handler 时守卫覆盖面自动跟上，不需人工维护。同时断言 `tools/list` 经协议
  发出的工具集合与 `mcp_tools.TOOLS` **逐名相等**（不引入硬编码计数）。两条测试刻意在子进程里
  跑：`tests/test_mcp_server.py` 会 `sys.modules.setdefault("mcp", <mock>)`，一旦拿到 mock，
  `request_handlers` 就是 MagicMock，守卫会静默变成恒真。
- CI 的 docker job 在同一个 stdio 会话里补做 `tools/list`，断言工具列表非空且含 `boss_status`。
  只断言 `initialize` 正是让上面那个回归发出去的原因。
- 开放 assisted / research 下全部已实现能力，移除模式级 `COMPLIANCE_BLOCKED` 执行门
  （错误码保留作历史协议兼容）；平台未实现的能力仍返回 `NOT_SUPPORTED`。
- 新增共享 Workflow 持久化与纯终端 `boss wizard`：无子命令时默认进入向导，TTY 向导与
  `--input-json` / `--resume` / MCP 共用同一个确定性 runner，状态落 SQLite 的
  `workflow_runs` / `workflow_steps`。`schema` 与 MCP 同步暴露 `wizard` 与 `wizard_catalog`。
- 增强 crawl 传输：登录态注入、页面内请求与回退，命中风控时保留浏览器便于续跑。
- 信封 `hints` 拆为双受众通道：`next_actions` 给 Agent 执行的后继命令，`operator_actions` 给真人的
  自然语言指引（扫码、去浏览器操作等）。判定标准是「是否需要人离开终端」，不是「是否需要确认」。
  TTY 下 `display.render_operator_actions` **只渲染 `operator_actions`** 到 stderr——此前 TTY 分支
  把 `hints` 整个丢弃，真人从来看不到任何提示。无 `operator_actions` 时零输出，既有命令的 TTY
  行为逐字节不变。`SCHEMA_DATA["conventions"]` 新增 `hints` 与 `command_vs_wizard` 两块。
- 新增向导入口登录门 `wizard/preflight.py`：登录检查从 goal 的第一个 step 前移到收集角色 /
  平台 / 目标 / 参数之前——此前未登录时这五层选择全部白做。已登录时零输出零开销（本地文件读，
  不预检浏览器内核）；未登录时可内联扫码登录并无缝进向导；浏览器内核缺失时先给
  `patchright install chromium` 指引，不再开完浏览器才失败。headless 路径保持 `WAITING_INPUT`
  不变（TTY 下人就在终端前，阻塞等扫码是刻意的；Agent 不能卡在子进程里空等）。
- 向导未登录时 run 状态由 `failed` 改为 `WAITING_INPUT`，可 `--resume` 且保留已选的角色 /
  平台 / 目标，不必从头重走。
- 补齐候选者侧九个命令组（`stats` / `preset` / `watch` / `clean` / `ai-local` / `resume` / `ai` /
  `crawl` / `agent`）共 63 处 `handle_output` 的 TTY 渲染分支，真人不再需要自己读 JSON 信封。
  `display.py` 新增 `render_next_steps` / `render_action_result` / `render_list_result` /
  `render_record_result` / `render_ai_result` 五个通用件；`render_ai_result` 不假设键名、只按值类型
  呈现，并对所有值做 escape，避免 AI 输出里的方括号被 Rich 当标记解析。stdout 仍只有单行信封。
- 向导整个多轮交互会话跑在 alternate screen（`\033[?1049h` / `\033[?1049l`）里：进入切、退出还原，
  终端原有内容完好，不再往 scrollback 留残框——`clear_wizard_screen` 的 `\033[2J` 只清可视屏、
  清不掉历史，这是「上滚看到多个框」的根因。用 context manager 保证还原（`commands/wizard.py`
  有 59 处 `return` 且内部会 `raise SystemExit`，逐点还原必然漏）。单次 flag 路径
  （`--status` / `--resume` / `--stop`）刻意不进备用屏，否则渲染完立刻被抹掉。菜单包进 `Frame`，
  框标题为当前步骤；菜单支持 `←` / `Esc` 返回。

## [1.18.0] - 2026-07-29

### Added
- 容器化落地：仓库自带的 `Dockerfile` 重写为多阶段 uv 构建（`uv sync --frozen` 严格按 `uv.lock` 安装，依赖不再漂移）+ 非 root 运行 + `.dockerignore` + `docker-compose.yml` + [Docker 接入文档](docs/integrations/docker.md)，并在 CI 新增 `docker` job 每次构建并验证 CLI 信封、MCP stdio 握手与非 root 身份。镜像定位刻意收窄为**只跑 MCP server 与只读 / 本地命令**：不含浏览器内核，`boss login` 仍在宿主机完成后挂载 `~/.boss-agent`（容器内 `HOME=/data`，故默认数据目录解析为 `/data/.boss-agent`）。不发布 registry 镜像。
- P0 质量门禁新增离线 MCP 评测：`evals/run_eval.py --mode fixture`，产物写 CI 临时目录，避免污染入库的 `evals/results/`。
- CI 矩阵补齐 Python 3.14；`scripts/` 纳入 ruff lint 门禁，并补齐 CI 冒烟路径。

### Fixed
- 修正 MCP 工具 `boss_favorites_list` 的 `page` 参数 schema：类型写成了 JSON Schema 不存在的 `"int"`（其余 105 个参数均为合法值），严格校验 schema 的 MCP 宿主可能因此拒掉该工具。同时给 `boss_crawl_status` / `boss_crawl_results` / `boss_crawl_shortlist` 的 9 个参数补上缺失的 `description`——Agent 依赖它判断该传什么值。新增三条契约测试守住这一整类问题（type 合法性、参数必须有 description、顶层结构与 required/items 完整性）。
- `RecruiterPlatform` 抽象基类补齐两个已被命令层实际调用、却从未写进契约的方法：`send_message_by_friend`（`hr reply` 在用）与 `job_detail`（`hr jobs detail` 在用）。此前它们只存在于 `BossRecruiterPlatform` 实现里，任何新写的招聘者平台按 ABC 实现完都能通过类型检查，却会在这两条命令上运行时抛 `AttributeError`。
- MCP 的 HTTP streaming 传输改用 `@asynccontextmanager` 声明 lifespan：此前是裸 async generator，会走 Starlette 的弃用路径并触发 `DeprecationWarning`。
- MCP `_run_boss` 现在只接受 JSON 对象形式的 CLI 信封：解析结果非 dict 时（契约违例）返回 `CLI_ERROR` 信封，而不是把非 dict 结果透传给调用方。
- GitHub Release 资产过滤改为只上传 `dist/*.whl` / `dist/*.tar.gz`，避免构建后端生成的 `dist/.gitignore` 被当成 `default.gitignore` 垃圾资产挂上 Release。

### Changed
- 将 1600+ 行的 `mcp_server.py` 拆为 `mcp_tools`（工具目录与合规过滤）、`mcp_args`（工具名 → CLI 参数映射）与精简后的服务器/传输层；公开符号原样再导出，`mcp-server/server.py` wrapper 与既有测试导入路径不变。
- mypy 逐模块严格化白名单 120 → 130：纳入 `crawler/` 全部子模块、`commands/crawl.py`、`commands/_recruiter_platform.py`、`mcp_server.py`，以及拆分后的 `mcp_args.py` / `mcp_tools.py`。
- `pyproject.toml` 显式钉死 ruff 规则集 `select = ["E4", "E7", "E9", "F"]`。此前未配置 `[tool.ruff.lint]`，门禁范围实际由所装 ruff 版本的隐式默认值决定——升级到 0.16.0 时默认集扩展到 I / BLE / RUF / UP / DTZ / S / PYI 等家族，同一份代码凭空多出 344 条报错。新增规则家族改为需单独提 PR 并连同修复一起评审。
- 依赖刷新（`uv.lock`）：rich 14.3.3 → 15.0.0、uvicorn 0.46.0 → 0.51.0、pytest 9.0.3 → 9.1.1、ruff 0.15.8 → 0.16.0、websockets 16.0 → 16.1.1 等共 20 个包。rich 为大版本跨越，`display.py` 是全仓唯一使用方，已在加强断言后的渲染测试下逐项验证无行为变化。
- 补齐渲染层断言并收紧 crawl / 招聘者平台实例化覆盖；codecov 项目门槛同步收紧。
- README 首页 badge 行增加 MCP Toplist live rank（中英对称）。

## [1.17.0] - 2026-07-27

### Added
- 新增 `boss favorites list/sync`：读取 BOSS 职位收藏（geekGetJob?tag=4）并同步到本地候选池，让 `boss ai analyze-jd/fit/resume-optimize/chat-coach` 能处理用户真正感兴趣的职位。默认只读、assisted 放行、用户主动触发；sync 远端只读拉取后本地 upsert（按稳定 job_id 去重，刷新动态 security_id 并保留首次收藏时间），list 单页预览不落库且输出脱敏 `security_id`/`job_id` 为 `[REDACTED]`（完整值落库后从 `shortlist list` 取，遵循现有导出脱敏约定）。MCP 仅暴露 `boss_favorites_list`（只读预览），sync 不对 MCP 暴露以保持 assisted 主动触发边界。查看职位详情需 sync 落库后用 `boss --json shortlist list` 取完整 `security_id`/`job_id`，再 `boss detail <security_id> --job-id <job_id>` 查看（list 预览已脱敏；detail 以 sid 为必填位置参数，httpx 快速通道只用 job_id）。
- 搜索结果与详情输出新增原始岗位类型、规范化用工类型、每周实习天数、最短实习周期等字段；导出与索引缓存同步保留这些元数据。
- 新增受限 Research Mode 的可恢复 BOSS crawl 工作流：独立 Chrome profile、固定请求/详情/墙钟/重试预算、SQLite checkpoint、停止开关、外置 Hook 完整性校验，以及脱敏 selector 导入本地候选池。MCP 保持 assisted-only，仅可读取或本地导入已有 crawl run。

### Fixed
- 修复 BOSS 直聘“实习”筛选错误映射为兼职参数的问题：请求参数改为 `jobType=1902`；明确仅搜索实习时，搜索、导出和批量打招呼会按响应中的真实岗位类型失败关闭，避免把全职、兼职或类型缺失的岗位误当作实习岗位。搜索缓存结构同步升级，避免复用旧版本产生的混合岗位缓存。

## [1.16.0] - 2026-07-17

### Added
- 新增显式 `operating_mode` 双模式契约：默认 `assisted` 保持既有低风险阻断；用户主动设置 `research` 后，可启用策略注册表声明的浏览器协议、反调试、风控适配和受控采集研究能力。`boss schema` 同步暴露当前模式、可选模式和逐命令风险/数据分类。

### Changed
- 合规护栏升级为不可变能力策略注册表，CLI、schema 与默认 MCP 过滤从同一真源派生；旧内部配置 `low_risk_mode=false` 兼容映射到 `research`，但不再作为公开配置入口。

## [1.15.0] - 2026-07-14

### Added
- 新增 `boss ai cover-letter <resume> [--jd <文本|@文件>|--job-id <id>] [--tone 简洁专业|热情积极|谨慎稳重] [--lang zh|en]`：基于本地简历与目标岗位起草求职信/自我介绍草稿，纯本地 + AIService、零平台请求，draft-only（只产出文本、不发送、不进敏感命令）；MCP 同步新增 `boss_ai_cover_letter`。

### Removed
- 移除 `config.json` 中 6 个从未被任何命令消费的死配置键（`default_city` / `default_salary` / `batch_greet_max` / `login_timeout` / `resume_default_template` / `resume_export_format`）：此前它们可经 `boss config set` 设置却静默无效，现改为按未知键明确拒绝；同时消除 `resume_export_format` 默认 `pdf` 与 `resume export` 命令默认 `json` 的自相矛盾。
- 移除 `interviews` 命令中恒为假的 `_stub` 占位分支：`interview_data` 端点对 zhipin/zhilian 均已接通、无任何 client 再写入 `_stub`，故删除该死码及其 `capability: stub` hint（纯内部清理，正常输出不变）。

### Changed
- 命令层错误信封统一改用既有 `handle_platform_error_output`：当平台响应携带 `error.details`（主要是 `qiancheng` 占位响应）时，错误信封现在会带上 `details` 字段（新增字段，与已在用该 helper 的 `me` / `history` / hr `jobs` 命令口径一致）；BOSS 直聘 / 智联的真实响应不含该字段，输出不变。

### Fixed
- 修复智联招聘 CDP / 浏览器页面选择的域名校验：此前用 `"zhaopin.com" in url` 子串判断，可能被 `zhaopin.com.evil.example` 等伪造 hostname 误判为可信页面；改为 `urlparse().hostname` 精确匹配 `zhaopin.com` 及其子域（CodeQL `py/incomplete-url-substring-sanitization`，两处页面查找共用同一 host 判断）。
- 修复 #334：CDP 模式下每次搜索/推荐等高风险操作都会在用户 Chrome 新开一个 BOSS 首页标签页并导航（观感为“反复自动新开/刷新页面”）。现优先复用用户已打开的 zhipin 标签页（精确 hostname 校验），不再多余新开与导航，`close()` 也不再关闭被复用的用户标签页；无已打开 zhipin 页时回退原新建行为。风控即停（code 36）逻辑不变。

## [1.14.0] - 2026-06-25

### Added
- `boss shortlist` 新增本地标签、备注与离线对比能力：`shortlist add --tags/--note` 可在加入候选池时记录本地整理信息，`shortlist annotate` 支持增删标签和更新备注，`shortlist compare [--tag]` 仅从 SQLite 候选池读取并按标签本地过滤；MCP 同步新增 `boss_shortlist_annotate` / `boss_shortlist_compare`，默认低风险工具数 33 → 35。
- 新增 `boss ai fit --resume <name> [--limit N]`：基于本地简历与候选池已缓存职位详情生成逐岗匹配度、能力缺口、关键词命中和建议；缺少详情的岗位只报告本地缺口并提示先执行 `boss detail`，不发起任何新增平台请求。
- 新增 `boss ai suggest-keywords [--limit N]`：基于候选池职位分析推荐搜索关键词组合，零平台请求，纯本地 AI 辅助；MCP 同步新增 `boss_ai_suggest_keywords`。
- 新增 `boss ai resume-optimize <name> --jd <text>|--job-id <id>`：基于目标岗位优化简历措辞（仅建议，不修改简历），支持从缓存读取职位描述，零平台请求；MCP 同步新增 `boss_ai_resume_optimize`，默认低风险工具数 35 → 37。

### Changed
- 转向 MCP-first：README / landing / docs 将 MCP（`uvx --from boss-agent-cli[mcp] boss-mcp`，33 个默认低风险 MCP 工具）列为 Agent 首选接入，移除 `npx skills add` 引导；MCP server 新增 server-level `instructions` 承载低风险与错误恢复 doctrine；独立 Agent Skill 仓库 boss-skill 退役。CLI 命令、JSON 信封与错误码不变，仍以 `boss schema` 为真源。
- `boss search` 结果新增纯本地 `match_score`，福利筛选结果可用 `--sort score` 按匹配分降序输出；评分只使用已抓取字段与福利匹配注解，不改变翻页、详情补抓、节流或并发上限。
- 修正 ROADMAP 多平台条目的智联状态漂移：父行此前仍写"智联为 v2.0 优先接入候选（2-3 周）"，与自身已勾选的 Week 2-3 子项（只读 + 写操作已实现）及 CHANGELOG 1.12.0「候选者侧已接通」矛盾；改为如实声明"智联候选者侧已接入"，保留拉勾/猎聘不接入与 51job backlog 结论。

### Changed
- 修正 `docs/marketing/awesome-submissions.md` 投稿模板内自相矛盾且陈旧的数字（命令数 34/33、招聘者子命令 7、MCP 工具 49/31、测试 1315、release 1.11.0），统一为当前准确值：35 顶层命令 / 9 招聘者子命令 / 33 默认低风险 MCP 工具 / 1467 测试 / release 1.13.1（其中"默认低风险 MCP 工具"取合规过滤后实际导出的 33 个，与 README.en 受测口径一致）。

### Changed
- 修正贡献者文档中指向已迁出路径 `skills/boss-agent-cli/SKILL.md` 的陈旧引用（`CONTRIBUTING.md` / `CONTRIBUTING.en.md` 添加新命令清单第 5 步改为 `docs/commands.md`、PR 模板指向独立仓库 boss-skill），消除迁出 SKILL 后遗留的误导。
- 合并 `docs/blog/` 进 `docs/marketing/`：发布/推广文案归拢到单一目录，去除同类内容的双目录冗余。

## [1.13.1] - 2026-06-16

### Changed
- `--welfare` 搜索的职位描述详情新增本地缓存（按稳定的 `job_id`/`encryptJobId` 键，TTL 24h）：重复或微调条件的 welfare 搜索复用已取描述，跳过对应 `job_card` 请求，减少平台请求量并加速重跑；不改变请求间隔与并发上限（合规姿态不变）。
- `RequestThrottle` 改为线程安全：welfare 详情线程池并发调用下，加锁并预占下一次发送时刻，杜绝竞态导致的请求突发（突发更像爬虫，是合规风险）。

### Fixed
- `boss search` 浏览器通道冷启动削减两处无效等待（CDP 自动探测超时 3s→1s、headless 首页 networkidle 宽限 3s→0.8s），普通搜索实测 14.3s→10.0s（约 -30%），惠及全部搜索；零新增请求、不触碰风控节流。
- `boss export` 缺少关键词时的 `INVALID_PARAM` 信封补充可执行 `recovery_action`，Agent 可程序化恢复。

## [1.13.0] - 2026-06-11

### Added
- `boss platforms` 新增 `--platform` 单平台过滤，支持 `qiancheng` 与 `51job` 别名，仅读取本地能力元数据并保持未知平台的 `INVALID_PARAM` JSON 包络。
- `boss platforms` 输出新增 `capability_status_legend`，解释 `available` / `not_supported` / `placeholder_only` / `low_risk_blocked` 的清晰语义，避免 Agent 将占位或低风险阻断误读为真实能力。
- `boss schema` 错误码枚举补齐 login 链路 6 码（`LOGIN_TIMEOUT` / `CDP_UNAVAILABLE` / `BROWSER_KERNEL_MISSING` / `LOGIN_RISK_CONTROL` / `LOGIN_EXPIRED` / `LOGIN_CREDENTIAL_EXTRACTION_FAILED`），错误码总数 26 → 32，Agent 可经 schema 发现全部登录错误的恢复动作。

### Changed
- 补强 `boss config` 未知配置项错误路径的 stdout 单行 JSON 包络契约测试，确保 Agent 可稳定解析 `INVALID_PARAM`。
- PR 模板补充无 `Co-authored-by` 尾注或 AI 署名行检查项，对齐贡献规范。
- 51job/前程无忧占位适配器补齐候选者侧全量能力的稳定 `NOT_SUPPORTED` 包络，避免未启用真实适配前落入默认 `NotImplementedError`。
- 补强 51job/前程无忧占位包络的 `capability` 明细契约，确保 Agent 可稳定区分具体未支持能力。
- 双语 README 重构为导航型主页：命令参考与诊断排障下沉到 `docs/commands(.en).md` 与 `docs/troubleshooting(.en).md`，中英结构对齐；根目录演示资产归位 `demo/`，推广物料归位 `docs/marketing/`。
- Agent Skill 安装路径统一为 `npx skills add can4hou6joeng4/boss-skill`：CLI 的 Agent Skill 迁至独立仓库 [boss-skill](https://github.com/can4hou6joeng4/boss-skill)（仓库本身即 Skill），与 CLI 发版节奏解耦；CLI 能力与错误码继续以 `boss schema` 自描述为真源。

### Fixed
- `boss doctor` 的 `patchright_chromium` 检查改为按 patchright 自带 `browsers.json` 声明的修订版精确校验，修复"缓存存在其他版本即报 ok 但 `boss login` 启动浏览器失败"的假绿灯。
- `boss login` 浏览器内核缺失时不再误报 `CDP_UNAVAILABLE`：新增 `BROWSER_KERNEL_MISSING` 分类，`recovery_action` 修正为可执行的 `patchright install chromium`。
- 隔离简历导出默认路径测试，避免每次全量测试向仓库根目录写入临时文件。

### Removed（含 Breaking Change）
- 根目录 `SKILL.md` 迁移至独立仓库 [boss-skill](https://github.com/can4hou6joeng4/boss-skill)；**此前经 `npx skills add can4hou6joeng4/boss-agent-cli` 安装的用户，请改用 `npx skills add can4hou6joeng4/boss-skill` 获取更新**。CLI 命令行为、JSON 信封与既有错误码全部向后兼容。
- `skills/boss-resume-downloader` 招聘者简历批量同步工作流退役下线（与默认低风险合规姿态存在张力）；历史版本可在本仓库 git 历史中找到。

## [1.12.0] - 2026-06-09

### Added
- MCP server 新增 `stdio` / `sse` / `http` 三种传输模式，`boss-mcp --transport http --port 8765` 与 `boss-mcp --transport sse` 可直接启动，默认仍保持 `stdio`。
- `mcp-server/README.md` 补齐 SSE / HTTP Streaming 启动示例、默认路径和自定义路径说明。
- MCP 新增 `boss_export`：协议服务对齐 CLI `boss export`，Agent 可直接导出搜索结果为 CSV / JSON / HTML（默认脱敏 job_id/security_id/boss_name），工具计数 31 → 32。
- 增加前程无忧 / 51job（`qiancheng`）平台占位适配器，统一返回 `NOT_SUPPORTED`，为后续多平台能力矩阵保留低风险扩展入口。

### Changed
- 招聘者写操作收口到同一条 chat-tab Vue/CDP 链路：`boss hr reply` 现在用真实 WebSocket 帧校验发送成功，`boss hr request-resume` 不再需要 `--job-id`，`boss hr resume --exchange` 支持 `phone|wechat`。
- MCP 新增 `boss_hr_exchange`，并同步修正 `boss_hr_request_resume` 的输入契约为仅需 `friend_id`。
- 测试依赖 `pytest` 最低版本提升到 `9.0.3`，并同步更新锁文件，修复 Dependabot 报告的临时目录处理漏洞告警。
- ROADMAP / README / README.en 对齐当前主线事实：`#48` 已完成，智联候选者侧登录与读写链路已接通，招聘者侧仍保持显式拒绝。
- ROADMAP Week 4 招聘者侧能力评估落槌为「暂不接入」（接入条件 0/4 满足，保留 `RecruiterPlatform` 骨架待社区信号重启；详见 `docs/research/platforms/zhaopin-recruiter-evaluation.md`）。
- README 与设计文档同步前程无忧占位状态，并明确 RPA / 浏览器自动化风控边界。

### Fixed
- `boss hr chat --label-id` 兼容招聘端沟通列表接口直接返回数组的响应形态，避免成功响应被误处理为 `NETWORK_ERROR`。
- `boss interviews` 占位实现现在经 hints 如实声明能力状态（`capability=stub` + `note`），避免 Agent 把空集合占位误读为「无真实面试邀请」。
- 改善登录错误诊断，并补强登录合规与隐私清理契约。

### Tests
- 补强低风险合规、求职流程只读、智联客户端生命周期、招聘者平台生命周期、终端渲染、统计、简历命令、联系人查找、招聘端点和招聘端低风险阻断等回归契约。

### Packaging
- 显式排除本地虚拟环境、缓存和构建产物，避免本地 `.venv*` 目录污染 sdist。

## [1.11.0] - 2026-04-23

### Added
- **招聘者模式**（`--role recruiter`）— 全套招聘者 CLI 命令组：
  - `boss hr applications` — 查看/筛选候选人投递申请
  - `boss hr resume <id>` — 查看/请求候选人简历
  - `boss hr chat` — 与候选人沟通列表
  - `boss hr jobs list/online/offline` — 职位发布管理
  - `boss hr candidates` — 候选人池
- `BossRecruiterClient` 双通道客户端（httpx 低风险 + 浏览器高风险），复用 `AuthManager`/`BrowserSession`/`RequestThrottle`
- `RecruiterPlatform` ABC + `BossRecruiterPlatform` 适配器，遵循现有 Platform 抽象模式
- `api/recruiter.yaml` 招聘者端点定义（11 个 wapi 端点，Phase 0 待确认）
- `schema` 输出新增 `current_role`、`--role` 选项、`hr` 命令组、4 个招聘者错误码
- `CacheStore` 新增 `recruiter_applications`/`recruiter_jobs` 表
- 测试 998→1021（+23），覆盖客户端、平台适配器、命令、端点加载
- **ZhilianClient 内部 HTTP 客户端骨架**（Issue #140 Week 2 起步）— 新增 `src/boss_agent_cli/api/zhilian_client.py`：
  - 类结构 + `__init__(auth_manager, *, delay, cdp_url)` 签名完全对齐 `BossClient`
  - `close()` / `__enter__` / `__exit__` 资源生命周期支持
  - `atexit` 自动清理未显式关闭的实例
  - 所有 P0 方法（search_jobs / job_detail / recommend_jobs / user_info）抛 `NotImplementedError` 附 Issue #140 链接
- **`get_platform_instance` 支持按平台分发 client**（`_platform.py`）：
  - zhipin → BossClient
  - zhilian → ZhilianClient
  - 新增 `_build_client(name, ...)` helper 封装分发逻辑
- `tests/test_zhilian_client.py` 13 条契约测试覆盖类结构 / 上下文管理器 / stub 方法 / 平台路由分发

### Changed
- **`boss batch-greet` 迁移到 Platform**（Week 1c 第 3 个命令）— 从 `BossClient` 直用切换到 `get_platform_instance(ctx, auth)`，内部 `client.search_jobs` / `client.greet` 改为 `platform.search_jobs` / `platform.greet`。删除 `greet.py` 对 `BossClient` 的直接引用。
- mypy 严格白名单扩到 72（新增 `api.zhilian_client`）
- README、Agent Quickstart、Codex / Claude Code 接入文档统一到 recruiter workflow + MCP / skill 导向的对外表述
- GitHub 仓库设置补齐：自动合并、update branch、security fixes、master 分支保护增强（加入 `test (3.13)` / `typecheck` / `GitGuardian`）

## [1.10.1] - 2026-04-21

### Added
- **Week 1c 100% 完成**（PR #139）— 剩余 6 个命令迁移到 Platform 抽象：`search` / `export` / `chat_summary` / `history` / `status` / `watch`。`run_search_pipeline` 接受 `client: Any` 保持兼容，直接传 Platform 实例。
- Platform ABC 新增 `job_history(page=1)` 方法（history 命令所需）。
- **`docs/platform-abstraction.md`** — Platform 抽象设计与迁移 SOP 文档化 Week 1 全经验，包含 ABC 方法契约、迁移模板、测试 mock 位点规则、ZhilianPlatform 自证经验。

### Changed
- 至此 `commands/` 目录下除 `_platform.py`（桥接层）外**无任何命令直接引用 BossClient**。
- ZhilianPlatform stub 的 `NotImplementedError` 消息从"详见 Issue #129"更新为"追踪进度见 Issue #140"（PR #141）。

### Fixed
- CHANGELOG 中 [1.10.0] 的"14 命令 + search 留待独立 PR"描述，实际在本版本完成全量 20 命令迁移。

## [1.10.0] - 2026-04-21

### 🎯 核心里程碑：Platform 抽象架构落地（Issue #129 Week 1 全量收口）

从 v1.9.1 到 v1.10.0，本版本沿着 C → A → B 三阶段路线把 Platform 抽象从"只有骨架"推到"两个平台注册 + 14 个命令走抽象"的稳定状态，为 v2.0 多平台（智联真实现）打平底盘。

#### Platform ABC 完整设计（PR #131 / #132 / #133 / #136）
- `src/boss_agent_cli/platforms/` 新子包定义 Platform ABC + 注册表 + BossPlatform adapter
- P0 只读（`search_jobs` / `job_detail` / `recommend_jobs` / `user_info`）+ P1 写操作（`greet` / `apply`）+ P2 沟通（`friend_list`）
- ABC 扩展 P0+：`resume_baseinfo` / `resume_expect` / `deliver_list` / `job_card` / `interview_data` / `chat_history` / `friend_label` / `exchange_contact`
- ABC 统一 `__init__(client: Any)` 签名 + `with` 上下文管理器（`__enter__` / `__exit__` / `close()`）
- `Platform` / `BossPlatform` / `ZhilianPlatform` / `get_platform` / `list_platforms` 导出到 `from boss_agent_cli import ...` 公共 API 面

#### CLI 与注册表（PR #132 / #134）
- `--platform` 全局 CLI 选项（默认 `zhipin`），未知平台 exit code 2
- `~/.boss-agent/config.json` 支持 `"platform": "zhipin"` 字段持久化
- `boss schema` 输出 `current_platform` + `supported_platforms`
- Zhilian stub 接入注册表，`boss --platform zhilian schema` 端到端可用

#### 命令层迁移（PR #133 / #135 / #136 / #137，共 14 个命令）
| 命令 | PR | 使用的 Platform 方法 |
|------|-----|--------------------|
| `greet` / `apply` | #133 | `greet` / `apply` |
| `batch-greet` | #135 | `search_jobs` + `greet` |
| `interviews` / `detail` / `show` / `me` / `recommend` | #136 | P0 + P0+ 方法 |
| `chat` / `chatmsg` / `mark` / `exchange` / `pipeline` / `digest` | #137 | `friend_list` / `chat_history` / `friend_label` / `exchange_contact` / `interview_data` |

唯一留待独立 PR 的是 `search`，因为通过 `run_search_pipeline(client, ...)` 间接耦合 BossClient 签名需单独重构。

#### ZhilianPlatform 自证（PR #134）
- `src/boss_agent_cli/platforms/zhilian.py` — 智联招聘 stub 实现
- 包络适配按 [zhaopin.md](docs/research/platforms/zhaopin.md) §4 完整实现（`code==200` / `data` key / 401+403+429 映射）
- P0/P1/P2 方法抛 `NotImplementedError("Week 2 待实现")`
- 27 条契约测试覆盖元信息 / 包络适配 / stub 行为 / CLI 集成

#### Research 闭环
- Issue #90 关闭（拉勾 + 智联 + 猎聘 API 调研全部归档到 `docs/research/platforms/`）

### Tests & Quality
- 测试 927 → **998**（+71 新增：25 platform ABC + 27 zhilian + 10 CLI + 5 context manager + 4 contract）
- mypy 严格模块 66 → **71**（新增 4 个 platforms 子模块 + `commands._platform`）
- mypy `--strict` 0 错误 / 80 源文件全覆盖
- 7 PR 连合（#131/#132/#133/#134/#135/#136/#137），全部 CI 7/7 绿

### Breaking Changes
**无**。命令行为、信封格式、错误码枚举、CLI 接口全部向后兼容。Platform 抽象为纯重构，新增能力均在基类默认 `NotImplementedError` 形式下兼容老代码。

## [1.9.1] - 2026-04-20

### Changed
- **🎯 mypy 严格模式全量接入达成 100%**（#124）— 本轮收尾 4 个 patchright / aiohttp 外部依赖深水区（`api/browser_client` / `auth/browser` / `bridge/client` / `bridge/daemon`），白名单 61 → **66（业务代码 100%）**
- ROADMAP v2.0「架构演进」分区 2/3 完成（mypy 严格化 ✅、类型 stubs 导出 ✅）
- 所有业务模块强制 `disallow_untyped_defs + disallow_any_generics + warn_return_any`

### 里程碑
从 R1（#86）到 R14（#124），14 轮连续推进，严格模块 0 → 66。CI `typecheck` job 阻塞式，新代码零容忍。

## [1.9.0] - 2026-04-20

### Summary
1.8.x 系列连发 19 个 patch 完成以下核心里程碑，按 SemVer 规范 bump 到 1.9.0 标记这个里程碑节点。

### 🎯 核心里程碑（从 1.8.0 到 1.9.0）

#### 严格类型体系（从 0 到 81%）
- mypy 严格类型检查模块：3 → **61** 个（81% 覆盖率）
- CLI 命令层 **32/32 = 100%**
- api / auth / cache 核心基础层全部进入严格保护
- `typecheck` CI 从 non-blocking 升级为阻塞式门禁
- Python 嵌入 API：canonical `__all__` 导出 14 个核心符号 + 16 条契约测试 + `py.typed` marker

#### 智能能力扩展
- `boss ai interview-prep` — 基于 JD 生成模拟面试题
- `boss ai chat-coach` — 基于聊天记录给出沟通技巧建议
- AI Provider 扩至 **8 家**：OpenAI / DeepSeek / Moonshot / OpenRouter / Qwen / Zhipu / SiliconFlow / Custom
- 支持 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 / GLM-4.6 等最新模型

#### 数据输出
- `boss digest --format md` — 邮件/飞书可直接发送的 Markdown 日报
- `boss stats --format html` — 自包含交互式漏斗报表

#### Agent 集成
- Cursor / Windsurf 专用接入示例（MCP 推荐 + 规则文件兜底）
- `boss schema --format openai-tools / anthropic-tools` 直出 SDK 可用格式
- `docs/integrations/ai-models.md` 推荐模型配置表

#### 工程卫生
- 测试覆盖率 80% → **85%**
- 测试数 802 → **927**
- `CONTRIBUTING.en.md` 英文贡献者指南
- research Issue 模式建立：#90（多平台适配器）+ #96（Bridge gRPC）

### 🗂️ ROADMAP 进度对齐
- v1.8.x 数据可视化分区：**3/3 ✅**
- v1.8.x 智能能力分区：**3/3 ✅**
- v1.8.x Agent 集成分区：**2/3**（剩 MCP HTTP Streaming，Issue #48 外部认领）
- v2.0 架构演进：类型 stubs 导出 ✅，mypy 严格化 81%（进行中），Bridge gRPC 调研中

## [1.8.18] - 2026-04-20

### Changed
- **严格类型检查覆盖 api/client 核心请求客户端**（#119）— 37 error → 0，白名单 60 → 61
- 用 `TYPE_CHECKING` 避免 BrowserSession / AuthManager 循环导入
- `_request` / `_browser_request` 返回值用 `cast` 修 no-any-return
- `__exit__` 精确 `TracebackType` 签名
- 连锁修复 `commands/exchange.py` 类型传播问题

### 里程碑
- api/ 层覆盖达 6/7（剩 api/browser_client，patchright 外部依赖最重）
- 严格模块累计 61 / 75（81%）

## [1.8.17] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 60 个模块**（#117）— 新增 `auth/manager` / `auth/qr_login` / `commands/chat_summary`
- auth/ 层严格覆盖达到 4/5（剩 `auth/browser` 外部依赖）
- 核心认证状态机（AuthManager）进入严格保护

## [1.8.16] - 2026-04-20

### Changed
- **严格类型检查覆盖 cache/store 模块**（#115）— 白名单 56 → 57，SQLite 存储层首次严格化
- `__enter__` / `__exit__` 补精确 `TracebackType` 类型签名
- `get_search` 返回值用 `cast("str", ...)` 修复 `no-any-return`
- 所有 SQL 参数/返回 dict 补泛型 `dict[str, Any]`

### Progress
- cache/ + api/ 两个基础层全部严格化完成
- 剩余 6 个硬骨头（外部依赖 playwright / aiohttp）：api/client / api/browser_client / auth/manager / auth/browser / bridge/daemon / bridge/client

## [1.8.15] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 56 个模块**（#113）— 新增 `api/models` / `api/throttle` / `api/endpoints` / `api/endpoints_loader` / `commands/stats`
- 所有 api/* 基础模块首次全部进入严格白名单
- SQL 聚合查询结果（`_safe_count` / `_count_since`）用 typed 中间变量避免 `no-any-return`

## [1.8.14] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 51 个模块**（#111）— 新增 8 个非 CLI 模块：`ai/config` / `ai/service` / `ai/prompts` / `resume/templates` / `resume/export` / `auth/token_store` / `auth/cookie_extract` / `main`
- `token_store.load()` / `ai/service.chat()` 使用 `cast()` 精确声明 JSON 返回类型
- `main.cli` 重命名局部变量避免 `str → Path` 类型冲突
- `ai/config.get_base_url` 显式 `str()` 包装解决 `warn_return_any`

## [1.8.13] - 2026-04-20

### Changed
- **CLI 命令层严格类型覆盖首次达 100% (32/32)**（#109）— 新增 `ai_cmd` / `resume_cmd` 两个大模块，白名单 41 → 43
- `cache/store.CacheStore.close` 补 `-> None` 类型注解（解锁 resume_cmd 对其的调用）
- `ai_cmd._call_ai` 使用 cast 精确声明 JSON 解析结果类型
- 22 个 AI + Resume 子命令签名升级为完整类型化

### 里程碑
- 从 R3 `handle_auth_errors` 装饰器解锁开始，6 轮推进（#99→#109）
- 每轮都不堆积模块，而是修系统级障碍带一批模块严格化

## [1.8.12] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 41 个模块**（#107）— 新增 `schema` / `chat_export` / `config_cmd`
- 本轮单日三连发（#103 → #105 → #107），严格类型模块从 24 → 41，**净增 17 个**

## [1.8.11] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 38 个模块**（#105）— 新增核心 CLI 命令 6 个：`search` / `greet` / `chat` / `detail` / `doctor` / `export`
- CLI 命令层严格覆盖率从 66% (21/32) 提升至 **84% (27/32)**
- `detail._detail_via_httpx` / `_detail_via_browser` 补全 BossClient / Path 类型
- `doctor.add_check` 的 `hint` 参数支持 `str | None`

## [1.8.10] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 32 个模块**（#103）— 新增 8 个 CLI 命令：`me` / `show` / `mark` / `clean` / `shortlist` / `chat_snapshot` / `preset` / `watch`
- CLI 命令层严格覆盖率从 40% (13/32) 提升至 **66% (21/32)**
- `preset` / `watch` 的参数构造器给 10 个参数逐个精确标注 `str | None`
- `chat_snapshot` 使用 `boss_agent_cli.output.Logger` 替代裸 `logger` 参数

## [1.8.9] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 24 个模块**（#101）— 本轮新增 CLI 命令层 7 个：`apply` / `chatmsg` / `digest` / `exchange` / `interviews` / `pipeline` / `status`
- 所有 7 个命令签名升级为 `def cmd(ctx: click.Context, ...) -> None`
- `pipeline._collect_pipeline_items` 返回类型精确标注为 `list[dict[str, Any]]`

## [1.8.8] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 17 个模块**（#99）— 首次覆盖 CLI 命令层：`commands/cities` / `commands/chat_utils` / `commands/history` / `commands/login` / `commands/logout` / `commands/recommend`
- `display.handle_auth_errors` 装饰器补全类型注解，解锁下游 11+ 个命令的严格化路径
- 所有目标命令的 `def cmd(ctx, ...)` 签名升级为 `def cmd(ctx: click.Context, ...) -> None`

## [1.8.7] - 2026-04-20

### Changed
- **严格类型检查白名单扩至 11 个模块**（#97）— 新增 `chat_summary` / `search_filters` / `resume/models` / `resume/store`，所有这些模块现在强制 `disallow_untyped_defs` + `disallow_any_generics` + `warn_return_any`
- 所有白名单模块的裸 `dict` / `list` 补上泛型参数
- `search_filters._check_details_parallel` 修正 `welfare_conditions` 参数类型为 `list[tuple[str, list[str]]]`

### Added
- Issue #96「Bridge 协议 HTTP/WS → gRPC 升级调研」— 按多平台适配器（#90）的调研先行模式，为 v2.0 架构演进剩余一项锁定调研清单

## [1.8.6] - 2026-04-20

### Added
- **包级公开接口**（#94）— `boss_agent_cli.__all__` 导出 14 个核心符号（AuthManager / BossClient / CacheStore / JobItem / JobDetail / AIService / ResumeData 等），下游项目可直接 `from boss_agent_cli import X` 使用，不再需要深路径 import
- `tests/test_public_api.py` 16 条契约测试守护 public API（identity 一致性 / 异常继承 / SemVer 版本格式 / py.typed marker 存在）
- `README.md` 新增「方式三：Python 直接嵌入」章节，给出 canonical 使用示例

### Changed
- `src/boss_agent_cli/__init__.py` 补完整 docstring 说明包的公开 API 契约

## [1.8.5] - 2026-04-20

### Added
- `research` issue label + Issue #90「多平台适配器 API 调研：拉勾 / 智联 / 猎聘」— 对齐顶层设计：API 调研优先、实现 PR 必须基于调研报告
- ROADMAP「生态扩展」条目补入调研 issue 引用（#91）

### Changed
- 严格类型检查白名单从 **3 个扩到 7 个**（#92）— 新增 `digest` / `match_score` / `pipeline_state` / `index_cache` 四个 100% 覆盖率纯函数模块
- 启用的严格选项：`disallow_untyped_defs` + `disallow_any_generics` + `warn_return_any`
- 所有白名单模块的裸 `dict` / `list` 均补上泛型参数

## [1.8.4] - 2026-04-20

### Changed
- **类型检查门禁升级为阻塞式**（#88）— mypy 12 baseline errors 全部清零，CI `typecheck` job 去掉 `continue-on-error`，新代码必须零 mypy 错误才能合入 master

### Fixed
- `api/browser_client.py` × 8：patchright 类属性加 `Any` 类型注解
- `auth/manager.py`：登录方法 `token` 变量显式声明为 `dict | None`
- `bridge/daemon.py`：Popen `kwargs` 显式声明为 `dict[str, Any]`
- `commands/chat_utils.py`：`RELATION_LABELS` key 类型放宽为 `object`
- `resume/models.py`：for 循环变量 `item` 改名 `ji_item` 避免类型混淆

## [1.8.3] - 2026-04-20

### Added
- 英文版贡献者指南 `CONTRIBUTING.en.md`（#84） — 对齐中文版全量章节，并明确说明 commit message 纯中文描述约定
- 静态类型检查接入（#86）— `mypy` 依赖 + 宽松基线配置 + `typecheck` CI 非阻塞 job；`output` / `config` / `hooks` 三个纯工具模块启用严格模式（`disallow_untyped_defs`）
- `docs/integrations/ai-models.md` 作为 ROADMAP v2.0 社区建设的英文贡献者入口被纳入索引

### Changed
- `CONTRIBUTING.md` 首行补 `CONTRIBUTING.en.md` 链接
- `CONTRIBUTING` 双语版增加 mypy 本地跑法提示
- `output.py` / `hooks.py` 补齐类型注解（`emit_success` / `emit_error` / `Logger.*` / `SyncHook.__init__` / `BailHook.__init__`）

### 测试覆盖率二次冲刺（#85）
- `commands/logout.py` 86% → 100%
- `commands/show.py` 85% → ~98%
- `commands/mark.py` 86% → ~95%
- `commands/me.py` 88% → 100%
- `match_score.py` 93% → 100%
- `pipeline_state.py` 93% → 100%
- 全量测试 **893 → 911**（+18）

### Fixed
- （无 bug 修复）

## [1.8.2] - 2026-04-20

### Added
- AI Provider 扩展 4 家，覆盖主流国内外聚合入口：
  - `openrouter` — Anthropic Claude 4.7 / OpenAI GPT-5 / Google Gemini 等全家桶聚合
  - `qwen` — 通义千问 DashScope OpenAI 兼容入口
  - `zhipu` — 智谱 GLM-4.6 开放平台
  - `siliconflow` — 硅基流动聚合推理
- 新建 `docs/integrations/ai-models.md` 推荐模型与入口表，给 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 / GLM-4.6 等最新模型最短接入路径

### Changed
- `ai config` 命令 `--provider` / `--model` 帮助文案同步最新 provider 列表
- `docs/agent-hosts.md` 索引补入 AI 模型入口文档链接
- `README.md` AI 命令表下方加推荐模型引用

### Fixed
- （无 bug 修复）

### 测试覆盖率冲刺（独立主题）
- `commands/display.py` 30% → **100%**（+21 测试）
- `commands/status.py` 73% → **95%**（+2 测试）
- `commands/ai_cmd.py` 77% → **84%**（+30 测试，剩余是 ctx.exit 后的防御性死代码不做硬刷）
- `tests/test_ai_config.py` 新增 4 条 provider base_url 断言
- 整体测试 **835 → 893**（+58），覆盖率 **85% → 88%**

## [1.8.1] - 2026-04-19

### Added
- `boss digest --format md [-o <path>]` — 每日摘要 Markdown 输出，可直接贴邮件/飞书发送；核心指标表 + 新匹配 / 待跟进 / 面试三段落，空数据写「暂无」占位
- `docs/integrations/cursor.md` — Cursor Composer Agent 接入示例（MCP 推荐 + `.cursor/rules` 兜底）
- `docs/integrations/windsurf.md` — Windsurf Cascade Agent 接入示例（MCP 推荐 + `.windsurfrules` 兜底）
- `docs/agent-hosts.md` 宿主索引表补入 Cursor / Windsurf 两条
- `.gitignore` 精准忽略本地专属的社区发帖草稿（`docs/blog/linuxdo-promo.md`）
- 协议服务 `boss_digest` 工具新增 `format` / `output` 参数
- `tests/test_digest_command.py` 新增 5 条 md 输出路径测试、`tests/test_mcp_server.py` 新增 2 条 `_build_args` 测试（828 → 835，+7）

### Changed
- ROADMAP v1.8.x「数据可视化」和「Agent 集成」分区各勾选一项完成
- `digest` 命令描述同步更新说明支持 JSON / Markdown 两种输出
- `tests/test_agent_host_examples.py` meta 测试覆盖新增两份集成示例

### Fixed
- （无 bug 修复）

## [1.8.0] - 2026-04-19

### Added
- `boss ai interview-prep <jd_text>` — 基于目标职位生成模拟面试题与准备建议，支持 `--resume` 参考简历、`--count` 控制题量；返回分类（技术/行为/情景）、参考回答框架、考察点、难度、高优先级准备项
- `boss ai chat-coach <chat_text>` — 基于聊天记录诊断沟通状态并给出下一步建议，支持 `--resume`、`--style` 偏好；输出阶段判断、招聘者意图、优劣势、可直发消息模板、需避免的雷区
- 协议服务新增 `boss_ai_interview_prep` / `boss_ai_chat_coach` 两个工具（41 → 43）
- Prompt 模板新增 `INTERVIEW_PREP_PROMPT` / `CHAT_COACH_PROMPT`

### Changed
- ROADMAP v1.8.x 智能能力分区两项勾选完成
- README / capability-matrix / SKILL 同步新增两条 AI 能力
- schema 中 `ai` 命令子命令列表由 6 → 8

### Fixed
- （无 bug 修复）

## [1.7.2] - 2026-04-19

### Added
- `docs/integrations/python-sdk.md` — Python SDK 直调集成样例（OpenAI + Anthropic 两套 ~150 行可运行代码），Agent 宿主索引表同步补入口
- README 中英文版均链接认可 LINUX DO 社区

### Changed
- 测试覆盖率大幅提升：总体 **80% → 84%**（+4 点）
  - `api/client.py` 63% → **97%**（+37 测试）
  - `commands/greet.py` 65% → **97%**（+8 测试）
  - `commands/export.py` 47% → **98%**（+13 测试）
  - `auth/manager.py` 74% → **96%**（+14 测试）
  - `auth/token_store.py` 81% → **100%**（+14 测试）
  - `commands/login.py` 83% → **100%**（+6 测试）
- 总测试数 **710 → 802**（+92）

### Fixed
- 移除误跟踪的 `.coverage` artifact 并扩展 `.gitignore` 忽略覆盖率文件
- Dependabot 批量升级 5 个 GitHub Actions 版本（`checkout`/`upload-pages-artifact`/`configure-pages`/`deploy-pages`/`gh-release`）

## [1.7.1] - 2026-04-17

### Added
- `boss schema --format openai-tools` 和 `--format anthropic-tools` — 一键导出 OpenAI Functions / Claude Tool Use 兼容的 JSON Schema，免手动转换即可喂给 SDK
- `boss stats --format html -o <path>` 输出自包含交互式漏斗报表（纯 CSS + SVG，无外部 CDN）
- 协议服务文档补齐传输层章节（stdio 当前支持 / SSE 规划中）和贡献指引
- Issue / PR 模板全面升级：bug_report 强制 doctor 输出和版本号，feature_request 新增贡献意愿字段，新增 documentation 专用模板，PR 模板新增 Closes 关联和 Breaking Change 声明
- 英文版说明补齐 Troubleshooting 章节（诊断清单 / 登录 / 浏览器 / 搜索 / 错误码 / 术语表），对齐中文版
- Codecov 覆盖率追踪接入，基线 80%，每次 PR 自动上报
- 开发容器（`.devcontainer/devcontainer.json`）支持 GitHub Codespaces 一键启动
- `ROADMAP.md` + 4 个 Issue（含 2 个 good-first-issue）招募外部贡献者
- 本地提交质量门禁 `.pre-commit-config.yaml`（ruff + 通用 hooks）

### Changed
- CI 支持 `workflow_dispatch` 手动触发
- 协议服务 `awesome-mcp-servers` 投稿材料和多语言投稿模板整理

### Fixed
- 清理 `tests/test_qr_login.py` 未使用 import，修复 ruff lint 失败

## [1.7.0] - 2026-04-17

### Added
- 新增 `boss ai reply` 命令 — 基于招聘者消息生成 2-3 条回复草稿，支持简历参考和语气偏好
- 新增 `boss stats` 命令 — 投递转化漏斗统计，只读聚合打招呼/投递/候选池/监控数据
- 协议服务扩展：新增 18 个工具覆盖简历管理、智能能力、状态管理增删（23→41）
- 元测试：main.py 注册命令与 SCHEMA_DATA 对齐的防漂移校验
- 本地提交质量门禁：新增 `.pre-commit-config.yaml`（ruff check + 通用 hooks）
- 新增英文版 README（`README.en.md`），README 首屏加入语言切换
- 能力矩阵文档补齐简历管理、智能能力、数据洞察三大分区
- README 加入 CHANGELOG 导航入口

### Changed
- 能力矩阵命令总数对齐到当前状态

### Fixed
- 清理 `tests/test_qr_login.py` 未使用 import，修复 ruff lint 失败

## [1.6.0] - 2026-04-14

### Added
- 新增 `resume` 命令组 — 本地简历管理，支持初始化、列表、查看、编辑、删除、导出、导入、克隆、版本比对
- 新增 `ai` 命令组 — 智能简历优化，支持配置、JD 分析、润色、优化、建议五个子命令，覆盖 OpenAI/Claude/Gemini/通义千问/DeepSeek 多模型
- 简历数据模型、本地存储、模板渲染、多格式导出（HTML/Markdown/PDF/DOCX）
- AI 服务模块：多模型配置、密钥加密存储、提示词模板、对话补全
- 模型上下文协议服务从十一个工具扩展至二十三个，覆盖全部命令

### Changed
- 协议服务文档按功能分类并补齐全部工具说明
- 能力矩阵补齐配置管理和缓存清理命令

## [1.5.0] - 2026-04-14

### Added
- 新增 `clean` 命令 — 清理过期缓存和临时文件，支持预览和全量模式
- 模型上下文协议服务新增二十九个测试覆盖工具定义和参数构建

### Changed
- 收窄八处网络和解析模块的异常捕获为具体类型
- 统一调试协议默认地址为单一常量引用

## [1.4.0] - 2026-04-14

### Added
- 新增 `config` 命令组 — 查看、设置、重置配置项，支持类型自动推断
- 新增类型标记文件，下游项目可获得类型检查支持
- 新增版本查询选项，终端输入即可查看当前版本
- 缓存模块新增保存搜索、增量监控、投递记录、候选池四表扩展测试
- 浏览器桥接模块新增协议结构和客户端重试逻辑测试
- 测试数量从三百六十八增至四百二十七

### Changed
- 安装指引统一覆盖三种安装方式
- 能力矩阵文档按功能分类并补齐全部命令
- 清理仓库内部开发计划文档

## [1.3.0] - 2026-04-13

### Added
- 新增 `watch` 命令组 — 保存搜索条件并执行增量监控，自动标出新职位
- 新增 `pipeline` 命令 — 汇总沟通和面试数据，构建求职流水线全景视图
- 新增 `follow-up` 命令 — 筛选超时未推进的联系人，生成跟进提醒
- 新增 `apply` 命令 — 发起投递/立即沟通动作，幂等设计防止重复投递
- 新增 `shortlist` 命令组 — 管理职位候选池，支持添加/列表/移除
- 新增 `chat-summary` 命令 — 对沟通消息生成结构化摘要
- 新增 `preset` 命令组 — 管理可复用搜索预设，保存常用参数组合一键复用
- 新增 `digest` 命令 — 每日摘要，综合流水线、跟进、统计信息
- 搜索结果新增匹配分和匹配原因输出
- 快速入门文档和冒烟测试框架
- 多宿主集成示例文档（Claude Code / Codex / Shell Agent）
- 接口合约和错误码一致性测试
- 高风险链路测试覆盖补齐

### Changed
- 开源仓库元信息优化，补充英文摘要和变更记录

### Fixed
- 检测风控状态码并输出明确错误标识（此前静默失败）
- 调试协议模式复用用户上下文，规避自动化检测
- 扩展优先复用已有招聘页而非空白自动化页
- 职位详情快速通道失败时自动降级到浏览器通道（此前误报"职位不存在"）
- 搜索分页边界条件修正

## [1.2.0] - 2026-04-09

### Added
- CI 新增 ruff lint 质量门禁步骤
- CI 矩阵新增 Python 3.13 支持
- 新增 bridge/display/endpoints_loader/index_cache 四模块测试（123→182 用例）
- SKILL.md 命令速查表补全至 19 个命令

### Changed
- chat.py 拆分为 chat_export/chat_snapshot/chat_utils 三子模块（655→227 行）
- 浏览器超时从裸数字提取为命名常量
- search_filters 异常捕获从 Exception 收窄为具体类型
- client.py 根据运行平台动态设置请求头
- daemon.py 文件句柄改为 with 语句防泄漏
- 安装命令改为从 GitHub 源码安装

### Fixed
- CLAUDE.md 缩进规范、模块索引、技术栈、架构图与代码对齐
- README 配置文档补全 cdp_url/export_dir 字段
- .gitignore 排除 .trellis/.agents 目录

## [1.1.0] - 2026-04-03

### Added
- 新增 `boss me` 命令 — 获取当前登录用户的基本信息、简历、求职期望、投递记录
- 跨平台 Agent Skill 体系 — 支持 Codex / Claude Code / Gemini CLI / OpenCode / OpenClaw
- `.agents/skills/` symlink 供 Codex / OpenCode 发现 skill
- pyproject.toml 补全 authors、keywords、classifiers、urls 元数据

### Fixed
- 修复 `boss me` 命令 AuthManager 路径拼接和 emit_error 参数问题
- 消息模板标准化 — hints 补全 + 参数引用修正 + recovery_action 统一

### Changed
- SKILL.md 重构为 AgentSkills 标准格式
- skill 目录从 `skills/SKILL.md` 迁移到 `skills/boss-agent-cli/SKILL.md`

## [0.1.0] - 2026-03-20

### Added
- 核心 CLI 框架（Click）+ JSON 信封输出协议
- `boss login` — patchright 反检测浏览器扫码登录 + 本地浏览器 Cookie 自动提取
- `boss status` — 检查登录态
- `boss search` — 职位搜索（支持城市 / 薪资 / 经验 / 学历 / 规模筛选）
- `boss search --welfare` — 福利精准筛选（双休、五险一金等，逗号分隔 AND 逻辑，自动翻页）
- `boss recommend` — 基于简历的个性化职位推荐
- `boss detail` — 职位完整详情（描述、地址、招聘者信息）
- `boss greet` — 向招聘者打招呼
- `boss batch-greet` — 批量打招呼（上限 10，支持 dry-run 预览）
- `boss export` — 导出搜索结果为 CSV / JSON
- `boss cities` — 列出 40 个支持城市
- `boss schema` — 工具能力自描述 JSON（Agent 调用入口）
- Token 加密存储（Fernet + PBKDF2 机器绑定密钥）
- SQLite WAL 缓存（搜索历史 100 条上限 + 已打招呼记录）
- 高斯分布请求延迟 + 指数退避 403 重试
- GitHub Actions CI（多 OS + 多 Python 版本）
