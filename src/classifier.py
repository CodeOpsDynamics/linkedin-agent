"""
Impact Classifier Agent.

Advisory only, per Himanshu's preference: this agent SUGGESTS post vs article
with reasoning and a confidence score. Nothing gets written until he confirms
or overrides the suggestion via the Telegram review step.
"""
import os
import json
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

CLASSIFIER_PROMPT = """You are an Impact Classifier for a LinkedIn thought-leadership pipeline.

Given a news item, classify it as:
- "post": narrow, tactical, single-company or single-trend news. Good for a short
  (150-300 word) LinkedIn post with a quick take.
- "article": structural change with broad reach -- affects multiple industries,
  geographies, or changes underlying incentive structures (e.g. new regulation,
  a genuine technology shift, a macro policy change, something that changes how
  the game is played for years, not weeks).

Score confidence 0.0-1.0. Give 2-3 sentences of reasoning grounded in the
specific content, not generic hedging.

Respond ONLY with JSON, no markdown fences, no preamble:
{"classification": "post" | "article", "confidence": 0.0-1.0, "reasoning": "..."}

Item title: {title}
Item summary: {summary}
Source: {source}
"""


def classify(item: dict) -> dict:
    prompt = CLASSIFIER_PROMPT.format(
        title=item["title"], summary=item.get("summary", ""), source=item.get("source", "")
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
