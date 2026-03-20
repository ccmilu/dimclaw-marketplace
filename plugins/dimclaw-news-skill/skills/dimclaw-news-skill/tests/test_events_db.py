"""events_db.py 的单元测试。

使用 mock 替代真实的 embedding API 调用，
用临时目录作为 LanceDB 存储路径。
"""

import json
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

DIMENSIONS = 8  # 测试用小维度


def _fake_embedding(text: str) -> list[float]:
    """根据文本生成确定性的假 embedding，方便测试。"""
    h = hash(text) & 0xFFFFFFFF
    return [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(DIMENSIONS)]


def _make_mock_client():
    """创建 mock 的 OpenAI 客户端。"""
    client = MagicMock()

    def _create_embeddings(model, input, dimensions=None):
        resp = MagicMock()
        if isinstance(input, str):
            texts = [input]
        else:
            texts = list(input)
        data = []
        for i, t in enumerate(texts):
            item = MagicMock()
            item.index = i
            item.embedding = _fake_embedding(t)
            data.append(item)
        resp.data = data
        return resp

    client.embeddings.create = _create_embeddings
    return client


@pytest.fixture
def db_instance(tmp_path):
    """创建一个使用临时目录和 mock embedding 的 EventsDB 实例。"""
    env_vars = {
        "EMBEDDING_BASE_URL": "https://fake.api/v4",
        "EMBEDDING_API_KEY": "fake-key",
        "EMBEDDING_MODEL": "test-model",
        "EMBEDDING_DIMENSIONS": str(DIMENSIONS),
    }
    with patch.dict(os.environ, env_vars):
        with patch("events_db._get_embedding_client", return_value=_make_mock_client()):
            from events_db import EventsDB
            db = EventsDB(db_path=str(tmp_path / "test_db"))
            yield db


def _sample_event(event_id="evt_001", event_name="测试事件", **overrides):
    """生成样本事件数据。"""
    data = {
        "event_id": event_id,
        "event_name": event_name,
        "summary": "这是一个测试摘要",
        "keywords": ["测试", "事件"],
        "category": "tech",
        "first_seen": "2026-03-18",
        "last_seen": "2026-03-18",
        "consecutive_days": 1,
        "latest_importance": 7,
        "importance_trend": [7],
        "daily_entries": {"2026-03-18": {"title": "测试标题", "url": ""}},
        "related_events": [],
    }
    data.update(overrides)
    return data


# ============================================================
# 1. 基本的 upsert 和 get
# ============================================================

class TestUpsertAndGet:
    def test_insert_new_event(self, db_instance):
        result = db_instance.upsert_event(_sample_event())
        assert result == "new"
        assert db_instance.count() == 1

    def test_update_existing_event(self, db_instance):
        db_instance.upsert_event(_sample_event())
        result = db_instance.upsert_event(
            _sample_event(summary="更新后的摘要")
        )
        assert result == "updated"
        assert db_instance.count() == 1

    def test_get_event_found(self, db_instance):
        db_instance.upsert_event(_sample_event())
        evt = db_instance.get_event("evt_001")
        assert evt is not None
        assert evt["event_id"] == "evt_001"
        assert evt["event_name"] == "测试事件"
        assert evt["keywords"] == ["测试", "事件"]
        assert isinstance(evt["daily_entries"], dict)

    def test_get_event_not_found(self, db_instance):
        result = db_instance.get_event("nonexistent")
        assert result is None

    def test_upsert_updates_fields(self, db_instance):
        db_instance.upsert_event(_sample_event())
        db_instance.upsert_event(_sample_event(
            summary="新摘要",
            latest_importance=9,
            last_seen="2026-03-19",
        ))
        evt = db_instance.get_event("evt_001")
        assert evt["summary"] == "新摘要"
        assert evt["latest_importance"] == 9
        assert evt["last_seen"] == "2026-03-19"


# ============================================================
# 2. 批量 upsert
# ============================================================

class TestBatchUpsert:
    def test_batch_insert(self, db_instance):
        events = [
            _sample_event(event_id="evt_001", event_name="事件A"),
            _sample_event(event_id="evt_002", event_name="事件B"),
            _sample_event(event_id="evt_003", event_name="事件C"),
        ]
        result = db_instance.batch_upsert(events)
        assert result == {"new": 3, "updated": 0}
        assert db_instance.count() == 3

    def test_batch_mixed_insert_update(self, db_instance):
        db_instance.upsert_event(_sample_event(event_id="evt_001"))
        events = [
            _sample_event(event_id="evt_001", summary="更新"),
            _sample_event(event_id="evt_002", event_name="新事件"),
        ]
        result = db_instance.batch_upsert(events)
        assert result == {"new": 1, "updated": 1}
        assert db_instance.count() == 2

    def test_batch_empty_list(self, db_instance):
        result = db_instance.batch_upsert([])
        assert result == {"new": 0, "updated": 0}


# ============================================================
# 3. 向量检索
# ============================================================

class TestSearchSimilar:
    def test_basic_search(self, db_instance):
        db_instance.upsert_event(_sample_event(
            event_id="evt_001", event_name="人工智能大模型发展"
        ))
        db_instance.upsert_event(_sample_event(
            event_id="evt_002", event_name="全球气候变化峰会"
        ))
        results = db_instance.search_similar("人工智能大模型发展", limit=5)
        assert len(results) > 0
        assert "_distance" in results[0]

    def test_search_with_category_filter(self, db_instance):
        db_instance.upsert_event(_sample_event(
            event_id="evt_001", event_name="AI事件", category="tech"
        ))
        db_instance.upsert_event(_sample_event(
            event_id="evt_002", event_name="金融事件", category="finance"
        ))
        results = db_instance.search_similar(
            "科技", category="tech", limit=10
        )
        for r in results:
            assert r["category"] == "tech"

    def test_search_with_days_back_filter(self, db_instance):
        today = datetime.now().strftime("%Y-%m-%d")
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        db_instance.upsert_event(_sample_event(
            event_id="evt_new", event_name="新事件", last_seen=today
        ))
        db_instance.upsert_event(_sample_event(
            event_id="evt_old", event_name="旧事件", last_seen=old_date
        ))
        results = db_instance.search_similar("事件", days_back=7, limit=10)
        event_ids = [r["event_id"] for r in results]
        assert "evt_new" in event_ids
        assert "evt_old" not in event_ids

    def test_search_limit(self, db_instance):
        for i in range(10):
            db_instance.upsert_event(_sample_event(
                event_id=f"evt_{i:03d}", event_name=f"事件{i}"
            ))
        results = db_instance.search_similar("事件", limit=3)
        assert len(results) <= 3


# ============================================================
# 4. 结构化检索
# ============================================================

class TestSearchByCategory:
    def test_filter_by_category(self, db_instance):
        today = datetime.now().strftime("%Y-%m-%d")
        db_instance.upsert_event(_sample_event(
            event_id="evt_001", category="headline", last_seen=today
        ))
        db_instance.upsert_event(_sample_event(
            event_id="evt_002", category="tech", last_seen=today
        ))
        results = db_instance.search_by_category("headline", days_back=7)
        assert len(results) == 1
        assert results[0]["category"] == "headline"

    def test_filter_excludes_old_events(self, db_instance):
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        db_instance.upsert_event(_sample_event(
            event_id="evt_old", category="tech", last_seen=old_date
        ))
        results = db_instance.search_by_category("tech", days_back=7)
        assert len(results) == 0


# ============================================================
# 5. list_events 和 count
# ============================================================

class TestListAndCount:
    def test_list_all(self, db_instance):
        db_instance.upsert_event(_sample_event(event_id="evt_001"))
        db_instance.upsert_event(_sample_event(event_id="evt_002"))
        results = db_instance.list_events()
        assert len(results) == 2

    def test_list_with_category(self, db_instance):
        db_instance.upsert_event(_sample_event(
            event_id="evt_001", category="tech"
        ))
        db_instance.upsert_event(_sample_event(
            event_id="evt_002", category="finance"
        ))
        results = db_instance.list_events(category="finance")
        assert len(results) == 1
        assert results[0]["category"] == "finance"

    def test_count(self, db_instance):
        assert db_instance.count() == 0
        db_instance.upsert_event(_sample_event(event_id="evt_001"))
        assert db_instance.count() == 1
        db_instance.upsert_event(_sample_event(event_id="evt_002"))
        assert db_instance.count() == 2


# ============================================================
# 6. JSON 字段序列化/反序列化
# ============================================================

class TestSerialization:
    def test_keywords_round_trip(self, db_instance):
        db_instance.upsert_event(_sample_event(
            keywords=["AI", "深度学习", "GPT"]
        ))
        evt = db_instance.get_event("evt_001")
        assert evt["keywords"] == ["AI", "深度学习", "GPT"]

    def test_daily_entries_round_trip(self, db_instance):
        entries = {
            "2026-03-18": {"title": "标题A", "url": "http://a.com"},
            "2026-03-19": {"title": "标题B", "url": "http://b.com"},
        }
        db_instance.upsert_event(_sample_event(daily_entries=entries))
        evt = db_instance.get_event("evt_001")
        assert evt["daily_entries"] == entries

    def test_importance_trend_round_trip(self, db_instance):
        trend = [5, 7, 8, 9]
        db_instance.upsert_event(_sample_event(importance_trend=trend))
        evt = db_instance.get_event("evt_001")
        assert evt["importance_trend"] == trend

    def test_related_events_round_trip(self, db_instance):
        related = ["evt_002", "evt_003"]
        db_instance.upsert_event(_sample_event(related_events=related))
        evt = db_instance.get_event("evt_001")
        assert evt["related_events"] == related


# ============================================================
# 7. 错误处理
# ============================================================

# ============================================================
# 7. _build_embed_text 测试
# ============================================================

class TestBuildEmbedText:
    """测试 _build_embed_text 多字段拼接逻辑。"""

    def test_build_embed_text_all_fields(self, db_instance):
        """event_name + keywords(list) + summary 全有 → 'name | kw1, kw2 | summary'"""
        event = {
            "event_name": "AI突破",
            "keywords": ["大模型", "GPT"],
            "summary": "AI取得重大进展",
        }
        result = db_instance._build_embed_text(event)
        assert result == "AI突破 | 大模型, GPT | AI取得重大进展"

    def test_build_embed_text_only_name(self, db_instance):
        """只有 event_name，无 keywords 无 summary → 'name'"""
        event = {"event_name": "测试事件"}
        result = db_instance._build_embed_text(event)
        assert result == "测试事件"

    def test_build_embed_text_name_and_keywords_json(self, db_instance):
        """keywords 是 JSON string '["a","b"]' → 应正确解析拼接"""
        event = {
            "event_name": "测试事件",
            "keywords": '["关键词A","关键词B"]',
        }
        result = db_instance._build_embed_text(event)
        assert result == "测试事件 | 关键词A, 关键词B"

    def test_build_embed_text_empty(self, db_instance):
        """全部字段为空 → 返回 ''"""
        event = {"event_name": "", "keywords": [], "summary": ""}
        result = db_instance._build_embed_text(event)
        assert result == ""

    def test_build_embed_text_name_and_summary_no_keywords(self, db_instance):
        """有 name 和 summary，keywords 为空列表 → 'name | summary'"""
        event = {
            "event_name": "事件名称",
            "keywords": [],
            "summary": "事件摘要",
        }
        result = db_instance._build_embed_text(event)
        assert result == "事件名称 | 事件摘要"

    def test_build_embed_text_no_fields(self, db_instance):
        """完全空 dict → 返回 ''"""
        result = db_instance._build_embed_text({})
        assert result == ""

    def test_build_embed_text_keywords_invalid_json_string(self, db_instance):
        """keywords 是无效 JSON string → 忽略 keywords"""
        event = {
            "event_name": "测试",
            "keywords": "not valid json",
            "summary": "摘要",
        }
        result = db_instance._build_embed_text(event)
        assert result == "测试 | 摘要"


# ============================================================
# 8. embedding 调用链验证
# ============================================================

class TestEmbeddingCallChain:
    """验证 upsert_event 和 batch_upsert 使用 _build_embed_text 拼接后的文本。"""

    def test_upsert_event_uses_build_embed_text(self, db_instance):
        """upsert_event 应使用 _build_embed_text 拼接后的文本调用 _embed。"""
        from unittest.mock import patch as mp

        event = _sample_event(
            event_name="AI事件",
            keywords=["大模型", "GPT"],
            summary="AI重大突破",
        )
        expected_text = "AI事件 | 大模型, GPT | AI重大突破"

        original_embed = db_instance._embed
        called_texts = []

        def tracking_embed(text):
            called_texts.append(text)
            return original_embed(text)

        db_instance._embed = tracking_embed
        db_instance.upsert_event(event)

        assert len(called_texts) == 1
        assert called_texts[0] == expected_text

    def test_batch_upsert_uses_build_embed_text(self, db_instance):
        """batch_upsert 应使用 _build_embed_text 拼接后的文本调用 _embed_batch。"""
        events = [
            _sample_event(
                event_id="evt_chain_1",
                event_name="事件A",
                keywords=["kw1"],
                summary="摘要A",
            ),
            _sample_event(
                event_id="evt_chain_2",
                event_name="事件B",
                keywords=["kw2", "kw3"],
                summary="摘要B",
            ),
        ]
        expected_texts = [
            "事件A | kw1 | 摘要A",
            "事件B | kw2, kw3 | 摘要B",
        ]

        original_embed_batch = db_instance._embed_batch
        called_texts_list = []

        def tracking_embed_batch(texts):
            called_texts_list.extend(texts)
            return original_embed_batch(texts)

        db_instance._embed_batch = tracking_embed_batch
        db_instance.batch_upsert(events)

        assert called_texts_list == expected_texts


# ============================================================
# 9. 错误处理
# ============================================================

# ============================================================
# 10. embed_batch 公开方法
# ============================================================

class TestEmbedBatch:
    def test_embed_batch_returns_vectors(self, db_instance):
        """embed_batch 应返回与输入等长的向量列表。"""
        texts = ["文本A", "文本B", "文本C"]
        vectors = db_instance.embed_batch(texts)
        assert len(vectors) == 3
        for v in vectors:
            assert len(v) == DIMENSIONS
            assert all(isinstance(x, float) for x in v)

    def test_embed_batch_empty_input(self, db_instance):
        """embed_batch 空列表应返回空列表。"""
        vectors = db_instance.embed_batch([])
        assert vectors == []

    def test_embed_batch_consistent_with_private(self, db_instance):
        """embed_batch 应与 _embed_batch 返回相同结果。"""
        texts = ["测试一致性"]
        public = db_instance.embed_batch(texts)
        private = db_instance._embed_batch(texts)
        assert public == private


# ============================================================
# 11. search_similar_by_vector
# ============================================================

class TestSearchSimilarByVector:
    def test_search_by_vector_returns_results(self, db_instance):
        """用预计算向量搜索应返回结果。"""
        # 先插入一些事件
        for i in range(3):
            db_instance.upsert_event(_sample_event(
                event_id=f"evt_sv_{i}",
                event_name=f"向量搜索事件{i}",
            ))

        # 用 embed 计算一个向量，再用 search_similar_by_vector 搜索
        vector = db_instance._embed("向量搜索事件0")
        results = db_instance.search_similar_by_vector(vector=vector, limit=5)
        assert len(results) > 0
        assert all("event_id" in r for r in results)

    def test_search_by_vector_consistent_with_search_similar(self, db_instance):
        """search_similar_by_vector 与 search_similar 使用相同文本应返回相同结果。"""
        for i in range(3):
            db_instance.upsert_event(_sample_event(
                event_id=f"evt_cons_{i}",
                event_name=f"一致性事件{i}",
            ))

        text = "一致性事件0"
        results_text = db_instance.search_similar(text=text, limit=5)
        vector = db_instance._embed(text)
        results_vec = db_instance.search_similar_by_vector(vector=vector, limit=5)

        # 返回的 event_id 集合应相同
        ids_text = {r["event_id"] for r in results_text}
        ids_vec = {r["event_id"] for r in results_vec}
        assert ids_text == ids_vec

    def test_search_by_vector_with_category_filter(self, db_instance):
        """search_similar_by_vector 支持 category 过滤。"""
        db_instance.upsert_event(_sample_event(
            event_id="evt_cat_tech", event_name="技术事件", category="tech",
        ))
        db_instance.upsert_event(_sample_event(
            event_id="evt_cat_fin", event_name="金融事件", category="finance",
        ))

        vector = db_instance._embed("技术事件")
        results = db_instance.search_similar_by_vector(
            vector=vector, category="tech", limit=10
        )
        # 只应返回 tech 类别
        assert all(r["category"] == "tech" for r in results)


# ============================================================
# 12. 错误处理
# ============================================================

class TestErrorHandling:
    def test_missing_env_raises_error(self, tmp_path):
        """缺少 embedding 配置时应该抛异常。"""
        env_vars = {
            "EMBEDDING_BASE_URL": "",
            "EMBEDDING_API_KEY": "",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            # 清除可能存在的环境变量
            for key in ["EMBEDDING_BASE_URL", "EMBEDDING_API_KEY"]:
                os.environ.pop(key, None)
            from events_db import EventsDB
            with pytest.raises(RuntimeError, match="缺少 embedding 配置"):
                EventsDB(db_path=str(tmp_path / "err_db"))
