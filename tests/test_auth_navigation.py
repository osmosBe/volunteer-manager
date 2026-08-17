import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def clear_cached_settings():
    yield
    get_settings.cache_clear()


def _set_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("AUTH_MODE", mode)
    monkeypatch.setenv("DEBUG", "false")
    if mode == "oidc":
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://identity.example.org/issuer")
        monkeypatch.setenv("OIDC_CLIENT_ID", "volunteer-manager")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-client-secret")
        monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
        monkeypatch.setenv("APP_BASE_URL", "https://volunteer.example.org")
    get_settings.cache_clear()


def test_disabled_login_and_logout_keep_local_navigation(monkeypatch):
    _set_mode(monkeypatch, "disabled")
    client = TestClient(app)

    login = client.get("/auth/login", follow_redirects=False)
    logout = client.get("/auth/logout", follow_redirects=False)

    assert login.status_code == 307
    assert login.headers["location"] == "/admin"
    assert logout.status_code == 307
    assert logout.headers["location"] == "/"


def test_easyauth_login_and_logout_keep_platform_routes(monkeypatch):
    _set_mode(monkeypatch, "easyauth")
    client = TestClient(app)

    login = client.get("/auth/login", follow_redirects=False)
    logout = client.get("/auth/logout", follow_redirects=False)

    assert login.status_code == 307
    assert login.headers["location"] == (
        "/.auth/login/aad?post_login_redirect_uri=/auth/post-login"
    )
    assert logout.status_code == 307
    assert logout.headers["location"] == "/.auth/logout?post_logout_redirect_uri=/"


def test_oidc_login_and_logout_keep_provider_adapter(monkeypatch):
    _set_mode(monkeypatch, "oidc")
    cleared = []

    async def fake_begin_login(request):
        del request
        return RedirectResponse("https://identity.example.org/authorize")

    def fake_clear_login(request):
        cleared.append(request.url.path)

    monkeypatch.setattr("app.main.begin_login", fake_begin_login)
    monkeypatch.setattr("app.main.clear_login", fake_clear_login)
    client = TestClient(app)

    login = client.get("/auth/login", follow_redirects=False)
    logout = client.get("/auth/logout", follow_redirects=False)

    assert login.status_code == 307
    assert login.headers["location"] == "https://identity.example.org/authorize"
    assert logout.status_code == 307
    assert logout.headers["location"] == "/"
    assert cleared == ["/auth/logout"]
