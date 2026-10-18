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


def _draw_slide(c, text: str, index: int, total: int):
    dark = index == 0 or index == total - 1
    bg = DARK_BG if dark else LIGHT_BG
    fg = DARK_TEXT if dark else LIGHT_TEXT
    muted = DARK_MUTED if dark else LIGHT_MUTED

    c.setFillColor(bg)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

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
    height = TEXT_TOP - TEXT_BOTTOM
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
