"""Remove obvious personal data before anything is stored or sent to a model.

This is a floor, not a guarantee: it catches credentials (API keys, tokens, private keys,
"password: ..."), emails, phone numbers, card-like and IBAN-like numbers, IP addresses and
URL query strings. Names stay, because "ask Sara" is often part of the rule.
"""

from __future__ import annotations

import re

# Credentials first, before shorter patterns cut them into pieces.
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")
_SECRET_FIELD = re.compile(r"(?i)\b(password|passwd|pwd|passcode|pin|otp|secret|api[ _-]?key|token)"
                           r"(\s*(?:is|=|:)\s*)\S+")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?\S+")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-\d{2,4}")


def _phone(m: re.Match[str]) -> str:
    s = m.group(0)
    digits = sum(c.isdigit() for c in s)
    # Dates ("2026-11-09") and short number runs are not phone numbers; E.164 caps numbers at 15 digits.
    if _DATE.fullmatch(s.strip()) or not 9 <= digits <= 15:
        return s
    return "[phone]"


_SECRET_WORDS = ("pass", "pwd", "pin", "otp", "secret", "key", "token")


def redact(text: str) -> str:
    # Cheap substring checks first: most chat lines contain none of these, and exports run to 100k+ lines.
    low = text.lower()
    if "-----begin" in low:
        text = _PRIVATE_KEY.sub("[private key]", text)
    text = _TOKEN.sub("[secret]", text)
    if any(w in low for w in _SECRET_WORDS):
        text = _SECRET_FIELD.sub(lambda m: m.group(1) + m.group(2) + "[secret]", text)
    if "?" in text and "http" in low:
        text = _URL_QUERY.sub(r"\1?[redacted]", text)
    if "@" in text:
        text = _EMAIL.sub("[email]", text)
    if text.count(".") >= 3:
        text = _IPV4.sub("[ip]", text)
    text = _IBAN.sub("[iban]", text)
    text = _CARD.sub("[card]", text)
    return _PHONE.sub(_phone, text)
