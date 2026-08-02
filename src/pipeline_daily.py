"""
Daily pipeline -- Phase 1 + 2.

Run: python -m src.pipeline_daily

Flow:
  Scanner -> Dedup -> Impact Classifier -> store candidates -> notify (Telegram)

The Writer Agent (writer.py) is invoked separately once you've confirmed a
classification via /confirm, /post, or /article -- that command handler lives
in the Phase 4 webhook receiver, not here. For now, this script logs
confirmed-but-not-yet-written candidates so you can manually trigger writing
via `python -m src.write_confirmed` during the bridge period before Phase 4
is deployed.
"""
from src import scanner, dedup, classifier, state_store
from src.notifier_telegram import notify_candidates


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

    candidates_with_ids = []
    for item in classified:
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

    dedup.mark_processed(fresh_items)

    notify_candidates(candidates_with_ids)
    print(f"[pipeline] Sent {len(candidates_with_ids)} candidates for your review.")


if __name__ == "__main__":
    run()
