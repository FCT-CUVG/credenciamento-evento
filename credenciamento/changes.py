"""Alterações da coordenação num participante (uma pessoa ou em lote), com as mesmas regras.

Cada função recebe a linha atual e devolve (ação do histórico, campos) para update_participant,
None quando não há o que mudar, ou levanta ChangeError com o motivo para o painel mostrar."""
from .db import pending_reasons
from .desks import auto_guiche, available_guiches, guiche_for, priority_guiche
from .participants import STATUS_STEPS, status_fields
from .payments import payment_fields

STARTED = ("searching", "ready", "completed")


class ChangeError(ValueError):
    pass


def payment_change(db, row, status):
    try:
        fields = payment_fields(db, status)
    except ValueError as exc:
        raise ChangeError(str(exc)) from exc
    if fields["payment_status"] == row["payment_status"] and fields["paid"] == row["paid"]:
        return None
    return "payment_update", fields


def status_change(db, row, status, actor):
    if status not in STATUS_STEPS:
        raise ChangeError("Situação inválida.")
    if status == row["status"]:
        return None
    reasons = pending_reasons(row)
    if status in STARTED and reasons:
        detail = [f"pagamento “{row['payment_status']}” não libera o credenciamento" if "payment" in reasons else "",
                  "afiliação não informada" if "affiliation" in reasons else ""]
        raise ChangeError("Pendência: " + " e ".join(filter(None, detail)) + ". Resolva antes de avançar a situação.")
    return "status_" + status, status_fields(row, status, actor)


def priority_change(db, row, priority):
    if priority not in (0, 1):
        raise ChangeError("Prioridade inválida.")
    desk, guiche = priority_guiche(), row["guiche"]
    try:
        if priority and desk:
            guiche = desk
        elif not priority and guiche == desk:
            guiche = guiche_for(row["name"])
    except ValueError as exc:
        raise ChangeError(str(exc)) from exc
    if guiche != row["guiche"] and row["status"] in STARTED:
        raise ChangeError("Não é possível mudar o guichê: a busca ou a retirada do kit já começou.")
    if row["priority"] == priority and row["guiche"] == guiche:
        return None
    manual = 0 if priority and desk else row["guiche_manual"] if guiche == row["guiche"] else 0
    return ("priority_on" if priority else "priority_off"), {"priority": priority, "guiche": guiche,
                                                             "guiche_manual": manual}


def desk_change(db, row, desk):
    desk = str(desk or "").strip()
    if desk not in {item["id"] for item in available_guiches(db)}:
        raise ChangeError("Guichê inválido.")
    if desk == row["guiche"]:
        return None
    if row["status"] in STARTED:
        raise ChangeError("Não é possível mudar o guichê: a busca ou a retirada do kit já começou.")
    try:
        manual = int(desk != auto_guiche(row["name"], row["priority"]))
    except ValueError:
        manual = 1
    return "guiche_set", {"guiche": desk, "guiche_manual": manual}


def participant_change(db, row, field, value, actor):
    """Uma alteração do painel (campo + valor) aplicada às regras do campo."""
    if field == "payment":
        return payment_change(db, row, value)
    if field == "status":
        return status_change(db, row, str(value), actor)
    if field == "priority":
        return priority_change(db, row, int(value) if str(value) in ("0", "1") else -1)
    if field == "guiche":
        return desk_change(db, row, value)
    raise ChangeError("Campo inválido.")
