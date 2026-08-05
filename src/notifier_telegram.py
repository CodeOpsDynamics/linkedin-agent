"""
Notifier -- Telegram, Phase 1 (one-way send).
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
        f"`/confirm {candidate_id}` to draft using suggested type\n"
        f"`/post {candidate_id}` to draft as POST\n"
        f"`/article {candidate_id}` to draft as ARTICLE\n"
        f"`/skip {candidate_id}` to drop it\n\n"
        f"_(drafting does not publish -- you'll get a draft_id and a "
        f"separate `/publish` step to actually push it live)_"
    )


def notify_candidates(classified_items_with_ids):
    for candidate_id, item in classified_items_with_ids:
        send_message(format_candidate_message(candidate_id, item))
