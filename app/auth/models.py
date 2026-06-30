from pydantic import BaseModel, Field


class AuthenticatedUser(BaseModel):
    """Authenticated user details safe for app-level authorization."""

    user_id: str = ""
    name: str = ""
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
