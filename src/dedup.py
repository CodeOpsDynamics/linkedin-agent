"""
Dedup Agent -- filters out topics we've already covered or already surfaced.
"""
from src import state_store


def filter_new(items):
    """Given scanner output, return only items not already in seen_topics."""
    fresh = []
    for item in items:
        if not item["title"]:
            continue
        if not state_store.is_seen(item["title"]):
            fresh.append(item)
    return fresh


def mark_processed(items):
    for item in items:
        state_store.mark_seen(item["title"], item["source"], item["link"])
