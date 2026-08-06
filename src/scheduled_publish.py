"""
Daily scheduled publish -- Phase 5.

Runs twice a day via GitHub Actions cron, each invocation scoped to one
draft type, so LinkedIn output timing stays consistent for the algorithm
regardless of when you happen to approve via /publish in Telegram:

    python -m src.scheduled_publish post      # morning slot
    python -m src.scheduled_publish article   # evening slot

Called with no argument, it publishes the oldest queued draft of ANY type
(kept for backward compatibility / manual runs).
"""
import sys
import os
import requests
from src import state_store, writer, linkedin_publish

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )


def post_url_from_urn(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def run(draft_type: str = None):
    state_store.init_db()
    draft = state_store.get_next_queued_draft(draft_type=draft_type)

    if not draft:
        print(f"[scheduled_publish] Nothing queued for {draft_type or 'any type'} today.")
        return

    draft_id = draft["id"]
    candidate = state_store.get_candidate(draft["candidate_id"])

    print(f"[scheduled_publish] Publishing draft #{draft_id} ({draft['draft_type']})...")

    token = linkedin_publish.get_access_token()
    urn = linkedin_publish.publish_post(draft["draft_text"], access_token=token)

    state_store.mark_draft_published(draft_id, urn)
    state_store.mark_candidate_published(draft["candidate_id"])

    comment_note = ""
    if candidate:
        comment = writer.suggest_first_comment_link(candidate)
        if comment:
            try:
                linkedin_publish.post_first_comment(urn, comment, access_token=token)
            except Exception as comment_err:
                print("post_first_comment failed:", comment_err)
                comment_note = (
                    f"\n\n(Couldn't auto-add the source comment -- add it "
                    f"manually if you want:\n{comment})"
                )

    notify(f"Today's {draft['draft_type']} is live!\n{post_url_from_urn(urn)}{comment_note}")
    print(f"[scheduled_publish] Done: {urn}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
