"""search_batch _has_history 标记的交叉测试。

Mock 审计:
- EventsDB (MagicMock): mock 整个 db 实例。
  - embed_batch: 返回假向量列表（与输入长度一致）。
    真实行为：调用 embedding API 批量计算向量。
    Mock 差异：跳过 API 调用，直接返回固定向量。风险低。
  - search_similar_by_vector: 返回构造的候选事件列表。
    真实行为：在 LanceDB 中做向量检索，返回带 _distance 的事件 dict。
    Mock 差异：不验证向量相似度计算。风险中。
    缓解：重点测试 _has_history 判断逻辑，不测向量检索质量。
- date: 不 mock date.today()，而是使用真实的 today 构建测试数据，
  确保 first_seen 的比较逻辑在真实日期下正确。

测试要点:
- 有历史事件（first_seen < today）时 _has_history 为 True
- 全新事件（无匹配 或 first_seen == today）时 _has_history 为 False
- candidates 数组内容与之前的 hits 一致
- 混合场景（部分新、部分旧）
- 返回结构从 {id: [hits]} 变为 {id: {candidates: [hits], _has_history: bool}}
"""

import json
import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from search_events import search_batch

DIMENSIONS = 8
TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
OLD_DATE = (date.today() - timedelta(days=30)).isoformat()


def _make_hit(event_id: str, first_seen: str, distance: float = 0.1) -> dict:
    """构造一个候选事件 hit。"""
    return {
        "event_id": event_id,
        "event_name": f"事件_{event_id}",
        "summary": "摘要",
        "keywords": ["kw"],
        "category": "tech",
        "first_seen": first_seen,
        "last_seen": first_seen,
        "consecutive_days": 1,
        "latest_importance": 5,
        "importance_trend": [5],
        "daily_entries": {},
        "related_events": [],
        "_distance": distance,
    }


def _setup_batch_test(tmp_path, queries, hits_per_query):
    """公共设置：写查询文件，创建 mock db，返回 (db, queries_file)。

    Args:
        queries: [{"id": ..., "query": ...}, ...]
        hits_per_query: [[hit, hit, ...], [hit, ...], ...]  每个查询对应的返回列表
    """
    queries_file = str(tmp_path / "queries.json")
    with open(queries_file, "w", encoding="utf-8") as f:
        json.dump(queries, f, ensure_ascii=False)

    db = MagicMock()
    db.embed_batch.return_value = [[0.1] * DIMENSIONS] * len(queries)
    db.search_similar_by_vector.side_effect = hits_per_query

    return db, queries_file


class TestSearchBatchHasHistory:
    """测试 search_batch 返回的 _has_history 标记。"""

    def test_has_history_true_when_old_events(self, tmp_path):
        """候选事件中有 first_seen < today → _has_history = True。"""
        queries = [{"id": "q1", "query": "历史事件查询"}]
        hits = [
            [
                _make_hit("evt_old", OLD_DATE),       # first_seen < today
                _make_hit("evt_today", TODAY),          # first_seen == today
            ]
        ]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        assert "q1" in results
        assert results["q1"]["_has_history"] is True
        assert len(results["q1"]["candidates"]) == 2

    def test_has_history_true_when_yesterday(self, tmp_path):
        """候选事件 first_seen = yesterday → _has_history = True。"""
        queries = [{"id": "q1", "query": "昨天的事件"}]
        hits = [[_make_hit("evt_yday", YESTERDAY)]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        assert results["q1"]["_has_history"] is True

    def test_has_history_false_when_all_today(self, tmp_path):
        """所有候选事件的 first_seen == today → _has_history = False。"""
        queries = [{"id": "q1", "query": "全新事件查询"}]
        hits = [
            [
                _make_hit("evt_new1", TODAY),
                _make_hit("evt_new2", TODAY),
            ]
        ]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        assert results["q1"]["_has_history"] is False

    def test_has_history_false_when_no_hits(self, tmp_path):
        """无候选事件 → _has_history = False。"""
        queries = [{"id": "q1", "query": "无结果查询"}]
        hits = [[]]  # 空结果
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        assert results["q1"]["_has_history"] is False
        assert results["q1"]["candidates"] == []


class TestSearchBatchReturnStructure:
    """测试 search_batch 返回结构的正确性。"""

    def test_return_structure_has_candidates_and_history(self, tmp_path):
        """每个 id 的返回值必须包含 candidates 和 _has_history 两个 key。"""
        queries = [
            {"id": "q1", "query": "查询1"},
            {"id": "q2", "query": "查询2"},
        ]
        hits = [
            [_make_hit("evt_1", OLD_DATE)],
            [_make_hit("evt_2", TODAY)],
        ]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        for qid in ["q1", "q2"]:
            assert qid in results
            assert "candidates" in results[qid]
            assert "_has_history" in results[qid]
            assert isinstance(results[qid]["candidates"], list)
            assert isinstance(results[qid]["_has_history"], bool)

    def test_candidates_have_source_tag(self, tmp_path):
        """candidates 中每个 hit 都应有 _source='vector' 标记。"""
        queries = [{"id": "q1", "query": "tagged"}]
        hits = [[_make_hit("evt_1", OLD_DATE)]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        for h in results["q1"]["candidates"]:
            assert h["_source"] == "vector"

    def test_candidates_content_matches_hits(self, tmp_path):
        """candidates 的内容应与 search_similar_by_vector 返回的 hits 一致。"""
        queries = [{"id": "q1", "query": "内容校验"}]
        original_hits = [
            _make_hit("evt_a", OLD_DATE, distance=0.05),
            _make_hit("evt_b", YESTERDAY, distance=0.15),
        ]
        db, qfile = _setup_batch_test(tmp_path, queries, [original_hits])
        results = search_batch(db, qfile, limit=5)

        candidates = results["q1"]["candidates"]
        assert len(candidates) == 2
        assert candidates[0]["event_id"] == "evt_a"
        assert candidates[1]["event_id"] == "evt_b"
        assert candidates[0]["_distance"] == 0.05
        assert candidates[1]["_distance"] == 0.15

    def test_no_extra_keys_in_result(self, tmp_path):
        """返回结构只包含 candidates 和 _has_history，没有多余 key。"""
        queries = [{"id": "q1", "query": "test"}]
        hits = [[_make_hit("evt_1", TODAY)]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        assert set(results["q1"].keys()) == {"candidates", "_has_history"}


class TestSearchBatchMixedScenarios:
    """混合场景：多个查询中部分有历史、部分全新。"""

    def test_mixed_history_and_new(self, tmp_path):
        """多查询混合场景：q1 有历史，q2 全新，q3 无结果。"""
        queries = [
            {"id": "q_old", "query": "历史事件"},
            {"id": "q_new", "query": "全新事件"},
            {"id": "q_empty", "query": "无结果"},
        ]
        hits = [
            [_make_hit("evt_old", OLD_DATE)],    # 有历史
            [_make_hit("evt_new", TODAY)],         # 全新
            [],                                     # 无结果
        ]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        assert results["q_old"]["_has_history"] is True
        assert results["q_new"]["_has_history"] is False
        assert results["q_empty"]["_has_history"] is False

    def test_single_old_among_many_new(self, tmp_path):
        """候选列表中只有 1 个旧事件，其余全新 → _has_history = True。"""
        queries = [{"id": "q1", "query": "混合"}]
        hits = [[
            _make_hit("evt_new1", TODAY),
            _make_hit("evt_new2", TODAY),
            _make_hit("evt_old", OLD_DATE),  # 唯一的旧事件
            _make_hit("evt_new3", TODAY),
        ]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=10)

        assert results["q1"]["_has_history"] is True
        assert len(results["q1"]["candidates"]) == 4

    def test_multiple_queries_independent(self, tmp_path):
        """每个查询的 _has_history 独立判断，互不影响。"""
        queries = [
            {"id": "q1", "query": "a"},
            {"id": "q2", "query": "b"},
        ]
        hits = [
            [_make_hit("evt_old", OLD_DATE)],   # q1 有历史
            [_make_hit("evt_new", TODAY)],        # q2 全新
        ]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        # q1 有历史不影响 q2
        assert results["q1"]["_has_history"] is True
        assert results["q2"]["_has_history"] is False


class TestSearchBatchEdgeCases:
    """边界和异常场景。"""

    def test_first_seen_empty_string_treated_as_history(self, tmp_path):
        """first_seen 为空字符串时，'' < today 为 True，所以算有历史。

        这是代码的当前行为。空 first_seen 被 .get("first_seen", "")
        获取为 ""，而 "" < "2026-03-20" 在字符串比较中为 True。
        """
        queries = [{"id": "q1", "query": "empty first_seen"}]
        hits = [[_make_hit("evt_empty_fs", "")]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        # "" < any ISO date string is True
        assert results["q1"]["_has_history"] is True

    def test_first_seen_missing_key(self, tmp_path):
        """hit 缺少 first_seen 字段时，get 返回 ''，'' < today 为 True。"""
        queries = [{"id": "q1", "query": "no first_seen"}]
        hit_no_fs = {
            "event_id": "evt_no_fs",
            "event_name": "无 first_seen",
            "_distance": 0.1,
        }
        db, qfile = _setup_batch_test(tmp_path, queries, [[hit_no_fs]])
        results = search_batch(db, qfile, limit=5)

        # h.get("first_seen", "") → "" < today → True
        assert results["q1"]["_has_history"] is True

    def test_embed_batch_called_with_all_queries(self, tmp_path):
        """验证 embed_batch 被传入所有查询文本。"""
        queries = [
            {"id": "q1", "query": "查询A"},
            {"id": "q2", "query": "查询B"},
            {"id": "q3", "query": "查询C"},
        ]
        db, qfile = _setup_batch_test(
            tmp_path, queries, [[], [], []]
        )
        search_batch(db, qfile, limit=5)

        db.embed_batch.assert_called_once_with(["查询A", "查询B", "查询C"])

    def test_search_by_vector_called_with_correct_params(self, tmp_path):
        """验证 search_similar_by_vector 被正确调用（含 category 和 days_back）。"""
        queries = [{"id": "q1", "query": "test"}]
        db = MagicMock()
        db.embed_batch.return_value = [[0.5] * DIMENSIONS]
        db.search_similar_by_vector.return_value = []

        qfile = str(tmp_path / "q.json")
        with open(qfile, "w") as f:
            json.dump(queries, f)

        search_batch(db, qfile, category="tech", days_back=14, limit=3)

        db.search_similar_by_vector.assert_called_once_with(
            vector=[0.5] * DIMENSIONS,
            category="tech",
            days_back=14,
            limit=3,
        )

    def test_future_first_seen_treated_as_no_history(self, tmp_path):
        """first_seen 为明天的日期 → 不算历史事件。"""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        queries = [{"id": "q1", "query": "future"}]
        hits = [[_make_hit("evt_future", tomorrow)]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        # tomorrow > today → has_history is False
        assert results["q1"]["_has_history"] is False

    def test_first_seen_equals_today_exactly(self, tmp_path):
        """first_seen 恰好等于 today → 不算历史。"""
        queries = [{"id": "q1", "query": "today exact"}]
        hits = [[_make_hit("evt_today", TODAY)]]
        db, qfile = _setup_batch_test(tmp_path, queries, hits)
        results = search_batch(db, qfile, limit=5)

        # today == today → not < today → False
        assert results["q1"]["_has_history"] is False
