#!/usr/bin/env python3
"""Merge multiple sub-agent news JSON files into a single news2html-compatible JSON."""

import argparse
import json
import sys
import datetime
import difflib
import os

ALLOWED_CATEGORIES = {"headline", "tech", "finance", "life", "other"}

MAIN_REQUIRED_FIELDS = {"title", "url", "category", "summary"}
BRIEF_REQUIRED_FIELDS = {"title", "url", "category"}
INTERNAL_FIELDS = {"level", "importance"}


def load_files(paths):
    """Load and merge all input JSON files. Warn and skip on errors."""
    all_items = []
    loaded_count = 0
    for path in paths:
        if not os.path.exists(path):
            print(f"警告: 文件不存在，跳过: {path}", file=sys.stderr)
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"警告: JSON 无效，跳过: {path} ({e})", file=sys.stderr)
            continue
        if not isinstance(data, list):
            print(f"警告: 文件内容不是数组，跳过: {path}", file=sys.stderr)
            continue
        all_items.extend(data)
        loaded_count += 1
    return all_items, loaded_count


def normalize_url(url):
    """Normalize URL for dedup: strip trailing slash."""
    if url:
        return url.rstrip("/")
    return url


def dedup_by_url(items):
    """Remove duplicates with the same URL, keeping higher importance."""
    seen = {}
    removed = 0
    for item in items:
        key = normalize_url(item.get("url", ""))
        if not key:
            seen[id(item)] = item
            continue
        if key in seen:
            existing = seen[key]
            if item.get("importance", 0) > existing.get("importance", 0):
                seen[key] = item
            removed += 1
        else:
            seen[key] = item
    return list(seen.values()), removed


def dedup_by_title(items, threshold=0.7):
    """Remove near-duplicate titles (SequenceMatcher ratio >= threshold).
    Process in importance descending order. Keep higher importance item."""
    sorted_items = sorted(items, key=lambda x: x.get("importance", 0), reverse=True)
    accepted = []
    removed = 0
    for item in sorted_items:
        title = item.get("title", "")
        is_dup = False
        for existing in accepted:
            ratio = difflib.SequenceMatcher(None, title, existing.get("title", "")).ratio()
            if ratio >= threshold:
                is_dup = True
                break
        if is_dup:
            removed += 1
        else:
            accepted.append(item)
    return accepted, removed


def enforce_main_limit(items, max_main, category_min=0, category_max=0):
    """Ensure no more than max_main items are 'main', with per-category floor/ceiling.

    Algorithm:
    1. If category_min > 0: guarantee each category up to category_min slots
       (if that category has enough main candidates), sorted by importance.
    2. Fill remaining slots from all un-selected main candidates by importance,
       but skip candidates whose category already reached category_max (if > 0).
    3. Anything left over is downgraded to brief.
    """
    main_items = [i for i in items if i.get("level") == "main"]
    brief_items = [i for i in items if i.get("level") != "main"]

    main_items.sort(key=lambda x: x.get("importance", 0), reverse=True)

    if category_min <= 0 and category_max <= 0:
        # No category constraints, simple truncation
        final_main = main_items[:max_main]
        downgraded = main_items[max_main:]
    else:
        selected = []
        selected_set = set()
        cat_counts = {}

        # Phase 1: guarantee per-category minimum
        if category_min > 0:
            by_cat = {}
            for item in main_items:
                cat = item.get("category", "other")
                by_cat.setdefault(cat, []).append(item)
            for cat, cat_items in by_cat.items():
                for item in cat_items[:category_min]:
                    if len(selected) < max_main:
                        selected.append(item)
                        selected_set.add(id(item))
                        cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # Phase 2: fill remaining slots by global importance
        for item in main_items:
            if len(selected) >= max_main:
                break
            if id(item) in selected_set:
                continue
            cat = item.get("category", "other")
            if category_max > 0 and cat_counts.get(cat, 0) >= category_max:
                continue
            selected.append(item)
            selected_set.add(id(item))
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        final_main = selected
        downgraded = [i for i in main_items if id(i) not in selected_set]

    for item in downgraded:
        item["level"] = "brief"
        item.pop("summary", None)
        item.pop("insights", None)

    return final_main + brief_items + downgraded


def clean_item(item):
    """Remove internal fields from an item."""
    cleaned = {k: v for k, v in item.items() if k not in INTERNAL_FIELDS}
    cat = cleaned.get("category")
    if not cat or cat not in ALLOWED_CATEGORIES:
        cleaned["category"] = "other"
    return cleaned


def build_output(items, title, signature, tagline):
    """Build news2html-compatible output structure."""
    today = datetime.date.today().isoformat()

    main_items = []
    brief_items = []
    for item in items:
        cleaned = clean_item(item)
        if item.get("level") == "main":
            main_items.append(cleaned)
        else:
            brief_items.append(cleaned)

    return {
        "title": title,
        "date": today,
        "main": main_items,
        "brief": brief_items,
        "signature": signature,
        "tagline": tagline,
    }


def merge(paths, max_main=20, category_min=0, category_max=0,
          title="每日新闻简报", signature="最爱你的爪爪丁", tagline=""):
    """Main merge pipeline. Returns (output_dict, stats_dict)."""
    all_items, loaded_count = load_files(paths)
    total_raw = len(all_items)

    items_after_url, url_removed = dedup_by_url(all_items)
    items_after_title, title_removed = dedup_by_title(items_after_url)
    items_final = enforce_main_limit(items_after_title, max_main, category_min, category_max)

    output = build_output(items_final, title, signature, tagline)

    stats = {
        "files_loaded": loaded_count,
        "total_raw": total_raw,
        "after_dedup": len(items_after_title),
        "url_removed": url_removed,
        "title_removed": title_removed,
        "main_count": len(output["main"]),
        "brief_count": len(output["brief"]),
    }
    return output, stats


def print_stats(stats, output_path):
    """Print merge statistics to stdout."""
    print(f"已合并: {stats['files_loaded']} 个文件, 共 {stats['total_raw']} 条")
    print(f"去重后: {stats['after_dedup']} 条 (URL -{stats['url_removed']}, 标题 -{stats['title_removed']})")
    print(f"最终: {stats['main_count']} 条 main + {stats['brief_count']} 条 brief → {output_path}")


def parse_args(argv=None):
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="合并多个子 Agent 新闻 JSON 文件为 news2html 兼容格式")
    parser.add_argument("files", nargs="+", help="输入的 JSON 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出的 JSON 文件路径")
    parser.add_argument("--max-main", type=int, default=20, help="main 条数上限 (默认 20)")
    parser.add_argument("--category-min", type=int, default=0, help="每个分类的 main 保底条数 (默认 0 不限)")
    parser.add_argument("--category-max", type=int, default=0, help="每个分类的 main 上限条数 (默认 0 不限)")
    parser.add_argument("--title", default="每日新闻简报", help="报告标题")
    parser.add_argument("--signature", default="最爱你的爪爪丁", help="签名")
    parser.add_argument("--tagline", default="", help="一句话 tagline")
    return parser.parse_args(argv)


def main(argv=None):
    """Main entry point."""
    args = parse_args(argv)
    output, stats = merge(
        args.files,
        max_main=args.max_main,
        category_min=args.category_min,
        category_max=args.category_max,
        title=args.title,
        signature=args.signature,
        tagline=args.tagline,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print_stats(stats, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
