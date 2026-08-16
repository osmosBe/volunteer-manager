from app.auth.models import AuthenticatedUser


def get_local_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="local-dev",
        name="Local Developer",
        email="local-dev@example.invalid",
        groups=[],
        claims=[],
    )
