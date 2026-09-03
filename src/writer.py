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
import json
from pathlib import Path
from urllib.parse import quote as url_quote
from concurrent.futures import ThreadPoolExecutor
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VOICE_REF_PATH = Path(__file__).parent.parent / "config" / "voice_reference.md"
POSITIONING_PATH = Path(__file__).parent.parent / "config" / "positioning_strategy.md"
# keep below LinkedIn's ~3000-character hard limit for the short-form
# FEED POST composer. This budget applies to "post" drafts only -- LinkedIn's
# separate long-form Articles editor (what "article" drafts are for) has no
# comparable limit, so squeezing an 800-1200 word article down to this size
# would gut the whole point of writing a proper article. See
# generate_draft_package() below for where that distinction is enforced.
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

# NOTE on the "management/EMBA angle": earlier prompt versions hinted at his
# "infra/EMBA background" in every single draft, which made the tech+business
# bridge angle show up on nearly every post -- read as forced/rta rta. Fixed
# by making that connection conditional ("only if the topic genuinely has
# it") and by explicitly banning naming the framework/degree in the output
# text itself. See config/positioning_strategy.md pillar 2 for the full
# rationale.
MANAGEMENT_ANGLE_RULE = """
On the management/business angle:
- Only connect this item to a business-strategy or management insight IF the
  topic genuinely calls for it. Most posts should NOT have this angle --
  default to writing purely as a platform/infra practitioner unless the
  story is clearly about a structural or strategic shift.
- If you do include it, express it as ONE plain-language observation woven
  naturally into the piece -- not a separate paragraph, not a framework
  citation.
- Never write the words "EMBA," "business school," "framework," or name any
  specific academic model (Akerlof, diffusion of innovation, etc.) in the
  output. The thinking should read as his own, not as a citation.
"""

POST_PROMPT = """You are drafting a LinkedIn POST for Himanshu Rai,
a Senior DevOps/Platform Engineer at Barclays, writing in professional
English for a management/tech-strategy audience, with the goal of building
visibility toward engineering-leadership/management roles.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + MANAGEMENT_ANGLE_RULE + """
Write a post about this item. Structure: a hook in the first line, the core
insight, and end per the closing-question rule above. Ground it in genuine
platform-engineering/infra substance first -- the business angle (if used
at all) supports that, it doesn't replace it.

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}

Output only the post body text. No hashtags, nothing else.
Stay well under 3,500 characters.
"""

ARTICLE_PROMPT = """You are drafting a LinkedIn ARTICLE (800-1200 words) for Himanshu Rai,
a Senior DevOps/Platform Engineer at Barclays, with the goal of building
visibility toward engineering-leadership/management roles.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + MANAGEMENT_ANGLE_RULE + """
Structure it like a strategy analysis grounded in practitioner experience:
1. The situation -- what actually changed
2. Why it matters beyond the immediate news cycle
3. A management/strategy lens applied to it, ONLY if genuinely warranted by
   this specific topic (see rule above) -- otherwise stay purely technical
   and skip this section
4. Practical implication for practitioners/leaders reading this
5. A closing point of view -- take a real stance, don't hedge into mush

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}

Output only the article text with a title at the top. No hashtags, nothing else.
Write the full 800-1200 words -- there's no LinkedIn character-limit constraint
here, since this goes through LinkedIn's separate Articles editor (manual
paste), not the short-form post composer. Don't cut it short to fit a post-
sized budget.
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
  (e.g. #SRE, #FinOps, #CircularEconomy, #CloudCost) -- these reach a smaller
  but highly relevant audience who follows that exact hashtag. Never use
  #EMBA or other academic/degree hashtags -- keep tags grounded in the
  actual topic, not his education.
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

TEASER_POST_PROMPT = """You are drafting a short LinkedIn POST for Himanshu
Rai that points his audience toward a longer ARTICLE he's publishing
separately on the same topic -- the article itself is handled elsewhere;
this is just the hook that gets people to go read it.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + """
This post has one job: make someone want to click through to the article.
Open with the sharpest insight or claim from the piece, don't summarize the
whole argument, leave something worth reading further for. Close by pointing
to the article in his own voice (e.g. "I go deeper on this in an article --
link's in the comments"), WITHOUT a raw link in the body -- the article's
actual LinkedIn URL only exists after he publishes it manually, so it gets
added as a first comment on this teaser post once that's done, the same way
source links are handled for regular posts.

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}

Output only the post body text. No hashtags, nothing else.
"""


CAROUSEL_SLIDE_COUNT = "5 to 7"

# Document posts (PDF carousels) are the highest-engagement LinkedIn format
# in 2026 (~6.6% engagement vs ~2% for plain text posts) -- this is a
# DIFFERENT deliverable shape than post/article: instead of one body of
# prose, it's a title slide + several short skimmable slides + a normal
# post caption that accompanies the uploaded PDF in the feed. There's no
# LinkedIn API for uploading a document-post PDF on any individual-dev
# tier (same permanent wall as native Articles -- see README), so like
# articles, this is delivered as a copy-paste-ready package: Himanshu drops
# the slide text into Canva/a slide template, exports as PDF, and uploads
# it manually as a LinkedIn document post with the generated caption.
CAROUSEL_PROMPT = """You are creating the content for a LinkedIn DOCUMENT POST
(PDF carousel) for Himanshu Rai, a Senior DevOps/Platform Engineer at
Barclays, with the goal of building visibility toward engineering-
leadership/management roles. Document posts are a highly visual, skimmable
format -- NOT a text post split into pieces. Each slide must stand alone
and be readable in about 3 seconds.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + MANAGEMENT_ANGLE_RULE + f"""
Output ONLY valid JSON, no markdown fences, no preamble, in exactly this
shape:
{{{{"title": "...", "slides": ["...", "..."], "caption": "..."}}}}

- "title": the title slide's headline. Short, punchy, states the core claim
  or question directly -- this is what makes someone stop scrolling and tap
  into the document.
- "slides": {CAROUSEL_SLIDE_COUNT} slide bodies that follow the title slide,
  in order. Each entry is what goes on ONE slide -- 1 to 3 short lines,
  never a paragraph. Build one clear idea per slide, in a logical sequence
  (e.g. problem -> cause -> insight -> implication -> takeaway). The last
  slide should land the closing point of view, not trail off.
- "caption": the normal LinkedIn post text that goes in the feed alongside
  the uploaded document (this is a real post caption, not a slide) --
  follow the algorithm rules above (no raw links, no engagement-bait
  closer, genuine closing question). Keep it short -- its job is to make
  someone open the document, not repeat its content. Do NOT include
  hashtags in the caption -- they're generated separately.

Item: {{title}}
Summary: {{summary}}
Source: {{source}}
Classification reasoning: {{reasoning}}
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


def generate_image_brief(candidate: dict, image_spec: str = ARTICLE_IMAGE_SPEC) -> str:
    """Dedicated small call -- returns comma-separated visual search
    keywords for a cover image. No image is generated or fetched (keeps the
    stack API-key-free for this step); Himanshu picks one manually via the
    constructed search link or Canva. image_spec defaults to the article
    cover-image dimensions but callers (e.g. carousel drafts) can pass a
    different spec -- see CAROUSEL_IMAGE_SPEC."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": IMAGE_BRIEF_PROMPT.format(
                    title=candidate["title"],
                    summary=candidate.get("summary", ""),
                    image_spec=image_spec,
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


def generate_teaser_post(candidate: dict, voice_ref: str, positioning: str) -> str:
    """Dedicated small call, articles only -- a short copy-paste-ready POST
    that teases the article and drives clicks to it. Delivered as a
    SEPARATE piece from the article package (see generate_draft_package):
    Himanshu publishes the article manually, then publishes this as its
    own normal post, then drops the article's LinkedIn URL as a first
    comment on THIS post -- same "no raw link in body" pattern used for
    regular posts' source credit, for the same reach reasons."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": TEASER_POST_PROMPT.format(
                    voice_ref=voice_ref,
                    positioning=positioning,
                    title=candidate["title"],
                    summary=candidate.get("summary", ""),
                    source=candidate.get("source", ""),
                    reasoning=candidate.get("reasoning", ""),
                ),
            }],
        )
        teaser = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if teaser and len(teaser) < LINKEDIN_MAX_CHARS:
            return teaser
    except Exception as e:
        print(f"[writer] WARN: teaser post generation failed: {e}")
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


def generate_carousel_package(candidate: dict) -> dict:
    """Dedicated call for the document-post (PDF carousel) format --
    highest-engagement LinkedIn format as of 2026 data, but a genuinely
    different shape than post/article (title slide + short standalone
    slides + a normal caption), so it gets its own prompt and its own
    JSON-structured response rather than being squeezed into
    generate_draft_package's body/hashtags shape.

    Returns {"title": ..., "slides": [...], "caption": ...}. Hashtags are
    NOT included here -- append generate_hashtags()'s output to "caption"
    the same way generate_draft_package does for posts, so the hashtag
    quality-check logic stays in one place.

    No PDF/image is generated here (keeps the stack API-key-free for this
    step, same reasoning as generate_image_brief) -- this is the raw
    content Himanshu drops into Canva/a slide template himself before
    exporting and uploading manually. There's no LinkedIn API for
    uploading a document-post PDF on any individual-dev tier, so like
    articles, this always ends as a manual last step, not an API publish.
    """
    voice_ref = load_voice_reference()
    positioning = load_positioning_strategy()

    prompt = CAROUSEL_PROMPT.format(
        voice_ref=voice_ref,
        positioning=positioning,
        title=candidate["title"],
        summary=candidate.get("summary", ""),
        source=candidate.get("source", ""),
        reasoning=candidate.get("reasoning", ""),
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # fall back safe default if the model wraps in fences despite
        # instructions -- same pattern as classifier.py
        cleaned = raw_text.strip("`").replace("json\n", "", 1)
        parsed = json.loads(cleaned)

    slides = parsed.get("slides", [])
    if not isinstance(slides, list):
        slides = []

    return {
        "title": parsed.get("title", candidate.get("title", "")),
        "slides": slides,
        "caption": parsed.get("caption", ""),
    }


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
    {"draft_text": ..., "title": ..., "image_brief": ..., "teaser_post": ...}
    (title/image_brief/teaser_post are None for posts).

    Why this exists: an article draft used to need several sequential
    Claude calls in a row -- body, hashtags, title, image brief -- inside a
    single Telegram webhook request. On Vercel's Hobby-tier 60-second
    function timeout (the hard ceiling for this plan -- already set to the
    max in vercel.json, can't be raised without upgrading), that chain
    could run long enough to get killed mid-request. A Vercel timeout is a
    hard process kill, not a Python exception, so it bypasses
    handle_command's own try/except entirely -- no error ever reaches
    Telegram, the request just goes silent. None of these calls actually
    depend on each other's OUTPUT (title, image_brief, and teaser_post only
    need `candidate`, not the finished body), so running them in parallel
    threads cuts wall-clock time to roughly the slowest single call instead
    of the sum of all of them.

    teaser_post: articles have no in-app "share to feed" the way a normal
    post does, so for articles we also generate a short, separate POST that
    teases the article and drives clicks to it -- Himanshu publishes the
    article manually, publishes this teaser as its own normal post, then
    drops the article's LinkedIn URL as a first comment on the teaser
    (never in the body -- same reach reasoning as regular posts' source
    credit). Normal "post" drafts are unaffected -- they still go straight
    to direct auto-publish on /publish or /publishnow as before.
    """
    voice_ref = load_voice_reference()
    positioning = load_positioning_strategy()

    with ThreadPoolExecutor(max_workers=5) as pool:
        body_future = pool.submit(_generate_body, candidate, confirmed_type, voice_ref, positioning)
        hashtag_future = pool.submit(generate_hashtags, candidate["title"], positioning)

        title_future = None
        image_brief_future = None
        teaser_future = None
        if confirmed_type == "article":
            title_future = pool.submit(generate_article_title, candidate)
            image_brief_future = pool.submit(generate_image_brief, candidate)
            teaser_future = pool.submit(generate_teaser_post, candidate, voice_ref, positioning)

        body = body_future.result()
        hashtags = hashtag_future.result()
        title = title_future.result() if title_future else None
        image_brief = image_brief_future.result() if image_brief_future else None
        teaser = teaser_future.result() if teaser_future else None

    reserved = len(hashtags) + 2 if hashtags else 0

    # LinkedIn's short-post character budget applies to POSTS only -- an
    # article naturally runs 800-1200 words (~5000-7500 chars), and
    # squeezing that down to fit the post-composer budget used to gut
    # articles down to a fraction of their intended length, defeating the
    # entire point of writing one. Articles ship long; Telegram delivery
    # handles the length via reply()'s automatic message-splitting instead.
    if confirmed_type == "post":
        body_limit = LINKEDIN_MAX_CHARS - reserved
        if len(body) > body_limit:
            body = condense_body(body, body_limit)

    draft_text = f"{body}\n\n{hashtags}" if hashtags else body
    # teaser gets the same hashtags -- same topic/pillar, and it saves a
    # redundant call for something that would land on nearly identical tags
    teaser_post = (f"{teaser}\n\n{hashtags}" if teaser and hashtags else teaser) if teaser else None

    return {
        "draft_text": draft_text.strip(),
        "title": title,
        "image_brief": image_brief,
        "teaser_post": teaser_post.strip() if teaser_post else None,
    }


def write_draft(candidate: dict, confirmed_type: str) -> str:
    """Kept for callers that only want the body text (e.g. write_confirmed.py,
    the local CLI bridge script, which has no 60-second constraint). Thin
    wrapper over generate_draft_package so both paths share one code path."""
    return generate_draft_package(candidate, confirmed_type)["draft_text"]


# ---------------------------------------------------------------------------
# Carousel / document posts -- Phase 6.
#
# Document posts (PDF carousels) are LinkedIn's highest-engagement format
# in 2026 (~6.6% engagement vs ~2% for plain text, per 2026 platform data).
# But same platform ceiling as native Articles: there's no reliable
# standard-tier API path to upload a PDF/multi-image document post. So this
# follows the exact same manual-bridge pattern already used for articles --
# generate copy-paste-ready slide text + a cover-visual brief, hand the
# whole package to Himanshu via Telegram, and he assembles the actual PDF
# in Canva (a few minutes) and posts it as a "Document" himself. This is
# NOT wired to auto-publish -- don't assume an API call can do this step.
# ---------------------------------------------------------------------------

# LinkedIn's recommended document-post image dimensions (2026) -- square or
# 4:5 portrait reads best in the native carousel viewer.
CAROUSEL_IMAGE_SPEC = "1080x1080px (square) or 1080x1350px (4:5 portrait)"

CAROUSEL_PROMPT = """You are drafting a LinkedIn DOCUMENT POST (a PDF
carousel, 5-7 slides) for Himanshu Rai, a Senior DevOps/Platform Engineer at
Barclays, with the goal of building visibility toward engineering-
leadership/management roles. Document posts get the highest engagement of
any LinkedIn format in 2026 -- but only when each slide earns the swipe to
the next one.

Style reference (match tone, not content):
{voice_ref}

Positioning strategy (pillars + keywords to draw from):
{positioning}
""" + ALGORITHM_RULES + MANAGEMENT_ANGLE_RULE + """
Structure:
- Slide 1 (hook): a bold claim or sharp question, big and short -- this is
  the ONLY slide most people see before deciding whether to swipe, so it
  has to work standalone. No "in this post I'll cover..." setup.
- Slides 2 through (second-to-last): one clear idea per slide, short punchy
  lines, not paragraphs -- this is read as a slide, not an article. Each
  slide should make someone want the next one.
- Final slide: a closing takeaway + the same genuine-question closer rule
  used for posts (not engagement bait).

Output format -- plain text, one slide per block, EXACTLY like this, with
nothing else outside the slide blocks:
SLIDE 1:
<text>

SLIDE 2:
<text>

(continue through the final slide)

No hashtags in slide text -- generated separately.

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}
"""


def _generate_carousel_slides(candidate: dict, voice_ref: str, positioning: str) -> str:
    prompt = CAROUSEL_PROMPT.format(
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


def generate_carousel_package(candidate: dict) -> dict:
    """Runs slide generation, hashtags, and the cover-visual brief
    concurrently (same ThreadPoolExecutor pattern as generate_draft_package,
    for the same wall-clock reason). Returns:
    {"draft_text": <slide blocks>, "image_brief": ..., "hashtags": ...}
    -- draft_text is the slide copy ONLY; the caller decides whether/how to
    append hashtags when assembling the final Telegram message, since
    hashtags belong on the LinkedIn post caption that accompanies the
    uploaded PDF, not inside the slides themselves.
    """
    voice_ref = load_voice_reference()
    positioning = load_positioning_strategy()

    with ThreadPoolExecutor(max_workers=3) as pool:
        slides_future = pool.submit(_generate_carousel_slides, candidate, voice_ref, positioning)
        hashtag_future = pool.submit(generate_hashtags, candidate["title"], positioning)
        visual_future = pool.submit(generate_image_brief, candidate, CAROUSEL_IMAGE_SPEC)

        slides_text = slides_future.result()
        hashtags = hashtag_future.result()
        visual_brief = visual_future.result()

    return {
        "draft_text": slides_text,
        "image_brief": visual_brief,
        "hashtags": hashtags,
    }


# ---------------------------------------------------------------------------
# Manual comment-draft bridge -- Phase 6.
#
# A live "scan LinkedIn's feed and suggest posts to comment on" digest was
# considered, but hits the exact same platform ceiling as auto-publishing
# comments and Articles: standard-tier LinkedIn apps have no API access to
# read other people's feed content at all (that's a Marketing Developer
# Platform capability, not available here). So instead of a fake feature
# that can't actually scan LinkedIn, this is the honest, safe version:
# Himanshu pastes the text of a post he's already looking at, and gets a
# genuine, specific comment drafted in his voice -- same manual-in-the-loop
# spirit as the article/carousel packages above.
# ---------------------------------------------------------------------------

COMMENT_PROMPT = """Himanshu Rai (Senior DevOps/Platform Engineer at
Barclays, building toward engineering-leadership roles) wants to leave a
genuine, substantive comment on someone else's LinkedIn post below -- not a
generic "Great post!" reply. The comment should add something real: a
related experience, a respectful pushback, an extending idea, or a specific
question -- something that shows he actually read and thought about it.

Style reference (match tone, not content):
{voice_ref}

Keep it to 2-4 sentences, no hashtags, no self-promotion, and no forced
management/business-school language -- this is a peer commenting on a
peer's post, not a pitch.

Their post:
{post_text}

Output only the comment text.
"""


def generate_comment(post_text: str) -> str:
    voice_ref = load_voice_reference()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": COMMENT_PROMPT.format(voice_ref=voice_ref, post_text=post_text),
        }],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
