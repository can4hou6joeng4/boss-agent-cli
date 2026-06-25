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

## 基础操作

| 命令 | 说明 |
|------|------|
| `boss schema` | 输出完整工具能力描述 JSON（36 个顶层命令 + hr 分组展开，Agent 首先调用） |
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
| `boss hr resume <geek_id> --job-id <id> --security-id <id>` | 受限：默认低风险模式阻断，涉及候选人在线简历 |
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
