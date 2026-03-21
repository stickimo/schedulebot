"""
Generic report renderer — takes { "title": "...", "content": "..." } and produces a clean PDF.
Content is plain text with optional markdown-style formatting:
  **bold**, # Heading, ## Subheading, | table | rows |, - bullet
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

CHARCOAL = colors.HexColor('#2D2D2D')
BLUE     = colors.HexColor('#4A7FB5')
NEON     = colors.HexColor('#00D4FF')
LGRAY    = colors.HexColor('#E0E0E0')
WHITE    = colors.white
# Aliases for backward compat within this file
NAVY     = CHARCOAL
TEAL     = BLUE
LIGHT_GRAY = colors.HexColor('#F5F5F5')

PAGE_SIZE = letter
MARGIN    = 0.85 * inch

def footer(canvas, doc, title):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(NAVY)
    canvas.drawString(MARGIN, 0.4 * inch, 'Mountain Engineering and Testing, Inc.')
    canvas.drawCentredString(PAGE_SIZE[0] / 2, 0.4 * inch, title)
    canvas.drawRightString(PAGE_SIZE[0] - MARGIN, 0.4 * inch, f'Page {doc.page}')
    canvas.restoreState()

def parse_inline(text):
    """Convert **bold** and *italic* to ReportLab XML tags."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*',     r'<i>\1</i>', text)
    return text

def build(data, output_path):
    title   = data.get('title', 'Report')
    content = data.get('content', '')
    date    = data.get('date', '')

    h1    = ParagraphStyle('h1',    fontName='Helvetica-Bold',  fontSize=14, textColor=WHITE,  spaceAfter=0)
    h2    = ParagraphStyle('h2',    fontName='Helvetica-Bold',  fontSize=11, textColor=WHITE,  spaceAfter=0)
    h3    = ParagraphStyle('h3',    fontName='Helvetica-Bold',  fontSize=10, textColor=NAVY,   spaceBefore=8, spaceAfter=4)
    body  = ParagraphStyle('body',  fontName='Helvetica',       fontSize=9,  leading=13,       spaceAfter=4)
    bold  = ParagraphStyle('bold',  fontName='Helvetica-Bold',  fontSize=9,  leading=13)
    th    = ParagraphStyle('th',    fontName='Helvetica-Bold',  fontSize=8,  textColor=WHITE)
    td    = ParagraphStyle('td',    fontName='Helvetica',       fontSize=8,  leading=11)
    bullet= ParagraphStyle('bullet',fontName='Helvetica',       fontSize=9,  leading=13, leftIndent=12, spaceAfter=2, bulletIndent=0)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=PAGE_SIZE,
        rightMargin=MARGIN, leftMargin=MARGIN,
        topMargin=MARGIN,   bottomMargin=0.8 * inch,
    )

    W = PAGE_SIZE[0] - 2 * MARGIN
    story = []

    # ── Title block ───────────────────────────────────────────────────────────
    title_rows = [[Paragraph(title, h1)]]
    if date:
        sub = ParagraphStyle('sub', fontName='Helvetica', fontSize=9, textColor=WHITE)
        title_rows.append([Paragraph(date, sub)])
    title_tbl = Table(title_rows, colWidths=[W])
    title_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), CHARCOAL),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING',   (0, 0), (-1, -1), 10),
        ('BOX',           (0, 0), (-1, -1), 0.5, NEON),
    ]))
    story.append(title_tbl)
    story.append(Spacer(1, 10))

    # ── Parse content line by line ────────────────────────────────────────────
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # H1/H2 headings
        if line.startswith('## '):
            tbl = Table([[Paragraph(line[3:].strip(), h2)]], colWidths=[W])
            tbl.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), TEAL),
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

        # Horizontal rule
        if line.strip() in ('---', '***', '___'):
            story.append(HRFlowable(width=W, thickness=0.5, color=colors.grey))
            story.append(Spacer(1, 4))
            i += 1
            continue

        # Table (lines starting with |)
        if line.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].startswith('|'):
                table_lines.append(lines[i])
                i += 1
            # Filter out separator rows (|---|---|)
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
                    ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
                    ('BOX',           (0, 0), (-1, -1), 0.5, colors.grey),
                    ('INNERGRID',     (0, 0), (-1, -1), 0.25, colors.grey),
                    ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
                    ('TOPPADDING',    (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ('LEFTPADDING',   (0, 0), (-1, -1), 4),
                ]
                for ri in range(1, len(parsed)):
                    bg = LIGHT_GRAY if ri % 2 == 0 else WHITE
                    ts.append(('BACKGROUND', (0, ri), (-1, ri), bg))
                tbl.setStyle(TableStyle(ts))
                story.append(tbl)
                story.append(Spacer(1, 6))
            continue

        # Bullet points
        if line.startswith('- ') or line.startswith('* '):
            story.append(Paragraph(f'• {parse_inline(line[2:].strip())}', bullet))
            i += 1
            continue

        # Numbered list
        m = re.match(r'^(\d+)\.\s+(.+)', line)
        if m:
            story.append(Paragraph(f'{m.group(1)}. {parse_inline(m.group(2))}', bullet))
            i += 1
            continue

        # Bold-only line (acts as a label/subheader)
        if line.strip().startswith('**') and line.strip().endswith('**') and len(line.strip()) > 4:
            story.append(Paragraph(parse_inline(line.strip()), bold))
            i += 1
            continue

        # Empty line → small spacer
        if line.strip() == '':
            story.append(Spacer(1, 4))
            i += 1
            continue

        # Normal paragraph
        story.append(Paragraph(parse_inline(line.strip()), body))
        i += 1

    doc.build(story,
              onFirstPage=lambda c, d: footer(c, d, title),
              onLaterPages=lambda c, d: footer(c, d, title))


if __name__ == '__main__':
    data = json.load(open(sys.argv[1]))
    build(data, sys.argv[2])
