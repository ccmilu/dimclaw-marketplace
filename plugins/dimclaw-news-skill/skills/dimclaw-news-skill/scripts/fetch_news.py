import argparse
import json
import requests
from bs4 import BeautifulSoup
import sys
import time
import re
import concurrent.futures
from datetime import datetime

# Headers for scraping to avoid basic bot detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def _has_cjk(text):
    """Check if text contains CJK (Chinese/Japanese/Korean) characters."""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]', text))

def filter_items(items, keyword=None):
    if not keyword:
        return items
    keywords = [k.strip() for k in keyword.split(',') if k.strip()]
    # \b word boundary doesn't work with CJK characters, only use it for ASCII keywords
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

def fetch_url_content(url):
    """
    Fetches the content of a URL and extracts text from paragraphs.
    Truncates to 3000 characters.
    """
    if not url or not url.startswith('http'):
        return ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
         # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.extract()
        # Get text
        text = soup.get_text(separator=' ', strip=True)
        # Simple cleanup
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        return text[:3000]
    except Exception:
        return ""

def enrich_items_with_content(items, max_workers=10):
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(fetch_url_content, item['url']): item for item in items}
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                content = future.result()
                if content:
                    item['content'] = content
            except Exception:
                item['content'] = ""
    return items

# --- Source Fetchers ---

def fetch_hackernews(limit=5, keyword=None):
    # Primary: Algolia HN API (free, no auth required, reliable)
    try:
        url = f"https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage={max(limit * 2, 30)}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            items = []
            for hit in data.get('hits', []):
                title = hit.get('title', '')
                if not title:
                    continue
                story_url = hit.get('url') or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
                points = hit.get('points', 0)
                num_comments = hit.get('num_comments', 0)
                created_at = hit.get('created_at', '')
                # Convert ISO time to relative-like format
                time_str = created_at
                if created_at:
                    try:
                        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        now = datetime.now(dt.tzinfo)
                        delta = now - dt
                        hours = int(delta.total_seconds() / 3600)
                        if hours < 1:
                            time_str = f"{int(delta.total_seconds() / 60)} minutes ago"
                        elif hours < 24:
                            time_str = f"{hours} hours ago"
                        else:
                            time_str = f"{hours // 24} days ago"
                    except:
                        pass
                items.append({
                    "source": "Hacker News",
                    "title": title,
                    "url": story_url,
                    "heat": f"{points} points",
                    "time": time_str
                })
            return filter_items(items, keyword)[:limit]
    except:
        pass

    # Fallback: Firebase HN API
    try:
        ids_resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
        story_ids = ids_resp.json()[:max(limit * 2, 30)]
        items = []
        for sid in story_ids:
            try:
                item_resp = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5)
                story = item_resp.json()
                if not story or not story.get('title'):
                    continue
                ts = story.get('time', 0)
                time_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else ""
                items.append({
                    "source": "Hacker News",
                    "title": story['title'],
                    "url": story.get('url', f"https://news.ycombinator.com/item?id={sid}"),
                    "heat": f"{story.get('score', 0)} points",
                    "time": time_str
                })
                if len(filter_items(items, keyword)) >= limit:
                    break
            except:
                continue
        return filter_items(items, keyword)[:limit]
    except:
        pass

    # Last fallback: scrape HTML (original method)
    base_url = "https://news.ycombinator.com"
    news_items = []
    page = 1
    max_pages = 5
    while len(news_items) < limit and page <= max_pages:
        url = f"{base_url}/news?p={page}"
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code != 200: break
        except: break
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select('.athing')
        if not rows: break
        page_items = []
        for row in rows:
            try:
                id_ = row.get('id')
                title_line = row.select_one('.titleline a')
                if not title_line: continue
                title = title_line.get_text()
                link = title_line.get('href')
                score_span = soup.select_one(f'#score_{id_}')
                score = score_span.get_text() if score_span else "0 points"
                age_span = soup.select_one(f'.age a[href="item?id={id_}"]')
                time_str = age_span.get_text() if age_span else ""
                if link and link.startswith('item?id='): link = f"{base_url}/{link}"
                page_items.append({
                    "source": "Hacker News",
                    "title": title,
                    "url": link,
                    "heat": score,
                    "time": time_str
                })
            except: continue
        news_items.extend(filter_items(page_items, keyword))
        if len(news_items) >= limit: break
        page += 1
        time.sleep(0.5)
    return news_items[:limit]

def fetch_weibo(limit=5, keyword=None):
    # Use the PC Ajax API which returns JSON directly and is less rate-limited than scraping s.weibo.com
    url = "https://weibo.com/ajax/side/hotSearch"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://weibo.com/"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        items = data.get('data', {}).get('realtime', [])
        
        all_items = []
        for item in items:
            # key 'note' is usually the title, sometimes 'word'
            title = item.get('note', '') or item.get('word', '')
            if not title: continue
            
            # 'num' is the heat value
            heat = item.get('num', 0)
            
            # Construct URL (usually search query)
            # Web UI uses: https://s.weibo.com/weibo?q=%23TITLE%23&Refer=top
            full_url = f"https://s.weibo.com/weibo?q={requests.utils.quote(title)}&Refer=top"
            
            all_items.append({
                "source": "Weibo Hot Search", 
                "title": title, 
                "url": full_url, 
                "heat": f"{heat}",
                "time": "Real-time"
            })
            
        return filter_items(all_items, keyword)[:limit]
    except Exception: 
        return []

def fetch_github(limit=5, keyword=None):
    try:
        response = requests.get("https://github.com/trending", headers=HEADERS, timeout=10)
    except: return []
    
    soup = BeautifulSoup(response.text, 'html.parser')
    items = []
    for article in soup.select('article.Box-row'):
        try:
            h2 = article.select_one('h2 a')
            if not h2: continue
            title = h2.get_text(strip=True).replace('\n', '').replace(' ', '')
            link = "https://github.com" + h2['href']
            
            desc = article.select_one('p')
            desc_text = desc.get_text(strip=True) if desc else ""
            
            # Stars (Heat)
            # usually the first 'Link--muted' with a SVG star
            stars_tag = article.select_one('a[href$="/stargazers"]')
            stars = stars_tag.get_text(strip=True) if stars_tag else ""
            
            items.append({
                "source": "GitHub Trending", 
                "title": f"{title} - {desc_text}", 
                "url": link,
                "heat": f"{stars} stars",
                "time": "Today"
            })
        except: continue
    return filter_items(items, keyword)[:limit]

def fetch_36kr(limit=5, keyword=None):
    try:
        response = requests.get("https://36kr.com/newsflashes", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        items = []
        for item in soup.select('.newsflash-item'):
            title = item.select_one('.item-title').get_text(strip=True)
            href = item.select_one('.item-title')['href']
            time_tag = item.select_one('.time')
            time_str = time_tag.get_text(strip=True) if time_tag else ""
            
            items.append({
                "source": "36Kr", 
                "title": title, 
                "url": f"https://36kr.com{href}" if not href.startswith('http') else href,
                "time": time_str,
                "heat": ""
            })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_v2ex(limit=5, keyword=None):
    # V2EX API 经常被限制访问，尝试多个备选方案
    items = []

    # 方案1：官方 API（可能被限制）
    try:
        data = requests.get("https://www.v2ex.com/api/topics/hot.json", headers=HEADERS, timeout=5).json()
        for t in data:
            replies = t.get('replies', 0)
            items.append({
                "source": "V2EX",
                "title": t['title'],
                "url": t['url'],
                "heat": f"{replies} replies",
                "time": "Hot"
            })
        if items:
            return filter_items(items, keyword)[:limit]
    except:
        pass

    # 方案2：抓取首页 HTML
    try:
        response = requests.get("https://www.v2ex.com/?tab=hot", headers=HEADERS, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for item in soup.select('.item_title a'):
                title = item.get_text(strip=True)
                href = item.get('href', '')
                if href and title:
                    url = f"https://www.v2ex.com{href}" if href.startswith('/') else href
                    items.append({
                        "source": "V2EX",
                        "title": title,
                        "url": url,
                        "heat": "",
                        "time": "Hot"
                    })
            if items:
                return filter_items(items, keyword)[:limit]
    except:
        pass

    # 方案3：RSSHub 公共实例获取 V2EX 热门主题（无关键词时使用，返回真正的热帖）
    if not keyword:
        rsshub_instances = [
            "https://rsshub.rssforever.com/v2ex/topics/hot",
            "https://rsshub.pseudoyu.com/v2ex/topics/hot",
        ]
        for rsshub_url in rsshub_instances:
            try:
                response = requests.get(rsshub_url, timeout=10)
                if response.status_code != 200:
                    continue
                soup = BeautifulSoup(response.text, 'xml')
                rss_items = soup.find_all('item')
                if not rss_items:
                    continue
                for entry in rss_items:
                    title_tag = entry.find('title')
                    link_tag = entry.find('link')
                    pub_tag = entry.find('pubDate')
                    title = title_tag.get_text(strip=True) if title_tag else ''
                    link = link_tag.get_text(strip=True) if link_tag else ''
                    pub = pub_tag.get_text(strip=True) if pub_tag else ''
                    if not title:
                        continue
                    time_str = pub
                    if pub:
                        try:
                            from email.utils import parsedate_to_datetime
                            dt = parsedate_to_datetime(pub)
                            time_str = dt.strftime('%Y-%m-%d %H:%M')
                        except:
                            pass
                    items.append({
                        "source": "V2EX",
                        "title": title,
                        "url": link,
                        "heat": "Hot",
                        "time": time_str
                    })
                if items:
                    return items[:limit]
            except:
                continue

    # 方案4：sov2ex 第三方搜索 API（有关键词时使用，按关键词搜索）
    if not keyword:
        return []
    try:
        seen_ids = set()
        all_items = []
        search_queries = [k.strip() for k in keyword.split(',') if k.strip()]
        fetch_size = max(limit * 2, 20)
        for search_query in search_queries:
            try:
                sov2ex_url = f"https://www.sov2ex.com/api/search?q={requests.utils.quote(search_query)}&size={fetch_size}&sort=created"
                response = requests.get(sov2ex_url, timeout=10)
                if response.status_code != 200:
                    continue
                data = response.json()
                for hit in data.get('hits', []):
                    src = hit.get('_source', {})
                    title = src.get('title', '')
                    topic_id = src.get('id', '')
                    if not title or topic_id in seen_ids:
                        continue
                    seen_ids.add(topic_id)
                    replies = src.get('replies', 0)
                    created = src.get('created', '')
                    time_str = created
                    if created:
                        try:
                            dt = datetime.fromisoformat(created)
                            time_str = dt.strftime('%Y-%m-%d %H:%M')
                        except:
                            pass
                    all_items.append({
                        "source": "V2EX",
                        "title": title,
                        "url": f"https://www.v2ex.com/t/{topic_id}" if topic_id else "",
                        "heat": f"{replies} replies",
                        "time": time_str,
                        "_replies": replies  # for sorting
                    })
            except:
                continue
        if all_items:
            # Sort by replies descending to surface popular posts
            all_items.sort(key=lambda x: x.get('_replies', 0), reverse=True)
            # Remove internal sort key
            for item in all_items:
                item.pop('_replies', None)
            items = all_items
            return filter_items(items, keyword)[:limit]
    except:
        pass

    return []

def fetch_tencent(limit=5, keyword=None):
    try:
        url = "https://i.news.qq.com/web_backend/v2/getTagInfo?tagId=aEWqxLtdgmQ%3D"
        data = requests.get(url, headers={"Referer": "https://news.qq.com/"}, timeout=10).json()
        items = []
        for news in data['data']['tabs'][0]['articleList']:
            items.append({
                "source": "Tencent News", 
                "title": news['title'], 
                "url": news.get('url') or news.get('link_info', {}).get('url'),
                "time": news.get('pub_time', '') or news.get('publish_time', '')
            })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_wallstreetcn(limit=5, keyword=None):
    try:
        url = "https://api-one.wallstcn.com/apiv1/content/information-flow?channel=global-channel&accept=article&limit=30"
        data = requests.get(url, timeout=10).json()
        items = []
        for item in data['data']['items']:
            res = item.get('resource')
            if res and (res.get('title') or res.get('content_short')):
                 ts = res.get('display_time', 0)
                 time_str = datetime.fromtimestamp(ts).strftime('%H:%M') if ts else ""
                 items.append({
                     "source": "Wall Street CN", 
                     "title": res.get('title') or res.get('content_short'), 
                     "url": res.get('uri'),
                     "time": time_str
                 })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_producthunt(limit=5, keyword=None):
    try:
        # Using RSS for speed and reliability without API key
        response = requests.get("https://www.producthunt.com/feed", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'xml')
        if not soup.find('item'): soup = BeautifulSoup(response.text, 'html.parser')
        
        items = []
        for entry in soup.find_all(['item', 'entry']):
            title = entry.find('title').get_text(strip=True)
            link_tag = entry.find('link')
            url = link_tag.get('href') or link_tag.get_text(strip=True) if link_tag else ""
            
            pubBox = entry.find('pubDate') or entry.find('published')
            pub = pubBox.get_text(strip=True) if pubBox else ""
            
            items.append({
                "source": "Product Hunt", 
                "title": title, 
                "url": url,
                "time": pub,
                "heat": "Top Product" # RSS implies top rank
            })
        return filter_items(items, keyword)[:limit]
    except: return []

def main():
    parser = argparse.ArgumentParser()
    sources_map = {
        'hackernews': fetch_hackernews, 'weibo': fetch_weibo, 'github': fetch_github,
        '36kr': fetch_36kr, 'v2ex': fetch_v2ex, 'tencent': fetch_tencent,
        'wallstreetcn': fetch_wallstreetcn, 'producthunt': fetch_producthunt
    }
    
    parser.add_argument('--source', default='all', help='Source(s) to fetch from (comma-separated)')
    parser.add_argument('--limit', type=int, default=10, help='Limit per source. Default 10')
    parser.add_argument('--keyword', help='Comma-sep keyword filter')
    parser.add_argument('--deep', action='store_true', help='Download article content for detailed summarization')
    
    args = parser.parse_args()
    
    to_run = []
    if args.source == 'all':
        to_run = list(sources_map.values())
    else:
        requested_sources = [s.strip() for s in args.source.split(',')]
        for s in requested_sources:
            if s in sources_map: to_run.append(sources_map[s])
            
    results = []
    for func in to_run:
        try:
            results.extend(func(args.limit, args.keyword))
        except: pass
        
    if args.deep and results:
        sys.stderr.write(f"Deep fetching content for {len(results)} items...\n")
        results = enrich_items_with_content(results)
        
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
