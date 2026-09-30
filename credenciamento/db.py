"""Banco SQLite: conexão, esquema e o ponto único de leitura/alteração de participantes."""
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from . import settings
from .common import now


@contextmanager
def connect():
    settings.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(settings.DATA, 0o700)
    db = sqlite3.connect(settings.DB, timeout=10, isolation_level=None)
    try:
        os.chmod(settings.DB, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db
    finally:
        db.close()


def create_schema(db):
    """Cria as tabelas e aplica migrações de coluna; devolve as colunas adicionadas agora."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS participants (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                badge_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL, email_key TEXT NOT NULL,
                cpf TEXT NOT NULL DEFAULT '', affiliation TEXT NOT NULL DEFAULT '',
                paid INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1)),
                priority INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1)), guiche TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN
        ('registered','prechecked','searching','ready','completed')),
      claimed_by TEXT, prechecked_at TEXT, claimed_at TEXT, ready_at TEXT,
      completed_at TEXT, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
      UNIQUE(name_key,email_key)
    );
    CREATE INDEX IF NOT EXISTS idx_queue ON participants(status,guiche,prechecked_at);
    CREATE TABLE IF NOT EXISTS users (
      username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('volunteer','attendant','admin')),
      guiche TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, action TEXT NOT NULL,
      actor TEXT NOT NULL, occurred_at TEXT NOT NULL,
      FOREIGN KEY(participant_id) REFERENCES participants(id)
    );
    CREATE TABLE IF NOT EXISTS sheet_outbox (
      event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered_at TEXT,
      attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
      FOREIGN KEY(event_id) REFERENCES events(id)
    );
    -- A busca pública passou a registrar a chegada num passo só, sem token de confirmação.
    DROP TABLE IF EXISTS lookup_tokens;
    """)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(participants)")}
    added = set()
    for name, definition in (
        ("badge_name", "TEXT NOT NULL DEFAULT ''"),
        ("paid", "INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1))"),
        ("priority", "INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1))"),
        ("guiche_manual", "INTEGER NOT NULL DEFAULT 0 CHECK(guiche_manual IN (0,1))"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE participants ADD COLUMN {name} {definition}")
            added.add(name)
    db.execute("UPDATE participants SET badge_name=name WHERE badge_name='' ")
    return added


def participant_dict(row, private=False):
    d = {k: row[k] for k in ("id", "name", "affiliation", "guiche", "status",
                                  "claimed_by", "prechecked_at", "claimed_at", "ready_at",
                                  "completed_at", "updated_at", "revision")}
    d["priority"] = bool(row["priority"])
    d["cpf_prefix"] = row["cpf"][:3]
    if private:
        d.update(email=row["email"], cpf=row["cpf"], badge_name=row["badge_name"], paid=bool(row["paid"]))
    return d


def pending_reasons(row):
    reasons = []
    if not row["paid"]:
        reasons.append("payment")
    if not row["affiliation"].strip():
        reasons.append("affiliation")
    return reasons


def record_event(db, row, action, actor):
    event = {"id": str(uuid.uuid4()), "participant_id": row["id"], "action": action,
             "actor": actor, "occurred_at": now()}
    db.execute("INSERT INTO events VALUES (:id,:participant_id,:action,:actor,:occurred_at)", event)
    payload = {"event": event, "participant": participant_dict(row, private=True)}
    db.execute("INSERT INTO sheet_outbox(event_id,payload) VALUES (?,?)",
               (event["id"], json.dumps(payload, ensure_ascii=False)))


def get_participant(db, pid):
    return db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()


def update_participant(db, pid, action, actor, **fields):
    """Única forma de alterar um participante: grava os campos, atualiza updated_at e revision,
    registra o evento (histórico e fila do Google Sheets) e devolve a linha atualizada."""
    columns = "".join(f"{name}=?," for name in fields)
    db.execute(f"UPDATE participants SET {columns}updated_at=?,revision=revision+1 WHERE id=?",
               (*fields.values(), now(), pid))
    row = get_participant(db, pid)
    record_event(db, row, action, actor)
    return row


def backup_file(path):
    target = Path(path).expanduser().resolve()
    if target == settings.DB.resolve():
        raise ValueError("Escolha um arquivo de backup diferente do banco ativo.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("O backup já existe. Escolha outro nome para preservar o anterior.")
    with connect() as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    os.chmod(target, 0o600)
    print(f"Backup consistente salvo em {target}")
