from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image as PILImage, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, Image, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

from .branding import active_profile, normalize_quote_signer
from .document_texts import fill_template
from .quotation_i18n import labels
from .quotation_models import QuoteData
from .quote_theme import QuotePalette, quote_palette


MUTED = HexColor("#657887")
LINE = HexColor("#B8C8D3")


def money(value: float, currency="MXN") -> str:
    return f"$ {value:,.2f}"


def _display_date(value: str, language: str) -> str:
    if language != "en":
        return value
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y")
    except ValueError:
        return value
    months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    return f"{months[parsed.month - 1]} {parsed.day}, {parsed.year}"


def _styles(palette: QuotePalette):
    base = getSampleStyleSheet()
    return {
        "normal": ParagraphStyle("QuoteNormal", parent=base["Normal"], fontName="Helvetica", fontSize=8.2, leading=11, textColor=palette.ink, spaceAfter=0),
        "small": ParagraphStyle("QuoteSmall", parent=base["Normal"], fontName="Helvetica", fontSize=7.1, leading=9, textColor=MUTED),
        "label": ParagraphStyle("QuoteLabel", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.2, leading=9, textColor=palette.accent),
        "concept": ParagraphStyle("QuoteConcept", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=palette.accent),
        "bullet": ParagraphStyle("QuoteBullet", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10.5, leftIndent=8, firstLineIndent=-5, textColor=palette.ink),
        "center": ParagraphStyle("QuoteCenter", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10, alignment=TA_CENTER, textColor=palette.ink),
        "right": ParagraphStyle("QuoteRight", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10, alignment=TA_RIGHT, textColor=palette.ink),
        "image": ParagraphStyle("ImageCaption", parent=base["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, alignment=TA_CENTER, textColor=MUTED),
        "bar_label": ParagraphStyle("BarLabel", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.2, leading=9, textColor=palette.bar_text, alignment=TA_CENTER),
        "bar_total": ParagraphStyle("BarTotal", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.2, leading=10, textColor=palette.bar_text, alignment=TA_RIGHT),
    }


class QuoteDocTemplate(BaseDocTemplate):
    def __init__(self, filename, quote: QuoteData, logo: Path | None, palette: QuotePalette):
        self.quote = quote
        self.logo = logo
        self.palette = palette
        self.texts = labels(quote.language)
        self.company = active_profile()
        super().__init__(filename, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=39 * mm, bottomMargin=20 * mm, title=f"{self.texts['document']} {quote.quote_number}", author=self.company.name)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id="quote", frames=[frame], onPage=self._page))

    def _page(self, canvas, doc):
        w, h = A4
        canvas.saveState()
        if self.logo and self.logo.exists():
            try:
                canvas.drawImage(str(self.logo), 15 * mm, h - 23 * mm, 27 * mm, 17 * mm, preserveAspectRatio=True, mask="auto", anchor="c")
            except Exception:
                pass
        canvas.setFillColor(self.palette.accent)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawCentredString(w / 2, h - 11.5 * mm, self.company.heading_for(self.quote.language))
        canvas.setFont("Helvetica", 6.6)
        canvas.setFillColor(MUTED)
        if self.company.address:
            canvas.drawCentredString(w / 2, h - 16.5 * mm, self.company.address)
            canvas.drawCentredString(w / 2, h - 21.5 * mm, self.company.contact_line_for(self.quote.language))
        else:
            canvas.drawCentredString(w / 2, h - 16.5 * mm, self.company.description_for(self.quote.language))
            canvas.drawCentredString(w / 2, h - 21.5 * mm, self.company.contact_line_for(self.quote.language))
        canvas.setFillColor(self.palette.primary)
        canvas.rect(15 * mm, h - 34 * mm, w - 30 * mm, 7 * mm, fill=1, stroke=0)
        canvas.setFillColor(self.palette.bar_text)
        canvas.setFont("Helvetica-Bold", 9)
        folio = self.quote.quote_number or self.texts["no_number"]
        canvas.drawCentredString(w / 2, h - 31.5 * mm, f"{self.texts['document'].upper()}: {folio}")
        canvas.setStrokeColor(self.palette.primary)
        canvas.setLineWidth(.5)
        canvas.line(15 * mm, 14 * mm, w - 15 * mm, 14 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.8)
        canvas.drawString(15 * mm, 9 * mm, self.company.footer_for(self.quote.language))
        canvas.drawRightString(w - 15 * mm, 9 * mm, f"{self.texts['page']} {doc.page}")
        canvas.restoreState()


def _client_table(q: QuoteData, s, palette: QuotePalette, t):
    data = [
        [Paragraph(t["date"], s["label"]), Paragraph(_display_date(q.quote_date, q.language), s["normal"]), Paragraph(t["name_company"], s["label"]), Paragraph(q.client_name or "-", s["normal"])],
        [Paragraph(t["contact"], s["label"]), Paragraph(q.contact or "-", s["normal"]), Paragraph(t["email"], s["label"]), Paragraph(q.email or "-", s["normal"])],
        [Paragraph(t["phone"], s["label"]), Paragraph(q.phone or "-", s["normal"]), Paragraph(t["location"], s["label"]), Paragraph(q.location or "-", s["normal"])],
    ]
    table = Table(data, colWidths=[27 * mm, 55 * mm, 32 * mm, 66 * mm], rowHeights=[9 * mm] * 3)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), palette.pale), ("BACKGROUND", (2, 0), (2, -1), palette.pale),
        ("GRID", (0, 0), (-1, -1), .35, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _concept_table(q: QuoteData, s, palette: QuotePalette, t):
    widths = [111 * mm, 18 * mm, 17 * mm, 24 * mm, 26 * mm]
    rows = [[Paragraph(t["concept"], s["bar_label"]), Paragraph(t["unit"], s["bar_label"]), Paragraph(t["quantity"], s["bar_label"]), Paragraph(t["unit_price"], s["bar_label"]), Paragraph(t["amount"], s["bar_label"])]]
    title_rows = []
    title_content_pairs = []
    value_spans = []
    for number, item in enumerate(q.items, 1):
        clean_subitems = [child.strip() for child in item.subitems if child.strip()]
        # Row spans cannot cross a PDF page. Very long concepts are divided into
        # manageable continuation blocks so every visible block keeps its four
        # value columns merged and vertically centered.
        chunks = [clean_subitems[:8]]
        remaining = clean_subitems[8:]
        while remaining:
            chunks.append(remaining[:10])
            remaining = remaining[10:]
        for chunk_index, chunk in enumerate(chunks):
            title_rows.append(len(rows))
            suffix = "" if chunk_index == 0 else f" ({t['continued']})"
            rows.append([Paragraph(f"{number}. {item.title}{suffix}", s["concept"]), "", "", "", ""])
            value_start = len(rows)
            title_content_pairs.append((title_rows[-1], value_start))
            if chunk_index == 0:
                concept_text = item.description or t["no_description"]
                children = chunk
            else:
                concept_text = f"• {chunk[0]}"
                children = chunk[1:]
            rows.append([
                Paragraph(concept_text, s["bullet"] if chunk_index else s["normal"]),
                Paragraph(item.unit, s["center"]), Paragraph(f"{item.quantity:g}", s["center"]),
                Paragraph(money(item.unit_price), s["center"]), Paragraph(money(item.amount), s["center"]),
            ])
            for child in children:
                rows.append([Paragraph(f"• {child}", s["bullet"]), "", "", "", ""])
            value_spans.append((value_start, len(rows) - 1))
    table = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), palette.primary), ("TEXTCOLOR", (0, 0), (-1, 0), palette.bar_text),
        ("ALIGN", (1, 0), (-1, 0), "CENTER"), ("GRID", (0, 0), (-1, -1), .35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]
    for row in title_rows:
        commands += [("SPAN", (0, row), (-1, row)), ("BACKGROUND", (0, row), (-1, row), palette.pale), ("TOPPADDING", (0, row), (-1, row), 6), ("BOTTOMPADDING", (0, row), (-1, row), 6)]
    for title_row, first_content_row in title_content_pairs:
        commands.append(("NOSPLIT", (0, title_row), (4, first_content_row)))
    for start, end in value_spans:
        for column in range(1, 5):
            commands.append(("SPAN", (column, start), (column, end)))
        commands += [
            ("VALIGN", (1, start), (4, end), "MIDDLE"),
            ("ALIGN", (1, start), (4, end), "CENTER"),
        ]
    table.setStyle(TableStyle(commands))
    return table


def _totals(q: QuoteData, s, palette: QuotePalette, t):
    rows = [[Paragraph(t["subtotal"], s["label"]), Paragraph(money(q.subtotal), s["right"])]]
    if q.include_vat:
        rows.append([Paragraph(f"{t['vat']} {q.vat_rate:g}%", s["label"]), Paragraph(money(q.vat), s["right"])])
    rows.append([Paragraph(t["total"], s["bar_label"]), Paragraph(money(q.total), s["bar_total"])])
    rows.append([Paragraph(f"{t['advance']} {q.advance_percent:g}%", s["label"]), Paragraph(money(q.advance), s["right"])])
    table = Table(rows, colWidths=[42 * mm, 35 * mm], hAlign="RIGHT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), .35, LINE), ("BACKGROUND", (0, 0), (0, -1), palette.pale),
        ("BACKGROUND", (0, -2), (-1, -2), palette.primary), ("TEXTCOLOR", (0, -2), (-1, -2), palette.bar_text),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _conditions(q: QuoteData, s, palette: QuotePalette, t):
    data = [
        [Paragraph(t["notes"], s["label"]), Paragraph(q.notes or "-", s["normal"]), Paragraph(t["currency"], s["label"]), Paragraph(q.currency, s["normal"])],
        [Paragraph(t["payment_terms"], s["label"]), Paragraph(q.payment_terms, s["normal"]), Paragraph(t["validity"], s["label"]), Paragraph(f"{q.validity_days} {t['days']}", s["normal"])],
        [Paragraph(t["bank_clabe"], s["label"]), Paragraph(f"{q.bank}  |  {q.clabe}", s["normal"]), Paragraph(t["account"], s["label"]), Paragraph(q.account, s["normal"])],
        [Paragraph(t["delivery_time"], s["label"]), Paragraph(q.delivery_time, s["normal"]), "", ""],
    ]
    table = Table(data, colWidths=[30 * mm, 72 * mm, 24 * mm, 54 * mm])
    table.setStyle(TableStyle([
        ("SPAN", (1, 3), (3, 3)), ("BACKGROUND", (0, 0), (0, -1), palette.pale),
        ("BACKGROUND", (2, 0), (2, 2), palette.pale), ("GRID", (0, 0), (-1, -1), .35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _quote_image(path: Path, max_w=175 * mm, max_h=150 * mm):
    with PILImage.open(path) as source:
        im = ImageOps.exif_transpose(source)
        width, height = im.size
    scale = min(max_w / width, max_h / height)
    return Image(str(path), width=width * scale, height=height * scale, hAlign="CENTER")


def generate_quote_pdf(quote: QuoteData, output: str | Path, logo: Path | None = None) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    palette = quote_palette()
    t = labels(quote.language)
    styles = _styles(palette)
    doc = QuoteDocTemplate(str(target), quote, logo, palette)
    story = [
        _client_table(quote, styles, palette, t), Spacer(1, 4 * mm),
        Paragraph(quote.project_title or t["quoted_services"], ParagraphStyle("Project", parent=styles["concept"], fontSize=11, leading=14, alignment=TA_CENTER, spaceAfter=3 * mm)),
    ]
    if quote.items:
        story.append(_concept_table(quote, styles, palette, t))
    else:
        story.append(Paragraph(t["no_items"], styles["normal"]))
    story += [Spacer(1, 3 * mm), _totals(quote, styles, palette, t), Spacer(1, 3 * mm), _conditions(quote, styles, palette, t), Spacer(1, 7 * mm)]
    try:
        parsed = datetime.strptime(quote.quote_date, "%d/%m/%Y")
        date_text = _display_date(parsed.strftime("%d/%m/%Y"), quote.language)
    except ValueError:
        date_text = quote.quote_date
    story += [
        Paragraph(fill_template(t["place_date"], date=date_text), styles["center"]), Spacer(1, 7 * mm),
        Paragraph(t["sincerely"], styles["center"]), Spacer(1, 8 * mm),
        Paragraph("______________________________", styles["center"]),
        Paragraph(normalize_quote_signer(quote.prepared_by), ParagraphStyle("Signer", parent=styles["center"], fontName="Helvetica-Bold")),
    ]
    for number, image in enumerate(quote.images, 1):
        path = Path(image.path)
        if not path.exists():
            continue
        caption = image.caption.strip() or fill_template(t["reference_image"], number=number)
        story += [Spacer(1, 10 * mm), Paragraph(f"{t['image']} {number}", styles["concept"]), Spacer(1, 2 * mm), _quote_image(path), Spacer(1, 2 * mm), Paragraph(caption, styles["image"])]
    doc.build(story)
    return target
