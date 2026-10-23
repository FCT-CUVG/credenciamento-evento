"""Backup dos participantes e do histórico pelo painel: baixar, restaurar e limpar.

O arquivo é um JSON com cada participante completo (situação, horários, quem buscou) e todos
os eventos do histórico. CPF e e-mail não estão nele: só as chaves de busca, que dependem de
CHECKIN_LOOKUP_SECRET. Por isso o backup leva uma marca da chave, e a restauração recusa um
arquivo feito com outra chave (a busca pública não reconheceria ninguém).

Contas da equipe e configurações (guichês, pagamento, identidade visual) não entram: limpar e
restaurar mexem só em participantes e histórico."""
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone

from . import settings
from .common import now
from .participants import STATUS_STEPS
from .payments import is_ok, ok_statuses

FORMAT = "credenciamento-backup"
VERSION = 1
TEXT_COLUMNS = ("id", "name", "name_key", "badge_name", "email_key", "cpf_key", "cpf_prefix", "affiliation",
                "country", "payment_status", "guiche", "status", "claimed_by", "prechecked_at", "claimed_at",
                "ready_at", "completed_at", "updated_at")
FLAG_COLUMNS = ("paid", "priority", "guiche_manual")
OPTIONAL_TEXT = {"claimed_by", "prechecked_at", "claimed_at", "ready_at", "completed_at"}
EVENT_COLUMNS = ("id", "participant_id", "action", "actor", "occurred_at")


def lookup_fingerprint():
    return hmac.new(settings.LOOKUP_SECRET, b"backup-fingerprint", hashlib.sha256).hexdigest()[:16]


def build_backup(db):
    participants = [dict(row) for row in db.execute(
        f"SELECT {','.join(TEXT_COLUMNS + FLAG_COLUMNS)},revision FROM participants ORDER BY name_key")]
    events = [dict(row) for row in db.execute(
        f"SELECT {','.join(EVENT_COLUMNS)} FROM events ORDER BY occurred_at, rowid")]
    return {"format": FORMAT, "version": VERSION, "created_at": now(), "lookup": lookup_fingerprint(),
            "participants": participants, "events": events}


def backup_filename():
    return datetime.now(timezone.utc).strftime("backup-credenciamento-%Y%m%d-%H%M%S.json")


def backup_bytes(backup):
    return json.dumps(backup, ensure_ascii=False, indent=1).encode("utf-8")


def save_server_copy(backup, reason):
    """Cópia no servidor (pasta de dados) antes de limpar ou restaurar: se o download falhar,
    nada se perde."""
    folder = settings.DATA / "backups-painel"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / backup_filename().replace("backup-", f"{reason}-", 1)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(backup_bytes(backup))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def clear_participants(db):
    """Apaga participantes, histórico e a fila do Google Sheets; devolve (participantes, eventos)."""
    counts = (db.execute("SELECT COUNT(*) FROM participants").fetchone()[0],
              db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    db.execute("DELETE FROM sheet_outbox")
    db.execute("DELETE FROM events")
    db.execute("DELETE FROM participants")
    return counts


def _participant(item, n):
    if not isinstance(item, dict):
        raise ValueError(f"Participante {n}: registro inválido.")
    values = {}
    for column in TEXT_COLUMNS:
        value = item.get(column)
        if value is None and column in OPTIONAL_TEXT:
            values[column] = None
            continue
        if value is None and column in ("country", "payment_status", "cpf_key", "cpf_prefix", "affiliation"):
            value = ""
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError(f"Participante {n}: campo {column} inválido.")
        values[column] = value
    for column in FLAG_COLUMNS:
        value = item.get(column, 0)
        if value not in (0, 1, True, False):
            raise ValueError(f"Participante {n}: campo {column} inválido.")
        values[column] = int(value)
    revision = item.get("revision", 1)
    if not isinstance(revision, int) or revision < 1:
        raise ValueError(f"Participante {n}: revisão inválida.")
    values["revision"] = revision
    try:
        values["id"] = str(uuid.UUID(values["id"]))
    except ValueError as exc:
        raise ValueError(f"Participante {n}: id inválido.") from exc
    if values["status"] not in STATUS_STEPS:
        raise ValueError(f"Participante {n}: situação inválida.")
    if not values["name"].strip() or not values["guiche"].strip() or not re.fullmatch(r"[0-9a-f]{64}", values["email_key"]):
        raise ValueError(f"Participante {n}: nome, guichê ou chave do e-mail inválidos.")
    return values


def _event(item, n, ids):
    if not isinstance(item, dict) or not all(isinstance(item.get(column), str) for column in EVENT_COLUMNS):
        raise ValueError(f"Evento {n}: registro inválido.")
    if item["participant_id"] not in ids:
        raise ValueError(f"Evento {n}: participante que não está no backup.")
    return {column: item[column][:500] for column in EVENT_COLUMNS}


def restore_backup(db, backup):
    """Troca todos os participantes e o histórico pelos do backup (na transação de quem chama).
    Devolve (participantes, eventos) restaurados."""
    if not isinstance(backup, dict) or backup.get("format") != FORMAT:
        raise ValueError("Arquivo não é um backup do credenciamento.")
    if backup.get("version") != VERSION:
        raise ValueError("Versão de backup não suportada.")
    if backup.get("lookup") != lookup_fingerprint():
        raise ValueError("Este backup foi feito com outra chave de busca (CHECKIN_LOOKUP_SECRET). "
                         "Restaurado aqui, a busca por CPF e e-mail não reconheceria ninguém.")
    people, events = backup.get("participants"), backup.get("events")
    if not isinstance(people, list) or not isinstance(events, list):
        raise ValueError("Backup incompleto.")
    rows = [_participant(item, n) for n, item in enumerate(people, 1)]
    ids = {row["id"] for row in rows}
    if len(ids) != len(rows):
        raise ValueError("Backup com participantes repetidos.")
    history = [_event(item, n, ids) for n, item in enumerate(events, 1)]
    # Quem libera o credenciamento segue a regra de pagamento atual deste servidor.
    ok = ok_statuses(db)
    clear_participants(db)
    columns = TEXT_COLUMNS + FLAG_COLUMNS + ("revision",)
    for row in rows:
        row["paid"] = int(is_ok(row["payment_status"], ok))
        db.execute(f"INSERT INTO participants({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                   [row[column] for column in columns])
    db.executemany(f"INSERT INTO events({','.join(EVENT_COLUMNS)}) VALUES (?,?,?,?,?)",
                   [[event[column] for column in EVENT_COLUMNS] for event in history])
    return len(rows), len(history)
