#!/usr/bin/env python3
"""
合并 Editor Agent 的编辑字段到原始新闻 JSON。

Editor Agent 只输出 3 个字段（overview, cross_links, reading_guide），
本脚本负责将它们合并到原始 merged JSON 中，确保其他字段完整保留。

用法:
    python merge_editor_output.py \
        --input /tmp/news_merged.json \
        --editor /tmp/editor_fields.json \
        --output /tmp/news_final.json
"""

import argparse
import json
import sys


def validate_editor_fields(editor_data: dict, main_count: int) -> list[str]:
    """校验 editor 字段的合法性。"""
    errors = []

    # 必填字段检查
    required = ["overview", "cross_links", "reading_guide"]
    for field in required:
        if field not in editor_data:
            errors.append(f"缺少必填字段: {field}")

    # overview 类型检查
    if "overview" in editor_data and not isinstance(editor_data["overview"], str):
        errors.append("overview 必须是字符串")

    # cross_links 检查
    if "cross_links" in editor_data:
        if not isinstance(editor_data["cross_links"], list):
            errors.append("cross_links 必须是数组")
        else:
            for i, link in enumerate(editor_data["cross_links"]):
                if not isinstance(link, dict):
                    errors.append(f"cross_links[{i}] 必须是对象")
                    continue
                for key in ("theme", "related_indices", "explanation"):
                    if key not in link:
                        errors.append(f"cross_links[{i}] 缺少字段: {key}")
                if "related_indices" in link:
                    if not isinstance(link["related_indices"], list):
                        errors.append(f"cross_links[{i}].related_indices 必须是数组")
                    else:
                        for idx in link["related_indices"]:
                            if not isinstance(idx, int) or idx < 0 or idx >= main_count:
                                errors.append(
                                    f"cross_links[{i}].related_indices 包含无效索引 {idx}"
                                    f"（main 数组长度为 {main_count}）"
                                )

    # reading_guide 类型检查
    if "reading_guide" in editor_data and not isinstance(editor_data["reading_guide"], str):
        errors.append("reading_guide 必须是字符串")

    return errors


def merge(input_data: dict, editor_data: dict) -> dict:
    """合并编辑字段到原始数据，返回新字典。"""
    result = dict(input_data)
    result["overview"] = editor_data["overview"]
    result["cross_links"] = editor_data["cross_links"]
    result["reading_guide"] = editor_data["reading_guide"]
    return result


def main():
    parser = argparse.ArgumentParser(
        description="合并 Editor Agent 编辑字段到原始新闻 JSON"
    )
    parser.add_argument("--input", required=True, help="原始 merged JSON 文件路径")
    parser.add_argument("--editor", required=True, help="Editor 输出的字段 JSON 文件路径")
    parser.add_argument("--output", required=True, help="合并后的输出文件路径")
    args = parser.parse_args()

    # 读取原始文件
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            input_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"错误: 读取 --input 文件失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 读取 editor 文件
    try:
        with open(args.editor, "r", encoding="utf-8") as f:
            editor_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"错误: 读取 --editor 文件失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 校验
    main_count = len(input_data.get("main", []))
    errors = validate_editor_fields(editor_data, main_count)
    if errors:
        print("校验失败:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # 合并
    result = merge(input_data, editor_data)

    # 写入
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"合并成功: {args.output}")
    print(f"  overview: {len(editor_data['overview'])} 字")
    print(f"  cross_links: {len(editor_data['cross_links'])} 组")
    print(f"  reading_guide: {len(editor_data['reading_guide'])} 字")


if __name__ == "__main__":
    main()
