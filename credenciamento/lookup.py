"""Chaves de busca: CPF e e-mail ficam guardados só como HMAC, nunca em texto.

Sem a chave CHECKIN_LOOKUP_SECRET, um banco, backup ou exportação vazados não revelam os CPFs
nem os e-mails: com só um bilhão de CPFs possíveis, um hash simples seria revertido em minutos."""
import hashlib
import hmac
import re

from . import settings


def lookup_ready():
    return len(settings.LOOKUP_SECRET) >= 32


def _key(kind, value):
    if not lookup_ready():
        raise ValueError("Defina CHECKIN_LOOKUP_SECRET com pelo menos 32 caracteres.")
    return hmac.new(settings.LOOKUP_SECRET, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def cpf_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def cpf_key(value):
    """Chave de um CPF com 11 dígitos (com ou sem pontuação); vazio quando não há CPF."""
    digits = cpf_digits(value)
    return _key("cpf", digits) if digits else ""


def email_key(value):
    return _key("email", str(value).strip().casefold())


def is_key(value):
    return bool(re.fullmatch(r"[0-9a-f]{64}", value or ""))
