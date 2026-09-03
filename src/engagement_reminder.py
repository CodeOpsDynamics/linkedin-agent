"""
Golden-hour engagement reminder -- Phase 6.

Run: python -m src.engagement_reminder

LinkedIn's 2026 ranking samples a small slice of your followers in the
first ~60-90 minutes after a post goes up (the "Testing Ground" / "Golden
Hour") and watches how they engage before deciding whether to expand
distribution further. Comments carry roughly 15x the algorithmic weight of
likes, and replying to every comment on your own post in that window is one
of the highest-leverage, lowest-effort things you can do -- but it's easy
to miss if you're not on your phone right when it goes live.

This runs on a fixed schedule ~45 minutes after the morning post slot
(.github/workflows/engagement_reminder.yml) and nudges you on Telegram
ONLY if a post-type draft actually published TODAY (checked via
state_store.get_most_recent_published_post) -- no point pinging you on a
day nothing went out. Articles/carousels are manual-delivery, so this
intentionally only covers auto-published "post" drafts, where we know the
exact publish time.
"""
import os
import requests
from datetime import datetime, timezone
from src import state_store

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )
    if not resp.ok:
        print(f"[engagement_reminder] WARN: Telegram send failed: {resp.text}")


def post_url_from_urn(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def run():
    state_store.init_db()

    draft = state_store.get_most_recent_published_post()
    if not draft:
        print("[engagement_reminder] No published post found at all -- skipping.")
        return

    published_at = draft.get("published_at") or ""
    published_date = published_at[:10]
    today = datetime.now(timezone.utc).date().isoformat()

    if published_date != today:
        print(
            f"[engagement_reminder] Most recent published post was on "
            f"{published_date or 'unknown date'}, not today ({today}) -- skipping."
        )
        return

    urn = draft.get("linkedin_post_urn")
    url = post_url_from_urn(urn) if urn else ""

    notify(
        "Golden hour check-in: your post from this morning is still in "
        "LinkedIn's early distribution window (first ~60-90 min matter "
        "most for reach). If you haven't already, reply to every comment "
        "now -- comments carry roughly 15x the ranking weight of likes, "
        "and an active reply thread gets pushed to a much wider audience."
        + (f"\n\n{url}" if url else "")
    )
    print("[engagement_reminder] Nudge sent.")


if __name__ == "__main__":
    run()
