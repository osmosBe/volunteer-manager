from fastapi.testclient import TestClient

from app.main import app


def test_shared_form_validation_asset_has_accessible_error_behavior():
    response = TestClient(app).get("/static/js/forms.js")

    assert response.status_code == 200
    source = response.text
    assert "Bitte überprüfe deine Eingaben." in source
    assert 'setAttribute("aria-invalid", "true")' in source
    assert 'setAttribute("aria-describedby"' in source
    assert "this.summary.focus()" in source
    assert "requiredGroup" in source
    assert "maxFileSize" in source
    assert "data-server-error" in source
    assert "window.confirm" in source
