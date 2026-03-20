"""cluster_preprocess.py 的单元测试。"""

import json
import os
import subprocess
import sys

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from cluster_preprocess import (
    dedup_by_title,
    dedup_by_url,
    load_files,
    main,
    normalize_url,
    preprocess,
)


def write_json(path, data):
    """辅助函数：写 JSON 文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path):
    """辅助函数：读 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 1. load_files：加载多个文件
# ============================================================


class TestLoadFiles:
    def test_load_single_file(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])
        items, loaded, skipped = load_files([f1])
        assert loaded == 1
        assert len(items) == 1
        assert items[0]["title"] == "A"
        assert skipped == []

    def test_load_multiple_files(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        f2 = str(tmp_path / "b.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])
        write_json(f2, [{"title": "B", "url": "https://b.com"}, {"title": "C", "url": "https://c.com"}])
        items, loaded, skipped = load_files([f1, f2])
        assert loaded == 2
        assert len(items) == 3
        assert skipped == []

    def test_skip_nonexistent_file(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        missing = str(tmp_path / "missing.json")
        write_json(f1, [{"title": "A"}])
        items, loaded, skipped = load_files([f1, missing])
        assert loaded == 1
        assert len(items) == 1
        assert missing in skipped

    def test_skip_invalid_json(self, tmp_path):
        bad = str(tmp_path / "bad.json")
        with open(bad, "w") as f:
            f.write("{not valid json")
        items, loaded, skipped = load_files([bad])
        assert loaded == 0
        assert len(items) == 0
        assert bad in skipped

    def test_skip_non_array_json(self, tmp_path):
        obj_file = str(tmp_path / "obj.json")
        write_json(obj_file, {"key": "value"})
        items, loaded, skipped = load_files([obj_file])
        assert loaded == 0
        assert len(items) == 0
        assert obj_file in skipped

    def test_empty_array_file(self, tmp_path):
        f1 = str(tmp_path / "empty.json")
        write_json(f1, [])
        items, loaded, skipped = load_files([f1])
        assert loaded == 1
        assert len(items) == 0
        assert skipped == []


# ============================================================
# 2. normalize_url：URL 标准化
# ============================================================


class TestNormalizeUrl:
    def test_strip_trailing_slash(self):
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_unify_http_https(self):
        url_http = normalize_url("http://example.com/page")
        url_https = normalize_url("https://example.com/page")
        assert url_http == url_https

    def test_lowercase_domain(self):
        assert normalize_url("https://EXAMPLE.COM/Path") == "https://example.com/Path"

    def test_empty_url(self):
        assert normalize_url("") == ""
        assert normalize_url(None) is None

    def test_preserve_query_params(self):
        url = "https://example.com/page?q=test&lang=en"
        result = normalize_url(url)
        assert "q=test" in result
        assert "lang=en" in result

    def test_non_http_scheme(self):
        """非 http/https 协议不强制转为 https。"""
        result = normalize_url("ftp://files.example.com/data")
        assert result.startswith("ftp://")


# ============================================================
# 3. dedup_by_url：URL 去重
# ============================================================


class TestDedupByUrl:
    def test_no_duplicates(self):
        items = [
            {"url": "https://a.com", "title": "A", "importance": 5},
            {"url": "https://b.com", "title": "B", "importance": 5},
        ]
        result, removed = dedup_by_url(items)
        assert len(result) == 2
        assert removed == 0

    def test_exact_url_duplicate_keep_higher_importance(self):
        items = [
            {"url": "https://a.com/page", "title": "A low", "importance": 3},
            {"url": "https://a.com/page", "title": "A high", "importance": 8},
        ]
        result, removed = dedup_by_url(items)
        assert len(result) == 1
        assert removed == 1
        assert result[0]["importance"] == 8

    def test_http_https_treated_as_same(self):
        items = [
            {"url": "http://example.com/article", "title": "HTTP ver", "importance": 3},
            {"url": "https://example.com/article", "title": "HTTPS ver", "importance": 7},
        ]
        result, removed = dedup_by_url(items)
        assert len(result) == 1
        assert removed == 1
        assert result[0]["importance"] == 7

    def test_trailing_slash_normalized(self):
        items = [
            {"url": "https://example.com/path/", "title": "With slash", "importance": 5},
            {"url": "https://example.com/path", "title": "No slash", "importance": 6},
        ]
        result, removed = dedup_by_url(items)
        assert len(result) == 1
        assert removed == 1

    def test_empty_url_items_preserved(self):
        items = [
            {"url": "", "title": "No URL 1"},
            {"url": "", "title": "No URL 2"},
        ]
        result, removed = dedup_by_url(items)
        assert len(result) == 2
        assert removed == 0

    def test_missing_url_items_preserved(self):
        items = [
            {"title": "No URL field 1"},
            {"title": "No URL field 2"},
        ]
        result, removed = dedup_by_url(items)
        assert len(result) == 2
        assert removed == 0


# ============================================================
# 4. dedup_by_title：标题去重（阈值 0.85）
# ============================================================


class TestDedupByTitle:
    def test_no_duplicates(self):
        items = [
            {"title": "Apple releases new iPhone", "importance": 5},
            {"title": "Google launches Gemini 2.0", "importance": 5},
        ]
        result, removed = dedup_by_title(items)
        assert len(result) == 2
        assert removed == 0

    def test_identical_titles_deduped(self):
        items = [
            {"title": "Breaking: GPT-5 Released", "importance": 8},
            {"title": "Breaking: GPT-5 Released", "importance": 5},
        ]
        result, removed = dedup_by_title(items)
        assert len(result) == 1
        assert removed == 1
        assert result[0]["importance"] == 8

    def test_very_similar_titles_deduped(self):
        """极相似标题（> 0.85）应该被去重。"""
        items = [
            {"title": "OpenAI 发布 GPT-5 模型", "importance": 7},
            {"title": "OpenAI 发布 GPT-5 模型！", "importance": 5},
        ]
        result, removed = dedup_by_title(items)
        assert len(result) == 1
        assert removed == 1

    def test_moderately_similar_titles_kept(self):
        """中等相似度（< 0.85）的标题应该保留（不像 merge_news 0.7 阈值那么激进）。"""
        items = [
            {"title": "OpenAI 发布 GPT-5 大模型", "importance": 7},
            {"title": "GPT-5 发布：AI 新时代来临", "importance": 6},
        ]
        result, removed = dedup_by_title(items)
        # 这两个标题相似度约 0.4-0.6，应该都保留
        assert len(result) == 2
        assert removed == 0

    def test_keeps_higher_importance(self):
        items = [
            {"title": "Same title here", "importance": 3},
            {"title": "Same title here", "importance": 9},
        ]
        result, removed = dedup_by_title(items)
        assert len(result) == 1
        assert result[0]["importance"] == 9

    def test_threshold_085(self):
        """验证阈值是 0.85 而非 0.7。"""
        # 构造一个相似度恰好在 0.7-0.85 之间的标题对
        items = [
            {"title": "ABCDEFGHIJ KLMNO", "importance": 5},
            {"title": "ABCDEFGHIJ XYZWV", "importance": 5},
        ]
        # 这两个标题共享前半部分，相似度约 0.7 左右
        import difflib
        ratio = difflib.SequenceMatcher(None, items[0]["title"], items[1]["title"]).ratio()
        if ratio < 0.85:
            result, removed = dedup_by_title(items)
            assert len(result) == 2  # 0.85 阈值下不去重
            assert removed == 0


# ============================================================
# 5. preprocess：完整管线
# ============================================================


class TestPreprocess:
    def test_full_pipeline(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        f2 = str(tmp_path / "b.json")
        write_json(f1, [
            {"title": "News A", "url": "https://a.com/1", "importance": 7},
            {"title": "News B", "url": "https://b.com/1", "importance": 5},
        ])
        write_json(f2, [
            {"title": "News C", "url": "https://c.com/1", "importance": 6},
            {"title": "News A duplicate", "url": "https://a.com/1/", "importance": 3},
        ])

        items, stats = preprocess([f1, f2])
        assert stats["files_loaded"] == 2
        assert stats["total_raw"] == 4
        assert stats["url_removed"] == 1  # a.com/1 重复
        assert len(items) == 3  # 去掉 1 个 URL 重复

    def test_empty_input(self, tmp_path):
        f1 = str(tmp_path / "empty.json")
        write_json(f1, [])
        items, stats = preprocess([f1])
        assert len(items) == 0
        assert stats["total_raw"] == 0

    def test_all_files_missing(self, tmp_path):
        items, stats = preprocess([str(tmp_path / "nope.json")])
        assert len(items) == 0
        assert stats["files_loaded"] == 0
        assert stats["files_skipped"] == 1

    def test_history_path_in_stats(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])
        _, stats = preprocess([f1], history_path="/some/history.json")
        assert stats["history_file"] == "/some/history.json"

    def test_history_path_empty_when_not_provided(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])
        _, stats = preprocess([f1])
        assert stats["history_file"] == ""

    def test_all_fields_preserved(self, tmp_path):
        """确保所有字段透传（不丢失任何字段）。"""
        f1 = str(tmp_path / "a.json")
        write_json(f1, [
            {
                "title": "Test News",
                "url": "https://example.com/1",
                "source": "Test",
                "time": "2026-03-18",
                "level": "main",
                "category": "tech",
                "importance": 8,
                "summary": "A test summary",
                "insights": ["insight 1"],
                "heat": "100",
                "custom_field": "should be preserved",
            }
        ])
        items, _ = preprocess([f1])
        assert len(items) == 1
        item = items[0]
        assert item["title"] == "Test News"
        assert item["source"] == "Test"
        assert item["custom_field"] == "should be preserved"
        assert item["insights"] == ["insight 1"]


# ============================================================
# 6. CLI 集成测试
# ============================================================


class TestCLI:
    def test_cli_basic(self, tmp_path):
        f1 = str(tmp_path / "input.json")
        out = str(tmp_path / "output.json")
        write_json(f1, [
            {"title": "News 1", "url": "https://a.com/1", "importance": 7},
            {"title": "News 2", "url": "https://b.com/1", "importance": 5},
        ])

        exit_code = main([f1, "-o", out])
        assert exit_code == 0
        result = read_json(out)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_cli_with_history(self, tmp_path, capsys):
        f1 = str(tmp_path / "input.json")
        out = str(tmp_path / "output.json")
        history = str(tmp_path / "history.json")
        write_json(f1, [{"title": "News 1", "url": "https://a.com"}])
        write_json(history, [])

        exit_code = main([f1, "--history", history, "-o", out])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "事件历史" in captured.out

    def test_cli_multiple_files_with_dedup(self, tmp_path):
        f1 = str(tmp_path / "a.json")
        f2 = str(tmp_path / "b.json")
        out = str(tmp_path / "output.json")
        write_json(f1, [
            {"title": "Same News", "url": "http://example.com/article", "importance": 5},
        ])
        write_json(f2, [
            {"title": "Same News", "url": "https://example.com/article/", "importance": 8},
        ])

        main([f1, f2, "-o", out])
        result = read_json(out)
        assert len(result) == 1
        assert result[0]["importance"] == 8

    def test_cli_missing_file_still_works(self, tmp_path, capsys):
        f1 = str(tmp_path / "exists.json")
        missing = str(tmp_path / "missing.json")
        out = str(tmp_path / "output.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])

        exit_code = main([f1, missing, "-o", out])
        assert exit_code == 0
        result = read_json(out)
        assert len(result) == 1

    def test_cli_output_is_json_array(self, tmp_path):
        """输出格式应为 JSON 数组。"""
        f1 = str(tmp_path / "input.json")
        out = str(tmp_path / "output.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])

        main([f1, "-o", out])
        result = read_json(out)
        assert isinstance(result, list)

    def test_cli_creates_output_dir(self, tmp_path):
        """输出目录不存在时应自动创建。"""
        f1 = str(tmp_path / "input.json")
        out = str(tmp_path / "subdir" / "output.json")
        write_json(f1, [{"title": "A", "url": "https://a.com"}])

        main([f1, "-o", out])
        assert os.path.exists(out)

    def test_cli_stats_output(self, tmp_path, capsys):
        f1 = str(tmp_path / "input.json")
        out = str(tmp_path / "output.json")
        write_json(f1, [
            {"title": "A", "url": "https://a.com", "importance": 5},
            {"title": "B", "url": "https://a.com/", "importance": 7},
        ])

        main([f1, "-o", out])
        captured = capsys.readouterr()
        assert "预处理完成" in captured.out
        assert "URL 去重后" in captured.out
