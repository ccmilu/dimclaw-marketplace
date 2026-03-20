"""migrate_json_to_lancedb.py 的单元测试。"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from migrate_json_to_lancedb import (
    fill_missing_fields,
    load_json,
    main,
    migrate,
    print_stats,
)


def write_json(path, data):
    """辅助函数：写 JSON 文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_SENTINEL = object()


def _make_old_event(
    event_id="evt_test",
    event_name="测试事件",
    first_seen="2026-03-15",
    last_seen="2026-03-17",
    consecutive_days=3,
    importance_trend=_SENTINEL,
    daily_entries=_SENTINEL,
    category="tech",
):
    """构建旧格式事件记录（缺少新字段）。"""
    return {
        "event_id": event_id,
        "event_name": event_name,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "consecutive_days": consecutive_days,
        "daily_entries": {
            last_seen: {"title": "标题", "url": "https://example.com"}
        } if daily_entries is _SENTINEL else daily_entries,
        "category": category,
        "importance_trend": [5, 6, 7] if importance_trend is _SENTINEL else importance_trend,
    }


# ============================================================
# 1. load_json
# ============================================================

class TestLoadJson:
    def test_loads_valid_array(self, tmp_path):
        path = str(tmp_path / "events.json")
        write_json(path, [{"event_id": "evt_001"}])
        result = load_json(path)
        assert len(result) == 1

    def test_returns_empty_on_missing_file(self):
        result = load_json("/nonexistent/path.json")
        assert result == []

    def test_returns_empty_on_empty_path(self):
        result = load_json("")
        assert result == []

    def test_returns_empty_on_non_array(self, tmp_path):
        path = str(tmp_path / "obj.json")
        with open(path, "w") as f:
            json.dump({"key": "value"}, f)
        result = load_json(path)
        assert result == []

    def test_returns_empty_on_invalid_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("not valid json")
        result = load_json(path)
        assert result == []


# ============================================================
# 2. fill_missing_fields
# ============================================================

class TestFillMissingFields:
    def test_adds_keywords(self):
        evt = _make_old_event()
        result = fill_missing_fields(evt)
        assert result["keywords"] == []

    def test_adds_summary(self):
        evt = _make_old_event()
        result = fill_missing_fields(evt)
        assert result["summary"] == ""

    def test_adds_related_events(self):
        evt = _make_old_event()
        result = fill_missing_fields(evt)
        assert result["related_events"] == []

    def test_adds_latest_importance_from_trend(self):
        evt = _make_old_event(importance_trend=[5, 6, 8])
        result = fill_missing_fields(evt)
        assert result["latest_importance"] == 8

    def test_default_latest_importance_when_empty_trend(self):
        evt = _make_old_event(importance_trend=[])
        result = fill_missing_fields(evt)
        assert result["latest_importance"] == 5

    def test_preserves_existing_keywords(self):
        evt = _make_old_event()
        evt["keywords"] = ["AI", "GPT"]
        result = fill_missing_fields(evt)
        assert result["keywords"] == ["AI", "GPT"]

    def test_preserves_existing_summary(self):
        evt = _make_old_event()
        evt["summary"] = "一个摘要"
        result = fill_missing_fields(evt)
        assert result["summary"] == "一个摘要"

    def test_preserves_existing_latest_importance(self):
        evt = _make_old_event()
        evt["latest_importance"] = 9
        result = fill_missing_fields(evt)
        assert result["latest_importance"] == 9

    def test_migrates_daily_titles_to_daily_entries(self):
        evt = {
            "event_id": "evt_old",
            "event_name": "旧事件",
            "first_seen": "2026-03-15",
            "last_seen": "2026-03-15",
            "consecutive_days": 1,
            "daily_titles": {"2026-03-15": "旧标题"},
            "category": "tech",
            "importance_trend": [5],
        }
        result = fill_missing_fields(evt)
        assert "daily_titles" not in result
        assert result["daily_entries"] == {
            "2026-03-15": {"title": "旧标题", "url": ""}
        }

    def test_does_not_mutate_original(self):
        evt = _make_old_event()
        original_keys = set(evt.keys())
        fill_missing_fields(evt)
        assert set(evt.keys()) == original_keys

    def test_all_required_fields_present(self):
        evt = _make_old_event()
        result = fill_missing_fields(evt)
        required_fields = [
            "event_id", "event_name", "first_seen", "last_seen",
            "consecutive_days", "daily_entries", "category",
            "importance_trend", "keywords", "summary",
            "related_events", "latest_importance",
        ]
        for field in required_fields:
            assert field in result, f"缺少字段: {field}"


# ============================================================
# 3. migrate 函数
# ============================================================

class TestMigrate:
    def test_empty_file_returns_zero_stats(self, tmp_path):
        path = str(tmp_path / "empty.json")
        write_json(path, [])
        stats = migrate(path, str(tmp_path / "db"))
        assert stats["total"] == 0
        assert stats["new"] == 0
        assert stats["updated"] == 0

    def test_missing_file_returns_zero_stats(self, tmp_path):
        stats = migrate(str(tmp_path / "missing.json"), str(tmp_path / "db"))
        assert stats["total"] == 0

    def test_migrate_calls_batch_upsert(self, tmp_path):
        path = str(tmp_path / "events.json")
        write_json(path, [
            _make_old_event(event_id="evt_001"),
            _make_old_event(event_id="evt_002"),
        ])

        mock_db = MagicMock()
        mock_db.batch_upsert.return_value = {"new": 2, "updated": 0}

        mock_events_db_module = MagicMock()
        mock_events_db_module.EventsDB.return_value = mock_db

        import importlib
        import migrate_json_to_lancedb as mod

        with patch.dict("sys.modules", {"events_db": mock_events_db_module}):
            importlib.reload(mod)
            stats = mod.migrate(path, str(tmp_path / "db"))

        assert stats["total"] == 2
        assert stats["new"] == 2
        assert stats["updated"] == 0
        assert stats["elapsed"] >= 0

        # 验证 batch_upsert 被调用，且事件已补充缺失字段
        upserted = mock_db.batch_upsert.call_args[0][0]
        assert len(upserted) == 2
        assert "keywords" in upserted[0]
        assert "summary" in upserted[0]
        assert "related_events" in upserted[0]
        assert "latest_importance" in upserted[0]

        importlib.reload(mod)

    def test_migrate_fills_missing_fields_before_upsert(self, tmp_path):
        path = str(tmp_path / "events.json")
        write_json(path, [
            _make_old_event(event_id="evt_001", importance_trend=[5, 8]),
        ])

        mock_db = MagicMock()
        mock_db.batch_upsert.return_value = {"new": 1, "updated": 0}

        mock_events_db_module = MagicMock()
        mock_events_db_module.EventsDB.return_value = mock_db

        import importlib
        import migrate_json_to_lancedb as mod

        with patch.dict("sys.modules", {"events_db": mock_events_db_module}):
            importlib.reload(mod)
            mod.migrate(path, str(tmp_path / "db"))

        upserted = mock_db.batch_upsert.call_args[0][0]
        evt = upserted[0]
        assert evt["latest_importance"] == 8
        assert evt["keywords"] == []
        assert evt["summary"] == ""
        assert evt["related_events"] == []

        importlib.reload(mod)


# ============================================================
# 4. main CLI
# ============================================================

class TestMainCLI:
    def test_no_events(self, tmp_path, capsys):
        path = str(tmp_path / "empty.json")
        write_json(path, [])
        main(["--input", path])
        captured = capsys.readouterr()
        assert "无事件可迁移" in captured.out

    def test_missing_input_file(self, tmp_path, capsys):
        main(["--input", str(tmp_path / "missing.json")])
        captured = capsys.readouterr()
        assert "无事件可迁移" in captured.out

    def test_successful_migration(self, tmp_path, capsys):
        path = str(tmp_path / "events.json")
        write_json(path, [
            _make_old_event(event_id="evt_001"),
            _make_old_event(event_id="evt_002"),
            _make_old_event(event_id="evt_003"),
        ])

        mock_db = MagicMock()
        mock_db.batch_upsert.return_value = {"new": 3, "updated": 0}

        mock_events_db_module = MagicMock()
        mock_events_db_module.EventsDB.return_value = mock_db

        import importlib
        import migrate_json_to_lancedb as mod

        with patch.dict("sys.modules", {"events_db": mock_events_db_module}):
            importlib.reload(mod)
            mod.main(["--input", path, "--db-path", str(tmp_path / "custom_db")])

        captured = capsys.readouterr()
        assert "迁移完成:" in captured.out
        assert "总事件数:   3" in captured.out
        assert "新增:       3" in captured.out

        mock_events_db_module.EventsDB.assert_called_once_with(
            db_path=str(tmp_path / "custom_db")
        )

        importlib.reload(mod)


# ============================================================
# 5. print_stats
# ============================================================

class TestPrintStats:
    def test_prints_correct_format(self, capsys):
        print_stats({"total": 10, "new": 8, "updated": 2, "elapsed": 1.23})
        captured = capsys.readouterr()
        assert "总事件数:   10" in captured.out
        assert "新增:       8" in captured.out
        assert "更新:       2" in captured.out
        assert "耗时:       1.23 秒" in captured.out
