from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import sys
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import state_store, writer, linkedin_publish

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))

app = FastAPI()


def reply(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )


def post_url_from_urn(urn):
    return f"https://www.linkedin.com/feed/update/{urn}/"


def handle_command(chat_id, text):
    parts = text.strip().split()

    if len(parts) < 2:
        reply(chat_id, "Usage: /confirm <id>")
        return

    cmd = parts[0].lower()
    candidate_id = int(parts[1])

    state_store.init_db()

    candidate = state_store.get_candidate(candidate_id)

    if not candidate:
        reply(chat_id, "Candidate not found.")
        return

    if cmd == "/skip":
        state_store.mark_candidate_skipped(candidate_id)
        reply(chat_id, "Skipped.")
        return

    confirmed_type = (
        candidate["suggested_type"]
        if cmd == "/confirm"
        else cmd.replace("/", "")
    )

    reply(chat_id, f"Writing {confirmed_type}...")

    state_store.mark_candidate_confirmed(candidate_id, confirmed_type)

    draft = writer.write_draft(candidate, confirmed_type)

    draft_id = state_store.add_draft(
        candidate_id,
        confirmed_type,
        draft,
    )

    token = linkedin_publish.get_access_token()

    urn = linkedin_publish.publish_post(
        draft,
        access_token=token,
    )

    state_store.mark_draft_published(
        draft_id,
        urn,
    )

    comment = writer.suggest_first_comment_link(candidate)

    if comment:
        linkedin_publish.post_first_comment(
            urn,
            comment,
            access_token=token,
        )

    reply(
        chat_id,
        f"Published!\n{post_url_from_urn(urn)}",
    )


@app.post("/")
@app.post("/api/telegram_webhook")
async def webhook(request: Request):
    try:
        body = await request.json()

        message = body.get("message", {})

        chat_id = str(message.get("chat", {}).get("id", ""))

        text = message.get("text", "")

        if chat_id == ALLOWED_CHAT_ID and text:
            handle_command(chat_id, text)

        return JSONResponse({"ok": True})

    except Exception as e:
        print(e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)