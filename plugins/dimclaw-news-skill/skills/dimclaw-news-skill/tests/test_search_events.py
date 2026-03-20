"""search_events.py 的单元测试。

使用 mock 替代 EventsDB 的真实调用，测试 CLI 脚本的参数解析和检索逻辑。
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from search_events import (
    main,
    search_batch,
    search_combined,
    search_structural,
    search_vector,
)


def _make_sample_events(n=3, category="tech", with_distance=False):
    """生成样本事件列表。"""
    events = []
    for i in range(n):
        evt = {
            "event_id": f"evt_{i:03d}",
            "event_name": f"事件{i}",
            "summary": f"摘要{i}",
            "keywords": ["关键词"],
            "category": category,
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 7,
            "importance_trend": [7],
            "daily_entries": {},
            "related_events": [],
        }
        if with_distance:
            evt["_distance"] = 0.1 * i
        events.append(evt)
    return events


def _make_mock_db():
    """创建 mock 的 EventsDB 实例。"""
    db = MagicMock()
    db.search_similar.return_value = _make_sample_events(
        3, with_distance=True
    )
    db.search_by_category.return_value = _make_sample_events(3)
    return db


# ============================================================
# 1. search_vector
# ============================================================

class TestSearchVector:
    def test_adds_source_tag(self):
        db = _make_mock_db()
        results = search_vector(db, "测试查询", limit=5)
        assert all(r["_source"] == "vector" for r in results)

    def test_passes_params_to_db(self):
        db = _make_mock_db()
        search_vector(
            db, "查询", category="headline", days_back=7, limit=5
        )
        db.search_similar.assert_called_once_with(
            text="查询", category="headline", days_back=7, limit=5
        )


# ============================================================
# 2. search_structural
# ============================================================

class TestSearchStructural:
    def test_adds_source_tag(self):
        db = _make_mock_db()
        results = search_structural(db, "tech", days_back=7, limit=10)
        assert all(r["_source"] == "structural" for r in results)

    def test_passes_params_to_db(self):
        db = _make_mock_db()
        search_structural(db, "headline", days_back=14, limit=20)
        db.search_by_category.assert_called_once_with(
            category="headline", days_back=14, limit=20
        )


# ============================================================
# 3. search_combined
# ============================================================

class TestSearchCombined:
    def test_merges_and_deduplicates(self):
        db = MagicMock()
        # 向量结果和结构化结果有重叠
        vector_events = _make_sample_events(3, with_distance=True)
        structural_events = _make_sample_events(3)  # 相同 event_id
        structural_events.append({
            "event_id": "evt_unique",
            "event_name": "独有事件",
            "summary": "",
            "keywords": [],
            "category": "tech",
            "first_seen": "2026-03-18",
            "last_seen": "2026-03-18",
            "consecutive_days": 1,
            "latest_importance": 5,
            "importance_trend": [5],
            "daily_entries": {},
            "related_events": [],
        })
        db.search_similar.return_value = vector_events
        db.search_by_category.return_value = structural_events

        results = search_combined(
            db, "测试", category="tech", days_back=7, limit=10
        )
        event_ids = [r["event_id"] for r in results]
        # 不应有重复
        assert len(event_ids) == len(set(event_ids))
        # 向量结果在前
        assert results[0]["_source"] == "vector"
        # 独有的结构化结果也在
        assert "evt_unique" in event_ids

    def test_respects_limit(self):
        db = MagicMock()
        db.search_similar.return_value = _make_sample_events(
            10, with_distance=True
        )
        db.search_by_category.return_value = _make_sample_events(10)
        results = search_combined(
            db, "测试", category="tech", days_back=7, limit=5
        )
        assert len(results) <= 5


# ============================================================
# 4. CLI 参数校验
# ============================================================

class TestCliValidation:
    def test_no_args_exits_with_error(self, capsys):
        """无参数应报错退出。"""
        with patch("search_events.EventsDB"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0

    def test_combined_without_query_exits(self, capsys, monkeypatch):
        """--combined 缺 --query 应报错。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--combined", "--category", "tech"]
        )
        with patch("search_events.EventsDB"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_combined_without_category_exits(self, capsys, monkeypatch):
        """--combined 缺 --category 应报错。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--combined", "--query", "test"]
        )
        with patch("search_events.EventsDB"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_vector_search_outputs_json(self, capsys, monkeypatch):
        """--query 模式应输出 JSON。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "AI", "--db-path", "/tmp/fake"]
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_structural_search_outputs_json(self, capsys, monkeypatch):
        """--category 模式应输出 JSON。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--category", "tech", "--db-path", "/tmp/fake"]
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)

    def test_combined_search_outputs_json(self, capsys, monkeypatch):
        """--combined 模式应输出 JSON。"""
        monkeypatch.setattr(
            "sys.argv",
            [
                "search_events.py",
                "--query", "AI",
                "--category", "tech",
                "--combined",
                "--db-path", "/tmp/fake",
            ]
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)

    def test_batch_conflicts_with_query(self, capsys, monkeypatch):
        """--batch 与 --query 同时使用应报错。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--batch", "/tmp/q.json", "--query", "test"]
        )
        with patch("search_events.EventsDB"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_batch_conflicts_with_combined(self, capsys, monkeypatch):
        """--batch 与 --combined 同时使用应报错。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--batch", "/tmp/q.json", "--combined",
             "--query", "test", "--category", "tech"]
        )
        with patch("search_events.EventsDB"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


# ============================================================
# 5. search_batch
# ============================================================

class TestSearchBatch:
    def test_calls_embed_batch_and_search_by_vector(self, tmp_path):
        """search_batch 应调用 embed_batch 和 search_similar_by_vector。"""
        queries = [
            {"id": "news_0", "query": "AI 新突破"},
            {"id": "news_1", "query": "股市波动"},
        ]
        queries_file = str(tmp_path / "queries.json")
        with open(queries_file, "w", encoding="utf-8") as f:
            json.dump(queries, f, ensure_ascii=False)

        db = MagicMock()
        fake_vectors = [[0.1] * 8, [0.2] * 8]
        db.embed_batch.return_value = fake_vectors
        db.search_similar_by_vector.return_value = _make_sample_events(
            2, with_distance=True
        )

        results = search_batch(db, queries_file, limit=5)

        # 验证 embed_batch 被调用一次，传入所有 query 文本
        db.embed_batch.assert_called_once_with(
            ["AI 新突破", "股市波动"]
        )
        # 验证 search_similar_by_vector 被调用两次
        assert db.search_similar_by_vector.call_count == 2
        # 结果是 dict，包含两个 key
        assert "news_0" in results
        assert "news_1" in results
        # 每条结果都标记了 _source（新格式：{candidates: [...], _has_history: bool}）
        for entry in results.values():
            assert all(h["_source"] == "vector" for h in entry["candidates"])

    def test_passes_category_and_days_back(self, tmp_path):
        """search_batch 应将 category 和 days_back 传给 search_similar_by_vector。"""
        queries = [{"id": "n0", "query": "test"}]
        queries_file = str(tmp_path / "q.json")
        with open(queries_file, "w", encoding="utf-8") as f:
            json.dump(queries, f, ensure_ascii=False)

        db = MagicMock()
        db.embed_batch.return_value = [[0.1] * 8]
        db.search_similar_by_vector.return_value = []

        search_batch(db, queries_file, category="tech", days_back=14, limit=3)

        db.search_similar_by_vector.assert_called_once_with(
            vector=[0.1] * 8, category="tech", days_back=14, limit=3
        )

    def test_batch_cli_outputs_json_dict(self, capsys, monkeypatch, tmp_path):
        """--batch CLI 模式应输出 JSON 对象（dict）。"""
        queries = [
            {"id": "q0", "query": "测试查询"},
        ]
        queries_file = str(tmp_path / "batch_cli.json")
        with open(queries_file, "w", encoding="utf-8") as f:
            json.dump(queries, f, ensure_ascii=False)

        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--batch", queries_file,
             "--db-path", "/tmp/fake"]
        )
        mock_db = MagicMock()
        mock_db.embed_batch.return_value = [[0.1] * 8]
        mock_db.search_similar_by_vector.return_value = _make_sample_events(
            1, with_distance=True
        )
        with patch("search_events.EventsDB", return_value=mock_db):
            main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, dict)
        assert "q0" in data


# ============================================================
# 6. --output 参数交叉测试（tester 编写）
# ============================================================
#
# Mock 审计:
# - EventsDB: 通过 patch("search_events.EventsDB") mock 整个类，
#   避免 LanceDB + embedding API 连接。返回值使用 _make_sample_events()
#   保持与实现者测试一致的数据结构。
# - 风险: 中。需确保 mock 返回结构与真实 EventsDB 一致。
#   缓解: 复用实现者已有的 _make_sample_events / _make_mock_db。


class TestOutputParam:
    """交叉测试 --output 参数：将 JSON 结果写入文件。"""

    def test_query_output_writes_json_file(self, capsys, monkeypatch, tmp_path):
        """--query + --output 将结果写入指定文件。"""
        output_file = tmp_path / "results.json"
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "测试查询",
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["event_id"] == "evt_000"

    def test_output_file_is_valid_json(self, capsys, monkeypatch, tmp_path):
        """--output 写入的文件必须是合法 JSON。"""
        output_file = tmp_path / "valid.json"
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "验证",
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        content = output_file.read_text(encoding="utf-8")
        parsed = json.loads(content)  # 不应抛异常
        assert parsed is not None

    def test_output_stdout_shows_stats_not_json(self, capsys, monkeypatch, tmp_path):
        """--output 时 stdout 输出统计信息而非 JSON 数据。"""
        output_file = tmp_path / "out.json"
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "测试",
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        captured = capsys.readouterr()
        assert "已写入" in captured.out
        assert "条结果" in captured.out
        # stdout 不应该是 JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.out)

    def test_no_output_prints_json_to_stdout(self, capsys, monkeypatch, tmp_path):
        """不带 --output 时结果输出到 stdout（向后兼容）。"""
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "兼容性测试",
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_batch_with_output(self, capsys, monkeypatch, tmp_path):
        """--batch + --output 组合：结果写入文件，stdout 显示统计。"""
        queries = [
            {"id": "q1", "query": "查询1"},
            {"id": "q2", "query": "查询2"},
        ]
        queries_file = tmp_path / "queries.json"
        queries_file.write_text(
            json.dumps(queries, ensure_ascii=False), encoding="utf-8"
        )
        output_file = tmp_path / "batch_out.json"

        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--batch", str(queries_file),
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = MagicMock()
        mock_db.embed_batch.return_value = [[0.1] * 8, [0.2] * 8]
        mock_db.search_similar_by_vector.return_value = _make_sample_events(
            1, with_distance=True
        )
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        # 文件写入正确
        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "q1" in data
        assert "q2" in data

        # stdout 包含统计信息
        captured = capsys.readouterr()
        assert "已写入" in captured.out
        assert "组" in captured.out

    def test_batch_without_output_to_stdout(self, capsys, monkeypatch, tmp_path):
        """--batch 不带 --output 时输出到 stdout（向后兼容）。"""
        queries = [{"id": "q0", "query": "test"}]
        queries_file = tmp_path / "q.json"
        queries_file.write_text(
            json.dumps(queries, ensure_ascii=False), encoding="utf-8"
        )

        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--batch", str(queries_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = MagicMock()
        mock_db.embed_batch.return_value = [[0.1] * 8]
        mock_db.search_similar_by_vector.return_value = _make_sample_events(
            1, with_distance=True
        )
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, dict)

    def test_structural_with_output(self, capsys, monkeypatch, tmp_path):
        """结构化检索 (--category) + --output 组合。"""
        output_file = tmp_path / "structural.json"
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--category", "headline",
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_output_preserves_chinese(self, capsys, monkeypatch, tmp_path):
        """--output 文件正确保留中文字符（ensure_ascii=False）。"""
        output_file = tmp_path / "chinese.json"
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "中文编码测试",
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        content = output_file.read_text(encoding="utf-8")
        assert "事件" in content
        assert "\\u" not in content  # 中文未被 escape

    def test_combined_with_output(self, capsys, monkeypatch, tmp_path):
        """--combined + --output 组合。"""
        output_file = tmp_path / "combined.json"
        monkeypatch.setattr(
            "sys.argv",
            ["search_events.py", "--query", "AI",
             "--category", "tech", "--combined",
             "--output", str(output_file),
             "--db-path", str(tmp_path / "fake_db")],
        )
        mock_db = _make_mock_db()
        with patch("search_events.EventsDB", return_value=mock_db):
            main()

        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)
