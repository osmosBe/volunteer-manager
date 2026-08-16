"""Portable, database-backed application branding."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.models import BrandingSettings
from app.models.core import utcnow

DEFAULT_LOGO_URL = "/static/images/logo.png"
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_RASTER_PIXELS = 20_000_000
MAX_RASTER_WIDTH = 8_000
MAX_RASTER_HEIGHT = 8_000
OUTPUT_MAX_WIDTH = 1_600
OUTPUT_MAX_HEIGHT = 800
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/svg+xml"}
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
FORBIDDEN_SVG_ELEMENTS = {
    "animate",
    "animatemotion",
    "animatetransform",
    "audio",
    "discard",
    "embed",
    "foreignobject",
    "iframe",
    "object",
    "script",
    "set",
    "style",
    "video",
}
SVG_NUMBER = re.compile(r"^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:px)?$")
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS


class BrandingError(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedLogo:
    data: bytes
    content_type: str
    filename: str | None


def validate_logo_url(value: str) -> str:
    """Validate a render-only remote logo URL without fetching it server-side."""

    url = value.strip()
    if not url or len(url) > 2048 or any(ord(char) < 32 for char in url):
        raise BrandingError("Bitte gib eine gültige Logo-URL ein.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise BrandingError("Logo-URLs müssen mit http:// oder https:// beginnen.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrandingError("Die Logo-URL enthält einen ungültigen Port.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise BrandingError("Die Logo-URL enthält einen ungültigen Port.")
    if parsed.username or parsed.password:
        raise BrandingError("Logo-URLs dürfen keine Zugangsdaten enthalten.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost":
        raise BrandingError("Die Logo-URL muss auf einen erreichbaren Host zeigen.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise BrandingError("Private oder lokale IP-Adressen sind nicht erlaubt.")
    return url


def _safe_filename(filename: str | None, fallback: str) -> str:
    name = Path(filename or fallback).name.strip()
    return (name or fallback)[:255]


def _process_raster(
    data: bytes, declared_content_type: str, filename: str | None
) -> ProcessedLogo:
    try:
        with Image.open(BytesIO(data)) as original:
            actual_format = str(original.format or "").upper()
            expected_format = {
                "image/png": "PNG",
                "image/jpeg": "JPEG",
            }[declared_content_type]
            if actual_format != expected_format:
                raise BrandingError("Dateityp und Bildinhalt stimmen nicht überein.")
            width, height = original.size
            if (
                width <= 0
                or height <= 0
                or width > MAX_RASTER_WIDTH
                or height > MAX_RASTER_HEIGHT
                or width * height > MAX_RASTER_PIXELS
            ):
                raise BrandingError(
                    "Das Logo ist zu groß; maximal 8000 × 8000 Pixel und "
                    "20 Megapixel sind erlaubt."
                )
            original.load()
            image = ImageOps.exif_transpose(original)
            image.thumbnail((OUTPUT_MAX_WIDTH, OUTPUT_MAX_HEIGHT), LANCZOS)
            output = BytesIO()
            if declared_content_type == "image/jpeg":
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=88, optimize=True)
                fallback = "logo.jpg"
            else:
                if image.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                    image = image.convert("RGBA")
                image.save(output, format="PNG", optimize=True)
                fallback = "logo.png"
    except BrandingError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise BrandingError("Die hochgeladene Bilddatei ist ungültig.") from exc
    result = output.getvalue()
    if len(result) > MAX_LOGO_BYTES:
        raise BrandingError("Das verarbeitete Logo überschreitet 2 MB.")
    return ProcessedLogo(
        data=result,
        content_type=declared_content_type,
        filename=_safe_filename(filename, fallback),
    )


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].lower()


def _svg_dimension(value: str | None) -> float | None:
    if not value or not SVG_NUMBER.fullmatch(value.strip()):
        return None
    return float(value.strip().removesuffix("px"))


def _process_svg(data: bytes, filename: str | None) -> ProcessedLogo:
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise BrandingError(
            "SVG-Dateien dürfen keine DTD- oder Entity-Deklarationen enthalten."
        )
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise BrandingError("Die hochgeladene SVG-Datei ist ungültig.") from exc
    if _local_name(root.tag) != "svg":
        raise BrandingError("Die Datei enthält kein SVG-Logo.")

    for element in root.iter():
        if _local_name(element.tag) in FORBIDDEN_SVG_ELEMENTS:
            raise BrandingError("Das SVG enthält nicht erlaubte aktive Inhalte.")
        for attribute, raw_value in element.attrib.items():
            name = _local_name(attribute)
            value = raw_value.strip()
            lowered_value = value.lower()
            if name.startswith("on") or name in {"base", "src"}:
                raise BrandingError("Das SVG enthält nicht erlaubte aktive Inhalte.")
            if name in {"href", "xlink:href"} and not value.startswith("#"):
                raise BrandingError("Externe SVG-Ressourcen sind nicht erlaubt.")
            referenced_urls = re.findall(r"url\(\s*(['\"]?)(.*?)\1\s*\)", value, re.I)
            if any(not target.strip().startswith("#") for _, target in referenced_urls):
                raise BrandingError("Externe SVG-Ressourcen sind nicht erlaubt.")
            if name == "style" and (
                "@import" in lowered_value or "expression(" in lowered_value
            ):
                raise BrandingError("Externe SVG-Ressourcen sind nicht erlaubt.")

    width = _svg_dimension(root.attrib.get("width"))
    height = _svg_dimension(root.attrib.get("height"))
    if (width and width > MAX_RASTER_WIDTH) or (height and height > MAX_RASTER_HEIGHT):
        raise BrandingError(
            "Das SVG ist zu groß; maximal 8000 × 8000 Pixel sind erlaubt."
        )
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if view_box:
        try:
            _, _, view_width, view_height = [
                float(item) for item in view_box.replace(",", " ").split()
            ]
        except (TypeError, ValueError) as exc:
            raise BrandingError("Das SVG enthält eine ungültige viewBox.") from exc
        if view_width <= 0 or view_height <= 0:
            raise BrandingError("Das SVG enthält eine ungültige viewBox.")

    ElementTree.register_namespace("", SVG_NAMESPACE)
    sanitized = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(sanitized) > MAX_LOGO_BYTES:
        raise BrandingError("Das verarbeitete Logo überschreitet 2 MB.")
    return ProcessedLogo(
        data=sanitized,
        content_type="image/svg+xml",
        filename=_safe_filename(filename, "logo.svg"),
    )


def process_logo_upload(
    data: bytes, content_type: str | None, filename: str | None
) -> ProcessedLogo:
    media_type = str(content_type or "").lower().split(";", 1)[0].strip()
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise BrandingError("Erlaubt sind PNG-, JPEG- und SVG-Dateien.")
    if not data:
        raise BrandingError("Bitte wähle eine Logo-Datei aus.")
    if len(data) > MAX_LOGO_BYTES:
        raise BrandingError("Die Logo-Datei darf maximal 2 MB groß sein.")
    if media_type == "image/svg+xml":
        return _process_svg(data, filename)
    return _process_raster(data, media_type, filename)


class BrandingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self) -> BrandingSettings | None:
        return self.db.get(BrandingSettings, 1)

    def _get_or_create(self) -> BrandingSettings:
        settings = self.get()
        if settings is None:
            settings = BrandingSettings(id=1)
            self.db.add(settings)
        return settings

    def set_logo_url(self, value: str) -> BrandingSettings:
        settings = self._get_or_create()
        settings.logo_url = validate_logo_url(value)
        settings.logo_data = None
        settings.logo_content_type = None
        settings.logo_filename = None
        settings.updated_at = utcnow()
        self.db.flush()
        return settings

    def set_uploaded_logo(self, logo: ProcessedLogo) -> BrandingSettings:
        settings = self._get_or_create()
        settings.logo_url = None
        settings.logo_data = logo.data
        settings.logo_content_type = logo.content_type
        settings.logo_filename = logo.filename
        settings.updated_at = utcnow()
        self.db.flush()
        return settings

    def reset(self) -> BrandingSettings:
        settings = self._get_or_create()
        settings.logo_url = None
        settings.logo_data = None
        settings.logo_content_type = None
        settings.logo_filename = None
        settings.updated_at = utcnow()
        self.db.flush()
        return settings


def branding_source(settings: BrandingSettings | None) -> str:
    if settings and settings.logo_url:
        return "url"
    if settings and settings.logo_data:
        return "upload"
    return "default"
