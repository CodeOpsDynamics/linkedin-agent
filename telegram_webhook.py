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


TELEGRAM_MAX_CHARS = 4096  # Telegram's hard per-message limit for sendMessage


def _send_single_message(chat_id, text):
    """One actual API call. Returns True on success. Logs the REAL reason
    on failure instead of swallowing it -- previously this only caught
    network-level exceptions (timeouts, connection errors); if Telegram's
    API itself rejected the message (e.g. 400 Bad Request for exceeding
    the 4096-char limit), requests.post() doesn't raise anything, so that
    failure was invisible: no error in the logs, nothing sent to Telegram,
    nothing to go on. Checking response.ok surfaces exactly that case."""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        if not resp.ok:
            print(f"Telegram API rejected message (status {resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        print("Telegram reply failed:", e)
        return False


def reply(chat_id, text):
    """Article drafts (title + body + cover-image brief + instructions) can
    exceed Telegram's 4096-char limit on a single message, which used to
    get silently dropped -- see _send_single_message. Long text is now
    split on paragraph breaks (falling back to hard slices) into multiple
    messages sent in order, so nothing gets lost just because it ran long."""
    print("Sending Telegram reply:", text)

    if len(text) <= TELEGRAM_MAX_CHARS:
        _send_single_message(chat_id, text)
        return

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_MAX_CHARS:
            chunks.append(remaining)
            break
        # prefer to break on a paragraph boundary near the limit so a
        # message doesn't get cut mid-sentence; fall back to a hard slice
        # if there's no paragraph break in range
        window = remaining[:TELEGRAM_MAX_CHARS]
        split_at = window.rfind("\n\n")
        if split_at < TELEGRAM_MAX_CHARS * 0.5:
            split_at = TELEGRAM_MAX_CHARS
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        prefix = f"[{i}/{total}]\n" if total > 1 else ""
        _send_single_message(chat_id, prefix + chunk)


def post_url_from_urn(urn):
    return f"https://www.linkedin.com/feed/update/{urn}/"


def do_draft(chat_id, candidate_id, requested_type):
    """/post, /article, /confirm all land here -- generates and SAVES a
    draft, replies with the text + a draft_id, but does NOT publish.

    For articles: LinkedIn's native Articles tab (title + cover image +
    rich body) has NO API support on any access tier -- there is no
    endpoint to publish to it. So an article draft also gets a generated
    title and a cover-image keyword brief, and the reply makes clear the
    final publish step will be a manual copy-paste, not automatic."""
    candidate = state_store.get_candidate(candidate_id)

    if not candidate:
        reply(chat_id, "Candidate not found.")
        return

    if candidate.get("status") in ("published", "delivered_manual"):
        reply(chat_id, "This candidate has already been published.")
        return

    confirmed_type = (
        candidate["suggested_type"] if requested_type == "confirm" else requested_type
    )

    reply(chat_id, f"Writing {confirmed_type} draft for review...")

    state_store.mark_candidate_confirmed(candidate_id, confirmed_type)

    # generate_draft_package runs body/hashtags/title/image-brief/teaser
    # CONCURRENTLY instead of one after another -- articles were creeping
    # close to (and sometimes past) Vercel's 60s function timeout with the
    # old sequential chain, which silently kills the request with no error
    # reaching Telegram. See writer.py's generate_draft_package docstring.
    package = writer.generate_draft_package(candidate, confirmed_type)
    draft_text = package["draft_text"]
    title = package["title"]
    image_brief = package["image_brief"]
    teaser_post = package["teaser_post"]

    draft_id = state_store.add_draft(
        candidate_id, confirmed_type, draft_text,
        title=title, image_brief=image_brief, teaser_post=teaser_post,
    )

    if confirmed_type == "article":
        image_link = writer.build_image_search_link(image_brief)
        teaser_block = (
            f"\n---\n"
            f"Suggested teaser post (publish this separately as a normal "
            f"post once the article is live -- put the article's LinkedIn "
            f"URL as the FIRST COMMENT on this teaser, not in its body):\n\n"
            f"{teaser_post}\n"
            if teaser_post else ""
        )
        reply(
            chat_id,
            f"Draft #{draft_id} (article):\n\n"
            f"Title: {title}\n\n"
            f"{draft_text}\n\n"
            f"---\n"
            f"Suggested cover image ({writer.ARTICLE_IMAGE_SPEC}):\n"
            f"Keywords: {image_brief}\n"
            + (f"Quick search: {image_link}\n" if image_link else "")
            + teaser_block
            + f"\n---\n"
            f"LinkedIn's Articles tab has no API access, so publishing this "
            f"needs one manual step from you.\n"
            f"Reply /publish {draft_id} to get the copy-paste-ready package "
            f"delivered here at tonight's scheduled slot.\n"
            f"Reply /publishnow {draft_id} to get it right now instead.\n"
            f"Reply /discard {draft_id} to cancel this draft.",
        )
    else:
        reply(
            chat_id,
            f"Draft #{draft_id} ({confirmed_type}):\n\n{draft_text}\n\n"
            f"---\n"
            f"Reply /publish {draft_id} to queue this for the next scheduled push "
            f"({'tomorrow morning' if confirmed_type == 'post' else 'tonight'}).\n"
            f"Reply /publishnow {draft_id} to push it live immediately instead.\n"
            f"Reply /discard {draft_id} to cancel this draft.",
        )


def do_publish(chat_id, draft_id):
    """/publish QUEUES the draft for the next matching scheduled slot
    (morning for posts, night for articles) instead of publishing
    immediately -- keeps output timing consistent for the algorithm
    regardless of when you happen to approve it.

    For posts, the scheduled slot auto-publishes via the LinkedIn API as
    before. For articles, the scheduled slot instead DELIVERS the
    copy-paste-ready package back to you on Telegram -- LinkedIn's Articles
    tab can't be reached via API, so that last step stays yours."""
    draft = state_store.get_draft(draft_id)

    if not draft:
        reply(chat_id, "Draft not found.")
        return

    if draft.get("status") == "published":
        reply(chat_id, "This exact draft has already been published -- not queuing again.")
        return

    if draft.get("status") == "delivered_manual":
        reply(chat_id, "This was already delivered to you for manual publishing -- not queuing again.")
        return

    if draft.get("status") == "rejected":
        reply(chat_id, "This draft was discarded. Generate a new one with /post or /article.")
        return

    if draft.get("status") == "queued":
        reply(chat_id, "Already queued -- it'll go out at the next matching scheduled slot.")
        return

    candidate = state_store.get_candidate(draft["candidate_id"])
    if candidate and candidate.get("status") in ("published", "delivered_manual"):
        reply(chat_id, "This candidate was already handled (via a different draft). Not queuing.")
        return

    state_store.queue_draft(draft_id)

    if draft["draft_type"] == "article":
        reply(
            chat_id,
            f"Queued draft #{draft_id} (article) -- I'll deliver the "
            f"copy-paste-ready package here tonight so you can publish it "
            f"in LinkedIn's Articles tab yourself.\n"
            f"Reply /publishnow {draft_id} if you want it right now instead.",
        )
    else:
        reply(
            chat_id,
            f"Queued draft #{draft_id} (post) -- will publish automatically "
            f"at tomorrow morning's slot.\n"
            f"Reply /publishnow {draft_id} if you want it live immediately instead.",
        )


def deliver_article_now(chat_id, draft, candidate):
    """Articles never touch the LinkedIn API -- this hands Himanshu
    everything he needs to paste into LinkedIn's Articles editor himself:
    title, body, and a cover-image suggestion sized to LinkedIn's spec.
    Also resurfaces the teaser post generated at draft time -- articles
    have no in-app auto-share, so this is the copy-paste post that drives
    people to it, published separately with the article's URL added as
    that teaser's first comment once it's live."""
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

    reply(
        chat_id,
        f"Ready to publish -- LinkedIn's Articles tab has no API access, so "
        f"this last step is on you (30 seconds):\n\n"
        f"1. linkedin.com -> Write article\n"
        f"2. Title: {title}\n"
        f"3. Paste this body:\n\n{draft['draft_text']}\n\n"
        f"4. Cover image ({writer.ARTICLE_IMAGE_SPEC}): {image_brief}"
        + (f"\nQuick search: {image_link}" if image_link else "")
        + comment_note
        + teaser_block,
    )

    state_store.mark_draft_delivered_manual(draft["id"])
    state_store.mark_candidate_delivered_manual(draft["candidate_id"])


def do_publish_now(chat_id, draft_id):
    """Escape hatch: publish immediately, bypassing the scheduled slot.
    Posts still go straight to the LinkedIn API. Articles are handed to you
    as a ready-to-paste package instead -- see deliver_article_now."""
    draft = state_store.get_draft(draft_id)

    if not draft:
        reply(chat_id, "Draft not found.")
        return

    if draft.get("status") == "published":
        reply(chat_id, "This exact draft has already been published -- not posting again.")
        return

    if draft.get("status") == "delivered_manual":
        reply(chat_id, "This was already delivered to you for manual publishing.")
        return

    if draft.get("status") == "rejected":
        reply(chat_id, "This draft was discarded. Generate a new one with /post or /article.")
        return

    candidate = state_store.get_candidate(draft["candidate_id"])
    if candidate and candidate.get("status") in ("published", "delivered_manual"):
        reply(chat_id, "This candidate was already handled (via a different draft). Not posting again.")
        return

    if draft["draft_type"] == "article":
        deliver_article_now(chat_id, draft, candidate)
        return

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
    if draft.get("status") in ("published", "delivered_manual"):
        reply(chat_id, "Can't discard -- this draft has already been handled.")
        return
    state_store.mark_draft_rejected(draft_id)
    reply(chat_id, f"Draft #{draft_id} discarded.")


def handle_command(chat_id, text):
    print("Entered handle_command")
    try:
        parts = text.strip().split()

        if not parts:
            reply(
                chat_id,
                "Usage:\n/post <candidate_id>\n/article <candidate_id>\n"
                "/confirm <candidate_id>\n/skip <candidate_id>\n"
                "/publish [draft_id] (queue for next scheduled slot -- id "
                "optional, defaults to your latest pending draft)\n"
                "/publishnow [draft_id] (publish immediately -- id optional; "
                "posts go live via API, articles are delivered to you to "
                "paste into LinkedIn's Articles tab)\n"
                "/discard [draft_id] (id optional)",
            )
            return

        cmd = parts[0].lower()

        if cmd not in ("/confirm", "/post", "/article", "/skip", "/publish", "/publishnow", "/discard"):
            reply(chat_id, "Unknown command.")
            return

        state_store.init_db()

        # Draft-scoped commands: fall back to "the most recent draft still
        # awaiting a decision" when no id is given. Candidates (/post,
        # /article, /confirm, /skip) usually arrive in a same-day batch, so
        # "latest" would be ambiguous there -- those still require an
        # explicit id. Drafts are a one-at-a-time conversation in practice,
        # so the fallback is unambiguous and saves re-typing the id.
        if cmd in ("/publish", "/publishnow", "/discard"):
            if len(parts) >= 2 and parts[1].isdigit():
                entity_id = int(parts[1])
            else:
                latest = state_store.get_latest_actionable_draft()
                if not latest:
                    reply(
                        chat_id,
                        "No draft_id given and no pending draft found -- "
                        f"specify one, e.g. {cmd} 41.",
                    )
                    return
                entity_id = latest["id"]
                reply(chat_id, f"No ID given -- using your most recent pending draft, #{entity_id}.")

            if cmd == "/publish":
                do_publish(chat_id, entity_id)
            elif cmd == "/publishnow":
                do_publish_now(chat_id, entity_id)
            else:
                do_discard(chat_id, entity_id)
            return

        if len(parts) < 2 or not parts[1].isdigit():
            reply(chat_id, f"Usage: {cmd} <candidate_id> -- id is required for this command.")
            return

        entity_id = int(parts[1])

        if cmd == "/skip":
            state_store.mark_candidate_skipped(entity_id)
            reply(chat_id, "Skipped.")
            return

        if cmd in ("/post", "/article", "/confirm"):
            do_draft(chat_id, entity_id, cmd.replace("/", ""))
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
