"""PDF generation for AI reports using fpdf2 with UTF-8 support."""

import re
import os
from fpdf import FPDF

# Directory for font files (bundled DejaVu for full UTF-8)
FONT_DIR = os.path.join(os.path.dirname(__file__), "static", "fonts")


def _has_dejavu():
    """Check if DejaVu fonts are available."""
    return os.path.exists(os.path.join(FONT_DIR, "DejaVuSans.ttf"))


class ReportPDF(FPDF):
    """PDF with header/footer for ZFS reports."""

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
        self.set_font(self._fn, style, size)

    def header(self):
        self._f("B", 14)
        self.cell(0, 10, self._s("PVE ZFS Tool - AI Report"), new_x="LMARGIN", new_y="NEXT", align="C")
        if self.report_meta:
            self._f("", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 5, self._s(self.report_meta), new_x="LMARGIN", new_y="NEXT", align="C")
            self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self._f("I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

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
    meta = f"{timestamp} | {provider} ({model}) | Hosts: {host_str}"

    use_unicode = _has_dejavu()
    pdf = ReportPDF(report_meta=meta, use_unicode=use_unicode)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    _render_markdown(pdf, content)

    return pdf.output()


def _render_markdown(pdf, content):
    """Render markdown content into the PDF using multi_cell for safe wrapping."""
    lines = content.split("\n")
    i = 0
    in_code = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Code block
        if stripped.startswith("```"):
            in_code = not in_code
            i += 1
            continue

        if in_code:
            pdf.set_font(pdf._fn_mono, "", 8)
            pdf.set_fill_color(235, 235, 235)
            pdf.multi_cell(0, 4.5, pdf._s(line), fill=True)
            i += 1
            continue

        # Empty line
        if not stripped:
            pdf.ln(3)
            i += 1
            continue

        # Table separator (|---|---|)
        if stripped.startswith("|") and re.match(r'^\|[\s\-:]+(\|[\s\-:]+)+\|$', stripped):
            i += 1
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            n_cols = len(cells)
            if n_cols > 0:
                page_w = pdf.w - pdf.l_margin - pdf.r_margin
                col_w = page_w / n_cols
                # Detect if this is a header (check if next line is separator)
                is_header = False
                if i + 1 < len(lines):
                    next_s = lines[i + 1].strip()
                    if next_s.startswith("|") and re.match(r'^\|[\s\-:]+(\|[\s\-:]+)+\|$', next_s):
                        is_header = True
                pdf._f("B" if is_header else "", 8)
                for cell_text in cells:
                    safe_text = pdf._s(cell_text)
                    # Truncate if too long for cell
                    pdf.cell(col_w, 5.5, safe_text[:int(col_w / 2)], border=1)
                pdf.ln()
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            pdf.ln(2)
            y = pdf.get_y()
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(4)
            i += 1
            continue

        # Headers
        hdr_match = re.match(r'^(#{1,4})\s+(.+)', stripped)
        if hdr_match:
            level = len(hdr_match.group(1))
            sizes = {1: 13, 2: 12, 3: 11, 4: 10}
            pdf.ln(2)
            pdf._f("B", sizes.get(level, 10))
            pdf.multi_cell(0, 6, pdf._s(hdr_match.group(2)))
            pdf.ln(2)
            i += 1
            continue

        # Bullet list
        if stripped.startswith("- ") or stripped.startswith("* "):
            pdf._f("", 9)
            text = stripped[2:]
            pdf.cell(8, 5, pdf._s("  -"))
            x = pdf.get_x()
            w = pdf.w - x - pdf.r_margin
            pdf.multi_cell(w, 5, pdf._s(_strip_md(text)))
            i += 1
            continue

        # Numbered list
        m = re.match(r'^(\d+)\.\s+(.+)', stripped)
        if m:
            pdf._f("", 9)
            num = m.group(1)
            text = m.group(2)
            pdf.cell(10, 5, pdf._s(f"  {num}."))
            x = pdf.get_x()
            w = pdf.w - x - pdf.r_margin
            pdf.multi_cell(w, 5, pdf._s(_strip_md(text)))
            i += 1
            continue

        # Normal paragraph – use multi_cell for automatic wrapping
        pdf._f("", 9)
        pdf.multi_cell(0, 5, pdf._s(_strip_md(stripped)))
        pdf.ln(1)
        i += 1


def _strip_md(text):
    """Remove markdown formatting for plain text output."""
    # Remove bold **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Remove italic *text*
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Remove inline code `text`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text
