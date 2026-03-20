#!/bin/bash
# 智谱 Reader API 脚本 - 读取并解析网页内容
# 用法:
#   单 URL: ./zhipu_reader.sh '{"url": "https://example.com"}'
#   批量:   ./zhipu_reader.sh '{"urls": ["https://a.com", "https://b.com"]}'
#
# 批量模式：脚本内串行调用，每个 URL 失败自动重试（最多 3 次），
# 输出 JSON 数组 [{"url":..., "title":..., "content":..., "status":"ok|failed"}, ...]

set -e

JSON_INPUT="$1"

if [ -z "$JSON_INPUT" ]; then
    echo "用法: ./zhipu_reader.sh '<json>'"
    echo ""
    echo "单 URL 模式:"
    echo "  ./zhipu_reader.sh '{\"url\": \"https://example.com\"}'"
    echo ""
    echo "批量模式（串行调用，自动重试）:"
    echo "  ./zhipu_reader.sh '{\"urls\": [\"https://a.com\", \"https://b.com\"]}'"
    echo ""
    echo "可选参数（两种模式通用）:"
    echo "  return_format: \"markdown\"(默认), \"text\" - 返回格式"
    echo "  timeout: integer (默认: 20) - 请求超时时间（秒）"
    echo "  no_cache: true/false (默认: false) - 是否禁用缓存"
    echo "  retain_images: true/false (默认: true) - 是否保留图片"
    echo "  no_gfm: true/false (默认: false) - 是否禁用 GFM"
    echo "  max_retries: integer (默认: 3) - 批量模式每个 URL 最大重试次数"
    exit 1
fi

if [ -z "$ZHIPU_API_KEY" ]; then
    echo "错误: 未设置 ZHIPU_API_KEY 环境变量"
    echo "请前往 https://bigmodel.cn/usercenter/proj-mgmt/apikeys 获取 API Key"
    exit 1
fi

# 验证 JSON 格式
if ! echo "$JSON_INPUT" | jq empty 2>/dev/null; then
    echo "错误: 无效的 JSON 输入"
    exit 1
fi

# 判断模式：urls 数组 vs 单个 url
HAS_URLS=$(echo "$JSON_INPUT" | jq -e '.urls | type == "array"' 2>/dev/null || echo "false")
HAS_URL=$(echo "$JSON_INPUT" | jq -e '.url | type == "string"' 2>/dev/null || echo "false")

if [ "$HAS_URLS" != "true" ] && [ "$HAS_URL" != "true" ]; then
    echo "错误: 缺少必填字段 'url'（单 URL）或 'urls'（批量）"
    exit 1
fi

# === 单 URL 模式（保持原有行为） ===
if [ "$HAS_URL" = "true" ] && [ "$HAS_URLS" != "true" ]; then
    curl -s --connect-timeout 30 --request POST \
        --url https://open.bigmodel.cn/api/paas/v4/reader \
        --header "Authorization: Bearer $ZHIPU_API_KEY" \
        --header 'Content-Type: application/json' \
        --data "$JSON_INPUT" | jq '.'
    exit 0
fi

# === 批量模式 ===
MAX_RETRIES=$(echo "$JSON_INPUT" | jq -r '.max_retries // 3')
BATCH_SIZE=$(echo "$JSON_INPUT" | jq -r '.batch_size // 6')
# 提取公共选项（排除 urls、max_retries 和 batch_size）
COMMON_OPTS=$(echo "$JSON_INPUT" | jq 'del(.urls, .max_retries, .batch_size)')

RESULTS="[]"
TOTAL=$(echo "$JSON_INPUT" | jq '.urls | length')
IDX=0

# 分批处理
BATCH_IDX=0
TOTAL_BATCHES=$(( (TOTAL + BATCH_SIZE - 1) / BATCH_SIZE ))

for BATCH_START in $(seq 0 $BATCH_SIZE $((TOTAL - 1))); do
    BATCH_IDX=$((BATCH_IDX + 1))
    BATCH_END=$((BATCH_START + BATCH_SIZE))
    if [ "$BATCH_END" -gt "$TOTAL" ]; then
        BATCH_END=$TOTAL
    fi
    BATCH_COUNT=$((BATCH_END - BATCH_START))
    >&2 echo "[批次 $BATCH_IDX/$TOTAL_BATCHES] 处理 $BATCH_COUNT 条 URL..."

    # 提取当前批次的 URL
    BATCH_URLS=$(echo "$JSON_INPUT" | jq -r ".urls[$BATCH_START:$BATCH_END][]")

    for URL in $BATCH_URLS; do
        IDX=$((IDX + 1))
        # 构建单个请求 body：公共选项 + 当前 url
        REQ_BODY=$(echo "$COMMON_OPTS" | jq --arg u "$URL" '. + {url: $u}')

        SUCCESS=false
        ATTEMPT=0
        RESP=""

        while [ "$SUCCESS" = "false" ] && [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; do
            ATTEMPT=$((ATTEMPT + 1))

            RESP=$(curl -s --connect-timeout 30 --max-time 60 --request POST \
                --url https://open.bigmodel.cn/api/paas/v4/reader \
                --header "Authorization: Bearer $ZHIPU_API_KEY" \
                --header 'Content-Type: application/json' \
                --data "$REQ_BODY" 2>/dev/null || echo '{"error":{"message":"curl failed"}}')

            # 检查是否有 reader_result.content
            CONTENT=$(echo "$RESP" | jq -r '.reader_result.content // empty' 2>/dev/null)
            if [ -n "$CONTENT" ] && [ "$CONTENT" != "null" ]; then
                SUCCESS=true
            else
                # 检查错误信息
                ERR_MSG=$(echo "$RESP" | jq -r '.error.message // empty' 2>/dev/null)
                if [ -z "$ERR_MSG" ]; then
                    ERR_MSG=$(echo "$RESP" | jq -r '.msg // empty' 2>/dev/null)
                fi
                >&2 echo "[$IDX/$TOTAL] $URL - 第${ATTEMPT}次失败: ${ERR_MSG:-未知错误}"
                if [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; then
                    sleep 2
                fi
            fi
        done

        if [ "$SUCCESS" = "true" ]; then
            TITLE=$(echo "$RESP" | jq -r '.reader_result.title // ""')
            DESC=$(echo "$RESP" | jq -r '.reader_result.description // ""')
            CONTENT_LEN=$(echo "$CONTENT" | wc -c | tr -d ' ')
            >&2 echo "[$IDX/$TOTAL] $URL - 成功 (${CONTENT_LEN}字)"
            ITEM=$(jq -n \
                --arg url "$URL" \
                --arg title "$TITLE" \
                --arg desc "$DESC" \
                --arg content "$CONTENT" \
                '{url: $url, title: $title, description: $desc, content: $content, status: "ok"}')
        else
            ERR_MSG=$(echo "$RESP" | jq -r '.error.message // .msg // "提取失败"' 2>/dev/null)
            >&2 echo "[$IDX/$TOTAL] $URL - 最终失败: $ERR_MSG"
            ITEM=$(jq -n \
                --arg url "$URL" \
                --arg err "$ERR_MSG" \
                '{url: $url, title: "", description: "", content: "", status: "failed", error: $err}')
        fi

        RESULTS=$(echo "$RESULTS" | jq --argjson item "$ITEM" '. + [$item]')
    done

    # 批次间间隔
    if [ "$BATCH_IDX" -lt "$TOTAL_BATCHES" ]; then
        sleep 1
    fi
done

# 输出统计
OK_COUNT=$(echo "$RESULTS" | jq '[.[] | select(.status == "ok")] | length')
FAIL_COUNT=$(echo "$RESULTS" | jq '[.[] | select(.status == "failed")] | length')
>&2 echo "完成: $OK_COUNT 成功, $FAIL_COUNT 失败 (共 $TOTAL 个)"

echo "$RESULTS" | jq '.'
