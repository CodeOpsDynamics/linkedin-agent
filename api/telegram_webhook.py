"""
Vercel serverless function -- Telegram webhook receiver (Phase 4).

Deploy path: api/telegram_webhook.py (Vercel auto-detects files under api/
as serverless functions using the Python runtime).

What it does: Telegram calls this URL every time you send a message to the
bot. We parse your command, generate the draft, publish to LinkedIn, and
reply back on Telegram with the live post link -- fully automated, no
terminal, no manual script.

One-time setup after deploying:
    curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<your-vercel-app>.vercel.app/api/telegram_webhook"

Env vars needed on Vercel (Project Settings -> Environment Variables):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY,
    LINKEDIN_ACCESS_TOKEN, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
"""
import os
import sys
import json
import requests
from http.server import BaseHTTPRequestHandler

# repo root needs to be importable so `from src import ...` works when
# Vercel bundles this function alongside the rest of the project
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import state_store, writer, linkedin_publish  # noqa: E402

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def reply(chat_id: str, text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
    )


def post_url_from_urn(urn: str) -> str:
    # urn looks like "urn:li:share:1234567890" -- LinkedIn's shareable web URL
    return f"https://www.linkedin.com/feed/update/{urn}/"


def handle_command(chat_id: str, text: str):
    parts = text.strip().split()
    if not parts:
        return

    command = parts[0].lower()

    if command not in ("/confirm", "/post", "/article", "/skip"):
        return  # ignore anything that isn't one of our commands

    if len(parts) < 2 or not parts[1].isdigit():
        reply(chat_id, "Usage: /confirm <id>, /post <id>, /article <id>, or /skip <id>")
        return

    candidate_id = int(parts[1])
    candidate = state_store.get_candidate(candidate_id)
    if not candidate:
        reply(chat_id, f"No candidate found with id {candidate_id}.")
        return

    if command == "/skip":
        state_store.mark_candidate_skipped(candidate_id)
        reply(chat_id, f"Candidate #{candidate_id} skipped.")
        return

    confirmed_type = candidate["suggested_type"] if command == "/confirm" else command.strip("/")

    reply(chat_id, f"Writing {confirmed_type} for '{candidate['title']}'...")

    state_store.mark_candidate_confirmed(candidate_id, confirmed_type)
    draft_text = writer.write_draft(candidate, confirmed_type)
    draft_id = state_store.add_draft(candidate_id, confirmed_type, draft_text)

    access_token = linkedin_publish.get_access_token()
    post_urn = linkedin_publish.publish_post(draft_text, access_token=access_token)
    state_store.mark_draft_published(draft_id, post_urn)

    first_comment = writer.suggest_first_comment_link(candidate)
    if first_comment:
        linkedin_publish.post_first_comment(post_urn, first_comment, access_token=access_token)

    reply(
        chat_id,
        f"Published! {post_url_from_urn(post_urn)}\n\n"
        f"Source link added as first comment.",
    )


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            update = json.loads(body)
            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "")

            # security: only act on messages from your own chat
            if chat_id and chat_id == str(ALLOWED_CHAT_ID) and text:
                handle_command(chat_id, text)

        except Exception as e:
            print(f"[webhook] ERROR: {e}")
            if ALLOWED_CHAT_ID:
                reply(ALLOWED_CHAT_ID, f"Something went wrong processing that: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())
