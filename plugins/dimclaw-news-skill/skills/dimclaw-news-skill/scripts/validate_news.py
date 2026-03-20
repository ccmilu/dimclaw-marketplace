#!/usr/bin/env python3
"""Validate sub-agent news JSON output for required/optional field correctness."""

import argparse
import json
import sys
import os

ALLOWED_CATEGORIES = {"headline", "tech", "finance", "life", "other"}
ALLOWED_LEVELS = {"main", "brief"}

# Fields required for ALL items (both main and brief)
BASE_REQUIRED = {"title", "url", "source", "time", "level", "category"}

# Additional fields required ONLY for main items
MAIN_EXTRA_REQUIRED = {"importance", "summary", "insights"}

# Optional fields (may or may not be present)
OPTIONAL_FIELDS = {"heat", "tracking_info", "event_id", "alt_sources", "related_events", "raw_content"}

# All known fields
ALL_KNOWN = BASE_REQUIRED | MAIN_EXTRA_REQUIRED | OPTIONAL_FIELDS


def validate_item(item, index):
    """Validate a single news item. Returns list of error strings."""
    errors = []

    if not isinstance(item, dict):
        return [f"[{index}] 不是对象类型，而是 {type(item).__name__}"]

    level = item.get("level")

    # Check base required fields
    for field in BASE_REQUIRED:
        if field not in item:
            errors.append(f"[{index}] 缺少必填字段: {field}")
        elif not item[field] and field != "time":
            # time can be empty string in some edge cases, others must be non-empty
            errors.append(f"[{index}] 必填字段为空: {field}")

    # Validate level value
    if level and level not in ALLOWED_LEVELS:
        errors.append(f'[{index}] level 值无效: "{level}" (应为 main 或 brief)')

    # Validate category value
    category = item.get("category")
    if category and category not in ALLOWED_CATEGORIES:
        errors.append(
            f'[{index}] category 值无效: "{category}" '
            f"(应为 {', '.join(sorted(ALLOWED_CATEGORIES))})"
        )

    # Check main-specific required fields
    if level == "main":
        for field in MAIN_EXTRA_REQUIRED:
            if field not in item:
                errors.append(f"[{index}] main 条目缺少必填字段: {field}")
            elif field == "importance":
                imp = item[field]
                if not isinstance(imp, int) or imp < 1 or imp > 10:
                    errors.append(
                        f'[{index}] importance 值无效: {imp} (应为 1-10 整数)'
                    )
            elif field == "summary" and not item[field]:
                errors.append(f"[{index}] main 条目 summary 为空")
            elif field == "insights":
                ins = item[field]
                if not ins:
                    errors.append(f"[{index}] main 条目 insights 为空")
                elif not isinstance(ins, (str, list)):
                    errors.append(
                        f"[{index}] insights 类型无效: {type(ins).__name__} "
                        f"(应为字符串或数组)"
                    )

    # Check brief items should NOT have main-only fields
    if level == "brief":
        for field in MAIN_EXTRA_REQUIRED:
            if field in item:
                errors.append(
                    f"[{index}] brief 条目不应包含字段: {field}"
                )

    # Warn about unknown fields
    unknown = set(item.keys()) - ALL_KNOWN
    if unknown:
        errors.append(
            f"[{index}] 包含未知字段: {', '.join(sorted(unknown))}"
        )

    return errors


def validate_file(path):
    """Validate a sub-agent JSON file. Returns (items_count, errors_list, warnings_list)."""
    errors = []

    if not os.path.exists(path):
        return 0, [f"文件不存在: {path}"], []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return 0, [f"JSON 解析失败: {e}"], []

    if not isinstance(data, list):
        return 0, [f"顶层结构不是数组，而是 {type(data).__name__}"], []

    if len(data) == 0:
        return 0, ["文件为空数组，无任何条目"], []

    all_errors = []
    main_count = 0
    brief_count = 0

    for i, item in enumerate(data):
        item_errors = validate_item(item, i)
        all_errors.extend(item_errors)
        level = item.get("level") if isinstance(item, dict) else None
        if level == "main":
            main_count += 1
        elif level == "brief":
            brief_count += 1

    return len(data), all_errors, {"main": main_count, "brief": brief_count}


def format_report(path, total, errors, counts):
    """Format validation report as string."""
    lines = [f"文件: {path}"]

    if isinstance(counts, dict):
        lines.append(f"条目: {total} 条 (main: {counts['main']}, brief: {counts['brief']})")
    else:
        lines.append(f"条目: {total} 条")

    if errors:
        lines.append(f"错误: {len(errors)} 个")
        for err in errors:
            lines.append(f"  ✗ {err}")
    else:
        lines.append("结果: ✓ 全部通过")

    return "\n".join(lines)


def parse_args(argv=None):
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="验证子 Agent 新闻 JSON 输出的字段完整性和正确性"
    )
    parser.add_argument("files", nargs="+", help="要验证的 JSON 文件路径")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：有任何错误则返回非零退出码",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Main entry point."""
    args = parse_args(argv)
    has_errors = False

    for path in args.files:
        total, errors, counts = validate_file(path)
        report = format_report(path, total, errors, counts)
        print(report)
        print()
        if errors:
            has_errors = True

    if has_errors:
        if args.strict:
            sys.exit(1)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
