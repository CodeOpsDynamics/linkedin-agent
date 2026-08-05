"""
Writer Agent -- Phase 2.

Generates a draft (post or article) once the classification has been
confirmed by Himanshu. Two distinct prompt templates rather than two
separate agents -- the difference is length/structure, not reasoning style.
"""
import os
from pathlib import Path
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VOICE_REF_PATH = Path(__file__).parent.parent / "config" / "voice_reference.md"
POSITIONING_PATH = Path(__file__).parent.parent / "config" / "positioning_strategy.md"
# keep below LinkedIn's 4000-character hard limit
LINKEDIN_MAX_CHARS = 3800
HASHTAG_DELIMITER = "---HASHTAGS---"

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
- Where a positioning keyword fits naturally, use it. Never stack more than
  one or two -- forced keyword density reads as SEO spam and hurts, not helps.

CRITICAL OUTPUT FORMAT:
Write the closing question as the LAST line of the main body. Then, on a new
line, write exactly this delimiter: """ + HASHTAG_DELIMITER + """
Then on the line after that, write 2-3 relevant hashtags separated by spaces,
nothing else. This separation is required so hashtags are never accidentally
cut off -- follow it exactly, every time.
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

Output only the post body, the delimiter, then the hashtags -- nothing else.
The body itself (not counting hashtags) should stay well under 3,500 characters.
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

Output only the article text with a title at the top, then the delimiter,
then the hashtags -- nothing else.
The article body itself (not counting hashtags) should stay well under 3,500 characters.
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


def write_draft(candidate: dict, confirmed_type: str) -> str:
    voice_ref = load_voice_reference()
    positioning = load_positioning_strategy()
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
    raw = "".join(
        block.text for block in response.content
        if block.type == "text"
    ).strip()

    # Split body from hashtags using the delimiter -- this guarantees
    # hashtags survive truncation, since only the body gets trimmed.
    if HASHTAG_DELIMITER in raw:
        body, hashtags = raw.split(HASHTAG_DELIMITER, 1)
        body = body.strip()
        hashtags = hashtags.strip()
    else:
        # model didn't follow the format for some reason -- fall back to
        # treating the whole thing as body, no hashtags, rather than risk
        # cutting real content off with a bad split
        body = raw
        hashtags = ""

    # Reserve room for the hashtags (plus a blank line) so truncation
    # never eats into them.
    reserved = len(hashtags) + 2 if hashtags else 0
    body_limit = LINKEDIN_MAX_CHARS - reserved

    if len(body) > body_limit:
        body = body[:body_limit].rsplit(" ", 1)[0].rstrip() + "..."

    draft = f"{body}\n\n{hashtags}" if hashtags else body
    return draft.strip()
