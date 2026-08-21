"""
Writer Agent -- Phase 2.

Generates a draft (post or article) once the classification has been
confirmed by Himanshu. Two distinct prompt templates rather than two
separate agents -- the difference is length/structure, not reasoning style.

Hashtags are generated via a SEPARATE, dedicated call rather than asked for
inside the main draft -- this guarantees they're always present, regardless
of how long the body runs or whether the model follows an in-prompt format
instruction. Decoupling was necessary after two failure modes: (1) hashtags
placed at the end of the body got silently truncated when the body ran long,
and (2) a delimiter-based approach depended on the model reliably emitting
an exact marker string, which it didn't always do.

Article title + cover-image brief follow the same "dedicated small call"
pattern for the same reason -- and also because LinkedIn's native Articles
tab (title + cover image + rich body) has NO API support at all, on any
access tier. There is no endpoint to publish to it, period. So "article"
drafts get a title and a cover-image suggestion bundled with the body, and
the Telegram/scheduled-publish flow hands the whole package to Himanshu to
paste into LinkedIn's Articles editor himself -- see telegram_webhook.py and
scheduled_publish.py for that hand-off.
"""
import os
from pathlib import Path
from urllib.parse import quote as url_quote
from concurrent.futures import ThreadPoolExecutor
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VOICE_REF_PATH = Path(__file__).parent.parent / "config" / "voice_reference.md"
POSITIONING_PATH = Path(__file__).parent.parent / "config" / "positioning_strategy.md"
# keep below LinkedIn's 4000-character hard limit
LINKEDIN_MAX_CHARS = 3800
HASHTAG_RESERVE = 120  # room reserved for up to 5 hashtags, e.g. "\n\n#Tag1 #Tag2 #Tag3 #Tag4 #Tag5"

# LinkedIn's recommended article/newsletter cover-image dimensions (2026).
ARTICLE_IMAGE_SPEC = "1200x644px, 1.91:1 ratio"

ALGORITHM_RULES = """
Formatting rules (LinkedIn 2026 ranking behavior -- follow strictly):
- NEVER put a raw URL or "read more here: <link>" inside the post/article body.
  If the source needs crediting, reference it by name in prose only
  (e.g. "as HBR reported this week") -- the actual link will be supplied
  separately as a first-comment, not in the body.
- NEVER use engagement-bait closers ("Agree? Comment below", "Like if you've
  seen this too", "Thoughts?" as a lone one-word closer). Instead end with a
  genuine, specific question that only makes sense if someone actually read
  the piece -- that's what earns real (high-weight) comments, not bait.
- Write enough substance to hold attention past ~60 seconds of reading --
  this is the single strongest ranking signal. Don't pad, but don't
  under-write either.
- Stay inside ONE of the three content pillars from positioning strategy
  below -- don't blend all three into one piece. Topical consistency across
  posts is what builds recognition for a specific area of expertise.
- Do NOT include hashtags in this output -- they're generated separately.
"""

POST_PROMPT = """You are drafting a LinkedIn POST for Himanshu Rai,
a Senior DevOps/Platform Engineer at Barclays and EMBA candidate at IIM Ranchi,
writing in professional English for a management/tech-strategy audience, with
the goal of building visibility toward engineering-leadership/management roles.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + """
Write a post about this item. Structure: a hook in the first line, the core
insight, a brief personal-angle connection if it fits naturally (his infra/EMBA
background), and end per the closing-question rule above.

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}

Output only the post body text. No hashtags, nothing else.
Stay well under 3,500 characters.
"""

ARTICLE_PROMPT = """You are drafting a LinkedIn ARTICLE (800-1200 words) for Himanshu Rai,
a Senior DevOps/Platform Engineer at Barclays and EMBA candidate at IIM Ranchi,
with the goal of building visibility toward engineering-leadership/management
roles.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + """
Structure it like a strategy analysis, drawing on management-framework thinking
(the kind used in case-method business education) without naming frameworks
explicitly unless it reads naturally:
1. The situation -- what actually changed
2. Why it matters beyond the immediate news cycle
3. A framework lens applied to it (pick whichever fits: incentive structures,
   diffusion of innovation, competitive positioning, etc.)
4. Practical implication for practitioners/leaders reading this
5. A closing point of view -- take a real stance, don't hedge into mush

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}

Output only the article text with a title at the top. No hashtags, nothing else.
Stay well under 3,500 characters.
"""

# Tuned against current LinkedIn engagement data + the yearly algorithm
# research (Richard van der Blom et al.): 1-3 hashtags gets the strongest
# engagement, and reach measurably drops below 3 and above 5 -- so the goal
# is a deliberate MIX for reach, not a maximum count. One broad/high-follower
# tag gets the post into a bigger first-look batch; two or three niche/topic
# tags reach the smaller audience who actually follows that exact hashtag
# and cares. More than 5 is a net loss, not a bonus.
HASHTAG_PROMPT = """Given this LinkedIn post topic and positioning strategy,
output 3 to 5 relevant hashtags, space-separated, nothing else -- no
preamble, no explanation, no line breaks.

Choose a deliberate MIX, not just a relevant list:
- Exactly 1 broad, high-follower hashtag for the pillar this topic belongs
  to (e.g. #PlatformEngineering, #TechLeadership, #Sustainability) -- this
  is what gets the post into a larger first-look distribution batch.
- 2 to 3 niche/community hashtags specific to the actual topic of THIS piece
  (e.g. #SRE, #FinOps, #CircularEconomy, #EMBA) -- these reach a smaller but
  highly relevant audience who follows that exact hashtag.
- Do not exceed 5 total. Posts with fewer than 3 or more than 5 hashtags
  measurably lose reach -- more tags is not more visibility.

Example output format:
#PlatformEngineering #SRE #CloudNative

Topic: {title}
Positioning pillars/keywords to draw from:
{positioning}
"""

TITLE_PROMPT = """Given this LinkedIn ARTICLE topic, write ONE compelling,
specific title for LinkedIn's native Articles feature -- 6-12 words, in
Himanshu Rai's voice (confident, direct, no fluff, no clickbait, no colon-
subtitle padding unless it genuinely earns its place).

Topic: {title}
Summary: {summary}
Classification reasoning: {reasoning}

Output ONLY the title text. No quotes, no preamble, no explanation.
"""

IMAGE_BRIEF_PROMPT = """Given this LinkedIn article topic, suggest a cover-
image concept for LinkedIn's article cover slot ({image_spec}).

Output ONLY 3-4 comma-separated, concrete, stock-photo-findable visual
keywords -- e.g. "server room blue light, data center corridor, fiber optic
cables macro". No preamble, no explanation, no full sentences.

Topic: {title}
Summary: {summary}
"""


def load_voice_reference():
    if VOICE_REF_PATH.exists():
        return VOICE_REF_PATH.read_text()
    return "(no voice reference on file yet -- default to clear, confident, non-jargon-heavy professional tone)"


def load_positioning_strategy():
    if POSITIONING_PATH.exists():
        return POSITIONING_PATH.read_text()
    return "(no positioning strategy on file -- default to general platform-engineering + business-strategy angle)"


def suggest_first_comment_link(candidate: dict) -> str:
    """Per algorithm rules, the source link is never in the body -- it's
    offered separately so you can drop it as the first comment after
    publishing, preserving reach while still crediting the source."""
    link = candidate.get("link", "")
    return f"Source: {link}" if link else ""


def generate_hashtags(title: str, positioning: str) -> str:
    """Dedicated small call -- kept separate from the main draft so a long
    body can never crowd hashtags out via truncation. Sanity-checks for
    3-5 tags (the reach sweet spot) rather than just "at least one" -- if
    the model under- or over-shoots, we don't use a malformed result."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": HASHTAG_PROMPT.format(title=title, positioning=positioning),
            }],
        )
        tags = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        tag_count = tags.count("#")
        # sanity check -- if the model ignored instructions and returned
        # something that isn't a clean 3-5 hashtag line, don't use it
        if tags and 3 <= tag_count <= 5 and len(tags) < 160:
            return tags
        print(f"[writer] WARN: hashtag output outside expected 3-5 range ({tag_count} tags), discarding: {tags!r}")
    except Exception as e:
        print(f"[writer] WARN: hashtag generation failed: {e}")
    return ""


def generate_article_title(candidate: dict) -> str:
    """Dedicated small call, articles only. Falls back to the source item's
    own title if the call fails, rather than leaving the field empty."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": TITLE_PROMPT.format(
                    title=candidate["title"],
                    summary=candidate.get("summary", ""),
                    reasoning=candidate.get("reasoning", ""),
                ),
            }],
        )
        generated = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip().strip('"')
        if generated and len(generated) < 200:
            return generated
    except Exception as e:
        print(f"[writer] WARN: title generation failed: {e}")
    return candidate.get("title", "")


def generate_image_brief(candidate: dict) -> str:
    """Dedicated small call, articles only -- returns comma-separated visual
    search keywords for a cover image. No image is generated or fetched
    (keeps the stack API-key-free for this step); Himanshu picks one
    manually via the constructed search link or Canva."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": IMAGE_BRIEF_PROMPT.format(
                    title=candidate["title"],
                    summary=candidate.get("summary", ""),
                    image_spec=ARTICLE_IMAGE_SPEC,
                ),
            }],
        )
        keywords = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if keywords and len(keywords) < 200:
            return keywords
    except Exception as e:
        print(f"[writer] WARN: image brief generation failed: {e}")
    return ""


def build_image_search_link(image_brief: str) -> str:
    """Turns the first keyword phrase into a one-tap Unsplash search link --
    no image-gen API key needed, Himanshu picks and downloads manually."""
    if not image_brief:
        return ""
    first_phrase = image_brief.split(",")[0].strip()
    if not first_phrase:
        return ""
    return f"https://unsplash.com/s/photos/{url_quote(first_phrase)}"


def condense_body(body: str, char_limit: int) -> str:
    """Used only when the draft runs over the character budget. Rather than
    hard-cutting mid-sentence (which can lop off the closing question or
    end on an incomplete thought), ask the model to tighten the same piece
    down to fit -- same voice, same core insight, same ending, just less
    padding. Falls back to a sentence-boundary cut only if this call fails."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1800,
            messages=[{
                "role": "user",
                "content": (
                    f"Tighten this LinkedIn post to fit within {char_limit} "
                    f"characters. Keep the same voice, the same core insight, "
                    f"and the same closing question -- just remove padding, "
                    f"redundant sentences, or less essential detail. Do not "
                    f"cut it off mid-sentence; it must end cleanly on the "
                    f"closing question. Output only the tightened text, "
                    f"nothing else.\n\n{body}"
                ),
            }],
        )
        tightened = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if tightened and len(tightened) <= char_limit + 50:  # small slack
            return tightened
    except Exception as e:
        print(f"[writer] WARN: condense call failed: {e}")

    # Fallback: cut at the last complete sentence within budget, not
    # mid-sentence -- still better than an arbitrary word-boundary cut.
    truncated = body[:char_limit]
    last_stop = max(truncated.rfind(". "), truncated.rfind("? "), truncated.rfind("! "))
    if last_stop > char_limit * 0.6:  # only use it if we're not losing too much
        return truncated[: last_stop + 1].strip()
    return truncated.rsplit(" ", 1)[0].rstrip() + "..."


def _generate_body(candidate: dict, confirmed_type: str, voice_ref: str, positioning: str) -> str:
    template = POST_PROMPT if confirmed_type == "post" else ARTICLE_PROMPT
    prompt = template.format(
        voice_ref=voice_ref,
        positioning=positioning,
        title=candidate["title"],
        summary=candidate.get("summary", ""),
        source=candidate.get("source", ""),
        reasoning=candidate.get("reasoning", ""),
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


def generate_draft_package(candidate: dict, confirmed_type: str) -> dict:
    """Runs every independent generation call CONCURRENTLY instead of one
    after another, and returns everything a draft needs in one shot:
    {"draft_text": ..., "title": ..., "image_brief": ...} (title/image_brief
    are None for posts).

    Why this exists: an article draft used to need 4 sequential Claude
    calls in a row -- body, hashtags, title, image brief -- inside a single
    Telegram webhook request. On Vercel's Hobby-tier 60-second function
    timeout (the hard ceiling for this plan -- already set to the max in
    vercel.json, can't be raised without upgrading), that chain could run
    long enough to get killed mid-request. A Vercel timeout is a hard
    process kill, not a Python exception, so it bypasses handle_command's
    own try/except entirely -- no error ever reaches Telegram, the request
    just goes silent. None of these four calls actually depend on each
    other's OUTPUT (title and image_brief only need `candidate`, not the
    finished body), so running them in parallel threads cuts wall-clock
    time to roughly the slowest single call instead of the sum of all four.
    """
    voice_ref = load_voice_reference()
    positioning = load_positioning_strategy()

    with ThreadPoolExecutor(max_workers=4) as pool:
        body_future = pool.submit(_generate_body, candidate, confirmed_type, voice_ref, positioning)
        hashtag_future = pool.submit(generate_hashtags, candidate["title"], positioning)

        title_future = None
        image_brief_future = None
        if confirmed_type == "article":
            title_future = pool.submit(generate_article_title, candidate)
            image_brief_future = pool.submit(generate_image_brief, candidate)

        body = body_future.result()
        hashtags = hashtag_future.result()
        title = title_future.result() if title_future else None
        image_brief = image_brief_future.result() if image_brief_future else None

    reserved = len(hashtags) + 2 if hashtags else 0
    body_limit = LINKEDIN_MAX_CHARS - reserved
    if len(body) > body_limit:
        body = condense_body(body, body_limit)

    draft_text = f"{body}\n\n{hashtags}" if hashtags else body

    return {
        "draft_text": draft_text.strip(),
        "title": title,
        "image_brief": image_brief,
    }


def write_draft(candidate: dict, confirmed_type: str) -> str:
    """Kept for callers that only want the body text (e.g. write_confirmed.py,
    the local CLI bridge script, which has no 60-second constraint). Thin
    wrapper over generate_draft_package so both paths share one code path."""
    return generate_draft_package(candidate, confirmed_type)["draft_text"]
