from pydantic import BaseModel, Field


class AuthenticatedUser(BaseModel):
    """Minimal authenticated user details safe for app-level authorization."""

    user_id: str | None = None
    name: str | None = None
    email: str | None = None
    groups: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
