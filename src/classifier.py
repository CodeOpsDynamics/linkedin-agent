"""
Impact Classifier Agent.
Advisory only, per Himanshu's preference: this agent SUGGESTS post vs article
with reasoning and a confidence score. Nothing gets written until he confirms
or overrides the suggestion via the Telegram review step.

Pillar-fit check (Phase 6): confidence alone measures "how big is this
story," not "does this belong on Himanshu's profile." A structurally huge
story (e.g. international tax policy) can score high on impact while being
completely off his content pillars -- publishing it costs more in topical-
consistency signal (which LinkedIn's 2026 ranking rewards) than it gains,
and previously slipped through as a high-confidence candidate all the way to
draft generation, where the writer correctly declined to write it but the
webhook didn't know that and delivered the refusal text as if it were the
draft. So the classifier now ALSO checks pillar fit against
config/positioning_strategy.md and returns which pillar (if any) the item
maps to, so pipeline_daily.py can filter on confidence AND pillar fit
together, catching off-pillar items before they ever reach a human review
step or a generation call.
"""
import os
import json
from pathlib import Path
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

POSITIONING_PATH = Path(__file__).parent.parent / "config" / "positioning_strategy.md"


def load_positioning_strategy() -> str:
    if POSITIONING_PATH.exists():
        return POSITIONING_PATH.read_text()
    return "(no positioning strategy on file -- default to general platform-engineering + business-strategy angle)"


CLASSIFIER_PROMPT = """You are an Impact Classifier for Himanshu Rai's LinkedIn
thought-leadership pipeline. You do TWO jobs on every item:

1. TYPE -- classify as:
   - "post": narrow, tactical, single-company or single-trend news. Good for
     a short (150-300 word) LinkedIn post with a quick take.
   - "article": structural change with broad reach -- affects multiple
     industries, geographies, or changes underlying incentive structures
     (e.g. new regulation, a genuine technology shift, a macro policy
     change, something that changes how the game is played for years, not
     weeks).

2. PILLAR FIT -- this pipeline exists to build recognition in a SPECIFIC,
   narrow area, not to comment on all business news. Check the item against
   Himanshu's actual content pillars below. A story can be structurally
   huge (high impact, "article"-worthy) and still be completely off-pillar
   -- e.g. international tax policy is a genuine structural shift, but has
   no real connection to platform engineering, infra strategy, or
   sustainability in tech. Mark such items pillar_fit: false regardless of
   how big the underlying story is. Do not stretch or manufacture a
   connection -- if it would take a forced angle to make it fit, it doesn't
   fit, and pillar_fit must be false.

Positioning strategy / content pillars:
{positioning}

Score confidence 0.0-1.0 for the TYPE classification. Give 2-3 sentences of
reasoning grounded in the specific content, not generic hedging -- if
pillar_fit is false, say plainly which pillar (if any) it's closest to and
why it still doesn't genuinely belong, rather than softening it.

Respond ONLY with JSON, no markdown fences, no preamble:
{{"classification": "post" | "article", "confidence": 0.0-1.0,
  "pillar_fit": true | false,
  "pillar": "platform_engineering" | "tech_business_bridge" | "sustainability" | "none",
  "reasoning": "..."}}

Item title: {title}
Item summary: {summary}
Source: {source}
"""


def classify(item: dict) -> dict:
    prompt = CLASSIFIER_PROMPT.format(
        title=item["title"],
        summary=item.get("summary", ""),
        source=item.get("source", ""),
        positioning=load_positioning_strategy(),
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # fall back safe default if the model wraps in fences despite instructions
        cleaned = raw_text.strip("`").replace("json\n", "", 1)
        parsed = json.loads(cleaned)
    return {
        "classification": parsed.get("classification", "post"),
        "confidence": float(parsed.get("confidence", 0.5)),
        # default to True (not False) on missing/malformed field -- an
        # ambiguous parse should fall back to "let the human decide", not
        # silently drop a possibly-good candidate.
        "pillar_fit": bool(parsed.get("pillar_fit", True)),
        "pillar": parsed.get("pillar", "none"),
        "reasoning": parsed.get("reasoning", ""),
    }


def classify_batch(items):
    results = []
    for item in items:
        try:
            verdict = classify(item)
            results.append({**item, **verdict})
        except Exception as e:
            print(f"[classifier] WARN: failed on '{item['title']}': {e}")
    return results
