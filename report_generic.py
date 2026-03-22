"""
Generic report renderer — takes { "title": "...", "content": "...", "date": "...", "theme": {...} }
and produces a clean PDF.

Content is plain text with optional markdown-style formatting:
  **bold**, *italic*, # Heading, ## Subheading, | table | rows |, - bullet

Theme object (all optional, falls back to MET defaults):
  {
    "header_bg":    "#2D2D2D",   # title block background
    "header_text":  "#FFFFFF",   # title text color
    "h2_bg":        "#4A7FB5",   # ## heading background
    "h2_text":      "#FFFFFF",   # ## heading text
    "h3_color":     "#2D2D2D",   # # heading text color
    "accent":       "#00D4FF",   # border / HR color
    "body_font":    "Helvetica", # Helvetica | Times-Roman | Courier
    "body_color":   "#000000",   # body text color
    "page_bg":      null,        # page background color (null = white)
    "row_alt_bg":   "#F5F5F5",   # alternating table row color
    "table_hdr_bg": "#2D2D2D"    # table header background
  }
"""
import sys
import json
import re
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

PAGE_SIZE = letter
MARGIN    = 0.85 * inch

def hex_color(val, fallback):
    if val:
        try:
            return colors.HexColor(val)
        except Exception:
            pass
    return fallback

def build(data, output_path):
    title   = data.get('title', 'Report')
    content = data.get('content', '')
    date    = data.get('date', '')
    theme   = data.get('theme', {}) or {}

    # ── Resolve theme colors ───────────────────────────────────────────────
    HEADER_BG    = hex_color(theme.get('header_bg'),    colors.HexColor('#2D2D2D'))
    HEADER_TEXT  = hex_color(theme.get('header_text'),  colors.white)
    H2_BG        = hex_color(theme.get('h2_bg'),        colors.HexColor('#4A7FB5'))
    H2_TEXT      = hex_color(theme.get('h2_text'),      colors.white)
    H3_COLOR     = hex_color(theme.get('h3_color'),     colors.HexColor('#2D2D2D'))
    ACCENT       = hex_color(theme.get('accent'),       colors.HexColor('#00D4FF'))
    BODY_COLOR   = hex_color(theme.get('body_color'),   colors.black)
    ROW_ALT      = hex_color(theme.get('row_alt_bg'),   colors.HexColor('#F5F5F5'))
    TABLE_HDR_BG = hex_color(theme.get('table_hdr_bg'), colors.HexColor('#2D2D2D'))
    PAGE_BG      = hex_color(theme.get('page_bg'),      None)

    # ── Resolve fonts ──────────────────────────────────────────────────────
    raw_font = theme.get('body_font', 'Helvetica')
    FONT_MAP = {
        'helvetica':   ('Helvetica',        'Helvetica-Bold',   'Helvetica-Oblique'),
        'times':       ('Times-Roman',      'Times-Bold',       'Times-Italic'),
        'times-roman': ('Times-Roman',      'Times-Bold',       'Times-Italic'),
        'courier':     ('Courier',          'Courier-Bold',     'Courier-Oblique'),
    }
    font_normal, font_bold, font_italic = FONT_MAP.get(raw_font.lower(), FONT_MAP['helvetica'])

    # ── Paragraph styles ───────────────────────────────────────────────────
    h1     = ParagraphStyle('h1',     fontName=font_bold,   fontSize=14, textColor=HEADER_TEXT, spaceAfter=0)
    h2     = ParagraphStyle('h2',     fontName=font_bold,   fontSize=11, textColor=H2_TEXT,     spaceAfter=0)
    h3     = ParagraphStyle('h3',     fontName=font_bold,   fontSize=10, textColor=H3_COLOR,    spaceBefore=8, spaceAfter=4)
    body   = ParagraphStyle('body',   fontName=font_normal, fontSize=9,  textColor=BODY_COLOR,  leading=13, spaceAfter=4)
    bold   = ParagraphStyle('bold',   fontName=font_bold,   fontSize=9,  textColor=BODY_COLOR,  leading=13)
    th     = ParagraphStyle('th',     fontName=font_bold,   fontSize=8,  textColor=colors.white)
    td     = ParagraphStyle('td',     fontName=font_normal, fontSize=8,  textColor=BODY_COLOR,  leading=11)
    bullet = ParagraphStyle('bullet', fontName=font_normal, fontSize=9,  textColor=BODY_COLOR,  leading=13, leftIndent=12, spaceAfter=2, bulletIndent=0)

    # ── Document ───────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path,
        pagesize=PAGE_SIZE,
        rightMargin=MARGIN, leftMargin=MARGIN,
        topMargin=MARGIN,   bottomMargin=0.8 * inch,
    )

    W     = PAGE_SIZE[0] - 2 * MARGIN
    story = []

    def footer(canvas, doc):
        canvas.saveState()
        if PAGE_BG:
            canvas.setFillColor(PAGE_BG)
            canvas.rect(0, 0, PAGE_SIZE[0], PAGE_SIZE[1], fill=1, stroke=0)
        canvas.setFont(font_normal, 8)
        canvas.setFillColor(HEADER_BG)
        canvas.drawString(MARGIN, 0.4 * inch, 'Mountain Engineering and Testing, Inc.')
        canvas.drawCentredString(PAGE_SIZE[0] / 2, 0.4 * inch, title)
        canvas.drawRightString(PAGE_SIZE[0] - MARGIN, 0.4 * inch, f'Page {doc.page}')
        canvas.restoreState()

    def parse_inline(text):
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'\*(.+?)\*',     r'<i>\1</i>', text)
        return text

    # ── Title block ────────────────────────────────────────────────────────
    title_rows = [[Paragraph(title, h1)]]
    if date:
        sub = ParagraphStyle('sub', fontName=font_normal, fontSize=9, textColor=HEADER_TEXT)
        title_rows.append([Paragraph(date, sub)])
    title_tbl = Table(title_rows, colWidths=[W])
    title_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), HEADER_BG),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING',   (0, 0), (-1, -1), 10),
        ('BOX',           (0, 0), (-1, -1), 0.5, ACCENT),
    ]))
    story.append(title_tbl)
    story.append(Spacer(1, 10))

    # ── Parse content line by line ─────────────────────────────────────────
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith('## '):
            tbl = Table([[Paragraph(line[3:].strip(), h2)]], colWidths=[W])
            tbl.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), H2_BG),
                ('TOPPADDING',    (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 4))
            i += 1
            continue

        if line.startswith('# '):
            story.append(Paragraph(line[2:].strip(), h3))
            i += 1
            continue

        if line.strip() in ('---', '***', '___'):
            story.append(HRFlowable(width=W, thickness=0.5, color=ACCENT))
            story.append(Spacer(1, 4))
            i += 1
            continue

        if line.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].startswith('|'):
                table_lines.append(lines[i])
                i += 1
            rows = [l for l in table_lines if not re.match(r'^\|[\s\-|]+\|$', l)]
            if rows:
                parsed = []
                for ri, row in enumerate(rows):
                    cells = [c.strip() for c in row.strip('|').split('|')]
                    style = th if ri == 0 else td
                    parsed.append([Paragraph(parse_inline(c), style) for c in cells])
                col_count = max(len(r) for r in parsed)
                col_w = [W / col_count] * col_count
                tbl = Table(parsed, colWidths=col_w, repeatRows=1)
                ts = [
                    ('BACKGROUND',    (0, 0), (-1, 0),  TABLE_HDR_BG),
                    ('BOX',           (0, 0), (-1, -1), 0.5, ACCENT),
                    ('INNERGRID',     (0, 0), (-1, -1), 0.25, colors.grey),
                    ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
                    ('TOPPADDING',    (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ('LEFTPADDING',   (0, 0), (-1, -1), 4),
                ]
                for ri in range(1, len(parsed)):
                    bg = ROW_ALT if ri % 2 == 0 else colors.white
                    ts.append(('BACKGROUND', (0, ri), (-1, ri), bg))
                tbl.setStyle(TableStyle(ts))
                story.append(tbl)
                story.append(Spacer(1, 6))
            continue

        if line.startswith('- ') or line.startswith('* '):
            story.append(Paragraph(f'• {parse_inline(line[2:].strip())}', bullet))
            i += 1
            continue

        m = re.match(r'^(\d+)\.\s+(.+)', line)
        if m:
            story.append(Paragraph(f'{m.group(1)}. {parse_inline(m.group(2))}', bullet))
            i += 1
            continue

        if line.strip().startswith('**') and line.strip().endswith('**') and len(line.strip()) > 4:
            story.append(Paragraph(parse_inline(line.strip()), bold))
            i += 1
            continue

        if line.strip() == '':
            story.append(Spacer(1, 4))
            i += 1
            continue

        story.append(Paragraph(parse_inline(line.strip()), body))
        i += 1

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == '__main__':
    data = json.load(open(sys.argv[1]))
    build(data, sys.argv[2])
