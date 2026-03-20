# dimclaw-news-skill

> 本文档同时面向 AI Agent 阅读。如果你是 AI，请按照本文档的步骤完成安装和初始化，并在用户触发技能前确认所有前置条件已满足。

综合新闻聚合 Skill，从 16 个来源实时获取新闻，支持 AI 语义聚类去重、事件向量追踪、HTML 邮件报告生成与发送。

来源：Hacker News、GitHub Trending、Product Hunt、36氪、腾讯新闻、华尔街见闻、V2EX、微博、ArXiv、TechCrunch、The Verge、HuggingFace Papers、财联社、少数派。

---

## 一、前置依赖：zhipu-reader

本 Skill 依赖 **zhipu-reader** 提取文章正文，必须先安装。安装和配置方式请参考 zhipu-reader 的 `SKILL.md`。

---

## 二、安装本 Skill

### Claude Code

```
/plugin marketplace add jason/dimclaw-marketplace
/plugin install zhipu-reader@dimclaw
/plugin install dimclaw-news-skill@dimclaw
```

### OpenClaw

OpenClaw 不支持 GitHub 链接自动安装，需手动复制：

```bash
# 克隆仓库
git clone https://github.com/jason/dimclaw-marketplace.git

# 将两个 skill 文件夹复制到 OpenClaw skills 目录
cp -r dimclaw-marketplace/plugins/zhipu-reader/skills/zhipu-reader ~/.openclaw/skills/
cp -r dimclaw-marketplace/plugins/dimclaw-news-skill/skills/dimclaw-news-skill ~/.openclaw/skills/
```

---

## 三、初始化

### 1. 创建 Python 虚拟环境并安装依赖

在 skill 目录下执行：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 2. 配置环境变量

在 `~/.claude/settings.json`（Claude Code）或对应配置文件中添加：

```json
{
  "env": {
    "ZHIPU_API_KEY": "your-zhipu-api-key",
    "EMBEDDING_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
    "EMBEDDING_API_KEY": "your-zhipu-api-key",
    "EMBEDDING_MODEL": "embedding-3",
    "EMBEDDING_DIMENSIONS": "2048",
    "SMTP_HOST": "smtp.163.com",
    "SMTP_PORT": "465",
    "SMTP_USER": "your-email@163.com",
    "SMTP_PASSWORD": "your-smtp-password"
  }
}
```

| 变量 | 必需 | 说明 |
|------|------|------|
| `ZHIPU_API_KEY` | 是 | 智谱 AI API Key（[获取](https://bigmodel.cn/usercenter/proj-mgmt/apikeys)） |
| `EMBEDDING_BASE_URL` | 是 | Embedding API base URL（OpenAI 兼容协议） |
| `EMBEDDING_API_KEY` | 是 | Embedding API 密钥 |
| `EMBEDDING_MODEL` | 是 | Embedding 模型名，如 `embedding-3` |
| `EMBEDDING_DIMENSIONS` | 是 | 向量维度，如 `2048` |
| `SMTP_HOST` | 是（发邮件） | SMTP 服务器，默认 `smtp.163.com` |
| `SMTP_PORT` | 否 | SMTP 端口，默认 `465` |
| `SMTP_USER` | 是（发邮件） | 发件人邮箱 |
| `SMTP_PASSWORD` | 是（发邮件） | SMTP 授权码（非登录密码） |

### 3. 初始化个人偏好

`importance_criteria.md` 定义了新闻重要性的判断标准，其中「按受众相关性」部分包含占位符，**必须填写后才能准确筛选新闻**。

> **如果你是 AI**：请先读取 `importance_criteria.md`，检查其中是否存在 `{YOUR_BACKGROUND}`、`{YOUR_INTERESTS}`、`{YOUR_DOMAIN_*}` 等未填写的占位符。如果存在，**在执行任何新闻聚合任务之前**，主动向用户逐一提问以了解其背景和兴趣领域，获取信息后直接将占位符替换写入文件，无需用户手动操作。

如需手动填写，将以下占位符替换为你自己的描述：

- `{YOUR_BACKGROUND}`：你的职业/学历背景，如「软件工程师 + 独立开发者」
- `{YOUR_INTERESTS}`：你关注的核心领域，如「独立产品、开源工具、AI 应用」
- `{YOUR_DOMAIN_1~3}`：你希望重点追踪的细分方向

### 4. 验证安装

```bash
# 测试新闻抓取
./.venv/bin/python scripts/fetch_news.py --source hackernews --limit 3

# 测试事件检索（需要 embedding 环境变量）
./.venv/bin/python scripts/search_events.py --query "测试" --limit 3
```

---

## 四、使用方法

将以下提示词放入 Claude Code 或 OpenClaw 的定时任务中：

```
执行 dimclaw-news-skill 的如意如意模式12，生成报告并发送邮件。

参数：
- emails: 收件人1@example.com,收件人2@example.com
- title: 每日新闻简报
- signature: 新闻小助手
- tagline: 你的一句格言
- max-main: 30
- category-min: 4
- category-max: 8
```

### 参数说明

| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `emails` | 是 | — | 收件人邮箱，多个用逗号分隔 |
| `title` | 否 | `每日新闻简报` | 邮件标题 |
| `signature` | 否 | `新闻聚合助手` | 报告署名 |
| `tagline` | 否 | `让信息为你服务` | 报告副标题 |
| `max-main` | 否 | `20` | 头条新闻最大数量 |
| `category-min` | 否 | `3` | 每个分类最少新闻数 |
| `category-max` | 否 | `10` | 每个分类最多新闻数 |

---

## 五、技术细节

### 整体架构

本 Skill 采用**多 Agent 并行流水线**架构，由父 Agent 编排，多个子 Agent 并行执行，各司其职：

```
父 Agent
├── 并行启动 6 个采集子 Agent（各负责 2-4 个来源）
│   └── 每个子 Agent：抓取 → 正文提取 → 重要性评分 → 写入 /tmp/news_*.json
├── 聚类去重 Agent：URL + 标题机械预去重 → 语义聚类合并
├── 事件关联 Agent：向量检索历史事件 → 标注追踪徽章 → 更新 LanceDB
├── merge_news.py：结构化合并，控制头条/分类数量
├── Editor Agent：生成今日概览、跨领域关联、阅读建议
└── news2html + send-email：渲染 HTML 报告并发送邮件
```

### 新闻采集

- **fetch_news.py**：覆盖 8 个来源（Hacker News、GitHub、Product Hunt、36氪、腾讯、华尔街见闻、V2EX、微博），内置多级回退策略
- **fetch_news_v2.py**：覆盖另外 6 个来源（ArXiv、HuggingFace、TechCrunch、The Verge、财联社、少数派），依赖 `ZHIPU_API_KEY` 作为正文提取兜底
- 正文提取通过 **zhipu-reader** skill 批量并发调用智谱 Reader API，最多 6 条/批

### 语义去重与聚类

- `cluster_preprocess.py` 先做 URL + 标题的机械去重
- 聚类去重 Agent 在此基础上做语义层面的合并，将同一事件的多篇报道聚合为一条

### 事件追踪（LanceDB）

- 使用 **LanceDB** 本地向量数据库存储历史事件，数据存放于 `data/events_vector_db/`
- 每条新闻通过 OpenAI 兼容协议生成 embedding，最多 60 条/批
- 事件关联 Agent 对当日新闻做两阶段检索：向量语义检索 + 结构化字段过滤
- 匹配到历史事件的新闻自动标注追踪徽章，并更新事件时间线
- 检索时支持 `days_back` 时间窗口过滤

### 报告生成

- `merge_news.py` 将多路输出合并为结构化 JSON（区分头条 `main` 和简报 `brief`）
- `news2html` 将 JSON 渲染为带追踪徽章、跨领域关联、阅读建议的 HTML 邮件
- `send-email` 通过 SMTP SSL 发送，支持多收件人

### 设计亮点

**极致的并行效率**
6 组采集子 Agent 同时运行，按地域和内容类型精心分组，互不干扰。相比串行方案，采集阶段耗时压缩至原来的六分之一，即便面对十几个来源也能在分钟级完成。

**两阶段去重，精准又高效**
先用机械规则（URL + 标题）快速预过滤，再交给语义聚类 Agent 做深度合并。两步走的设计让语义处理只面对真正需要判断的内容，大幅降低 token 消耗，同时保证去重质量。

**跨天事件追踪，真正理解新闻**
内置 LanceDB 本地向量数据库，每条新闻都会与历史事件做语义匹配。同一事件连续多天的报道会被自动识别并标注追踪徽章，让读者一眼看出哪些是持续发酵的大事，而不是把每天的新闻孤立地看待。这在同类聚合工具中极为罕见。

**清晰的 Agent 职责分离**
采集、去重、事件关联、编辑润色、报告生成，五个阶段各有专属 Agent，任务模板独立维护。修改某一环节的行为无需触碰其他模块，扩展新来源或调整报告风格都只需改对应的模板文件。

**多重降级策略，稳健可靠**
每个新闻来源内置多级回退，zhipu-reader 作为正文提取的兜底方案。单个来源或 API 异常不会影响整体输出，系统会优雅地跳过问题节点继续运行。

---

## TODO

### 事件关系图

事件之间的跨领域关联数据已经写入数据库，但可视化呈现方式尚未确定。关系图能帮助读者直观看到不同领域事件之间的隐性联系（如某项技术突破如何同时影响金融、开源和学术圈），目前还在思考最合适的展示形式。

---

### 目录结构

```
dimclaw-marketplace/
├── .claude-plugin/marketplace.json       # Marketplace 注册表
└── plugins/
    ├── dimclaw-news-skill/
    │   ├── .claude-plugin/plugin.json
    │   └── skills/dimclaw-news-skill/
    │       ├── SKILL.md                  # Skill 定义（AI 读取）
    │       ├── requirements.txt
    │       ├── scripts/                  # 所有执行脚本
    │       ├── tests/                    # 单元与集成测试
    │       ├── *_agent_template.md       # 各子 Agent 任务模板
    │       ├── importance_criteria.md    # 新闻重要性评分标准
    │       └── data/                     # LanceDB 向量数据库（本地，不入库）
    └── zhipu-reader/
        ├── .claude-plugin/plugin.json
        └── skills/zhipu-reader/
            ├── SKILL.md
            └── scripts/zhipu_reader.sh
```
