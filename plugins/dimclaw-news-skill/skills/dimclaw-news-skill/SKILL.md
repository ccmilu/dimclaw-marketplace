---
name: dimclaw-news-skill
description: "综合新闻聚合器，从16大来源实时获取、过滤和深度分析内容：Hacker News、GitHub Trending、Product Hunt、36氪、腾讯新闻、华尔街见闻、V2EX、微博、ArXiv、TechCrunch、The Verge、HuggingFace Papers、财联社、少数派。支持 AI 语义去重、事件追踪和编辑概览。"
---

# 新闻聚合技能

从多个来源获取实时热点新闻。执行任务前先获取当前日期：`date +%Y-%m-%d`

---

## 一、工具与配置

### 1.1 fetch_news.py（原有 8 来源）

所有来源均已内置多级回退，可直接使用。

**环境配置（必需）：** 必须使用本技能文件夹中的 .venv 虚拟环境运行。

```bash
./.venv/bin/python scripts/fetch_news.py --source <来源> --limit <数量>
```

**参数说明：**
- `--source`：`hackernews`、`weibo`、`github`、`36kr`、`producthunt`、`v2ex`、`tencent`、`wallstreetcn`、`all`
- `--limit`：每个来源的最大条目数（默认 10）
- `--keyword`：逗号分隔的过滤词

### 1.1b fetch_news_v2.py（新增 6 来源）

```bash
./.venv/bin/python scripts/fetch_news_v2.py --source <来源> --limit <数量>
```

**参数说明：**
- `--source`：`huggingface`、`arxiv`、`techcrunch`、`theverge`、`cls`（财联社）、`sspai`（少数派）、`all`
- `--limit`：每个来源的最大条目数（默认 10）
- `--keyword`：逗号分隔的过滤词
- 输出格式与 fetch_news.py 完全相同

**依赖说明：** 需要 `ZHIPU_API_KEY` 环境变量（智谱 Reader 正文提取 + The Verge/少数派兜底方案）

### 1.2 智谱 Reader（获取正文，按需使用）

子 Agent 使用智谱 Reader 批量提取文章正文（详见 `subagent_template.md`）。

### 1.3 cluster_preprocess.py（聚类预处理）

```bash
./.venv/bin/python scripts/cluster_preprocess.py <file1> <file2> ... -o /tmp/news_pre_clustered.json
```

由聚类去重 Agent 调用，做 URL + 标题机械预去重。

### 1.4 merge_news.py（合并子 Agent 输出）

```bash
./.venv/bin/python scripts/merge_news.py <file1> <file2> ... -o output.json \
  [--max-main 20] [--category-min 3] [--category-max 10] \
  [--title "标题"] [--signature "签名"] [--tagline "tagline"]
```

### 1.5 events_db.py（LanceDB 事件数据库）

事件向量数据库管理模块，提供事件的存储、检索和管理功能。

- 数据库路径：`data/events_vector_db/`
- 自动创建 embedding 向量用于语义检索

**环境变量（必需）：**
- `EMBEDDING_BASE_URL`：Embedding API 的 base URL
- `EMBEDDING_API_KEY`：Embedding API 的密钥
- `EMBEDDING_MODEL`：Embedding 模型名称
- `EMBEDDING_DIMENSIONS`：向量维度

### 1.6 search_events.py（事件检索）

```bash
./.venv/bin/python scripts/search_events.py \
  --query "事件描述" --category tech --days-back 7 --combined --limit 15
```

两阶段检索：向量语义检索 + 结构化字段过滤，返回候选历史事件列表。由事件关联 Agent 调用。

### 1.7 update_events_history.py（事件历史更新）

```bash
./.venv/bin/python scripts/update_events_history.py --update /tmp/events_history_update.json
```

将当天事件记录写入 LanceDB 向量数据库，自动清理过期事件。

### 1.8 news2html + send-email（生成报告）

```bash
# 1. JSON 转 HTML（支持今日概览、追踪徽章、跨领域关联、阅读建议）
./.venv/bin/python scripts/news2html <input.json> -o <output.html>

# 2. 发送邮件
./.venv/bin/python scripts/send-email --html --file <output.html> "email1,email2,email3" "标题"
```

---

## 二、如意如意模式12（全局扫描）

适用于 cronjob 定时任务：采集昨天到今早的新闻，生成报告并发送邮件。

### 2.1 cronjob 参数解析

从 message 中提取参数：

| 参数 | 来源 | 类型 | 默认值 |
|------|------|------|--------|
| `emails` | `- emails: ` 后行内容，逗号分隔 | 数组 | **必填** |
| `title` | `- title: ` 后行内容 | 字符串 | `"每日新闻简报"` |
| `signature` | `- signature: ` 后行内容 | 字符串 | `"—— 新闻聚合助手"` |
| `tagline` | `- tagline: ` 后行内容 | 字符串 | `"让信息为你服务"` |

| `max-main` | `- max-main: ` 后行内容 | 整数 | `20` |
| `category-min` | `- category-min: ` 后行内容 | 整数 | `3` |
| `category-max` | `- category-max: ` 后行内容 | 整数 | `10` |

**emails 格式**：逗号分隔，`recipient1@example.com,recipient2@example.com`，直接传给 send-email 的 `--html` 参数。

### 2.2 执行步骤

**行为约束（必须遵守）：**
- 必须调用子 agent，必须等待所有 agent 都执行结束后再继续
- 严令禁止启动子 agent 后以任何方式检查它们的状态，包括但不限于：禁止调用 sessions_list、sessions_history 查看子 agent 状态；禁止反复查看它们的任务进度；禁止任何形式的主动轮询或确认
- 子 agent 启动后，耐心等待系统通知你结果，不要采取任何行动
- 耗时不限，质量优先
- 不要自己上手抓取新闻，只启动子 agent 后坐着等待

**第一步：采集新闻**（输出格式：JSON **数组** `[{...}, {...}]`）

使用并行子 Agent 策略，6 组同时执行：

1. 海外科技组（Hacker News + Product Hunt）→ `/tmp/news_hn_ph.json`
2. 开发者组（GitHub + V2EX）→ `/tmp/news_github_v2ex.json`
3. 国内科技组（36kr + 腾讯）→ `/tmp/news_36kr_tencent.json`
4. 财经社会组（华尔街见闻 + 微博）→ `/tmp/news_wallstreetcn_weibo.json`
5. 国际媒体组（TechCrunch + The Verge）→ `/tmp/news_tc_verge.json`
6. 学术前沿组（ArXiv + HuggingFace Papers + 财联社 + 少数派）→ `/tmp/news_academic_extra.json`

**路径说明：** 下方 `{SKILL_DIR}` 指本 skill 的安装目录，`{READER_SKILL_DIR}` 指 `zhipu-reader` skill 的安装目录。父 Agent 派发任务时必须将这两个占位符替换为实际绝对路径。

**派发组 1-4 的子 Agent 时告知：**
```
你是新闻分析子 agent。开始工作前，你必须先读取以下文件获取完整的任务指令和判断标准：

1. 读取任务模板（本 skill 内）：
   {SKILL_DIR}/subagent_template.md

2. 读取新闻重要性判断标准（本 skill 内）：
   {SKILL_DIR}/importance_criteria.md

3. 读取正文提取工具用法（外部 skill: zhipu-reader）：
   {READER_SKILL_DIR}/SKILL.md

读取完毕后，严格按照模板中的执行步骤操作。
- 负责来源：{来源列表}
- 使用脚本：./.venv/bin/python scripts/fetch_news.py --source {来源} --limit 15
- 输出文件路径：{output_file}
```

**派发组 5-6 的子 Agent 时告知：**
```
你是新闻分析子 agent。开始工作前，你必须先读取以下文件获取完整的任务指令和判断标准：

1. 读取任务模板（本 skill 内）：
   {SKILL_DIR}/subagent_template.md

2. 读取新闻重要性判断标准（本 skill 内）：
   {SKILL_DIR}/importance_criteria.md

3. 读取正文提取工具用法（外部 skill: zhipu-reader）：
   {READER_SKILL_DIR}/SKILL.md

读取完毕后，严格按照模板中的执行步骤操作。
- 负责来源：{来源列表}
- 使用脚本：./.venv/bin/python scripts/fetch_news_v2.py --source {来源} --limit 15
- 输出文件路径：{output_file}
```

**第二步：聚类去重**（输入/输出格式：JSON **数组** `[{...}, {...}]`）

启动"聚类去重 Agent"：
```
你是聚类去重 Agent。开始工作前，读取任务模板（本 skill 内）：
{SKILL_DIR}/cluster_agent_template.md

读取完毕后，严格按照模板中的执行步骤操作。
- 输入文件：/tmp/news_hn_ph.json /tmp/news_github_v2ex.json /tmp/news_36kr_tencent.json /tmp/news_wallstreetcn_weibo.json /tmp/news_tc_verge.json /tmp/news_academic_extra.json
- 输出聚类结果：/tmp/news_clustered.json
```

**第二点五步：事件关联**（输入/输出格式：JSON **数组** `[{...}, {...}]`）

启动"事件关联 Agent"：
```
你是事件关联 Agent。开始工作前，读取任务模板（本 skill 内）：
{SKILL_DIR}/event_linker_agent_template.md

读取完毕后，严格按照模板中的执行步骤操作。
- 输入文件：/tmp/news_clustered.json
- 输出新闻文件：/tmp/news_linked.json
- 输出事件更新：/tmp/events_history_update.json
```

**第三步：合并新闻**（输入：JSON **数组**；输出：JSON **对象** `{title, date, main:[], brief:[], ...}`）

```bash
./.venv/bin/python scripts/merge_news.py \
  /tmp/news_linked.json \
  -o /tmp/news_merged.json --max-main <max-main> \
  --category-min <category-min> --category-max <category-max> \
  --title "<title>" --signature "<signature>" --tagline "<tagline>"
```

其中 title/signature/tagline/max-main/category-min/category-max 从 message 参数中读取。

**第四步：总编辑润色（新增）**（输入/输出格式：JSON **对象** `{title, date, main:[], brief:[], ...}`）

启动"Editor Agent"：
```
你是 Editor Agent（总编辑）。开始工作前，读取任务模板（本 skill 内）：
{SKILL_DIR}/editor_agent_template.md

读取完毕后，严格按照模板中的任务执行。
- 输入文件：/tmp/news_merged.json
- 输出文件：/tmp/news_final.json
```

> Editor Agent 会输出 3 个编辑字段到 `/tmp/editor_fields.json`，然后调用 `merge_editor_output.py` 合并到原始文件。

**第五步：生成 HTML 并发送邮件**（输入：JSON **对象**；输出：HTML 文件）

```bash
# 1. JSON 转 HTML
./.venv/bin/python scripts/news2html /tmp/news_final.json -o /tmp/news_report.html

# 2. 发送邮件（emails/title 从 message 参数读取）
./.venv/bin/python scripts/send-email --html --file /tmp/news_report.html "<emails>" "<title>"
```

**第六步：更新事件历史**

事件关联 Agent 已在 Step 7 中自动更新 LanceDB，此步骤无需额外操作。如需手动补充更新：
```bash
./.venv/bin/python scripts/update_events_history.py --update /tmp/events_history_update.json
```

**第七步：清理临时文件**

```bash
rm -f /tmp/news_*.json /tmp/news_report.html /tmp/events_history_update.json
```

---

## 三、并行子 Agent 策略

### 3.1 来源分组

| 场景 | 分组策略 |
|------|----------|
| 全局扫描（选项12） | 6组：海外科技(HN+PH) / 开发者(GitHub+V2EX) / 国内科技(36kr+腾讯) / 财经社会(华尔街见闻+微博) / 国际媒体(TechCrunch+TheVerge) / 学术与拓展(ArXiv+HuggingFace+财联社+少数派) |
| AI 速递 | 2组：hackernews / producthunt |
| 中国科技 | 2组：36kr / tencent |
| 极客开源 | 2组：github / v2ex |

**来源与脚本对应关系：**
- 组 1-4 使用 `fetch_news.py`
- 组 5-6 使用 `fetch_news_v2.py`

### 3.2 父子 Agent 及新增 Agent 职责划分

| 职责 | 抓取子 Agent | 聚类去重 Agent | 事件关联 Agent | Editor Agent | 父 Agent |
|------|:---:|:---:|:---:|:---:|:---:|
| **抓取新闻** | 负责 | - | - | - | - |
| **判断 main/brief** | 负责 | - | - | - | - |
| **importance 打分** | 负责 | - | - | - | - |
| **分类 category** | 负责 | - | - | - | - |
| **深度分析** | 负责 | - | - | - | - |
| **写入文件** | 负责 | - | - | - | - |
| **语义去重** | - | 负责 | - | - | - |
| **事件追踪匹配** | - | - | 负责 | - | - |
| **跨事件关联** | - | - | 负责 | - | - |
| **更新事件历史** | - | - | 负责 | - | - |
| **去重+总量控制** | - | - | - | - | 调用 merge_news.py |
| **今日概览** | - | - | - | 负责 | - |
| **跨领域关联** | - | - | - | 负责 | - |
| **阅读建议** | - | - | - | 负责 | - |
| **生成报告** | - | - | - | - | 调用 news2html + send-email |

### 3.3 Agent 任务模板

模板和判断标准已拆分为独立文件：
- 抓取子 Agent 任务模板：`subagent_template.md`
- 新闻重要性判断标准：`importance_criteria.md`
- 聚类去重 Agent 任务模板：`cluster_agent_template.md`
- 事件关联 Agent 任务模板：`event_linker_agent_template.md`
- Editor Agent 任务模板：`editor_agent_template.md`

各 Agent 会直接读取对应的模板文件。

---

## 四、新闻重要性判断标准

完整内容已移至独立文件：`importance_criteria.md`

子 Agent 在执行时会直接读取该文件。
