"""Situações de pagamento: texto livre vindo da inscrição (ex.: "Pago", "Isento", "Boleto
pendente") e a lista, definida pela coordenação, das que liberam o credenciamento.

A coluna paid (0/1) continua existindo e é sempre derivada: paid=1 quando a situação está na
lista que libera. Assim a fila, as pendências e os relatórios não precisam conhecer as situações."""
from .common import normalize
from .db import get_setting, set_setting, update_participant

DEFAULT_OK = ["Pago"]
DEFAULT_UNPAID = "Não pago"
EMPTY_STATUS = "Não informado"
MAX_STATUS = 60


def clean_status(value):
    """Situação como será guardada. Aceita os valores antigos 1/0 (sim/não)."""
    if isinstance(value, bool):
        value = int(value)
    text = " ".join(str(value if value is not None else "").split())
    if text == "1":
        return DEFAULT_OK[0]
    if text == "0":
        return DEFAULT_UNPAID
    if not text:
        return EMPTY_STATUS
    if len(text) > MAX_STATUS:
        raise ValueError(f"Situação de pagamento com mais de {MAX_STATUS} caracteres.")
    return text


def ok_statuses(db):
    return get_setting(db, "payment_ok", DEFAULT_OK)


def is_ok(status, ok):
    return normalize(status) in {normalize(item) for item in ok}


def extra_options(db):
    """Situações criadas no painel que ainda não aparecem em nenhuma inscrição."""
    return get_setting(db, "payment_options", [])


def payment_options(db):
    """Situações disponíveis: as das inscrições, as que liberam e as criadas no painel,
    sem repetir (comparando sem acento e sem diferença de maiúsculas)."""
    counts = {}
    for row in db.execute("SELECT payment_status, COUNT(*) AS total FROM participants GROUP BY payment_status"):
        counts[row["payment_status"]] = row["total"]
    ok = ok_statuses(db)
    seen, options = set(), []
    for value in [*counts, *ok, *extra_options(db)]:
        key = normalize(value)
        if value and key not in seen:
            seen.add(key)
            total = sum(count for status, count in counts.items() if normalize(status) == key)
            options.append({"value": value, "total": total, "ok": is_ok(value, ok)})
    return sorted(options, key=lambda option: (not option["ok"], normalize(option["value"])))


def resolve_option(db, value):
    """Usa a grafia já conhecida de uma situação (ex.: "pago" vira "Pago")."""
    status = clean_status(value)
    for option in payment_options(db):
        if normalize(option["value"]) == normalize(status):
            return option["value"]
    return status


def save_payment_config(db, ok, options, actor):
    """Grava quais situações liberam (e situações extras) e recalcula paid de todos;
    devolve quantos participantes mudaram."""
    if not isinstance(ok, list) or not isinstance(options, list):
        raise ValueError("Configuração de pagamento inválida.")
    ok = [clean_status(item) for item in ok]
    options = [clean_status(item) for item in options]
    if len(ok) + len(options) > 100:
        raise ValueError("Use no máximo 100 situações de pagamento.")
    set_setting(db, "payment_ok", ok)
    set_setting(db, "payment_options", options)
    changed = 0
    for row in db.execute("SELECT id, payment_status, paid FROM participants").fetchall():
        paid = int(is_ok(row["payment_status"], ok))
        if paid != row["paid"]:
            update_participant(db, row["id"], "payment_rule", actor, paid=paid)
            changed += 1
    return changed


def payment_fields(db, status):
    status = resolve_option(db, status)
    return {"payment_status": status, "paid": int(is_ok(status, ok_statuses(db)))}
