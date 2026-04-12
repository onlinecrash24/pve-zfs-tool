"""PDF generation for AI reports using fpdf2 with UTF-8 support and professional styling."""

import re
import os
from fpdf import FPDF

# Directory for font files (bundled DejaVu for full UTF-8)
FONT_DIR = os.path.join(os.path.dirname(__file__), "static", "fonts")
IMG_DIR = os.path.join(os.path.dirname(__file__), "static", "img")

# Color scheme (adapted for print on white background)
COLORS = {
    "accent": (26, 115, 167),       # #1a73a7 — headers, primary accent
    "accent_light": (230, 243, 250), # #e6f3fa — light accent background
    "success": (46, 164, 79),        # #2ea44f — OK status
    "warning": (210, 153, 34),       # #d29922 — warnings
    "danger": (207, 34, 46),         # #cf222e — critical
    "text": (36, 41, 47),            # #24292f — body text
    "text_light": (110, 119, 129),   # #6e7781 — secondary text
    "table_header": (26, 115, 167),  # #1a73a7 — table header bg
    "table_alt": (246, 248, 250),    # #f6f8fa — alternating rows
    "table_border": (208, 215, 222), # #d0d7de — table borders
    "rule": (208, 215, 222),         # #d0d7de — horizontal rules
}


def _has_dejavu():
    """Check if DejaVu fonts are available."""
    return os.path.exists(os.path.join(FONT_DIR, "DejaVuSans.ttf"))


def _has_logo():
    """Check if logo-small.png exists."""
    return os.path.exists(os.path.join(IMG_DIR, "logo-small.png"))


class ReportPDF(FPDF):
    """PDF with styled header/footer for ZFS reports."""

    def __init__(self, report_meta="", use_unicode=False):
        super().__init__()
        self.report_meta = report_meta
        self.use_unicode = use_unicode
        if use_unicode:
            self.add_font("DejaVu", "", os.path.join(FONT_DIR, "DejaVuSans.ttf"), uni=True)
            self.add_font("DejaVu", "B", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"), uni=True)
            self.add_font("DejaVu", "I", os.path.join(FONT_DIR, "DejaVuSans-Oblique.ttf"), uni=True)
            self.add_font("DejaVuMono", "", os.path.join(FONT_DIR, "DejaVuSansMono.ttf"), uni=True)
            self._fn = "DejaVu"
            self._fn_mono = "DejaVuMono"
        else:
            self._fn = "Helvetica"
            self._fn_mono = "Courier"

    def _f(self, style="", size=10):
        # Only allow valid fpdf2 font styles (B/I/U/S and combinations)
        if style and not all(c in "BIUS" for c in style.upper()):
            style = ""
        self.set_font(self._fn, style, size)

    def header(self):
        # --- Logo + Title row ---
        y_start = self.get_y()
        if _has_logo():
            try:
                self.image(os.path.join(IMG_DIR, "logo-small.png"), x=self.l_margin, y=y_start, h=12)
            except Exception:
                pass

        # Title — right-aligned or centered
        self._f("B", 16)
        self.set_text_color(*COLORS["accent"])
        title_x = self.l_margin + 45 if _has_logo() else self.l_margin
        self.set_xy(title_x, y_start)
        self.cell(self.w - title_x - self.r_margin, 7, self._s("AI Report"), align="L" if _has_logo() else "C")

        # Meta line
        if self.report_meta:
            self._f("", 7.5)
            self.set_text_color(*COLORS["text_light"])
            self.set_xy(title_x, y_start + 7)
            self.cell(self.w - title_x - self.r_margin, 5, self._s(self.report_meta), align="L" if _has_logo() else "C")

        # Accent line under header
        line_y = y_start + 15
        self.set_draw_color(*COLORS["accent"])
        self.set_line_width(0.6)
        self.line(self.l_margin, line_y, self.w - self.r_margin, line_y)
        self.set_line_width(0.2)  # Reset
        self.set_y(line_y + 4)
        self.set_text_color(*COLORS["text"])

    def footer(self):
        self.set_y(-15)
        self._f("", 7)
        self.set_text_color(*COLORS["text_light"])
        self.set_draw_color(*COLORS["rule"])
        line_y = self.get_y() - 2
        self.line(self.l_margin, line_y, self.w - self.r_margin, line_y)
        self.cell(0, 10, self._s(f"PVE ZFS Tool  |  Page {self.page_no()}/{{nb}}"), align="C")

    def _s(self, text):
        """Make text safe for the current font encoding."""
        if not text:
            return ""
        if self.use_unicode:
            return text
        return _latin1_safe(text)


def _latin1_safe(text):
    """Convert text to latin-1 safe string."""
    if not text:
        return ""
    r = {
        "\u2705": "[OK]", "\u26a0\ufe0f": "[!]", "\u26a0": "[!]",
        "\u274c": "[X]", "\u2022": "-", "\u2013": "-", "\u2014": "--",
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2026": "...", "\u2192": "->", "\u2248": "~",
        "\u2264": "<=", "\u2265": ">=", "\ufe0f": "", "\u200b": "",
        "\u2139\ufe0f": "[i]", "\u2139": "[i]",
    }
    for c, v in r.items():
        text = text.replace(c, v)
    text = re.sub(r'[\U0001F000-\U0001FFFF]', '', text)
    text = re.sub(r'[\U00002600-\U000027BF]', '', text)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def generate_pdf(report):
    """Generate a PDF from a report dict and return bytes."""
    timestamp = report.get("timestamp", "")
    provider = report.get("provider", "")
    model = report.get("model", "")
    host_names = report.get("host_names", [])
    content = report.get("content", "")

    host_str = ", ".join(host_names) if host_names else "?"
    meta = f"{timestamp}  |  {provider} ({model})  |  Hosts: {host_str}"

    use_unicode = _has_dejavu()
    pdf = ReportPDF(report_meta=meta, use_unicode=use_unicode)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    _render_markdown(pdf, content)

    return pdf.output()


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _render_markdown(pdf, content):
    """Render markdown content into the PDF with rich formatting."""
    lines = content.split("\n")
    i = 0
    in_code = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Code block toggle
        if stripped.startswith("```"):
            in_code = not in_code
            if in_code:
                pdf.ln(2)
            else:
                pdf.ln(2)
            i += 1
            continue

        # Code block content
        if in_code:
            pdf.set_font(pdf._fn_mono, "", 7.5)
            pdf.set_fill_color(245, 245, 245)
            pdf.set_text_color(50, 50, 50)
            pdf.set_x(pdf.l_margin + 2)
            w = pdf.w - pdf.l_margin - pdf.r_margin - 4
            pdf.multi_cell(w, 4, pdf._s(line), fill=True)
            pdf.set_text_color(*COLORS["text"])
            i += 1
            continue

        # Empty line
        if not stripped:
            pdf.ln(2)
            i += 1
            continue

        # Table separator (|---|---|)
        if stripped.startswith("|") and re.match(r'^\|[\s\-:]+(\|[\s\-:]+)+\|$', stripped):
            i += 1
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            # Collect all consecutive table rows
            table_rows = []
            while i < len(lines):
                row_s = lines[i].strip()
                if not (row_s.startswith("|") and row_s.endswith("|")):
                    break
                # Skip separator rows
                if re.match(r'^\|[\s\-:]+(\|[\s\-:]+)+\|$', row_s):
                    i += 1
                    continue
                cells = [c.strip() for c in row_s.split("|")[1:-1]]
                table_rows.append(cells)
                i += 1

            if table_rows:
                _render_table(pdf, table_rows)
                pdf.ln(2)
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            pdf.ln(3)
            y = pdf.get_y()
            pdf.set_draw_color(*COLORS["rule"])
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(4)
            i += 1
            continue

        # Headers (## Section)
        hdr_match = re.match(r'^(#{1,4})\s+(.+)', stripped)
        if hdr_match:
            level = len(hdr_match.group(1))
            text = _strip_md(hdr_match.group(2))
            _render_heading(pdf, text, level)
            i += 1
            continue

        # Bullet list
        if stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:]
            _render_bullet(pdf, text, indent=0)
            i += 1
            continue

        # Indented bullet (sub-item)
        indent_bullet = re.match(r'^(\s{2,})[*-]\s+(.+)', line)
        if indent_bullet:
            text = indent_bullet.group(2)
            _render_bullet(pdf, text, indent=1)
            i += 1
            continue

        # Numbered list
        m = re.match(r'^(\d+)\.\s+(.+)', stripped)
        if m:
            num = m.group(1)
            text = m.group(2)
            _render_numbered(pdf, num, text)
            i += 1
            continue

        # Normal paragraph — render with inline bold/italic
        pdf.set_x(pdf.l_margin)
        _render_rich_text(pdf, stripped, size=9)
        pdf.ln(1.5)
        i += 1


# ---------------------------------------------------------------------------
# Rich text rendering (inline bold, italic, code)
# ---------------------------------------------------------------------------

def _render_rich_text(pdf, text, size=9, line_height=5):
    """Render text with inline **bold**, *italic*, and `code` formatting."""
    # Split text into segments with formatting
    segments = _parse_inline(text)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    # Check if the full text fits on one line; if not, fall back to multi_cell
    total_w = 0
    for seg_text, seg_style in segments:
        if seg_style == "code":
            pdf.set_font(pdf._fn_mono, "", size - 1)
        else:
            pdf._f(seg_style, size)
        total_w += pdf.get_string_width(pdf._s(seg_text))

    if total_w > page_w - (pdf.get_x() - pdf.l_margin):
        # Multi-line: use multi_cell with stripped markdown
        plain = _strip_md(text)
        # Detect if it starts with bold for the paragraph
        if text.startswith("**") and "**" in text[2:]:
            bold_end = text.index("**", 2)
            bold_part = text[2:bold_end]
            rest_part = _strip_md(text[bold_end + 2:])
            pdf._f("B", size)
            pdf.write(line_height, pdf._s(bold_part))
            pdf._f("", size)
            if rest_part:
                pdf.write(line_height, pdf._s(rest_part))
            pdf.ln(line_height)
        else:
            pdf._f("", size)
            pdf.multi_cell(0, line_height, pdf._s(plain))
        return

    # Single line: render each segment
    for seg_text, seg_style in segments:
        if seg_style == "code":
            pdf.set_font(pdf._fn_mono, "", size - 1)
            pdf.set_fill_color(240, 240, 240)
            pdf.cell(pdf.get_string_width(pdf._s(seg_text)) + 2, line_height,
                     pdf._s(seg_text), fill=True)
            pdf._f("", size)
        else:
            pdf._f(seg_style, size)
            pdf.write(line_height, pdf._s(seg_text))

    pdf.ln(line_height)
    pdf._f("", size)


def _parse_inline(text):
    """Parse inline markdown into (text, style) segments."""
    segments = []
    pattern = re.compile(r'(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`([^`]+)`)')
    last_end = 0

    for m in pattern.finditer(text):
        # Add text before this match
        if m.start() > last_end:
            segments.append((text[last_end:m.start()], ""))
        if m.group(2):       # **bold**
            segments.append((m.group(2), "B"))
        elif m.group(4):     # *italic*
            segments.append((m.group(4), "I"))
        elif m.group(6):     # `code`
            segments.append((m.group(6), "code"))
        last_end = m.end()

    # Remaining text
    if last_end < len(text):
        segments.append((text[last_end:], ""))

    return segments if segments else [(text, "")]


# ---------------------------------------------------------------------------
# Section headings
# ---------------------------------------------------------------------------

def _render_heading(pdf, text, level):
    """Render a section heading with colored accent."""
    sizes = {1: 14, 2: 12, 3: 11, 4: 10}
    font_size = sizes.get(level, 10)

    if level <= 2:
        pdf.ln(5)
        # Colored accent bar + heading
        pdf.set_fill_color(*COLORS["accent_light"])
        pdf.set_text_color(*COLORS["accent"])
        pdf._f("B", font_size)
        pdf.set_x(pdf.l_margin)
        # Draw accent bar on left
        y = pdf.get_y()
        pdf.set_fill_color(*COLORS["accent"])
        pdf.rect(pdf.l_margin, y, 2.5, font_size * 0.55, "F")
        pdf.set_x(pdf.l_margin + 5)
        pdf.cell(0, font_size * 0.55, pdf._s(text))
        pdf.ln(font_size * 0.55)
        # Thin line under heading
        pdf.set_draw_color(*COLORS["accent"])
        pdf.set_line_width(0.15)
        pdf.line(pdf.l_margin, pdf.get_y() + 0.5, pdf.w - pdf.r_margin, pdf.get_y() + 0.5)
        pdf.set_line_width(0.2)
        pdf.ln(3)
    else:
        pdf.ln(3)
        pdf.set_text_color(*COLORS["text"])
        pdf._f("B", font_size)
        pdf.multi_cell(0, 5.5, pdf._s(text))
        pdf.ln(1.5)

    pdf.set_text_color(*COLORS["text"])


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

def _render_bullet(pdf, text, indent=0):
    """Render a bullet point with optional indentation."""
    pdf._f("", 9)
    x_offset = pdf.l_margin + (indent * 8) + 4

    # Bullet dot
    pdf.set_x(x_offset)
    y_bullet = pdf.get_y() + 2
    pdf.set_fill_color(*COLORS["accent"])
    pdf.ellipse(x_offset, y_bullet, 1.5, 1.5, "F")

    # Text
    pdf.set_x(x_offset + 4)
    w = max(10, pdf.w - pdf.get_x() - pdf.r_margin)
    _render_rich_text(pdf, text, size=9)


def _render_numbered(pdf, num, text):
    """Render a numbered list item."""
    # Number badge
    pdf._f("B", 8.5)
    pdf.set_x(pdf.l_margin + 4)
    pdf.set_fill_color(*COLORS["accent"])
    pdf.set_text_color(255, 255, 255)
    badge_w = max(6, pdf.get_string_width(num) + 4)
    pdf.cell(badge_w, 5, pdf._s(num), fill=True, align="C")
    pdf.set_text_color(*COLORS["text"])

    # Text
    pdf.set_x(pdf.l_margin + 4 + badge_w + 2)
    w = max(10, pdf.w - pdf.get_x() - pdf.r_margin)
    _render_rich_text(pdf, text, size=9)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def _render_table(pdf, rows):
    """Render a table with colored header and alternating rows."""
    if not rows:
        return

    n_cols = max(len(r) for r in rows)
    if n_cols == 0:
        return

    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    # Calculate column widths based on content
    col_widths = _calc_col_widths(pdf, rows, n_cols, page_w)

    row_h = 6

    # Header row (first row)
    pdf.set_x(pdf.l_margin)
    pdf._f("B", 8)
    pdf.set_fill_color(*COLORS["table_header"])
    pdf.set_text_color(255, 255, 255)
    pdf.set_draw_color(*COLORS["table_border"])

    for j in range(n_cols):
        text = rows[0][j] if j < len(rows[0]) else ""
        text = _strip_md(text)
        pdf.cell(col_widths[j], row_h, pdf._s(text), border=1, fill=True, align="C")
    pdf.ln()

    # Data rows
    pdf._f("", 8)
    pdf.set_text_color(*COLORS["text"])
    for idx, row in enumerate(rows[1:]):
        # Alternating row background
        if idx % 2 == 0:
            pdf.set_fill_color(*COLORS["table_alt"])
        else:
            pdf.set_fill_color(255, 255, 255)

        pdf.set_x(pdf.l_margin)
        for j in range(n_cols):
            text = row[j] if j < len(row) else ""
            text = _strip_md(text)

            # Color-code status cells
            original_color = COLORS["text"]
            if "[OK]" in text or "PASSED" in text:
                pdf.set_text_color(*COLORS["success"])
            elif "[X]" in text or "CRITICAL" in text.upper() or "KRITISCH" in text.upper():
                pdf.set_text_color(*COLORS["danger"])
            elif "[!]" in text or "WARNING" in text.upper() or "WARNUNG" in text.upper():
                pdf.set_text_color(*COLORS["warning"])

            pdf.cell(col_widths[j], row_h, pdf._s(text), border=1, fill=True)
            pdf.set_text_color(*original_color)
        pdf.ln()


def _calc_col_widths(pdf, rows, n_cols, page_w):
    """Calculate column widths proportionally based on content."""
    pdf._f("", 8)
    max_widths = [0] * n_cols
    for row in rows:
        for j in range(min(len(row), n_cols)):
            text = pdf._s(_strip_md(row[j]))
            w = pdf.get_string_width(text) + 6  # padding
            max_widths[j] = max(max_widths[j], w)

    # Ensure minimum width
    for j in range(n_cols):
        max_widths[j] = max(max_widths[j], 15)

    total = sum(max_widths)
    if total <= page_w:
        # Distribute remaining space proportionally
        extra = page_w - total
        for j in range(n_cols):
            max_widths[j] += extra / n_cols
    else:
        # Scale down to fit
        factor = page_w / total
        max_widths = [w * factor for w in max_widths]

    return max_widths


def _strip_md(text):
    """Remove markdown formatting for plain text output."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text
