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
"""
import os
from pathlib import Path
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VOICE_REF_PATH = Path(__file__).parent.parent / "config" / "voice_reference.md"
POSITIONING_PATH = Path(__file__).parent.parent / "config" / "positioning_strategy.md"
# keep below LinkedIn's 4000-character hard limit
LINKEDIN_MAX_CHARS = 3800
HASHTAG_RESERVE = 80  # room reserved for "\n\n#Tag1 #Tag2 #Tag3"

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

HASHTAG_PROMPT = """Given this LinkedIn post topic and positioning strategy,
output exactly 2-3 relevant hashtags, space-separated, nothing else -- no
preamble, no explanation, no line breaks. Example output format:
#PlatformEngineering #EngineeringLeadership

Topic: {title}
Positioning pillars/keywords to draw from:
{positioning}
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
    body can never crowd hashtags out via truncation."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": HASHTAG_PROMPT.format(title=title, positioning=positioning),
            }],
        )
        tags = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        # sanity check -- if the model ignored instructions and returned
        # something that isn't hashtags, don't use it
        if tags and tags.count("#") >= 1 and len(tags) < 120:
            return tags
    except Exception as e:
        print(f"[writer] WARN: hashtag generation failed: {e}")
    return ""


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
    body = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    hashtags = generate_hashtags(candidate["title"], positioning)
    reserved = len(hashtags) + 2 if hashtags else 0
    body_limit = LINKEDIN_MAX_CHARS - reserved

    if len(body) > body_limit:
        body = condense_body(body, body_limit)

    draft = f"{body}\n\n{hashtags}" if hashtags else body
    return draft.strip()
