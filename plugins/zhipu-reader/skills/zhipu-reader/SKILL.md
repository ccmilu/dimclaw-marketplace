---
name: zhipu-reader
description: "使用智谱 AI Reader API（网页阅读）读取并解析指定 URL 的网页内容，返回干净的 markdown 或纯文本。支持控制缓存、图片保留、GFM 格式、图片摘要和链接摘要等选项。当需要通过智谱读取/提取/解析网页内容，或用户说'智谱阅读'、'zhipu reader'、'用智谱读取网页'、'读取这个网页'时使用此技能。也可作为 extract skill 的中文替代方案，从已知 URL 提取网页正文。需要 ZHIPU_API_KEY 环境变量。"
---

# 智谱网页阅读

使用智谱 AI 的 Reader API 读取并解析网页内容，返回结构化的 markdown 或纯文本。

## 前置条件

**需要 ZHIPU_API_KEY** - 在 https://bigmodel.cn/usercenter/proj-mgmt/apikeys 获取

添加到 `~/.claude/settings.json`：
```json
{
  "env": {
    "ZHIPU_API_KEY": "your-api-key-here"
  }
}
```

## 快速开始

```bash
./scripts/zhipu_reader.sh '<json>'
```

### 单 URL 模式

返回原始 API 响应（`reader_result.content` 包含正文）。

```bash
# 基础读取（返回 markdown）
./scripts/zhipu_reader.sh '{"url": "https://www.example.com"}'

# 返回纯文本，不保留图片
./scripts/zhipu_reader.sh '{"url": "https://www.example.com", "retain_images": false, "return_format": "text"}'

# 禁用缓存，获取最新内容
./scripts/zhipu_reader.sh '{"url": "https://www.example.com", "no_cache": true}'

# 包含图片摘要和链接摘要
./scripts/zhipu_reader.sh '{"url": "https://www.example.com", "with_images_summary": true, "with_links_summary": true}'

# 自定义超时时间
./scripts/zhipu_reader.sh '{"url": "https://www.example.com", "timeout": 30}'
```

### 批量模式

传入 `urls` 数组，脚本内串行调用，每个 URL 失败自动重试（默认 3 次）。返回 JSON 数组。

```bash
# 批量提取多个 URL
./scripts/zhipu_reader.sh '{"urls": ["https://a.com/article1", "https://b.com/article2", "https://c.com/article3"], "retain_images": false}'

# 自定义重试次数
./scripts/zhipu_reader.sh '{"urls": ["https://a.com", "https://b.com"], "max_retries": 5}'
```

**批量模式返回格式：**
```json
[
  {"url": "https://a.com", "title": "标题", "description": "描述", "content": "正文...", "status": "ok"},
  {"url": "https://b.com", "title": "", "content": "", "status": "failed", "error": "错误信息"}
]
```

进度信息输出到 stderr，JSON 结果输出到 stdout，可安全地通过管道处理。

## API 参考

接口：`POST https://open.bigmodel.cn/api/paas/v4/reader`（脚本已封装，无需手动调用）

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `url` | string | 单URL必填 | - | 需要抓取的网页 URL |
| `urls` | string[] | 批量必填 | - | 批量模式：URL 数组，串行调用 |
| `max_retries` | integer | 否 | `3` | 批量模式每个 URL 最大重试次数 |
| `return_format` | string | 否 | `"markdown"` | 返回格式：`markdown` 或 `text` |
| `timeout` | integer | 否 | `20` | 请求超时时间（秒） |
| `no_cache` | boolean | 否 | `false` | 是否禁用缓存 |
| `retain_images` | boolean | 否 | `true` | 是否保留图片 |
| `no_gfm` | boolean | 否 | `false` | 是否禁用 GitHub Flavored Markdown |
| `keep_img_data_url` | boolean | 否 | `false` | 是否保留图片 data URL |
| `with_images_summary` | boolean | 否 | `false` | 是否包含图片摘要 |
| `with_links_summary` | boolean | 否 | `false` | 是否包含链接摘要 |

### 响应格式

```json
{
  "id": "task-id",
  "created": 1234567890,
  "request_id": "req-id",
  "model": "web-reader-xxx",
  "reader_result": {
    "title": "网页标题",
    "description": "网页简要描述",
    "url": "https://www.example.com",
    "content": "解析后的网页正文内容（markdown 或 text）...",
    "metadata": {
      "keywords": "页面关键词",
      "description": "元数据描述"
    }
  }
}
```

关键字段说明：
- `reader_result.content` - 网页解析后的主要内容，包含正文、图片、链接等标记
- `reader_result.title` - 网页标题
- `reader_result.description` - 网页简要描述
- `reader_result.metadata` - 页面元数据（关键词、视口设置等）

### 错误响应

```json
{
  "error": {
    "code": "错误码",
    "message": "错误描述"
  }
}
```

## 使用建议

- **默认使用 `return_format: "markdown"`** 获取结构化内容，便于后续处理
- **需要最新内容时**设置 `no_cache: true`
- **不需要图片时**设置 `retain_images: false` 减少响应体积
- **需要了解页面链接结构时**启用 `with_links_summary: true`
- **超时场景**适当增大 `timeout`（如大页面设为 30-60）
