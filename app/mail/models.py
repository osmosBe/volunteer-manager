"""Provider-neutral transactional mail models."""

from pydantic import BaseModel, EmailStr, Field, model_validator


class MailRecipient(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)


class MailAttachment(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    content: bytes


class MailMessage(BaseModel):
    to: list[MailRecipient] = Field(default_factory=list)
    cc: list[MailRecipient] = Field(default_factory=list)
    bcc: list[MailRecipient] = Field(default_factory=list)
    subject: str = Field(min_length=1, max_length=998)
    text_body: str | None = None
    html_body: str | None = None
    reply_to: list[MailRecipient] = Field(default_factory=list)
    attachments: list[MailAttachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_message(self):
        if not self.to and not self.cc and not self.bcc:
            raise ValueError("At least one recipient is required.")
        if not self.text_body and not self.html_body:
            raise ValueError("A text or HTML body is required.")
        return self


class MailSendResult(BaseModel):
    success: bool
    provider: str
    provider_message_id: str | None = None
    error_code: str | None = None
    correlation_id: str | None = None
