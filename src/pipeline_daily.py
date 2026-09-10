"""
Daily pipeline -- Phase 1, 2, and 7 (auto-draft).

Run: python -m src.pipeline_daily

Flow:
  Scanner -> Dedup -> Impact Classifier -> AUTO-GENERATE draft -> notify (Telegram)

Quality-over-quantity cap (Phase 6): 2026 LinkedIn platform data shows one
strong post consistently outperforms several mediocre ones, and low-quality/
filler content gets actively penalized by the ranking algorithm. Classified
items are filtered to those above MIN_CONFIDENCE and pillar_fit, sorted by
confidence, and only the top MAX_CANDIDATES_PER_DAY are surfaced -- the rest
are still marked seen (won't resurface tomorrow) but silently skipped.

Auto-draft (Phase 7): previously this step just asked Himanshu to pick a
command (/post, /article, /carousel, /newtech) per candidate every single
day -- a manual decision repeated for every item, every day, forever. Now
the pipeline makes that call itself:
  - pillar == "new_tech_vs_legacy" -> the fact-checked tech-eval carousel
    (writer.generate_tech_eval_package), regardless of post/article split,
    since that pillar's whole value IS the verified-carousel format.
  - otherwise -> whatever the classifier already suggested (post/article).
The finished draft (not just the raw candidate) is what lands in Telegram
each morning -- /publishnow and /discard are the only decisions left.
Manual /post, /article, /carousel, /newtech still exist in
telegram_webhook.py as overrides for a different angle on a specific
candidate; they're just no longer required for the normal daily flow.
"""
import os
from src import scanner, dedup, classifier, state_store, writer
from src.notifier_telegram import send_message

MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "0.6"))
MAX_CANDIDATES_PER_DAY = int(os.environ.get("MAX_CANDIDATES_PER_DAY", "3"))


def _auto_draft_type(item: dict) -> str:
    if item.get("pillar") == "new_tech_vs_legacy":
        return "carousel"
    return item.get("classification", "post")


def _generate_and_store_draft(candidate_id: int, candidate: dict, confirmed_type: str, pillar: str) -> dict:
    """Mirrors telegram_webhook.py's do_draft/do_carousel_draft/
    do_newtech_draft generation step, just without a chat_id -- this runs
    inside the GitHub Actions job, not a Telegram webhook call, so there's
    no Vercel 60s ceiling to worry about here either."""
    if pillar == "new_tech_vs_legacy":
        package = writer.generate_tech_eval_package(candidate)
    elif confirmed_type == "carousel":
        package = writer.generate_carousel_package(candidate)
    else:
        package = writer.generate_draft_package(candidate, confirmed_type)

    state_store.mark_candidate_confirmed(candidate_id, confirmed_type)

    draft_text = package["draft_text"]
    hashtags = package.get("hashtags", "")
    if confirmed_type == "carousel" and hashtags:
        draft_text = f"{draft_text}\n\n{hashtags}"

    draft_id = state_store.add_draft(
        candidate_id, confirmed_type, draft_text,
        title=package.get("title"),
        image_brief=package.get("image_brief"),
        teaser_post=package.get("teaser_post"),
    )

    return {
        "draft_id": draft_id,
        "draft_text": draft_text,
        "package": package,
        "confirmed_type": confirmed_type,
    }


def _format_ready_message(candidate_id: int, title: str, result: dict) -> str:
    draft_id = result["draft_id"]
    confirmed_type = result["confirmed_type"]
    draft_text = result["draft_text"]
    package = result["package"]

    if confirmed_type == "article":
        article_title = package.get("title") or "(no title generated -- add one manually)"
        image_brief = package.get("image_brief") or ""
        image_link = writer.build_image_search_link(image_brief) if image_brief else ""
        teaser_post = package.get("teaser_post") or ""
        teaser_block = (
            f"\n---\nSuggested teaser post (publish separately once the "
            f"article's live -- article URL as the FIRST COMMENT on the "
            f"teaser, not in its body):\n\n{teaser_post}\n"
            if teaser_post else ""
        )
        return (
            f"Today's article draft -- #{draft_id} (candidate #{candidate_id}): {title}\n\n"
            f"Title: {article_title}\n\n{draft_text}\n\n"
            f"---\nCover image ({writer.ARTICLE_IMAGE_SPEC}): {image_brief}\n"
            + (f"Quick search: {image_link}\n" if image_link else "")
            + teaser_block
            + f"\n---\nLinkedIn's Articles tab has no API access -- reply "
              f"/publishnow {draft_id} when ready for the copy-paste "
              f"package, or /discard {draft_id} to drop it."
        )

    if confirmed_type == "carousel":
        image_brief = package.get("image_brief") or ""
        return (
            f"Today's carousel draft -- #{draft_id} (candidate #{candidate_id}): {title}\n\n"
            f"{draft_text}\n\n---\nCover keywords: {image_brief}\n"
            f"Reply /publishnow {draft_id} to render the PDF and publish it, "
            f"or /discard {draft_id} to drop it."
        )

    return (
        f"Today's post draft -- #{draft_id} (candidate #{candidate_id}): {title}\n\n"
        f"{draft_text}\n\n---\n"
        f"Reply /publishnow {draft_id} to publish immediately, /publish "
        f"{draft_id} to queue for tomorrow morning's slot, or /discard "
        f"{draft_id} to drop it."
    )


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

    qualifying = [
        item for item in classified
        if item["confidence"] >= MIN_CONFIDENCE and item.get("pillar_fit", True)
    ]
    qualifying.sort(key=lambda item: item["confidence"], reverse=True)
    top_items = qualifying[:MAX_CANDIDATES_PER_DAY]
    off_pillar_count = sum(1 for item in classified if not item.get("pillar_fit", True))
    print(
        f"[pipeline] {off_pillar_count} item(s) dropped as off-pillar, "
        f"{len(qualifying)} qualifying above confidence {MIN_CONFIDENCE}, "
        f"surfacing top {len(top_items)} (cap {MAX_CANDIDATES_PER_DAY})"
    )

    drafted = 0
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
        candidate = state_store.get_candidate(candidate_id)
        pillar = item.get("pillar", "none")
        confirmed_type = _auto_draft_type(item)

        try:
            result = _generate_and_store_draft(candidate_id, candidate, confirmed_type, pillar)
        except Exception as e:
            print(f"[pipeline] WARN: auto-draft failed for candidate #{candidate_id} ('{item['title']}'): {e}")
            send_message(
                f"Skipped candidate #{candidate_id} ({item['title']}) -- "
                f"auto-draft failed:\n{e}\n\n"
                f"Try a different angle manually if it's still worth "
                f"covering: /post {candidate_id}, /article {candidate_id}, "
                f"/carousel {candidate_id}, or /skip {candidate_id} to drop it."
            )
            continue

        send_message(_format_ready_message(candidate_id, item["title"], result))
        drafted += 1

    # Mark ALL fresh items seen (not just the surfaced/drafted ones) --
    # otherwise a story that lost the cap, or failed to draft, would look
    # "new" again tomorrow and get reclassified pointlessly.
    dedup.mark_processed(fresh_items)

    if top_items:
        print(f"[pipeline] Drafted and sent {drafted}/{len(top_items)} candidate(s) for review.")
    else:
        print("[pipeline] Nothing cleared the quality bar today -- no candidates sent.")


if __name__ == "__main__":
    run()
