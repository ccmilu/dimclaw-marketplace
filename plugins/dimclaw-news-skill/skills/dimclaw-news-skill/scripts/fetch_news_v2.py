import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

# Suppress SSL warnings (corporate proxy issues)
warnings.filterwarnings('ignore', message='Unverified HTTPS request')
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Try importing feedparser; fall back to None if unavailable
try:
    import feedparser
except ImportError:
    feedparser = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")


# ---------------------------------------------------------------------------
# Utility functions (copied from fetch_news.py to avoid coupling)
# ---------------------------------------------------------------------------

def _has_cjk(text):
    """Check if text contains CJK (Chinese/Japanese/Korean) characters."""
    return bool(re.search(
        r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]',
        text,
    ))


def filter_items(items, keyword=None):
    if not keyword:
        return items
    keywords = [k.strip() for k in keyword.split(',') if k.strip()]
    parts = []
    for k in keywords:
        escaped = re.escape(k)
        if _has_cjk(k):
            parts.append(escaped)
        else:
            parts.append(r'\b' + escaped + r'\b')
    pattern = '|'.join(parts)
    regex = r'(?i)(' + pattern + r')'
    return [item for item in items if re.search(regex, item['title'])]


# ---------------------------------------------------------------------------
# Zhipu Reader helper
# ---------------------------------------------------------------------------

def _zhipu_reader(url):
    """Call Zhipu Reader API to extract page content as text.

    Returns the content string on success, or empty string on failure.
    """
    if not ZHIPU_API_KEY:
        sys.stderr.write("[zhipu_reader] ZHIPU_API_KEY not set\n")
        return ""
    api_url = "https://open.bigmodel.cn/api/paas/v4/reader"
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "return_format": "text",
        "retain_images": False,
        "no_cache": True,
    }
    try:
        resp = requests.post(api_url, json=payload, headers=headers,
                             timeout=30, verify=False)
        if resp.status_code != 200:
            sys.stderr.write(
                f"[zhipu_reader] HTTP {resp.status_code} for {url}\n")
            return ""
        data = resp.json()
        content = data.get("reader_result", {}).get("content", "")
        return content
    except Exception as e:
        sys.stderr.write(f"[zhipu_reader] error for {url}: {e}\n")
        return ""


# ---------------------------------------------------------------------------
# RSS / Atom helper using BeautifulSoup XML (fallback when feedparser absent)
# ---------------------------------------------------------------------------

def _parse_rss_bs4(text):
    """Parse RSS/Atom feed text with BeautifulSoup, return list of dicts
    with keys: title, url, time_str."""
    soup = BeautifulSoup(text, 'xml')
    entries = []

    # RSS 2.0 items
    for item in soup.find_all('item'):
        title_tag = item.find('title')
        link_tag = item.find('link')
        pub_tag = item.find('pubDate')
        title = title_tag.get_text(strip=True) if title_tag else ''
        link = link_tag.get_text(strip=True) if link_tag else ''
        pub = pub_tag.get_text(strip=True) if pub_tag else ''
        if title:
            entries.append({"title": title, "url": link, "time_str": pub})

    # Atom entries
    if not entries:
        for entry in soup.find_all('entry'):
            title_tag = entry.find('title')
            link_tag = entry.find('link')
            pub_tag = entry.find('published') or entry.find('updated')
            title = title_tag.get_text(strip=True) if title_tag else ''
            link = ''
            if link_tag:
                link = link_tag.get('href') or link_tag.get_text(strip=True)
            pub = pub_tag.get_text(strip=True) if pub_tag else ''
            if title:
                entries.append({"title": title, "url": link, "time_str": pub})

    return entries


def _fetch_rss(rss_url):
    """Fetch RSS feed via requests (to handle SSL verify=False) and parse.

    Returns list of dicts with keys: title, url, time_str.
    Uses feedparser if available, falls back to BeautifulSoup XML.
    """
    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=15,
                            verify=False)
        if resp.status_code != 200:
            sys.stderr.write(
                f"[_fetch_rss] HTTP {resp.status_code} for {rss_url}\n")
            return []
        text = resp.text
    except Exception as e:
        sys.stderr.write(f"[_fetch_rss] fetch error for {rss_url}: {e}\n")
        return []

    # Try feedparser on the already-fetched text
    if feedparser:
        feed = feedparser.parse(text)
        entries = []
        for entry in feed.entries:
            title = getattr(entry, 'title', '')
            link = getattr(entry, 'link', '')
            published = getattr(entry, 'published',
                                getattr(entry, 'updated', ''))
            if title:
                entries.append({
                    "title": title, "url": link, "time_str": published})
        if entries:
            return entries

    # Fallback: BeautifulSoup XML
    return _parse_rss_bs4(text)


def _format_time(time_str):
    """Try to convert various time formats to YYYY-MM-DD HH:MM."""
    if not time_str:
        return ""
    # Try RFC 2822
    try:
        dt = parsedate_to_datetime(time_str)
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        pass
    # Try ISO 8601
    try:
        dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        pass
    return time_str


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

def fetch_huggingface(limit=5, keyword=None):
    """Fetch HuggingFace daily papers via API, fallback to Zhipu Reader."""
    # --- Primary: HuggingFace API ---
    try:
        api_url = f"https://huggingface.co/api/daily_papers?limit={max(limit * 2, 30)}"
        resp = requests.get(api_url, headers=HEADERS, timeout=15, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            items = []
            for entry in data:
                paper = entry.get("paper", {})
                paper_id = paper.get("id", "")
                title = entry.get("title", "") or paper.get("title", "")
                title = re.sub(r'\s+', ' ', title).strip()
                if not title:
                    continue

                paper_url = f"https://huggingface.co/papers/{paper_id}" if paper_id else ""
                upvotes = paper.get("upvotes", 0) or entry.get("numUpvotes", 0)
                heat = f"{upvotes} upvotes" if upvotes else ""

                published = entry.get("publishedAt", "")
                time_str = ""
                if published:
                    try:
                        dt = datetime.fromisoformat(
                            published.replace('Z', '+00:00'))
                        time_str = dt.strftime('%Y-%m-%d')
                    except Exception:
                        time_str = published[:10]

                items.append({
                    "source": "HuggingFace Papers",
                    "title": title,
                    "url": paper_url,
                    "heat": heat,
                    "time": time_str or "Today",
                })
            if items:
                return filter_items(items, keyword)[:limit]
    except Exception as e:
        sys.stderr.write(f"[huggingface] API error: {e}\n")

    # --- Fallback: Zhipu Reader ---
    try:
        content = _zhipu_reader("https://huggingface.co/papers")
        if not content:
            return []

        items = []
        lines = content.split('\n')
        current_votes = ""

        for i, line in enumerate(lines):
            stripped = line.strip()

            vote_match = re.match(r'-\s*\[x\]\s*(\d+)', stripped)
            if vote_match:
                current_votes = vote_match.group(1)
                continue

            title_match = re.match(r'^###\s+(.+)', stripped)
            if title_match:
                title = title_match.group(1).strip()
                if not title:
                    continue

                paper_url = ""
                search_range = lines[max(0, i - 5):i + 5]
                for search_line in search_range:
                    url_match = re.search(
                        r'(https?://huggingface\.co/papers/[\w.]+)',
                        search_line)
                    if url_match:
                        paper_url = url_match.group(1)
                        break

                if not paper_url:
                    for search_line in search_range:
                        arxiv_match = re.search(
                            r'(\d{4}\.\d{4,5})', search_line)
                        if arxiv_match:
                            paper_url = (
                                "https://huggingface.co/papers/"
                                + arxiv_match.group(1))
                            break

                if not paper_url:
                    slug = re.sub(r'[^\w\s-]', '', title).strip()
                    paper_url = (
                        "https://huggingface.co/papers?search="
                        + requests.utils.quote(slug))

                heat = f"{current_votes} upvotes" if current_votes else ""
                items.append({
                    "source": "HuggingFace Papers",
                    "title": title,
                    "url": paper_url,
                    "heat": heat,
                    "time": "Today",
                })
                current_votes = ""

        return filter_items(items, keyword)[:limit]
    except Exception as e:
        sys.stderr.write(f"[huggingface] reader error: {e}\n")
        return []


def fetch_arxiv(limit=5, keyword=None):
    """Fetch recent AI/ML/NLP papers from ArXiv API."""
    try:
        max_results = max(limit * 2, 30)
        api_url = (
            "https://export.arxiv.org/api/query?"
            "search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
            "&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={max_results}"
        )
        resp = requests.get(api_url, timeout=20, verify=False)
        if resp.status_code != 200:
            sys.stderr.write(f"[arxiv] HTTP {resp.status_code}\n")
            return []

        soup = BeautifulSoup(resp.text, 'xml')
        items = []
        for entry in soup.find_all('entry'):
            title_tag = entry.find('title')
            id_tag = entry.find('id')
            published_tag = entry.find('published')

            title = title_tag.get_text(strip=True) if title_tag else ''
            # Clean up multi-line titles
            title = re.sub(r'\s+', ' ', title)
            url = id_tag.get_text(strip=True) if id_tag else ''
            published = published_tag.get_text(strip=True) if published_tag else ''

            # Format time
            time_str = ""
            if published:
                try:
                    dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                    time_str = dt.strftime('%Y-%m-%d')
                except Exception:
                    time_str = published[:10]

            # Collect categories for heat info
            categories = []
            for cat_tag in entry.find_all('category'):
                term = cat_tag.get('term', '')
                if term:
                    categories.append(term)
            heat = ', '.join(categories[:5]) if categories else ''

            if title and url:
                items.append({
                    "source": "ArXiv",
                    "title": title,
                    "url": url,
                    "heat": heat,
                    "time": time_str,
                })

        # ArXiv API courtesy: 3 second delay
        time.sleep(0.5)

        return filter_items(items, keyword)[:limit]
    except Exception as e:
        sys.stderr.write(f"[arxiv] error: {e}\n")
        return []


def fetch_techcrunch(limit=5, keyword=None):
    """Fetch TechCrunch articles via RSS feed."""
    try:
        entries = _fetch_rss("https://techcrunch.com/feed/")
        items = []
        for e in entries:
            items.append({
                "source": "TechCrunch",
                "title": e["title"],
                "url": e["url"],
                "heat": "",
                "time": _format_time(e["time_str"]),
            })
        return filter_items(items, keyword)[:limit]
    except Exception as e:
        sys.stderr.write(f"[techcrunch] error: {e}\n")
        return []


def fetch_theverge(limit=5, keyword=None):
    """Fetch The Verge articles via Zhipu Reader.

    Returns raw reader content as a single item for sub-agent to parse.
    The sub-agent (AI) will extract individual articles from the text.
    """
    try:
        content = _zhipu_reader("https://www.theverge.com/")
        if not content:
            sys.stderr.write("[theverge] reader returned empty\n")
            return []

        # Return raw content as a single item; sub-agent will parse it
        return [{
            "source": "The Verge",
            "title": "[原始内容] The Verge 首页",
            "url": "https://www.theverge.com/",
            "heat": "",
            "time": "Today",
            "raw_content": content,
        }]
    except Exception as e:
        sys.stderr.write(f"[theverge] error: {e}\n")
        return []


def fetch_cls(limit=5, keyword=None):
    """Fetch latest news from CLS (Cailian Press)."""
    try:
        last_time = int(time.time())
        rn = max(limit * 2, 30)
        api_url = (
            f"https://www.cls.cn/nodeapi/updateTelegraphList?"
            f"app=CailianpressWeb&os=web&sv=8.4.6"
            f"&rn={rn}&last_time={last_time}"
        )
        headers_cls = {
            **HEADERS,
            "Referer": "https://www.cls.cn/telegraph",
        }
        resp = requests.get(api_url, headers=headers_cls, timeout=15,
                            verify=False)
        if resp.status_code != 200:
            sys.stderr.write(f"[cls] HTTP {resp.status_code}\n")
            return []
        data = resp.json()
        roll_data = data.get('data', {}).get('roll_data', [])

        items = []
        for item in roll_data:
            # Pick the best title field
            title = (item.get('title') or item.get('brief')
                     or item.get('content') or '')
            title = title.strip()
            # Clean HTML tags from title
            title = re.sub(r'<[^>]+>', '', title)
            if not title:
                continue

            ctime = item.get('ctime', 0)
            time_str = ""
            if ctime:
                try:
                    dt = datetime.fromtimestamp(ctime)
                    time_str = dt.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass

            reading_num = item.get('reading_num', 0)
            heat = f"{reading_num} 阅读" if reading_num else ""

            share_url = item.get('shareurl', '')
            if not share_url:
                # Construct from id
                item_id = item.get('id', '')
                if item_id:
                    share_url = f"https://www.cls.cn/detail/{item_id}"

            items.append({
                "source": "财联社",
                "title": title,
                "url": share_url,
                "heat": heat,
                "time": time_str,
            })

        return filter_items(items, keyword)[:limit]
    except Exception as e:
        sys.stderr.write(f"[cls] error: {e}\n")
        return []


def fetch_sspai(limit=5, keyword=None):
    """Fetch articles from SSPAI (少数派) via Zhipu Reader.

    Returns raw reader content as a single item for sub-agent to parse.
    The sub-agent (AI) will extract individual articles from the text.
    """
    try:
        content = _zhipu_reader("https://sspai.com/")
        if not content:
            sys.stderr.write("[sspai] reader returned empty\n")
            return []

        # Return raw content as a single item; sub-agent will parse it
        return [{
            "source": "少数派",
            "title": "[原始内容] 少数派首页",
            "url": "https://sspai.com/",
            "heat": "",
            "time": "Today",
            "raw_content": content,
        }]
    except Exception as e:
        sys.stderr.write(f"[sspai] error: {e}\n")
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch news from v2 sources")
    sources_map = {
        'huggingface': fetch_huggingface,
        'arxiv': fetch_arxiv,
        'techcrunch': fetch_techcrunch,
        'theverge': fetch_theverge,
        'cls': fetch_cls,
        'sspai': fetch_sspai,
    }

    parser.add_argument(
        '--source', default='all',
        help='Source(s) to fetch from (comma-separated). '
             f'Available: {", ".join(sources_map.keys())}')
    parser.add_argument(
        '--limit', type=int, default=10,
        help='Limit per source. Default 10')
    parser.add_argument(
        '--keyword',
        help='Comma-separated keyword filter')

    args = parser.parse_args()

    to_run = []
    if args.source == 'all':
        to_run = list(sources_map.values())
    else:
        requested = [s.strip() for s in args.source.split(',')]
        for s in requested:
            if s in sources_map:
                to_run.append(sources_map[s])
            else:
                sys.stderr.write(
                    f"[warn] unknown source '{s}', skipping\n")

    results = []
    for func in to_run:
        try:
            results.extend(func(args.limit, args.keyword))
        except Exception as e:
            sys.stderr.write(f"[error] {func.__name__}: {e}\n")

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
