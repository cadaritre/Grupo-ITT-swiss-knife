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
CELL = HexColor("#F4F7FA")               # background of a photo's box in the 2, 4 and 6 per page layouts


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
    include_credentials: bool = False
    signature: Path | None = None
    license_card: Path | None = None
    signer: str = ""                    # los pone el perfil corporativo (`comun.perfiles.opciones_reporte`)
    professional_license: str = ""
    photos_per_page: int = 1            # 1 (the desktop layout), 2, 4 or 6


#: photos per page -> (columns, rows) of the grid; 1 keeps the original full-page layout
PHOTO_GRIDS = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3)}
PHOTOS_PER_PAGE = tuple(PHOTO_GRIDS)


def photo_pages(count: int, per_page: int = 1, include_map: bool = True) -> list[list[int]]:
    """Which photos (0-based indices) go on each page: the first list is the cover, then one list per photo page.

    Without the location sketch the first photo goes alone on the cover; the rest follow `per_page` at a time.
    The web editor mirrors this exact rule to show the page breaks (`reportes/orden.js`, `paginas`).
    """
    if per_page not in PHOTO_GRIDS:
        raise ValueError(f"photos_per_page must be one of {PHOTOS_PER_PAGE}")
    first = 0 if include_map else min(1, count)
    return [list(range(first))] + [list(range(i, min(i + per_page, count))) for i in range(first, count, per_page)]


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


def _ellipsize(text: str, max_width: float, font: str, size: float, force: bool = False) -> str:
    """`text` cut to `max_width` with an ellipsis (always ends with one when `force`)."""
    if not force and stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap_lines(text: str, max_width: float, font: str, size: float, max_lines: int) -> list[str]:
    """Word-wrap `text` to at most `max_lines` lines; the last one ends with an ellipsis when text is left over."""
    words, lines, line = text.split(), [], ""
    while words and len(lines) < max_lines:
        word = words[0]
        candidate = f"{line} {word}" if line else word
        if stringWidth(candidate, font, size) <= max_width:
            line = candidate
            words.pop(0)
        elif line:
            lines.append(line)
            line = ""
        else:                                   # a single word wider than the cell: cut it
            cut = len(word)
            while cut > 1 and stringWidth(word[:cut], font, size) > max_width:
                cut -= 1
            lines.append(word[:cut])
            words[0] = word[cut:]
    if line and len(lines) < max_lines:
        lines.append(line)
    if words:                                   # text left over: the last line says so
        lines[-1] = _ellipsize(f"{lines[-1]} {words[0][:80]}", max_width, font, size, force=True)
    return lines


#: per grid layout: label size, metadata size, metadata lines, note size, note lines, image resolution
_CELL_STYLES = {
    2: {"label": 11, "meta": 7.5, "meta_lines": 1, "note": 9, "note_lines": 2, "pixels": (1800, 1800)},
    4: {"label": 10, "meta": 7, "meta_lines": 2, "note": 8, "note_lines": 3, "pixels": (1400, 1400)},
    6: {"label": 9.5, "meta": 7, "meta_lines": 2, "note": 7.5, "note_lines": 2, "pixels": (1200, 1200)},
}
_GRID_LEFT, _GRID_TOP_OFFSET, _GRID_BOTTOM = 52, 100, 62
_GUTTER_X, _GUTTER_Y = 16, 18


def grid_cells(per_page: int) -> list[tuple[float, float, float, float]]:
    """Cells (x, y, width, height; y is the bottom edge) of a photo page, in reading order. Same cells on every page."""
    w, h = A4
    cols, rows = PHOTO_GRIDS[per_page]
    top = h - _GRID_TOP_OFFSET
    cell_w = (w - 2 * _GRID_LEFT - _GUTTER_X * (cols - 1)) / cols
    cell_h = (top - _GRID_BOTTOM - _GUTTER_Y * (rows - 1)) / rows
    return [(_GRID_LEFT + col * (cell_w + _GUTTER_X), top - (row + 1) * cell_h - row * _GUTTER_Y, cell_w, cell_h)
            for row in range(rows) for col in range(cols)]


def _photo_metadata(photo: PhotoInfo, texts: dict[str, str]) -> list[str]:
    metadata = []
    if photo.taken_at:
        metadata.append(f"{texts['captured_label']}: {photo.taken_at.strftime('%d/%m/%Y %H:%M')}")
    if photo.has_gps:
        metadata.append(f"{texts['gps_label']}: {photo.latitude:.6f}, {photo.longitude:.6f}")
    return metadata


def image_box(per_page: int, cell: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The part of a cell that holds the image: below the label, the metadata and the space reserved for the note."""
    style = _CELL_STYLES[per_page]
    x, y, cw, ch = cell
    text_h = style["label"] + 4 + (style["meta_lines"] - 1) * (style["meta"] + 3) + style["note_lines"] * (style["note"] * 1.25) + 6
    return x, y, cw, ch - text_h


def _photo_cell(c: Canvas, photo: PhotoInfo, number: int, cell, per_page: int, texts: dict[str, str]):
    """One photo of a grid page: label and metadata, the note (wrapped, with an ellipsis) and the image contained in its box."""
    style = _CELL_STYLES[per_page]
    x, y, cw, ch = cell
    top = y + ch
    label = f"{texts['photo_label']} {number:02d}"
    baseline = top - style["label"] * .8
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", style["label"])
    c.drawString(x, baseline, label)
    label_w = stringWidth(label, "Helvetica-Bold", style["label"]) + 10
    metadata = _photo_metadata(photo, texts) or [photo.path.name]
    c.setFillColor(MUTED)
    c.setFont("Helvetica", style["meta"])
    if style["meta_lines"] == 1:
        c.drawRightString(x + cw, baseline, _ellipsize("  |  ".join(metadata), cw - label_w, "Helvetica", style["meta"]))
    else:
        c.drawRightString(x + cw, baseline, _ellipsize(metadata[0], cw - label_w, "Helvetica", style["meta"]))
        if len(metadata) > 1:
            c.drawString(x, baseline - style["meta"] - 3, _ellipsize("  |  ".join(metadata[1:]), cw, "Helvetica", style["meta"]))
    baseline -= (style["meta_lines"] - 1) * (style["meta"] + 3)
    description = photo.description.strip()
    if description:
        c.setFillColor(TEXT)
        c.setFont("Helvetica", style["note"])
        for k, line in enumerate(_wrap_lines(description, cw, "Helvetica", style["note"], style["note_lines"])):
            c.drawString(x, baseline - 4 - style["note"] - k * style["note"] * 1.25, line)
    box = image_box(per_page, cell)
    c.setFillColor(CELL)
    c.setStrokeColor(LIGHT)
    c.setLineWidth(.6)
    c.rect(*box, stroke=1, fill=1)
    image = _safe_image(photo.path, max_pixels=style["pixels"])
    try:
        _draw_contain(c, image, box)
    finally:
        image.close()


def _credentials_page(c: Canvas, opts: ReportOptions, page: int, texts: dict[str, str]):
    """Draw the optional professional credentials as a separate closing page."""
    w, h = A4
    _header(c, opts, page, texts)
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(w / 2, h - 126, texts["credentials_title"])

    if opts.signature and opts.signature.exists():
        signature = _safe_image(opts.signature, max_pixels=(1600, 800))
        _draw_contain(c, signature, (150, h - 286, w - 300, 82))
    else:
        c.setStrokeColor(BLUE)
        c.setLineWidth(.8)
        c.line(155, h - 252, w - 155, h - 252)

    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(w / 2, h - 298, opts.signer)
    c.setFillColor(TEXT)
    c.setFont("Helvetica", 10)
    license_text = f"{texts['professional_license_label']}: {opts.professional_license}"
    c.drawCentredString(w / 2, h - 314, license_text)

    if opts.license_card and opts.license_card.exists():
        card = _safe_image(opts.license_card, max_pixels=(1800, 1400))
        _draw_contain(c, card, (72, 102, w - 144, 340))

    _footer(c, opts, page, texts)


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

    if opts.photos_per_page != 1:
        page = _grid_pages(c, opts, page, texts, progress)
    else:
        page = _single_pages(c, opts, page, texts, first_photo_on_cover, progress)
    if opts.include_credentials:
        page += 1
        _credentials_page(c, opts, page, texts)
        c.showPage()
    c.save()
    return opts.output


def _grid_pages(c: Canvas, opts: ReportOptions, page: int, texts: dict[str, str], progress) -> int:
    """Photo pages with 2, 4 or 6 photos each: the same cells on every page, a short last page keeps them, top-left first."""
    per_page = opts.photos_per_page
    pages = photo_pages(len(opts.photos), per_page, opts.include_map)[1:]
    cells = grid_cells(per_page)
    for done, indices in enumerate(pages, 1):
        page += 1
        _header(c, opts, page, texts)
        for cell, index in zip(cells, indices):
            _photo_cell(c, opts.photos[index], index + 1, cell, per_page, texts)
        _footer(c, opts, page, texts)
        if progress:
            progress(done, len(pages))
        c.showPage()
    return page


def _single_pages(c: Canvas, opts: ReportOptions, page: int, texts: dict[str, str], first_photo_on_cover: bool, progress) -> int:
    """One photo per page: the original layout, byte for byte the desktop app's."""
    w, h = A4
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
    return page
