#!/usr/bin/env python3
"""Validate Editor Agent output: check new fields + ensure existing fields untouched."""

import argparse
import json
import sys
import os


def load_json(path):
    """Load JSON file. Returns (data, error_msg)."""
    if not os.path.exists(path):
        return None, f"文件不存在: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败: {e}"
    if not isinstance(data, dict):
        return None, f"顶层结构应为对象，实际为 {type(data).__name__}"
    return data, None


def validate_editor_fields(output_data):
    """Validate the three fields Editor Agent should add. Returns list of errors."""
    errors = []

    # overview: required, non-empty string
    overview = output_data.get("overview")
    if overview is None:
        errors.append("缺少 overview 字段")
    elif not isinstance(overview, str) or not overview.strip():
        errors.append("overview 应为非空字符串")

    # cross_links: required, list of objects
    cross_links = output_data.get("cross_links")
    if cross_links is None:
        errors.append("缺少 cross_links 字段")
    elif not isinstance(cross_links, list):
        errors.append(f"cross_links 应为数组，实际为 {type(cross_links).__name__}")
    else:
        for i, link in enumerate(cross_links):
            if not isinstance(link, dict):
                errors.append(f"cross_links[{i}] 应为对象")
                continue
            if "theme" not in link:
                errors.append(f"cross_links[{i}] 缺少 theme 字段")
            if "related_indices" not in link:
                errors.append(f"cross_links[{i}] 缺少 related_indices 字段")
            elif not isinstance(link["related_indices"], list):
                errors.append(f"cross_links[{i}].related_indices 应为数组")
            if "explanation" not in link:
                errors.append(f"cross_links[{i}] 缺少 explanation 字段")

    # reading_guide: required, string (can be empty if main is empty)
    reading_guide = output_data.get("reading_guide")
    if reading_guide is None:
        errors.append("缺少 reading_guide 字段")
    elif not isinstance(reading_guide, str):
        errors.append(f"reading_guide 应为字符串，实际为 {type(reading_guide).__name__}")

    return errors


def validate_preserved(input_data, output_data):
    """Ensure existing fields are not modified. Returns list of errors."""
    errors = []
    preserved_fields = ["title", "date", "main", "brief", "signature", "tagline"]

    for field in preserved_fields:
        if field not in input_data:
            continue
        if field not in output_data:
            errors.append(f"原有字段被删除: {field}")
        elif input_data[field] != output_data[field]:
            if field in ("main", "brief"):
                in_len = len(input_data[field])
                out_len = len(output_data[field])
                if in_len != out_len:
                    errors.append(
                        f"{field} 数组长度被修改: {in_len} → {out_len}"
                    )
                else:
                    errors.append(f"{field} 数组内容被修改（长度相同但内容不一致）")
            else:
                errors.append(
                    f"{field} 被修改: \"{input_data[field]}\" → \"{output_data[field]}\""
                )

    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="校验 Editor Agent 输出：检查新增字段 + 确保原有字段未被篡改"
    )
    parser.add_argument("input_file", help="Editor 的输入文件（合并后的 JSON）")
    parser.add_argument("output_file", help="Editor 的输出文件")
    parser.add_argument(
        "--strict", action="store_true",
        help="严格模式：有错误则返回非零退出码"
    )
    args = parser.parse_args(argv)

    input_data, err = load_json(args.input_file)
    if err:
        print(f"输入文件错误: {err}", file=sys.stderr)
        sys.exit(1)

    output_data, err = load_json(args.output_file)
    if err:
        print(f"输出文件错误: {err}", file=sys.stderr)
        sys.exit(1)

    errors = []
    errors.extend(validate_editor_fields(output_data))
    errors.extend(validate_preserved(input_data, output_data))

    if errors:
        print(f"校验失败，{len(errors)} 个错误:")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print("✓ 校验通过")

    if errors and args.strict:
        sys.exit(1)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
