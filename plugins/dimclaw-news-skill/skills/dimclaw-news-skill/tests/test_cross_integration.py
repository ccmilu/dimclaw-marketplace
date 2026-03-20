"""跨模块集成测试：events_db + search_events + update_events_history 联动。

测试策略：
- 只 mock embedding API（高风险但必要：真实 API 需要网络和密钥）
- 使用真实 LanceDB 实例（临时目录），验证完整数据流
- 不 mock EventsDB，所有模块通过真实 LanceDB 交互

为什么 embedding mock 可以接受：
  本测试的核心目标是验证三个模块之间的数据流转（写入→检索→更新→再检索）。
  embedding mock 使用确定性哈希生成固定维度向量，保证：
  1) 向量维度与 EMBEDDING_DIMENSIONS 一致（8维）
  2) 相同文本生成相同向量（确定性）
  3) 不同文本生成不同向量（哈希碰撞概率极低）
  embedding 质量（语义相似度）不是集成测试关注点，由真实 API 保证。
"""

import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

DIMENSIONS = 8


def _fake_embedding(text: str) -> list[float]:
    """确定性的假 embedding：相同文本 → 相同向量。"""
    h = hash(text) & 0xFFFFFFFF
    return [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(DIMENSIONS)]


def _make_mock_client():
    """创建 mock embedding 客户端。"""
    client = MagicMock()

    def _create_embeddings(model, input, dimensions=None):
        resp = MagicMock()
        texts = [input] if isinstance(input, str) else list(input)
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
def env_and_mock():
    """设置 embedding 环境变量并 mock 客户端。"""
    env_vars = {
        "EMBEDDING_BASE_URL": "https://fake.api/v4",
        "EMBEDDING_API_KEY": "fake-key",
        "EMBEDDING_MODEL": "test-model",
        "EMBEDDING_DIMENSIONS": str(DIMENSIONS),
    }
    with patch.dict(os.environ, env_vars):
        with patch("events_db._get_embedding_client", return_value=_make_mock_client()):
            yield


@pytest.fixture
def db_path(tmp_path):
    """返回临时数据库路径。"""
    return str(tmp_path / "integration_db")


@pytest.fixture
def db(env_and_mock, db_path):
    """创建真实 EventsDB 实例（mock embedding，真实 LanceDB）。"""
    from events_db import EventsDB
    return EventsDB(db_path=db_path)


# ============================================================
# 1. 完整数据流：写入 → 检索 → 更新 → 再检索
# ============================================================

class TestFullDataFlow:
    """验证 events_db → search_events → update_events_history 三者联动。"""

    def test_write_search_update_search(self, db, db_path, env_and_mock):
        """写入事件 → 向量检索找到 → 更新事件 → 再检索看到更新后的数据。"""
        from search_events import search_vector, search_structural
        from update_events_history import merge_single_event, build_new_event

        today = datetime.now().strftime("%Y-%m-%d")

        # Step 1: 通过 build_new_event 构建新事件
        update_data = {
            "event_id": "evt_flow_001",
            "event_name": "人工智能大模型突破",
            "date": today,
            "title": "GPT-5 发布",
            "url": "https://example.com/gpt5",
            "category": "tech",
            "importance": 8,
            "keywords": ["AI", "GPT"],
            "summary": "AI大模型取得突破性进展",
        }
        new_evt = build_new_event(update_data)

        # Step 2: 写入 LanceDB
        result = db.upsert_event(new_evt)
        assert result == "new"
        assert db.count() == 1

        # Step 3: 向量检索
        vector_results = search_vector(db, "人工智能大模型", limit=5)
        assert len(vector_results) >= 1
        found = vector_results[0]
        assert found["event_id"] == "evt_flow_001"
        assert found["_source"] == "vector"

        # Step 4: 结构化检索
        struct_results = search_structural(db, "tech", days_back=7, limit=10)
        assert len(struct_results) >= 1
        event_ids = [r["event_id"] for r in struct_results]
        assert "evt_flow_001" in event_ids

        # Step 5: 模拟第二天更新
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        existing = db.get_event("evt_flow_001")
        update_day2 = {
            "event_id": "evt_flow_001",
            "event_name": "人工智能大模型突破持续发酵",
            "date": tomorrow,
            "title": "GPT-5 引发行业变革",
            "url": "https://example.com/gpt5-day2",
            "importance": 9,
        }
        merged = merge_single_event(existing, update_day2)

        # Step 6: 写回 LanceDB
        result = db.upsert_event(merged)
        assert result == "updated"
        assert db.count() == 1

        # Step 7: 验证更新后数据
        updated_evt = db.get_event("evt_flow_001")
        assert updated_evt["event_name"] == "人工智能大模型突破持续发酵"
        assert updated_evt["last_seen"] == tomorrow
        assert updated_evt["consecutive_days"] == 2
        assert updated_evt["importance_trend"] == [8, 9]
        assert updated_evt["latest_importance"] == 9
        assert tomorrow in updated_evt["daily_entries"]

    def test_multiple_events_write_and_search(self, db, env_and_mock):
        """批量写入多个事件，验证各种检索方式都能正确返回。"""
        from search_events import search_combined

        today = datetime.now().strftime("%Y-%m-%d")
        events = [
            {
                "event_id": "evt_multi_001",
                "event_name": "量子计算新突破",
                "category": "tech",
                "first_seen": today,
                "last_seen": today,
                "consecutive_days": 1,
                "latest_importance": 7,
                "importance_trend": [7],
                "daily_entries": {today: {"title": "量子计算", "url": ""}},
                "keywords": ["量子"],
                "summary": "",
                "related_events": [],
            },
            {
                "event_id": "evt_multi_002",
                "event_name": "全球气候峰会召开",
                "category": "headline",
                "first_seen": today,
                "last_seen": today,
                "consecutive_days": 1,
                "latest_importance": 8,
                "importance_trend": [8],
                "daily_entries": {today: {"title": "气候峰会", "url": ""}},
                "keywords": ["气候"],
                "summary": "",
                "related_events": [],
            },
            {
                "event_id": "evt_multi_003",
                "event_name": "股市大幅波动",
                "category": "finance",
                "first_seen": today,
                "last_seen": today,
                "consecutive_days": 1,
                "latest_importance": 6,
                "importance_trend": [6],
                "daily_entries": {today: {"title": "股市波动", "url": ""}},
                "keywords": ["股市"],
                "summary": "",
                "related_events": [],
            },
        ]
        stats = db.batch_upsert(events)
        assert stats["new"] == 3
        assert db.count() == 3

        # 按类别检索只返回对应类别
        tech_results = db.search_by_category("tech", days_back=7)
        assert all(r["category"] == "tech" for r in tech_results)

        # combined 检索
        combined = search_combined(
            db, "量子计算", category="tech", days_back=7, limit=10
        )
        event_ids = [r["event_id"] for r in combined]
        # 不应有重复
        assert len(event_ids) == len(set(event_ids))


# ============================================================
# 2. update_events_history.process_updates 与真实 LanceDB
# ============================================================

class TestProcessUpdatesIntegration:
    """用真实 LanceDB 测试 process_updates 的完整流程。"""

    def test_process_new_events(self, db, env_and_mock):
        """process_updates 写入全新事件到真实 LanceDB。"""
        from update_events_history import process_updates

        updates = [
            {
                "event_id": "evt_proc_001",
                "event_name": "新闻事件A",
                "date": "2026-03-19",
                "title": "标题A",
                "url": "https://a.com",
                "category": "tech",
                "importance": 7,
            },
            {
                "event_id": "evt_proc_002",
                "event_name": "新闻事件B",
                "date": "2026-03-19",
                "title": "标题B",
                "url": "https://b.com",
                "category": "headline",
                "importance": 8,
            },
        ]
        stats = process_updates(db, updates)
        assert stats["new"] == 2
        assert stats["updated"] == 0
        assert stats["total"] == 2
        assert db.count() == 2

        evt_a = db.get_event("evt_proc_001")
        assert evt_a is not None
        assert evt_a["event_name"] == "新闻事件A"
        assert evt_a["category"] == "tech"

    def test_process_updates_existing(self, db, env_and_mock):
        """process_updates 更新已有事件，验证合并逻辑。"""
        from update_events_history import process_updates

        # 先写入一个事件
        initial_update = [{
            "event_id": "evt_upd_001",
            "event_name": "持续性事件",
            "date": "2026-03-17",
            "title": "第一天",
            "url": "",
            "category": "tech",
            "importance": 5,
        }]
        process_updates(db, initial_update)
        assert db.count() == 1

        # 第二天更新
        day2_update = [{
            "event_id": "evt_upd_001",
            "event_name": "持续性事件第二天",
            "date": "2026-03-18",
            "title": "第二天标题",
            "url": "https://day2.com",
            "importance": 7,
        }]
        stats = process_updates(db, day2_update)
        assert stats["updated"] == 1
        assert stats["new"] == 0
        assert db.count() == 1

        evt = db.get_event("evt_upd_001")
        assert evt["last_seen"] == "2026-03-18"
        assert evt["consecutive_days"] == 2
        assert evt["importance_trend"] == [5, 7]
        assert "2026-03-17" in evt["daily_entries"]
        assert "2026-03-18" in evt["daily_entries"]

    def test_multi_day_consecutive_accumulation(self, db, env_and_mock):
        """多天连续更新 → consecutive_days 逐步累加。"""
        from update_events_history import process_updates

        dates = ["2026-03-15", "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19"]
        for i, date in enumerate(dates):
            updates = [{
                "event_id": "evt_consec",
                "event_name": "连续事件",
                "date": date,
                "title": f"Day {i+1}",
                "url": "",
                "importance": 5 + i,
            }]
            process_updates(db, updates)

        evt = db.get_event("evt_consec")
        assert evt["consecutive_days"] == 5
        assert evt["last_seen"] == "2026-03-19"
        assert evt["importance_trend"] == [5, 6, 7, 8, 9]
        assert len(evt["daily_entries"]) == 5

    def test_gap_resets_consecutive_days(self, db, env_and_mock):
        """间隔 > 2 天后 consecutive_days 重置为 1。"""
        from update_events_history import process_updates

        # 连续 3 天
        for date in ["2026-03-10", "2026-03-11", "2026-03-12"]:
            process_updates(db, [{
                "event_id": "evt_gap",
                "event_name": "gap事件",
                "date": date,
                "title": "t",
                "url": "",
                "importance": 5,
            }])

        evt = db.get_event("evt_gap")
        assert evt["consecutive_days"] == 3

        # 间隔 5 天后再次出现
        process_updates(db, [{
            "event_id": "evt_gap",
            "event_name": "gap事件回归",
            "date": "2026-03-17",
            "title": "回归",
            "url": "",
            "importance": 8,
        }])

        evt = db.get_event("evt_gap")
        assert evt["consecutive_days"] == 1
        assert evt["last_seen"] == "2026-03-17"


# ============================================================
# 3. 重复 upsert 同一 event_id
# ============================================================

class TestDuplicateUpsert:
    """测试重复写入同一 event_id 的行为。"""

    def test_repeated_upsert_keeps_single_record(self, db, env_and_mock):
        """多次 upsert 同一 event_id → 数据库只有一条记录。"""
        event = {
            "event_id": "evt_dup",
            "event_name": "重复事件",
            "category": "tech",
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [5],
            "daily_entries": {},
            "keywords": [],
            "summary": "",
            "related_events": [],
        }
        for i in range(5):
            event_copy = dict(event)
            event_copy["summary"] = f"第{i+1}次写入"
            db.upsert_event(event_copy)

        assert db.count() == 1
        evt = db.get_event("evt_dup")
        assert evt["summary"] == "第5次写入"

    def test_batch_upsert_with_duplicate_ids_in_same_batch(self, db, env_and_mock):
        """同一批次中有重复 event_id 时的行为。"""
        events = [
            {
                "event_id": "evt_batch_dup",
                "event_name": "版本1",
                "category": "tech",
                "first_seen": "2026-03-18",
                "last_seen": "2026-03-18",
                "consecutive_days": 1,
                "latest_importance": 5,
                "importance_trend": [5],
                "daily_entries": {},
                "keywords": [],
                "summary": "第一条",
                "related_events": [],
            },
            {
                "event_id": "evt_batch_dup",
                "event_name": "版本2",
                "category": "tech",
                "first_seen": "2026-03-18",
                "last_seen": "2026-03-18",
                "consecutive_days": 1,
                "latest_importance": 7,
                "importance_trend": [7],
                "daily_entries": {},
                "keywords": [],
                "summary": "第二条",
                "related_events": [],
            },
        ]
        # batch_upsert 内部用 merge_insert，重复 ID 应以最后一条为准
        db.batch_upsert(events)
        assert db.count() == 1


# ============================================================
# 4. 边界/异常场景
# ============================================================

class TestEdgeCases:
    """边界和异常场景测试。"""

    def test_empty_db_search_similar(self, db, env_and_mock):
        """空数据库上执行向量检索 → 返回空列表，不崩溃。"""
        results = db.search_similar("随便什么", limit=10)
        assert results == []

    def test_empty_db_search_by_category(self, db, env_and_mock):
        """空数据库上执行结构化检索 → 返回空列表。"""
        results = db.search_by_category("tech", days_back=7)
        assert results == []

    def test_empty_db_list_events(self, db, env_and_mock):
        """空数据库上 list_events → 空列表。"""
        results = db.list_events()
        assert results == []

    def test_empty_db_count(self, db, env_and_mock):
        """空数据库 count → 0。"""
        assert db.count() == 0

    def test_event_with_empty_event_name(self, db, env_and_mock):
        """event_name 为空时应使用零向量，不崩溃。"""
        event = {
            "event_id": "evt_empty_name",
            "event_name": "",
            "category": "other",
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [5],
            "daily_entries": {},
            "keywords": [],
            "summary": "",
            "related_events": [],
        }
        result = db.upsert_event(event)
        assert result == "new"
        evt = db.get_event("evt_empty_name")
        assert evt is not None
        assert evt["event_name"] == ""

    def test_event_with_special_characters(self, db, env_and_mock):
        """事件名包含特殊字符（中文标点、引号、换行等）。"""
        event = {
            "event_id": "evt_special",
            "event_name": '特殊字符：引号"\'换行\n制表\t斜杠/反斜杠\\',
            "category": "other",
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [5],
            "daily_entries": {},
            "keywords": ["特殊"],
            "summary": "包含特殊字符",
            "related_events": [],
        }
        db.upsert_event(event)
        evt = db.get_event("evt_special")
        assert evt is not None
        assert "特殊字符" in evt["event_name"]

    def test_event_with_unicode_emoji(self, db, env_and_mock):
        """事件名包含 emoji 等多字节 Unicode。"""
        event = {
            "event_id": "evt_emoji",
            "event_name": "测试事件🔥🚀✨",
            "category": "other",
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [5],
            "daily_entries": {},
            "keywords": [],
            "summary": "",
            "related_events": [],
        }
        db.upsert_event(event)
        evt = db.get_event("evt_emoji")
        assert evt is not None
        assert "🔥" in evt["event_name"]

    def test_nonexistent_category_search(self, db, env_and_mock):
        """搜索不存在的 category → 返回空列表。"""
        today = datetime.now().strftime("%Y-%m-%d")
        db.upsert_event({
            "event_id": "evt_cat",
            "event_name": "测试",
            "category": "tech",
            "first_seen": today,
            "last_seen": today,
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [5],
            "daily_entries": {},
            "keywords": [],
            "summary": "",
            "related_events": [],
        })
        results = db.search_by_category("nonexistent_category", days_back=30)
        assert results == []

    def test_large_daily_entries(self, db, env_and_mock):
        """daily_entries 包含大量条目（模拟长期追踪事件）。"""
        from datetime import datetime, timedelta
        daily = {}
        base = datetime(2026, 1, 1)
        for i in range(60):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            daily[d] = {"title": f"Day {i+1} 标题", "url": f"https://example.com/{i}"}

        event = {
            "event_id": "evt_large_daily",
            "event_name": "长期追踪事件",
            "category": "headline",
            "first_seen": "2026-01-01",
            "last_seen": "2026-03-01",
            "consecutive_days": 60,
            "latest_importance": 8,
            "importance_trend": list(range(1, 61)),
            "daily_entries": daily,
            "keywords": ["长期"],
            "summary": "长期事件",
            "related_events": [],
        }
        db.upsert_event(event)
        evt = db.get_event("evt_large_daily")
        assert len(evt["daily_entries"]) == 60
        assert len(evt["importance_trend"]) == 60

    def test_json_field_with_empty_string_values(self, db, env_and_mock):
        """JSON 字段为空字符串而非正常 JSON 时的处理。"""
        event = {
            "event_id": "evt_empty_json",
            "event_name": "空JSON字段",
            "category": "other",
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [],
            "daily_entries": {},
            "keywords": [],
            "summary": "",
            "related_events": [],
        }
        db.upsert_event(event)
        evt = db.get_event("evt_empty_json")
        assert evt["keywords"] == []
        assert evt["daily_entries"] == {}
        assert evt["importance_trend"] == []
        assert evt["related_events"] == []


# ============================================================
# 5. search_events 的 combined 模式去重（真实 LanceDB）
# ============================================================

class TestCombinedDedup:
    """用真实 LanceDB 验证 combined 检索的合并去重。"""

    def test_combined_no_duplicate_event_ids(self, db, env_and_mock):
        """combined 模式下同一事件不应出现两次。"""
        from search_events import search_combined

        today = datetime.now().strftime("%Y-%m-%d")
        # 写入同类别多个事件
        for i in range(5):
            db.upsert_event({
                "event_id": f"evt_comb_{i:03d}",
                "event_name": f"科技事件{i}",
                "category": "tech",
                "first_seen": today,
                "last_seen": today,
                "consecutive_days": 1,
                "latest_importance": 5 + i,
                "importance_trend": [5 + i],
                "daily_entries": {},
                "keywords": [],
                "summary": "",
                "related_events": [],
            })

        results = search_combined(
            db, "科技事件", category="tech", days_back=7, limit=10
        )
        event_ids = [r["event_id"] for r in results]
        # 验证无重复
        assert len(event_ids) == len(set(event_ids))
        # 验证有结果
        assert len(results) > 0

    def test_combined_vector_results_have_priority(self, db, env_and_mock):
        """combined 模式下向量结果排在前面。"""
        from search_events import search_combined

        today = datetime.now().strftime("%Y-%m-%d")
        for i in range(3):
            db.upsert_event({
                "event_id": f"evt_prio_{i:03d}",
                "event_name": f"优先级事件{i}",
                "category": "tech",
                "first_seen": today,
                "last_seen": today,
                "consecutive_days": 1,
                "latest_importance": 5,
                "importance_trend": [5],
                "daily_entries": {},
                "keywords": [],
                "summary": "",
                "related_events": [],
            })

        results = search_combined(
            db, "优先级事件", category="tech", days_back=7, limit=10
        )
        # 前面的结果应来自向量检索
        if results:
            assert results[0]["_source"] == "vector"


# ============================================================
# 6. merge_single_event 的 daily_entries 字符串处理
# ============================================================

class TestMergeEdgeCases:
    """merge_single_event 的边界情况。"""

    def test_daily_entries_as_json_string(self):
        """existing 的 daily_entries 是 JSON 字符串（从 LanceDB 反序列化可能出现）。"""
        from update_events_history import merge_single_event

        existing = {
            "event_id": "evt_str",
            "event_name": "测试",
            "first_seen": "2026-03-17",
            "last_seen": "2026-03-17",
            "consecutive_days": 1,
            "daily_entries": json.dumps({"2026-03-17": {"title": "旧", "url": ""}}),
            "importance_trend": json.dumps([5]),
            "latest_importance": 5,
            "keywords": [],
            "summary": "",
            "related_events": [],
            "category": "tech",
        }
        update = {
            "event_id": "evt_str",
            "date": "2026-03-18",
            "title": "新标题",
            "url": "",
            "importance": 7,
        }
        result = merge_single_event(existing, update)
        assert isinstance(result["daily_entries"], dict)
        assert "2026-03-17" in result["daily_entries"]
        assert "2026-03-18" in result["daily_entries"]
        assert isinstance(result["importance_trend"], list)
        assert result["importance_trend"] == [5, 7]

    def test_daily_entries_invalid_json_string(self):
        """daily_entries 是无效的 JSON 字符串 → 应降级为空 dict。"""
        from update_events_history import merge_single_event

        existing = {
            "event_id": "evt_bad",
            "event_name": "测试",
            "first_seen": "2026-03-17",
            "last_seen": "2026-03-17",
            "consecutive_days": 1,
            "daily_entries": "not valid json",
            "importance_trend": "also invalid",
            "latest_importance": 5,
            "keywords": [],
            "summary": "",
            "related_events": [],
            "category": "tech",
        }
        update = {
            "event_id": "evt_bad",
            "date": "2026-03-18",
            "title": "新标题",
            "url": "",
            "importance": 7,
        }
        result = merge_single_event(existing, update)
        assert isinstance(result["daily_entries"], dict)
        assert "2026-03-18" in result["daily_entries"]

    def test_related_events_update(self):
        """更新数据中包含 related_events 时应合并去重。"""
        from update_events_history import merge_single_event

        existing = {
            "event_id": "evt_rel",
            "event_name": "测试",
            "first_seen": "2026-03-17",
            "last_seen": "2026-03-17",
            "consecutive_days": 1,
            "daily_entries": {},
            "importance_trend": [5],
            "latest_importance": 5,
            "keywords": [],
            "summary": "",
            "related_events": ["evt_old_rel"],
            "category": "tech",
        }
        update = {
            "event_id": "evt_rel",
            "date": "2026-03-18",
            "title": "新标题",
            "url": "",
            "importance": 7,
            "related_events": ["evt_new_rel_1", "evt_new_rel_2"],
        }
        result = merge_single_event(existing, update)
        assert result["related_events"] == ["evt_old_rel", "evt_new_rel_1", "evt_new_rel_2"]
