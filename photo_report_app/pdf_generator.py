from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .document_texts import fill_template, report_months, report_texts
from .maps import build_map
from .metadata import PhotoInfo


BLUE = HexColor("#173B5F")
ACCENT = HexColor("#0B7FAB")
TEXT = HexColor("#263746")
MUTED = HexColor("#6B7C8C")
LIGHT = HexColor("#E8EFF4")


@dataclass
class ReportOptions:
    title: str
    report_date: date
    photos: list[PhotoInfo]
    output: Path
    logo: Path | None = None
    company: str = "INGENIERÍA TÉCNICA Y TOPOGRÁFICA"
    website: str = "www.grupoitt.com"
    footer: str = "Ingeniería Técnica y Topográfica | Chihuahua, Chih."
    include_map: bool = True


def _fit_text(canvas, text: str, max_width: float, size: float, font="Helvetica-Bold") -> float:
    while size > 11 and stringWidth(text, font, size) > max_width:
        size -= .5
    return size


def _header(c: Canvas, opts: ReportOptions, page: int, texts: dict[str, str]):
    w, h = A4
    if opts.logo and opts.logo.exists():
        try:
            c.drawImage(str(opts.logo), 48, h - 79, width=70, height=50, preserveAspectRatio=True, mask="auto", anchor="c")
        except Exception:
            pass
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 9.2)
    c.drawCentredString(w / 2, h - 45, opts.company)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(w / 2, h - 57, texts["header_title"])
    c.setFillColor(ACCENT)
    c.setFont("Helvetica", 8)
    c.drawCentredString(w / 2, h - 68, opts.website)
    c.setFillColor(TEXT)
    c.setFont("Helvetica", 8)
    c.drawRightString(w - 48, h - 48, opts.report_date.strftime("%d/%m/%Y"))
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(w - 48, h - 59, texts["document_tag"])
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.1)
    c.line(48, h - 87, w - 48, h - 87)


def _footer(c: Canvas, opts: ReportOptions, page: int, texts: dict[str, str]):
    w, _ = A4
    c.setStrokeColor(BLUE)
    c.setLineWidth(.55)
    c.line(48, 45, w - 48, 45)
    c.setFillColor(ACCENT)
    c.setFont("Helvetica", 7.2)
    c.drawString(49, 28, opts.footer)
    c.setFillColor(TEXT)
    c.drawRightString(w - 49, 28, f"{texts['page_label']} {page}")


def _safe_image(path: Path, max_pixels=(1800, 1800)) -> Image.Image:
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail(max_pixels, Image.Resampling.LANCZOS)
        return image.copy()


def _draw_contain(c: Canvas, image: Image.Image, box: tuple[float, float, float, float]):
    x, y, bw, bh = box
    scale = min(bw / image.width, bh / image.height)
    iw, ih = image.width * scale, image.height * scale
    # Feeding JPEG bytes to ReportLab avoids large lossless RGB streams while
    # preserving more than enough detail for an A4 print.
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=88, optimize=True, progressive=True)
    stream.seek(0)
    c.drawImage(ImageReader(stream), x + (bw - iw) / 2, y + (bh - ih) / 2, iw, ih, preserveAspectRatio=True)


def generate_report(opts: ReportOptions, progress=None) -> Path:
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    c = Canvas(str(opts.output), pagesize=A4, pageCompression=1)
    c.setTitle(opts.title)
    c.setAuthor(opts.company)
    w, h = A4
    texts = report_texts()
    page = 1
    _header(c, opts, page, texts)
    c.setFillColor(TEXT)
    title_size = _fit_text(c, opts.title, w - 110, 23)
    c.setFont("Helvetica-Bold", title_size)
    c.drawCentredString(w / 2, h - 127, opts.title)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 11)
    months = report_months(texts)
    date_line = fill_template(
        texts["cover_date_template"], day=f"{opts.report_date.day:02d}",
        month=months[opts.report_date.month - 1], year=opts.report_date.year,
    )
    c.drawCentredString(w / 2, h - 148, date_line)
    first_photo_on_cover = not opts.include_map
    if opts.include_map:
        map_image, has_map = build_map(opts.photos)
        c.setFillColor(BLUE)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(52, h - 184, texts["location_sketch"])
        _draw_contain(c, map_image, (52, 190, w - 104, h - 390))
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        note = texts["map_number_note"] if has_map else texts["map_no_gps_note"]
        c.drawString(52, 174, note)
    else:
        cover_image = _safe_image(opts.photos[0].path)
        if opts.photos[0].description.strip():
            description = opts.photos[0].description.strip()
            size = _fit_text(c, description, w - 104, 9, "Helvetica")
            c.setFillColor(TEXT)
            c.setFont("Helvetica", size)
            c.drawCentredString(w / 2, h - 170, description)
        _draw_contain(c, cover_image, (52, 125, w - 104, h - 320))
    _footer(c, opts, page, texts)
    c.showPage()

    report_photos = opts.photos[1:] if first_photo_on_cover else opts.photos
    total = len(report_photos)
    for index, photo in enumerate(report_photos, 2 if first_photo_on_cover else 1):
        page += 1
        _header(c, opts, page, texts)
        c.setFillColor(BLUE)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(52, h - 112, f"{texts['photo_label']} {index:02d}")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        metadata = []
        if photo.taken_at:
            metadata.append(f"{texts['captured_label']}: {photo.taken_at.strftime('%d/%m/%Y %H:%M')}")
        if photo.has_gps:
            metadata.append(f"{texts['gps_label']}: {photo.latitude:.6f}, {photo.longitude:.6f}")
        c.drawRightString(w - 52, h - 112, "  |  ".join(metadata) or photo.path.name)
        if photo.description.strip():
            description = photo.description.strip()
            size = _fit_text(c, description, w - 104, 9, "Helvetica")
            c.setFillColor(TEXT)
            c.setFont("Helvetica", size)
            c.drawString(52, h - 128, description)
        image = _safe_image(photo.path)
        _draw_contain(c, image, (52, 78, w - 104, h - 228 if photo.description.strip() else h - 212))
        _footer(c, opts, page, texts)
        if progress:
            progress(index - (1 if first_photo_on_cover else 0), total)
        c.showPage()
    c.save()
    return opts.output
