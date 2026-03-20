#!/usr/bin/env python3
"""
LanceDB 事件向量数据库封装层。

封装所有 LanceDB 操作，提供事件的向量检索、结构化检索、
插入/更新等功能。通过 OpenAI 兼容协议调用 embedding API。

环境变量:
    EMBEDDING_BASE_URL: embedding API base url（如 https://open.bigmodel.cn/api/paas/v4）
    EMBEDDING_API_KEY:  API key
    EMBEDDING_MODEL:    模型名（如 embedding-3）
    EMBEDDING_DIMENSIONS: 向量维度（如 1024）
"""

import json
import os
import sys
from datetime import datetime, timedelta

import lancedb
import pyarrow as pa
from openai import OpenAI


TABLE_NAME = "events"


def _get_embedding_client():
    """创建 OpenAI 兼容的 embedding 客户端。"""
    base_url = os.environ.get("EMBEDDING_BASE_URL", "")
    api_key = os.environ.get("EMBEDDING_API_KEY", "")
    if not base_url or not api_key:
        raise RuntimeError(
            "缺少 embedding 配置，请设置环境变量: "
            "EMBEDDING_BASE_URL, EMBEDDING_API_KEY"
        )
    return OpenAI(base_url=base_url, api_key=api_key)


def _get_embedding_config():
    """读取 embedding 模型配置。"""
    model = os.environ.get("EMBEDDING_MODEL", "embedding-3")
    dimensions = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))
    return model, dimensions


def _compute_embedding(client, model: str, text: str, dimensions: int) -> list[float]:
    """调用 embedding API 计算单条文本的向量。"""
    resp = client.embeddings.create(
        model=model,
        input=text,
        dimensions=dimensions,
    )
    return resp.data[0].embedding


def _compute_embeddings_batch(
    client, model: str, texts: list[str], dimensions: int
) -> list[list[float]]:
    """批量计算 embedding 向量。"""
    resp = client.embeddings.create(
        model=model,
        input=texts,
        dimensions=dimensions,
    )
    # 按 index 排序确保顺序一致
    sorted_data = sorted(resp.data, key=lambda x: x.index)
    return [item.embedding for item in sorted_data]


def _build_schema(dimensions: int) -> pa.Schema:
    """构建 LanceDB 表的 PyArrow Schema。"""
    return pa.schema([
        pa.field("event_id", pa.string()),
        pa.field("event_name", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dimensions)),
        pa.field("summary", pa.string()),
        pa.field("keywords", pa.string()),       # JSON 数组
        pa.field("category", pa.string()),        # headline/tech/finance/life/other
        pa.field("first_seen", pa.string()),      # ISO 日期
        pa.field("last_seen", pa.string()),       # ISO 日期
        pa.field("consecutive_days", pa.int32()),
        pa.field("latest_importance", pa.int32()),
        pa.field("importance_trend", pa.string()),  # JSON 数组
        pa.field("daily_entries", pa.string()),   # JSON 对象
        pa.field("related_events", pa.string()),  # JSON 数组
    ])


class EventsDB:
    """LanceDB 事件向量数据库。"""

    def __init__(self, db_path: str = "data/events_vector_db"):
        """连接/创建 LanceDB 数据库，从环境变量读取 embedding 配置。

        Args:
            db_path: 数据库存储路径，相对路径基于工作目录。
        """
        self._client = _get_embedding_client()
        self._model, self._dimensions = _get_embedding_config()
        self._db = lancedb.connect(db_path)
        self._table = self._ensure_table()

    def _ensure_table(self):
        """确保事件表存在，不存在则创建。"""
        existing_tables = self._db.list_tables().tables
        if TABLE_NAME in existing_tables:
            return self._db.open_table(TABLE_NAME)
        schema = _build_schema(self._dimensions)
        return self._db.create_table(TABLE_NAME, schema=schema)

    def _embed(self, text: str) -> list[float]:
        """计算单条文本的 embedding。"""
        return _compute_embedding(
            self._client, self._model, text, self._dimensions
        )

    def _build_embed_text(self, event: dict) -> str:
        """拼接多字段作为 embedding 文本。

        格式: "{event_name} | {keywords_joined} | {summary}"
        空段不留分隔符，所有字段都为空返回空字符串。
        """
        parts = []
        event_name = event.get("event_name", "")
        if event_name:
            parts.append(event_name)

        keywords = event.get("keywords", None)
        if keywords:
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except (json.JSONDecodeError, TypeError):
                    keywords = []
            if isinstance(keywords, list) and keywords:
                parts.append(", ".join(str(k) for k in keywords))

        summary = event.get("summary", "")
        if summary:
            parts.append(summary)

        return " | ".join(parts)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量计算 embedding，自动分批避免 API 限制（每批最多 60 条）。"""
        if not texts:
            return []
        batch_size = 60
        if len(texts) <= batch_size:
            return _compute_embeddings_batch(
                self._client, self._model, texts, self._dimensions
            )
        all_vectors = []
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_vectors = _compute_embeddings_batch(
                self._client, self._model, batch_texts, self._dimensions
            )
            all_vectors.extend(batch_vectors)
        return all_vectors

    def _prepare_record(self, event: dict, vector: list[float]) -> dict:
        """将事件 dict 转为 LanceDB 记录格式。"""
        return {
            "event_id": event["event_id"],
            "event_name": event.get("event_name", ""),
            "vector": vector,
            "summary": event.get("summary", ""),
            "keywords": (
                json.dumps(event["keywords"], ensure_ascii=False)
                if isinstance(event.get("keywords"), list)
                else event.get("keywords", "[]")
            ),
            "category": event.get("category", "other"),
            "first_seen": event.get("first_seen", ""),
            "last_seen": event.get("last_seen", ""),
            "consecutive_days": event.get("consecutive_days", 1),
            "latest_importance": event.get("latest_importance", 5),
            "importance_trend": (
                json.dumps(event["importance_trend"], ensure_ascii=False)
                if isinstance(event.get("importance_trend"), list)
                else event.get("importance_trend", "[]")
            ),
            "daily_entries": (
                json.dumps(event["daily_entries"], ensure_ascii=False)
                if isinstance(event.get("daily_entries"), dict)
                else event.get("daily_entries", "{}")
            ),
            "related_events": (
                json.dumps(event["related_events"], ensure_ascii=False)
                if isinstance(event.get("related_events"), list)
                else event.get("related_events", "[]")
            ),
        }

    @staticmethod
    def _deserialize_record(record: dict) -> dict:
        """将 LanceDB 记录反序列化为业务 dict，解析 JSON 字段。"""
        result = dict(record)
        # 移除 vector 字段（业务层一般不需要）
        result.pop("vector", None)
        # 解析 JSON 字符串字段
        for field in ("keywords", "importance_trend", "related_events"):
            val = result.get(field, "[]")
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    result[field] = []
        for field in ("daily_entries",):
            val = result.get(field, "{}")
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    result[field] = {}
        return result

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """公开的批量 embedding 方法。"""
        return self._embed_batch(texts)

    def search_similar(
        self,
        text: str,
        category: str = None,
        days_back: int = None,
        limit: int = 10,
    ) -> list[dict]:
        """向量检索：返回最相似的历史事件 + 相似度分数。

        Args:
            text: 检索文本，会先计算 embedding 再做向量搜索。
            category: 按分类过滤（可选）。
            days_back: 时间窗口过滤，None 表示不过滤。
            limit: 最大返回数。

        Returns:
            结果列表，每条包含 _distance 分数（越小越相似）。
        """
        vector = self._embed(text)
        return self.search_similar_by_vector(
            vector=vector, category=category, days_back=days_back, limit=limit
        )

    def search_similar_by_vector(
        self,
        vector: list[float],
        category: str = None,
        days_back: int = None,
        limit: int = 10,
    ) -> list[dict]:
        """用已计算的向量做搜索（跳过 embedding 计算）。

        Args:
            vector: 已计算的 embedding 向量。
            category: 按分类过滤（可选）。
            days_back: 时间窗口过滤，None 表示不过滤。
            limit: 最大返回数。

        Returns:
            结果列表，每条包含 _distance 分数（越小越相似）。
        """
        query = self._table.search(vector).limit(limit)

        filters = []
        if category:
            filters.append(f"category = '{category}'")
        if days_back is not None:
            cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            filters.append(f"last_seen >= '{cutoff}'")

        if filters:
            query = query.where(" AND ".join(filters))

        results = query.to_list()
        return [self._deserialize_record(r) for r in results]

    def search_by_category(
        self,
        category: str,
        days_back: int = 7,
        limit: int = 20,
    ) -> list[dict]:
        """结构化检索：按 category + 时间窗口返回事件（不用向量）。

        用于辅助发现"语义不同但有关联"的事件。

        Args:
            category: 分类名。
            days_back: 时间窗口天数。
            limit: 最大返回数。

        Returns:
            事件列表。
        """
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        where = f"category = '{category}' AND last_seen >= '{cutoff}'"
        results = self._table.search().where(where).limit(limit).to_list()
        return [self._deserialize_record(r) for r in results]

    def upsert_event(self, event: dict) -> str:
        """插入或更新事件。

        如果 event_id 已存在则更新所有字段，否则新建。
        event_name 字段会被用来计算 embedding 向量。

        Args:
            event: 事件字典，必须包含 event_id 和 event_name。

        Returns:
            'new' 或 'updated'。
        """
        event_id = event["event_id"]
        event_name = event.get("event_name", "")

        # 检查是否已存在
        existing = self._table.search().where(
            f"event_id = '{event_id}'"
        ).limit(1).to_list()
        is_update = len(existing) > 0

        # 计算 embedding
        embed_text = self._build_embed_text(event)
        vector = self._embed(embed_text) if embed_text else [0.0] * self._dimensions

        record = self._prepare_record(event, vector)

        # 使用 merge_insert 实现 upsert
        self._table.merge_insert("event_id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute([record])

        return "updated" if is_update else "new"

    def batch_upsert(self, events: list[dict]) -> dict:
        """批量写入事件。

        Args:
            events: 事件字典列表。

        Returns:
            {'new': N, 'updated': N}
        """
        if not events:
            return {"new": 0, "updated": 0}

        # 同一批次内按 event_id 去重，保留最后一条（后出现的覆盖先出现的）
        deduped = {}
        for e in events:
            deduped[e["event_id"]] = e
        events = list(deduped.values())

        # 查询哪些 event_id 已存在
        event_ids = [e["event_id"] for e in events]
        existing_ids = set()
        for eid in event_ids:
            rows = self._table.search().where(
                f"event_id = '{eid}'"
            ).limit(1).to_list()
            if rows:
                existing_ids.add(eid)

        # 批量计算 embedding
        texts = [self._build_embed_text(e) for e in events]
        # 将空文本替换为占位符以避免 API 错误
        texts_for_embed = [t if t else "empty" for t in texts]
        vectors = self._embed_batch(texts_for_embed)

        # 对空 event_name 的，用零向量替换
        for i, t in enumerate(texts):
            if not t:
                vectors[i] = [0.0] * self._dimensions

        records = [
            self._prepare_record(event, vector)
            for event, vector in zip(events, vectors)
        ]

        # 使用 merge_insert 批量 upsert
        self._table.merge_insert("event_id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(records)

        new_count = len(event_ids) - len(existing_ids)
        updated_count = len(existing_ids)
        return {"new": new_count, "updated": updated_count}

    def get_event(self, event_id: str) -> dict | None:
        """按 event_id 精确查询。

        Args:
            event_id: 事件 ID。

        Returns:
            事件字典或 None。
        """
        results = self._table.search().where(
            f"event_id = '{event_id}'"
        ).limit(1).to_list()
        if not results:
            return None
        return self._deserialize_record(results[0])

    def list_events(
        self,
        category: str = None,
        limit: int = 100,
    ) -> list[dict]:
        """列出事件，可按 category 过滤。

        Args:
            category: 分类过滤（可选）。
            limit: 最大返回数。

        Returns:
            事件列表。
        """
        query = self._table.search()
        if category:
            query = query.where(f"category = '{category}'")
        results = query.limit(limit).to_list()
        return [self._deserialize_record(r) for r in results]

    def count(self) -> int:
        """返回事件总数。"""
        return self._table.count_rows()
