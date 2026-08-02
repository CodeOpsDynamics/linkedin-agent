"""
Scanner Agent -- pulls raw candidate stories from configured sources.
Run standalone: python src/scanner.py
"""
import os
import yaml
import feedparser
import requests
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sources.yaml"
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")  # https://newsapi.org


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def fetch_rss(feed_conf):
    """Returns list of {title, summary, link, source, published}."""
    items = []
    try:
        parsed = feedparser.parse(feed_conf["url"])
        for entry in parsed.entries[:15]:
            items.append({
                "title": entry.get("title", "").strip(),
                "summary": entry.get("summary", "")[:500],
                "link": entry.get("link", ""),
                "source": feed_conf["name"],
                "published": entry.get("published", ""),
            })
    except Exception as e:
        print(f"[scanner] WARN: failed to fetch {feed_conf['name']}: {e}")
    return items


def fetch_newsapi(query):
    """Returns list of {title, summary, link, source, published}."""
    if not NEWSAPI_KEY:
        print("[scanner] NEWSAPI_KEY not set, skipping NewsAPI queries")
        return []
    items = []
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 10,
                "apiKey": NEWSAPI_KEY,
            },
            timeout=15,
        )
        resp.raise_for_status()
        for article in resp.json().get("articles", []):
            items.append({
                "title": (article.get("title") or "").strip(),
                "summary": (article.get("description") or "")[:500],
                "link": article.get("url", ""),
                "source": article.get("source", {}).get("name", "NewsAPI"),
                "published": article.get("publishedAt", ""),
            })
    except Exception as e:
        print(f"[scanner] WARN: NewsAPI query '{query}' failed: {e}")
    return items


def scan_all():
    config = load_config()
    all_items = []

    for feed_conf in config.get("rss_feeds", []):
        all_items.extend(fetch_rss(feed_conf))

    for query in config.get("news_api_queries", []):
        all_items.extend(fetch_newsapi(query))

    # de-dupe within this batch by title
    seen_titles = set()
    unique_items = []
    for item in all_items:
        key = item["title"].lower().strip()
        if key and key not in seen_titles:
            seen_titles.add(key)
            unique_items.append(item)

    return unique_items


if __name__ == "__main__":
    results = scan_all()
    print(f"[scanner] Collected {len(results)} unique candidate items")
    for r in results[:10]:
        print(f"  - [{r['source']}] {r['title']}")
