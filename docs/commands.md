# 命令参考

> 能力真源是 `boss schema`（机器可读的完整自描述：命令、参数、平台支持与错误码）。
> 本页是面向人类的速查表；当两者不一致时，以 `boss schema` 实际输出为准。
> 英文版见 [commands.en.md](commands.en.md)。

```bash
boss schema                            # 完整能力 JSON（Agent 首先调用）
boss schema --format openai-tools      # 导出 OpenAI Functions / Tools 定义
boss schema --format anthropic-tools   # 导出 Claude Tool Use 定义
boss <命令> --help                      # 查看单个命令选项
```

运行模式：`boss config set operating_mode assisted|research`。默认 `assisted`；切换后重新执行 `boss schema` 查看逐命令模式、风险和数据分类。

## 基础操作

| 命令 | 说明 |
|------|------|
| `boss schema` | 输出完整工具能力描述 JSON（37 个顶层命令 + hr 分组展开，Agent 首先调用） |
| `boss platforms` | 本地平台注册与能力状态（不触网；支持 `--platform` 单平台过滤与 `--capability` 反查，附 `capability_status_legend`） |
| `boss login` | 用户主动登录（按平台走 Cookie / CDP / QR / 浏览器降级链路） |
| `boss logout` | 退出登录 |
| `boss status` | 检查登录态（默认仅本地；`--live` 才执行低频只读验证） |
| `boss doctor` | 诊断环境、依赖、凭据完整性和网络；默认仅本地诊断，`--live-probe` 才执行低频只读探测；敏感操作或命中风控时提示回到官方页面手动完成 |
| `boss me` | 我的信息（用户/简历/期望/投递记录） |

## 职位搜索

| 命令 | 说明 |
|------|------|
| `boss search <query>` | 搜索职位（支持 `--url` 网页筛选、逗号多选、`--welfare` 筛选、`--sort score` 本地排序、`--preset` 预设） |
| `boss recommend` | 受限：默认低风险模式阻断，避免自动读取推荐流 |
| `boss detail <security_id>` | 职位详情（`--job-id` 走快速通道） |
| `boss show <#>` | 按编号查看上次搜索结果 |
| `boss cities` | 40 个支持城市 |

## 显式批量采集

`crawl` 是用户显式触发的受限 Research Mode Chrome 任务。`run`、`start` 和 `resume` 必须带共享 `operating_mode=research`；MCP 仍保持 assisted-only，只暴露已有任务的 `status/results/shortlist` 本地读取或导入接口。首次使用需安装 `uv sync --extra crawl`；采集器只启动 `<data-dir>/crawl/chrome-profile` 这个独立 profile，不接管日常 Chrome。

```powershell
boss crawl configure --max-requests 20 --max-details 50 --max-seconds 600 --max-retries 1
boss crawl run "AI" --city 杭州 --pages 3 --with-detail `
  --hook-profile screenshot-full --hook-dir E:\boss-agent-cli-local-hooks\AntiDebug_Breaker
boss crawl resume <run_id>
boss crawl stop <run_id>
```

| 命令 | 说明 |
|------|------|
| `boss crawl configure [--chrome-path PATH] [--port N] [--max-* N]` | 设置 crawl 专用 Chrome 和请求、详情、墙钟、重试预算；profile 固定为 `<data-dir>/crawl/chrome-profile` |
| `boss crawl run <query> --city <城市或代码> [--pages N] [--with-detail]` | 串行采集；`--pages` 默认 `5` 且必须为正数；`--with-detail` 串行补全职位详情 |
| `boss crawl start <query> --city <城市或代码> [...]` | 创建后台任务并立即返回 `run_id`；供本地任务调度使用 |
| `boss crawl status <run_id>` / `boss crawl results <run_id>` | 仅读取 SQLite 中的页游标、风险状态、详情进度和已持久化职位；不打开浏览器 |
| `boss crawl resume <run_id> [--pages N] [--with-detail] [--background]` | 从页游标、已见职位和待补详情队列恢复；`--background` 立即返回以便轮询；可提高正数页数上限并补全详情，不重复写入已完成项 |
| `boss crawl stop <run_id>` | 请求运行中任务在下一个安全点停止并保留 checkpoint |
| `boss crawl shortlist <run_id> (--all \| --selector <csel_...>)` | 将 crawl 结果导入原项目的本地候选池；不请求平台，保留内部职位关联和详情缓存供 `boss ai fit` 使用 |

默认 Hook 为 `none`。`screenshot-full` 仅在用户明确选择 `--hook-profile screenshot-full --hook-dir <目录>` 时启用；目录必须由用户拥有相应授权，并提供原始 7 个脚本及 `SHA256SUMS`。项目不再发布这些第三方脚本，运行前逐文件校验 SHA-256 并记录脚本标识与摘要；不记录 Cookie、请求头或完整请求体。

候选人侧可用 `boss agent crawl --run-id <run_id> --resume <简历名>` 执行“完成的 crawl → shortlist → ai fit → 按匹配分排序”。它不会启动浏览器；只有 `boss agent crawl --query <关键词> --city <城市> --allow-crawl --resume <简历名>` 才会启动新的真实采集。遇到 `risk_stopped` 或 `budget_stopped` 时 Agent 只返回 `run_id` 和恢复命令，不会无限重试或重开会话。

每页完成后更新 `<data-dir>/crawl/runs/<run_id>/jobs.json`、`jobs.csv` 和带筛选/冻结首行的 `jobs.xlsx`。XLSX 保留完整值但所有数据行固定为单行和统一行高，长内容仅在表格中截断显示。JSON/CSV/XLSX 和 `crawl results` 默认不包含 `security_id`、职位 ID、selector、招聘者姓名或职位；这些仅保留在受限本地 SQLite 状态，`boss clean --privacy` 会删除 crawl 运行、预算和导出。风险码 `37` / `38`、安全页、职位列表容器异常、预算耗尽或 stop 请求都会保存断点并停止；stdout 始终只输出 JSON 信封和恢复命令。

## 求职动作

| 命令 | 说明 |
|------|------|
| `boss greet <sid> <jid>` | 受限：默认低风险模式阻断，打招呼请回到平台官网手动完成 |
| `boss batch-greet <query>` | 受限：默认低风险模式阻断，避免批量触达 |
| `boss apply <sid> <jid>` | 受限：默认低风险模式阻断，投递请回到平台官网手动完成 |
| `boss exchange <sid>` | 受限：默认低风险模式阻断，联系方式交换涉及个人信息 |

## 沟通跟进

| 命令 | 说明 |
|------|------|
| `boss chat` | 受限：默认低风险模式阻断，涉及会话数据 |
| `boss chatmsg <sid> [--raw]` | 受限：默认低风险模式阻断；`--raw` 仅在合规放行后保留结构化 body、链接和职位卡片字段 |
| `boss chat-summary <sid>` | 受限：默认低风险模式阻断，依赖通信内容 |
| `boss mark <sid> --label X` | 受限：默认低风险模式阻断，涉及平台关系写入 |
| `boss interviews` | 面试邀请 |
| `boss history` | 浏览历史 |

## 流水线监控

| 命令 | 说明 |
|------|------|
| `boss pipeline` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss follow-up` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss digest` | 受限：默认低风险模式阻断，依赖会话/面试数据 |
| `boss watch add/list/remove/run` | add/list/remove 为本地预设；run 默认阻断，避免自动增量拉取平台数据 |
| `boss shortlist add/list/annotate/compare/remove` | 本地候选池：支持标签、备注和离线对比 |
| `boss preset add/list/remove` | 搜索预设 |

## 招聘者模式

| 命令 | 说明 |
|------|------|
| `boss hr applications` | 受限：默认低风险模式阻断，涉及候选人投递申请 |
| `boss hr resume <geek_id> --selector <csel_...> --security-id <id>` | 受限：默认低风险模式阻断，涉及候选人在线简历 |
| `boss hr resume --exchange --friend-id <friend_id> [--type wechat]` | 受限：默认低风险模式阻断，涉及联系方式交换 |
| `boss hr chat` | 受限：默认低风险模式阻断，涉及候选人沟通列表 |
| `boss hr chatmsg <friend_id>` | 受限：默认低风险模式阻断，涉及候选人聊天记录 |
| `boss hr last-messages [--friend-id <id>]` | 受限：默认低风险模式阻断，涉及候选人消息摘要 |
| `boss hr jobs list/offline/online` | 职位列表与上下线管理 |
| `boss hr candidates <keyword>` | 受限：默认低风险模式阻断，涉及候选人搜索 |
| `boss hr reply <friend_id> <message>` | 受限：默认低风险模式阻断，回复请回到平台官网手动完成 |
| `boss hr request-resume <friend_id>` | 受限：默认低风险模式阻断，附件简历请求请回到平台官网手动完成 |

## 简历与 AI

| 命令 | 说明 |
|------|------|
| `boss resume init/list/show/edit/delete/export/import/clone/diff/link/applications` | 本地简历管理 |
| `boss ai config` | 配置 AI 服务 |
| `boss ai local status` | 查看本地模型配置、推荐模型和导入登记 |
| `boss ai local configure --runtime ollama --model qwen3:14b` | 配置本地 Ollama OpenAI 兼容服务 |
| `boss ai local pull --model qwen3:14b --confirm-download` | 显式下载本地模型权重 |
| `boss ai local smoke` | 调用本地模型做一次健康检查 |
| `boss ai analyze-jd` | 分析岗位要求 |
| `boss ai polish` | 润色简历 |
| `boss ai optimize` | 针对目标岗位优化 |
| `boss ai suggest` | 求职建议 |
| `boss ai reply` | 生成招聘者消息回复草稿 |
| `boss ai interview-prep` | 基于 JD 生成模拟面试题 |
| `boss ai chat-coach` | 基于聊天记录给沟通建议 |
| `boss ai cover-letter` | 基于本地简历与目标岗位起草求职信/自我介绍（仅草稿，不发送） |

> 支持 Claude 4.7 / GPT-5 / DeepSeek-V3 / Qwen3 等最新模型，详见 [推荐模型与入口](integrations/ai-models.md)。

## 系统管理

| 命令 | 说明 |
|------|------|
| `boss config list/set/reset` | 配置管理 |
| `boss clean` | 清理缓存 |
| `boss stats` | 投递转化漏斗统计（greeted/applied/shortlist） |
| `boss export <query>` | 导出结果（CSV/JSON/HTML，支持 `--url` 网页筛选） |

## 搜索筛选参数详解

```bash
boss search "golang" \
  --city 广州 \             # 城市（40 个可选）
  --salary 20-50K \         # 薪资范围
  --experience 3-5年,5-10年 \ # 经验要求（支持逗号多选）
  --education 本科,硕士 \    # 学历要求（支持逗号多选）
  --scale 100-499人 \       # 公司规模
  --industry 互联网 \       # 行业
  --stage 已上市 \          # 融资阶段
  --welfare "双休,五险一金" \ # 福利筛选（AND 逻辑）
  --sort score              # 按本地 match_score 降序
```

也可以先在 BOSS 直聘网页上手动选好筛选条件，再复制搜索页 URL 给 CLI：

```bash
boss search --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100&experience=104,105'
boss export --url 'https://www.zhipin.com/web/geek/jobs?query=Golang&city=101280100' --count 50 -o jobs.csv
```

**福利筛选工作原理**：

1. 先检查职位福利标签（`welfareList`）
2. 标签不匹配时自动获取职位描述全文搜索
3. 自动翻页（最多 5 页）
4. 每个结果带 `welfare_match` 说明匹配来源，并带 `match_score` 供 `--sort score` 本地排序

支持关键词：`双休` `五险一金` `年终奖` `餐补` `住房补贴` `定期体检` `股票期权` `加班补助` `带薪年假`
