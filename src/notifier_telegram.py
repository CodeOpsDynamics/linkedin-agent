"""
Notifier -- Telegram, Phase 1 (one-way send).

Sends each classified candidate to you as a message. Reply/approval handling
(Phase 4) needs a small always-on webhook receiver -- GitHub Actions cron
can't sit and listen for your reply between runs. See README "Phase 4" for
the Vercel-based approach (same shape as the Jyoti Darshan proxy).

For now: this posts suggestions to your Telegram so you have full visibility
into what the pipeline found and how it classified things, even before the
approval loop is wired up.
"""
import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_message(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[notifier] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- printing instead:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    })
    if not resp.ok:
        print(f"[notifier] WARN: Telegram send failed: {resp.text}")


def format_candidate_message(candidate_id: int, item: dict) -> str:
    conf_pct = round(item.get("confidence", 0) * 100)
    return (
        f"*New candidate #{candidate_id}*\n\n"
        f"*{item['title']}*\n"
        f"Source: {item.get('source', 'unknown')}\n\n"
        f"Suggested: *{item.get('classification', 'post').upper()}* ({conf_pct}% confidence)\n"
        f"Reasoning: {item.get('reasoning', '')}\n\n"
        f"Link: {item.get('link', 'n/a')}\n\n"
        f"Reply with:\n"
        f"`/confirm {candidate_id}` to accept as suggested\n"
        f"`/post {candidate_id}` to force POST\n"
        f"`/article {candidate_id}` to force ARTICLE\n"
        f"`/skip {candidate_id}` to drop it"
    )


def notify_candidates(classified_items_with_ids):
    """classified_items_with_ids: list of (candidate_id, item_dict)"""
    for candidate_id, item in classified_items_with_ids:
        send_message(format_candidate_message(candidate_id, item))
