from app.services.qr_codes import checkin_scan_url, qr_svg


def test_qr_payload_contains_only_checkin_reference():
    url = checkin_scan_url("https://volunteer.example/", 42)

    assert url == "https://volunteer.example/admin/check-in/scan/42"
    image = qr_svg(url)
    assert image.startswith(b"<?xml")
    assert b"person" not in image
