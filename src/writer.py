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

# Algorithm-aware rules baked into every draft, per LinkedIn's 2026 ranking
# behavior: dwell time and meaningful comments outrank likes/shares by a wide
# margin, external links in the body suppress reach, and engagement-bait
# phrasing gets actively down-ranked. See config/positioning_strategy.md for
# the full rationale and content-pillar strategy.
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
background), and end per the closing-question rule above. 2-3 relevant
hashtags maximum, at the very end.

Item: {title}
Summary: {summary}
Source: {source}
Classification reasoning: {reasoning}

Output only the post text, nothing else.
The final post MUST NOT exceed 3,800 characters (including spaces and line breaks).
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

Output only the article text with a title at the top, nothing else.
The final article MUST NOT exceed 3,800 characters (including spaces and line breaks).
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
    draft = "".join(
        block.text for block in response.content
        if block.type == "text"
    ).strip()

    if len(draft) > LINKEDIN_MAX_CHARS:
        draft = draft[:LINKEDIN_MAX_CHARS].rsplit(" ", 1)[0].rstrip() + "..."

    return draft
