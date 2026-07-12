"""Shared reportlab styling + build engine for the PVE ZFS Tool guides.

Both the Admin Guide and the User Guide are built from a simple content list
(see CONTENT in each build_*.py) of tuples like:

    ("h1", "Titel")
    ("h2", "Untertitel")
    ("h3", "Unter-Untertitel")
    ("p", "Fließtext ...")
    ("bullets", ["Punkt 1", "Punkt 2", ...])
    ("numbered", ["Schritt 1", "Schritt 2", ...])
    ("cmd", "Aktion-Name", "Beschreibung", ["befehl 1", "befehl 2"])
    ("note", "Hinweistext")
    ("warn", "Warnungstext")
    ("table", ["Spalte1", "Spalte2"], [["a", "b"], ["c", "d"]], mono_cols, col_widths)
    ("space", points)
    ("pagebreak",)

This keeps the actual German content in the build_*.py files readable as
plain data instead of interleaved with layout code. See tools/docgen/README.md
for how to run the builders.
"""

import subprocess
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
    PageBreak, ListFlowable, ListItem, KeepTogether, NextPageTemplate,
)
from reportlab.platypus.tableofcontents import TableOfContents

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#0f2942")
BLUE = colors.HexColor("#2b6cb0")
LIGHT_BLUE_BG = colors.HexColor("#eaf2fb")
GRAY_TEXT = colors.HexColor("#3c4757")
LIGHT_GRAY = colors.HexColor("#7c8a9c")
CODE_BG = colors.HexColor("#f4f6f8")
CODE_BORDER = colors.HexColor("#d0d7de")
WARN_BG = colors.HexColor("#fdecea")
WARN_BORDER = colors.HexColor("#c0392b")
TABLE_HEAD_BG = NAVY
TABLE_ROW_ALT = colors.HexColor("#f7f9fb")
RULE = colors.HexColor("#c7d2de")

PAGE_W, PAGE_H = A4
MARGIN = 22 * mm

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "docs")


def repo_version():
    """Best-effort 'vX.Y.Z (branch)' string from the current git checkout, so
    the guides don't need a manually-maintained version constant. Falls back
    to a generic label if git isn't available (e.g. a plain source checkout)."""
    try:
        tag = subprocess.run(
            ["git", "-C", REPO_ROOT, "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", REPO_ROOT, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if tag:
            return f"{tag} ({branch})" if branch and branch != "HEAD" else tag
    except Exception:
        pass
    return "unbekannt (kein Git-Checkout gefunden)"


def _esc(s):
    """Escape a plain string for reportlab's mini-XML Paragraph markup."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_styles():
    ss = getSampleStyleSheet()
    styles = {}
    styles["TitleBig"] = ParagraphStyle(
        "TitleBig", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=28,
        leading=34, textColor=NAVY, spaceAfter=6, alignment=TA_LEFT,
    )
    styles["Subtitle"] = ParagraphStyle(
        "Subtitle", parent=ss["Normal"], fontName="Helvetica", fontSize=14,
        leading=20, textColor=BLUE, spaceAfter=4,
    )
    styles["MetaTitle"] = ParagraphStyle(
        "MetaTitle", parent=ss["Normal"], fontName="Helvetica", fontSize=10.5,
        leading=15, textColor=GRAY_TEXT,
    )
    styles["H1"] = ParagraphStyle(
        "H1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=18,
        leading=22, textColor=NAVY, spaceBefore=22, spaceAfter=10,
        borderWidth=0, borderPadding=0,
    )
    styles["H2"] = ParagraphStyle(
        "H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=13.5,
        leading=17, textColor=BLUE, spaceBefore=14, spaceAfter=6,
    )
    styles["H3"] = ParagraphStyle(
        "H3", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=11,
        leading=14, textColor=GRAY_TEXT, spaceBefore=10, spaceAfter=4,
    )
    styles["Body"] = ParagraphStyle(
        "Body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.7,
        leading=14, textColor=colors.HexColor("#1c2430"), spaceAfter=6,
        alignment=TA_LEFT,
    )
    styles["BodyBold"] = ParagraphStyle(
        "BodyBold", parent=styles["Body"], fontName="Helvetica-Bold",
    )
    styles["Bullet"] = ParagraphStyle(
        "Bullet", parent=styles["Body"], leftIndent=0, spaceAfter=3,
    )
    styles["Code"] = ParagraphStyle(
        "Code", parent=ss["Normal"], fontName="Courier", fontSize=8.3,
        leading=11.2, textColor=colors.HexColor("#0f2942"),
    )
    styles["CmdAction"] = ParagraphStyle(
        "CmdAction", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=13, textColor=NAVY, spaceAfter=2,
    )
    styles["CmdDesc"] = ParagraphStyle(
        "CmdDesc", parent=ss["Normal"], fontName="Helvetica-Oblique", fontSize=9,
        leading=12.5, textColor=GRAY_TEXT, spaceAfter=4,
    )
    styles["NoteText"] = ParagraphStyle(
        "NoteText", parent=styles["Body"], fontName="Helvetica", fontSize=9.2,
        leading=13, textColor=colors.HexColor("#1c2430"),
    )
    styles["TOCHeading"] = ParagraphStyle(
        "TOCHeading", parent=styles["H1"], spaceBefore=0,
    )
    styles["TOC1"] = ParagraphStyle(
        "TOC1", fontName="Helvetica-Bold", fontSize=11, leading=16,
        textColor=NAVY, spaceBefore=6,
    )
    styles["TOC2"] = ParagraphStyle(
        "TOC2", fontName="Helvetica", fontSize=9.7, leading=14,
        textColor=GRAY_TEXT, leftIndent=12,
    )
    styles["TableHead"] = ParagraphStyle(
        "TableHead", fontName="Helvetica-Bold", fontSize=8.6, leading=11,
        textColor=colors.white,
    )
    styles["TableCell"] = ParagraphStyle(
        "TableCell", fontName="Helvetica", fontSize=8.6, leading=11.5,
        textColor=colors.HexColor("#1c2430"),
    )
    styles["TableCellMono"] = ParagraphStyle(
        "TableCellMono", fontName="Courier", fontSize=7.8, leading=10.5,
        textColor=colors.HexColor("#0f2942"),
    )
    return styles


def code_box(styles, lines):
    """A shaded, bordered box of one-or-more monospace command lines."""
    if isinstance(lines, str):
        lines = [lines]
    paras = []
    for ln in lines:
        text = _esc(ln)
        paras.append(Paragraph(f"$ {text}" if not ln.startswith("#") else text,
                                styles["Code"]))
    tbl = Table([[p] for p in paras], colWidths=[PAGE_W - 2 * MARGIN - 4])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.6, CODE_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def note_box(styles, text, warn=False):
    bg = WARN_BG if warn else LIGHT_BLUE_BG
    border = WARN_BORDER if warn else BLUE
    label = "Achtung: " if warn else "Hinweis: "
    p = Paragraph(f"<b>{label}</b>{_esc(text)}", styles["NoteText"])
    tbl = Table([[p]], colWidths=[PAGE_W - 2 * MARGIN - 4])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def make_table(styles, header, rows, col_widths=None, mono_cols=None):
    mono_cols = mono_cols or set()
    head_row = [Paragraph(_esc(h), styles["TableHead"]) for h in header]
    data = [head_row]
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            st = styles["TableCellMono"] if i in mono_cols else styles["TableCell"]
            cells.append(Paragraph(_esc(cell), st))
        data.append(cells)
    n = len(header)
    if not col_widths:
        col_widths = [(PAGE_W - 2 * MARGIN) / n] * n
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ROW_ALT))
    tbl.setStyle(TableStyle(style))
    return tbl


def bullets(styles, items, style_name="Bullet"):
    return ListFlowable(
        [ListItem(Paragraph(_esc(t), styles[style_name]), leftIndent=6, spaceAfter=3)
         for t in items],
        bulletType="bullet", start="•", leftIndent=14, bulletFontSize=8,
        bulletColor=BLUE, spaceBefore=2, spaceAfter=8,
    )


def numbered(styles, items, style_name="Bullet"):
    return ListFlowable(
        [ListItem(Paragraph(_esc(t), styles[style_name])) for t in items],
        bulletType="1", leftIndent=16, bulletFontSize=9,
        bulletColor=BLUE, spaceBefore=2, spaceAfter=8,
    )


# ---------------------------------------------------------------------------
# Document class with real TOC (2-pass build), bookmarks, header/footer
# ---------------------------------------------------------------------------

class GuideDocTemplate(BaseDocTemplate):
    def __init__(self, filename, doc_title, **kw):
        self.doc_title = doc_title
        self.allowSplitting = 1
        BaseDocTemplate.__init__(self, filename, pagesize=A4,
                                  leftMargin=MARGIN, rightMargin=MARGIN,
                                  topMargin=26 * mm, bottomMargin=20 * mm, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                       id="normal")
        template = PageTemplate(id="normal", frames=[frame],
                                 onPage=self._header_footer)
        self.addPageTemplates([template])
        self._toc_entries = []

    def _header_footer(self, canv, doc):
        canv.saveState()
        canv.setFont("Helvetica", 8)
        canv.setFillColor(LIGHT_GRAY)
        canv.drawString(MARGIN, PAGE_H - 15 * mm, "PVE ZFS Tool — " + self.doc_title)
        canv.drawRightString(PAGE_W - MARGIN, PAGE_H - 15 * mm,
                              "onlinecrash24/pve-zfs-tool")
        canv.setStrokeColor(RULE)
        canv.setLineWidth(0.6)
        canv.line(MARGIN, PAGE_H - 17 * mm, PAGE_W - MARGIN, PAGE_H - 17 * mm)
        canv.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
        canv.drawCentredString(PAGE_W / 2, 10 * mm, f"Seite {doc.page}")
        canv.restoreState()

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        style = flowable.style.name
        text = flowable.getPlainText()
        if style == "H1":
            self.notify("TOCEntry", (0, text, self.page))
            key = f"h1-{self.page}-{len(self._toc_entries)}"
            self._toc_entries.append(key)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0, closed=False)
        elif style == "H2":
            self.notify("TOCEntry", (1, text, self.page))
            key = f"h2-{self.page}-{len(self._toc_entries)}"
            self._toc_entries.append(key)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=1, closed=True)


def build_toc(styles):
    toc = TableOfContents()
    toc.levelStyles = [styles["TOC1"], styles["TOC2"]]
    return toc


def render_content(styles, content):
    """Turn the plain-data CONTENT list into a list of flowables."""
    story = []
    for item in content:
        kind = item[0]
        if kind == "h1":
            story.append(Paragraph(_esc(item[1]), styles["H1"]))
        elif kind == "h2":
            story.append(Paragraph(_esc(item[1]), styles["H2"]))
        elif kind == "h3":
            story.append(Paragraph(_esc(item[1]), styles["H3"]))
        elif kind == "p":
            story.append(Paragraph(_esc(item[1]), styles["Body"]))
        elif kind == "bullets":
            story.append(bullets(styles, item[1]))
        elif kind == "numbered":
            story.append(numbered(styles, item[1]))
        elif kind == "cmd":
            _, action, desc, cmds = item
            block = [Paragraph(_esc(action), styles["CmdAction"])]
            if desc:
                block.append(Paragraph(_esc(desc), styles["CmdDesc"]))
            block.append(code_box(styles, cmds))
            block.append(Spacer(1, 7))
            story.append(KeepTogether(block))
        elif kind == "note":
            story.append(note_box(styles, item[1], warn=False))
            story.append(Spacer(1, 8))
        elif kind == "warn":
            story.append(note_box(styles, item[1], warn=True))
            story.append(Spacer(1, 8))
        elif kind == "table":
            _, header, rows = item[0], item[1], item[2]
            mono = set(item[3]) if len(item) > 3 else set()
            widths = item[4] if len(item) > 4 else None
            story.append(make_table(styles, header, rows, widths, mono))
            story.append(Spacer(1, 10))
        elif kind == "space":
            story.append(Spacer(1, item[1] if len(item) > 1 else 10))
        elif kind == "pagebreak":
            story.append(PageBreak())
        else:
            raise ValueError(f"unknown content kind: {kind}")
    return story


def build_guide(filename, title, subtitle, meta_lines, content):
    styles = make_styles()
    doc = GuideDocTemplate(filename, doc_title=title)

    story = []
    # Title page
    story.append(Spacer(1, 55 * mm))
    story.append(Paragraph(_esc(title), styles["TitleBig"]))
    story.append(Paragraph(_esc(subtitle), styles["Subtitle"]))
    story.append(Spacer(1, 14))
    for ln in meta_lines:
        story.append(Paragraph(_esc(ln), styles["MetaTitle"]))
    story.append(NextPageTemplate("normal"))
    story.append(PageBreak())

    # TOC page
    story.append(Paragraph("Inhaltsverzeichnis", styles["TOCHeading"]))
    story.append(Spacer(1, 8))
    story.append(build_toc(styles))
    story.append(PageBreak())

    # Body
    story.extend(render_content(styles, content))

    doc.multiBuild(story)
    return filename
