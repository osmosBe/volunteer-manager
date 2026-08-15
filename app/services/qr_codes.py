"""QR generation for check-in links without embedding personal data."""

from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage


def checkin_scan_url(base_url: str, assignment_id: int) -> str:
    return f"{base_url.rstrip('/')}/admin/check-in/scan/{assignment_id}"


def qr_svg(payload: str) -> bytes:
    image = qrcode.make(
        payload,
        image_factory=SvgPathImage,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=2,
        box_size=8,
    )
    output = BytesIO()
    image.save(output)
    return output.getvalue()
