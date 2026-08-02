# LinkedIn Thought-Leadership Agent

Multi-agent pipeline (same shape as Bharat Ke Rang): **Scan → Dedup → Classify →
Write → Review → Publish**. Finds management/world-change topics daily,
classifies each as a quick LinkedIn *post* or a deeper *article*, drafts it in
your voice, and (once Phase 3/4 are live) publishes after your approval.

## Architecture

```
Scanner Agent        -- pulls RSS + NewsAPI candidates          (src/scanner.py)
Dedup Agent          -- filters against seen_topics             (src/dedup.py)
Impact Classifier     -- suggests post/article + reasoning       (src/classifier.py)
                         (ADVISORY ONLY -- you always confirm before writing)
Writer Agent          -- generates draft in your voice            (src/writer.py)
Notifier (Telegram)  -- sends candidates + drafts for review     (src/notifier_telegram.py)
LinkedIn Publisher    -- OAuth + Posts API publish                (src/linkedin_publish.py)
State Store           -- SQLite: topics, candidates, drafts, tokens (src/state_store.py)
```

## Status: Phase 1 + 2 scaffolded, Phase 3 + 4 need your API keys/setup

| Phase | What it does | Status |
|---|---|---|
| 1 | Scan, dedup, classify, notify (one-way) | Built |
| 2 | Write draft once type is confirmed | Built (manual trigger for now) |
| 3 | LinkedIn OAuth + auto-publish | Scaffolded, needs your LinkedIn app + one-time auth |
| 4 | Two-way Telegram approval loop (no manual script running) | Not yet built -- needs a small always-on webhook (Vercel, like Jyoti Darshan) |

## Setup

1. `pip install -r requirements.txt --break-system-packages` (or use a venv)
2. Set environment variables (locally in `.env`, or as GitHub Actions secrets):
   - `ANTHROPIC_API_KEY`
   - `NEWSAPI_KEY` (optional but recommended -- https://newsapi.org)
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (create a bot via @BotFather,
     message it once, then hit `https://api.telegram.org/bot<TOKEN>/getUpdates`
     to find your chat_id)
3. Paste 3-5 of your real past posts into `config/voice_reference.md` so the
   Writer Agent has something concrete to match against.
4. Run once locally to sanity check: `python -m src.pipeline_daily`
5. Push to CodeOpsDynamics, add the same secrets under repo Settings > Secrets
   > Actions, and the daily cron takes over.

## LinkedIn App Setup (for Phase 3)

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
6. Store that access token as a GitHub secret named `LINKEDIN_ACCESS_TOKEN`
   -- **never commit it to the repo.** Also add `LINKEDIN_CLIENT_ID` /
   `LINKEDIN_CLIENT_SECRET` as secrets (not strictly needed for publishing,
   but auth_flow.py needs them each time you re-run it).

**Important constraint, confirmed against LinkedIn's docs**: standard-tier
apps (what we have) do NOT receive a refresh token -- that's exclusive to
Marketing Developer Platform partners, a much heavier approval tier not
worth pursuing for personal posting. The access token lasts ~60 days and
there's no way to silently auto-refresh it. `token_reminder.yml` pings you
via Telegram on the 1st and 15th of each month so this doesn't sneak up on
you -- when it fires, just re-run `auth_flow.py` and update the secret.

**Open risk to validate during Phase 3**: LinkedIn's native long-form
"Articles" feature may not have direct Posts API support the way regular
posts do. If so, fallback is publishing "articles" as long native text posts
-- common practice on LinkedIn today regardless of the old Articles format.

## Bridge script (until Phase 4's approval loop is live)

Candidates land in `data/agent_state.db` and get messaged to you via
Telegram. To act on one manually:

```
python -m src.write_confirmed <candidate_id> post        # prints draft only
python -m src.write_confirmed <candidate_id> article --publish   # asks to confirm, then publishes + drops source link as first comment
```

## Phase 4 -- closing the approval loop

GitHub Actions cron runs on a schedule; it can't sit and listen for your
Telegram reply between runs. To make `/confirm`, `/post`, `/article`, `/skip`
actually work without the manual bridge script above, we need a tiny
always-on receiver -- a Vercel serverless function acting as the Telegram
webhook (same shape as your Jyoti Darshan proxy), which writes your decision
somewhere the next Action run can read (commit to a state file, or a
lightweight hosted DB like Turso/Supabase).

## LinkedIn algorithm + management-visibility layer

`config/positioning_strategy.md` defines 2-3 content pillars (platform
engineering + biz impact, tech-business bridge via EMBA frameworks,
sustainability/circular economy) and target keywords for management-track
roles. The Writer Agent (`src/writer.py`) now enforces, per piece:

- No raw links in the body (reach-suppressing) -- source link is generated
  separately via `suggest_first_comment_link()` for you to drop as the first
  comment after publishing (`linkedin_publish.post_first_comment()` handles
  this once Phase 3 is live).
- No engagement-bait closers -- genuine, specific questions only.
- Enough substance to hold ~60+ seconds of read time (top-weighted ranking
  signal), without padding.
- One pillar per piece, not a blend -- topical consistency is what LinkedIn's
  semantic ranking rewards with recognition in a specific area.

**What this pipeline can't do for you**: profile-level signals (headline,
About section, Open to Work setting) matter as much as content for recruiter
visibility, and those are manual, one-time edits -- see the bottom of
`positioning_strategy.md` for specifics.

**Not built (by design, for now)**: an "auto-engage on other people's posts"
feature. LinkedIn's 2026 ranking actively detects and suppresses coordinated/
pod-like engagement patterns, and inauthentic auto-commenting as you carries
real reputational risk for a management-track brand. If useful later, a
manual "here are 3-5 posts from people in your space worth a genuine comment
today" digest is a safer version of this -- flag it if you want it added.

## Should this be a Claude Project too?

Yes -- for the thinking/iteration layer, not the execution layer. Create a
Claude Project ("LinkedIn Content Engine") and upload `voice_reference.md`,
`sources.yaml`, and `positioning_strategy.md` as its knowledge base. Use it
for prompt tuning, reviewing drafts you're unsure about, and strategy
conversations, so that context is already loaded instead of re-explained each
time. The actual pipeline keeps running from this repo via GitHub Actions --
Projects don't execute scheduled code, they just hold persistent context for
chats.

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
