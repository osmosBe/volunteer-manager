from hashlib import sha256


def normalize_email(email: str) -> str:
    return email.strip().lower()


def deterministic_email_hash(email: str) -> str:
    return sha256(normalize_email(email).encode("utf-8")).hexdigest()
