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
@app.get("/")
async def health():
    return {"status": "ok"}

@app.get("/api/telegram_webhook")
async def webhook_health():
    return {"status": "ok"}

def reply(chat_id, text):
    print("Sending Telegram reply:", text)
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
    except Exception as e:
        print("Telegram reply failed:", e)


def post_url_from_urn(urn):
    return f"https://www.linkedin.com/feed/update/{urn}/"


def handle_command(chat_id, text):
    print("Entered handle_command")
    try:
        parts = text.strip().split()

        if len(parts) < 2:
            reply(chat_id, "Usage: /confirm <id>")
            return
        
        cmd = parts[0].lower()

        if cmd not in ("/confirm", "/post", "/article", "/skip"):
            reply(chat_id, "Unknown command.")
            return

        if not parts[1].isdigit():
            reply(chat_id, "Candidate id must be a number.")
            return

        candidate_id = int(parts[1])
        
        state_store.init_db()

        candidate = state_store.get_candidate(candidate_id)

        if candidate and candidate.get("status") == "published":
            reply(chat_id, "✅ This candidate has already been published.")
            return

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
        print("Webhook token:", repr(token[:20]) if token else "EMPTY")
        print("Webhook token length:", len(token) if token else 0)

        urn = linkedin_publish.publish_post(
            draft,
            access_token=token,
        )

        state_store.mark_draft_published(
            draft_id,
            urn,
        )
        
        state_store.mark_candidate_published(candidate_id)

        comment = writer.suggest_first_comment_link(candidate)

        if comment:
            linkedin_publish.post_first_comment(
                urn,
                comment,
                access_token=token,
            )

        reply(
            chat_id,
            f"✅ Published!\n{post_url_from_urn(urn)}",
        )

    except Exception as e:
        print(e)
        try:
            reply(chat_id, f"❌ Error:\n{e}")
        except Exception:
            pass


@app.post("/")
@app.post("/api/telegram_webhook")
async def webhook(request: Request):
    try:
        body = await request.json()

        update_id = body.get("update_id")
        print(f"Received Telegram update: {update_id}")

        state_store.init_db()

        # Duplicate Telegram update? Ignore it.
        if update_id is not None:
            if not state_store.try_mark_update_processed(update_id):
                print(f"Ignoring duplicate update {update_id}")
                return JSONResponse({"ok": True})

        message = body.get("message", {})

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")

        print("Chat ID:", chat_id)
        print("Allowed Chat ID:", ALLOWED_CHAT_ID)
        print("Text:", text)

        if chat_id == ALLOWED_CHAT_ID and text:
            print("Calling handle_command()")
            handle_command(
                chat_id,
                text,
            )
        else:
            print("Ignored request")
            
        return JSONResponse({"ok": True})

    except Exception as e:
        print("Webhook Exception:", e)

        # Always return 200 so Telegram doesn't keep retrying
        return JSONResponse({"ok": True})