"""
Carousel PDF -- turns a stored carousel draft (SLIDE N: blocks + optional
hashtag line) into a branded 4:5 PDF for LinkedIn's document-post viewer.

Pure Python (reportlab only) so it runs inside the Vercel function.

    slides, hashtags = parse_slides(draft_text)
    pdf_bytes = render_pdf(slides, title="...")
"""
import io
import re

from reportlab.lib.colors import HexColor
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

# 1080x1350 = LinkedIn's recommended 4:5 portrait document size
PAGE_W, PAGE_H = 1080, 1350
MARGIN_X = 100
HEADER_Y = PAGE_H - 90
FOOTER_Y = 70
TEXT_TOP = PAGE_H - 200
TEXT_BOTTOM = 170

AUTHOR = "Himanshu Rai"
AUTHOR_TAGLINE = "Platform Engineering \u00b7 Strategy"

DARK_BG = HexColor("#0F172A")
DARK_TEXT = HexColor("#F8FAFC")
DARK_MUTED = HexColor("#94A3B8")
LIGHT_BG = HexColor("#F8FAFC")
LIGHT_TEXT = HexColor("#0F172A")
LIGHT_MUTED = HexColor("#64748B")
ACCENT = HexColor("#38BDF8")

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

# Matches "SLIDE 1:", "**Slide 2:**", "Slide 3 -", "## SLIDE 4 (Hook):" etc.
_SLIDE_HEADER = re.compile(
    r"^\s*[#*_\s]*slide\s+(\d+)\b[^\n]*$", re.IGNORECASE | re.MULTILINE
)

# Built-in PDF fonts only cover cp1252 -- map common extras, drop the rest
_CHAR_MAP = {
    "\u2192": "->", "\u2190": "<-", "\u2194": "<->", "\u21d2": "=>",
    "\u2713": "-", "\u2714": "-", "\u2705": "", "\u274c": "",
    "\u2264": "<=", "\u2265": ">=", "\u2248": "~", "\u2260": "!=",
    "\u20b9": "Rs ", "\u00a0": " ", "\u2009": " ", "\u202f": " ",
}


def _sanitize(text: str) -> str:
    for src, dst in _CHAR_MAP.items():
        text = text.replace(src, dst)
    return text.encode("cp1252", errors="ignore").decode("cp1252")


def _clean_slide(text: str) -> str:
    lines = []
    for line in text.strip().splitlines():
        line = line.rstrip()
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)   # **bold**
        line = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", line)  # *italic*
        line = re.sub(r"^#+\s*", "", line)              # markdown headings
        line = re.sub(r"^\s*[-*]\s+", "\u2022 ", line)  # bullets
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return _sanitize(cleaned)


def _split_hashtags(text: str):
    """Pulls a trailing hashtag-only line off the end of the text."""
    lines = text.rstrip().splitlines()
    if lines:
        tokens = lines[-1].split()
        if tokens and all(t.startswith("#") for t in tokens):
            return "\n".join(lines[:-1]), " ".join(tokens)
    return text, ""


def parse_slides(draft_text: str):
    """Returns (slides: list[str], hashtags: str). Raises ValueError if the
    draft doesn't contain at least 2 usable SLIDE blocks."""
    if not draft_text or not draft_text.strip():
        raise ValueError("Draft is empty.")

    body, hashtags = _split_hashtags(draft_text)

    headers = list(_SLIDE_HEADER.finditer(body))
    if len(headers) < 2:
        raise ValueError(
            "Expected 'SLIDE 1:', 'SLIDE 2:' ... blocks but found "
            f"{len(headers)}. Regenerate with /carousel."
        )

    slides = []
    for i, match in enumerate(headers):
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        chunk = body[start:end]
        # inline content on the header line itself ("SLIDE 1: Big claim")
        inline = match.group(0).split(":", 1)[1] if ":" in match.group(0) else ""
        inline = re.sub(r"^[*_\s]+|[*_\s]+$", "", inline)
        text = _clean_slide(f"{inline}\n{chunk}" if inline else chunk)
        if text:
            slides.append(text)

    if len(slides) < 2:
        raise ValueError("Fewer than 2 slides had any text in them.")

    return slides, hashtags


def _fit_text(text: str, font: str, max_size: int, min_size: int,
              width: float, height: float):
    """Largest font size (stepping down) where the wrapped text fits."""
    size = max_size
    while size >= min_size:
        leading = size * 1.3
        lines = []
        for para in text.split("\n"):
            if not para.strip():
                lines.append("")
                continue
            lines.extend(simpleSplit(para, font, size, width))
        if len(lines) * leading <= height:
            return size, leading, lines
        size -= 2
    # still too long at min size -- render at min size, clip the overflow
    leading = min_size * 1.3
    lines = []
    for para in text.split("\n"):
        lines.extend(simpleSplit(para, font, min_size, width) if para.strip() else [""])
    max_lines = int(height // leading)
    return min_size, leading, lines[:max_lines]



# ---------------------------------------------------------------------------
# Generated graphics -- pure reportlab vector drawing, no external images or
# API keys, so rendering can never fail on a network call and every carousel
# keeps a consistent branded look.
#   - hook + closing slides: abstract background pattern (dot grid + rings)
#   - content slides: big faint slide-number watermark + a topic icon badge
#     picked by keyword match on the slide's own text
# ---------------------------------------------------------------------------
import hashlib
import math

ICON_KEYWORDS = [
    ("database", ("database", "postgres", "sql", "db ", "query", "storage",
                  "data ", "warehouse", "analytics", "olap", "oltp")),
    ("cloud", ("cloud", "aws", "azure", "gcp", "saas", "serverless", "hosted")),
    ("cost", ("cost", "price", "pricing", "budget", "spend", "roi", "$",
              "revenue", "saving", "money", "cheaper", "expensive")),
    ("chart", ("growth", "adoption", "market", "percent", "%", "metric",
               "benchmark", "performance", "faster", "speed", "scale")),
    ("shield", ("security", "secure", "risk", "compliance", "regulat",
                "trust", "safe", "reliab", "governance", "audit")),
    ("people", ("team", "people", "org", "hiring", "talent", "culture",
                "leader", "engineer", "dba", "skill", "staff", "person")),
    ("server", ("kubernetes", "k8s", "cluster", "infra", "server", "node",
                "platform", "eks", "container", "deploy", "production")),
    ("gear", ("process", "workflow", "automat", "pipeline", "tooling",
              "operat", "maintain", "ecosystem", "extension")),
    ("bulb", ("idea", "lesson", "insight", "takeaway", "learn", "why it matters",
              "the point", "key ")),
    ("warning", ("but ", "however", "caution", "careful", "trade-off",
                 "tradeoff", "downside", "limitation", "not ready", "risk")),
    ("rocket", ("new", "launch", "innovation", "next-gen", "modern",
                "breakthrough", "release", "startup")),
]


def _pick_icon(text: str, index: int) -> str:
    lower = f" {text.lower()} "
    best, best_hits = None, 0
    for name, words in ICON_KEYWORDS:
        hits = sum(lower.count(w) for w in words)
        if hits > best_hits:
            best, best_hits = name, hits
    if best:
        return best
    fallback = ["network", "bulb", "gear", "chart"]
    return fallback[index % len(fallback)]


def _seed(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:8], 16)


def _draw_pattern(c, text: str, color):
    """Dot grid in one corner + concentric rings in the opposite one.
    Corner choice varies per slide text so carousels don't look cloned."""
    seed = _seed(text)
    flip = seed % 2 == 0

    c.saveState()
    c.setFillColor(color)
    c.setFillAlpha(0.22)
    # dot columns live in the side margin (outside the text column), so
    # they never sit behind text
    gx = PAGE_W - 70 if flip else 30
    for row in range(14):
        for col in range(2):
            r = 4 if (row + col + seed) % 3 else 7
            c.circle(gx + col * 28, 180 + row * 44, r, stroke=0, fill=1)

    c.setStrokeColor(color)
    c.setLineWidth(3)
    cx = 40 if flip else PAGE_W - 40
    cy = PAGE_H - 260
    for i, radius in enumerate((90, 160, 230, 300)):
        c.setStrokeAlpha(0.22 - i * 0.045)
        c.circle(cx, cy, radius, stroke=1, fill=0)
    c.restoreState()


def _draw_watermark(c, number: int, color):
    c.saveState()
    c.setFillColor(color)
    c.setFillAlpha(0.05)
    c.setFont(FONT_BOLD, 420)
    c.drawRightString(PAGE_W - 40, 140, f"{number:02d}")
    c.restoreState()


def _draw_icon(c, kind: str, cx: float, cy: float, s: float, color):
    """Icon centred on (cx, cy), roughly s x s in size."""
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(s * 0.07)
    c.setLineCap(1)
    c.setLineJoin(1)
    h = s / 2

    if kind == "database":
        w, eh = s * 0.8, s * 0.22
        top, bot = cy + h * 0.75, cy - h * 0.75
        c.ellipse(cx - w / 2, top - eh / 2, cx + w / 2, top + eh / 2, stroke=1, fill=0)
        c.line(cx - w / 2, top, cx - w / 2, bot)
        c.line(cx + w / 2, top, cx + w / 2, bot)
        for y in (top - (top - bot) / 3, top - 2 * (top - bot) / 3, bot):
            p = c.beginPath()
            p.moveTo(cx - w / 2, y)
            p.curveTo(cx - w / 2, y - eh * 0.7, cx + w / 2, y - eh * 0.7, cx + w / 2, y)
            c.drawPath(p, stroke=1, fill=0)

    elif kind == "cloud":
        p = c.beginPath()
        b = cy - h * 0.45
        p.moveTo(cx - h * 0.75, b)
        p.lineTo(cx + h * 0.75, b)
        p.curveTo(cx + h * 1.15, b, cx + h * 1.1, b + h * 0.65, cx + h * 0.6, b + h * 0.6)
        p.curveTo(cx + h * 0.55, b + h * 1.35, cx - h * 0.35, b + h * 1.4, cx - h * 0.35, b + h * 0.75)
        p.curveTo(cx - h * 1.15, b + h * 0.85, cx - h * 1.2, b, cx - h * 0.75, b)
        p.close()
        c.drawPath(p, stroke=1, fill=0)

    elif kind == "chart":
        base = cy - h * 0.8
        bw = s * 0.17
        for i, frac in enumerate((0.35, 0.6, 0.95)):
            x = cx - h * 0.75 + i * s * 0.28
            c.roundRect(x, base, bw, s * 0.8 * frac, s * 0.03, stroke=0, fill=1)
        c.line(cx - h * 0.9, base - s * 0.06, cx + h * 0.9, base - s * 0.06)

    elif kind == "cost":
        c.circle(cx, cy, h * 0.85, stroke=1, fill=0)
        c.setFont(FONT_BOLD, s * 0.75)
        c.drawCentredString(cx, cy - s * 0.27, "$")

    elif kind == "shield":
        p = c.beginPath()
        p.moveTo(cx, cy + h * 0.9)
        p.lineTo(cx + h * 0.75, cy + h * 0.6)
        p.curveTo(cx + h * 0.75, cy - h * 0.2, cx + h * 0.3, cy - h * 0.7, cx, cy - h * 0.9)
        p.curveTo(cx - h * 0.3, cy - h * 0.7, cx - h * 0.75, cy - h * 0.2, cx - h * 0.75, cy + h * 0.6)
        p.close()
        c.drawPath(p, stroke=1, fill=0)
        p = c.beginPath()
        p.moveTo(cx - h * 0.32, cy)
        p.lineTo(cx - h * 0.05, cy - h * 0.28)
        p.lineTo(cx + h * 0.38, cy + h * 0.25)
        c.drawPath(p, stroke=1, fill=0)

    elif kind == "people":
        for dx, sc in ((-h * 0.42, 0.8), (h * 0.42, 0.8), (0, 1.0)):
            r = h * 0.24 * sc
            hy = cy + h * 0.3 * sc
            c.circle(cx + dx, hy, r, stroke=1, fill=0)
            p = c.beginPath()
            bw = h * 0.42 * sc
            p.moveTo(cx + dx - bw, cy - h * 0.75)
            p.curveTo(cx + dx - bw, hy - r * 2.6, cx + dx + bw, hy - r * 2.6, cx + dx + bw, cy - h * 0.75)
            c.drawPath(p, stroke=1, fill=0)

    elif kind == "server":
        rh = s * 0.24
        for i in range(3):
            y = cy + h * 0.85 - (i + 1) * rh - i * s * 0.07
            c.roundRect(cx - h * 0.85, y, s * 0.85, rh, s * 0.04, stroke=1, fill=0)
            c.circle(cx + h * 0.55, y + rh / 2, s * 0.03, stroke=0, fill=1)
            c.line(cx - h * 0.6, y + rh / 2, cx - h * 0.1, y + rh / 2)

    elif kind == "gear":
        teeth = 8
        for i in range(teeth):
            a = 2 * math.pi * i / teeth
            c.line(cx + math.cos(a) * h * 0.55, cy + math.sin(a) * h * 0.55,
                   cx + math.cos(a) * h * 0.9, cy + math.sin(a) * h * 0.9)
        c.circle(cx, cy, h * 0.55, stroke=1, fill=0)
        c.circle(cx, cy, h * 0.2, stroke=1, fill=0)

    elif kind == "bulb":
        c.circle(cx, cy + h * 0.2, h * 0.55, stroke=1, fill=0)
        c.line(cx - h * 0.25, cy - h * 0.5, cx + h * 0.25, cy - h * 0.5)
        c.line(cx - h * 0.2, cy - h * 0.72, cx + h * 0.2, cy - h * 0.72)
        for a in (90, 30, 150):
            r = math.radians(a)
            c.line(cx + math.cos(r) * h * 0.75, cy + h * 0.2 + math.sin(r) * h * 0.75,
                   cx + math.cos(r) * h * 0.95, cy + h * 0.2 + math.sin(r) * h * 0.95)

    elif kind == "warning":
        p = c.beginPath()
        p.moveTo(cx, cy + h * 0.85)
        p.lineTo(cx + h * 0.95, cy - h * 0.75)
        p.lineTo(cx - h * 0.95, cy - h * 0.75)
        p.close()
        c.drawPath(p, stroke=1, fill=0)
        c.line(cx, cy + h * 0.3, cx, cy - h * 0.2)
        c.circle(cx, cy - h * 0.48, s * 0.035, stroke=0, fill=1)

    elif kind == "rocket":
        # upward-trending arrow
        p = c.beginPath()
        p.moveTo(cx - h * 0.85, cy - h * 0.6)
        p.lineTo(cx - h * 0.25, cy)
        p.lineTo(cx + h * 0.1, cy - h * 0.3)
        p.lineTo(cx + h * 0.8, cy + h * 0.6)
        c.drawPath(p, stroke=1, fill=0)
        p = c.beginPath()
        p.moveTo(cx + h * 0.35, cy + h * 0.65)
        p.lineTo(cx + h * 0.85, cy + h * 0.65)
        p.lineTo(cx + h * 0.85, cy + h * 0.15)
        c.drawPath(p, stroke=1, fill=0)

    elif kind == "question":
        c.roundRect(cx - h * 0.9, cy - h * 0.45, s * 0.9, s * 0.65, s * 0.12, stroke=1, fill=0)
        p = c.beginPath()
        p.moveTo(cx - h * 0.4, cy - h * 0.45)
        p.lineTo(cx - h * 0.55, cy - h * 0.85)
        p.lineTo(cx - h * 0.05, cy - h * 0.45)
        c.drawPath(p, stroke=1, fill=0)
        c.setFont(FONT_BOLD, s * 0.45)
        c.drawCentredString(cx, cy - h * 0.15, "?")

    else:  # network
        pts = [(cx, cy + h * 0.7), (cx - h * 0.7, cy - h * 0.1),
               (cx + h * 0.7, cy - h * 0.1), (cx - h * 0.3, cy - h * 0.75),
               (cx + h * 0.35, cy - h * 0.7)]
        for a, b in ((0, 1), (0, 2), (1, 3), (2, 4), (3, 4), (1, 2)):
            c.line(*pts[a], *pts[b])
        for x, y in pts:
            c.circle(x, y, s * 0.08, stroke=0, fill=1)

    c.restoreState()


def _draw_icon_badge(c, kind: str, x: float, y: float, size: float, color):
    """Rounded tinted square with the icon centred in it. (x, y) = bottom-left."""
    c.saveState()
    c.setFillColor(color)
    c.setFillAlpha(0.14)
    c.roundRect(x, y, size, size, size * 0.22, stroke=0, fill=1)
    c.restoreState()
    _draw_icon(c, kind, x + size / 2, y + size / 2, size * 0.55, color)


def _draw_slide(c, text: str, index: int, total: int):
    dark = index == 0 or index == total - 1
    bg = DARK_BG if dark else LIGHT_BG
    fg = DARK_TEXT if dark else LIGHT_TEXT
    muted = DARK_MUTED if dark else LIGHT_MUTED

    c.setFillColor(bg)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # generated graphics (drawn first so text always sits on top)
    badge = 140
    if dark:
        _draw_pattern(c, text, ACCENT)
    else:
        _draw_watermark(c, index + 1, fg)

    # accent bar
    c.setFillColor(ACCENT)
    c.rect(MARGIN_X, HEADER_Y - 22, 80, 8, stroke=0, fill=1)

    # header
    c.setFillColor(muted)
    c.setFont(FONT_BOLD, 26)
    c.drawString(MARGIN_X, HEADER_Y, AUTHOR.upper())
    c.setFont(FONT, 26)
    c.drawRightString(PAGE_W - MARGIN_X, HEADER_Y, f"{index + 1} / {total}")

    # body -- hook slide gets the biggest type
    width = PAGE_W - 2 * MARGIN_X
    # every slide except the hook gets an icon badge at the top of the text
    # area, so the text block starts below it
    # hook slide gets a larger hero badge picked from its topic
    if index == 0:
        kind, badge = _pick_icon(text, index), 180
    elif index == total - 1:
        kind = "question"
    else:
        kind = _pick_icon(text, index)
    _draw_icon_badge(c, kind, MARGIN_X, TEXT_TOP - badge, badge, ACCENT)
    text_top = TEXT_TOP - badge - 50
    height = text_top - TEXT_BOTTOM
    if index == 0:
        font, max_size, min_size = FONT_BOLD, 84, 40
    elif index == total - 1:
        font, max_size, min_size = FONT_BOLD, 60, 32
    else:
        font, max_size, min_size = FONT, 56, 28

    size, leading, lines = _fit_text(text, font, max_size, min_size, width, height)
    block_h = len(lines) * leading
    # vertically centre the block in the text area
    y = TEXT_BOTTOM + (height + block_h) / 2 - size

    # Bold the first line as a mini-heading only when it's genuinely a
    # heading: a short standalone first paragraph followed by more text --
    # never the first wrapped line of a long paragraph.
    paras = [p for p in text.split("\n") if p.strip()]
    first_is_heading = (
        index not in (0, total - 1)
        and len(paras) > 1
        and len(simpleSplit(paras[0], FONT_BOLD, size, width)) == 1
        and len(paras[0]) <= 60
    )

    c.setFillColor(fg)
    for i, line in enumerate(lines):
        c.setFont(FONT_BOLD if (i == 0 and first_is_heading) else font, size)
        c.drawString(MARGIN_X, y, line)
        y -= leading

    # footer
    c.setStrokeColor(muted)
    c.setLineWidth(1)
    c.line(MARGIN_X, FOOTER_Y + 40, PAGE_W - MARGIN_X, FOOTER_Y + 40)
    c.setFillColor(muted)
    c.setFont(FONT, 24)
    c.drawString(MARGIN_X, FOOTER_Y, AUTHOR_TAGLINE)
    if index < total - 1:
        c.drawRightString(PAGE_W - MARGIN_X, FOOTER_Y, "Swipe ->")


def render_pdf(slides, title: str = "Document") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(_sanitize(title)[:200])
    c.setAuthor(AUTHOR)
    total = len(slides)
    for i, text in enumerate(slides):
        _draw_slide(c, text, i, total)
        c.showPage()
    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    # local preview: python -m src.carousel_pdf draft.txt out.pdf
    import sys
    raw = open(sys.argv[1]).read()
    s, tags = parse_slides(raw)
    open(sys.argv[2] if len(sys.argv) > 2 else "preview.pdf", "wb").write(
        render_pdf(s, "Preview")
    )
    print(f"{len(s)} slides, hashtags: {tags!r}")
