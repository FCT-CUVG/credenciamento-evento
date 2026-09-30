"""Utilitários de texto e data usados em vários módulos."""
import re
import unicodedata
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "").strip())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).casefold().split())


def mask_public_text(value):
    """Hide the middle of each word before returning participant data publicly."""
    def mask_word(match):
        word = match.group()
        return word[0] + "*" * (len(word) - 2) + word[-1] if len(word) > 2 else word

    return re.sub(r"[^\W_]+", mask_word, value or "", flags=re.UNICODE)
