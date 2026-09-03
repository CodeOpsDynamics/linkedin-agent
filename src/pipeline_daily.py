"""
Daily pipeline -- Phase 1 + 2.

Run: python -m src.pipeline_daily

Flow:
  Scanner -> Dedup -> Impact Classifier -> store candidates -> notify (Telegram)

The Writer Agent (writer.py) is invoked separately once you've confirmed a
classification via /confirm, /post, /article, or /carousel -- that command
handler lives in the Phase 4 webhook receiver, not here.

Quality-over-quantity cap (Phase 6): 2026 LinkedIn platform data shows one
strong post consistently outperforms several mediocre ones, and low-quality/
filler content gets actively penalized by the ranking algorithm. Flooding
Telegram with every borderline fresh item just adds decision fatigue and
dilutes the content pillars. So classified items are filtered to those above
MIN_CONFIDENCE, sorted by confidence, and only the top
MAX_CANDIDATES_PER_DAY are actually surfaced for review -- everything else
is still marked seen (won't resurface tomorrow as "new") but silently
skipped rather than sent to you. Both are overridable via env vars if the
default feels too tight or too loose.
"""
import os
from src import scanner, dedup, classifier, state_store
from src.notifier_telegram import notify_candidates

MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "0.6"))
MAX_CANDIDATES_PER_DAY = int(os.environ.get("MAX_CANDIDATES_PER_DAY", "3"))


def run():
    state_store.init_db()

    print("[pipeline] Scanning sources...")
    raw_items = scanner.scan_all()
    print(f"[pipeline] {len(raw_items)} raw items collected")

    fresh_items = dedup.filter_new(raw_items)
    print(f"[pipeline] {len(fresh_items)} fresh (unseen) items")

    if not fresh_items:
        print("[pipeline] Nothing new today.")
        return

    classified = classifier.classify_batch(fresh_items)
    print(f"[pipeline] {len(classified)} items classified")

    qualifying = [item for item in classified if item["confidence"] >= MIN_CONFIDENCE]
    qualifying.sort(key=lambda item: item["confidence"], reverse=True)
    top_items = qualifying[:MAX_CANDIDATES_PER_DAY]
    print(
        f"[pipeline] {len(qualifying)} item(s) above confidence {MIN_CONFIDENCE}, "
        f"surfacing top {len(top_items)} (cap {MAX_CANDIDATES_PER_DAY})"
    )

    candidates_with_ids = []
    for item in top_items:
        candidate_id = state_store.add_candidate(
            title=item["title"],
            summary=item.get("summary", ""),
            source=item.get("source", ""),
            link=item.get("link", ""),
            suggested_type=item["classification"],
            confidence=item["confidence"],
            reasoning=item["reasoning"],
        )
        candidates_with_ids.append((candidate_id, item))

    # Mark ALL fresh items seen (not just the surfaced top few) -- otherwise
    # a good story that lost out on today's cap would look "new" again
    # tomorrow and get reclassified/resurfaced pointlessly.
    dedup.mark_processed(fresh_items)

    if candidates_with_ids:
        notify_candidates(candidates_with_ids)
        print(f"[pipeline] Sent {len(candidates_with_ids)} candidates for your review.")
    else:
        print("[pipeline] Nothing cleared the quality bar today -- no candidates sent.")


if __name__ == "__main__":
    run()
