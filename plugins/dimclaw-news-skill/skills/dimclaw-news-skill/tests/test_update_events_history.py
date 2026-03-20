"""update_events_history.py 的单元测试。

测试策略：
- merge_single_event / build_new_event / process_updates 使用 mock EventsDB
- load_update_file 直接测试文件读写
- main 函数通过 mock 模块级导入来测试 CLI 逻辑
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from update_events_history import (
    MAX_GAP_DAYS,
    build_new_event,
    load_update_file,
    merge_single_event,
    parse_date,
    print_stats,
    process_updates,
    main,
)


def write_json(path, data):
    """辅助函数：写 JSON 文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _make_existing_event(
    event_id="evt_test",
    event_name="测试事件",
    first_seen="2026-03-15",
    last_seen="2026-03-17",
    consecutive_days=3,
    importance_trend=None,
    daily_entries=None,
    category="tech",
    keywords=None,
    summary="",
    related_events=None,
    latest_importance=6,
):
    """辅助函数：构建一个已有事件记录。"""
    return {
        "event_id": event_id,
        "event_name": event_name,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "consecutive_days": consecutive_days,
        "daily_entries": daily_entries or {
            last_seen: {"title": "已有标题", "url": "https://example.com"}
        },
        "category": category,
        "importance_trend": importance_trend or [5, 6],
        "latest_importance": latest_importance,
        "keywords": keywords or ["关键词1"],
        "summary": summary,
        "related_events": related_events or [],
    }


def _make_update(
    event_id="evt_test",
    event_name="测试事件更新",
    date="2026-03-18",
    title="新标题",
    url="https://new.example.com",
    category="tech",
    importance=7,
    keywords=None,
    summary=None,
):
    """辅助函数：构建一条更新数据。"""
    upd = {
        "event_id": event_id,
        "event_name": event_name,
        "date": date,
        "title": title,
        "url": url,
        "category": category,
        "importance": importance,
    }
    if keywords is not None:
        upd["keywords"] = keywords
    if summary is not None:
        upd["summary"] = summary
    return upd


# ============================================================
# 1. parse_date
# ============================================================

class TestParseDate:
    def test_parses_valid_date(self):
        dt = parse_date("2026-03-18")
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 18

    def test_raises_on_invalid_format(self):
        with pytest.raises(ValueError):
            parse_date("invalid-date")


# ============================================================
# 2. load_update_file
# ============================================================

class TestLoadUpdateFile:
    def test_loads_valid_json_array(self, tmp_path):
        path = str(tmp_path / "update.json")
        write_json(path, [{"event_id": "evt_001", "date": "2026-03-18"}])
        result = load_update_file(path)
        assert len(result) == 1
        assert result[0]["event_id"] == "evt_001"

    def test_returns_empty_on_missing_file(self):
        result = load_update_file("/nonexistent/path.json")
        assert result == []

    def test_returns_empty_on_empty_path(self):
        result = load_update_file("")
        assert result == []

    def test_returns_empty_on_none_path(self):
        result = load_update_file(None)
        assert result == []

    def test_returns_empty_on_invalid_json(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("{invalid json content")
        result = load_update_file(path)
        assert result == []

    def test_returns_empty_on_non_array(self, tmp_path):
        path = str(tmp_path / "obj.json")
        with open(path, "w") as f:
            json.dump({"key": "value"}, f)
        result = load_update_file(path)
        assert result == []


# ============================================================
# 3. build_new_event
# ============================================================

class TestBuildNewEvent:
    def test_builds_from_minimal_update(self):
        upd = _make_update()
        evt = build_new_event(upd)

        assert evt["event_id"] == "evt_test"
        assert evt["event_name"] == "测试事件更新"
        assert evt["first_seen"] == "2026-03-18"
        assert evt["last_seen"] == "2026-03-18"
        assert evt["consecutive_days"] == 1
        assert evt["daily_entries"] == {
            "2026-03-18": {
                "title": "新标题",
                "url": "https://new.example.com",
                "summary": "",
                "insights": [],
                "keywords": [],
            }
        }
        assert evt["category"] == "tech"
        assert evt["importance_trend"] == [7]
        assert evt["latest_importance"] == 7
        assert evt["keywords"] == []
        assert evt["summary"] == ""
        assert evt["related_events"] == []

    def test_builds_with_keywords_and_summary(self):
        upd = _make_update(keywords=["AI", "GPT"], summary="一个摘要")
        evt = build_new_event(upd)
        assert evt["keywords"] == ["AI", "GPT"]
        assert evt["summary"] == "一个摘要"

    def test_defaults_importance_to_5(self):
        upd = {"event_id": "evt_x", "date": "2026-03-18"}
        evt = build_new_event(upd)
        assert evt["importance_trend"] == [5]
        assert evt["latest_importance"] == 5

    def test_defaults_category_to_other(self):
        upd = {"event_id": "evt_x", "date": "2026-03-18"}
        evt = build_new_event(upd)
        assert evt["category"] == "other"


# ============================================================
# 4. merge_single_event — 连续性判断
# ============================================================

class TestMergeSingleEvent:
    def test_gap_0_same_day(self):
        """间隔 0 天（同天）→ consecutive_days 不增加。"""
        existing = _make_existing_event(last_seen="2026-03-18", consecutive_days=3)
        upd = _make_update(date="2026-03-18")
        result = merge_single_event(existing, upd)
        assert result["consecutive_days"] == 3

    def test_gap_1_day(self):
        """间隔 1 天 → 视为连续，consecutive_days +1。"""
        existing = _make_existing_event(last_seen="2026-03-17", consecutive_days=3)
        upd = _make_update(date="2026-03-18")
        result = merge_single_event(existing, upd)
        assert result["consecutive_days"] == 4

    def test_gap_2_days(self):
        """间隔 2 天 → 视为连续，consecutive_days +1。"""
        existing = _make_existing_event(last_seen="2026-03-16", consecutive_days=3)
        upd = _make_update(date="2026-03-18")
        result = merge_single_event(existing, upd)
        assert result["consecutive_days"] == 4

    def test_gap_3_days_not_consecutive(self):
        """间隔 3 天 → 不连续，consecutive_days 重置为 1。"""
        existing = _make_existing_event(last_seen="2026-03-15", consecutive_days=3)
        upd = _make_update(date="2026-03-18")
        result = merge_single_event(existing, upd)
        assert result["consecutive_days"] == 1

    def test_negative_gap_no_change(self):
        """更新日期早于 last_seen → 不改变 last_seen 和 consecutive_days。"""
        existing = _make_existing_event(last_seen="2026-03-18", consecutive_days=3)
        upd = _make_update(date="2026-03-16")
        result = merge_single_event(existing, upd)
        assert result["last_seen"] == "2026-03-18"
        assert result["consecutive_days"] == 3

    def test_updates_last_seen(self):
        existing = _make_existing_event(last_seen="2026-03-17")
        upd = _make_update(date="2026-03-18")
        result = merge_single_event(existing, upd)
        assert result["last_seen"] == "2026-03-18"

    def test_appends_daily_entry(self):
        existing = _make_existing_event(
            daily_entries={"2026-03-17": {"title": "旧标题", "url": ""}}
        )
        upd = _make_update(date="2026-03-18", title="新标题", url="https://new.com")
        result = merge_single_event(existing, upd)
        assert "2026-03-17" in result["daily_entries"]
        assert "2026-03-18" in result["daily_entries"]
        assert result["daily_entries"]["2026-03-18"]["title"] == "新标题"

    def test_overwrites_same_day_entry(self):
        existing = _make_existing_event(
            daily_entries={"2026-03-18": {"title": "旧标题", "url": ""}}
        )
        upd = _make_update(date="2026-03-18", title="覆盖标题")
        result = merge_single_event(existing, upd)
        assert result["daily_entries"]["2026-03-18"]["title"] == "覆盖标题"

    def test_appends_importance_trend(self):
        existing = _make_existing_event(importance_trend=[5, 6])
        upd = _make_update(importance=8)
        result = merge_single_event(existing, upd)
        assert result["importance_trend"] == [5, 6, 8]

    def test_updates_latest_importance(self):
        existing = _make_existing_event(latest_importance=6)
        upd = _make_update(importance=9)
        result = merge_single_event(existing, upd)
        assert result["latest_importance"] == 9

    def test_updates_event_name(self):
        existing = _make_existing_event(event_name="旧名称")
        upd = _make_update(event_name="新名称")
        result = merge_single_event(existing, upd)
        assert result["event_name"] == "新名称"

    def test_empty_event_name_keeps_old(self):
        existing = _make_existing_event(event_name="旧名称")
        upd = _make_update(event_name="")
        result = merge_single_event(existing, upd)
        assert result["event_name"] == "旧名称"

    def test_updates_keywords_if_provided(self):
        existing = _make_existing_event(keywords=["旧关键词"])
        upd = _make_update(keywords=["新关键词1", "新关键词2"])
        result = merge_single_event(existing, upd)
        assert result["keywords"] == ["新关键词1", "新关键词2"]

    def test_keeps_keywords_if_not_in_update(self):
        existing = _make_existing_event(keywords=["旧关键词"])
        upd = _make_update()  # 不包含 keywords
        result = merge_single_event(existing, upd)
        assert result["keywords"] == ["旧关键词"]

    def test_updates_summary_if_provided(self):
        existing = _make_existing_event(summary="旧摘要")
        upd = _make_update(summary="新摘要")
        result = merge_single_event(existing, upd)
        assert result["summary"] == "新摘要"

    def test_does_not_mutate_original(self):
        existing = _make_existing_event(importance_trend=[5, 6])
        original_trend = existing["importance_trend"].copy()
        upd = _make_update(importance=8)
        merge_single_event(existing, upd)
        # 原始对象不应被修改
        assert existing["importance_trend"] == original_trend


# ============================================================
# 5. process_updates — 使用 mock EventsDB
# ============================================================

class TestProcessUpdates:
    def _make_mock_db(self, existing_events=None):
        """创建 mock EventsDB 实例。"""
        db = MagicMock()
        existing_map = {}
        if existing_events:
            for evt in existing_events:
                existing_map[evt["event_id"]] = evt

        def mock_get_event(eid):
            return existing_map.get(eid)

        db.get_event.side_effect = mock_get_event
        db.batch_upsert.return_value = {"new": 0, "updated": 0}
        db.count.return_value = len(existing_map)
        return db

    def test_new_events_only(self):
        db = self._make_mock_db()
        updates = [
            _make_update(event_id="evt_001"),
            _make_update(event_id="evt_002"),
        ]
        stats = process_updates(db, updates)
        assert stats["new"] == 2
        assert stats["updated"] == 0
        assert stats["total"] == 2
        db.batch_upsert.assert_called_once()
        upserted = db.batch_upsert.call_args[0][0]
        assert len(upserted) == 2

    def test_update_existing_events(self):
        existing = [
            _make_existing_event(event_id="evt_001"),
            _make_existing_event(event_id="evt_002"),
        ]
        db = self._make_mock_db(existing_events=existing)
        updates = [
            _make_update(event_id="evt_001"),
            _make_update(event_id="evt_002"),
        ]
        stats = process_updates(db, updates)
        assert stats["new"] == 0
        assert stats["updated"] == 2
        assert stats["total"] == 2

    def test_mixed_new_and_existing(self):
        existing = [_make_existing_event(event_id="evt_001")]
        db = self._make_mock_db(existing_events=existing)
        updates = [
            _make_update(event_id="evt_001"),
            _make_update(event_id="evt_new"),
        ]
        stats = process_updates(db, updates)
        assert stats["new"] == 1
        assert stats["updated"] == 1
        assert stats["total"] == 2

    def test_empty_updates(self):
        db = self._make_mock_db()
        stats = process_updates(db, [])
        assert stats["new"] == 0
        assert stats["updated"] == 0
        db.batch_upsert.assert_not_called()

    def test_merged_event_has_correct_fields(self):
        existing = [_make_existing_event(
            event_id="evt_001",
            last_seen="2026-03-17",
            consecutive_days=2,
            importance_trend=[5, 6],
        )]
        db = self._make_mock_db(existing_events=existing)
        updates = [_make_update(
            event_id="evt_001",
            date="2026-03-18",
            importance=8,
            event_name="更新后的名称",
        )]
        process_updates(db, updates)

        upserted = db.batch_upsert.call_args[0][0]
        evt = upserted[0]
        assert evt["last_seen"] == "2026-03-18"
        assert evt["consecutive_days"] == 3
        assert evt["importance_trend"] == [5, 6, 8]
        assert evt["latest_importance"] == 8
        assert evt["event_name"] == "更新后的名称"


# ============================================================
# 6. main CLI 测试
# ============================================================

class TestMainCLI:
    def test_no_update_file(self, tmp_path, capsys):
        """更新文件不存在时直接退出。"""
        main(["--update", str(tmp_path / "nonexistent.json")])
        captured = capsys.readouterr()
        assert "无更新事件" in captured.out

    def test_empty_update_file(self, tmp_path, capsys):
        """更新文件为空数组时直接退出。"""
        path = str(tmp_path / "empty.json")
        write_json(path, [])
        main(["--update", path])
        captured = capsys.readouterr()
        assert "无更新事件" in captured.out

    @patch("update_events_history.EventsDB", create=True)
    def test_cli_with_updates(self, mock_events_db_cls, tmp_path, capsys):
        """正常更新时输出统计信息。"""
        # 准备 mock
        mock_db = MagicMock()
        mock_db.count.return_value = 5
        mock_db.get_event.return_value = None  # 全部是新事件
        mock_db.batch_upsert.return_value = {"new": 2, "updated": 0}

        # 由于 main 中使用延迟导入，我们需要 patch 正确的位置
        update_path = str(tmp_path / "update.json")
        write_json(update_path, [
            _make_update(event_id="evt_001"),
            _make_update(event_id="evt_002"),
        ])

        with patch.dict("sys.modules", {"events_db": MagicMock()}):
            # 重新导入以使 patch 生效
            import importlib
            import update_events_history as ueh
            original_main = ueh.main

            # mock process_updates 和 EventsDB 的导入
            mock_events_db_module = MagicMock()
            mock_events_db_module.EventsDB.return_value = mock_db

            with patch.dict("sys.modules", {"events_db": mock_events_db_module}):
                importlib.reload(ueh)
                ueh.main(["--update", update_path])

            # 恢复模块
            importlib.reload(ueh)

        captured = capsys.readouterr()
        assert "统计:" in captured.out
        assert "历史事件数: 5" in captured.out
        assert "新增事件:   2" in captured.out

    def test_cli_with_db_path(self, tmp_path, capsys):
        """--db-path 参数传递给 EventsDB。"""
        update_path = str(tmp_path / "update.json")
        write_json(update_path, [_make_update(event_id="evt_001")])

        mock_db = MagicMock()
        mock_db.count.return_value = 0
        mock_db.get_event.return_value = None
        mock_db.batch_upsert.return_value = {"new": 1, "updated": 0}

        mock_events_db_module = MagicMock()
        mock_events_db_module.EventsDB.return_value = mock_db

        import importlib
        import update_events_history as ueh

        with patch.dict("sys.modules", {"events_db": mock_events_db_module}):
            importlib.reload(ueh)
            ueh.main([
                "--update", update_path,
                "--db-path", str(tmp_path / "custom_db"),
            ])

        mock_events_db_module.EventsDB.assert_called_once_with(
            db_path=str(tmp_path / "custom_db")
        )

        # 恢复模块
        importlib.reload(ueh)


# ============================================================
# 7. print_stats
# ============================================================

class TestPrintStats:
    def test_prints_correct_format(self, capsys):
        print_stats(10, {"total": 5, "new": 3, "updated": 2})
        captured = capsys.readouterr()
        assert "历史事件数: 10" in captured.out
        assert "更新事件数: 5" in captured.out
        assert "新增事件:   3" in captured.out
        assert "更新事件:   2" in captured.out


# ============================================================
# 8. daily_entries 扩展字段（summary / insights / keywords）
# ============================================================

class TestDailyEntriesExtended:
    """验证 daily_entries 中新增的 summary, insights, keywords 字段。"""

    def test_merge_daily_entries_with_summary_insights(self):
        """合并后 daily_entries 的新条目包含 summary, insights, keywords。"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            daily_entries={"2026-03-17": {"title": "旧标题", "url": ""}},
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "新标题",
            "url": "https://example.com",
            "importance": 7,
            "summary": "今日摘要内容",
            "insights": ["洞察1", "洞察2"],
            "keywords": ["关键词A", "关键词B"],
        }
        result = merge_single_event(existing, upd)
        entry = result["daily_entries"]["2026-03-18"]
        assert entry["summary"] == "今日摘要内容"
        assert entry["insights"] == ["洞察1", "洞察2"]
        assert entry["keywords"] == ["关键词A", "关键词B"]

    def test_build_new_event_daily_entries_extended(self):
        """新建事件的 daily_entries 包含扩展字段。"""
        upd = {
            "event_id": "evt_new_ext",
            "event_name": "新事件",
            "date": "2026-03-18",
            "title": "标题",
            "url": "https://example.com",
            "importance": 8,
            "summary": "新事件摘要",
            "insights": ["insight1"],
            "keywords": ["kw1", "kw2"],
            "category": "tech",
        }
        evt = build_new_event(upd)
        entry = evt["daily_entries"]["2026-03-18"]
        assert entry["summary"] == "新事件摘要"
        assert entry["insights"] == ["insight1"]
        assert entry["keywords"] == ["kw1", "kw2"]

    def test_merge_daily_entries_missing_optional_fields(self):
        """update 数据没有 summary/insights/keywords 时，daily_entries 中有默认值。"""
        existing = _make_existing_event(last_seen="2026-03-17")
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "新标题",
            "url": "",
            "importance": 5,
            # 没有 summary, insights, keywords
        }
        result = merge_single_event(existing, upd)
        entry = result["daily_entries"]["2026-03-18"]
        assert entry["summary"] == ""
        assert entry["insights"] == []
        assert entry["keywords"] == []

    def test_build_new_event_missing_optional_fields(self):
        """build_new_event 没有 summary/insights/keywords 时也有默认值。"""
        upd = {"event_id": "evt_min", "date": "2026-03-18"}
        evt = build_new_event(upd)
        entry = evt["daily_entries"]["2026-03-18"]
        assert entry["summary"] == ""
        assert entry["insights"] == []
        assert entry["keywords"] == []


# ============================================================
# 9. related_events 合并去重
# ============================================================

class TestRelatedEventsMergeDedup:
    """验证 related_events 合并去重逻辑（不再覆盖）。"""

    def test_related_events_merge_dedup(self):
        """已有 ['evt_a']，新增 ['evt_a', 'evt_b'] → ['evt_a', 'evt_b']"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            related_events=["evt_a"],
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "t",
            "url": "",
            "importance": 5,
            "related_events": ["evt_a", "evt_b"],
        }
        result = merge_single_event(existing, upd)
        assert result["related_events"] == ["evt_a", "evt_b"]

    def test_related_events_merge_empty_existing(self):
        """已有为空，新增 ['evt_c'] → ['evt_c']"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            related_events=[],
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "t",
            "url": "",
            "importance": 5,
            "related_events": ["evt_c"],
        }
        result = merge_single_event(existing, upd)
        assert result["related_events"] == ["evt_c"]

    def test_related_events_merge_preserves_order(self):
        """合并去重保持原有顺序：已有在前，新增在后。"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            related_events=["evt_b", "evt_a"],
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "t",
            "url": "",
            "importance": 5,
            "related_events": ["evt_c", "evt_a", "evt_d"],
        }
        result = merge_single_event(existing, upd)
        # evt_b, evt_a 保持原序在前；evt_c, evt_d 按新增顺序追加（evt_a 已去重）
        assert result["related_events"] == ["evt_b", "evt_a", "evt_c", "evt_d"]

    def test_related_events_existing_is_json_string(self):
        """已有是 JSON string '["evt_a"]'（模拟 LanceDB 反序列化不完整），应正确解析后合并。"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            related_events='["evt_a"]',  # JSON string 而非 list
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "t",
            "url": "",
            "importance": 5,
            "related_events": ["evt_a", "evt_b"],
        }
        result = merge_single_event(existing, upd)
        assert result["related_events"] == ["evt_a", "evt_b"]

    def test_related_events_no_update_keeps_existing(self):
        """update 不包含 related_events 时，保持已有值不变。"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            related_events=["evt_x"],
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "t",
            "url": "",
            "importance": 5,
            # 没有 related_events 字段
        }
        result = merge_single_event(existing, upd)
        assert result["related_events"] == ["evt_x"]

    def test_related_events_existing_invalid_json_string(self):
        """已有是无效 JSON 字符串，应降级为空列表后正常合并。"""
        existing = _make_existing_event(
            last_seen="2026-03-17",
            related_events="not valid json",
        )
        upd = {
            "event_id": "evt_test",
            "date": "2026-03-18",
            "title": "t",
            "url": "",
            "importance": 5,
            "related_events": ["evt_new"],
        }
        result = merge_single_event(existing, upd)
        assert result["related_events"] == ["evt_new"]
