# LinkedIn Thought-Leadership Agent

Multi-agent pipeline: **Scan → Dedup → Classify → Write → Review → Publish**.
Finds management/world-change topics daily, classifies each as a quick
LinkedIn *post* or a deeper *article*, drafts it in your voice, and publishes
after your approval via Telegram -- fully automated end to end except for the
two places LinkedIn's API genuinely can't reach (see "Permanent manual
hand-offs" below).

## Architecture

```
Scanner Agent          -- pulls RSS + NewsAPI candidates            (src/scanner.py)
Dedup Agent            -- filters against seen_topics                (src/dedup.py)
Impact Classifier      -- suggests post/article + reasoning          (src/classifier.py)
                           (ADVISORY ONLY -- you always confirm before writing)
Writer Agent           -- generates draft package in your voice      (src/writer.py)
Telegram Webhook       -- interactive /post /article /confirm /publish etc,
                           runs as a Vercel serverless function       (api/telegram_webhook.py)
Notifier (Telegram)    -- one-way "here are today's candidates" push  (src/notifier_telegram.py)
LinkedIn Publisher     -- OAuth + Posts API publish                   (src/linkedin_publish.py)
Scheduled Publisher    -- twice-daily cron: auto-publish posts,
                           deliver articles for manual publish        (src/scheduled_publish.py)
State Store            -- Turso (hosted libsql): topics, candidates,
                           drafts, processed Telegram updates          (src/state_store.py)
```

## Status: fully built and running

| Phase | What it does | Status |
|---|---|---|
| 1 | Scan, dedup, classify, notify | Built, running daily via cron |
| 2 | Write draft once type is confirmed | Built |
| 3 | LinkedIn OAuth + auto-publish (posts) | Built, needs your LinkedIn app + periodic re-auth (see below) |
| 4 | Two-way Telegram approval loop | Built -- `api/telegram_webhook.py` on Vercel |
| 5 | Twice-daily scheduled publish (post AM / article PM) | Built -- see `.github/workflows/` |

The old "bridge script" (`src/write_confirmed.py`, run manually before Phase 4
existed) still works as a local fallback but isn't the normal path anymore --
everything below happens through Telegram.

## Setup

1. `pip install -r requirements.txt --break-system-packages` (or use a venv)
2. Set environment variables (locally in `.env`, and as both GitHub Actions
   secrets *and* Vercel project env vars -- the pipeline runs in both places):
   - `ANTHROPIC_API_KEY`
   - `NEWSAPI_KEY` (optional but recommended -- https://newsapi.org)
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (create a bot via @BotFather,
     message it once, then hit `https://api.telegram.org/bot<TOKEN>/getUpdates`
     to find your chat_id)
   - `TELEGRAM_WEBHOOK_SECRET` (any random string you choose -- see Telegram
     webhook setup below)
   - `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` (see Turso setup below)
   - `LINKEDIN_ACCESS_TOKEN` (see LinkedIn app setup below)
3. Paste 3-5 of your real past posts into `config/voice_reference.md` so the
   Writer Agent has something concrete to match against.
4. Run once locally to sanity check: `python -m src.pipeline_daily`
5. Push to CodeOpsDynamics, add the same secrets under repo Settings > Secrets
   > Actions (for the cron jobs) and under the Vercel project's Environment
   Variables (for the Telegram webhook), and everything below takes over.

## Turso setup (state store)

State (candidates, drafts, dedup history) lives in Turso, a hosted libsql
(SQLite-compatible) database, so both GitHub Actions and the Vercel webhook
can read/write the same state without a persistent local disk.

1. Create a database at https://turso.tech (free tier is enough for this).
2. Grab the database URL (`libsql://...`) and generate an auth token.
3. Set both as `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` everywhere the
   pipeline runs (GitHub Actions secrets + Vercel env vars + local `.env`).
4. Schema is created and migrated automatically on first call to
   `state_store.init_db()` -- no manual migration step needed.

## Telegram webhook setup (Phase 4)

`api/telegram_webhook.py` is deployed on Vercel and handles `/post`,
`/article`, `/confirm`, `/skip`, `/publish`, `/publishnow`, `/discard` as they
arrive from Telegram in real time.

1. Deploy this repo to Vercel (it's already wired via `vercel.json` +
   `pyproject.toml`'s `[tool.vercel]` entrypoint).
2. Register the webhook with Telegram, including a secret token so random
   internet traffic can't trigger your bot's commands:
   ```
   curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
     -d "url=https://<your-vercel-domain>/api/telegram_webhook" \
     -d "secret_token=<same value as TELEGRAM_WEBHOOK_SECRET>"
   ```
3. Message the bot -- commands should now get an instant reply instead of
   requiring the local bridge script.

**Vercel Hobby-tier constraint worth knowing**: serverless functions have a
hard 60-second timeout on this plan (`vercel.json` already sets `maxDuration`
to the max allowed). Article drafting involves several Claude calls (body,
hashtags, title, cover-image brief, teaser post) -- `writer.py`'s
`generate_draft_package()` runs all of them **concurrently** rather than in
sequence specifically to stay well under that ceiling. If you ever add
another generation step to article drafting, add it to that same thread pool
rather than calling it sequentially after the others.

## LinkedIn App Setup (Phase 3)

1. Create an app at https://www.linkedin.com/developers/apps
2. Add products: "Sign In with LinkedIn using OpenID Connect" + "Share on
   LinkedIn" (self-serve -- no lengthy Partner Program review needed for
   basic posting; that's only required for Marketing/Compliance APIs)
3. Verify the app (requires an admin-verified company page -- a placeholder
   page works if you don't want to use a real one)
4. Add redirect URL `http://localhost:8765/callback` in the app's Auth tab
5. Run `python src/auth_flow.py` locally with `LINKEDIN_CLIENT_ID` and
   `LINKEDIN_CLIENT_SECRET` set as env vars. Opens a browser consent screen,
   prints your access token at the end.
6. Store that access token as a GitHub secret **and** a Vercel env var named
   `LINKEDIN_ACCESS_TOKEN` -- **never commit it to the repo.**

**Confirmed constraint**: standard-tier apps (what we have) do NOT receive a
refresh token -- that's exclusive to Marketing Developer Platform partners, a
much heavier approval tier not worth pursuing for personal posting. The
access token lasts ~60 days with no silent auto-refresh. `token_reminder.yml`
pings you via Telegram on the 1st and 15th of each month -- when it fires,
re-run `auth_flow.py` and update the secret in both GitHub and Vercel.

## Permanent manual hand-offs (not bugs, not TODOs)

Two things are architecturally impossible to fully automate on an individual
LinkedIn developer account. Both are handled with a deliberate, minimal
Telegram hand-off rather than a workaround:

**LinkedIn Articles.** There is no API endpoint to publish to LinkedIn's
native long-form Articles tab, on any access tier, for anyone. So `/article`
(or `/confirm` when the classifier suggests article) generates the full
package -- title, 800-1200 word body, a cover-image keyword brief with a
one-tap Unsplash search link, and a **teaser post** -- and delivers it via
Telegram for a ~30-second manual paste into `linkedin.com -> Write article`.
The teaser post is a separate short post (in your voice, no raw link in the
body) meant to be published normally alongside the article to drive clicks;
add the article's actual LinkedIn URL as the teaser's first comment once the
article is live, same reach-preserving pattern used for source credit on
regular posts.

**Comment auto-posting for source credit.** The Community Management API
(needed to post comments as yourself) is restricted to registered business
entities and permanently unavailable for individual developer accounts. Posts
still publish automatically via the API; the source-link text is delivered
alongside for a manual copy-paste as the first comment.

## Telegram commands

```
/post <candidate_id>       draft as a short POST
/article <candidate_id>    draft as a long-form ARTICLE
/confirm <candidate_id>    draft using the classifier's suggested type
/skip <candidate_id>       drop this candidate

/publish [draft_id]        queue for the next scheduled slot
                            (posts: tomorrow AM, articles: tonight)
/publishnow [draft_id]     act immediately instead of waiting for the slot
/discard [draft_id]        cancel a pending draft
```

`draft_id` is optional on `/publish`, `/publishnow`, and `/discard` -- omit it
and the command acts on your most recent still-pending draft. `/post`,
`/article`, `/confirm`, and `/skip` still require an explicit `candidate_id`
since candidates usually arrive in same-day batches and "latest" would be
ambiguous there.

For posts, `/publishnow` publishes straight to LinkedIn via the API. For
articles, `/publishnow` instead delivers the full copy-paste package (see
above) right away instead of waiting for the evening slot.

## LinkedIn algorithm + management-visibility layer

`config/positioning_strategy.md` defines 2-3 content pillars (platform
engineering + biz impact, tech-business bridge via EMBA frameworks,
sustainability/circular economy) and target keywords for management-track
roles. The Writer Agent (`src/writer.py`) enforces, per piece:

- No raw links in the body (reach-suppressing) -- source link is generated
  separately via `suggest_first_comment_link()` for you to drop as the first
  comment after publishing.
- No engagement-bait closers -- genuine, specific questions only.
- Enough substance to hold ~60+ seconds of read time (top-weighted ranking
  signal), without padding. Articles are NOT squeezed to post-length --
  they run their full natural 800-1200 words; only posts have a character
  budget, since that's the format with LinkedIn's actual composer limit.
- One pillar per piece, not a blend -- topical consistency is what LinkedIn's
  semantic ranking rewards with recognition in a specific area.
- **Hashtags, deliberately mixed for reach, not maximized for count**:
  3-5 total (LinkedIn engagement data and yearly algorithm research both
  show reach drops measurably below 3 and above 5) -- one broad,
  high-follower hashtag for the pillar, plus two-to-three niche/community
  hashtags specific to the actual topic.

**What this pipeline can't do for you**: profile-level signals (headline,
About section, Open to Work setting) matter as much as content for recruiter
visibility, and those are manual, one-time edits -- see the bottom of
`positioning_strategy.md` for specifics.

**Not built (by design)**: an "auto-engage on other people's posts" feature.
LinkedIn's 2026 ranking actively detects and suppresses coordinated/pod-like
engagement patterns, and inauthentic auto-commenting as you carries real
reputational risk for a management-track brand.

## Should this be a Claude Project too?

Yes -- for the thinking/iteration layer, not the execution layer. Create a
Claude Project ("LinkedIn Content Engine") and upload `voice_reference.md`,
`sources.yaml`, and `positioning_strategy.md` as its knowledge base. Use it
for prompt tuning, reviewing drafts you're unsure about, and strategy
conversations, so that context is already loaded instead of re-explained each
time. The actual pipeline keeps running from this repo via GitHub Actions +
Vercel -- Projects don't execute scheduled code, they just hold persistent
context for chats.

## Design notes

- Classifier is **advisory only** per your preference -- it never proceeds to
  writing on its own. You confirm or override before a single word is drafted.
- Sources in `config/sources.yaml` are a starting set -- HBR, McKinsey,
  Strategy+Business, The Economist, Stratechery, MIT Sloan -- tune freely.
  One query already ties to PunarChakra ("circular economy e-waste policy")
  so the pipeline surfaces things relevant to your own venture too.
- Dedup is title-hash based for v1 -- fine for now, upgrade to embedding-based
  similarity later if near-duplicate stories from different outlets start
  slipping through.
- `state_store.py` opens every Turso connection with `with get_client() as
  client:` rather than a bare open/close pair -- the client's background
  worker runs on a non-daemon thread that only stops on `.close()`, so any
  unhandled exception between open and a manual close used to leak that
  thread and hang the whole process indefinitely. `with` guarantees cleanup
  even on exception.
- `telegram_webhook.py`'s `reply()` and `scheduled_publish.py`'s `notify()`
  both check the actual Telegram API response and auto-split any message
  over Telegram's 4096-character limit (article deliveries routinely exceed
  it) into multiple sequential messages, instead of the message silently
  vanishing if Telegram rejected it.
