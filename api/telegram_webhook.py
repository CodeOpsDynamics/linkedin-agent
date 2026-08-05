from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import sys
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import state_store, writer, linkedin_publish

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

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


def do_draft(chat_id, candidate_id, requested_type):
    """/post, /article, /confirm all land here -- generates and SAVES a
    draft, replies with the text + a draft_id, but does NOT publish."""
    candidate = state_store.get_candidate(candidate_id)

    if not candidate:
        reply(chat_id, "Candidate not found.")
        return

    if candidate.get("status") == "published":
        reply(chat_id, "This candidate has already been published.")
        return

    confirmed_type = (
        candidate["suggested_type"] if requested_type == "confirm" else requested_type
    )

    reply(chat_id, f"Writing {confirmed_type} draft for review...")

    state_store.mark_candidate_confirmed(candidate_id, confirmed_type)
    draft_text = writer.write_draft(candidate, confirmed_type)
    draft_id = state_store.add_draft(candidate_id, confirmed_type, draft_text)

    reply(
        chat_id,
        f"Draft #{draft_id} ({confirmed_type}):\n\n{draft_text}\n\n"
        f"---\n"
        f"Reply /publish {draft_id} to push this live on LinkedIn.\n"
        f"Reply /discard {draft_id} to cancel this draft.",
    )


def do_publish(chat_id, draft_id):
    draft = state_store.get_draft(draft_id)

    if not draft:
        reply(chat_id, "Draft not found.")
        return

    if draft.get("status") == "published":
        reply(chat_id, "This exact draft has already been published -- not posting again.")
        return

    if draft.get("status") == "rejected":
        reply(chat_id, "This draft was discarded. Generate a new one with /post or /article.")
        return

    candidate = state_store.get_candidate(draft["candidate_id"])
    if candidate and candidate.get("status") == "published":
        reply(chat_id, "This candidate was already published (via a different draft). Not posting again.")
        return

    token = linkedin_publish.get_access_token()

    urn = linkedin_publish.publish_post(draft["draft_text"], access_token=token)

    state_store.mark_draft_published(draft_id, urn)
    state_store.mark_candidate_published(draft["candidate_id"])

    # Best-effort source-link comment -- standard-tier LinkedIn apps usually
    # lack permission for this endpoint. A failure here must not look like
    # the publish itself failed, since it already succeeded above.
    comment_note = ""
    if candidate:
        comment = writer.suggest_first_comment_link(candidate)
        if comment:
            try:
                linkedin_publish.post_first_comment(urn, comment, access_token=token)
            except Exception as comment_err:
                print("post_first_comment failed:", comment_err)
                comment_note = (
                    f"\n\n(Couldn't auto-add the source comment -- your "
                    f"LinkedIn app doesn't have permission for that "
                    f"endpoint. Add it manually if you want:\n{comment})"
                )

    reply(chat_id, f"Published!\n{post_url_from_urn(urn)}{comment_note}")


def do_discard(chat_id, draft_id):
    draft = state_store.get_draft(draft_id)
    if not draft:
        reply(chat_id, "Draft not found.")
        return
    if draft.get("status") == "published":
        reply(chat_id, "Can't discard -- this draft is already published.")
        return
    state_store.mark_draft_rejected(draft_id)
    reply(chat_id, f"Draft #{draft_id} discarded.")


def handle_command(chat_id, text):
    print("Entered handle_command")
    try:
        parts = text.strip().split()

        if len(parts) < 2:
            reply(
                chat_id,
                "Usage:\n/post <candidate_id>\n/article <candidate_id>\n"
                "/confirm <candidate_id>\n/skip <candidate_id>\n"
                "/publish <draft_id>\n/discard <draft_id>",
            )
            return

        cmd = parts[0].lower()

        if cmd not in ("/confirm", "/post", "/article", "/skip", "/publish", "/discard"):
            reply(chat_id, "Unknown command.")
            return

        if not parts[1].isdigit():
            reply(chat_id, "ID must be a number.")
            return

        entity_id = int(parts[1])
        state_store.init_db()

        if cmd == "/skip":
            state_store.mark_candidate_skipped(entity_id)
            reply(chat_id, "Skipped.")
            return

        if cmd in ("/post", "/article", "/confirm"):
            do_draft(chat_id, entity_id, cmd.replace("/", ""))
            return

        if cmd == "/publish":
            do_publish(chat_id, entity_id)
            return

        if cmd == "/discard":
            do_discard(chat_id, entity_id)
            return

    except Exception as e:
        print(e)
        try:
            reply(chat_id, f"Error:\n{e}")
        except Exception:
            pass


@app.post("/")
@app.post("/api/telegram_webhook")
async def webhook(request: Request):
    # --- Security: verify this request actually came from Telegram ---
    # Without this, anyone who discovers the webhook URL could POST a
    # forged payload (with a guessed/leaked chat_id) and trigger commands.
    # Telegram sends this header on every webhook call when a secret_token
    # was set via setWebhook -- see the registration command in README.
    incoming_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not WEBHOOK_SECRET or incoming_secret != WEBHOOK_SECRET:
        print("Rejected request: missing/invalid secret token")
        return JSONResponse({"ok": False}, status_code=401)

    try:
        body = await request.json()

        update_id = body.get("update_id")
        print(f"Received Telegram update: {update_id}")

        state_store.init_db()

        # Duplicate Telegram update (retry)? Ignore it -- this is what
        # prevented yesterday's issue from becoming an actual double-post
        # loop, and stays in place here too.
        if update_id is not None:
            if not state_store.try_mark_update_processed(update_id):
                print(f"Ignoring duplicate update {update_id}")
                return JSONResponse({"ok": True})

        message = body.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")

        print("Chat ID:", chat_id)
        print("Text:", text)

        if chat_id == ALLOWED_CHAT_ID and text:
            handle_command(chat_id, text)
        else:
            print("Ignored request -- chat_id mismatch or empty text")

        return JSONResponse({"ok": True})

    except Exception as e:
        print("Webhook Exception:", e)
        return JSONResponse({"ok": True})
