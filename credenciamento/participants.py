"""Regras do fluxo do participante: etapas da situação e ações da fila de busca e guichê."""
from .common import now
from .db import get_setting, pending_reasons, set_setting


STATUS_STEPS = ("registered", "prechecked", "searching", "ready", "completed")

STEP_TIMESTAMPS = {"prechecked": "prechecked_at", "searching": "claimed_at",
                   "ready": "ready_at", "completed": "completed_at"}


def status_fields(row, status, actor):
    """Campos para levar alguém direto a uma etapa: preenche o que ficou para trás (mantendo
    horários já registrados) e limpa as etapas seguintes."""
    target, stamp = STATUS_STEPS.index(status), now()
    fields = {"status": status}
    for step, column in STEP_TIMESTAMPS.items():
        fields[column] = (row[column] or stamp) if STATUS_STEPS.index(step) <= target else None
    if status == "searching":
        fields["claimed_by"] = actor
    elif target > STATUS_STEPS.index("searching"):
        fields["claimed_by"] = row["claimed_by"] or actor
    else:
        fields["claimed_by"] = None
    return fields


def queue_action_fields(action, user, row):
    """Campos de uma ação da fila (busca e guichê), ou None se ela não vale para este usuário agora."""
    role, username, stamp = user["role"], user["username"], now()
    staff = role in ("volunteer", "admin")
    owns_search = row["claimed_by"] == username or role == "admin"
    at_desk = staff or (role == "attendant" and user["guiche"] == row["guiche"])
    clear = not pending_reasons(row)
    if action == "claim" and staff and row["status"] == "prechecked" and clear:
        return {"status": "searching", "claimed_by": username, "claimed_at": stamp}
    if action == "ready" and staff and row["status"] == "searching" and clear and owns_search:
        return {"status": "ready", "ready_at": stamp}
    if action == "release" and staff and row["status"] == "searching" and owns_search:
        return {"status": "prechecked", "claimed_by": None, "claimed_at": None}
    if action == "complete" and at_desk and row["status"] == "ready" and clear:
        return {"status": "completed", "completed_at": stamp}
    if action == "undo_ready" and at_desk and row["status"] == "ready":
        return {"status": "searching" if row["claimed_by"] else "prechecked", "ready_at": None}
    if action == "undo_complete" and at_desk and row["status"] == "completed":
        return {"status": "ready", "completed_at": None}
    return None


def public_checkin_state(db):
    """O pré-check-in público começa fechado; a coordenação abre e fecha pelo painel."""
    return get_setting(db, "public_checkin", {"open": False, "by": "", "at": ""})


def set_public_checkin(db, is_open, actor):
    state = {"open": bool(is_open), "by": actor, "at": now()}
    set_setting(db, "public_checkin", state)
    print(f"Pré-check-in público {'aberto' if is_open else 'fechado'} por {actor}.", flush=True)
    return state
