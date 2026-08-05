"""
Daily scheduled publish -- Phase 5.

Run once a day at 10:30 AM IST via GitHub Actions cron. Publishes the
single oldest queued draft (if any), keeping output to one quality post
per day at the highest-reach window rather than publishing the instant
you approve via /publish in Telegram.

Run: python -m src.scheduled_publish
"""
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


def run():
    state_store.init_db()
    draft = state_store.get_next_queued_draft()

    if not draft:
        print("[scheduled_publish] Nothing queued today.")
        return

    draft_id = draft["id"]
    candidate = state_store.get_candidate(draft["candidate_id"])

    print(f"[scheduled_publish] Publishing draft #{draft_id}...")

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

    notify(f"Today's post is live!\n{post_url_from_urn(urn)}{comment_note}")
    print(f"[scheduled_publish] Done: {urn}")


if __name__ == "__main__":
    run()
