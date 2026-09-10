"""
Notifier -- Telegram, Phase 1 (one-way send).
"""
import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TELEGRAM_MAX_CHARS = 4096  # Telegram's hard per-message limit for sendMessage


def _send_single_message(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[notifier] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- printing instead:")
        print(text)
        return True
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )
    if not resp.ok:
        print(f"[notifier] WARN: Telegram API rejected message (status {resp.status_code}): {resp.text}")
        return False
    return True


def send_message(text: str):
    """Phase 7's auto-drafted articles (title + full body + cover-image
    brief + teaser post + instructions) routinely exceed Telegram's
    4096-char single-message limit -- same issue telegram_webhook.py's
    reply() hit and fixed for interactive replies (see that function's
    docstring). This mirrors the same fix here so pipeline-initiated
    messages (which don't go through reply()) don't silently get rejected
    or truncated just because a draft ran long."""
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


def format_candidate_message(candidate_id: int, item: dict) -> str:
    """Kept for any manual/legacy use -- the normal daily flow no longer
    calls this (pipeline_daily.py now auto-drafts and sends the finished
    draft directly via send_message, see that module's Phase 7 docstring)."""
    conf_pct = round(item.get("confidence", 0) * 100)
    return (
        f"New candidate #{candidate_id}\n\n"
        f"{item['title']}\n"
        f"Source: {item.get('source', 'unknown')}\n\n"
        f"Suggested: {item.get('classification', 'post').upper()} ({conf_pct}% confidence)\n"
        f"Reasoning: {item.get('reasoning', '')}\n\n"
        f"Link: {item.get('link', 'n/a')}\n\n"
        f"Reply with:\n"
        f"/confirm {candidate_id} to draft using suggested type\n"
        f"/post {candidate_id} to draft as POST\n"
        f"/article {candidate_id} to draft as ARTICLE\n"
        f"/skip {candidate_id} to drop it\n\n"
        f"(drafting does not publish -- you'll get a draft_id and a "
        f"separate /publish step to queue it for the next scheduled push)"
    )


def notify_candidates(classified_items_with_ids):
    for candidate_id, item in classified_items_with_ids:
        send_message(format_candidate_message(candidate_id, item))
