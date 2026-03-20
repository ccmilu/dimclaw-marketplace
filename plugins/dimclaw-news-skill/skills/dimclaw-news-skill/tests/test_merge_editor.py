"""merge_editor_output.py 的交叉测试。

Mock 审计:
- 无外部依赖需要 mock。merge_editor_output.py 只做纯 JSON 操作，
  不依赖数据库、API 或网络。只需要构造 input/editor JSON 文件。

测试要点:
- 正常合并：input + editor → 输出包含所有字段
- editor 缺少必填字段 → 报错退出
- cross_links related_indices 越界 → 报错退出
- overview 类型错误（传数字）→ 报错退出
- 空 main 数组 + 空 cross_links → 正常通过
- 原始 JSON 的 main/brief 内容在输出中完全不变
"""

import json
import os
import sys

import pytest

# 将 scripts 目录加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from merge_editor_output import merge, validate_editor_fields


def _make_input_data(main_count=3):
    """构造原始输入数据。"""
    main_items = []
    for i in range(main_count):
        main_items.append({
            "id": f"news_{i}",
            "title": f"新闻标题{i}",
            "summary": f"新闻摘要{i}",
            "importance": 7 - i,
            "source": f"来源{i}",
        })
    return {
        "date": "2026-03-20",
        "main": main_items,
        "brief": [
            {"title": "简讯1", "summary": "简讯摘要1"},
            {"title": "简讯2", "summary": "简讯摘要2"},
        ],
        "metadata": {"total_sources": 14, "version": "1.0"},
    }


def _make_editor_data(main_count=3):
    """构造合法的 editor 数据。"""
    related_indices = list(range(min(2, main_count)))
    return {
        "overview": "今日要闻概述：AI 技术突破，股市震荡。",
        "cross_links": [
            {
                "theme": "AI 与资本市场",
                "related_indices": related_indices,
                "explanation": "AI 技术发展推动科技股上涨",
            }
        ] if main_count > 0 else [],
        "reading_guide": "建议先阅读第一条了解 AI 进展，再看第二条理解市场反应。",
    }


# ============================================================
# 1. validate_editor_fields 单元测试
# ============================================================

class TestValidateEditorFields:
    """测试 validate_editor_fields 的校验逻辑。"""

    def test_valid_editor_data_no_errors(self):
        """完全合法的 editor 数据 → 无错误。"""
        editor = _make_editor_data(3)
        errors = validate_editor_fields(editor, main_count=3)
        assert errors == []

    def test_missing_overview(self):
        """缺少 overview → 报错。"""
        editor = _make_editor_data(3)
        del editor["overview"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("overview" in e for e in errors)

    def test_missing_cross_links(self):
        """缺少 cross_links → 报错。"""
        editor = _make_editor_data(3)
        del editor["cross_links"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("cross_links" in e for e in errors)

    def test_missing_reading_guide(self):
        """缺少 reading_guide → 报错。"""
        editor = _make_editor_data(3)
        del editor["reading_guide"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("reading_guide" in e for e in errors)

    def test_missing_all_required_fields(self):
        """缺少所有必填字段 → 报 3 个错误。"""
        errors = validate_editor_fields({}, main_count=3)
        assert len(errors) >= 3
        field_names = ["overview", "cross_links", "reading_guide"]
        for fn in field_names:
            assert any(fn in e for e in errors)

    def test_overview_not_string(self):
        """overview 传数字 → 报错。"""
        editor = _make_editor_data(3)
        editor["overview"] = 12345
        errors = validate_editor_fields(editor, main_count=3)
        assert any("overview" in e and "字符串" in e for e in errors)

    def test_overview_is_list(self):
        """overview 传列表 → 报错。"""
        editor = _make_editor_data(3)
        editor["overview"] = ["not", "a", "string"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("overview" in e for e in errors)

    def test_cross_links_not_array(self):
        """cross_links 不是数组 → 报错。"""
        editor = _make_editor_data(3)
        editor["cross_links"] = "not an array"
        errors = validate_editor_fields(editor, main_count=3)
        assert any("cross_links" in e and "数组" in e for e in errors)

    def test_cross_links_item_not_object(self):
        """cross_links[i] 不是对象 → 报错。"""
        editor = _make_editor_data(3)
        editor["cross_links"] = ["not a dict"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("cross_links[0]" in e and "对象" in e for e in errors)

    def test_cross_links_missing_theme(self):
        """cross_links[i] 缺少 theme → 报错。"""
        editor = _make_editor_data(3)
        del editor["cross_links"][0]["theme"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("theme" in e for e in errors)

    def test_cross_links_missing_related_indices(self):
        """cross_links[i] 缺少 related_indices → 报错。"""
        editor = _make_editor_data(3)
        del editor["cross_links"][0]["related_indices"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("related_indices" in e for e in errors)

    def test_cross_links_missing_explanation(self):
        """cross_links[i] 缺少 explanation → 报错。"""
        editor = _make_editor_data(3)
        del editor["cross_links"][0]["explanation"]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("explanation" in e for e in errors)

    def test_related_indices_out_of_range_positive(self):
        """related_indices 包含越界正整数 → 报错。"""
        editor = _make_editor_data(3)
        editor["cross_links"][0]["related_indices"] = [0, 3]  # 3 >= main_count=3
        errors = validate_editor_fields(editor, main_count=3)
        assert any("无效索引" in e for e in errors)

    def test_related_indices_negative(self):
        """related_indices 包含负数 → 报错。"""
        editor = _make_editor_data(3)
        editor["cross_links"][0]["related_indices"] = [0, -1]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("无效索引" in e for e in errors)

    def test_related_indices_not_array(self):
        """related_indices 不是数组 → 报错。"""
        editor = _make_editor_data(3)
        editor["cross_links"][0]["related_indices"] = "not array"
        errors = validate_editor_fields(editor, main_count=3)
        assert any("related_indices" in e and "数组" in e for e in errors)

    def test_related_indices_float(self):
        """related_indices 包含浮点数 → 报错（因为 isinstance(1.5, int) 为 False）。"""
        editor = _make_editor_data(3)
        editor["cross_links"][0]["related_indices"] = [0, 1.5]
        errors = validate_editor_fields(editor, main_count=3)
        assert any("无效索引" in e for e in errors)

    def test_reading_guide_not_string(self):
        """reading_guide 不是字符串 → 报错。"""
        editor = _make_editor_data(3)
        editor["reading_guide"] = 42
        errors = validate_editor_fields(editor, main_count=3)
        assert any("reading_guide" in e for e in errors)

    def test_empty_main_empty_cross_links_ok(self):
        """main 为空 + cross_links 为空 → 正常通过。"""
        editor = {
            "overview": "无主要新闻",
            "cross_links": [],
            "reading_guide": "暂无阅读建议",
        }
        errors = validate_editor_fields(editor, main_count=0)
        assert errors == []

    def test_multiple_cross_links_all_valid(self):
        """多个 cross_links 全部合法 → 无错误。"""
        editor = {
            "overview": "概述",
            "cross_links": [
                {
                    "theme": "主题A",
                    "related_indices": [0, 1],
                    "explanation": "解释A",
                },
                {
                    "theme": "主题B",
                    "related_indices": [2],
                    "explanation": "解释B",
                },
            ],
            "reading_guide": "指南",
        }
        errors = validate_editor_fields(editor, main_count=5)
        assert errors == []

    def test_multiple_cross_links_partial_invalid(self):
        """多个 cross_links 中部分非法 → 只报对应的错误。"""
        editor = {
            "overview": "概述",
            "cross_links": [
                {
                    "theme": "主题A",
                    "related_indices": [0, 1],
                    "explanation": "解释A",
                },
                {
                    "theme": "主题B",
                    "related_indices": [99],  # 越界
                    "explanation": "解释B",
                },
            ],
            "reading_guide": "指南",
        }
        errors = validate_editor_fields(editor, main_count=3)
        # 只有 cross_links[1] 报错，cross_links[0] 正常
        assert any("cross_links[1]" in e for e in errors)
        assert not any("cross_links[0]" in e for e in errors)


# ============================================================
# 2. merge 函数测试
# ============================================================

class TestMerge:
    """测试 merge 函数的合并逻辑。"""

    def test_normal_merge_contains_all_fields(self):
        """正常合并后输出包含原始字段 + editor 字段。"""
        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)
        result = merge(input_data, editor_data)

        # editor 字段存在
        assert "overview" in result
        assert "cross_links" in result
        assert "reading_guide" in result
        # editor 字段值正确
        assert result["overview"] == editor_data["overview"]
        assert result["cross_links"] == editor_data["cross_links"]
        assert result["reading_guide"] == editor_data["reading_guide"]

    def test_original_main_preserved(self):
        """合并后 main 数组内容完全不变。"""
        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)
        original_main = json.dumps(input_data["main"], ensure_ascii=False)

        result = merge(input_data, editor_data)

        result_main = json.dumps(result["main"], ensure_ascii=False)
        assert result_main == original_main

    def test_original_brief_preserved(self):
        """合并后 brief 内容完全不变。"""
        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)
        original_brief = json.dumps(input_data["brief"], ensure_ascii=False)

        result = merge(input_data, editor_data)

        result_brief = json.dumps(result["brief"], ensure_ascii=False)
        assert result_brief == original_brief

    def test_original_metadata_preserved(self):
        """合并后 metadata 等其他字段不变。"""
        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)

        result = merge(input_data, editor_data)

        assert result["date"] == "2026-03-20"
        assert result["metadata"]["total_sources"] == 14

    def test_merge_does_not_mutate_input(self):
        """merge 不应修改原始 input_data。"""
        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)
        input_copy = json.dumps(input_data, ensure_ascii=False)

        merge(input_data, editor_data)

        assert json.dumps(input_data, ensure_ascii=False) == input_copy

    def test_merge_with_empty_main_and_cross_links(self):
        """空 main + 空 cross_links → 正常合并。"""
        input_data = {"date": "2026-03-20", "main": [], "brief": []}
        editor_data = {
            "overview": "无新闻",
            "cross_links": [],
            "reading_guide": "无建议",
        }
        result = merge(input_data, editor_data)
        assert result["overview"] == "无新闻"
        assert result["cross_links"] == []
        assert result["main"] == []

    def test_merge_overwrites_existing_overview(self):
        """如果 input_data 已有 overview，merge 应覆盖。"""
        input_data = _make_input_data(3)
        input_data["overview"] = "旧概述"
        editor_data = _make_editor_data(3)

        result = merge(input_data, editor_data)

        assert result["overview"] == editor_data["overview"]
        assert result["overview"] != "旧概述"


# ============================================================
# 3. CLI 集成测试（通过 subprocess 调用 main）
# ============================================================

class TestMainCLI:
    """测试 merge_editor_output.py 的 CLI 行为。"""

    def test_cli_normal_merge(self, tmp_path):
        """正常调用 CLI → 输出文件包含合并结果。"""
        input_file = tmp_path / "input.json"
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"

        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)

        input_file.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        editor_file.write_text(json.dumps(editor_data, ensure_ascii=False), encoding="utf-8")

        # 模拟 CLI 调用
        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        main()

        assert output_file.exists()
        result = json.loads(output_file.read_text(encoding="utf-8"))
        assert result["overview"] == editor_data["overview"]
        assert result["main"] == input_data["main"]
        assert result["brief"] == input_data["brief"]

    def test_cli_missing_editor_field_exits(self, tmp_path):
        """editor 缺少必填字段 → 退出码 1。"""
        input_file = tmp_path / "input.json"
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"

        input_data = _make_input_data(3)
        editor_data = {"overview": "只有 overview"}  # 缺少 cross_links 和 reading_guide

        input_file.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        editor_file.write_text(json.dumps(editor_data, ensure_ascii=False), encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert not output_file.exists()

    def test_cli_index_out_of_range_exits(self, tmp_path):
        """cross_links 索引越界 → 退出码 1。"""
        input_file = tmp_path / "input.json"
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"

        input_data = _make_input_data(2)  # main 只有 2 条
        editor_data = _make_editor_data(2)
        editor_data["cross_links"] = [{
            "theme": "越界",
            "related_indices": [0, 5],  # 5 >= 2，越界
            "explanation": "测试越界",
        }]

        input_file.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        editor_file.write_text(json.dumps(editor_data, ensure_ascii=False), encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_cli_overview_type_error_exits(self, tmp_path):
        """overview 传数字 → 退出码 1。"""
        input_file = tmp_path / "input.json"
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"

        input_data = _make_input_data(3)
        editor_data = _make_editor_data(3)
        editor_data["overview"] = 99999  # 数字而非字符串

        input_file.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        editor_file.write_text(json.dumps(editor_data, ensure_ascii=False), encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_cli_output_is_valid_json(self, tmp_path):
        """输出文件必须是合法 JSON。"""
        input_file = tmp_path / "input.json"
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"

        input_data = _make_input_data(2)
        editor_data = _make_editor_data(2)

        input_file.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        editor_file.write_text(json.dumps(editor_data, ensure_ascii=False), encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        main()

        content = output_file.read_text(encoding="utf-8")
        result = json.loads(content)  # 不应抛异常
        assert isinstance(result, dict)

    def test_cli_preserves_chinese(self, tmp_path):
        """输出文件正确保留中文（ensure_ascii=False）。"""
        input_file = tmp_path / "input.json"
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"

        input_data = _make_input_data(1)
        editor_data = _make_editor_data(1)

        input_file.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        editor_file.write_text(json.dumps(editor_data, ensure_ascii=False), encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        main()

        content = output_file.read_text(encoding="utf-8")
        assert "新闻标题" in content
        assert "\\u" not in content

    def test_cli_input_file_not_found_exits(self, tmp_path):
        """--input 文件不存在 → 退出码 1。"""
        editor_file = tmp_path / "editor.json"
        output_file = tmp_path / "output.json"
        editor_file.write_text("{}", encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(tmp_path / "nonexistent.json"),
            "--editor", str(editor_file),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_cli_editor_file_not_found_exits(self, tmp_path):
        """--editor 文件不存在 → 退出码 1。"""
        input_file = tmp_path / "input.json"
        output_file = tmp_path / "output.json"
        input_file.write_text("{}", encoding="utf-8")

        sys.argv = [
            "merge_editor_output.py",
            "--input", str(input_file),
            "--editor", str(tmp_path / "nonexistent.json"),
            "--output", str(output_file),
        ]
        from merge_editor_output import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
