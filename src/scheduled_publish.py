"""
Daily scheduled publish -- Phase 5.

Runs twice a day via GitHub Actions cron, each invocation scoped to one
draft type, so LinkedIn output timing stays consistent for the algorithm
regardless of when you happen to approve via /publish in Telegram:

    python -m src.scheduled_publish post      # morning slot
    python -m src.scheduled_publish article   # evening slot

Called with no argument, it publishes the oldest queued draft of ANY type
(kept for backward compatibility / manual runs).

IMPORTANT: LinkedIn's native Articles tab (title + cover image + rich body)
has NO API support on any access tier -- there is no endpoint to publish to
it, full stop. So the "article" slot does NOT call the LinkedIn API. It
instead delivers a copy-paste-ready package (title, body, cover-image
keyword suggestion) to Telegram, and marks the draft/candidate as
'delivered_manual' rather than 'published' -- the actual publish click in
LinkedIn's Articles editor stays a manual, ~30-second step for Himanshu.
The "post" slot is unaffected and continues to auto-publish via the API.
"""
import sys
import os
import requests
from src import state_store, writer, linkedin_publish

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


TELEGRAM_MAX_CHARS = 4096  # Telegram's hard per-message limit for sendMessage


def _send_single_message(text: str) -> bool:
    """One actual API call. Returns True on success and logs the REAL
    reason on failure instead of swallowing it -- previously this only
    caught network-level exceptions (timeouts, connection errors); if
    Telegram's API itself rejected the message (e.g. 400 Bad Request for
    exceeding the 4096-char limit), requests.post() doesn't raise anything,
    so a rejected article delivery from tonight's cron run would vanish
    with zero trace anywhere. Checking response.ok surfaces that case."""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
            timeout=20,
        )
        if not resp.ok:
            print(f"[scheduled_publish] Telegram API rejected message (status {resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        print(f"[scheduled_publish] Telegram send failed: {e}")
        return False


def notify(text: str):
    """An article delivery message (title + full 800-1200 word body +
    cover-image brief + instructions) routinely exceeds Telegram's 4096-char
    single-message limit, so long text is split on paragraph breaks into
    multiple messages sent in order -- see telegram_webhook.py's reply()
    for the same pattern used on the interactive-command side."""
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return

    if len(text) <= TELEGRAM_MAX_CHARS:
        _send_single_message(text)
        return

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_MAX_CHARS:
            chunks.append(remaining)
            break
        window = remaining[:TELEGRAM_MAX_CHARS]
        split_at = window.rfind("\n\n")
        if split_at < TELEGRAM_MAX_CHARS * 0.5:
            split_at = TELEGRAM_MAX_CHARS
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        prefix = f"[{i}/{total}]\n" if total > 1 else ""
        _send_single_message(prefix + chunk)


def post_url_from_urn(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def deliver_article_package(draft: dict, candidate: dict):
    """Hands the finished article to Himanshu on Telegram for the manual
    paste-into-Articles-tab step -- see module docstring for why. Also
    resurfaces the teaser post generated at draft time: articles have no
    in-app auto-share, so this is the copy-paste post that drives people
    to it, published separately once the article's live, with the
    article's URL added as that teaser's first comment."""
    title = draft.get("title") or "(no title generated -- add one manually)"
    image_brief = draft.get("image_brief") or ""
    image_link = writer.build_image_search_link(image_brief) if image_brief else ""
    teaser_post = draft.get("teaser_post") or ""

    comment_note = ""
    if candidate:
        comment = writer.suggest_first_comment_link(candidate)
        if comment:
            comment_note = f"\n\nSource credit (add as a comment after publishing):\n{comment}"

    teaser_block = (
        f"\n\n---\nSuggested teaser post (publish separately once the "
        f"article's live -- add the article's URL as the FIRST COMMENT on "
        f"this teaser, not in its body):\n\n{teaser_post}"
        if teaser_post else ""
    )

    notify(
        f"Tonight's article is ready -- LinkedIn's Articles tab has no API "
        f"access, so this needs your 30-second manual step:\n\n"
        f"1. linkedin.com -> Write article\n"
        f"2. Title: {title}\n"
        f"3. Paste this body:\n\n{draft['draft_text']}\n\n"
        f"4. Cover image ({writer.ARTICLE_IMAGE_SPEC}): {image_brief}"
        + (f"\nQuick search: {image_link}" if image_link else "")
        + comment_note
        + teaser_block
    )


def run(draft_type: str = None):
    state_store.init_db()
    draft = state_store.get_next_queued_draft(draft_type=draft_type)

    if not draft:
        print(f"[scheduled_publish] Nothing queued for {draft_type or 'any type'} today.")
        return

    draft_id = draft["id"]
    candidate = state_store.get_candidate(draft["candidate_id"])

    if draft["draft_type"] == "article":
        print(f"[scheduled_publish] Delivering draft #{draft_id} (article) for manual publish...")
        deliver_article_package(draft, candidate)
        state_store.mark_draft_delivered_manual(draft_id)
        state_store.mark_candidate_delivered_manual(draft["candidate_id"])
        print(f"[scheduled_publish] Done: #{draft_id} delivered for manual publish")
        return

    print(f"[scheduled_publish] Publishing draft #{draft_id} (post)...")

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
    run(sys.argv[1] if len(sys.argv) > 1 else None)
