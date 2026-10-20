"""Reports for an unanswered question: a PDF for the asker, JSON for developers.

The PDF is the product view: why no answer could be verified, how the research
ran, what the model found, and what to try next, in two pages or so. The
developer log is the record's technical trace and diagnostics as JSON.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import MethodReturnValue, XPos, YPos

from backend.core import config

logger = logging.getLogger(__name__)
# Font subsetting reports every OpenType table it drops; that is not actionable.
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

# The workspace palette (frontend/src/styles.css).
INK = (32, 26, 58)
PAPER = (255, 244, 231)
CARD = (255, 250, 243)
PEACH = (247, 226, 209)
CORAL = (240, 91, 67)
CORAL_DARK = (200, 68, 49)
CORAL_SOFT = (253, 233, 226)
LAVENDER = (235, 200, 215)
MUTED = (112, 104, 121)
GREEN = (67, 138, 92)
LINE = (220, 210, 214)
WHITE = (255, 255, 255)

_STATUS_COLORS = {
    "single_source": ((252, 238, 210), (128, 84, 10)),
    "conflicted": ((253, 226, 219), CORAL_DARK),
    "incomplete": ((243, 226, 235), (107, 61, 89)),
}
_STEP_COLORS = {"done": GREEN, "stopped": CORAL, "not_reached": (190, 182, 192)}
_STEP_LABELS = {"done": "Done", "stopped": "Stopped here", "not_reached": "Not reached"}

_FONT_FAMILIES = (
    ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"),
    ("segoeui.ttf", "segoeuib.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("arial.ttf", "arialbd.ttf"),
    ("Arial.ttf", "Arial Bold.ttf"),
)
_DEVANAGARI_FONTS = (
    ("NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Bold.ttf"),
    ("Nirmala.ttf", "NirmalaB.ttf"),
    ("Lohit-Devanagari.ttf", "Lohit-Devanagari.ttf"),
)
_LATIN1_REPLACEMENTS = str.maketrans(
    {"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...", "•": "-", "\u00a0": " "}
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

MARGIN = 16.0


def _font_dirs() -> list[Path]:
    dirs = []
    if config.REPORT_FONT_DIR:
        dirs.append(Path(config.REPORT_FONT_DIR))
    dirs.append(Path(__file__).resolve().parents[1] / "assets" / "fonts")
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
    else:
        base = Path("/usr/share/fonts")
        dirs += [
            base / "truetype" / "noto",
            base / "opentype" / "noto",
            base / "truetype" / "dejavu",
            base / "TTF",
            base / "truetype" / "liberation",
            base / "truetype" / "lohit-devanagari",
        ]
    return dirs


def _find(name: str) -> Path | None:
    for directory in _font_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def resolve_fonts() -> dict[str, Path | None]:
    """Unicode fonts for the report; all None means built-in Helvetica (Latin-1 only)."""
    for regular_name, bold_name in _FONT_FAMILIES:
        regular = _find(regular_name)
        if regular:
            bold = _find(bold_name) or regular
            devanagari = devanagari_bold = None
            for deva_regular, deva_bold in _DEVANAGARI_FONTS:
                devanagari = _find(deva_regular)
                if devanagari:
                    devanagari_bold = _find(deva_bold) or devanagari
                    break
            return {"regular": regular, "bold": bold, "devanagari": devanagari, "devanagari_bold": devanagari_bold}
    logger.warning(
        "No Unicode font found for PDF reports; using Helvetica, which cannot show non-Latin text. "
        "Set REPORT_FONT_DIR to a directory with NotoSans-Regular.ttf and NotoSans-Bold.ttf."
    )
    return {"regular": None, "bold": None, "devanagari": None, "devanagari_bold": None}


def _shaping_available() -> bool:
    try:
        import uharfbuzz  # noqa: F401
    except ImportError:
        return False
    return True


def _date(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        moment = datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class _ReportPDF(FPDF):
    def __init__(self, reference: str, generated: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.reference = reference
        self.generated = generated
        fonts = resolve_fonts()
        self.unicode = fonts["regular"] is not None
        if self.unicode:
            self.add_font("Report", "", str(fonts["regular"]))
            self.add_font("Report", "B", str(fonts["bold"]))
            if fonts["devanagari"]:
                self.add_font("ReportDeva", "", str(fonts["devanagari"]))
                self.add_font("ReportDeva", "B", str(fonts["devanagari_bold"]))
                self.set_fallback_fonts(["ReportDeva"], exact_match=False)
            if _shaping_available():
                self.set_text_shaping(True)
            self.family_name = "Report"
        else:
            self.family_name = "helvetica"
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(True, margin=20)
        self.alias_nb_pages()

    # Text that the selected font can carry.
    def safe(self, text: object) -> str:
        value = _CONTROL.sub("", str(text or ""))
        if self.unicode:
            return value
        return value.translate(_LATIN1_REPLACEMENTS).encode("latin-1", "replace").decode("latin-1")

    def font(self, size: float, bold: bool = False, color: tuple[int, int, int] = INK) -> None:
        self.set_font(self.family_name, "B" if bold else "", size)
        if self.page and not self.current_font_is_set_on_page:
            # fpdf2 sets a new font lazily, inside the first line drawn. When that
            # line starts with a fallback-font glyph (Devanagari), the setting is
            # scoped to the line, and the next line is drawn at the old size.
            self._out(self._set_font_for_page(self.current_font, self.font_size_pt))
        self.set_text_color(*color)

    @property
    def content_width(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def text_height(self, width: float, line_height: float, text: str) -> float:
        return float(
            self.multi_cell(
                width, line_height, self.safe(text), align="L", dry_run=True, output=MethodReturnValue.HEIGHT
            )
        )

    def paragraph(self, text: str, line_height: float, width: float | None = None, x: float | None = None) -> None:
        if x is not None:
            self.set_x(x)
        self.multi_cell(
            width or self.content_width,
            line_height,
            self.safe(text),
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    def ensure_space(self, height: float) -> None:
        if self.will_page_break(height):
            self.add_page()

    def eyebrow(self, text: str, color: tuple[int, int, int] = MUTED) -> None:
        self.font(7.5, bold=True, color=color)
        self.set_char_spacing(0.6)
        self.cell(0, 4.5, self.safe(text.upper()), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_char_spacing(0)

    def section(self, title: str) -> None:
        self.ensure_space(22)
        self.ln(5)
        self.font(12.5, bold=True)
        self.cell(0, 7, self.safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_fill_color(*CORAL)
        self.rect(self.l_margin, self.get_y() + 0.6, 14, 0.9, style="F")
        self.ln(4.5)

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.font(7.5, bold=True, color=MUTED)
        self.set_char_spacing(0.6)
        self.cell(0, 5, self.safe("SPYMONK ENCYCLOPEDIA · RESEARCH REPORT"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_char_spacing(0)
        self.set_draw_color(*LINE)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(5)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_draw_color(*LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1.6)
        self.font(7.5, color=MUTED)
        half = self.content_width / 2
        self.cell(half, 4, self.safe(f"Reference {self.reference} · Generated {self.generated}"))
        self.cell(half, 4, self.safe(f"Page {self.page_no()} of {{nb}}"), align="R")


def _hero(pdf: _ReportPDF, record: dict) -> None:
    pdf.set_fill_color(*INK)
    pdf.rect(0, 0, pdf.w, 46, style="F")
    pdf.set_fill_color(*CORAL)
    pdf.rect(0, 46, pdf.w, 1.8, style="F")
    pdf.set_xy(MARGIN, 12)
    pdf.eyebrow("SpyMonk Encyclopedia · Research report", color=LAVENDER)
    pdf.set_x(MARGIN)
    pdf.font(22, bold=True, color=WHITE)
    pdf.cell(0, 12, pdf.safe("Unverified question"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.font(9, color=PEACH)
    pdf.cell(0, 5, pdf.safe(_date(record.get("created_at", ""))), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    # Status chip on the right of the band.
    chip = "NO VERIFIED ANSWER"
    pdf.font(7.5, bold=True, color=WHITE)
    pdf.set_char_spacing(0.5)
    width = pdf.get_string_width(chip) + 8
    x = pdf.w - MARGIN - width
    pdf.set_fill_color(*CORAL)
    pdf.rect(x, 15, width, 7, style="F", round_corners=True, corner_radius=3.5)
    pdf.set_xy(x, 15)
    pdf.cell(width, 7, chip, align="C")
    pdf.set_char_spacing(0)
    pdf.set_y(56)


def _question(pdf: _ReportPDF, record: dict) -> None:
    pdf.eyebrow("Your question")
    pdf.font(15, bold=True)
    pdf.paragraph(_clip(record.get("question", ""), 500), 7.2)
    pdf.ln(4)


def _outcome(pdf: _ReportPDF, record: dict) -> None:
    outcome = record.get("outcome", {})
    headline = outcome.get("message", "")
    explanation = outcome.get("explanation", "")
    if outcome.get("previous_verified_claims"):
        explanation += " Your earlier verified answers on this topic are unchanged."
    inner = pdf.content_width - 14
    pdf.font(11.5, bold=True)
    headline_h = pdf.text_height(inner, 5.8, headline)
    pdf.font(9.5)
    explanation_h = pdf.text_height(inner, 5, explanation)
    height = 5.5 + headline_h + 2 + explanation_h + 5.5
    pdf.ensure_space(height)
    top = pdf.get_y()
    pdf.set_fill_color(*CORAL_SOFT)
    pdf.rect(MARGIN, top, pdf.content_width, height, style="F", round_corners=True, corner_radius=2)
    pdf.set_fill_color(*CORAL)
    pdf.rect(MARGIN, top, 2.2, height, style="F")
    pdf.set_xy(MARGIN + 8, top + 5.5)
    pdf.font(11.5, bold=True)
    pdf.paragraph(headline, 5.8, inner, MARGIN + 8)
    pdf.ln(2)
    pdf.font(9.5)
    pdf.paragraph(explanation, 5, inner, MARGIN + 8)
    pdf.set_y(top + height + 5)


def _stats(pdf: _ReportPDF, record: dict) -> None:
    summary = record.get("summary", {})
    tiles = [
        ("Sources read", summary.get("sources_read", 0), INK),
        ("Independent websites", summary.get("independent_sites", 0), INK),
        ("Possible answers found", summary.get("claims_found", 0), INK),
        ("Answers verified", summary.get("claims_verified", 0), CORAL_DARK),
    ]
    gap = 4
    width = (pdf.content_width - gap * (len(tiles) - 1)) / len(tiles)
    height = 22
    pdf.ensure_space(height + 4)
    top = pdf.get_y()
    for index, (label, value, color) in enumerate(tiles):
        x = MARGIN + index * (width + gap)
        pdf.set_fill_color(*CARD)
        pdf.set_draw_color(*INK)
        pdf.set_line_width(0.35)
        pdf.rect(x, top, width, height, style="DF", round_corners=True, corner_radius=2)
        pdf.set_xy(x + 4, top + 3.5)
        pdf.font(19, bold=True, color=color)
        pdf.cell(width - 8, 9, str(value))
        pdf.set_xy(x + 4, top + 13.5)
        pdf.font(7.8, color=MUTED)
        pdf.cell(width - 8, 4.5, pdf.safe(label))
    pdf.set_line_width(0.2)
    pdf.set_y(top + height + 2)


def _process(pdf: _ReportPDF, record: dict) -> None:
    pdf.section("How we researched your question")
    for step in record.get("process", []):
        status = step.get("status", "not_reached")
        color = _STEP_COLORS.get(status, MUTED)
        detail_width = pdf.content_width - 44
        pdf.font(8.8)
        height = max(9.5, 5 + pdf.text_height(detail_width, 4.4, step.get("detail", "")) + 1.5)
        pdf.ensure_space(height)
        top = pdf.get_y()
        pdf.set_fill_color(*color)
        pdf.rect(MARGIN, top + 0.6, 6.5, 6.5, style="F", round_corners=True, corner_radius=3.25)
        pdf.set_xy(MARGIN, top + 0.6)
        pdf.font(8, bold=True, color=WHITE)
        pdf.cell(6.5, 6.5, pdf.safe(step.get("step", "")), align="C")
        pdf.set_xy(MARGIN + 10, top)
        pdf.font(9.8, bold=True, color=INK if status != "not_reached" else MUTED)
        pdf.cell(detail_width, 5, pdf.safe(step.get("label", "")))
        pdf.font(7.6, bold=True, color=color)
        pdf.set_xy(pdf.w - MARGIN - 30, top)
        pdf.cell(30, 5, pdf.safe(_STEP_LABELS.get(status, "").upper()), align="R")
        pdf.set_xy(MARGIN + 10, top + 5)
        pdf.font(8.8, color=MUTED)
        pdf.paragraph(step.get("detail", ""), 4.4, detail_width, MARGIN + 10)
        pdf.set_y(top + height)


def _claims(pdf: _ReportPDF, record: dict) -> None:
    claims = record.get("claims", [])
    pdf.section("What the AI found")
    if not claims:
        pdf.font(9.5, color=MUTED)
        pdf.paragraph("The AI did not find any claim backed by an exact quote from the sources.", 5)
        return
    pdf.font(8.8, color=MUTED)
    pdf.paragraph(
        "These possible answers came with exact quotes, but none met the bar for a verified answer.", 4.6
    )
    pdf.ln(2)
    text_width = pdf.content_width - 8
    for claim in claims:
        label = claim.get("status_label", "Not verified")
        background, foreground = _STATUS_COLORS.get(claim.get("status", ""), ((238, 234, 238), MUTED))
        claim_text = _clip(claim.get("claim", ""), 400)
        detail = claim.get("detail", "")
        pdf.font(10)
        claim_h = pdf.text_height(text_width, 5, claim_text)
        pdf.font(8.4)
        detail_h = pdf.text_height(text_width, 4.3, detail)
        height = 4 + 5.5 + 1.5 + claim_h + 1 + detail_h + 4
        pdf.ensure_space(height)
        top = pdf.get_y()
        pdf.set_fill_color(*CARD)
        pdf.set_draw_color(*LINE)
        pdf.rect(MARGIN, top, pdf.content_width, height, style="DF", round_corners=True, corner_radius=2)
        pdf.font(7, bold=True, color=foreground)
        pdf.set_char_spacing(0.4)
        chip_width = pdf.get_string_width(pdf.safe(label.upper())) + 6
        pdf.set_fill_color(*background)
        pdf.rect(MARGIN + 4, top + 4, chip_width, 5.5, style="F", round_corners=True, corner_radius=2.75)
        pdf.set_xy(MARGIN + 4, top + 4)
        pdf.cell(chip_width, 5.5, pdf.safe(label.upper()), align="C")
        pdf.set_char_spacing(0)
        pdf.set_xy(MARGIN + 4, top + 11)
        pdf.font(10)
        pdf.paragraph(claim_text, 5, text_width, MARGIN + 4)
        pdf.ln(1)
        pdf.font(8.4, color=MUTED)
        pdf.paragraph(detail, 4.3, text_width, MARGIN + 4)
        pdf.set_y(top + height + 2.5)


def _sources(pdf: _ReportPDF, record: dict) -> None:
    sources = record.get("sources", [])
    if not sources:
        return
    pdf.section("Sources we read")
    shown = sources[:8]
    for source in shown:
        pdf.ensure_space(6)
        pdf.font(9)
        title = _clip(source.get("title") or source.get("url", ""), 90)
        pdf.set_x(MARGIN)
        pdf.cell(4, 5, pdf.safe("•"))
        pdf.cell(pdf.get_string_width(pdf.safe(title)) + 2, 5, pdf.safe(title))
        pdf.font(8.4, color=MUTED)
        pdf.cell(0, 5, pdf.safe(f"· {source.get('site', '')}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if len(sources) > len(shown):
        pdf.font(8.4, color=MUTED)
        pdf.paragraph(f"and {len(sources) - len(shown)} more.", 5)


def _next_steps(pdf: _ReportPDF, record: dict) -> None:
    suggestions = record.get("suggestions", [])
    if suggestions:
        pdf.section("What you can try next")
        for suggestion in suggestions:
            pdf.font(9.5)
            height = pdf.text_height(pdf.content_width - 6, 5, suggestion) + 1.5
            pdf.ensure_space(height)
            top = pdf.get_y()
            pdf.set_fill_color(*CORAL)
            pdf.rect(MARGIN + 0.5, top + 1.8, 1.6, 1.6, style="F", round_corners=True, corner_radius=0.8)
            pdf.paragraph(suggestion, 5, pdf.content_width - 6, MARGIN + 6)
            pdf.ln(1.5)
    note = (
        "About this report: SpyMonk saves an answer only when a claim is found, word for word, in sources "
        "from at least two independent websites. Unverified does not mean false; it means the evidence was "
        "not strong enough to rely on."
    )
    pdf.font(8, color=MUTED)
    height = pdf.text_height(pdf.content_width - 10, 4.2, note) + 8
    pdf.ln(4)
    pdf.ensure_space(height)
    top = pdf.get_y()
    pdf.set_fill_color(*PAPER)
    pdf.rect(MARGIN, top, pdf.content_width, height, style="F", round_corners=True, corner_radius=2)
    pdf.set_xy(MARGIN + 5, top + 4)
    pdf.paragraph(note, 4.2, pdf.content_width - 10, MARGIN + 5)


def render_user_report(record: dict[str, Any]) -> bytes:
    """The asker's report as PDF bytes."""
    pdf = _ReportPDF(reference=record.get("id", ""), generated=_date(datetime.now(timezone.utc).isoformat()))
    pdf.set_title(pdf.safe(f"Research report: {_clip(record.get('question', ''), 120)}"))
    pdf.set_author("SpyMonk Encyclopedia")
    pdf.set_creator("SpyMonk Encyclopedia")
    pdf.set_subject("Why this question could not be answered with verified evidence")
    pdf.add_page()
    _hero(pdf, record)
    _question(pdf, record)
    _outcome(pdf, record)
    _stats(pdf, record)
    _process(pdf, record)
    _claims(pdf, record)
    _sources(pdf, record)
    _next_steps(pdf, record)
    return bytes(pdf.output())


def render_developer_log(record: dict[str, Any]) -> bytes:
    """The technical trace and diagnostics as indented JSON."""
    log = {
        "report": "spymonk.developer_log",
        "schema_version": record.get("schema_version", 1),
        "inquiry_id": record.get("id", ""),
        "request_id": record.get("request_id", ""),
        "created_at": record.get("created_at", ""),
        "question": record.get("question", ""),
        "topic_key": record.get("topic_key", ""),
        "outcome": record.get("outcome", {}),
        "summary": record.get("summary", {}),
        **record.get("developer", {}),
    }
    return json.dumps(log, indent=2, ensure_ascii=False).encode("utf-8")


def report_filename(record: dict[str, Any], kind: str, extension: str) -> str:
    """An ASCII download name, so no header encoding is needed."""
    slug = re.sub(r"[^a-z0-9-]+", "", str(record.get("topic_slug", "")).lower())[:40].strip("-") or "question"
    stamp = str(record.get("id", ""))[:8] or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"spymonk-{kind}-{slug}-{stamp}.{extension}"
