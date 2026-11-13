from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image


@dataclass
class PhotoInfo:
    path: Path
    taken_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    width: int = 0
    height: int = 0
    description: str = ""

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def orientation(self) -> str:
        return "Horizontal" if self.width >= self.height else "Vertical"


def _ratio(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return float(value[0]) / float(value[1])


def _coordinate(parts, ref: str) -> float | None:
    if not parts or len(parts) < 3:
        return None
    value = _ratio(parts[0]) + _ratio(parts[1]) / 60 + _ratio(parts[2]) / 3600
    return -value if ref.upper() in {"S", "W"} else value


def read_photo(path: str | Path) -> PhotoInfo:
    path = Path(path)
    info = PhotoInfo(path=path)
    with Image.open(path) as image:
        info.width, info.height = image.size
        exif = image.getexif()
        if not exif:
            return info
        for key in (36867, 36868, 306):
            raw = exif.get(key)
            if raw:
                try:
                    info.taken_at = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
                    break
                except ValueError:
                    pass
        gps_raw = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if gps_raw:
            info.latitude = _coordinate(gps_raw.get(2), str(gps_raw.get(1, "N")))
            info.longitude = _coordinate(gps_raw.get(4), str(gps_raw.get(3, "E")))
    return info


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
